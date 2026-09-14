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
