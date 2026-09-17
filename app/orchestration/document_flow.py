"""
Document-mode orchestration — uploaded file -> whole form.

This is the third generation path, alongside generate (description -> form) and
edit (form + instruction -> change-set). Here the admin uploads a source document
(a certificate, an existing paper form, an output template) and the LLM infers the
APPLICATION FORM a citizen would fill in to produce/obtain it.

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
from app.core.document_convert import (
    DocumentConversionError,
    extract_docx_text,
    is_docx,
)

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
    max_repair_attempts: int | None = None,
) -> GenerateResult:
    """
    Generate a complete form from an uploaded document using `provider`.

    Returns a GenerateResult (same type generate-mode returns) so the route can
    handle both paths uniformly.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()

    # --- Prepare the input (DOCX->text, or native attachment) -------------
    prep_started = time.monotonic()
    prepared = _prepare_input(file_bytes, filename, content_type)
    if prepared.error:
        return GenerateResult(status=GenerateStatus.FAILED, errors=[prepared.error])
    logger.info(
        "document_flow: prepare took=%.2fs path=%s",
        time.monotonic() - prep_started,
        "docx_text" if prepared.extracted_text is not None else "attachment",
    )

    system_prompt = get_system_prompt()

    # DOCX text path: no attachment, prompt carries the extracted text.
    if prepared.extracted_text is not None:
        user_prompt = build_doc_prompt_text_fallback(instruction, prepared.extracted_text)
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
        user_prompt = build_doc_prompt(instruction)
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
        return GenerateResult(
            status=GenerateStatus.PROPOSAL,
            form=outcome.form,
            warnings=prepared.warnings + outcome.warnings,
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
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
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
            return GenerateResult(
                status=GenerateStatus.PROPOSAL,
                form=outcome.form,
                warnings=prepared.warnings + outcome.warnings,
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
    max_repair_attempts: int | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a document-mode generation as (event, payload) tuples."""
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()

    # --- Prepare input ----------------------------------------------------
    prepared = _prepare_input(file_bytes, filename, content_type)
    if prepared.error:
        yield ("error", [prepared.error])
        return

    # If streaming isn't supported, fall back to the non-streaming flow and
    # emit its result as a single terminal event (no deltas).
    if not getattr(provider, "supports_streaming", False):
        result = await run_document_flow(
            file_bytes, filename, content_type, instruction, provider,
            max_repair_attempts=max_repair_attempts,
        )
        if result.status is GenerateStatus.FAILED:
            yield ("error", result.errors)
        else:
            yield ("done", result.form or {})
        return

    system_prompt = get_system_prompt()

    if prepared.extracted_text is not None:
        user_prompt = build_doc_prompt_text_fallback(instruction, prepared.extracted_text)
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
        user_prompt = build_doc_prompt(instruction)
        attachments = [prepared.attachment]

    # --- First call: STREAM, accumulating the full text -------------------
    chunks: list[str] = []
    try:
        async for fragment in provider.generate_stream(
            system_prompt, user_prompt, attachments=attachments
        ):
            chunks.append(fragment)
            yield ("delta", fragment)
    except LLMError as exc:
        yield ("error", [f"LLM call failed: {exc}"])
        return

    raw = "".join(chunks)
    outcome = _parse_and_validate(raw)
    if outcome.ok:
        logger.info(
            "document_flow(stream): SUCCESS on first call, total=%.1fs",
            time.monotonic() - flow_started,
        )
        yield ("done", outcome.form or {})
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
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
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
            yield ("done", outcome.form or {})
            return
        last_errors = outcome.errors

    logger.info(
        "document_flow(stream): FAILED after %d repair(s), total=%.1fs",
        attempts,
        time.monotonic() - flow_started,
    )
    yield ("error", last_errors)


# --- Internal: normalise the upload -----------------------------------------


def _prepare_input(
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
