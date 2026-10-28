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

# Validator.type union -- admin configPanel "Add Validator" + TS union.
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
    "condition",
    "minAge",
    "maxAge",
    "futureOnly",
    "pastOnly",
    "todayAndFuture",
    "todayAndPast",
    "dateRangeFromToday",
    "dateRangeAroundToday",
    "dateRangeFromAnchor",
    "specificDays",
    "dateAfterField",
    "dateBeforeField",
    "dateEqualOrAfterField",
    "dateEqualOrBeforeField",
    "workingDays",
    "excludeWeekends",
    "advanceBooking",
    "timeRange",
]

# TableColumn.type union -- a DIFFERENT, smaller set than FieldType.
# Includes admin tableColumnConfig types (button + nested verification).
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
    "button",
    "verification",
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
            "formula": (
                "string expression referencing other field ids, e.g. \"qty * price\". "
                "Prefer Form Builder functions when they fit: calculateAge(dob), "
                "dateDiffYears(start,end), calendarYearDiff(start,end), "
                "dateDiffMonths(start,end), dateDiffDays(start,end), "
                "addDays(date,n), addMonths(date,n), addYears(date,n), today(), "
                "isPastDate(date), isFutureDate(date), add(a,b), subtract(a,b), "
                "multiply(a,b), divide(a,b), percentage(part,whole), round(n,d), "
                "min(a,b), max(a,b), average(a,b), tableRowCount(tableId)"
            ),
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
            "subset, and per-column flags (showAsTag, dynamicId, showTotal, "
            "transliteration) are FLAT properties on the table field / its columns "
            "-- the same keys the admin Form Builder config panel writes. Omit a "
            "key unless the requirement needs it; when creating a typical table, "
            "include minRows/maxRows and rowKey/rowKeyStrategy defaults."
        ),
        "properties": {
            "columns": (
                "REQUIRED TableColumn[]. Each column: id, label, type, plus optional "
                "required, readonly, hidden, placeholder, options, validators, formula, "
                "showAsTag, dynamicId, showTotal, minValue, maxValue, maxLength, "
                "transliteration, transliterationField, transliterationWhen, trim, "
                "capitalizeFirstWord, capitalizeAll, visibleWhen, hiddenWhen, "
                "disabledWhen, conditionalAutoFillWhen, dependsOn"
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
            "showTotal": (
                "boolean ON A NUMBER COLUMN ONLY -- Form Builder 'Show Total Sum' "
                "checkbox. When true, the table footer shows the sum of all values "
                "in that column. Set \"showTotal\": true ONLY when the requirement "
                "explicitly asks for a total / sum / grand total of that named "
                "column (e.g. 'total sum of income', 'tick Show Total Sum on "
                "income', 'sum of the amount column'). NEVER set showTotal on "
                "text/select/date/etc columns. NEVER set showTotal on every "
                "number column by default — omit it unless the user asked for "
                "that column's sum."
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
                {
                    "id": "income",
                    "type": "number",
                    "label": {"en": "Income"},
                    "required": True,
                    "showTotal": True,
                },
            ],
        },
        "never": [
            'using "enableTransliteration" on citizen-form tables (the key is "transliteration")',
            "putting table min/max row limits inside tableValidators (use minRows/maxRows on the field)",
            'disableEditWhen / disableDeleteWhen as objects or visibleWhen-style maps (they are strings)',
            'rowValidators[].errorMessage as a plain string (it is LocalizedText with "en")',
            "showAsTag / dynamicId / showTotal on the TABLE field (they belong on a COLUMN)",
            'showTotal on a non-number column (only type "number" supports Show Total Sum)',
            "setting showTotal: true on number columns when the user did not ask for a sum/total of that column",
            "transliterationField on a column pointing at a form-level field; it must be a sibling column id",
            "listColumns without enableListColumns: true",
            "putting static options on the TABLE field (options belong on each COLUMN)",
            "using datasource/url for a hardcoded list of cities, states, relations, etc. (use options)",
            "one shared options array for every column (each column has its own options)",
        ],
    },
    {
        "name": "Text hygiene (trim / capitalize)",
        "summary": (
            "Normalize typed text the same way the Form Builder Basic Settings "
            "checkboxes do. These are booleans on the field (and on table columns)."
        ),
        "properties": {
            "trim": "boolean -- strip leading/trailing spaces",
            "capitalizeFirstWord": (
                "boolean -- capitalize the first letter of the first word "
                "(default true for text/textarea in the builder)"
            ),
            "capitalizeAll": (
                "boolean -- force uppercase (PAN, vehicle numbers, etc.)"
            ),
        },
        "applies_to": "text, textarea, email, phone, and matching table columns",
        "example": {
            "id": "pan_number",
            "type": "text",
            "label": {"en": "PAN Number"},
            "trim": True,
            "capitalizeAll": True,
        },
        "never": [
            'using a "trim" validator instead of the trim boolean when the builder checkbox is meant',
            "inventing camelCase keys like trimWhitespace",
        ],
    },
    {
        "name": "Required-when expression",
        "summary": (
            "Field is required only when an expression is true. Set required=true "
            "AND requiredExpression. The expression uses other field ids "
            "(e.g. age < 18)."
        ),
        "properties": {
            "required": "boolean -- must be true when using requiredExpression",
            "requiredExpression": "string expression over other field ids",
        },
        "applies_to": "most input fields and table columns",
        "example": {
            "id": "guardian_name",
            "type": "text",
            "label": {"en": "Guardian Name"},
            "required": True,
            "requiredExpression": "age < 18",
        },
        "never": [
            "requiredExpression without required: true",
            "putting the expression in visibleWhen",
        ],
    },
    {
        "name": "Date / datetime / time field settings",
        "summary": (
            "Picker format and bound dates are FLAT field properties, not only "
            "validators. Use validators for extra rules (age, weekday, ranges)."
        ),
        "properties": {
            "format": 'string picker format, e.g. "DD/MM/YYYY", "DD/MM/YYYY HH:mm", "HH:mm"',
            "minDate": "string bound (YYYY-MM-DD or today-relative as the builder stores)",
            "maxDate": "string bound",
        },
        "applies_to": "date, datetime, time fields and date table columns",
        "example": {
            "id": "date_of_birth",
            "type": "date",
            "label": {"en": "Date of Birth"},
            "required": True,
            "format": "DD/MM/YYYY",
            "validators": [
                {"type": "pastOnly", "errorMessage": {"en": "Date of birth must be in the past"}},
                {"type": "minAge", "value": 18, "errorMessage": {"en": "Applicant must be at least 18"}},
            ],
        },
        "never": [
            'using type "text" for a calendar date',
            "putting format inside validators",
        ],
    },
    {
        "name": "Date / time validators",
        "summary": (
            "Use ONLY these Validator.type values for dates and times. "
            "errorMessage should be LocalizedText with en."
        ),
        "properties": {
            "type": (
                "condition | minDate | maxDate | minAge | maxAge | futureOnly | "
                "pastOnly | todayAndFuture | todayAndPast | dateRangeFromToday | "
                "dateRangeAroundToday | dateRangeFromAnchor | specificDays | "
                "dateAfterField | dateBeforeField | dateEqualOrAfterField | "
                "dateEqualOrBeforeField | workingDays | excludeWeekends | "
                "advanceBooking (datetime only) | timeRange (time only)"
            ),
            "value": "number for minAge/maxAge/minDate offsets when used",
            "field": "other field id for dateAfterField / dateBeforeField / equal-or variants",
            "condition": "string expression for type condition",
            "rangeMode": '"enable" | "disable" for dateRangeFromToday / dateRangeAroundToday',
            "rangeUnit": '"days" | "months" | "years"',
            "daysBeforeToday / daysAfterToday / daysAfterStart": "range sizes",
            "monthsBeforeToday / monthsAfterStart / yearsBeforeToday / yearsAfterStart / yearsAfterToday": "range sizes",
            "disableDaysFromToday / disableYearsFromToday": "disable-mode range",
            "anchorMonth / anchorDay / endDate / rangeScope": (
                'dateRangeFromAnchor: month 1-12, day 1-31, endDate "today"|YYYY-MM-DD, '
                'rangeScope "betweenAnchorAndEnd"|"beforeAnchor"'
            ),
            "allowedDays": 'specificDays: ["Monday","Tuesday",...]',
            "hours": "advanceBooking: hours ahead that must remain",
            "minTime / maxTime": "timeRange bounds (HH:mm)",
            "errorMessage": "LocalizedText",
        },
        "applies_to": "date, datetime, time (and date columns)",
        "example": {
            "id": "appointment_date",
            "type": "date",
            "label": {"en": "Appointment Date"},
            "validators": [
                {"type": "todayAndFuture", "errorMessage": {"en": "Choose today or a future date"}},
                {
                    "type": "workingDays",
                    "errorMessage": {"en": "Choose a working day"},
                },
                {
                    "type": "dateEqualOrAfterField",
                    "field": "application_date",
                    "errorMessage": {"en": "Must be on or after the application date"},
                },
            ],
        },
        "never": [
            "inventing validator types like date_only or notWeekend",
            "errorMessage as a nested object without en",
        ],
    },
    {
        "name": "Select options from an API (datasource)",
        "summary": (
            "Dropdown/radio/multiselect/checkbox can load options from an API "
            "instead of a static options array. Use this ONLY for a real API, "
            "never for a hardcoded city/state/relation list."
        ),
        "properties": {
            "datasource": (
                '{ "url": string, "method"?: "GET"|"POST", "payload"?: object, '
                '"trigger_condition"?: string }  -- omit options when using this'
            ),
            "dependsOn / dependsOnAny": "refetch when parent geo/select fields change",
        },
        "applies_to": "select, multiselect, radio, checkbox (fields and table columns)",
        "example": {
            "id": "district",
            "type": "select",
            "label": {"en": "District"},
            "required": True,
            "dependsOnAny": ["state"],
            "datasource": {
                "url": "api/um/GeoUnit/GetGeoUnitDList",
                "method": "POST",
                "payload": {"parentId": "{state}"},
            },
        },
        "never": [
            "datasource for a fixed list of 5 relations / genders (use options)",
            "leaving both options and datasource empty on select/radio/multiselect",
        ],
    },
    {
        "name": "Description / note styling",
        "summary": "type description is a read-only note. Styling is flat booleans + textColor.",
        "properties": {
            "description": "LocalizedText -- the note body (this is the visible text)",
            "textColor": "hex string e.g. #1a1a1a",
            "isBold": "boolean",
            "isItalic": "boolean",
            "isUnderline": "boolean",
            "visibleWhen": "optional visibility map",
            "clearOnHide": "boolean",
        },
        "applies_to": "description",
        "example": {
            "id": "fee_note",
            "type": "description",
            "label": {"en": "Fee note"},
            "description": {"en": "Application fee is non-refundable."},
            "isBold": True,
            "textColor": "#b42318",
        },
        "never": [
            "putting the note in htmlContent (that is htmlDescription)",
            "using type text + readonly for a static instruction block",
        ],
    },
    {
        "name": "HTML description",
        "summary": "Rich HTML help block. Body is htmlContent, not description.",
        "properties": {
            "htmlContent": 'LocalizedText -- HTML string; may include [field_id] tokens',
            "visibleWhen": "optional visibility map",
        },
        "applies_to": "htmlDescription",
        "example": {
            "id": "instructions_html",
            "type": "htmlDescription",
            "label": {"en": "Instructions"},
            "htmlContent": {
                "en": "<p>Upload a clear photo of your <strong>Aadhaar</strong>.</p>"
            },
        },
        "never": [
            'using "description" instead of htmlContent on htmlDescription',
            "type html or richtext (the type is htmlDescription)",
        ],
    },
    {
        "name": "File upload / live capture",
        "summary": (
            "Upload and camera fields use FLAT file properties. OCR is a boolean "
            "plus doc_type -- not a separate field type."
        ),
        "properties": {
            "allowedTypes": 'string[] of extensions, e.g. [".pdf",".jpg",".jpeg",".png"]',
            "maxSizeMB": "number -- prefer this OR maxSizeKB, not both unless needed",
            "maxSizeKB": "number",
            "minSizeKB": "number",
            "maxFiles": "number of files allowed",
            "minResolution": '{ "width": number, "height": number }',
            "DocumentType": 'LocalizedText plus optional numeric id -- UI document label',
            "OCR": "boolean -- run document OCR after upload (file only, not live_capture)",
            "doc_type": (
                'required when OCR is true: "passport"|"aadhaar"|"pan"|'
                '"driving_license"|"voter_id"'
            ),
            "enableBlurDetection": "boolean (images)",
            "blurThreshold": "number 5-30 (default 10)",
            "disableView": "boolean -- hide View after upload",
            "disableDelete": "boolean -- hide Delete after upload",
            "disableViewWhen": "string expression",
            "disableDeleteWhen": "string expression",
        },
        "applies_to": "file, live_capture, live_capture_general (and matching table columns)",
        "example": {
            "id": "aadhaar_scan",
            "type": "file",
            "label": {"en": "Aadhaar Card"},
            "required": True,
            "allowedTypes": [".jpg", ".jpeg", ".png", ".pdf"],
            "maxSizeMB": 5,
            "maxFiles": 1,
            "DocumentType": {"en": "Aadhaar", "pa": "ਆਧਾਰ"},
            "OCR": True,
            "doc_type": "aadhaar",
            "enableBlurDetection": True,
            "blurThreshold": 18,
        },
        "never": [
            "OCR: true without doc_type",
            "using validators fileType/maxSize instead of allowedTypes/maxSizeMB "
            "(the builder writes the flat keys)",
            "setting OCR on live_capture (file only in the builder)",
        ],
    },
    {
        "name": "Autofill from citizen profile",
        "summary": "Pre-fill from the logged-in profile and optionally lock after fill.",
        "properties": {
            "autofillFromProfile": "string profile attribute key the runtime maps",
            "lockAfterProfileAutofill": "boolean -- make readonly after profile fill",
        },
        "applies_to": "most input fields (not file / table / live_capture / widgets)",
        "example": {
            "id": "applicant_email",
            "type": "email",
            "label": {"en": "Email"},
            "autofillFromProfile": "email",
            "lockAfterProfileAutofill": True,
        },
        "never": [
            'using autoFillWhen form_load for profile data (that is a static/storage default)',
            "autofillFromProfile as a boolean",
        ],
    },
    {
        "name": "Autofill from Aadhaar eKYC",
        "summary": (
            "A normal input is filled from an eKYC widget on the same form. "
            "source is the eKYC FIELD TYPE (not a field id)."
        ),
        "properties": {
            "source": (
                '"applicant_aadhaar_ekyc"|"beneficiary_aadhaar_ekyc"|'
                '"guardian_aadhaar_ekyc"|"other_aadhaar_ekyc"'
            ),
            "autoFillFormAadharEkyc": (
                "string or string[] of eKYC keys: Name, DOB, Gender, "
                "CareOfOrFatherName, Country, State, District, SubDistrict, "
                "PostOffice, Pincode, Vtc, Street, Landmark, Location, House"
            ),
            "lockAfterAutoFill": "boolean",
        },
        "applies_to": "text/date/select inputs that should receive eKYC values",
        "example": {
            "id": "applicant_name",
            "type": "text",
            "label": {"en": "Applicant Name"},
            "source": "applicant_aadhaar_ekyc",
            "autoFillFormAadharEkyc": "Name",
            "lockAfterAutoFill": True,
        },
        "never": [
            "putting autoFillFormAadharEkyc on the eKYC widget itself",
            'source as a field id (it is the eKYC type string)',
            "inventing type aadhaar_ekyc",
        ],
    },
    {
        "name": "Autofill from PAN verification",
        "summary": "Same pattern as eKYC, sourced from a pan_verification widget.",
        "properties": {
            "source": '"pan_verification"',
            "autoFillFormPan": (
                "string or string[]: Name, DOB, PanNumber, FatherName"
            ),
            "lockAfterAutoFill": "boolean",
        },
        "applies_to": "text/date inputs filled from PAN",
        "example": {
            "id": "pan_name",
            "type": "text",
            "label": {"en": "Name as on PAN"},
            "source": "pan_verification",
            "autoFillFormPan": "Name",
            "lockAfterAutoFill": True,
        },
        "never": [
            "autoFillFormPan on the pan_verification widget itself",
            "mixing autoFillFormAadharEkyc keys into PAN",
        ],
    },
    {
        "name": "Autofill from OCR",
        "summary": "Fill this field from OCR output of a file field that has OCR: true.",
        "properties": {
            "autofillFromOCR": "string -- the FILE field id (or document name) that runs OCR",
        },
        "applies_to": "text/date inputs that should receive OCR values",
        "example": {
            "id": "ocr_name",
            "type": "text",
            "label": {"en": "Name (from document)"},
            "autofillFromOCR": "aadhaar_scan",
        },
        "never": [
            "autofillFromOCR as a boolean",
            "OCR autofill without a file field that has OCR true",
        ],
    },
    {
        "name": "Highlight rules",
        "summary": "Conditionally color a field and show hover text. Property is an ARRAY.",
        "properties": {
            "highlightRules": (
                "array of { id?: string, "
                'condition: { compareWith: fieldId, operator: '
                '"equals"|"not_equals"|"greater_than"|"less_than"|'
                '"greater_than_or_equal"|"less_than_or_equal"|"contains"|'
                '"not_contains"|"is_empty"|"is_not_empty", value?: any, '
                "compareField?: fieldId }, "
                "highlight: { color: hex, hoverText?: LocalizedText } }"
            ),
        },
        "applies_to": "most input fields (not table/file/description/api_trigger)",
        "example": {
            "id": "declared_income",
            "type": "number",
            "label": {"en": "Declared Income"},
            "highlightRules": [
                {
                    "id": "income_mismatch",
                    "condition": {
                        "compareWith": "computed_income",
                        "operator": "not_equals",
                    },
                    "highlight": {
                        "color": "#ff5722",
                        "hoverText": {"en": "Does not match computed income"},
                    },
                }
            ],
        },
        "never": [
            "highlightRules as a single object (it is an array)",
            "using CSS class names instead of highlight.color hex",
        ],
    },
    {
        "name": "API trigger (button / auto / form_load)",
        "summary": (
            "Calls an API and maps the response into fields. type api_trigger. "
            "Same keys on a table column of type _api_trigger or button."
        ),
        "properties": {
            "trigger_mode": '"manual"|"auto"|"form_load" (default manual)',
            "trigger_on_change": "string[] of field ids -- auto mode",
            "debounce": "number ms",
            "load_delay": "number ms for form_load",
            "execute_once": "boolean for form_load",
            "api_config": (
                "{ endpoint: string (REQUIRED), method?: GET|POST|PUT|DELETE, "
                "base_url?: string, headers?: object, payload_mapping?: object, "
                "auto_fill_mapping?: object, response_storage_key?: string, "
                "execute_when?: { operator?: AND|OR, conditions: "
                "[{ field, operator, value }] }, "
                "response_validation?: { required_fields?, success_condition?, error_message? }, "
                "loading_config?: { show_spinner?, disable_form?, disable_fields?, message? }, "
                "lock_filled_fields?: boolean, lock_filled_fields_conditional?: string, "
                "on_success?: { show_message?, trigger_next? }, "
                "on_error?: { show_message?, clear_fields? } }"
            ),
            "button_props": (
                '{ type?: "default"|"primary"|"dashed"|"link"|"text", '
                'size?: "small"|"middle"|"large", label?: LocalizedText }'
            ),
        },
        "applies_to": "api_trigger fields; table columns type _api_trigger or button",
        "example": {
            "id": "fetch_ifms",
            "type": "api_trigger",
            "label": {"en": "Fetch IFMS details"},
            "trigger_mode": "manual",
            "button_props": {"type": "primary", "label": {"en": "Fetch"}},
            "api_config": {
                "endpoint": "api/ifms/lookup",
                "method": "POST",
                "payload_mapping": {"accountNo": "{account_number}"},
                "auto_fill_mapping": {"ifms_name": "name"},
                "lock_filled_fields": True,
            },
        },
        "never": [
            "api_trigger without api_config.endpoint",
            "inventing type button at FIELD level (field type is api_trigger; "
            "button is a TABLE COLUMN type only)",
        ],
    },
    {
        "name": "Computed table",
        "summary": (
            "A derived table from other tables. Uses computation, NOT formula. "
            "Still requires columns[]."
        ),
        "properties": {
            "columns": "REQUIRED TableColumn[] describing output columns",
            "computation": (
                "{ operation: difference|intersection|union|filter|copy, "
                "source_table: field id, subtract_table?: field id "
                "(required for difference/intersection), match_key: column id, "
                "include_columns?: column id[], "
                "filter_condition?: { field, operator: equals|not_equals|gt|lt|"
                "gte|lte|contains|not_contains, value } }"
            ),
            "include_in_submission": "boolean, default true",
            "submission_key": "string override for payload key",
            "hidden": "boolean",
        },
        "applies_to": "computed_table",
        "example": {
            "id": "remaining_plots",
            "type": "computed_table",
            "label": {"en": "Remaining plots"},
            "include_in_submission": True,
            "computation": {
                "operation": "difference",
                "source_table": "all_plots",
                "subtract_table": "allotted_plots",
                "match_key": "plot_id",
            },
            "columns": [
                {"id": "plot_id", "type": "text", "label": {"en": "Plot ID"}},
            ],
        },
        "never": [
            'using "formula" on computed_table',
            "computed_table without computation.source_table and match_key",
            "difference/intersection without subtract_table",
        ],
    },
    {
        "name": "Select options from another table (form_field datasource)",
        "summary": (
            "A select COLUMN (or field) whose options are rows of another table "
            "on the same form. datasource.type is exactly form_field."
        ),
        "properties": {
            "datasource": (
                '{ "type": "form_field", "sourceField": table field id, '
                '"labelFormat"?: "{colA} - {colB}", "valueKey": column id, '
                '"autoFillColumns"?: [{ sourceColumn, targetColumn }], '
                '"uniqueOptions"?: boolean, "optionFilterExpression"?: string }'
            ),
        },
        "applies_to": "select/multiselect/radio table columns (sometimes fields)",
        "example": {
            "id": "member_ref",
            "type": "select",
            "label": {"en": "Family member"},
            "datasource": {
                "type": "form_field",
                "sourceField": "family_members",
                "valueKey": "member_name",
                "labelFormat": "{member_name} - {relation}",
                "uniqueOptions": True,
                "autoFillColumns": [
                    {"sourceColumn": "relation", "targetColumn": "picked_relation"}
                ],
            },
        },
        "never": [
            'type "formField" or "table" (it is "form_field")',
            "using url datasource for options that come from another table on this form",
        ],
    },
    {
        "name": "Configuration column",
        "summary": "Special table column type configuration with preset row values.",
        "properties": {
            "type": '"configuration"',
            "configOptions": '[{ "id": string, "label": LocalizedText }]',
            "defaultNewRowValue": "option id used when adding a row",
            "defaultAutoFillValue": "option id used when auto-filling",
            "defaultEditValue": "option id used when editing",
        },
        "applies_to": "table columns only",
        "example": {
            "id": "row_mode",
            "type": "configuration",
            "label": {"en": "Row mode"},
            "configOptions": [
                {"id": "new", "label": {"en": "New"}},
                {"id": "edit", "label": {"en": "Edit"}},
            ],
            "defaultNewRowValue": "new",
            "defaultEditValue": "edit",
        },
        "never": [
            "type configuration as a top-level field (column only)",
            "configOptions with value/label like select options (id + label)",
        ],
    },
    {
        "name": "OTP field",
        "summary": (
            "Standalone otp type (rare; mobile_verification already nests OTP). "
            "Links to the phone/email field that receives the OTP."
        ),
        "properties": {
            "linkedField": "id of the phone or email field",
            "otpSource": '"sms"|"email"',
        },
        "applies_to": "otp",
        "example": {
            "id": "email_otp",
            "type": "otp",
            "label": {"en": "Email OTP"},
            "linkedField": "applicant_email",
            "otpSource": "email",
        },
        "never": [
            "using type otp inside mobile_verification (that uses nested otpField)",
        ],
    },
    {
        "name": "Form-level extras (constants, declaration, cross-field table target)",
        "summary": (
            "Optional top-level keys besides sections. Generate flow may strip "
            "serviceCode/title/category/version; these extras are kept when present."
        ),
        "properties": {
            "constants": "object of named constants referenced by visibility builders",
            "verify_declaration": '{ "aadhaar_ekyc"?: boolean, "declaration"?: boolean }',
            "crossFieldValidators": (
                "array of { id, type: \"custom\", expression, errorMessage, "
                "validatesTableColumn?: boolean, tableField?: table id, "
                "columnToValidate?: column id }"
            ),
            "jurisdiction": '{ "level": string, "field": field id }',
        },
        "applies_to": "the form root object",
        "example": {
            "id": "root_example_not_a_field",
            "type": "text",
            "label": {"en": "n/a"},
            "description": {
                "en": "Put verify_declaration / constants / crossFieldValidators on the FORM root, not on a field."
            },
        },
        "never": [
            "putting verify_declaration or constants on a field",
            "crossFieldValidators[].type other than custom",
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
