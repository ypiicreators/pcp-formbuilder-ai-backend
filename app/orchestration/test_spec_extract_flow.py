"""LLM spec extract: unknown layouts become the same ParsedFieldSpec."""

import asyncio
import json

from app.orchestration.spec_extract_flow import extract_spec_from_document
from app.providers.base import LLMProvider


class _SpecStub(LLMProvider):
    name = "stub"

    async def generate(self, system_prompt, user_prompt, attachments=None, *, max_tokens=None):
        assert "field SPEC" in system_prompt or "FIELD SPEC" in system_prompt
        return json.dumps(
            {
                "serviceName": "Firearms",
                "sections": [
                    {
                        "title": "Application Details",
                        "fields": [{"name": "Applicant Name", "type": "text"}],
                    },
                    {
                        "title": "Service Details",
                        "fields": [
                            {"name": "Nomenclature", "type": "select"},
                            {"name": "Business Address", "type": "text"},
                        ],
                    },
                    {
                        "title": "Documents",
                        "fields": [{"name": "Application Form", "type": "file"}],
                    },
                ],
            }
        )


def test_extract_spec_from_freeform_text():
    parsed = asyncio.run(
        extract_spec_from_document(
            _SpecStub(),
            extracted_text="random write-up, not a pipe table",
            instruction="frame json from the table",
        )
    )
    assert parsed is not None
    assert [s.title for s in parsed.sections] == [
        "Application Details",
        "Service Details",
        "Documents",
    ]
    assert [f.name for f in parsed.sections[1].fields] == [
        "Nomenclature",
        "Business Address",
    ]
