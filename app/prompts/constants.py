"""
Shared Form Builder schema constants -- the SINGLE SOURCE OF TRUTH for the
allowed lists used by BOTH the system prompt and the validation layer.

Transcribed directly from the frontend. Keep in sync if these files change:
  - pcp-admin-portal/src/types/formBuilder.types.ts  (FieldType, Validator.type,
    TableColumn.type, Section, SubSection, LocalizedText)
  - pcp-admin-portal/src/helper/fieldDefaults.ts      (canonical per-type shapes)
"""

# FieldType union -- 39 values (verified against formBuilder.types.ts).
# NOTE: there is no generic "aadhaar_ekyc" or "pan" type; the Aadhaar eKYC
# variants are explicit per-role types.
ALLOWED_FIELD_TYPES: list[str] = [
    "text",
    "email",
    "phone",
    "number",
    "textarea",
    "select",
    "multiselect",
    "radio",
    "applying_for",
    "checkbox",
    "toggle",
    "date",
    "datetime",
    "time",
    "file",
    "live_capture",
    "live_capture_general",
    "calculated",
    "age_display",
    "date_difference",
    "applicant_aadhaar_ekyc",
    "beneficiary_aadhaar_ekyc",
    "guardian_aadhaar_ekyc",
    "other_aadhaar_ekyc",
    "pan_verification",
    "declaration",
    "mobile_verification",
    "in_form_login",
    "pincode",
    "aadhaar",
    "otp",
    "table",
    "computed_table",
    "api_trigger",
    "concatenate",
    "line_break",
    "description",
    "htmlDescription",
    "verification",
    "translate_textarea",
]

# Validator.type union.
ALLOWED_VALIDATOR_TYPES: list[str] = [
    "regex",
    "minLength",
    "maxLength",
    "minValue",
    "maxValue",
    "minDate",
    "maxDate",
    "trim",
    "noEmoji",
    "minResolution",
    "email",
    "phone",
    "aadhaar",
    "min",
    "max",
    "dateEqualOrAfterField",
    "dateEqualOrBeforeField",
    "todayAndFuture",
    "todayAndPast",
    "dateRangeFromToday",
    "specificDays",
]

# TableColumn.type union -- a DIFFERENT, smaller set than FieldType.
ALLOWED_TABLE_COLUMN_TYPES: list[str] = [
    "text",
    "number",
    "calculated",
    "select",
    "multiselect",
    "radio",
    "date",
    "checkbox",
    "file",
    "live_capture",
    "live_capture_general",
    "configuration",
    "_api_trigger",
]

# Field types that MUST carry `options`.
TYPES_REQUIRING_OPTIONS: list[str] = ["select", "multiselect", "radio", "applying_for"]

# Field types that MUST carry `columns`.
TYPES_REQUIRING_COLUMNS: list[str] = ["table", "computed_table", "verification"]

# Field types that MUST carry nested mobileNumberField + otpField.
TYPES_REQUIRING_MOBILE_OTP: list[str] = ["mobile_verification", "in_form_login"]

# Field types that MUST carry api_config.
TYPES_REQUIRING_API_CONFIG: list[str] = ["api_trigger"]

# The applying_for singleton rule (mirrors handleDragEnd in formBuilder.tsx).
APPLYING_FOR_TYPE: str = "applying_for"
APPLYING_FOR_FIXED_ID: str = "applying_for_yourself"

# Canonical nested ids for mobile_verification / in_form_login
# (from fieldDefaults.ts). When a form has more than one such field, the nested
# ids must be made distinct to preserve global id uniqueness.
DEFAULT_MOBILE_FIELD_ID: str = "mobile_number"
DEFAULT_OTP_FIELD_ID: str = "otp"

# Known-good validator patterns reused by the prompt guidance.
PHONE_REGEX: str = r"^[6-9][0-9]{9}$"
PINCODE_REGEX: str = r"^[1-9][0-9]{5}$"


# ---------------------------------------------------------------------------
# FEATURE CONTRACTS -- the SINGLE SOURCE OF TRUTH for advanced FIELD FEATURES.
#
# The type lists above tell the model WHICH field types exist. This catalog
# tells it, for each advanced FEATURE (transliteration, auto-fill, visibility,
# etc.), the EXACT property names + shapes the frontend actually reads, plus a
# correct example and the common WRONG shape to avoid.
#
# WHY THIS EXISTS: a feature is expressed as flat properties on an ORDINARY
# field, NOT as a special field `type`. Without this catalog the model guesses
# plausible-but-ignored shapes (e.g. inventing a "translate_textarea" type with
# "sourceField"/"dependsOn" instead of "transliteration": true +
# "transliterationField"). Both the system prompt and the validator consume this
# so they never drift.
#
# Contracts transcribed from the frontend contract:
#   - pcp-admin-portal/src/types/formBuilder.types.ts   (Field/TableColumn/... interfaces)
#   - pcp-admin-portal/src/helper/jsonGenerator.ts       (authoritative EMITTED shape)
#   - pcp-admin-portal/src/components/formBuilder/configPanel.tsx (what each UI reads)
# Keep in sync if those change.
#
# Each entry:
#   name        : human-readable feature name
#   summary     : one line on what it does
#   properties  : "prop": "type / meaning"
#   applies_to  : which field types it is valid on ("most input fields" = any
#                 non-container input type)
#   example     : a minimal CORRECT field (as a dict) using the feature
#   never       : list of common WRONG shapes to avoid
# ---------------------------------------------------------------------------

FEATURE_CONTRACTS: list[dict] = [
    {
        "name": "Transliteration (script conversion, e.g. English -> Punjabi)",
        "summary": (
            "Auto-convert one field's text into another script as the user types. "
            "Set on an ORDINARY text/textarea field -- it is NOT a special field type."
        ),
        "properties": {
            "transliteration": "boolean -- true to enable",
            "transliterationField": (
                "string -- the id of the OTHER field in the pair (the field whose "
                "text drives / receives the conversion). Must be an existing field "
                "id, or for table columns a sibling column id."
            ),
            "transliterationWhen": (
                'optional { "targetEmpty"?: boolean, "on"?: string[] } '
                '(e.g. "on": ["blur"])'
            ),
        },
        "applies_to": (
            "most input fields (text, textarea, number, select, ...) AND table "
            "columns (transliterationField then names a sibling COLUMN id)"
        ),
        "example": {
            "id": "applicant_name_punjabi",
            "type": "textarea",
            "label": {"en": "Applicant Name (Punjabi)", "pa": "ਬਿਨੈਕਾਰ ਦਾ ਨਾਮ"},
            "required": True,
            "transliteration": True,
            "transliterationField": "applicant_name_english",
        },
        "never": [
            'inventing a "translate_textarea" field type for this',
            'using "sourceField" or "dependsOn" to express transliteration',
            'putting a field id in "transliteration" (it is a boolean; the id goes in "transliterationField")',
            '"transliterationWhen" as a boolean/string (it is an object)',
        ],
    },
    {
        "name": "Translation (Bhashini NMT -- meaning-based, DIFFERENT from transliteration)",
        "summary": (
            "Machine-translate a field's value into another language field. "
            "Separate from transliteration (which is script conversion)."
        ),
        "properties": {
            "translation": "boolean -- true to enable",
            "translationField": "string -- target field id that receives the translation",
            "translationSource": 'string language code, default "en"',
            "translationTarget": 'string language code, default "pa"',
        },
        "applies_to": "most input fields (typically text/textarea)",
        "example": {
            "id": "address_english",
            "type": "textarea",
            "label": {"en": "Address (English)"},
            "translation": True,
            "translationField": "address_punjabi",
            "translationSource": "en",
            "translationTarget": "pa",
        },
        "never": [
            'using "enableTranslation" (the key is "translation")',
            "reusing transliterationField for translation (they are separate features)",
            "language codes as objects (they are plain strings)",
        ],
    },
    {
        "name": "Default value / pre-fill on form load",
        "summary": (
            'Give a field a starting value when the form loads. Implemented by '
            'OVERLOADING "autoFillWhen" with the sentinel field "form_load". '
            'There is NO "defaultValue" property.'
        ),
        "properties": {
            "autoFillWhen": (
                '{ "field": "form_load", "value": "true", '
                '"source": { "type": "static", "value": <the default> } }. '
                '"source" may instead be '
                '{ "type": "localStorage"|"sessionStorage"|"formData", "key": string }.'
            ),
        },
        "applies_to": "most input fields",
        "example": {
            "id": "state",
            "type": "text",
            "label": {"en": "State"},
            "autoFillWhen": {
                "field": "form_load",
                "value": "true",
                "source": {"type": "static", "value": "Punjab"},
            },
        },
        "never": [
            'a top-level "defaultValue" key (nothing reads it)',
            'omitting "field": "form_load" (then it becomes a conditional watch, not a default)',
        ],
    },
    {
        "name": "Conditional auto-fill (fill a field based on another field's value)",
        "summary": (
            'Fill a field from static text, another field, storage, an API, or a '
            'calculation WHEN a condition on another field is met. Property is an '
            'ARRAY of rules.'
        ),
        "properties": {
            "conditionalAutoFillWhen": (
                "array of rules; each rule = "
                '{ "condition": { "field": string, '
                '"operator"?: "equals"|"not_equals"|"in"|"not_in"|"greater_than"|"less_than"|"contains", '
                '"value": any }, '
                '"autoFill": { "source": "static"|"formData"|"localStorage"|"sessionStorage"|"apiData"|"calculation", '
                '"value"?: any, "fieldId"?: string, "storageKey"?: string, '
                '"apiSource"?: string, "apiPath"?: string, "formula"?: string }, '
                '"lock"?: boolean, "clearOnConditionChange"?: boolean }'
            ),
        },
        "applies_to": "most input fields and table columns",
        "example": {
            "id": "district",
            "type": "text",
            "label": {"en": "District"},
            "conditionalAutoFillWhen": [
                {
                    "condition": {"field": "pincode", "operator": "not_equals", "value": ""},
                    "autoFill": {"source": "apiData", "apiSource": "pincode_lookup", "apiPath": "district"},
                    "lock": True,
                }
            ],
        },
        "never": [
            "making conditionalAutoFillWhen a single object (it MUST be an array)",
            "flattening condition/autoFill (they are nested objects with those exact keys)",
            'using "fieldId" for a static value (use "value")',
        ],
    },
    {
        "name": "Conditional visibility / enable-disable",
        "summary": (
            "Show, hide, or disable a field (or section) based on other fields' values. "
            "Shape is a MAP of watched field id -> array of matching values."
        ),
        "properties": {
            "visibleWhen": 'Record<fieldId, any[]> -- field is shown when the watched field\'s value is IN the array',
            "hiddenWhen": "Record<fieldId, any[]> -- inverse of visibleWhen",
            "disabledWhen": "Record<fieldId, any[]> -- disable when matched",
            "clearOnHide": "boolean -- clear the field's value when it becomes hidden",
        },
        "applies_to": "any field, and sections",
        "example": {
            "id": "other_reason",
            "type": "text",
            "label": {"en": "Please specify"},
            "visibleWhen": {"reason": ["other"]},
            "clearOnHide": True,
        },
        "never": [
            'the object shape { "field": "reason", "operator": "equals", "value": "other" } (NOT supported)',
            'a bare string value: { "reason": "other" } -- values MUST be an array: { "reason": ["other"] }',
        ],
    },
    {
        "name": "Field dependencies",
        "summary": "Mark that a field should recompute/refetch when other fields change.",
        "properties": {
            "dependsOn": "string[] of field ids -- updates when ALL listed deps change (AND)",
            "dependsOnAny": "string[] of field ids -- updates when ANY listed dep changes (OR)",
        },
        "applies_to": "most input fields and table columns",
        "example": {
            "id": "tehsil",
            "type": "select",
            "label": {"en": "Tehsil"},
            "dependsOnAny": ["district"],
            "options": [{"value": "t1", "label": {"en": "Tehsil 1"}}],
        },
        "never": [
            "dependsOn as a single string (it is an array)",
            "using dependsOn when the semantics need OR (use dependsOnAny)",
        ],
    },
    {
        "name": "Concatenate fields",
        "summary": "Build a read-only field by joining other fields' values.",
        "properties": {
            "concatenateFields": "string[] of field ids to join, in order",
            "concatenateSeparator": 'string joiner, default " " (single space); use "" for none',
        },
        "applies_to": "concatenate field type",
        "example": {
            "id": "full_name",
            "type": "concatenate",
            "label": {"en": "Full Name"},
            "concatenateFields": ["first_name", "last_name"],
            "concatenateSeparator": " ",
            "readonly": True,
        },
        "never": [
            "concatenateFields as a comma-separated string (it is an array of ids)",
        ],
    },
    {
        "name": "Calculated field (formula)",
        "summary": "A read-only field computed from a formula expression over other field ids.",
        "properties": {
            "formula": "string expression referencing other field ids, e.g. \"qty * price\"",
            "readonly": "boolean -- typically true",
        },
        "applies_to": "calculated field type (and calculated table columns)",
        "example": {
            "id": "total",
            "type": "calculated",
            "label": {"en": "Total"},
            "formula": "qty * price",
            "readonly": True,
        },
        "never": [
            'using "expression" for a calculated field (the key is "formula")',
            'using "formula" on a computed_table (that uses "computation" -- a different shape)',
        ],
    },
    {
        "name": "Age / date-difference display",
        "summary": "Show a person's age from a date field, or the gap between two dates.",
        "properties": {
            "sourceField": (
                "string field id -- for age_display: the date-of-birth field; "
                "for date_difference: the START date field"
            ),
            "endDateField": "string field id -- date_difference only: the END date field",
        },
        "applies_to": "age_display, date_difference",
        "example": {
            "id": "duration",
            "type": "date_difference",
            "label": {"en": "Duration"},
            "sourceField": "start_date",
            "endDateField": "end_date",
        },
        "never": [
            'using "startDateField" (it is "sourceField")',
            'using sourceField/endDateField for transliteration or auto-fill (unrelated features)',
        ],
    },
    {
        "name": "Table field configuration (row actions, keys, validators, columns)",
        "summary": (
            "A type 'table' field is an editable repeating row of columns. "
            "Row-action flags, row-key strategy, row/table validators, list-column "
            "subset, and per-column flags (showAsTag, dynamicId, transliteration) "
            "are FLAT properties on the table field / its columns -- the same keys "
            "the admin Form Builder config panel writes. Omit a key unless the "
            "requirement needs it; when creating a typical table, include "
            "minRows/maxRows and rowKey/rowKeyStrategy defaults."
        ),
        "properties": {
            "columns": (
                "REQUIRED TableColumn[]. Each column: id, label, type, plus optional "
                "required, readonly, hidden, placeholder, options, validators, formula, "
                "showAsTag, dynamicId, transliteration, transliterationField, "
                "transliterationWhen, trim, capitalizeFirstWord, capitalizeAll, "
                "visibleWhen, hiddenWhen, disabledWhen, conditionalAutoFillWhen, dependsOn"
            ),
            "minRows": "number -- default minimum rows (typically 1)",
            "maxRows": "number -- default maximum rows (typically 10)",
            "disableMinMaxRows": "boolean -- skip min/max row validation and hide the UI counters",
            "conditionalRows": (
                "optional array of { field, operator?, value, minRows?, maxRows? } "
                "-- different row limits based on another field's value"
            ),
            "addRowLabel": "LocalizedText -- Add Row button text",
            "removeRowLabel": "LocalizedText -- Remove/delete row button text",
            "disableAddRow": "boolean -- hide the Add Row button for the whole table",
            "disableEdit": "boolean -- hide Edit on every row",
            "disableDelete": "boolean -- hide Delete on every row",
            "disableEditWhen": (
                "string expression over column ids; when true, hide Edit for THAT row "
                '(e.g. is_deleted == 1). Ignored if disableEdit is true. NOT a visibleWhen map.'
            ),
            "disableDeleteWhen": (
                "string expression over column ids; when true, hide Delete for THAT row. "
                "Ignored if disableDelete is true. NOT a visibleWhen map."
            ),
            "rowKey": 'string -- column/field name used as the unique row id (default "row_id")',
            "rowKeyStrategy": '"uuid" | "auto" | "manual" -- how row keys are generated (default "uuid")',
            "rowValidators": (
                "array of { type: \"custom\", expression: string, errorMessage: LocalizedText } "
                "-- cross-column checks within a single row"
            ),
            "tableValidators": (
                "array of { type: \"uniqueCombo\", fields: string[] (column ids), "
                "errorMessage: LocalizedText } -- uniqueness across rows. Do NOT use "
                "type minRows/maxRows here; those are the field properties minRows/maxRows."
            ),
            "enableListColumns": "boolean -- table list shows only listColumns; the add/edit drawer still has every column",
            "listColumns": "string[] of column ids visible in the table list (requires enableListColumns)",
            "showAsTag": "boolean ON A COLUMN -- render that cell as a tag in the table list",
            "dynamicId": (
                "boolean ON A COLUMN -- append the row number to the column id "
                '(activity_name -> activity_name1). Do not set on verification-field columns.'
            ),
            "options": (
                "STATIC dropdown data on a select|multiselect|radio COLUMN: "
                "[{ \"value\": string, \"label\": LocalizedText }, ...]. "
                "This is the Form Builder 'Static Options' source (no datasource). "
                "When the user lists values for a column, or says static data on "
                "ALL columns, put an options array on EACH such column. Convert a "
                "text column to type select if the user gave it a fixed list. "
                "Each column has its OWN options — never one shared list on the table field."
            ),
            "conditionalAutoFillWhen": (
                "optional STATIC DEFAULT cell value (not the dropdown list): "
                "[{ \"condition\": { \"field\": string, \"operator\"?: string, \"value\": any }, "
                "\"autoFill\": { \"source\": \"static\", \"value\": any }, \"lock\"?: boolean }]. "
                "Use when the user wants a pre-filled value (e.g. country always India)."
            ),
        },
        "applies_to": "table field type (column flags apply to items in columns[])",
        "example": {
            "id": "family_members",
            "type": "table",
            "label": {"en": "Family Members"},
            "minRows": 1,
            "maxRows": 10,
            "rowKey": "row_id",
            "rowKeyStrategy": "uuid",
            "addRowLabel": {"en": "Add Member"},
            "disableAddRow": True,
            "disableEditWhen": "is_deleted == 1",
            "disableDeleteWhen": 'status == "submitted"',
            "enableListColumns": True,
            "listColumns": ["member_name", "relation", "status"],
            "rowValidators": [
                {
                    "type": "custom",
                    "expression": "!(dob && date_of_death) || (dob <= date_of_death)",
                    "errorMessage": {
                        "en": "Date of death must be on or after date of birth"
                    },
                }
            ],
            "tableValidators": [
                {
                    "type": "uniqueCombo",
                    "fields": ["member_name", "relation"],
                    "errorMessage": {
                        "en": "This member and relation combination already exists"
                    },
                }
            ],
            "columns": [
                {
                    "id": "member_name",
                    "type": "text",
                    "label": {"en": "Name (English)"},
                    "required": True,
                    "dynamicId": True,
                    "transliteration": True,
                    "transliterationField": "member_name_pa",
                },
                {
                    "id": "member_name_pa",
                    "type": "text",
                    "label": {"en": "Name (Punjabi)"},
                    "required": True,
                },
                {
                    "id": "relation",
                    "type": "select",
                    "label": {"en": "Relation"},
                    "required": True,
                    "options": [
                        {"value": "spouse", "label": {"en": "Spouse"}},
                        {"value": "child", "label": {"en": "Child"}},
                    ],
                },
                {
                    "id": "status",
                    "type": "select",
                    "label": {"en": "Status"},
                    "showAsTag": True,
                    "options": [
                        {"value": "active", "label": {"en": "Active"}},
                        {"value": "submitted", "label": {"en": "Submitted"}},
                    ],
                },
            ],
        },
        "never": [
            'using "enableTransliteration" on citizen-form tables (the key is "transliteration")',
            "putting table min/max row limits inside tableValidators (use minRows/maxRows on the field)",
            'disableEditWhen / disableDeleteWhen as objects or visibleWhen-style maps (they are strings)',
            'rowValidators[].errorMessage as a plain string (it is LocalizedText with "en")',
            "showAsTag / dynamicId on the TABLE field (they belong on a COLUMN)",
            "transliterationField on a column pointing at a form-level field; it must be a sibling column id",
            "listColumns without enableListColumns: true",
            "putting static options on the TABLE field (options belong on each COLUMN)",
            "using datasource/url for a hardcoded list of cities, states, relations, etc. (use options)",
            "one shared options array for every column (each column has its own options)",
        ],
    },
]


# Canonical table row-key strategies and table-validator types (formBuilder.types.ts).
ROW_KEY_STRATEGIES: list[str] = ["uuid", "auto", "manual"]
TABLE_VALIDATOR_TYPES: list[str] = ["uniqueCombo", "minRows", "maxRows", "custom"]
ROW_VALIDATOR_TYPE: str = "custom"


def _field_types_needing_feature_note() -> None:
    """No-op anchor for docs; feature<->type mapping lives in FEATURE_CONTRACTS."""


def render_feature_contracts() -> str:
    """
    Render FEATURE_CONTRACTS as a readable prompt section: for each feature, its
    summary, exact properties, a CORRECT JSON example, and the WRONG shapes to
    avoid. Kept here (next to the data) so the prompt and validator share one
    source of truth.
    """
    import json

    blocks: list[str] = []
    for feat in FEATURE_CONTRACTS:
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
