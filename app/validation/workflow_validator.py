"""
Workflow Builder validation — L1 (structural) + L2 (business rules).

Mirrors validator.py / schema.py / business_rules.py in structure but enforces
the WORKFLOW field-type set and workflow-specific rules:

  L1 structural:
    - field type must be in WORKFLOW_FIELD_TYPES (37 types, no citizen-only types)
    - table column type must be in WORKFLOW_TABLE_COLUMN_TYPES (5 simple types)
    - select/multiselect/radio need options OR datasource
    - table needs columns, table_view needs tableViewColumns
    - api_trigger needs api_config with endpoint
    - enableTransliteration (not "transliteration") rules
    - validator types against WORKFLOW_VALIDATOR_TYPES

  L2 business:
    - globally unique field ids
    - singleton type enforcement (activity_timeline, previous_history,
      role_wise_history — at most one each)
    - dangling reference checks (same logic as citizen form business_rules)

This module is the single gate the workflow orchestration layer calls after
generating or applying a change-set. Both L1 and L2 run every time so the
caller sees the complete error list.
"""

from __future__ import annotations

from typing import Any

from app.core.form_index import COLUMN_BEARING_TYPES, FormIndex, build_index
from app.prompts.workflow_constants import (
    WORKFLOW_FIELD_TYPES,
    WORKFLOW_SINGLETON_TYPES,
    WORKFLOW_TABLE_COLUMN_TYPES,
    WORKFLOW_TYPES_REQUIRING_API_CONFIG,
    WORKFLOW_TYPES_REQUIRING_COLUMNS,
    WORKFLOW_TYPES_REQUIRING_OPTIONS,
    WORKFLOW_TYPES_REQUIRING_TABLE_VIEW_COLUMNS,
    WORKFLOW_VALIDATOR_TYPES,
)
from app.validation.result import Level, Severity, ValidationError, ValidationResult

# Frozen sets for fast membership tests
_WORKFLOW_FIELD_TYPES: frozenset[str] = frozenset(WORKFLOW_FIELD_TYPES)
_WORKFLOW_VALIDATOR_TYPES: frozenset[str] = frozenset(WORKFLOW_VALIDATOR_TYPES)
_WORKFLOW_TABLE_COLUMN_TYPES: frozenset[str] = frozenset(WORKFLOW_TABLE_COLUMN_TYPES)
_WORKFLOW_TYPES_REQUIRING_OPTIONS: frozenset[str] = frozenset(WORKFLOW_TYPES_REQUIRING_OPTIONS)
_WORKFLOW_TYPES_REQUIRING_COLUMNS: frozenset[str] = frozenset(WORKFLOW_TYPES_REQUIRING_COLUMNS)
_WORKFLOW_TYPES_REQUIRING_TABLE_VIEW: frozenset[str] = frozenset(
    WORKFLOW_TYPES_REQUIRING_TABLE_VIEW_COLUMNS
)
_WORKFLOW_TYPES_REQUIRING_API_CONFIG: frozenset[str] = frozenset(
    WORKFLOW_TYPES_REQUIRING_API_CONFIG
)
_WORKFLOW_SINGLETON_TYPES: frozenset[str] = frozenset(WORKFLOW_SINGLETON_TYPES)

# Citizen-only types — never valid in a workflow form.
# Used to give a more helpful error message than "invalid type".
_CITIZEN_ONLY_TYPES: frozenset[str] = frozenset({
    "applying_for",
    "applicant_aadhaar_ekyc",
    "beneficiary_aadhaar_ekyc",
    "guardian_aadhaar_ekyc",
    "other_aadhaar_ekyc",
    "pan_verification",
    "declaration",
    "mobile_verification",
    "in_form_login",
    "otp",
    "computed_table",
    "verification",
})

# Reference sentinels that are allowed even though they are not field ids.
_REFERENCE_SENTINELS: frozenset[str] = frozenset({"form_load", "onLoad", "load"})
_CONDITION_RESERVED_KEYS: frozenset[str] = frozenset({"operator", "conditions"})


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def validate_workflow_form(form: dict[str, Any]) -> ValidationResult:
    """Validate a complete workflow form object. Builds the index internally."""
    index = build_index(form)
    return _validate_workflow_index(index)


def _validate_workflow_index(index: FormIndex) -> ValidationResult:
    """Run L1 + L2 over a pre-built index."""
    result = ValidationResult()
    result.extend(_validate_workflow_structural(index))
    result.extend(_validate_workflow_business(index))
    return result


# ---------------------------------------------------------------------------
# L1 — Structural validation
# ---------------------------------------------------------------------------


def _validate_workflow_structural(index: FormIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for entry in index.fields.values():
        errors.extend(_check_workflow_field(entry.node, entry.path))
    for entry in index.columns.values():
        errors.extend(_check_workflow_column(entry.node, entry.path))
    return errors


def _err(
    code: str,
    message: str,
    path: str,
    field_id: str = "",
    severity: Severity = Severity.ERROR,
) -> ValidationError:
    return ValidationError(
        level=Level.L1_STRUCTURAL,
        code=code,
        message=message,
        path=path,
        field_id=field_id,
        severity=severity,
    )


def _check_workflow_field(fld: dict[str, Any], path: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    fid = str(fld.get("id", ""))
    ftype = fld.get("type")

    # 1. Type must be a known workflow FieldType
    if ftype in _CITIZEN_ONLY_TYPES:
        out.append(_err(
            "citizen_type_in_workflow",
            f"field '{fid}' uses citizen-portal type '{ftype}' which is not "
            f"available in the workflow builder. Use a workflow-compatible type instead.",
            path, fid,
        ))
        return out

    if ftype not in _WORKFLOW_FIELD_TYPES:
        out.append(_err(
            "invalid_workflow_field_type",
            f"field '{fid}' has invalid type '{ftype}'. "
            f"Must be one of the allowed workflow field types.",
            path, fid,
        ))
        return out

    # 2. label.en required (line_break and description exempt)
    if ftype not in ("line_break", "description", "htmlDescription"):
        out += _check_localized(fld.get("label"), f"field '{fid}' label", path, fid)

    # 3. select/multiselect/radio need options OR datasource
    if ftype in _WORKFLOW_TYPES_REQUIRING_OPTIONS:
        if not fld.get("datasource"):
            out += _check_options(fld.get("options"), fid, path)

    # 4. table needs columns
    if ftype in _WORKFLOW_TYPES_REQUIRING_COLUMNS:
        cols = fld.get("columns")
        if not isinstance(cols, list) or not cols:
            out.append(_err(
                "missing_columns",
                f"field '{fid}' of type 'table' must have a non-empty 'columns' array",
                path, fid,
            ))

    # 5. table_view needs tableViewColumns
    if ftype in _WORKFLOW_TYPES_REQUIRING_TABLE_VIEW:
        tvc = fld.get("tableViewColumns")
        if not isinstance(tvc, list) or not tvc:
            out.append(_err(
                "missing_table_view_columns",
                f"field '{fid}' of type 'table_view' must have a non-empty "
                f"'tableViewColumns' array. "
                f"Each entry: {{ \"id\": string, \"label\": LocalizedText, \"showTotal\"?: boolean }}",
                path, fid,
            ))

    # 6. api_trigger needs api_config with endpoint
    if ftype in _WORKFLOW_TYPES_REQUIRING_API_CONFIG:
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
                f"field '{fid}' api_config must include a non-empty 'endpoint'",
                path, fid,
            ))

    # 7. Transliteration: workflow uses "enableTransliteration" not "transliteration"
    out += _check_workflow_transliteration(fld, fid, path)

    # 8. Validators must use known workflow types
    out += _check_workflow_validators(fld.get("validators"), fid, path)

    return out


def _check_workflow_transliteration(
    fld: dict[str, Any], fid: str, path: str
) -> list[ValidationError]:
    """
    Check transliteration rules specific to the workflow builder.
    - enableTransliteration (boolean) is the correct key
    - If True, transliterationField must be present and must not be self-referential
    - If the citizen-form key "transliteration": True is present, flag it
    """
    out: list[ValidationError] = []

    # Catch the citizen-form key used in a workflow form
    if fld.get("transliteration") is True:
        out.append(_err(
            "wrong_transliteration_key",
            f"field '{fid}' uses \"transliteration\": true which is the citizen "
            f"form builder key. In the workflow builder, use "
            f"\"enableTransliteration\": true instead.",
            path, fid,
        ))

    # Check enableTransliteration rules
    if fld.get("enableTransliteration") is True:
        target = fld.get("transliterationField")
        if not isinstance(target, str) or not target.strip():
            out.append(_err(
                "transliteration_missing_target",
                f"field '{fid}' has \"enableTransliteration\": true but no "
                f"\"transliterationField\" — set it to the id of the paired field.",
                path, fid,
            ))
        elif target == fid:
            out.append(_err(
                "transliteration_self_reference",
                f"field '{fid}' \"transliterationField\" must reference the OTHER "
                f"field in the pair, not itself.",
                path, fid,
            ))

    return out


def _check_workflow_column(col: dict[str, Any], path: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    cid = str(col.get("id", ""))
    ctype = col.get("type")

    if ctype not in _WORKFLOW_TABLE_COLUMN_TYPES:
        out.append(_err(
            "invalid_workflow_column_type",
            f"column '{cid}' has invalid type '{ctype}'. "
            f"Workflow table columns support only: {', '.join(sorted(_WORKFLOW_TABLE_COLUMN_TYPES))}",
            path, cid,
        ))
        return out

    out += _check_localized(col.get("label"), f"column '{cid}' label", path, cid)

    if ctype in ("select", "multiselect", "radio"):
        if not col.get("options") and not col.get("datasource"):
            out.append(_err(
                "missing_column_options",
                f"column '{cid}' of type '{ctype}' needs 'options' or a 'datasource'",
                path, cid,
            ))

    out += _check_workflow_validators(col.get("validators"), cid, path)
    return out


def _check_localized(
    value: Any, what: str, path: str, fid: str
) -> list[ValidationError]:
    if not isinstance(value, dict):
        return [_err(
            "missing_localized",
            f"{what} must be a localized object with 'en'",
            path, fid,
        )]
    en = value.get("en")
    if not isinstance(en, str) or not en.strip():
        return [_err(
            "missing_en",
            f"{what} must have a non-empty 'en' value",
            path, fid,
        )]
    return []


def _check_options(options: Any, fid: str, path: str) -> list[ValidationError]:
    if not isinstance(options, list) or not options:
        return [_err(
            "missing_options",
            f"field '{fid}' must have a non-empty 'options' array (or a 'datasource')",
            path, fid,
        )]
    out: list[ValidationError] = []
    for i, opt in enumerate(options):
        if not isinstance(opt, dict) or "value" not in opt:
            out.append(_err(
                "invalid_option",
                f"field '{fid}' option[{i}] must be an object with a 'value'",
                path, fid,
            ))
    return out


def _check_workflow_validators(
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
        if vtype is not None and vtype not in _WORKFLOW_VALIDATOR_TYPES:
            out.append(_err(
                "invalid_workflow_validator_type",
                f"'{fid}' validators[{i}] has invalid type '{vtype}'",
                path, fid,
            ))
    return out


# ---------------------------------------------------------------------------
# L2 — Business-rule validation
# ---------------------------------------------------------------------------


def _validate_workflow_business(index: FormIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []
    errors += _check_duplicate_ids(index)
    errors += _check_workflow_singletons(index)
    errors += _check_dangling_references(index)
    return errors


def _berr(
    code: str,
    message: str,
    path: str = "",
    field_id: str = "",
    severity: Severity = Severity.ERROR,
) -> ValidationError:
    return ValidationError(
        level=Level.L2_BUSINESS,
        code=code,
        message=message,
        path=path,
        field_id=field_id,
        severity=severity,
    )


def _check_duplicate_ids(index: FormIndex) -> list[ValidationError]:
    out: list[ValidationError] = []
    for dup in index.duplicate_field_ids:
        out.append(_berr(
            "duplicate_field_id",
            f"field id '{dup}' is used more than once (ids must be globally unique)",
            field_id=dup,
        ))
    return out


def _check_workflow_singletons(index: FormIndex) -> list[ValidationError]:
    """
    Enforce singleton constraint: activity_timeline, previous_history, and
    role_wise_history may each appear AT MOST ONCE per form.
    """
    out: list[ValidationError] = []
    counts: dict[str, list[str]] = {t: [] for t in _WORKFLOW_SINGLETON_TYPES}

    for entry in index.fields.values():
        ftype = entry.node.get("type")
        if ftype in counts:
            counts[ftype].append(entry.id)

    for ftype, ids in counts.items():
        if len(ids) > 1:
            out.append(_berr(
                "multiple_singleton_type",
                f"type '{ftype}' may appear at most once per workflow form; "
                f"found {len(ids)} instances: {', '.join(ids)}",
            ))
    return out


def _check_dangling_references(index: FormIndex) -> list[ValidationError]:
    """
    Every field id referenced by conditional/dependency logic must exist.
    Mirrors the citizen form logic from business_rules.py.
    """
    out: list[ValidationError] = []
    known = index.all_field_ids()

    for entry in index.fields.values():
        out += _check_node_references(entry.node, entry.id, entry.path, known)

    for entry in index.columns.values():
        sibling_cols = {
            e.id for e in index.columns.values()
            if e.parent_field_id == entry.parent_field_id
        }
        col_scope = known | sibling_cols
        out += _check_node_references(entry.node, entry.id, entry.path, col_scope)

    return out


_CONDITION_MAP_KEYS = ("visibleWhen", "hiddenWhen", "disabledWhen")
_ID_LIST_KEYS = ("dependsOn", "dependsOnAny", "concatenateFields", "trigger_on_change")
_SINGLE_ID_KEYS = ("sourceField", "endDateField")
_SOFT_SINGLE_ID_KEYS = ("transliterationField", "translationField")


def _check_node_references(
    node: dict[str, Any], owner_id: str, path: str, known: set[str]
) -> list[ValidationError]:
    out: list[ValidationError] = []

    def flag(ref: Any, source_key: str, soft: bool = False) -> None:
        if not isinstance(ref, str) or not ref:
            return
        if ref in _REFERENCE_SENTINELS:
            return
        if ref not in known:
            severity = Severity.WARNING if soft else Severity.WARNING
            out.append(_berr(
                "dangling_reference",
                f"'{owner_id}' {source_key} references unknown field '{ref}'",
                path, owner_id, severity=severity,
            ))

    for key in _CONDITION_MAP_KEYS:
        for ref in _condition_field_refs(node.get(key)):
            flag(ref, key)

    for key in _ID_LIST_KEYS:
        val = node.get(key)
        if isinstance(val, list):
            for ref in val:
                flag(ref, key)

    for key in _SINGLE_ID_KEYS:
        flag(node.get(key), key)

    for key in _SOFT_SINGLE_ID_KEYS:
        flag(node.get(key), key, soft=True)

    # conditionalAutoFillWhen
    for rule in (node.get("conditionalAutoFillWhen") or []):
        if not isinstance(rule, dict):
            continue
        cond = rule.get("condition")
        if isinstance(cond, dict):
            flag(cond.get("field"), "conditionalAutoFillWhen.condition.field")
        auto = rule.get("autoFill")
        if isinstance(auto, dict) and auto.get("source") == "formData":
            flag(auto.get("fieldId"), "conditionalAutoFillWhen.autoFill.fieldId")

    # autoFillWhen
    awn = node.get("autoFillWhen")
    if isinstance(awn, dict):
        awn_field = awn.get("field")
        if awn_field and awn_field not in _REFERENCE_SENTINELS:
            flag(awn_field, "autoFillWhen.field")

    return out


def _condition_field_refs(cond: Any) -> list[str]:
    if not isinstance(cond, dict):
        return []
    if "conditions" in cond and isinstance(cond["conditions"], list):
        return [
            c["field"]
            for c in cond["conditions"]
            if isinstance(c, dict) and isinstance(c.get("field"), str)
        ]
    return [k for k in cond.keys() if k not in _CONDITION_RESERVED_KEYS]
