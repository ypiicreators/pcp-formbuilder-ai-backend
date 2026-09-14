"""
Level 2 — Business-rule validation (cross-reference correctness).

L1 checks that each node is shaped right. L2 checks that nodes are consistent
with EACH OTHER: ids are unique, conditional/dependency references point at
fields that actually exist, and singleton rules hold. With schema-constrained
generation in place, this is where most real failures surface (plan v3 §2.1).

Reference keys are transcribed from `formBuilder.types.ts`. Missing one here
means dangling references slip through, so the list is deliberately broad and
covers both field-level and table-column-level relationships.
"""

from __future__ import annotations

from typing import Any

from app.core.form_index import FormIndex
from app.validation.result import Level, Severity, ValidationError

#: The canonical id an `applying_for` field must use (plan singleton rule).
APPLYING_FOR_ID = "applying_for_yourself"


def _err(
    code: str,
    message: str,
    path: str = "",
    field_id: str = "",
    severity: Severity = Severity.ERROR,
) -> ValidationError:
    return ValidationError(
        level=Level.L2_BUSINESS, code=code, message=message,
        path=path, field_id=field_id, severity=severity,
    )


def validate_business(index: FormIndex) -> list[ValidationError]:
    """Run all L2 checks over an already-built form index."""
    errors: list[ValidationError] = []
    errors += _check_duplicate_ids(index)
    errors += _check_applying_for_singleton(index)
    errors += _check_dangling_references(index)
    return errors


# --- 1. Uniqueness ----------------------------------------------------------


def _check_duplicate_ids(index: FormIndex) -> list[ValidationError]:
    """Field ids must be globally unique (build_index already found dups)."""
    out: list[ValidationError] = []
    for dup in index.duplicate_field_ids:
        out.append(_err(
            "duplicate_field_id",
            f"field id '{dup}' is used more than once (ids must be globally unique)",
            field_id=dup,
        ))
    return out


# --- 2. applying_for singleton ---------------------------------------------


def _check_applying_for_singleton(index: FormIndex) -> list[ValidationError]:
    """At most one applying_for field; its id must be applying_for_yourself."""
    out: list[ValidationError] = []
    applying = [e for e in index.fields.values() if e.node.get("type") == "applying_for"]

    if len(applying) > 1:
        out.append(_err(
            "multiple_applying_for",
            f"only one 'applying_for' field is allowed; found {len(applying)}",
        ))

    for e in applying:
        if e.id != APPLYING_FOR_ID:
            out.append(_err(
                "applying_for_wrong_id",
                f"'applying_for' field id must be '{APPLYING_FOR_ID}', got '{e.id}'",
                e.path, e.id,
            ))
    return out


# --- 3. Dangling references -------------------------------------------------

#: Conditional keys. In real forms these take EITHER a simple map
#: `{ fieldId: [values...] }` OR a structured form
#: `{ operator: "AND"|"OR", conditions: [ { field, operator, value } ] }`.
CONDITION_MAP_KEYS = ("visibleWhen", "hiddenWhen", "disabledWhen")

#: Sentinels that appear where a field id would go but are not real fields.
REFERENCE_SENTINELS = frozenset({"form_load", "onLoad", "load"})

#: Field ids injected by the runtime/workflow rather than defined in the form
#: JSON. Real forms reference these freely, so they count as "known" and never
#: produce a dangling-reference finding.
RUNTIME_INJECTED_IDS = frozenset({
    "applying_for_yourself",  # the applying_for singleton, often runtime-provided
})

#: Reserved keys inside the structured condition shape (not field ids).
_CONDITION_RESERVED_KEYS = frozenset({"operator", "conditions"})

#: Keys whose value is a list of field ids.
ID_LIST_KEYS = ("dependsOn", "dependsOnAny", "concatenateFields", "trigger_on_change")

#: Keys whose value is a single field id and MUST resolve (hard error).
SINGLE_ID_KEYS = ("sourceField", "endDateField", "linkedField")

#: Keys whose value is a single field id that may point at a dynamically-created
#: counterpart (e.g. a Punjabi `*_pa` field materialized at render time). A
#: missing target here is a soft WARNING, not a hard error, because real,
#: working forms routinely reference such fields.
SOFT_SINGLE_ID_KEYS = ("transliterationField", "translationField")


def _check_dangling_references(index: FormIndex) -> list[ValidationError]:
    """
    Every field id referenced by conditional/dependency logic must exist.

    Checks field-level references and, for table columns, column-scoped
    references (a column condition may reference a sibling column or a
    form-level field).
    """
    out: list[ValidationError] = []
    known = index.all_field_ids() | RUNTIME_INJECTED_IDS

    for entry in index.fields.values():
        out += _check_node_references(entry.node, entry.id, entry.path, known)

    # Column references may point at sibling columns OR form-level fields.
    for entry in index.columns.values():
        sibling_cols = _sibling_column_ids(index, entry.parent_field_id)
        col_scope = known | sibling_cols
        out += _check_node_references(entry.node, entry.id, entry.path, col_scope)

    return out


def _sibling_column_ids(index: FormIndex, table_id: str | None) -> set[str]:
    """All column ids within the same table (bare ids, not table-scoped keys)."""
    if not table_id:
        return set()
    return {
        e.id for key, e in index.columns.items()
        if e.parent_field_id == table_id
    }


def _check_node_references(
    node: dict[str, Any], owner_id: str, path: str, known: set[str]
) -> list[ValidationError]:
    out: list[ValidationError] = []

    def flag(ref: Any, source_key: str) -> None:
        if not isinstance(ref, str) or not ref:
            return
        if ref in REFERENCE_SENTINELS:
            return  # special trigger token, not a field id
        if ref not in known:
            # Standalone full-form validation can't see runtime-injected fields,
            # so an unresolved reference is a WARNING here. The change-set flow
            # (which knows exactly what the AI edit introduced) escalates newly
            # introduced dangling references to hard errors.
            out.append(_err(
                "dangling_reference",
                f"'{owner_id}' {source_key} references unknown field '{ref}'",
                path, owner_id, severity=Severity.WARNING,
            ))

    # conditional keys: either a simple map or the structured operator/conditions
    # shape. Extract referenced field ids from whichever form is present.
    for key in CONDITION_MAP_KEYS:
        for ref in _condition_field_refs(node.get(key)):
            flag(ref, key)

    # id-list keys
    for key in ID_LIST_KEYS:
        val = node.get(key)
        if isinstance(val, list):
            for ref in val:
                flag(ref, key)

    # single-id keys that must resolve (hard error)
    for key in SINGLE_ID_KEYS:
        flag(node.get(key), key)

    # transliteration/translation targets: soft warning if unresolved, because
    # the counterpart field may be materialized dynamically at render time.
    for key in SOFT_SINGLE_ID_KEYS:
        ref = node.get(key)
        if (
            isinstance(ref, str)
            and ref
            and ref not in REFERENCE_SENTINELS
            and ref not in known
        ):
            out.append(_err(
                "unresolved_soft_reference",
                f"'{owner_id}' {key} references '{ref}', which is not a static "
                f"field (may be created at render time)",
                path, owner_id, severity=Severity.WARNING,
            ))

    # conditionalAutoFillWhen: list of { condition: { field }, autoFill: { fieldId? } }
    for rule in _as_list(node.get("conditionalAutoFillWhen")):
        if not isinstance(rule, dict):
            continue
        cond = rule.get("condition")
        if isinstance(cond, dict):
            flag(cond.get("field"), "conditionalAutoFillWhen.condition.field")
        auto = rule.get("autoFill")
        if isinstance(auto, dict) and auto.get("source") == "formData":
            flag(auto.get("fieldId"), "conditionalAutoFillWhen.autoFill.fieldId")

    # autoFillWhen: { field, ... }
    awn = node.get("autoFillWhen")
    if isinstance(awn, dict):
        flag(awn.get("field"), "autoFillWhen.field")

    return out


def _condition_field_refs(cond: Any) -> list[str]:
    """
    Extract referenced field ids from a conditional value, supporting both:
      - simple map:      { "field_a": ["yes"], "field_b": [1] }
      - structured form: { "operator": "AND",
                           "conditions": [ { "field": "field_a", ... } ] }
    """
    if not isinstance(cond, dict):
        return []

    # Structured shape: pull field ids from conditions[].field.
    if "conditions" in cond and isinstance(cond["conditions"], list):
        refs: list[str] = []
        for c in cond["conditions"]:
            if isinstance(c, dict) and isinstance(c.get("field"), str):
                refs.append(c["field"])
        return refs

    # Simple map: every key that isn't a reserved word is a field id.
    return [k for k in cond.keys() if k not in _CONDITION_RESERVED_KEYS]


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []
