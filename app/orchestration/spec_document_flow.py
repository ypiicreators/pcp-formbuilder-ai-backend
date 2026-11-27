"""
Section-by-section generation for numbered pipe-table form specs (DOCX text path).

A single 11-section JSON often hits max_tokens or fails validation; the repair
loop then returns a smaller "valid" form. This module calls the model once per
parsed section and merges the results.

Sections are independent, so they are generated concurrently (bounded by
`doc_spec_section_concurrency`) and merged back in document order.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable

from app.core.doc_trace import scope, stage
from app.config import get_settings
from app.core.doc_spec_parser import SpecSection, spec_summary_for_prompt
from app.kb.prompt import KbPromptContext
from app.orchestration.progress import (
    creating_section,
    event as progress_event,
    finished_section,
    validating_section,
)
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


def section_scope_text(sections: Iterable[SpecSection]) -> str:
    """Text used to pick KB entries: section titles, field names and dependencies."""
    parts: list[str] = []
    for sec in sections:
        parts.append(sec.title)
        for f in sec.fields:
            parts.append(f.name)
            if f.dependency:
                parts.append(f.dependency)
    return "\n".join(parts)


async def run_spec_document_flow(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    *,
    finalize,
    base_form: dict[str, Any] | None = None,
    max_repair_attempts: int | None = None,
    kb: KbPromptContext | None = None,
) -> GenerateResult:
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    started = time.monotonic()
    built, errors = await _build_all_sections(
        sections, provider, instruction, max_repair_attempts, kb=kb
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
    kb: KbPromptContext | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    if max_repair_attempts is None:
        max_repair_attempts = get_settings().max_repair_attempts

    started = time.monotonic()
    total = len(sections)
    built: list[dict[str, Any] | None] = [None] * total
    done = 0

    gen = _generate_sections_with_starts(
        sections, provider, instruction, max_repair_attempts, kb=kb
    )
    try:
        async for kind, idx, section_dict, err in gen:
            title = sections[idx - 1].title
            if kind == "start":
                yield creating_section(idx, total, title)
                continue
            if err:
                yield ("error", [err])
                return
            yield validating_section(idx, total, title)
            built[idx - 1] = section_dict
            done += 1
            yield finished_section(done, total, title)
    finally:
        await gen.aclose()

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
    yield progress_event("finalize", "Applying platform field rules…")
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
    *,
    kb: KbPromptContext | None = None,
) -> tuple[list[dict[str, Any]] | None, list[str] | None]:
    built: list[dict[str, Any] | None] = [None] * len(sections)
    gen = _generate_sections_concurrently(
        sections, provider, instruction, max_repair_attempts, kb=kb
    )
    try:
        async for idx, section_dict, err in gen:
            if err:
                return None, [err]
            built[idx - 1] = section_dict
    finally:
        await gen.aclose()

    if any(s is None for s in built):
        return None, ["Some sections were not built."]
    return [s for s in built if s is not None], None


async def _generate_sections_with_starts(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
    *,
    kb: KbPromptContext | None = None,
) -> AsyncIterator[tuple[str, int, dict[str, Any] | None, str | None]]:
    """Yield ('start'| 'done', index, section_dict, error) as work begins and finishes."""
    limit = max(1, get_settings().doc_spec_section_concurrency)
    checklist = spec_summary_for_prompt(sections)
    total = len(sections)
    semaphore = asyncio.Semaphore(limit)
    queue: asyncio.Queue[
        tuple[str, int, dict[str, Any] | None, str | None]
    ] = asyncio.Queue()

    async def build(idx: int, sec: SpecSection) -> None:
        await queue.put(("start", idx, None, None))
        queued_at = time.monotonic()
        async with semaphore:
            waited = time.monotonic() - queued_at
            if waited >= 0.5:
                with scope(f"section {idx}/{total}"):
                    logger.info(
                        "waited %.1fs for a free slot (concurrency=%d)", waited, limit
                    )
            section_dict, err = await _generate_one_section(
                sec,
                idx,
                total,
                checklist,
                provider,
                instruction,
                max_repair_attempts,
                kb=kb,
            )
            await queue.put(("done", idx, section_dict, err))

    tasks = [
        asyncio.ensure_future(build(idx, sec))
        for idx, sec in enumerate(sections, start=1)
    ]
    remaining = len(tasks)
    try:
        while remaining:
            kind, idx, section_dict, err = await queue.get()
            yield kind, idx, section_dict, err
            if kind == "done":
                remaining -= 1
                if err:
                    break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def _generate_sections_concurrently(
    sections: list[SpecSection],
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
    *,
    kb: KbPromptContext | None = None,
) -> AsyncIterator[tuple[int, dict[str, Any] | None, str | None]]:
    """
    Yield (section_number_1_based, section_dict, error) as each section finishes.
    Completion order is not document order; callers place results by index.
    Pending work is cancelled when the consumer stops early.
    """
    limit = max(1, get_settings().doc_spec_section_concurrency)
    checklist = spec_summary_for_prompt(sections)
    total = len(sections)
    semaphore = asyncio.Semaphore(limit)

    async def build(idx: int, sec: SpecSection):
        queued_at = time.monotonic()
        async with semaphore:
            waited = time.monotonic() - queued_at
            if waited >= 0.5:
                with scope(f"section {idx}/{total}"):
                    logger.info(
                        "waited %.1fs for a free slot (concurrency=%d)", waited, limit
                    )
            section_dict, err = await _generate_one_section(
                sec,
                idx,
                total,
                checklist,
                provider,
                instruction,
                max_repair_attempts,
                kb=kb,
            )
            return idx, section_dict, err

    tasks = [
        asyncio.ensure_future(build(idx, sec))
        for idx, sec in enumerate(sections, start=1)
    ]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def _generate_one_section(
    sec: SpecSection,
    index: int,
    total: int,
    checklist: str,
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
    *,
    kb: KbPromptContext | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build one section; every log line inside carries its `section i/n` tag."""
    with scope(f"section {index}/{total}"), stage(
        logger, "section", title=sec.title, fields=len(sec.fields)
    ) as st:
        section_dict, err = await _generate_one_section_inner(
            sec, index, total, checklist, provider, instruction, max_repair_attempts, kb=kb
        )
        st["result"] = "ok" if err is None else "FAILED"
        if err:
            logger.error("section build failed: %s", err[:600])
        return section_dict, err


async def _generate_one_section_inner(
    sec: SpecSection,
    index: int,
    total: int,
    checklist: str,
    provider: LLMProvider,
    instruction: str | None,
    max_repair_attempts: int,
    *,
    kb: KbPromptContext | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    # Slim system prompt (fallback path) — much lower input tokens per section.
    system_prompt = build_spec_enrich_system_prompt()
    rows = []
    for f in sec.fields:
        ftype, dependency = f.field_type, f.dependency
        pinned = kb.identity_for(f.name, f.field_type) if kb else None
        if pinned:
            # KB pins this field's id/type: tell the model, so ids match the KB.
            ftype = str(pinned.get("type") or ftype)
            hint = f'Field id MUST be exactly "{pinned["id"]}" (Knowledge Base).'
            dependency = f"{dependency} {hint}".strip() if dependency else hint
        rows.append((f.name, ftype, dependency))
    user_prompt = build_section_spec_prompt(
        section_number=sec.number,
        section_title=sec.title,
        field_rows=rows,
        section_index=index,
        total_sections=total,
        instruction=instruction,
        document_checklist=checklist,
    )
    # KB scoped to this section only: the entries its fields and dependencies match.
    kb_block = kb.block_for(section_scope_text([sec])) if kb else ""
    if kb_block:
        user_prompt = f"{user_prompt}\n\n{kb_block}"

    logger.info(
        "section prompt: chars=%d kb_chars=%d", len(system_prompt) + len(user_prompt), len(kb_block)
    )
    try:
        raw = await provider.generate(system_prompt, user_prompt)
    except LLMError as exc:
        return None, f"LLM call failed for section {sec.number} ({sec.title}): {exc}"

    outcome = _parse_single_section(raw, kb)
    if outcome.ok and outcome.section is not None:
        return outcome.section, None
    logger.warning(
        "section output invalid (%d errors) -> repair: %s",
        len(outcome.errors),
        " | ".join(outcome.errors[:3])[:500],
    )

    attempts = 0
    last_raw = raw
    last_errors = outcome.errors
    while attempts < max_repair_attempts:
        attempts += 1
        repair_prompt = build_repair_prompt(last_raw, last_errors, is_edit_mode=False)
        if kb_block:
            repair_prompt = f"{repair_prompt}\n\n{kb_block}"
        logger.warning("section repair %d/%d", attempts, max_repair_attempts)
        try:
            last_raw = await provider.generate(system_prompt, repair_prompt)
        except LLMError as exc:
            return None, f"LLM repair failed for section {sec.title}: {exc}"
        outcome = _parse_single_section(last_raw, kb)
        if outcome.ok and outcome.section is not None:
            logger.info("section fixed by repair %d", attempts)
            return outcome.section, None
        last_errors = outcome.errors
        logger.warning(
            "section repair %d still invalid: %s",
            attempts,
            " | ".join(last_errors[:3])[:500],
        )

    return (
        None,
        f"Could not build section {sec.number} ({sec.title}): "
        + "; ".join(last_errors[:3]),
    )


def _parse_single_section(raw: str, kb: KbPromptContext | None = None) -> _SectionOutcome:
    outcome = _parse_and_validate(raw, kb_identity=kb.entries if kb else None)
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
