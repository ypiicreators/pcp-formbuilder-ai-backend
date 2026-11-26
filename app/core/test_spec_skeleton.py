from pathlib import Path

from app.core.document_convert import extract_docx_text
from app.core.doc_spec_parser import parse_numbered_field_spec
from app.core.spec_skeleton import build_spec_skeleton, skeleton_field_counts
from app.prompts.spec_enrich_prompts import build_spec_enrich_system_prompt


def test_skeleton_matches_spec_counts():
    text = """\
1. Applicant Details
Field Name | Type | Dependency
Name | text | —
2. Beneficiary's eKYC
Field Name | Type | Dependency
Opt | radio | —
Child | beneficiary_aadhaar_ekyc | YES
"""
    spec = parse_numbered_field_spec(text)
    assert spec and len(spec) == 2
    form, manifest, _ = build_spec_skeleton(spec)
    assert len(form["sections"]) == 2
    assert len(form["sections"][0]["fields"]) == 1
    assert len(manifest) == 1  # YES dependency on second section
    assert skeleton_field_counts(spec, form) == []


def test_skeleton_transliteration_and_required():
    text = """\
Service Name: Demo
1. Names
Field Name | Type | Mandatory? | Dependency
Full Name (English) | text | Mandatory | —
Full Name (Punjabi) | text | Mandatory | —
2. Other
Field Name | Type | Mandatory? | Dependency
Notes | textarea | Non-mandatory | —
"""
    from app.core.doc_spec_parser import parse_field_spec_document

    parsed = parse_field_spec_document(text)
    assert parsed
    form, _, _ = build_spec_skeleton(parsed.sections, service_name=parsed.service_name)
    assert form["metadata"]["serviceName"] == "Demo"
    en = form["sections"][0]["fields"][0]
    pa = form["sections"][0]["fields"][1]
    assert en.get("required") is True
    assert pa.get("transliteration") is True
    assert pa.get("transliterationField") == en["id"]
    assert form["sections"][1]["fields"][0].get("required") is False


def test_slim_enrich_prompt_smaller_than_full_system():
    from app.prompts.system_prompt import build_system_prompt

    slim = build_spec_enrich_system_prompt()
    full = build_system_prompt()
    assert len(slim) < len(full) / 2


def test_user_docx_skeleton_section_count():
    path = Path(
        "/Users/yashashvipaliwal/Downloads/"
        "service_form_json_field_dependencies_tables_only.docx"
    )
    if not path.is_file():
        return
    spec = parse_numbered_field_spec(extract_docx_text(path.read_bytes()))
    assert spec is not None
    form, _, _ = build_spec_skeleton(spec)
    assert len(form["sections"]) == 11
    assert skeleton_field_counts(spec, form) == []