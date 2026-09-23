"""
Workflow Builder document-mode orchestration — uploaded file → whole workflow form.

Mirrors document_flow.py but uses the workflow system prompt, workflow doc
prompts, and workflow validator. The document preparation logic (_prepare_input,
is_docx, extract_docx_text) is fully reused from the shared core layer —
document handling is not builder-specific.

Pipeline (both paths):
    file (+ optional instruction)
        → _prepare_input()           # reused from document_flow
        → build_workflow_doc_prompt() or build_workflow_doc_prompt_text_fallback()
        → provider.generate()
        → _parse_and_validate_workflow()  # from workflow_generate_flow
        → bounded repair (TEXT-ONLY: source not re-sent)
        → return WorkflowGenerateResult
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from app.config import get_settings
from app.orchestration.document_flow import _prepare_input  # shared prep logic
from app.orchestration.workflow_generate_flow import (
    WorkflowGenerateResult,
    WorkflowGenerateStatus,
    _parse_and_validate_workflow,
)
from app.prompts.workflow_system_prompt import get_workflow_system_prompt
from app.prompts.workflow_user_prompts import (
    build_workflow_doc_prompt,
    build_workflow_doc_prompt_text_fallback,
    build_workflow_repair_prompt,
)
from app.providers.base import Attachment, LLMError, LLMProvider

logger = logging.getLogger("app.orchestration.workflow_document_flow")


async def run_workflow_document_flow(
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    instruction: str | None,
    provider: LLMProvider,
    *,
    max_repair_attempts: int | None = None,
) -> WorkflowGenerateResult:
    """
    Generate a complete workflow form from an uploaded document using `provider`.
    Returns a WorkflowGenerateResult so the route handles both document and
    NL paths uniformly.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()

    # --- Prepare input (shared: DOCX→text or native attachment) -----------
    prepared = _prepare_input(file_bytes, filename, content_type)
    if prepared.error:
        return WorkflowGenerateResult(
            status=WorkflowGenerateStatus.FAILED,
            errors=[prepared.error],
        )

    system_prompt = get_workflow_system_prompt()

    if prepared.extracted_text is not None:
        user_prompt = build_workflow_doc_prompt_text_fallback(
            instruction, prepared.extracted_text
        )
        attachments: list[Attachment] | None = None
    else:
        if not provider.supports_document_input:
            return WorkflowGenerateResult(
                status=WorkflowGenerateStatus.FAILED,
                errors=[
                    "The active AI provider does not support document input. "
                    "Upload a Word (.docx) file instead, or paste the workflow "
                    "requirements as text."
                ],
            )
        assert prepared.attachment is not None
        user_prompt = build_workflow_doc_prompt(instruction)
        attachments = [prepared.attachment]

    # --- First LLM call ---------------------------------------------------
    try:
        raw = await provider.generate(
            system_prompt, user_prompt, attachments=attachments
        )
    except LLMError as exc:
        return WorkflowGenerateResult(
            status=WorkflowGenerateStatus.FAILED,
            errors=[f"LLM call failed: {exc}"],
        )

    outcome = _parse_and_validate_workflow(raw, wants_flat=True)
    if outcome.ok:
        logger.info(
            "workflow_document_flow: SUCCESS on first call, total=%.1fs",
            time.monotonic() - flow_started,
        )
        return WorkflowGenerateResult(
            status=WorkflowGenerateStatus.PROPOSAL,
            form=outcome.form,
            warnings=prepared.warnings + outcome.warnings,
        )

    logger.info(
        "workflow_document_flow: first output FAILED (%d errors) → repair "
        "(max=%d). Errors: %s",
        len(outcome.errors),
        max_repair_attempts,
        "; ".join(outcome.errors)[:400],
    )

    # --- Bounded repair (TEXT-ONLY: source not re-sent) -------------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_workflow_repair_prompt(
            last_raw, last_errors, is_edit_mode=False
        )
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return WorkflowGenerateResult(
                status=WorkflowGenerateStatus.FAILED,
                errors=[f"LLM repair call failed: {exc}"],
            )
        outcome = _parse_and_validate_workflow(last_raw, wants_flat=True)
        if outcome.ok:
            logger.info(
                "workflow_document_flow: SUCCESS after %d repair(s), total=%.1fs",
                attempts,
                time.monotonic() - flow_started,
            )
            return WorkflowGenerateResult(
                status=WorkflowGenerateStatus.PROPOSAL,
                form=outcome.form,
                warnings=prepared.warnings + outcome.warnings,
            )
        last_errors = outcome.errors

    logger.info(
        "workflow_document_flow: FAILED after %d repair(s), total=%.1fs",
        attempts,
        time.monotonic() - flow_started,
    )
    return WorkflowGenerateResult(
        status=WorkflowGenerateStatus.FAILED,
        errors=last_errors,
    )


async def run_workflow_document_flow_stream(
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    instruction: str | None,
    provider: LLMProvider,
    *,
    max_repair_attempts: int | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """
    Stream a workflow document-mode generation as (event, payload) tuples.

    Events:
        ("delta",   str)        → raw text fragment as the model writes it
        ("done",    dict)       → validated, apply-ready workflow form (terminal)
        ("error",   list[str])  → unrecoverable errors (terminal)

    Mirrors run_document_flow_stream() from document_flow.py.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    flow_started = time.monotonic()

    prepared = _prepare_input(file_bytes, filename, content_type)
    if prepared.error:
        yield ("error", [prepared.error])
        return

    # If streaming is not supported, fall back to non-streaming and emit once.
    if not getattr(provider, "supports_streaming", False):
        result = await run_workflow_document_flow(
            file_bytes, filename, content_type, instruction, provider,
            max_repair_attempts=max_repair_attempts,
        )
        if result.status is WorkflowGenerateStatus.FAILED:
            yield ("error", result.errors)
        else:
            yield ("done", result.form or {})
        return

    system_prompt = get_workflow_system_prompt()

    if prepared.extracted_text is not None:
        user_prompt = build_workflow_doc_prompt_text_fallback(
            instruction, prepared.extracted_text
        )
        attachments: list[Attachment] | None = None
    else:
        if not provider.supports_document_input:
            yield (
                "error",
                [
                    "The active AI provider does not support document input. "
                    "Upload a Word (.docx) file instead."
                ],
            )
            return
        assert prepared.attachment is not None
        user_prompt = build_workflow_doc_prompt(instruction)
        attachments = [prepared.attachment]

    # --- First call: STREAM, accumulate full text -------------------------
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
    outcome = _parse_and_validate_workflow(raw, wants_flat=True)
    if outcome.ok:
        logger.info(
            "workflow_document_flow(stream): SUCCESS on first call, total=%.1fs",
            time.monotonic() - flow_started,
        )
        yield ("done", outcome.form or {})
        return

    logger.info(
        "workflow_document_flow(stream): first output FAILED (%d errors) → repair",
        len(outcome.errors),
    )

    # --- Bounded repair (non-streaming, TEXT-ONLY) -----------------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_workflow_repair_prompt(
            last_raw, last_errors, is_edit_mode=False
        )
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            yield ("error", [f"LLM repair call failed: {exc}"])
            return
        outcome = _parse_and_validate_workflow(last_raw, wants_flat=True)
        if outcome.ok:
            logger.info(
                "workflow_document_flow(stream): SUCCESS after %d repair(s), "
                "total=%.1fs",
                attempts,
                time.monotonic() - flow_started,
            )
            yield ("done", outcome.form or {})
            return
        last_errors = outcome.errors

    logger.info(
        "workflow_document_flow(stream): FAILED after %d repair(s), total=%.1fs",
        attempts,
        time.monotonic() - flow_started,
    )
    yield ("error", last_errors)
