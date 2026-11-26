from pathlib import Path

from app.core.document_convert import extract_docx_text
from app.core.doc_spec_parser import parse_field_spec_document, parse_numbered_field_spec


SAMPLE = """\
1. Applicant Details
Field Name | Type | Dependency
Applicant Full Name (English) | text | —
2. Beneficiary's eKYC
Field Name | Type | Dependency
Do you want to opt for Aadhaar eKYC? | radio | —
Beneficiary Aadhaar eKYC | beneficiary_aadhaar_ekyc | Visible when YES
"""

FOUR_COL = """\
Service Name: Birth Certificate
1. Applicant Details
Field Name | Type | Mandatory? | Dependency
Applicant Full Name (English) | text | Mandatory | —
Applicant Full Name (Punjabi) | text | Mandatory | —
2. Contact
Field Name | Type | Mandatory? | Dependency
Mobile | phone | Non-mandatory | Visible when Applicant is adult
"""


def test_parse_sample_spec():
    spec = parse_numbered_field_spec(SAMPLE)
    assert spec is not None
    assert len(spec) == 2
    assert spec[0].title == "Applicant Details"
    assert len(spec[0].fields) == 1
    assert spec[1].fields[1].field_type == "beneficiary_aadhaar_ekyc"


def test_parse_four_column_header_and_service_name():
    parsed = parse_field_spec_document(FOUR_COL)
    assert parsed is not None
    assert parsed.service_name == "Birth Certificate"
    assert len(parsed.sections) == 2
    row = parsed.sections[0].fields[0]
    assert row.mandatory == "Mandatory"
    assert row.dependency == "—"
    row2 = parsed.sections[0].fields[1]
    assert row2.name.endswith("(Punjabi)")
    assert row2.mandatory == "Mandatory"
    mob = parsed.sections[1].fields[0]
    assert mob.mandatory == "Non-mandatory"
    assert "Visible when" in mob.dependency


CROSS_FIELD_APPENDIX = """\
10. Contact Details
Field Name | Type | Mandatory?
Mobile | phone | Mandatory
11. Self Declaration
Field Name | Type | Mandatory?
Declaration checkbox | checkbox | Mandatory
Declaration | declaration | Mandatory
Cross-Field Validators (Not a Section)
Validator ID | Type | Error Message | Expression
rule_a | custom | Error A | field_a == 'YES'
rule_b | custom | Error B | field_b < field_c
"""


def test_cross_field_validators_not_section_fields():
    parsed = parse_field_spec_document(CROSS_FIELD_APPENDIX)
    assert parsed is not None
    assert len(parsed.sections) == 2
    assert parsed.sections[1].title == "Self Declaration"
    assert len(parsed.sections[1].fields) == 2
    assert len(parsed.cross_field_validators) == 2
    assert parsed.cross_field_validators[0].validator_id == "rule_a"
    assert "field_a" in parsed.cross_field_validators[0].expression


def test_parse_user_docx_when_present():
    path = Path(
        "/Users/yashashvipaliwal/Downloads/"
        "service_form_json_field_dependencies_tables_only.docx"
    )
    if not path.is_file():
        return
    text = extract_docx_text(path.read_bytes())
    spec = parse_numbered_field_spec(text)
    assert spec is not None
    assert len(spec) == 11
    assert sum(len(s.fields) for s in spec) >= 50