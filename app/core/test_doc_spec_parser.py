from pathlib import Path

from app.core.document_convert import extract_docx_text
from app.core.doc_spec_parser import (
    parse_field_spec_document,
    parse_numbered_field_spec,
    spec_from_object,
)


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


FIREARMS_DOCX = Path(
    "/Users/yashashvipaliwal/Downloads/"
    "Issuance of Firearms Free Zone License service.docx"
)


OUTLINE = """\
Application Details
Applicant Name
Father Name
Gender
Service Details
Nomenclature
Name of Company/Business
Business Address
Documents
Application Form
Site Plan
"""


def test_titled_outline_without_tables():
    parsed = parse_field_spec_document(OUTLINE)
    assert parsed is not None
    assert [s.title for s in parsed.sections] == [
        "Application Details",
        "Service Details",
        "Documents",
    ]
    assert [f.name for f in parsed.sections[1].fields] == [
        "Nomenclature",
        "Name of Company/Business",
        "Business Address",
    ]


def test_numbered_outline_without_pipe_rows():
    text = """\
1. Application Details
Applicant Name
Father Name
2. Service Details
1. Nomenclature (Mandatory) : Dropdown
2. Business Address (Mandatory) : Textbox
3. Documents
1. Application Form (Mandatory)
"""
    parsed = parse_field_spec_document(text)
    assert parsed is not None
    assert len(parsed.sections) == 3
    assert parsed.sections[1].fields[0].name == "Nomenclature"
    assert parsed.sections[1].fields[0].field_type == "select"


def test_spec_from_object_accepts_aliases():
    parsed = spec_from_object(
        {
            "serviceName": "Firearms",
            "sections": [
                {
                    "title": "Service Details",
                    "fields": [
                        {"label": {"en": "Nomenclature"}, "fieldType": "dropdown"},
                        "Business Address (Mandatory) : Textbox",
                    ],
                },
                {"name": "Documents", "fields": [{"name": "Site Plan", "type": "file"}]},
            ],
        }
    )
    assert parsed is not None
    assert parsed.service_name == "Firearms"
    assert parsed.sections[0].fields[0].name == "Nomenclature"
    assert parsed.sections[1].fields[0].name == "Site Plan"


def test_catalog_table_preserves_section_field_names():
    """Service | Section | Field catalog: 3 sections, not a 4-col Field Name|Type table."""
    if not FIREARMS_DOCX.is_file():
        return
    text = extract_docx_text(FIREARMS_DOCX.read_bytes())
    parsed = parse_field_spec_document(text)
    assert parsed is not None
    titles = [s.title for s in parsed.sections]
    assert titles == ["Application Details", "Service Details", "Documents"]
    assert "Declaration" not in titles
    app_names = [f.name for f in parsed.sections[0].fields]
    assert "Applicant Name" in app_names
    assert "Father Name" in app_names
    service_names = [f.name for f in parsed.sections[1].fields]
    assert "Nomenclature" in service_names
    assert "Name of Company/Business" in service_names
    assert "Nature of business or activity carried out" in service_names
    assert len(service_names) >= 18
    docs = [f.name for f in parsed.sections[2].fields]
    assert any("Application Form" in n for n in docs)
    assert any("checkbox" in n.lower() or n.lower() == "declaration" for n in docs)


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