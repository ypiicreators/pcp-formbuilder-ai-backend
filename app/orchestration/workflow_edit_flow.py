"""
Workflow Builder edit-mode orchestration — form + instruction → change-set.

Mirrors edit_flow.py but adds two critical differences:

  1. NORMALIZE / DENORMALIZE — workflow forms can arrive as either a flat
     Field[] array or a { "sections": [...] } object. All internal processing
     works on the sections-based representation. We normalize at entry,
     process, then denormalize the result back to the original shape before
     returning so the frontend gets back exactly what it sent.

  2. WORKFLOW PROMPT + VALIDATOR — uses get_workflow_system_prompt(),
     build_workflow_edit_prompt(), and validate_workflow_form() instead of
     their citizen-form equivalents.

Everything else — context selection, change-set parsing and application,
repair loop, diff building — is reused unchanged from the shared core layer.

Pipeline:
    (form: dict | list, instruction, provider, target_field_ids?)
        → detect shape → normalize to sections if needed (was_flat flag)
        → select_context(form, prompt) or select_context_for_ids()
            ├─ CLARIFY → return clarification (no LLM call)
            └─ else ↓
        → build_workflow_edit_prompt(context, instruction)
        → provider.generate(workflow_system_prompt, user_prompt)
        → parse_change_set → apply_change_set → validate_workflow_form
            ├─ ok    → build diff → denormalize → return PROPOSAL
            └─ errors → bounded repair → re-validate
                      → still bad → FAILED
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from app.config import get_settings
from app.core.change_set import (
    ApplyResult,
    apply_change_set,
    build_diff,
    diff_to_dicts,
    parse_change_set,
)
from app.core.context_selector import (
    ContextLevel,
    select_context,
    select_context_for_ids,
)
from app.core.form_index import build_index
from app.prompts.workflow_system_prompt import get_workflow_system_prompt
from app.prompts.workflow_user_prompts import (
    build_workflow_edit_prompt,
    build_workflow_repair_prompt,
)
from app.providers.base import LLMError, LLMProvider
from app.validation.workflow_validator import validate_workflow_form

#: Synthetic section id used when wrapping a flat array for internal processing.
_FLAT_SECTION_ID = "default_section"


class WorkflowEditStatus(str, Enum):
    PROPOSAL = "proposal"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"


@dataclass
class WorkflowEditResult:
    """
    Terminal outcome of a workflow edit-flow run.

    PROPOSAL            → GenerateResponse(form, diff, warnings)
    NEEDS_CLARIFICATION → ClarificationResponse(question, options)
    FAILED              → 422 ErrorResponse(errors)

    `was_flat` is carried here so the route can denormalize the response
    in a single place without re-detecting the shape.
    """
    status: WorkflowEditStatus
    form: dict[str, Any] | list[dict[str, Any]] | None = None
    diff: list[dict[str, Any]] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)
    clarification: list[dict[str, str]] = dc_field(default_factory=list)
    context_level: str = ""
    was_flat: bool = False


# ---------------------------------------------------------------------------
# Normalize / Denormalize helpers
# ---------------------------------------------------------------------------


def normalize_workflow_input(
    raw: dict[str, Any] | list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """
    Accept either a flat Field[] array or a sections object and always return
    a sections-based dict for internal processing.

    Returns (normalized_form, was_flat) where was_flat=True means the original
    input was a flat array and must be restored before returning to the frontend.
    """
    if isinstance(raw, list):
        # Flat array → wrap in a synthetic default section.
        return (
            {
                "sections": [
                    {
                        "id": _FLAT_SECTION_ID,
                        "title": {"en": ""},
                        "fields": raw,
                    }
                ]
            },
            True,  # was_flat
        )
    # Already sections-based — use as-is.
    return raw, False


def denormalize_workflow_output(
    form: dict[str, Any],
    was_flat: bool,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Restore the form to its original shape.

    If was_flat=True, extract and return the fields array from the synthetic
    default section. Otherwise return the sections object unchanged.
    """
    if not was_flat:
        return form

    sections = form.get("sections", [])
    if sections and isinstance(sections, list):
        # Return the fields from the (only) section.
        fields = sections[0].get("fields", [])
        return fields if isinstance(fields, list) else []
    return []


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


async def run_workflow_edit_flow(
    raw_form: dict[str, Any] | list[dict[str, Any]],
    instruction: str,
    provider: LLMProvider,
    *,
    target_field_ids: list[str] | None = None,
    max_repair_attempts: int | None = None,
) -> WorkflowEditResult:
    """
    Run the workflow edit pipeline.

    `raw_form` may be a flat Field[] array or a sections dict — both accepted.
    `target_field_ids` is the "reference chip" flow: when set, we bypass NL
    resolution and edit exactly those fields.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    # --- 1. Normalize to sections-based -----------------------------------
    form, was_flat = normalize_workflow_input(raw_form)

    # --- 2. Context selection (deterministic) -----------------------------
    if target_field_ids:
        selection = select_context_for_ids(form, target_field_ids)
    else:
        selection = select_context(form, instruction)

        if selection.level is ContextLevel.CLARIFY:
            return WorkflowEditResult(
                status=WorkflowEditStatus.NEEDS_CLARIFICATION,
                clarification=selection.clarification,
                context_level=selection.level.value,
                was_flat=was_flat,
            )

    # --- 3. First LLM call ------------------------------------------------
    system_prompt = get_workflow_system_prompt()
    user_prompt = build_workflow_edit_prompt(
        selection.context or {}, instruction, target_field_ids=target_field_ids
    )

    try:
        raw_output = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return WorkflowEditResult(
            status=WorkflowEditStatus.FAILED,
            errors=[f"LLM call failed: {exc}"],
            context_level=selection.level.value,
            was_flat=was_flat,
        )

    # --- 4. Parse → Apply → Validate -------------------------------------
    outcome = _parse_apply_validate_workflow(form, raw_output)
    if outcome.ok:
        return _finalize_workflow(
            form, outcome.updated, outcome.warnings,
            selection.level.value, was_flat,
        )

    # --- 5. Bounded repair loop ------------------------------------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw_output
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_workflow_repair_prompt(
            last_raw, last_errors, is_edit_mode=True
        )
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return WorkflowEditResult(
                status=WorkflowEditStatus.FAILED,
                errors=[f"LLM repair call failed: {exc}"],
                context_level=selection.level.value,
                was_flat=was_flat,
            )
        outcome = _parse_apply_validate_workflow(form, last_raw)
        if outcome.ok:
            return _finalize_workflow(
                form, outcome.updated, outcome.warnings,
                selection.level.value, was_flat,
            )
        last_errors = outcome.errors

    return WorkflowEditResult(
        status=WorkflowEditStatus.FAILED,
        errors=last_errors,
        context_level=selection.level.value,
        was_flat=was_flat,
    )


# ---------------------------------------------------------------------------
# Internal: parse → apply → validate
# ---------------------------------------------------------------------------


@dataclass
class _StepOutcome:
    ok: bool
    updated: dict[str, Any] | None = None
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


def _parse_apply_validate_workflow(
    form: dict[str, Any], raw: str
) -> _StepOutcome:
    """
    Parse a raw LLM change-set, apply it to the (sections-based) form, and
    validate the result against the workflow schema.
    """
    parsed = parse_change_set(raw)
    if not parsed.ok:
        return _StepOutcome(
            ok=False,
            errors=[f"change-set parse error: {e}" for e in parsed.errors],
        )

    applied: ApplyResult = apply_change_set(form, parsed.ops)
    if not applied.ok:
        return _StepOutcome(
            ok=False,
            errors=[f"change-set apply error: {e}" for e in applied.errors],
        )

    result = validate_workflow_form(applied.form)
    if not result.ok:
        return _StepOutcome(ok=False, errors=result.error_messages())

    return _StepOutcome(
        ok=True,
        updated=applied.form,
        warnings=result.warning_messages(),
    )


def _finalize_workflow(
    original: dict[str, Any],
    updated: dict[str, Any],
    warnings: list[str],
    context_level: str,
    was_flat: bool,
) -> WorkflowEditResult:
    """Build the diff, denormalize the result, and package a PROPOSAL."""
    changed = _changed_ids(original, updated)
    diff = diff_to_dicts(build_diff(original, updated, changed))

    # Denormalize back to the original shape (flat array or sections object).
    output_form = denormalize_workflow_output(updated, was_flat)

    return WorkflowEditResult(
        status=WorkflowEditStatus.PROPOSAL,
        form=output_form,
        diff=diff,
        warnings=warnings,
        context_level=context_level,
        was_flat=was_flat,
    )


def _changed_ids(
    original: dict[str, Any], updated: dict[str, Any]
) -> list[str]:
    """Field ids whose node changed (or was added/removed) between the two forms."""
    before = build_index(original)
    after = build_index(updated)
    ids: list[str] = []
    all_ids = before.all_field_ids() | after.all_field_ids()
    for fid in all_ids:
        b = before.get_field(fid)
        a = after.get_field(fid)
        if (b is None) != (a is None):
            ids.append(fid)
        elif b is not None and a is not None and b.node != a.node:
            ids.append(fid)
    return ids
