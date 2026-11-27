"""Document flow must apply the whole live KB: prompt block AND field binding."""

import asyncio
import io
import json
import re

from docx import Document

from app.orchestration import document_flow
from app.providers.base import LLMProvider

POLICY = {
    "key": "policy.test_rule",
    "kind": "policy",
    "title": "Test rule",
    "body": "RULE-MARKER-XYZ: every address block needs a PIN code.",
    "appliesTo": {"modes": [], "fieldTypes": [], "keywords": []},
    "payload": None,
}
DISTRICT_API = {
    "key": "geo.districts_by_state",
    "kind": "api_endpoint",
    "title": "Districts for a selected State",
    "body": "District list depends on State.",
    "appliesTo": {"modes": [], "fieldTypes": ["select"], "keywords": ["district", "state", "geo"]},
    "payload": {
        "url": "api/um/GeoUnit/GetGeoUnitDList",
        "method": "POST",
        "payload": {"parentId": "{state}"},
    },
}


def _docx_bytes() -> bytes:
    doc = Document()
    doc.add_paragraph("Service Name: Test Service")

    def section(title: str, rows: list[tuple[str, str, str, str]]) -> None:
        doc.add_paragraph(title)
        table = doc.add_table(rows=1, cols=4)
        for cell, text in zip(
            table.rows[0].cells, ("Field Name", "Type", "Mandatory?", "Field Rules & Logic")
        ):
            cell.text = text
        for row in rows:
            cells = table.add_row().cells
            for cell, text in zip(cells, row):
                cell.text = text

    section("1. Applicant", [("Full Name", "text", "Mandatory", "—")])
    section(
        "2. Address",
        [
            ("State", "select", "Mandatory", "—"),
            ("District", "select", "Mandatory", "Depends on State."),
            ("State of Origin", "select", "Mandatory", "—"),
        ],
    )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class _EchoSkeleton(LLMProvider):
    """Returns the skeleton from the enrich prompt unchanged (records every prompt)."""

    name = "echo"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._form: dict | None = None

    async def generate(self, system_prompt, user_prompt, attachments=None, *, max_tokens=None):
        self.prompts.append(user_prompt)
        m = re.search(r'SKELETON \(preserve structure exactly\):\n"""\n(.*?)\n"""', user_prompt, re.S)
        if m:
            form = json.loads(m.group(1))
            for sec in form["sections"]:
                for fld in sec["fields"]:
                    for i, opt in enumerate(fld.get("options") or []):
                        if isinstance(opt, dict) and not isinstance(opt.get("value"), int):
                            opt["value"] = i + 1
            self._form = form
        return json.dumps(self._form)


def test_document_flow_applies_kb_prompt_and_bindings(monkeypatch):
    async def fake_kb():
        return [POLICY, DISTRICT_API]

    monkeypatch.setattr(document_flow, "fetch_live_kb", fake_kb)
    provider = _EchoSkeleton()

    result = asyncio.run(
        document_flow.run_document_flow(_docx_bytes(), "spec.docx", None, None, provider)
    )

    assert result.status.value == "proposal", result.errors
    # Whole KB reaches the model, not just datasources.
    assert "PUBLISHED KNOWLEDGE BASE" in provider.prompts[0]
    assert "RULE-MARKER-XYZ" in provider.prompts[0]

    fields = {f["id"]: f for s in result.form["sections"] for f in s["fields"]}
    assert fields["district"]["kbKey"] == "geo.districts_by_state"
    assert fields["district"]["dependsOnAny"] == ["state"]
    # "state" is the district entry's parent role: it must not bind State fields.
    assert "datasource" not in fields["state"]
    assert "datasource" not in fields["state_of_origin"]


def test_document_flow_without_kb_is_unchanged(monkeypatch):
    async def no_kb():
        return []

    monkeypatch.setattr(document_flow, "fetch_live_kb", no_kb)
    provider = _EchoSkeleton()

    result = asyncio.run(
        document_flow.run_document_flow(_docx_bytes(), "spec.docx", None, None, provider)
    )

    assert result.status.value == "proposal", result.errors
    assert "PUBLISHED KNOWLEDGE BASE" not in provider.prompts[0]
