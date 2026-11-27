"""Document flow: the KB-pinned id (applying_for_yourself) reaches the generated form."""

import asyncio
import io
import json
import re

from docx import Document

from app.kb.identity import rename_field_id
from app.orchestration import document_flow
from app.providers.base import LLMProvider

IDENTITY = {
    "key": "std.applying_for_yourself",
    "kind": "template",
    "title": "Applying for yourself (fixed id)",
    "body": "Fixed id for the self/beneficiary control.",
    "appliesTo": {"modes": [], "fieldTypes": [], "keywords": ["applying for yourself"]},
    "payload": {
        "id": "applying_for_yourself",
        "type": "applying_for",
        "options": [
            {"value": "YES", "label": {"en": "Yes"}},
            {"value": "NO", "label": {"en": "No"}},
        ],
    },
}


def _docx_bytes() -> bytes:
    doc = Document()
    doc.add_paragraph("Service Name: Test Service")

    def section(title, rows):
        doc.add_paragraph(title)
        table = doc.add_table(rows=1, cols=4)
        for cell, text in zip(
            table.rows[0].cells, ("Field Name", "Type", "Mandatory?", "Field Rules & Logic")
        ):
            cell.text = text
        for row in rows:
            for cell, text in zip(table.add_row().cells, row):
                cell.text = text

    section(
        "1. Applicant Details",
        [
            ("Are you applying for yourself?", "radio", "Mandatory", "Controls other fields."),
            (
                "Relation with Beneficiary",
                "select",
                "Mandatory",
                "Visible when Are you applying for Yourself? = NO.",
            ),
            ("Applicant Full Name (English)", "text", "Mandatory", "—"),
        ],
    )
    section("2. Contact", [("Email ID", "email", "Non-mandatory", "—")])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class _Model(LLMProvider):
    """Stub enrich call. With `ignore_kb_id` it renames the field back, like a model
    that ignored the pinned id, and points a dependent field's visibleWhen at it."""

    name = "stub"

    def __init__(self, ignore_kb_id: bool = False) -> None:
        self.ignore_kb_id = ignore_kb_id
        self.prompts: list[str] = []
        self._form: dict | None = None

    async def generate(self, system_prompt, user_prompt, attachments=None, *, max_tokens=None):
        self.prompts.append(user_prompt)
        m = re.search(r'SKELETON \(preserve structure exactly\):\n"""\n(.*?)\n"""', user_prompt, re.S)
        if m:
            form = json.loads(m.group(1))
            for sec in form["sections"]:
                for f in sec["fields"]:
                    for i, opt in enumerate(f.get("options") or []):
                        if isinstance(opt, dict) and not isinstance(opt.get("value"), int):
                            opt["value"] = i + 1
            if self.ignore_kb_id:
                form = rename_field_id(form, "applying_for_yourself", "are_you_applying_for_yourself")
                for sec in form["sections"]:
                    for f in sec["fields"]:
                        if f["id"] == "relation_with_beneficiary":
                            f["visibleWhen"] = {"are_you_applying_for_yourself": ["NO"]}
            self._form = form
        return json.dumps(self._form)


POLICY = {
    "key": "policy.self_vs_beneficiary",
    "kind": "policy",
    "title": "Self vs beneficiary routing",
    "body": (
        "1. applying_for_yourself: type applying_for, id exactly applying_for_yourself,\n"
        "   options YES / NO (singleton).\n"
        "2. apply_for_beneficiary: radio YES/NO.\n"
    ),
    "appliesTo": {"modes": [], "fieldTypes": [], "keywords": ["applying for", "beneficiary"]},
    "payload": None,
}


def _run(monkeypatch, model, snapshot=None):
    live = snapshot if snapshot is not None else [IDENTITY]

    async def kb():
        return live

    monkeypatch.setattr(document_flow, "fetch_live_kb", kb)
    return asyncio.run(
        document_flow.run_document_flow(_docx_bytes(), "spec.docx", None, None, model)
    )


def _fields(result):
    return {f["id"]: f for s in result.form["sections"] for f in s["fields"]}


def test_kb_id_is_in_the_skeleton_the_model_receives_and_in_the_result(monkeypatch):
    model = _Model()
    result = _run(monkeypatch, model)

    assert result.status.value == "proposal", result.errors
    fields = _fields(result)
    assert "are_you_applying_for_yourself" not in fields
    assert fields["applying_for_yourself"]["type"] == "applying_for"
    assert [o["value"] for o in fields["applying_for_yourself"]["options"]] == ["YES", "NO"]
    # The model saw the KB id (so dependency text could be mapped onto it).
    assert '"id": "applying_for_yourself"' in model.prompts[0].replace('":"', '": "')


def test_safety_net_restores_kb_id_and_rewrites_references(monkeypatch):
    result = _run(monkeypatch, _Model(ignore_kb_id=True))

    assert result.status.value == "proposal", result.errors
    fields = _fields(result)
    assert "are_you_applying_for_yourself" not in fields
    assert fields["applying_for_yourself"]["type"] == "applying_for"
    # The dependent field now points at the KB id, not the model's id.
    assert fields["relation_with_beneficiary"]["visibleWhen"] == {
        "applying_for_yourself": ["NO"]
    }
    assert any("renamed field" in w for w in result.warnings)


def test_published_policy_without_payload_still_overrides_document_radio(monkeypatch):
    """Live Atlas stores Self vs beneficiary as a policy (payload=None); documents say radio."""
    model = _Model()
    result = _run(monkeypatch, model, snapshot=[POLICY])
    assert result.status.value == "proposal", result.errors
    fields = _fields(result)
    assert fields["applying_for_yourself"]["type"] == "applying_for"
    assert "are_you_applying_for_yourself" not in fields
    assert '"id": "applying_for_yourself"' in model.prompts[0].replace('":"', '": "')
