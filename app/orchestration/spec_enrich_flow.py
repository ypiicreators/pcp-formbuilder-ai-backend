"""
Parse -> skeleton -> one slim enrich call (fast, low tokens). Falls back to
section-by-section generation if enrich fails completeness or validation.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from app.core.doc_trace import stage
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
from app.kb.prompt import KbPromptContext
from app.orchestration.spec_document_flow import section_scope_text
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
    kb: KbPromptContext | None = None,
) -> GenerateResult:
    settings = get_settings()
    if max_repair_attempts is None:
        max_repair_attempts = settings.max_repair_attempts
    kb_block = kb.block_for(section_scope_text(sections)) if kb else ""

    started = time.monotonic()
    with stage(logger, "skeleton", sections=len(sections)) as st:
        skeleton, manifest, skel_warnings = build_spec_skeleton(
            sections,
            service_name=service_name,
            cross_field_validators=cross_field_validators,
            identity=kb.identity_for if kb else None,
        )
        st["fields"] = len(manifest)
        st["warnings"] = len(skel_warnings)
        st["kb_prompt_chars"] = len(kb_block)

    enriched, enrich_warnings, ok = await _enrich_once(
        skeleton,
        manifest,
        sections,
        provider,
        instruction,
        max_repair_attempts=max_repair_attempts,
        max_tokens=settings.doc_spec_enrich_max_tokens,
        kb_block=kb_block,
        kb_entries=kb.entries if kb else None,
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
            kb=kb,
        )

    outcome = _validate_enriched(sections, enriched, kb_entries=kb.entries if kb else None)
    if not outcome.ok:
        repaired = await _repair_full_form(
            sections,
            enriched,
            provider,
            outcome.errors,
            max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
            kb_block=kb_block,
            kb_entries=kb.entries if kb else None,
        )
        if repaired is not None:
            outcome = _validate_enriched(
                sections, repaired, kb_entries=kb.entries if kb else None
            )

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
        kb=kb,
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
    kb: KbPromptContext | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    settings = get_settings()
    if max_repair_attempts is None:
        max_repair_attempts = settings.max_repair_attempts
    kb_block = kb.block_for(section_scope_text(sections)) if kb else ""

    started = time.monotonic()
    yield (
        "progress",
        {"phase": "skeleton", "sections": len(sections), "message": "Building form skeleton"},
    )
    with stage(logger, "skeleton", sections=len(sections)) as st:
        skeleton, manifest, skel_warnings = build_spec_skeleton(
            sections,
            service_name=service_name,
            cross_field_validators=cross_field_validators,
            identity=kb.identity_for if kb else None,
        )
        st["fields"] = len(manifest)
        st["warnings"] = len(skel_warnings)
        st["kb_prompt_chars"] = len(kb_block)
    yield ("partial", skeleton)

    yield (
        "progress",
        {"phase": "enrich", "sections": len(sections), "message": "Applying dependencies"},
    )

    enriched: dict[str, Any] | None = None
    enrich_warnings: list[str] = []
    raw_from_stream: str | None = None
    system = build_spec_enrich_system_prompt()
    user = build_spec_enrich_user_prompt(skeleton, manifest, instruction, kb_block=kb_block)

    streamed = bool(
        settings.doc_spec_enrich_use_stream and getattr(provider, "supports_streaming", False)
    )
    if streamed:
        chunks: list[str] = []
        with stage(
            logger,
            "enrich",
            mode="stream",
            prompt_chars=len(system) + len(user),
            max_tokens=settings.doc_spec_enrich_max_tokens,
        ) as st:
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
                st["output_chars"] = len(raw_from_stream)
                parsed = _parse_and_validate(
                    raw_from_stream, kb_identity=kb.entries if kb else None
                )
                if parsed.ok and parsed.form:
                    enriched = parsed.form
                    enrich_warnings = parsed.warnings
                    st["result"] = "valid"
                else:
                    st["result"] = f"INVALID ({len(parsed.errors)} errors)"
                    logger.warning(
                        "spec_enrich stream: output failed validation (%d errors): %s",
                        len(parsed.errors),
                        " | ".join(parsed.errors[:5])[:800],
                    )
            except LLMError as exc:
                st["result"] = "LLM error"
                logger.warning("spec_enrich stream: LLM error: %s", exc)

    if enriched is None and raw_from_stream:
        yield (
            "progress",
            {"phase": "repair", "message": "Fixing enrich output"},
        )
        partial = _parse_and_validate(
            raw_from_stream, kb_identity=kb.entries if kb else None
        )
        base = partial.form if partial.ok and partial.form else skeleton
        repaired = await _repair_full_form(
            sections,
            base,
            provider,
            partial.errors if not partial.ok else [],
            max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
            kb_block=kb_block,
            kb_entries=kb.entries if kb else None,
        )
        if repaired is not None:
            check = _validate_enriched(
                sections, repaired, kb_entries=kb.entries if kb else None
            )
            if check.ok:
                enriched = check.form
                enrich_warnings = check.warnings

    if enriched is None and not streamed:
        enriched, enrich_warnings, _ok = await _enrich_once(
            skeleton,
            manifest,
            sections,
            provider,
            instruction,
            max_repair_attempts=max_repair_attempts,
            max_tokens=settings.doc_spec_enrich_max_tokens,
            kb_block=kb_block,
            kb_entries=kb.entries if kb else None,
        )
    if enriched is None:
        # A streamed attempt that failed is not retried as a second full call (each
        # attempt can run for minutes); go straight to the section-by-section path.
        async for ev in _fallback_section_stream(
            sections,
            provider,
            instruction,
            finalize,
            base_form,
            max_repair_attempts,
            "enrich failed",
            kb=kb,
        ):
            yield ev
        return

    outcome = _validate_enriched(sections, enriched, kb_entries=kb.entries if kb else None)
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
            kb_block=kb_block,
            kb_entries=kb.entries if kb else None,
        )
        if repaired is not None:
            outcome = _validate_enriched(
                sections, repaired, kb_entries=kb.entries if kb else None
            )

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
        kb=kb,
    ):
        yield ev


def _validate_enriched(
    sections: list[SpecSection],
    form: dict[str, Any],
    *,
    kb_entries: list | None = None,
) -> _Outcome:
    outcome = _parse_and_validate(json.dumps(form), kb_identity=kb_entries)
    if not outcome.ok:
        logger.warning(
            "spec_enrich: validation failed (%d errors): %s",
            len(outcome.errors),
            " | ".join(outcome.errors[:5])[:800],
        )
        return outcome
    count_errors = skeleton_field_counts(sections, form)
    if count_errors:
        logger.warning(
            "spec_enrich: field-count mismatch vs document (%d): %s",
            len(count_errors),
            " | ".join(count_errors[:5])[:800],
        )
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
    kb_block: str = "",
    kb_entries: list | None = None,
) -> tuple[dict[str, Any] | None, list[str], bool]:
    system = build_spec_enrich_system_prompt()
    user = build_spec_enrich_user_prompt(skeleton, manifest, instruction, kb_block=kb_block)
    with stage(
        logger, "enrich", mode="single-call", prompt_chars=len(system) + len(user)
    ) as st:
        try:
            raw = await provider.generate(
                system, user, attachments=None, max_tokens=max_tokens
            )
        except LLMError as exc:
            st["result"] = "LLM error"
            logger.warning("spec_enrich: LLM error: %s", exc)
            return None, [], False
        st["output_chars"] = len(raw)
    outcome = _parse_and_validate(raw, kb_identity=kb_entries)
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
        kb_block=kb_block,
        kb_entries=kb_entries,
    )
    if repaired is not None:
        check = _validate_enriched(sections, repaired, kb_entries=kb_entries)
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
    kb_block: str = "",
    kb_entries: list | None = None,
) -> dict[str, Any] | None:
    system = build_spec_enrich_system_prompt()
    last_raw = json.dumps(form, ensure_ascii=False)
    last_errors = errors
    for attempt in range(1, max_attempts + 1):
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
        if kb_block:
            repair_prompt = f"{repair_prompt}\n\n{kb_block}"
        with stage(
            logger,
            f"repair_full_form#{attempt}",
            attempt=f"{attempt}/{max_attempts}",
            fixing=len(last_errors),
            prompt_chars=len(repair_prompt),
        ) as st:
            logger.warning(
                "spec_enrich: repair %d/%d fixing: %s",
                attempt,
                max_attempts,
                " | ".join(last_errors[:3])[:500],
            )
            try:
                last_raw = await provider.generate(
                    system, repair_prompt, attachments=None, max_tokens=max_tokens
                )
            except LLMError as exc:
                st["result"] = "LLM error"
                logger.warning("spec_enrich: repair %d LLM error: %s", attempt, exc)
                return None
            parsed = _parse_and_validate(last_raw, kb_identity=kb_entries)
            if parsed.ok and parsed.form:
                check = skeleton_field_counts(sections, parsed.form)
                if not check:
                    st["result"] = "fixed"
                    return parsed.form
                last_errors = check
            else:
                last_errors = parsed.errors
            st["result"] = f"still invalid ({len(last_errors)} errors)"
    logger.warning("spec_enrich: repair exhausted; last errors: %s", " | ".join(last_errors[:3])[:500])
    return None


async def _fallback_section_flow(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    finalize: Finalize,
    base_form: dict[str, Any] | None,
    max_repair_attempts: int,
    reason: str,
    kb: KbPromptContext | None = None,
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
        kb=kb,
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
    kb: KbPromptContext | None = None,
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
        kb=kb,
    ):
        yield event