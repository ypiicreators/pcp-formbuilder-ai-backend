"""
Section-by-section generation for numbered pipe-table form specs (DOCX text path).

A single 11-section JSON often hits max_tokens or fails validation; the repair
loop then returns a smaller "valid" form. This module calls the model once per
parsed section and merges the results.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import get_settings
from app.core.doc_spec_parser import SpecSection, spec_summary_for_prompt
from app.orchestration.generate_flow import (
    GenerateResult,
    GenerateStatus,
    _parse_and_validate,
)
from app.prompts.spec_enrich_prompts import build_spec_enrich_system_prompt
from app.prompts.user_prompts import build_repair_prompt, build_section_spec_prompt
from app.providers.base import LLMError, LLMProvider

logger = logging.getLogger("app.orchestration.spec_document_flow")


@dataclass
class _SectionOutcome:
    ok: bool
    section: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


async def run_spec_document_flow(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    finalize,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
) -> GenerateResult:
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    started = time.monotonic()
    built, errors = await _build_all_sections(
        sections, provider, instruction, max_repair_attempts
    )
    if errors:
        return GenerateResult(status=GenerateStatus.FAILED, errors=errors)
    if built is None:
        return GenerateResult(status=GenerateStatus.FAILED, errors=["No sections built."])

    form = {"sections": built}
    outcome = _parse_and_validate(json.dumps(form))
    if not outcome.ok:
        return GenerateResult(status=GenerateStatus.FAILED, errors=outcome.errors)

    logger.info(
        "spec_document_flow: SUCCESS sections=%d total=%.1fs",
        len(built),
        time.monotonic() - started,
    )
    warnings = [
        f"Built {len(built)} section(s) from the document spec (section-by-section).",
    ]
    return finalize(outcome.form, warnings + outcome.warnings, base_form)


async def run_spec_document_flow_stream(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    finalize,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    started = time.monotonic()
    checklist = spec_summary_for_prompt(sections)
    total = len(sections)

    built: list[dict[str, Any]] = []
    for idx, sec in enumerate(sections, start=1):
        yield ("delta", f"\n/* Section {idx}/{total}: {sec.title} */\n")
        section_dict, err = await _generate_one_section(
            sec,
            idx,
            total,
            checklist,
            provider,
            instruction,
            max_repair_attempts,
        )
        if err:
            yield ("error", [err])
            return
        assert section_dict is not None
        built.append(section_dict)

    outcome = _parse_and_validate(json.dumps({"sections": built}))
    if not outcome.ok:
        yield ("error", outcome.errors)
        return

    logger.info(
        "spec_document_flow(stream): SUCCESS sections=%d total=%.1fs",
        len(built),
        time.monotonic() - started,
    )
    warnings = [
        f"Built {len(built)} section(s) from the document spec (section-by-section).",
    ]
    finalized = finalize(outcome.form, warnings + outcome.warnings, base_form)
    if finalized.status is GenerateStatus.FAILED:
        yield ("error", finalized.errors)
    else:
        yield ("done", finalized.form or {})


async def _build_all_sections(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
) -> tuple[list[dict[str, Any]] | None, list[str] | None]:
    checklist = spec_summary_for_prompt(sections)
    total = len(sections)
    built: list[dict[str, Any]] = []

    for idx, sec in enumerate(sections, start=1):
        section_dict, err = await _generate_one_section(
            sec,
            idx,
            total,
            checklist,
            provider,
            instruction,
            max_repair_attempts,
        )
        if err:
            return None, [err]
        assert section_dict is not None
        built.append(section_dict)

    return built, None


async def _generate_one_section(
    sec: SpecSection,
    index: int,
    total: int,
    checklist: str,
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
) -> tuple[dict[str, Any] | None, str | None]:
    # Slim system prompt (fallback path) — much lower input tokens per section.
    system_prompt = build_spec_enrich_system_prompt()
    rows = [(f.name, f.field_type, f.dependency) for f in sec.fields]
    user_prompt = build_section_spec_prompt(
        section_number=sec.number,
        section_title=sec.title,
        field_rows=rows,
        section_index=index,
        total_sections=total,
        instruction=instruction,
        document_checklist=checklist,
    )

    try:
        raw = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return None, f"LLM call failed for section {sec.number} ({sec.title}): {exc}"

    outcome = _parse_single_section(raw)
    if outcome.ok and outcome.section is not None:
        return outcome.section, None

    attempts = 0
    last_raw = raw
    last_errors = outcome.errors
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return None, f"LLM repair failed for section {sec.title}: {exc}"
        outcome = _parse_single_section(last_raw)
        if outcome.ok and outcome.section is not None:
            return outcome.section, None
        last_errors = outcome.errors

    return (
        None,
        f"Could not build section {sec.number} ({sec.title}): "
        + "; ".join(last_errors[:3]),
    )


def _parse_single_section(raw: str) -> _SectionOutcome:
    outcome = _parse_and_validate(raw)
    if not outcome.ok:
        return _SectionOutcome(ok=False, errors=outcome.errors)
    form = outcome.form or {}
    secs = form.get("sections")
    if not isinstance(secs, list) or len(secs) != 1:
        return _SectionOutcome(
            ok=False,
            errors=[
                "output must contain exactly one section in 'sections' "
                f"(got {len(secs) if isinstance(secs, list) else 'invalid'})."
            ],
        )
    section = secs[0]
    if not isinstance(section, dict):
        return _SectionOutcome(ok=False, errors=["section must be an object"])
    return _SectionOutcome(ok=True, section=section)