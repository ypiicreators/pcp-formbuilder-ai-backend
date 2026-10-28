"""New Form Builder FEATURE_CONTRACTS — prompt, L1, compaction."""

from app.core.compaction import compact_form
from app.prompts.constants import (
    ALLOWED_TABLE_COLUMN_TYPES,
    ALLOWED_VALIDATOR_TYPES,
    render_feature_contracts,
)
from app.prompts.system_prompt import build_system_prompt
from app.validation.validator import validate_form


def _section(*fields: dict) -> dict:
    return {"sections": [{"id": "s1", "title": {"en": "S"}, "fields": list(fields)}]}


def test_allowed_lists_include_admin_types():
    for v in (
        "condition", "minAge", "maxAge", "futureOnly", "pastOnly",
        "dateRangeAroundToday", "dateRangeFromAnchor", "dateAfterField",
        "dateBeforeField", "workingDays", "excludeWeekends",
        "advanceBooking", "timeRange",
    ):
        assert v in ALLOWED_VALIDATOR_TYPES
    for t in ("button", "verification"):
        assert t in ALLOWED_TABLE_COLUMN_TYPES


def test_contracts_document_pending_features():
    text = render_feature_contracts()
    for needle in (
        "requiredExpression",
        "capitalizeAll",
        "doc_type",
        "enableBlurDetection",
        "autofillFromProfile",
        "autoFillFormAadharEkyc",
        "autoFillFormPan",
        "autofillFromOCR",
        "highlightRules",
        "trigger_mode",
        "payload_mapping",
        "computation",
        "form_field",
        "htmlContent",
        "configOptions",
        "otpSource",
        "verify_declaration",
        "dateRangeFromAnchor",
        "calculateAge",
    ):
        assert needle in text, needle


def test_system_prompt_mentions_new_envelopes():
    prompt = build_system_prompt()
    assert "requiredExpression" in prompt
    assert "htmlContent" in prompt
    assert "computed_table MUST include" in prompt
    assert '"button"' in prompt or "button" in prompt


def test_file_ocr_requires_doc_type():
    form = _section({
        "id": "scan",
        "type": "file",
        "label": {"en": "Scan"},
        "OCR": True,
    })
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "ocr_missing_doc_type" for e in result.errors)


def test_file_ocr_valid():
    form = _section({
        "id": "scan",
        "type": "file",
        "label": {"en": "Scan"},
        "allowedTypes": [".jpg", ".pdf"],
        "maxSizeMB": 5,
        "OCR": True,
        "doc_type": "aadhaar",
    })
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_computed_table_requires_computation():
    form = _section({
        "id": "out",
        "type": "computed_table",
        "label": {"en": "Out"},
        "columns": [{"id": "c1", "type": "text", "label": {"en": "C"}}],
    })
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "missing_computation" for e in result.errors)


def test_computed_table_difference_ok():
    form = _section(
        {
            "id": "all_plots",
            "type": "table",
            "label": {"en": "All"},
            "columns": [{"id": "plot_id", "type": "text", "label": {"en": "ID"}}],
        },
        {
            "id": "taken",
            "type": "table",
            "label": {"en": "Taken"},
            "columns": [{"id": "plot_id", "type": "text", "label": {"en": "ID"}}],
        },
        {
            "id": "remaining",
            "type": "computed_table",
            "label": {"en": "Remaining"},
            "computation": {
                "operation": "difference",
                "source_table": "all_plots",
                "subtract_table": "taken",
                "match_key": "plot_id",
            },
            "columns": [{"id": "plot_id", "type": "text", "label": {"en": "ID"}}],
        },
    )
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_html_description_requires_html_content():
    form = _section({
        "id": "help",
        "type": "htmlDescription",
        "label": {"en": "Help"},
    })
    result = validate_form(form)
    assert not result.ok
    assert any(e.code in ("missing_localized", "missing_en") for e in result.errors)


def test_html_description_ok():
    form = _section({
        "id": "help",
        "type": "htmlDescription",
        "label": {"en": "Help"},
        "htmlContent": {"en": "<p>Read this</p>"},
    })
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_button_and_verification_columns_allowed():
    form = _section({
        "id": "members",
        "type": "table",
        "label": {"en": "Members"},
        "columns": [
            {"id": "name", "type": "text", "label": {"en": "Name"}},
            {
                "id": "fetch",
                "type": "button",
                "label": {"en": "Fetch"},
                "api_config": {"endpoint": "api/x", "method": "POST"},
            },
            {"id": "kyc", "type": "verification", "label": {"en": "KYC"}},
        ],
    })
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_date_validators_accepted():
    form = _section({
        "id": "dob",
        "type": "date",
        "label": {"en": "DOB"},
        "format": "DD/MM/YYYY",
        "validators": [
            {"type": "minAge", "value": 18, "errorMessage": {"en": "18+"}},
            {"type": "pastOnly", "errorMessage": {"en": "Past"}},
            {"type": "workingDays", "errorMessage": {"en": "Weekday"}},
        ],
    })
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_form_field_datasource_needs_source():
    form = _section({
        "id": "members",
        "type": "table",
        "label": {"en": "Members"},
        "columns": [
            {
                "id": "pick",
                "type": "select",
                "label": {"en": "Pick"},
                "datasource": {"type": "form_field"},
            }
        ],
    })
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "missing_form_field_source" for e in result.errors)


def test_api_datasource_select_ok():
    form = _section({
        "id": "district",
        "type": "select",
        "label": {"en": "District"},
        "datasource": {"url": "api/geo", "method": "POST", "payload": {}},
    })
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_required_expression_needs_required():
    form = _section({
        "id": "guardian",
        "type": "text",
        "label": {"en": "Guardian"},
        "requiredExpression": "age < 18",
    })
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "required_expression_without_required" for e in result.errors)


def test_ekyc_autofill_and_highlight_ok():
    form = _section(
        {
            "id": "applicant_aadhaar",
            "type": "applicant_aadhaar_ekyc",
            "label": {"en": "Aadhaar eKYC"},
        },
        {
            "id": "applicant_name",
            "type": "text",
            "label": {"en": "Name"},
            "required": True,
            "trim": True,
            "capitalizeFirstWord": True,
            "source": "applicant_aadhaar_ekyc",
            "autoFillFormAadharEkyc": "Name",
            "lockAfterAutoFill": True,
            "highlightRules": [
                {
                    "id": "hl1",
                    "condition": {"compareWith": "applicant_name", "operator": "is_empty"},
                    "highlight": {"color": "#ff5722"},
                }
            ],
        },
    )
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_compact_marks_new_features():
    form = _section({
        "id": "scan",
        "type": "file",
        "label": {"en": "Scan"},
        "OCR": True,
        "doc_type": "pan",
        "allowedTypes": [".jpg"],
    })
    compact = compact_form(form)
    features = compact["sections"][0]["fields"][0].get("features", [])
    assert "OCR" in features
    assert "allowedTypes" in features
