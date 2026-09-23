"""
Workflow Builder schema constants — the SINGLE SOURCE OF TRUTH for the
allowed lists used by BOTH the workflow system prompt and the workflow
validation layer.

Transcribed directly from the frontend AdditionalFieldsBuilder:
  - pcp-admin-portal/src/components/additionalFieldsBuilder/types.ts  (FieldType)
  - pcp-admin-portal/src/components/additionalFieldsBuilder/fieldLibrary.ts

Keep in sync if those files change. This is intentionally a SEPARATE constants
file from prompts/constants.py so the citizen form builder and the workflow
builder never share an allowed-type list — cross-contamination (the AI
generating a citizen-only type in a workflow form or vice versa) is caught at
validation time.
"""

# FieldType union for the Workflow / AdditionalFieldsBuilder — 37 types.
# KEY DIFFERENCES from the citizen form builder (constants.py):
#   WORKFLOW-ONLY (not in citizen form):
#     table_view, number_to_words, activity_timeline, previous_history,
#     role_wise_history, upload, input, password, url
#   CITIZEN-ONLY (must NOT appear in workflow):
#     applying_for, applicant_aadhaar_ekyc, beneficiary_aadhaar_ekyc,
#     guardian_aadhaar_ekyc, other_aadhaar_ekyc, pan_verification, declaration,
#     mobile_verification, in_form_login, otp, computed_table, verification
WORKFLOW_FIELD_TYPES: list[str] = [
    # Basic inputs
    "text",
    "input",          # alias for text in workflow context
    "email",
    "number",
    "textarea",
    "password",
    "url",
    # Selection
    "select",
    "multiselect",
    "radio",
    "checkbox",
    "toggle",
    # Date / time
    "date",
    "datetime",
    "time",
    # Files / media
    "file",
    "upload",         # alias for file in workflow context
    "live_capture",
    "live_capture_general",
    # Identity / validation inputs
    "phone",
    "pincode",
    "aadhaar",
    # Computed / display
    "calculated",
    "number_to_words",
    "age_display",
    "date_difference",
    "concatenate",
    # Rich content / layout
    "description",
    "htmlDescription",
    "line_break",
    "translate_textarea",
    # API / data
    "api_trigger",
    "table",
    "table_view",     # workflow-only: read-only table from API response
    # Workflow / history (officer-specific singletons)
    "activity_timeline",
    "previous_history",
    "role_wise_history",
]

# Validator types valid in workflow context.
# Extends the citizen form set with workflow-specific date validators.
WORKFLOW_VALIDATOR_TYPES: list[str] = [
    "regex",
    "minLength",
    "maxLength",
    "min",
    "max",
    "minValue",
    "maxValue",
    "email",
    "url",
    "minDate",
    "maxDate",
    "minAge",
    "maxAge",
    "futureOnly",
    "pastOnly",
    "todayAndFuture",
    "todayAndPast",
    "dateRangeFromToday",
    "specificDays",
    "dateAfterField",
    "dateBeforeField",
    "dateEqualOrAfterField",
    "dateEqualOrBeforeField",
    "workingDays",
    "excludeWeekends",
    # Observed in production corpus
    "required",
    "fileType",
    "maxSize",
    "trim",
    "noEmoji",
]

# Table column types for the workflow builder — simpler than citizen form.
# Only 5 basic types (no live_capture, configuration, _api_trigger etc).
WORKFLOW_TABLE_COLUMN_TYPES: list[str] = [
    "text",
    "number",
    "select",
    "date",
    "checkbox",
]

# Field types that MUST carry `options`.
WORKFLOW_TYPES_REQUIRING_OPTIONS: list[str] = [
    "select",
    "multiselect",
    "radio",
]

# Field types that MUST carry `columns`.
WORKFLOW_TYPES_REQUIRING_COLUMNS: list[str] = ["table"]

# Field types that MUST carry `tableViewColumns`.
WORKFLOW_TYPES_REQUIRING_TABLE_VIEW_COLUMNS: list[str] = ["table_view"]

# Field types that MUST carry `api_config`.
WORKFLOW_TYPES_REQUIRING_API_CONFIG: list[str] = ["api_trigger"]

# Singleton workflow types — may appear at most ONCE per form.
# (analogous to applying_for singleton in citizen form)
WORKFLOW_SINGLETON_TYPES: list[str] = [
    "activity_timeline",
    "previous_history",
    "role_wise_history",
]

# Known-good validator patterns (same as citizen form — phone/pincode regex).
WORKFLOW_PHONE_REGEX: str = r"^[6-9][0-9]{9}$"
WORKFLOW_PINCODE_REGEX: str = r"^[1-9][0-9]{5}$"


# ---------------------------------------------------------------------------
# FEATURE CONTRACTS for the workflow builder.
#
# Same concept as FEATURE_CONTRACTS in constants.py — each entry tells the AI
# the EXACT property names + shapes to use for a given feature on a field.
# Workflow-specific differences:
#   - transliteration uses "enableTransliteration" (not "transliteration")
#   - table_view uses "tableViewColumns" (no citizen form equivalent)
#   - number_to_words uses "sourceField" + "suffix"
#   - api_trigger shape is same as citizen form
# ---------------------------------------------------------------------------

WORKFLOW_FEATURE_CONTRACTS: list[dict] = [
    {
        "name": "Transliteration (script conversion, e.g. English → Punjabi)",
        "summary": (
            "Auto-convert one field's text into another script as the user types. "
            "In the workflow builder, the property name is \"enableTransliteration\" "
            "(boolean), NOT \"transliteration\". This is a key difference from the "
            "citizen form builder."
        ),
        "properties": {
            "enableTransliteration": "boolean — true to enable (NOT 'transliteration')",
            "transliterationField": (
                "string — the id of the OTHER field in the pair. Must be an existing field id."
            ),
            "transliterationWhen": (
                "optional { \"targetEmpty\"?: boolean, \"on\"?: string[] } "
                "(e.g. \"on\": [\"blur\"])"
            ),
        },
        "applies_to": "text, textarea, number, and most input fields",
        "example": {
            "id": "applicant_name_punjabi",
            "type": "textarea",
            "label": {"en": "Applicant Name (Punjabi)", "pa": "ਬਿਨੈਕਾਰ ਦਾ ਨਾਮ"},
            "required": True,
            "enableTransliteration": False,
            "transliterationField": "applicant_name_english",
        },
        "never": [
            "using \"transliteration\": true (wrong key in workflow builder — use \"enableTransliteration\")",
            "putting a field id in \"enableTransliteration\" (it is a boolean)",
            "omitting \"transliterationField\" when enableTransliteration is true",
        ],
    },
    {
        "name": "Table View (read-only display table from API data)",
        "summary": (
            "Display data returned from an API call as a read-only table. "
            "Used with type 'table_view'. The columns are defined by 'tableViewColumns'. "
            "This type does NOT exist in the citizen form builder."
        ),
        "properties": {
            "tableViewColumns": (
                "array of { id: string, label: LocalizedText, showTotal?: boolean } "
                "— defines which columns from the API data to display"
            ),
            "readonly": "boolean — always true for table_view",
        },
        "applies_to": "table_view field type only",
        "example": {
            "id": "fee_description",
            "type": "table_view",
            "label": {"en": "Fee Description", "pa": "ਫੀਸ ਦਾ ਵੇਰਵਾ"},
            "required": False,
            "readonly": True,
            "tableViewColumns": [
                {"id": "FeeName", "label": {"en": "Fee Name", "pa": "ਫੀਸ ਦਾ ਨਾਮ"}},
                {"id": "FeeAmount", "label": {"en": "Fee Amount", "pa": "ਫੀਸ ਦੀ ਰਕਮ"}, "showTotal": True},
            ],
        },
        "never": [
            "using 'columns' instead of 'tableViewColumns' for table_view",
            "using 'table_view' for editable tables (use 'table' instead)",
            "making table_view non-readonly",
        ],
    },
    {
        "name": "Number to Words (convert numeric value to text)",
        "summary": (
            "Display a number field's value as words (e.g. ₹36,000 → "
            "\"Thirty-Six Thousand Rupees Only\"). "
            "Uses type 'number_to_words' with a 'sourceField' pointing at the number field."
        ),
        "properties": {
            "sourceField": "string — the id of the number field to convert",
            "suffix": "string — optional suffix appended after the words (e.g. \"Rupees Only\")",
        },
        "applies_to": "number_to_words field type only",
        "example": {
            "id": "fee_in_words",
            "type": "number_to_words",
            "label": {"en": "Fee Amount in Words"},
            "sourceField": "total_fee",
            "suffix": "Rupees Only",
            "readonly": True,
        },
        "never": [
            "using 'number_to_words' without a 'sourceField'",
            "using 'formula' instead of 'sourceField' for number_to_words",
        ],
    },
    {
        "name": "API Trigger (button to call an API and fill fields)",
        "summary": (
            "A button field that triggers an API call when clicked (manual mode) "
            "or automatically on field change (auto mode). The API response is mapped "
            "to other fields via 'auto_fill_mapping'."
        ),
        "properties": {
            "trigger_mode": "\"manual\" | \"auto\" | \"form_load\"",
            "api_config": (
                "{ endpoint: string, method?: \"GET\"|\"POST\", base_url?: string, "
                "payload_mapping?: object, auto_fill_mapping?: object, "
                "on_success?: { show_message?: string }, "
                "on_error?: { show_message?: string, clear_fields?: string[] } }"
            ),
            "button_props": (
                "optional { type?: string, size?: string, label?: LocalizedText }"
            ),
            "trigger_on_change": "optional string[] — field ids that auto-trigger this",
            "debounce": "optional number — milliseconds debounce for auto triggers",
        },
        "applies_to": "api_trigger field type only",
        "example": {
            "id": "fetch_details",
            "type": "api_trigger",
            "label": {"en": "Fetch Details", "pa": "ਵੇਰਵੇ ਪ੍ਰਾਪਤ ਕਰੋ"},
            "trigger_mode": "manual",
            "api_config": {
                "endpoint": "api/service/GetDetails",
                "method": "POST",
                "payload_mapping": {"submission_id": "submission_id"},
                "auto_fill_mapping": {"CustomObject.Name": "applicant_name"},
                "on_success": {"show_message": ""},
                "on_error": {"show_message": "", "clear_fields": []},
            },
            "button_props": {
                "type": "default",
                "size": "middle",
                "label": {"en": "Fetch Details", "pa": "ਵੇਰਵੇ ਪ੍ਰਾਪਤ ਕਰੋ"},
            },
        },
        "never": [
            "using api_trigger without api_config",
            "omitting the 'endpoint' inside api_config",
        ],
    },
    {
        "name": "Conditional visibility / enable-disable",
        "summary": (
            "Show, hide, or disable a field or section based on other fields' values. "
            "Two supported shapes: simple map OR advanced operator/conditions."
        ),
        "properties": {
            "visibleWhen": (
                "Simple: { fieldId: [\"value1\", \"value2\"] } — shown when field value is in list. "
                "Advanced: { operator: \"AND\"|\"OR\", conditions: [ { field, operator, value } ] }"
            ),
            "clearOnHide": "boolean — clear the field value when it is hidden",
        },
        "applies_to": "any field and sections",
        "example": {
            "id": "correction_reason",
            "type": "textarea",
            "label": {"en": "Reason for Correction"},
            "visibleWhen": {"correction_needed": ["Yes"]},
            "clearOnHide": True,
        },
        "never": [
            "bare string value: { \"reason\": \"other\" } — values MUST be an array: { \"reason\": [\"other\"] }",
            "mixing simple and advanced shapes in the same visibleWhen",
        ],
    },
    {
        "name": "Auto-fill when (fill a field based on another field's value)",
        "summary": (
            "Fill this field from a static value, another form field, storage, or API "
            "when a condition on another field is met. Also used for form_load defaults."
        ),
        "properties": {
            "autoFillWhen": (
                "{ field: string, value: any, source: string | { type, value?, key? }, lock?: boolean } "
                "— 'field: \"form_load\"' makes it a default value on form load"
            ),
        },
        "applies_to": "most input fields",
        "example": {
            "id": "corrected_district",
            "type": "select",
            "label": {"en": "District (Corrected)"},
            "autoFillWhen": {
                "field": "correction_req",
                "value": "Yes",
                "source": "district_original",
                "lock": True,
            },
        },
        "never": [
            "using autoFillWhen without the 'field' key",
            "omitting 'source' from autoFillWhen",
        ],
    },
    {
        "name": "Conditional auto-fill (fill based on condition with operator)",
        "summary": (
            "Fill a field from static text, another field, storage, an API, or a "
            "calculation WHEN a condition on another field is met. Property is an ARRAY."
        ),
        "properties": {
            "conditionalAutoFillWhen": (
                "array of rules; each rule = "
                "{ condition: { field, operator?, value }, "
                "autoFill: { source, value?, fieldId?, storageKey?, apiSource?, apiPath?, formula? }, "
                "lock?: boolean }"
            ),
        },
        "applies_to": "most input fields",
        "example": {
            "id": "district",
            "type": "select",
            "label": {"en": "District"},
            "conditionalAutoFillWhen": [
                {
                    "condition": {"field": "state", "operator": "equals", "value": "Punjab"},
                    "autoFill": {"source": "static", "value": "Ludhiana"},
                    "lock": False,
                }
            ],
        },
        "never": [
            "making conditionalAutoFillWhen a single object (it MUST be an array)",
        ],
    },
    {
        "name": "Field dependencies (cascading dropdowns)",
        "summary": (
            "Mark that a field should re-fetch/recompute when other fields change. "
            "Use dependsOn (AND) or dependsOnAny (OR)."
        ),
        "properties": {
            "dependsOn": "string[] — updates when ALL listed fields change",
            "dependsOnAny": "string[] — updates when ANY listed field changes",
        },
        "applies_to": "select, multiselect, and fields with datasource",
        "example": {
            "id": "tehsil",
            "type": "select",
            "label": {"en": "Tehsil"},
            "datasource": {"url": "api/um/GeoUnit/GetGeoUnitDList", "method": "POST"},
            "dependsOn": ["district"],
        },
        "never": [
            "dependsOn as a single string (it is an array)",
        ],
    },
    {
        "name": "Calculated field (formula)",
        "summary": "A read-only field computed from a formula over other field ids.",
        "properties": {
            "formula": "string expression referencing other field ids, e.g. \"qty * price\"",
            "readonly": "boolean — typically true",
        },
        "applies_to": "calculated field type",
        "example": {
            "id": "total_amount",
            "type": "calculated",
            "label": {"en": "Total Amount"},
            "formula": "quantity * unit_price",
            "readonly": True,
        },
        "never": [
            "using 'expression' (the key is 'formula')",
        ],
    },
    {
        "name": "Age / date-difference display",
        "summary": "Show a person's age from a DOB field, or the gap between two dates.",
        "properties": {
            "sourceField": "string — for age_display: the DOB field; for date_difference: the start date field",
            "endDateField": "string — date_difference only: the end date field",
        },
        "applies_to": "age_display, date_difference",
        "example": {
            "id": "service_duration",
            "type": "date_difference",
            "label": {"en": "Service Duration"},
            "sourceField": "joining_date",
            "endDateField": "leaving_date",
        },
        "never": [
            "using 'startDateField' (it is 'sourceField')",
        ],
    },
    {
        "name": "Workflow history / activity displays (singletons)",
        "summary": (
            "Special officer-only display fields. Each of these types may appear "
            "AT MOST ONCE per workflow form. They have no editable configuration "
            "beyond label and visibility."
        ),
        "properties": {
            "type": "\"activity_timeline\" | \"previous_history\" | \"role_wise_history\"",
        },
        "applies_to": "activity_timeline, previous_history, role_wise_history",
        "example": {
            "id": "app_timeline",
            "type": "activity_timeline",
            "label": {"en": "Application Timeline", "pa": "ਅਰਜ਼ੀ ਸਮਾਂ-ਰੇਖਾ"},
        },
        "never": [
            "adding more than one field of the same singleton type",
            "adding api_config to activity_timeline/previous_history/role_wise_history",
        ],
    },
]


def render_workflow_feature_contracts() -> str:
    """
    Render WORKFLOW_FEATURE_CONTRACTS as a readable prompt section.
    Mirrors render_feature_contracts() in constants.py.
    """
    import json

    blocks: list[str] = []
    for feat in WORKFLOW_FEATURE_CONTRACTS:
        lines = [f"### {feat['name']}", feat["summary"], "", "Properties:"]
        for prop, meaning in feat["properties"].items():
            lines.append(f'  - "{prop}": {meaning}')
        lines.append("")
        lines.append("Correct example (a single field):")
        lines.append(json.dumps(feat["example"], ensure_ascii=False, indent=2))
        if feat.get("never"):
            lines.append("")
            lines.append("NEVER:")
            for wrong in feat["never"]:
                lines.append(f"  - {wrong}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
