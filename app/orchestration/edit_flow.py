"""
Edit-mode orchestration — the LINEAR pipeline that ties the deterministic layer
to the LLM (plan v3 §2.4, §7). This is the ONLY place the model is called for
Edit mode, and it is deliberately NOT a LangGraph graph (§7.12): a straight
line with a single bounded repair.

Pipeline:

    select_context(form, prompt)            # deterministic §7
        ├─ CLARIFY  -> return clarification (no LLM call)
        └─ else ↓
    build_edit_prompt(context, instruction)
    provider.generate(system, user)         # the LLM
    parse_change_set(raw)                    # deterministic
    apply_change_set(form, ops)              # deterministic, ID-anchored, atomic
    validate_form(updated)                   # L1 + L2
        ├─ ok    -> build diff, finalize
        └─ errors, attempt < max -> repair once, re-validate
                                  -> still bad -> 422

The FULL production form stays server-side throughout; the LLM only ever sees the
compact context (§7.9). The provider is INJECTABLE so the whole flow is testable
without credentials (pass a fake provider); in production it comes from the
factory driven by LLM_PROVIDER.

Async because providers do network I/O.
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
from app.core.context_selector import ContextLevel, select_context
from app.core.form_index import build_index
from app.core.change_set import Op  # noqa: F401  (re-exported for typing clarity)
from app.prompts.system_prompt import get_system_prompt
from app.prompts.user_prompts import build_edit_prompt, build_repair_prompt
from app.providers.base import LLMError, LLMProvider
from app.validation.validator import validate_form


class EditStatus(str, Enum):
    """Terminal outcome of an edit-flow run."""

    PROPOSAL = "proposal"                # success: form + diff ready for review
    NEEDS_CLARIFICATION = "needs_clarification"  # ambiguous target; ask the admin
    FAILED = "failed"                    # unrecoverable after repair budget


@dataclass
class EditResult:
    """
    What the route maps to an HTTP response.

    PROPOSAL           -> GenerateResponse(form, warnings, diff)
    NEEDS_CLARIFICATION-> ClarificationResponse(question, options)
    FAILED             -> 422 ErrorResponse(errors)
    """

    status: EditStatus
    form: dict[str, Any] | None = None
    diff: list[dict[str, Any]] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)
    clarification: list[dict[str, str]] = dc_field(default_factory=list)
    # Telemetry: which context level was used, for logs/tuning.
    context_level: str = ""


async def run_edit_flow(
    form: dict[str, Any],
    instruction: str,
    provider: LLMProvider,
    *,
    max_repair_attempts: int | None = None,
) -> EditResult:
    """
    Run the edit pipeline for `instruction` against `form` using `provider`.

    `provider` is injected so tests can supply a fake. Production passes the
    factory-built provider.
    """
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    # --- 1. Context selection (deterministic) -----------------------------
    selection = select_context(form, instruction)

    if selection.level is ContextLevel.CLARIFY:
        return EditResult(
            status=EditStatus.NEEDS_CLARIFICATION,
            clarification=selection.clarification,
            context_level=selection.level.value,
        )

    # --- 2. First LLM call ------------------------------------------------
    system_prompt = get_system_prompt()
    user_prompt = build_edit_prompt(selection.context or {}, instruction)

    try:
        raw = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return EditResult(
            status=EditStatus.FAILED,
            errors=[f"LLM call failed: {exc}"],
            context_level=selection.level.value,
        )

    # --- 3. Parse + apply + validate (deterministic) ----------------------
    outcome = _parse_apply_validate(form, raw)
    if outcome.ok:
        return _finalize(form, outcome.updated, outcome.warnings, selection.level.value)

    # --- 4. Bounded repair (one LLM call by default) ----------------------
    attempts = 0
    last_errors = outcome.errors
    last_raw = raw
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=True)
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return EditResult(
                status=EditStatus.FAILED,
                errors=[f"LLM repair call failed: {exc}"],
                context_level=selection.level.value,
            )
        outcome = _parse_apply_validate(form, last_raw)
        if outcome.ok:
            return _finalize(
                form, outcome.updated, outcome.warnings, selection.level.value
            )
        last_errors = outcome.errors

    # --- 5. Repair budget exhausted --------------------------------------
    return EditResult(
        status=EditStatus.FAILED,
        errors=last_errors,
        context_level=selection.level.value,
    )


# --- Internal step: parse -> apply -> validate ------------------------------


@dataclass
class _StepOutcome:
    ok: bool
    updated: dict[str, Any] | None = None
    applied_ids: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


def _parse_apply_validate(form: dict[str, Any], raw: str) -> _StepOutcome:
    """
    Turn raw LLM text into an applied+validated form, or a list of errors the
    repair prompt can act on. Pure/deterministic.
    """
    parsed = parse_change_set(raw)
    if not parsed.ok:
        return _StepOutcome(ok=False, errors=[f"change-set: {e}" for e in parsed.errors])

    applied: ApplyResult = apply_change_set(form, parsed.ops)
    if not applied.ok:
        return _StepOutcome(ok=False, errors=[f"apply: {e}" for e in applied.errors])

    result = validate_form(applied.form)
    if not result.ok:
        return _StepOutcome(ok=False, errors=result.error_messages())

    return _StepOutcome(
        ok=True,
        updated=applied.form,
        applied_ids=applied.applied_field_ids,
        warnings=result.warning_messages(),
    )


def _finalize(
    original: dict[str, Any],
    updated: dict[str, Any],
    warnings: list[str],
    context_level: str,
) -> EditResult:
    """Build the diff and package a PROPOSAL result."""
    # Recompute changed ids from the applied form vs original (robust even if
    # the step didn't carry them forward).
    changed = _changed_ids(original, updated)
    diff = diff_to_dicts(build_diff(original, updated, changed))
    return EditResult(
        status=EditStatus.PROPOSAL,
        form=updated,
        diff=diff,
        warnings=warnings,
        context_level=context_level,
    )


def _changed_ids(original: dict[str, Any], updated: dict[str, Any]) -> list[str]:
    """Field ids whose node changed (or was added/removed) between the two."""
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
