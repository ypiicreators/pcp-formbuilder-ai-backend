"""
Workflow Builder generate-mode orchestration — description → whole workflow form.

Mirrors generate_flow.py but uses the workflow system prompt and workflow
validator instead of the citizen form equivalents. Key differences:

  1. No metadata stripping — workflow forms have no serviceCode/title/category.
     The output is just { "sections": [...] }.

  2. Uses get_workflow_system_prompt() and build_workflow_nl_prompt() so the LLM
     knows it is building an officer-action UI schema, not a citizen form.

  3. Uses validate_workflow_form() which enforces the 37 workflow field types
     and workflow-specific rules (singletons, enableTransliteration, etc.).

  4. Exposes _parse_and_validate_workflow() as a module-level helper so
     workflow_document_flow.py can reuse the same parse → validate tail
     (identical pattern to how document_flow.py imports from generate_flow.py).

Pipeline:
    description
        → build_workflow_nl_prompt(description)
        → provider.generate(workflow_system_prompt, user_prompt)
        → _loads_tolerant()             # fence-tolerant JSON parse
        → _parse_and_validate_workflow()  # validate against workflow rules
        → bounded repair loop
        → return GenerateResult
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from app.config import get_settings
from app.core.change_set import _loads_tolerant
from app.prompts.workflow_system_prompt import get_workflow_system_prompt
from app.prompts.workflow_user_prompts import (
    build_workflow_nl_prompt,
    build_workflow_repair_prompt,
)
from app.providers.base import LLMError, LLMProvider
from app.validation.workflow_validator import validate_workflow_form


class WorkflowGenerateStatus(str, Enum):
    PROPOSAL = "proposal"
    FAILED = "failed"


@dataclass
class WorkflowGenerateResult:
    status: WorkflowGenerateStatus
    form: dict[str, Any] | None = None
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


async def run_workflow_generate_flow(
    description: str,
    provider: LLMProvider,
    *,
    max_repair_attempts: int | None = None,
) -> WorkflowGenerateResult:
    """Generate a complete workflow form from `description` using `provider`."""
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    # Detect if the user explicitly wants sections — only then do we keep the
    # sections wrapper. Otherwise default to flat array output.
    desc_lower = description.lower()
    wants_sections = any(
        word in desc_lower
        for word in ["section", "sections", "group", "groups", "grouped"]
    )
    wants_flat = not wants_sections

    system_prompt = get_workflow_system_prompt()
    user_prompt = build_workflow_nl_prompt(description)

    # --- First LLM call ---------------------------------------------------
    try:
        raw = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return WorkflowGenerateResult(
            status=WorkflowGenerateStatus.FAILED,
            errors=[f"LLM call failed: {exc}"],
        )

    outcome = _parse_and_validate_workflow(raw, wants_flat=wants_flat)
    if outcome.ok:
        return WorkflowGenerateResult(
            status=WorkflowGenerateStatus.PROPOSAL,
            form=outcome.form,
            warnings=outcome.warnings,
        )

    # --- Bounded repair loop ----------------------------------------------
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
        outcome = _parse_and_validate_workflow(last_raw, wants_flat=wants_flat)
        if outcome.ok:
            return WorkflowGenerateResult(
                status=WorkflowGenerateStatus.PROPOSAL,
                form=outcome.form,
                warnings=outcome.warnings,
            )
        last_errors = outcome.errors

    return WorkflowGenerateResult(
        status=WorkflowGenerateStatus.FAILED,
        errors=last_errors,
    )


# ---------------------------------------------------------------------------
# Internal: parse → validate (reused by workflow_document_flow.py)
# ---------------------------------------------------------------------------


@dataclass
class _WorkflowOutcome:
    ok: bool
    form: dict[str, Any] | None = None
    warnings: list[str] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


def _parse_and_validate_workflow(raw: str, wants_flat: bool = False) -> _WorkflowOutcome:
    """
    Parse raw LLM text into a workflow form and validate it.

    When `wants_flat=True` (default for generate mode when user didn't ask for
    sections), a sections-wrapped response is automatically unwrapped to a flat
    Field[] array before returning. This ensures the AI never silently wraps
    fields in a section when the user asked for a flat form.
    """
    obj, err = _loads_tolerant(raw)
    if err:
        return _WorkflowOutcome(
            ok=False,
            errors=[f"could not parse workflow form JSON: {err}"],
        )

    # Accept both flat array and sections object from the LLM.
    if isinstance(obj, list):
        # LLM returned a flat array — validate by wrapping temporarily.
        wrapped = {
            "sections": [
                {"id": "default_section", "title": {"en": ""}, "fields": obj}
            ]
        }
        result = validate_workflow_form(wrapped)
        if not result.ok:
            return _WorkflowOutcome(ok=False, errors=result.error_messages())
        # Return as flat array (what was requested).
        return _WorkflowOutcome(ok=True, form=obj, warnings=result.warning_messages())

    if not isinstance(obj, dict) or not isinstance(obj.get("sections"), list):
        return _WorkflowOutcome(
            ok=False,
            errors=[
                "workflow form output must be a JSON object with a 'sections' array "
                "or a flat Field[] array."
            ],
        )

    result = validate_workflow_form(obj)
    if not result.ok:
        return _WorkflowOutcome(ok=False, errors=result.error_messages())

    # If we wanted a flat array but got sections, unwrap automatically.
    if wants_flat:
        sections = obj.get("sections", [])
        flat_fields: list = []
        for sec in sections:
            if isinstance(sec, dict):
                flat_fields.extend(sec.get("fields") or [])
        return _WorkflowOutcome(ok=True, form=flat_fields, warnings=result.warning_messages())

    return _WorkflowOutcome(ok=True, form=obj, warnings=result.warning_messages())
