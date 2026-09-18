"""
Level 1 — Structural validation (schema shape).

Enforces the shape rules that come straight from `formBuilder.types.ts`. These
catch the class of errors an LLM most easily emits: an invalid `field.type`,
a `select` with no `options`, a `table` with no `columns`, a `mobile_verification`
missing its nested id fields, a `LocalizedText` with no `en`, etc. (plan v3 §2.1).

The allowed sets below are transcribed from the TypeScript union types. If the
Form Builder adds a new field/validator/column type, update these sets so they
never drift from the source of truth (plan §5 recommendation).

Pure functions over dicts; traverses via form_index so section fields,
subSection fields, and table columns are all covered.
"""

from __future__ import annotations

from typing import Any

from app.core.form_index import COLUMN_BEARING_TYPES, FormIndex, NodeKind
from app.validation.result import Level, ValidationError

# --- Allowed value sets (from formBuilder.types.ts) -------------------------

FIELD_TYPES: frozenset[str] = frozenset({
    "text", "email", "phone", "number", "textarea", "select", "multiselect",
    "radio", "applying_for", "checkbox", "toggle", "date", "datetime", "time",
    "file", "live_capture", "live_capture_general", "calculated", "age_display",
    "date_difference", "applicant_aadhaar_ekyc", "beneficiary_aadhaar_ekyc",
    "guardian_aadhaar_ekyc", "other_aadhaar_ekyc", "pan_verification",
    "declaration", "mobile_verification", "in_form_login", "pincode", "aadhaar",
    "otp", "table", "computed_table", "api_trigger", "concatenate", "line_break",
    "description", "htmlDescription", "verification", "translate_textarea",
})

# Base set from the TS `Validator.type` union.
_VALIDATOR_TYPES_TS: frozenset[str] = frozenset({
    "regex", "minLength", "maxLength", "minValue", "maxValue", "minDate",
    "maxDate", "trim", "noEmoji", "minResolution", "email", "phone", "aadhaar",
    "min", "max", "dateEqualOrAfterField", "dateEqualOrBeforeField",
    "todayAndFuture", "todayAndPast", "dateRangeFromToday", "specificDays",
})

# Additional validator types observed in the production corpus but not (yet) in
# the TS union. The running app tolerates these, so we accept them rather than
# flag valid forms. Kept as a separate set so the drift is visible/auditable.
_VALIDATOR_TYPES_OBSERVED: frozenset[str] = frozenset({
    "required", "fileType", "maxSize", "concatenate", "pastOnly", "futureOnly",
    "minAge", "workingDays",
})

VALIDATOR_TYPES: frozenset[str] = _VALIDATOR_TYPES_TS | _VALIDATOR_TYPES_OBSERVED

TABLE_COLUMN_TYPES: frozenset[str] = frozenset({
    "text", "number", "calculated", "select", "multiselect", "radio", "date",
    "checkbox", "file", "live_capture", "live_capture_general", "configuration",
    "_api_trigger",
})

#: Field types that must carry an `options` array.
OPTION_BEARING_TYPES: frozenset[str] = frozenset({"select", "multiselect", "radio"})

#: Table column types that must carry an `options` array.
OPTION_BEARING_COLUMN_TYPES: frozenset[str] = frozenset({"select", "multiselect", "radio"})

#: Field types that carry the mobile+otp nested id pair.
MOBILE_OTP_TYPES: frozenset[str] = frozenset({"mobile_verification", "in_form_login"})


# --- Public entry point -----------------------------------------------------


def validate_structural(index: FormIndex) -> list[ValidationError]:
    """Run all L1 checks over an already-built form index."""
    errors: list[ValidationError] = []

    for entry in index.fields.values():
        errors.extend(_check_field(entry.node, entry.path))

    for entry in index.columns.values():
        errors.extend(_check_column(entry.node, entry.path))

    return errors


# --- Field-level checks -----------------------------------------------------


def _err(code: str, message: str, path: str, field_id: str = "") -> ValidationError:
    return ValidationError(
        level=Level.L1_STRUCTURAL, code=code, message=message, path=path, field_id=field_id
    )


def _check_field(fld: dict[str, Any], path: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    fid = str(fld.get("id", ""))
    ftype = fld.get("type")

    # 1. type must be a known FieldType
    if ftype not in FIELD_TYPES:
        out.append(_err(
            "invalid_field_type",
            f"field '{fid}' has invalid type '{ftype}'",
            path, fid,
        ))
        # Type-specific checks below assume a known type; bail early.
        return out

    # 2. label.en required (line_break is exported without a label)
    if ftype != "line_break":
        out += _check_localized(fld.get("label"), f"field '{fid}' label", path, fid)

    # 3. option-bearing fields need options OR a datasource. Real forms source
    #    select/radio options from an API `datasource` instead of a static list.
    if ftype in OPTION_BEARING_TYPES and not fld.get("datasource"):
        out += _check_options(fld.get("options"), fid, path)

    # 4. table/computed_table/verification need columns
    if ftype in COLUMN_BEARING_TYPES:
        cols = fld.get("columns")
        if not isinstance(cols, list) or not cols:
            out.append(_err(
                "missing_columns",
                f"field '{fid}' of type '{ftype}' must have a non-empty 'columns' array",
                path, fid,
            ))

    # 5. mobile_verification / in_form_login need mobileNumberField + otpField
    if ftype in MOBILE_OTP_TYPES:
        for key in ("mobileNumberField", "otpField"):
            if not isinstance(fld.get(key), dict):
                out.append(_err(
                    "missing_nested_field",
                    f"field '{fid}' of type '{ftype}' must define '{key}'",
                    path, fid,
                ))

    # 6. api_trigger needs api_config with an endpoint
    if ftype == "api_trigger":
        cfg = fld.get("api_config")
        if not isinstance(cfg, dict):
            out.append(_err(
                "missing_api_config",
                f"field '{fid}' of type 'api_trigger' must define 'api_config'",
                path, fid,
            ))
        elif not cfg.get("endpoint"):
            out.append(_err(
                "missing_api_endpoint",
                f"field '{fid}' api_config must include an 'endpoint'",
                path, fid,
            ))

    # 7. validators, if present, must use known types
    out += _check_validators(fld.get("validators"), fid, path)

    # 8. feature-contract shape checks (transliteration/translation/etc.)
    out += _check_feature_contracts(fld, fid, path)

    return out


def _check_feature_contracts(
    fld: dict[str, Any], fid: str, path: str
) -> list[ValidationError]:
    """
    Enforce the FLAT-PROPERTY feature contracts (see prompts/constants.py
    FEATURE_CONTRACTS). Catches the common LLM mistakes:
      - transliteration/translation enabled but missing its target field
      - a feature target that points at the field itself
      - a `translate_textarea` field that carries none of the transliteration
        properties (the model invented a type instead of using the feature)
    Self-referential targets and enabled-without-target are hard errors so the
    repair loop corrects them; existence of the target is checked (softly) in
    business_rules.
    """
    out: list[ValidationError] = []
    ftype = fld.get("type")

    # transliteration: if enabled, transliterationField is required and must not
    # be the field itself.
    if fld.get("transliteration") is True:
        target = fld.get("transliterationField")
        if not isinstance(target, str) or not target.strip():
            out.append(_err(
                "transliteration_missing_target",
                f"field '{fid}' has \"transliteration\": true but no "
                f"\"transliterationField\" (the id of the paired field)",
                path, fid,
            ))
        elif target == fid:
            out.append(_err(
                "transliteration_self_reference",
                f"field '{fid}' \"transliterationField\" must reference the OTHER "
                f"field in the pair, not itself",
                path, fid,
            ))

    # translation (NMT): same rule with translationField.
    if fld.get("translation") is True:
        target = fld.get("translationField")
        if not isinstance(target, str) or not target.strip():
            out.append(_err(
                "translation_missing_target",
                f"field '{fid}' has \"translation\": true but no "
                f"\"translationField\" (the target field id)",
                path, fid,
            ))
        elif target == fid:
            out.append(_err(
                "translation_self_reference",
                f"field '{fid}' \"translationField\" must reference the target "
                f"field, not itself",
                path, fid,
            ))

    # translate_textarea used as a "type" without the transliteration feature is
    # almost always the model inventing a type instead of using the flat feature.
    # Nudge it toward the correct shape.
    if ftype == "translate_textarea" and fld.get("transliteration") is not True:
        out.append(_err(
            "translate_textarea_without_feature",
            f"field '{fid}' uses type 'translate_textarea' but does not set the "
            f"transliteration feature. Prefer type 'text'/'textarea' with "
            f"\"transliteration\": true and \"transliterationField\": \"<other field id>\". "
            f"Do not use \"sourceField\"/\"dependsOn\" for transliteration.",
            path, fid,
        ))

    return out


# --- Column-level checks ----------------------------------------------------


def _check_column(col: dict[str, Any], path: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    cid = str(col.get("id", ""))
    ctype = col.get("type")

    if ctype not in TABLE_COLUMN_TYPES:
        out.append(_err(
            "invalid_column_type",
            f"column '{cid}' has invalid type '{ctype}'",
            path, cid,
        ))
        return out

    out += _check_localized(col.get("label"), f"column '{cid}' label", path, cid)

    if ctype in OPTION_BEARING_COLUMN_TYPES:
        # Select columns may source options from a datasource instead of a
        # static list, so only flag when neither is present.
        if not col.get("options") and not col.get("datasource"):
            out.append(_err(
                "missing_column_options",
                f"column '{cid}' of type '{ctype}' needs 'options' or a 'datasource'",
                path, cid,
            ))

    out += _check_validators(col.get("validators"), cid, path)
    return out


# --- Small shared checks ----------------------------------------------------


def _check_localized(
    value: Any, what: str, path: str, fid: str
) -> list[ValidationError]:
    """A LocalizedText must be an object with a non-empty `en` string."""
    if not isinstance(value, dict):
        return [_err("missing_localized", f"{what} must be a localized object with 'en'", path, fid)]
    en = value.get("en")
    if not isinstance(en, str) or not en.strip():
        return [_err("missing_en", f"{what} must have a non-empty 'en' value", path, fid)]
    return []


def _check_options(options: Any, fid: str, path: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    if not isinstance(options, list) or not options:
        return [_err(
            "missing_options",
            f"field '{fid}' must have a non-empty 'options' array",
            path, fid,
        )]
    for i, opt in enumerate(options):
        if not isinstance(opt, dict) or "value" not in opt:
            out.append(_err(
                "invalid_option",
                f"field '{fid}' option[{i}] must be an object with a 'value'",
                path, fid,
            ))
    return out


def _check_validators(
    validators: Any, fid: str, path: str
) -> list[ValidationError]:
    if validators is None:
        return []
    if not isinstance(validators, list):
        return [_err(
            "invalid_validators",
            f"'{fid}' validators must be an array",
            path, fid,
        )]
    out: list[ValidationError] = []
    for i, v in enumerate(validators):
        if not isinstance(v, dict):
            out.append(_err(
                "invalid_validator",
                f"'{fid}' validators[{i}] must be an object",
                path, fid,
            ))
            continue
        vtype = v.get("type")
        # `type` is optional in the TS interface, but if present it must be known.
        if vtype is not None and vtype not in VALIDATOR_TYPES:
            out.append(_err(
                "invalid_validator_type",
                f"'{fid}' validators[{i}] has invalid type '{vtype}'",
                path, fid,
            ))
    return out
