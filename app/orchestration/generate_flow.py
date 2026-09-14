"""
Generate-mode orchestration — description -> whole form.

Unlike Edit mode (which returns a small change-set applied to an existing form),
Generate mode asks the LLM to produce a COMPLETE form from a natural-language
description. The pipeline is shorter than edit — there is no context selection
and no change-set applier, because nothing exists yet:

    description
        -> build system + NL prompt
        -> provider.generate()      # LLM returns a WHOLE form object
        -> parse JSON               # a full form, not a change-set
        -> strip metadata           # keep the sections-first output contract
        -> validate_form (L1 + L2)  # reused; matters more here (free-form output)
        -> one bounded repair        # reuse the repair prompt
        -> return the form

The full form the LLM emits is the proposal itself; the frontend previews it and
applies it via loadFromJSON. Provider is INJECTABLE so it can be tested without
credentials; production passes the factory-built provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from app.config import get_settings
from app.core.change_set import _loads_tolerant  # fence-tolerant JSON parse
from app.prompts.system_prompt import get_system_prompt
from app.prompts.user_prompts import build_nl_prompt, build_repair_prompt
from app.providers.base import LLMError, LLMProvider
from app.validation.validator import validate_form

#: Top-level metadata keys the AI must not own (plan v3 §0/§5). Stripped from the
#: generated form so the output matches the sections-first contract; the admin
#: sets metadata manually.
_METADATA_KEYS = ("serviceCode", "title", "description", "category", "version")


class GenerateStatus(str, Enum):
    PROPOSAL = "proposal"     # success: a full form ready for review
    FAILED = "failed"         # unrecoverable after the repair budget


@dataclass
class GenerateResult:
    status: GenerateStatus
    form: dict[str, Any] | None = None
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


async def run_generate_flow(
    description: str,
    provider: LLMProvider,
    *,
    max_repair_attempts: int | None = None,
) -> GenerateResult:
    """Generate a complete form from `description` using `provider`."""
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    system_prompt = get_system_prompt()
    user_prompt = build_nl_prompt(description)

    # --- First LLM call ---------------------------------------------------
    try:
        raw = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return GenerateResult(status=GenerateStatus.FAILED, errors=[f"LLM call failed: {exc}"])

    outcome = _parse_and_validate(raw)
    if outcome.ok:
        return GenerateResult(
            status=GenerateStatus.PROPOSAL, form=outcome.form, warnings=outcome.warnings
        )

    # --- Bounded repair ---------------------------------------------------
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
            return GenerateResult(
                status=GenerateStatus.PROPOSAL, form=outcome.form, warnings=outcome.warnings
            )
        last_errors = outcome.errors

    return GenerateResult(status=GenerateStatus.FAILED, errors=last_errors)


# --- Internal: parse -> strip metadata -> validate --------------------------


@dataclass
class _Outcome:
    ok: bool
    form: dict[str, Any] | None = None
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


def _parse_and_validate(raw: str) -> _Outcome:
    obj, err = _loads_tolerant(raw)
    if err:
        return _Outcome(ok=False, errors=[f"could not parse form JSON: {err}"])
    if not isinstance(obj, dict) or not isinstance(obj.get("sections"), list):
        return _Outcome(ok=False, errors=["output must be a form object with a 'sections' array"])

    form = _strip_metadata(obj)
    result = validate_form(form)
    if not result.ok:
        return _Outcome(ok=False, errors=result.error_messages())
    return _Outcome(ok=True, form=form, warnings=result.warning_messages())


def _strip_metadata(form: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with backend-owned metadata keys removed."""
    return {k: v for k, v in form.items() if k not in _METADATA_KEYS}
