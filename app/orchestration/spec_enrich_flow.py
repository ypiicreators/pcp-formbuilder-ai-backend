"""
Parse -> skeleton -> one slim enrich call (fast, low tokens). Falls back to
section-by-section generation if enrich fails completeness or validation.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from app.config import get_settings
from app.core.doc_spec_parser import SpecCrossFieldValidator, SpecSection
from app.core.spec_skeleton import (
    build_spec_skeleton,
    skeleton_field_counts,
)
from app.orchestration.generate_flow import (
    GenerateResult,
    GenerateStatus,
    _Outcome,
    _parse_and_validate,
)
from app.prompts.spec_enrich_prompts import (
    build_spec_enrich_system_prompt,
    build_spec_enrich_user_prompt,
)
from app.prompts.user_prompts import build_repair_prompt
from app.providers.base import LLMError, LLMProvider

logger = logging.getLogger("app.orchestration.spec_enrich_flow")

Finalize = Callable[
    [dict[str, Any] | None, list[str], dict[str, Any] | None], GenerateResult
]


async def run_spec_enrich_flow(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    finalize: Finalize,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
    service_name: str | None = None,
    cross_field_validators: list[SpecCrossFieldValidator] | None = None,
) -> GenerateResult:
    settings = get_settings()
    if max_repair_attempts is None:
        max_repair_attempts = settings.max_repair_attempts

    started = time.monotonic()
    skeleton, manifest, skel_warnings = build_spec_skeleton(
        sections,
        service_name=service_name,
        cross_field_validators=cross_field_validators,
    )

    enriched, enrich_warnings, ok = await _enrich_once(
        skeleton,
        manifest,
        sections,
        provider,
        instruction,
        max_repair_attempts=max_repair_attempts,
        max_tokens=settings.doc_spec_enrich_max_tokens,
    )
    if not ok or enriched is None:
        return await _fallback_section_flow(
            sections,
            provider,
            instruction,
            finalize,
            base_form,
            max_repair_attempts,
            reason="enrich call failed",
        )

    outcome = _validate_enriched(sections, enriched)
    if not outcome.ok:
        repaired = await _repair_full_form(
            sections,
            enriched,
            provider,
            outcome.errors,
            max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
        )
        if repaired is not None:
            outcome = _validate_enriched(sections, repaired)

    if outcome.ok and outcome.form:
        warnings = skel_warnings + enrich_warnings + outcome.warnings
        warnings.insert(
            0,
            f"Built {len(sections)} section(s) via skeleton + enrich in "
            f"{time.monotonic() - started:.1f}s.",
        )
        logger.info(
            "spec_enrich_flow: SUCCESS sections=%d total=%.1fs",
            len(sections),
            time.monotonic() - started,
        )
        return finalize(outcome.form, warnings, base_form)

    return await _fallback_section_flow(
        sections,
        provider,
        instruction,
        finalize,
        base_form,
        max_repair_attempts,
        reason="; ".join(outcome.errors[:3]) if outcome.errors else "validation failed",
    )


async def run_spec_enrich_flow_stream(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    finalize: Finalize,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
    service_name: str | None = None,
    cross_field_validators: list[SpecCrossFieldValidator] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    settings = get_settings()
    if max_repair_attempts is None:
        max_repair_attempts = settings.max_repair_attempts

    started = time.monotonic()
    yield (
        "progress",
        {"phase": "skeleton", "sections": len(sections), "message": "Building form skeleton"},
    )
    skeleton, manifest, skel_warnings = build_spec_skeleton(
        sections,
        service_name=service_name,
        cross_field_validators=cross_field_validators,
    )
    yield ("partial", skeleton)

    yield (
        "progress",
        {"phase": "enrich", "sections": len(sections), "message": "Applying dependencies"},
    )

    enriched: dict[str, Any] | None = None
    enrich_warnings: list[str] = []
    raw_from_stream: str | None = None
    system = build_spec_enrich_system_prompt()
    user = build_spec_enrich_user_prompt(skeleton, manifest, instruction)

    if settings.doc_spec_enrich_use_stream and getattr(
        provider, "supports_streaming", False
    ):
        chunks: list[str] = []
        try:
            async for frag in provider.generate_stream(
                system,
                user,
                attachments=None,
                max_tokens=settings.doc_spec_enrich_max_tokens,
            ):
                chunks.append(frag)
                yield ("delta", frag)
            raw_from_stream = "".join(chunks)
            parsed = _parse_and_validate(raw_from_stream)
            if parsed.ok and parsed.form:
                enriched = parsed.form
                enrich_warnings = parsed.warnings
        except LLMError as exc:
            logger.warning("spec_enrich stream: %s", exc)

    if enriched is None and raw_from_stream:
        yield (
            "progress",
            {"phase": "repair", "message": "Fixing enrich output"},
        )
        partial = _parse_and_validate(raw_from_stream)
        base = partial.form if partial.ok and partial.form else skeleton
        repaired = await _repair_full_form(
            sections,
            base,
            provider,
            partial.errors if not partial.ok else [],
            max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
        )
        if repaired is not None:
            check = _validate_enriched(sections, repaired)
            if check.ok:
                enriched = check.form
                enrich_warnings = check.warnings

    if enriched is None:
        enriched, enrich_warnings, ok = await _enrich_once(
            skeleton,
            manifest,
            sections,
            provider,
            instruction,
            max_repair_attempts=max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
        )
        if not ok:
            async for ev in _fallback_section_stream(
                sections,
                provider,
                instruction,
                finalize,
                base_form,
                max_repair_attempts,
                "enrich failed",
            ):
                yield ev
            return

    outcome = _validate_enriched(sections, enriched)
    if not outcome.ok:
        yield (
            "progress",
            {"phase": "repair", "message": "Fixing validation issues"},
        )
        repaired = await _repair_full_form(
            sections,
            enriched,
            provider,
            outcome.errors,
            max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
        )
        if repaired is not None:
            outcome = _validate_enriched(sections, repaired)

    if outcome.ok and outcome.form:
        warnings = skel_warnings + enrich_warnings + outcome.warnings
        warnings.insert(
            0,
            f"Built {len(sections)} section(s) via skeleton + enrich in "
            f"{time.monotonic() - started:.1f}s.",
        )
        finalized = finalize(outcome.form, warnings, base_form)
        if finalized.status is GenerateStatus.FAILED:
            yield ("error", finalized.errors)
        else:
            yield ("done", finalized.form or {})
        return

    yield (
        "progress",
        {"phase": "fallback", "message": "Enrich incomplete — building section-by-section"},
    )
    async for ev in _fallback_section_stream(
        sections,
        provider,
        instruction,
        finalize,
        base_form,
        max_repair_attempts,
        "; ".join(outcome.errors[:2]) if outcome.errors else "validation",
    ):
        yield ev


def _validate_enriched(sections: list[SpecSection], form: dict[str, Any]) -> _Outcome:
    outcome = _parse_and_validate(json.dumps(form))
    if not outcome.ok:
        return outcome
    count_errors = skeleton_field_counts(sections, form)
    if count_errors:
        return _Outcome(ok=False, errors=count_errors)
    return outcome


async def _enrich_once(
    skeleton: dict,
    manifest: list[dict[str, str]],
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    max_repair_attempts: int,
    max_tokens: int,
) -> tuple[dict[str, Any] | None, list[str], bool]:
    system = build_spec_enrich_system_prompt()
    user = build_spec_enrich_user_prompt(skeleton, manifest, instruction)
    try:
        raw = await provider.generate(
            system, user, attachments=None, max_tokens=max_tokens
        )
    except LLMError as exc:
        logger.warning("spec_enrich: LLM error: %s", exc)
        return None, [], False
    outcome = _parse_and_validate(raw)
    if outcome.ok and outcome.form:
        count_errors = skeleton_field_counts(sections, outcome.form)
        if not count_errors:
            return outcome.form, outcome.warnings, True
        outcome = _Outcome(ok=False, errors=count_errors)
        logger.warning(
            "spec_enrich: count mismatch after enrich: %s",
            count_errors[:2],
        )

    base_form = outcome.form if outcome.form else skeleton
    errors = outcome.errors or ["Enrich output failed validation."]
    logger.warning("spec_enrich: attempting repair before fallback: %s", errors[:2])
    repaired = await _repair_full_form(
        sections,
        base_form,
        provider,
        errors,
        max_repair_attempts,
        max_tokens=max_tokens,
    )
    if repaired is not None:
        check = _validate_enriched(sections, repaired)
        if check.ok and check.form:
            return check.form, check.warnings, True

    logger.warning("spec_enrich: parse/validate failed: %s", errors[:2])
    return None, [], False


async def _repair_full_form(
    sections: list[SpecSection],
    form: dict[str, Any],
    provider: LLMProvider,
    errors: list[str],
    max_attempts: int,
    *,
    max_tokens: int,
) -> dict[str, Any] | None:
    system = build_spec_enrich_system_prompt()
    last_raw = json.dumps(form, ensure_ascii=False)
    last_errors = errors
    for _ in range(max_attempts):
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
        try:
            last_raw = await provider.generate(
                system, repair_prompt, attachments=None, max_tokens=max_tokens
            )
        except LLMError:
            return None
        parsed = _parse_and_validate(last_raw)
        if parsed.ok and parsed.form:
            check = skeleton_field_counts(sections, parsed.form)
            if not check:
                return parsed.form
            last_errors = check
        else:
            last_errors = parsed.errors
    return None


async def _fallback_section_flow(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    finalize: Finalize,
    base_form: dict[str, Any] | None,
    max_repair_attempts: int,
    reason: str,
) -> GenerateResult:
    logger.warning("spec_enrich: falling back to section-by-section (%s)", reason)
    from app.orchestration import spec_document_flow

    result = await spec_document_flow.run_spec_document_flow(
        sections,
        provider,
        instruction,
        finalize=finalize,
        base_form=base_form,
        max_repair_attempts=max_repair_attempts,
    )
    result.warnings = (result.warnings or []) + [
        f"Used section-by-section fallback ({reason}).",
    ]
    return result


async def _fallback_section_stream(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    finalize: Finalize,
    base_form: dict[str, Any] | None,
    max_repair_attempts: int,
    reason: str,
) -> AsyncIterator[tuple[str, Any]]:
    from app.orchestration import spec_document_flow

    logger.warning("spec_enrich stream: fallback (%s)", reason)
    async for event in spec_document_flow.run_spec_document_flow_stream(
        sections,
        provider,
        instruction,
        finalize=finalize,
        base_form=base_form,
        max_repair_attempts=max_repair_attempts,
    ):
        yield event