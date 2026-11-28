"""
Document-mode orchestration — uploaded file -> whole form.

This is the third generation path, alongside generate (description -> form) and
edit (form + instruction -> change-set). Here the admin uploads a source document
(a field spec, requirement write-up, Word/PDF export, etc.) and the LLM builds
Form Builder JSON for every section and field defined in that document — without
summarizing or inventing a different form shape.

Two input paths, chosen by file type:

  PDF / PNG / JPEG / text  -> sent to the model as a native ATTACHMENT
                              (Claude reads it directly), using build_doc_prompt.
  DOCX                      -> text + tables EXTRACTED server-side (python-docx)
                              and sent as TEXT, using build_doc_prompt_text_fallback.
                              Claude can't read raw .docx bytes, and pure-Python
                              extraction avoids any system dependency (no
                              LibreOffice). Our form docs are digital Word files
                              with a real text layer, so this is accurate.

Pipeline (both paths):

    file (+ optional instruction)
        -> prepare input          # attachment OR extracted text
        -> build system + doc prompt
        -> provider.generate()    # FIRST call sends the file/text
        -> parse JSON             # a full form
        -> strip metadata         # sections-first output contract
        -> validate_form (L1 + L2)
        -> bounded repair         # TEXT-ONLY: the source is NOT re-sent
        -> return the form

Design choices:
- The source is sent ONCE (first call). Repairs operate on the model's previous
  text output + the validation errors — re-sending a multi-MB base64 document on
  every repair would be slow and wasteful, and the model already "read" it.
- Provider is INJECTABLE for testing; production passes the factory-built one.
- The parse/strip/validate tail is intentionally identical to generate_flow so
  the output contract is enforced the same way regardless of input mode.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, AsyncIterator

from app.config import get_settings
from app.core.doc_spec_parser import parse_field_spec_document
from app.core.document_convert import (
    DocumentConversionError,
    extract_docx_text,
    is_docx,
    is_pdf,
    extract_pdf_text,
)
from app.core.form_merge import merge_generated_sections
from app.core.doc_trace import ensure_trace, stage
from app.kb.binder import bind_kb_to_form
from app.kb.identity import (
    apply_kb_field_ids,
    coerce_dependency_lists,
    ensure_punjabi_pairs,
    form_field_ids,
    identity_entries,
)
from app.kb.loader import fetch_live_kb
from app.kb.prompt import KbPromptContext, build_kb_prompt_block

# Reuse the exact generate-mode tail so the output contract stays identical.
from app.orchestration.generate_flow import (
    GenerateResult,
    GenerateStatus,
    _parse_and_validate,
)
from app.prompts.system_prompt import get_system_prompt
from app.prompts.user_prompts import (
    build_doc_prompt,
    build_doc_prompt_text_fallback,
    build_repair_prompt,
)
from app.providers.base import Attachment, LLMError, LLMProvider

logger = logging.getLogger("app.orchestration.document_flow")

#: Content types Claude can read natively as an attachment.
_NATIVE_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "text/plain",
}

#: Filename extensions -> content type, used when the browser sends a generic
#: type (e.g. application/octet-stream) so we can still route correctly.
_EXT_CONTENT_TYPE = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
}

#: Hard upper bound on the attachment we send to the model. Keeps a single
#: request from ballooning in tokens/latency. Claude's PDF limit is ~32MB.
_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB


@dataclass
class _Prepared:
    """
    A normalised upload, resolved to exactly ONE of:
      - `attachment`      : a native file to attach (PDF/image/text), OR
      - `extracted_text`  : text pulled from a DOCX (text-fallback path).
    `error` is set instead when the upload can't be used.
    """

    attachment: Attachment | None = None
    extracted_text: str | None = None
    warnings: list[str] = dc_field(default_factory=list)
    error: str | None = None


async def run_document_flow(
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    instruction: str | None,
    provider: LLMProvider,
    *,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
) -> GenerateResult:
    """
    Generate a complete form from an uploaded document using `provider`.

    When `base_form` is provided (admin already has a form on the canvas), the
    newly generated sections are APPENDED onto that form so prior sections and
    fields are preserved.

    Returns a GenerateResult (same type generate-mode returns) so the route can
    handle both paths uniformly.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()
    append = base_form is not None

    # --- Prepare the input (DOCX->text, or native attachment) -------------
    ensure_trace(logger, "document_flow", file=filename, bytes=len(file_bytes))
    prep_started = time.monotonic()
    prepared = _prepare_traced(file_bytes, filename, content_type)
    if prepared.error:
        logger.error("document_flow: upload rejected: %s", prepared.error)
        return GenerateResult(status=GenerateStatus.FAILED, errors=[prepared.error])

    kb_snapshot, kb_block, kb_ctx = await _load_kb_traced(instruction, prepared)
    finalize = _timed_finalize(
        _kb_bound_finalize(_finalize_document_result, kb_snapshot, instruction)
    )
    logger.info(
        "document_flow: prepare took=%.2fs path=%s append=%s",
        time.monotonic() - prep_started,
        "docx_text" if prepared.extracted_text is not None else "attachment",
        append,
    )

    parsed_spec = await _resolve_spec(prepared, instruction, provider)
    if parsed_spec:
        logger.info(
            "document_flow: spec enrich path sections=%d",
            len(parsed_spec.sections),
        )
        from app.orchestration import spec_enrich_flow

        return await spec_enrich_flow.run_spec_enrich_flow(
            parsed_spec.sections,
            provider,
            instruction,
            finalize=finalize,
            base_form=base_form,
            max_repair_attempts=max_repair_attempts,
            service_name=parsed_spec.service_name,
            cross_field_validators=parsed_spec.cross_field_validators,
            kb=kb_ctx,
        )

    system_prompt = get_system_prompt()

    # DOCX text path: no attachment, prompt carries the extracted text.
    if prepared.extracted_text is not None:
        user_prompt = _with_kb(
            build_doc_prompt_text_fallback(
                instruction, prepared.extracted_text, append_to_existing=append
            ),
            kb_block,
        )
        attachments: list[Attachment] | None = None
    else:
        # Native attachment path (PDF/image/text) — needs provider support.
        if not provider.supports_document_input:
            return GenerateResult(
                status=GenerateStatus.FAILED,
                errors=[
                    "The active AI provider does not support document input. "
                    "Upload a Word (.docx) file instead, or paste the form "
                    "requirements as text."
                ],
            )
        assert prepared.attachment is not None  # guaranteed when no error/text
        user_prompt = _with_kb(
            build_doc_prompt(instruction, append_to_existing=append), kb_block
        )
        attachments = [prepared.attachment]

    # --- First LLM call: sends the file/text ------------------------------
    try:
        raw = await provider.generate(system_prompt, user_prompt, attachments=attachments)
    except LLMError as exc:
        return GenerateResult(status=GenerateStatus.FAILED, errors=[f"LLM call failed: {exc}"])

    outcome = _parse_and_validate(raw)
    if outcome.ok:
        logger.info(
            "document_flow: SUCCESS on first call, no repairs, total=%.1fs",
            time.monotonic() - flow_started,
        )
        return finalize(
            outcome.form, prepared.warnings + outcome.warnings, base_form
        )

    logger.info(
        "document_flow: first output FAILED validation (%d errors) -> entering "
        "repair loop (max=%d). Errors: %s",
        len(outcome.errors),
        max_repair_attempts,
        "; ".join(outcome.errors)[:400],
    )

    # --- Bounded repair (TEXT-ONLY: the source is NOT re-sent) ------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = _with_kb(
            build_repair_prompt(last_raw, last_errors, is_edit_mode=False), kb_block
        )
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return GenerateResult(
                status=GenerateStatus.FAILED, errors=[f"LLM repair call failed: {exc}"]
            )
        outcome = _parse_and_validate(last_raw)
        if outcome.ok:
            logger.info(
                "document_flow: SUCCESS after %d repair(s), total=%.1fs",
                attempts,
                time.monotonic() - flow_started,
            )
            return finalize(
                outcome.form, prepared.warnings + outcome.warnings, base_form
            )
        last_errors = outcome.errors

    logger.info(
        "document_flow: FAILED after %d repair(s), total=%.1fs",
        attempts,
        time.monotonic() - flow_started,
    )
    return GenerateResult(status=GenerateStatus.FAILED, errors=last_errors)


# --- Streaming variant (SSE) ------------------------------------------------
#
# Same pipeline as run_document_flow, but the FIRST model call streams so the
# UI can show progress. Emits a sequence of (event, payload) tuples:
#
#   ("delta",   str)        -> a raw text fragment as the model writes it
#   ("done",    dict)       -> the validated, apply-ready form (terminal)
#   ("error",   list[str])  -> unrecoverable errors (terminal)
#
# The delta fragments are for DISPLAY ONLY. Correctness still runs on the FULL
# accumulated text once streaming ends: parse -> strip metadata -> validate ->
# bounded repair (repairs are non-streaming, TEXT-ONLY, file not re-sent). The
# caller only applies the form from the ("done", ...) event.


async def run_document_flow_stream(
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    instruction: str | None,
    provider: LLMProvider,
    *,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a document-mode generation as (event, payload) tuples."""
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()
    append = base_form is not None

    from app.orchestration.progress import event as progress_event

    yield progress_event("read", "Reading your document…")

    # --- Prepare input ----------------------------------------------------
    ensure_trace(logger, "document_flow(stream)", file=filename, bytes=len(file_bytes))
    prepared = _prepare_traced(file_bytes, filename, content_type)
    if prepared.error:
        logger.error("document_flow(stream): upload rejected: %s", prepared.error)
        yield ("error", [prepared.error])
        return

    yield progress_event("read", "Extracting fields and sections…")
    kb_snapshot, kb_block, kb_ctx = await _load_kb_traced(instruction, prepared)
    finalize = _timed_finalize(
        _kb_bound_finalize(_finalize_document_result, kb_snapshot, instruction)
    )

    parsed_spec = _parse_spec_traced(prepared.extracted_text) if prepared.extracted_text else None
    if parsed_spec is None:
        yield progress_event("spec", "Understanding the document structure…")
        parsed_spec = await _extract_spec_traced(prepared, instruction, provider)
    if parsed_spec:
        logger.info(
            "document_flow(stream): spec enrich path sections=%d",
            len(parsed_spec.sections),
        )
        from app.orchestration import spec_enrich_flow

        titles = [s.title for s in parsed_spec.sections if s.title]
        preview = ", ".join(titles[:5])
        extra = f" and {len(titles) - 5} more" if len(titles) > 5 else ""
        yield progress_event(
            "spec",
            f"Found {len(parsed_spec.sections)} section"
            f"{'s' if len(parsed_spec.sections) != 1 else ''}"
            + (f": {preview}{extra}" if preview else ""),
            sections=len(parsed_spec.sections),
        )

        async for event in spec_enrich_flow.run_spec_enrich_flow_stream(
            parsed_spec.sections,
            provider,
            instruction,
            finalize=finalize,
            base_form=base_form,
            max_repair_attempts=max_repair_attempts,
            service_name=parsed_spec.service_name,
            cross_field_validators=parsed_spec.cross_field_validators,
            kb=kb_ctx,
        ):
            yield event
        return

    # If streaming isn't supported, fall back to the non-streaming flow and
    # emit its result as a single terminal event (no deltas).
    if not getattr(provider, "supports_streaming", False):
        result = await run_document_flow(
            file_bytes, filename, content_type, instruction, provider,
            base_form=base_form,
            max_repair_attempts=max_repair_attempts,
        )
        if result.status is GenerateStatus.FAILED:
            yield ("error", result.errors)
        else:
            yield ("done", result.form or {})
        return

    system_prompt = get_system_prompt()

    if prepared.extracted_text is not None:
        user_prompt = _with_kb(
            build_doc_prompt_text_fallback(
                instruction, prepared.extracted_text, append_to_existing=append
            ),
            kb_block,
        )
        attachments: list[Attachment] | None = None
    else:
        if not provider.supports_document_input:
            yield (
                "error",
                [
                    "The active AI provider does not support document input. "
                    "Upload a Word (.docx) file instead, or paste the form "
                    "requirements as text."
                ],
            )
            return
        assert prepared.attachment is not None
        user_prompt = _with_kb(
            build_doc_prompt(instruction, append_to_existing=append), kb_block
        )
        attachments = [prepared.attachment]

    yield progress_event("build", "Building the form from your document…")

    # --- First call: STREAM, accumulating the full text -------------------
    chunks: list[str] = []
    last_beat = time.monotonic()
    try:
        async for fragment in provider.generate_stream(
            system_prompt, user_prompt, attachments=attachments
        ):
            chunks.append(fragment)
            yield ("delta", fragment)
            now = time.monotonic()
            if now - last_beat >= 15:
                last_beat = now
                yield progress_event(
                    "build",
                    "Still building the form from your document…",
                )
    except LLMError as exc:
        yield ("error", [f"LLM call failed: {exc}"])
        return

    raw = "".join(chunks)
    yield progress_event("validate", "Checking fields and validation…")
    outcome = _parse_and_validate(raw)
    if outcome.ok:
        logger.info(
            "document_flow(stream): SUCCESS on first call, total=%.1fs",
            time.monotonic() - flow_started,
        )
        yield progress_event("finalize", "Applying platform field rules…")
        finalized = finalize(
            outcome.form, prepared.warnings + outcome.warnings, base_form
        )
        if finalized.status is GenerateStatus.FAILED:
            yield ("error", finalized.errors)
        else:
            yield ("done", finalized.form or {})
        return

    logger.info(
        "document_flow(stream): first output FAILED validation (%d errors) -> "
        "repair loop (max=%d)",
        len(outcome.errors),
        max_repair_attempts,
    )

    # --- Bounded repair (non-streaming, TEXT-ONLY) ------------------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw
    while attempts < max_repair_attempts:
        attempts += 1
        yield progress_event(
            "repair",
            f"Optimizing the generated form (attempt {attempts} of {max_repair_attempts})…",
        )
        repair_prompt = _with_kb(
            build_repair_prompt(last_raw, last_errors, is_edit_mode=False), kb_block
        )
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            yield ("error", [f"LLM repair call failed: {exc}"])
            return
        outcome = _parse_and_validate(last_raw)
        if outcome.ok:
            logger.info(
                "document_flow(stream): SUCCESS after %d repair(s), total=%.1fs",
                attempts,
                time.monotonic() - flow_started,
            )
            yield progress_event("finalize", "Applying platform field rules…")
            finalized = finalize(
                outcome.form, prepared.warnings + outcome.warnings, base_form
            )
            if finalized.status is GenerateStatus.FAILED:
                yield ("error", finalized.errors)
            else:
                yield ("done", finalized.form or {})
            return
        last_errors = outcome.errors

    logger.info(
        "document_flow(stream): FAILED after %d repair(s), total=%.1fs",
        attempts,
        time.monotonic() - flow_started,
    )
    yield ("error", last_errors)


def _prepare_traced(file_bytes: bytes, filename: str, content_type: str | None) -> _Prepared:
    with stage(logger, "prepare_upload", file=filename, bytes=len(file_bytes)) as st:
        prepared = prepare_document_upload(file_bytes, filename, content_type)
        st["path"] = (
            "rejected"
            if prepared.error
            else "docx_text"
            if prepared.extracted_text is not None
            else "attachment"
        )
        st["text_chars"] = len(prepared.extracted_text or "")
        if prepared.error:
            st["error"] = prepared.error
        return prepared


async def _load_kb_traced(instruction: str | None, prepared: _Prepared):
    """Fetch the live KB and build the prompt block/context, logging what was picked."""
    with stage(logger, "kb_load") as st:
        kb_snapshot = await fetch_live_kb()
        kb_block = build_kb_prompt_block(
            kb_snapshot,
            "\n".join(t for t in (instruction, prepared.extracted_text) if t),
            mode="document",
        )
        st["entries"] = len(kb_snapshot)
        st["prompt_block_chars"] = len(kb_block)
        pinned = identity_entries(kb_snapshot)
        st["identity_fields"] = [e["payload"]["id"] for e in pinned]
        if pinned:
            logger.info(
                "document_flow: KB identity fields: %s",
                ", ".join(f'{e["payload"]["id"]} ({e["payload"].get("type")}) from {e["key"]}' for e in pinned),
            )
        else:
            logger.warning(
                "document_flow: KB has no identity fields (no template payload.id and no "
                "policy body declaring e.g. applying_for_yourself: type applying_for) — "
                "document types like 'radio' will be kept as-is"
            )
        if not kb_snapshot:
            logger.warning(
                "document_flow: KB is EMPTY for this run - KB policies, datasources and fixed "
                "field ids will NOT be applied (see the 'KB snapshot EMPTY' line above for why)"
            )
    return kb_snapshot, kb_block, KbPromptContext(kb_snapshot, mode="document")


def _parse_spec_traced(text: str | None):
    if not (text or "").strip():
        return None
    with stage(logger, "parse_spec", text_chars=len(text)) as st:
        parsed = parse_field_spec_document(text)
        if parsed:
            st["sections"] = len(parsed.sections)
            st["fields"] = sum(len(sec.fields) for sec in parsed.sections)
            st["validators"] = len(parsed.cross_field_validators or [])
            st["titles"] = [sec.title for sec in parsed.sections]
        else:
            st["result"] = "no deterministic layout"
        return parsed


async def _extract_spec_traced(prepared: _Prepared, instruction: str | None, provider: LLMProvider):
    """Layout-agnostic fallback: model lists sections/fields, then enrich runs as usual."""
    from app.orchestration.spec_extract_flow import extract_spec_from_document

    attachments = [prepared.attachment] if prepared.attachment else None
    parsed = await extract_spec_from_document(
        provider,
        extracted_text=prepared.extracted_text,
        instruction=instruction,
        attachments=attachments,
    )
    if parsed is None:
        logger.warning("document_flow: spec extract produced nothing; falling back to one-shot form JSON")
    return parsed


async def _resolve_spec(prepared: _Prepared, instruction: str | None, provider: LLMProvider):
    parsed = _parse_spec_traced(prepared.extracted_text)
    if parsed is not None:
        return parsed
    return await _extract_spec_traced(prepared, instruction, provider)


def _timed_finalize(finalize):
    """Time + log the post-generation step (KB ids/binding, append onto the canvas)."""

    def _timed(generated, warnings, base_form):
        with stage(logger, "finalize", append=base_form is not None) as st:
            result = finalize(generated, warnings, base_form)
            st["status"] = getattr(result.status, "value", result.status)
            st["warnings"] = len(result.warnings or [])
            if result.status is GenerateStatus.FAILED:
                logger.error(
                    "document_flow: finalize FAILED: %s", " | ".join(result.errors)[:800]
                )
            return result

    return _timed


def _with_kb(prompt: str, kb_block: str) -> str:
    """Append the published-KB block to a prompt (no-op when empty)."""
    return f"{prompt}\n\n{kb_block}" if kb_block else prompt


def _kb_bound_finalize(finalize, kb_snapshot: list[dict[str, Any]], user_text: str | None):
    """
    Wrap a finalizer so the generated form gets live KB datasources (e.g. the
    District list for State) before it is merged or returned.
    """

    def _bound(
        generated: dict[str, Any] | None,
        warnings: list[str],
        base_form: dict[str, Any] | None,
    ) -> GenerateResult:
        if generated is None:
            return finalize(generated, warnings, base_form)
        extra: list[str] = []
        if kb_snapshot:
            # Canonical ids first (safety net for the section fallback / repairs): later
            # steps and the cascade resolution then see the KB ids. Ids already on the
            # canvas are reserved so an append never creates a duplicate.
            generated, id_warnings = apply_kb_field_ids(
                generated, kb_snapshot, reserved_ids=form_field_ids(base_form)
            )
            extra.extend(id_warnings)
        generated, pair_warnings = ensure_punjabi_pairs(generated)
        extra.extend(pair_warnings)
        generated = coerce_dependency_lists(generated)
        bound, kb_warnings = (
            bind_kb_to_form(generated, kb_snapshot, user_text or "")
            if kb_snapshot
            else (generated, [])
        )
        extra.extend(kb_warnings)
        for note in extra:
            logger.info("document_flow: KB applied: %s", note)
        return finalize(bound, warnings + extra, base_form)

    return _bound


def _finalize_document_result(
    generated: dict[str, Any] | None,
    warnings: list[str],
    base_form: dict[str, Any] | None,
) -> GenerateResult:
    """
    If a base form was supplied, append generated sections onto it; otherwise
    return the generated form as-is.
    """
    if generated is None:
        return GenerateResult(
            status=GenerateStatus.FAILED,
            errors=["Document generation produced no form."],
        )
    if base_form is None:
        return GenerateResult(
            status=GenerateStatus.PROPOSAL, form=generated, warnings=warnings
        )
    try:
        merged, merge_warnings = merge_generated_sections(base_form, generated)
    except ValueError as exc:
        return GenerateResult(status=GenerateStatus.FAILED, errors=[str(exc)])
    return GenerateResult(
        status=GenerateStatus.PROPOSAL,
        form=merged,
        warnings=warnings + merge_warnings,
    )


# --- Internal: normalise the upload -----------------------------------------


def prepare_document_upload(
    file_bytes: bytes, filename: str, content_type: str | None
) -> _Prepared:
    """
    Route a raw upload to the right input path.

    - Empty file       -> error.
    - DOCX             -> extract text+tables (python-docx); on failure, surface
                          the reason. No system dependency.
    - PDF/image/text   -> native attachment, inferring the type from the
                          extension when the browser sends a generic type.
    - Anything else    -> error listing what's supported.
    - Oversized attachment -> error.
    """
    if not file_bytes:
        return _Prepared(error="The uploaded file is empty.")

    # DOCX: extract text server-side and take the text-fallback path.
    if is_docx(filename, content_type):
        try:
            text = extract_docx_text(file_bytes)
        except DocumentConversionError as exc:
            return _Prepared(error=str(exc))
        return _Prepared(
            extracted_text=text,
            warnings=["Read the Word document by extracting its text and tables."],
        )

    # PDF with a text layer: extract text so pipe-table specs use the fast path.
    if is_pdf(filename, content_type):
        try:
            text = extract_pdf_text(file_bytes)
        except DocumentConversionError:
            pass
        else:
            if text.strip():
                return _Prepared(
                    extracted_text=text,
                    warnings=[
                        "Read the PDF by extracting its text (same path as Word specs)."
                    ],
                )

    # Native attachment path. Resolve the type, falling back to the extension.
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in _NATIVE_CONTENT_TYPES:
        ext = Path(filename or "").suffix.lower()
        inferred = _EXT_CONTENT_TYPE.get(ext)
        if inferred:
            ctype = inferred

    if ctype not in _NATIVE_CONTENT_TYPES:
        return _Prepared(
            error=(
                f"Unsupported file type ({content_type or 'unknown'}). Supported: "
                "PDF, Word (.docx), PNG, JPEG, or plain text."
            )
        )

    if len(file_bytes) > _MAX_ATTACHMENT_BYTES:
        mb = _MAX_ATTACHMENT_BYTES // (1024 * 1024)
        return _Prepared(error=f"The document is too large (limit {mb} MB).")

    return _Prepared(
        attachment=Attachment(
            filename=filename or "document",
            content_type=ctype,
            data=file_bytes,
        )
    )
