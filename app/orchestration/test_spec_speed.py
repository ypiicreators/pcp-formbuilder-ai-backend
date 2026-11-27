"""Spec (DOCX) path: concurrent sections, no duplicate enrich call, per-section KB scope."""

import asyncio
import time
from types import SimpleNamespace

from app.core.doc_spec_parser import SpecField, SpecSection
from app.kb.prompt import KbPromptContext
from app.orchestration import spec_document_flow, spec_enrich_flow
from app.orchestration.generate_flow import GenerateResult, GenerateStatus, _Outcome
from app.providers.base import LLMError, LLMProvider


def _sections(n: int) -> list[SpecSection]:
    return [
        SpecSection(
            number=i,
            title=f"Section {i}",
            fields=[SpecField(name=f"field_{i}", field_type="text")],
        )
        for i in range(1, n + 1)
    ]


def _settings(**overrides):
    base = dict(
        doc_spec_enrich_use_stream=True,
        doc_spec_enrich_max_tokens=24000,
        doc_spec_section_concurrency=4,
        max_repair_attempts=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _accept_any_form(monkeypatch):
    """Skip real validation: these tests check orchestration, not form rules."""
    monkeypatch.setattr(
        spec_document_flow,
        "_parse_and_validate",
        lambda raw: _Outcome(ok=True, form={"sections": []}, warnings=[]),
    )


def _finalize(form, warnings, base_form):
    return GenerateResult(status=GenerateStatus.PROPOSAL, form=form, warnings=warnings)


def test_sections_build_concurrently_and_keep_document_order(monkeypatch):
    monkeypatch.setattr(spec_document_flow, "get_settings", lambda: _settings())

    async def fake_section(sec, *_args, **_kwargs):
        await asyncio.sleep(0.2)
        return {"id": f"sec_{sec.number}"}, None

    monkeypatch.setattr(spec_document_flow, "_generate_one_section", fake_section)

    sections = _sections(4)
    started = time.monotonic()
    built, errors = asyncio.run(
        spec_document_flow._build_all_sections(sections, None, None, 2)
    )
    elapsed = time.monotonic() - started

    assert errors is None
    assert [s["id"] for s in built] == ["sec_1", "sec_2", "sec_3", "sec_4"]
    # Serial would take ~0.8s; four in parallel should finish in about one section's time.
    assert elapsed < 0.6


def test_stream_reports_real_progress_per_section(monkeypatch):
    monkeypatch.setattr(spec_document_flow, "get_settings", lambda: _settings())
    _accept_any_form(monkeypatch)

    async def fake_section(sec, *_args, **_kwargs):
        await asyncio.sleep(0.01)
        return {"id": f"sec_{sec.number}"}, None

    monkeypatch.setattr(spec_document_flow, "_generate_one_section", fake_section)

    async def collect():
        return [
            ev
            async for ev in spec_document_flow.run_spec_document_flow_stream(
                _sections(3), None, None, finalize=_finalize
            )
        ]

    events = asyncio.run(collect())
    finished = [
        p for kind, p in events
        if kind == "progress" and p.get("phase") == "sections"
    ]
    assert [p["done"] for p in finished] == [1, 2, 3]
    assert all(p["total"] == 3 for p in finished)
    creating = [
        p for kind, p in events
        if kind == "progress" and p.get("phase") == "section"
    ]
    assert len(creating) == 3
    assert not any(kind == "delta" for kind, _ in events)
    assert events[-1][0] == "done"


def test_failed_streamed_enrich_goes_to_sections_without_second_full_call(monkeypatch):
    monkeypatch.setattr(spec_enrich_flow, "get_settings", lambda: _settings())
    monkeypatch.setattr(spec_document_flow, "get_settings", lambda: _settings())
    _accept_any_form(monkeypatch)

    class StreamFails(LLMProvider):
        name = "stub"
        supports_streaming = True
        generate_calls = 0

        async def generate(self, *a, **k):
            StreamFails.generate_calls += 1
            raise AssertionError("no non-streamed full call expected")

        async def generate_stream(self, *a, **k):
            raise LLMError("Anthropic request failed: read timeout")
            yield  # pragma: no cover - makes this an async generator

    async def must_not_run(*_a, **_k):
        raise AssertionError("_enrich_once must not run after a failed streamed attempt")

    async def fake_section(sec, *_args, **_kwargs):
        return {"id": f"sec_{sec.number}"}, None

    monkeypatch.setattr(spec_enrich_flow, "_enrich_once", must_not_run)
    monkeypatch.setattr(spec_document_flow, "_generate_one_section", fake_section)

    async def collect():
        return [
            ev
            async for ev in spec_enrich_flow.run_spec_enrich_flow_stream(
                _sections(2), StreamFails(), None, finalize=_finalize
            )
        ]

    events = asyncio.run(collect())
    assert StreamFails.generate_calls == 0
    assert events[-1][0] == "done"


def test_kb_block_is_scoped_to_each_section():
    entries = [
        {
            "key": "policy.global",
            "kind": "policy",
            "title": "Global rule",
            "body": "GLOBAL-RULE",
            "appliesTo": {"modes": [], "fieldTypes": [], "keywords": []},
            "payload": None,
        },
        {
            "key": "geo.district_api",
            "kind": "api_endpoint",
            "title": "District list",
            "body": "",
            "appliesTo": {"modes": [], "fieldTypes": ["select"], "keywords": ["district"]},
            "payload": {"url": "api/x", "method": "POST", "payload": {"parentId": "{state}"}},
        },
    ]
    ctx = KbPromptContext(entries, mode="document")

    with_district = ctx.block_for("Section 2 | district | Mandatory")
    without_district = ctx.block_for("Father name | text | Mandatory")

    # Match the entry heading; the block's fixed header text also mentions the key name.
    heading = "(`geo.district_api`)"
    assert heading in with_district
    assert heading not in without_district
    # Global policies (no keywords) still ride along on every scoped block.
    assert "GLOBAL-RULE" in without_district
