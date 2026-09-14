"""
Change-set — the ID-anchored edit contract.

This is the cornerstone of Edit mode (plan v3 §2.3, §5, §7.1). Instead of asking
the LLM to regenerate a 6,000-line form (slow, drift-prone, and it drops locale
strings), the model returns a small list of OPERATIONS that name the field id and
the change. This module:

  1. Parses/validates that operation list (parse_change_set).
  2. Applies it to a DEEP COPY of the full server-side form (apply_change_set).
  3. Produces a before/after diff for the human reviewer (build_diff).

Non-negotiable rules baked in here:
  - **ID-anchored, never index-based.** Every op names a `fieldId` (and, for
    columns, a `tableId`). We resolve via form_index; we never touch `sections[3]`.
  - **All-or-nothing per request.** We apply to a copy and collect per-op errors.
    If any op fails, the caller keeps the untouched original (plan §9: nothing
    applied on failure).
  - **Metadata is out of scope.** Ops that target top-level metadata keys are
    rejected (plan v3 §0/§5). The change-set edits fields, not serviceCode/title.
  - **Locale-preserving.** setProp replaces a single property; it never rewrites
    a whole field, so `pa`/`hi` strings elsewhere on the field survive.

Pure Python: no LLM, no FastAPI.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.form_index import COLUMN_BEARING_TYPES, build_index


class OpType(str, Enum):
    """The operations the LLM may emit. Deliberately small and explicit."""

    SET_PROP = "setProp"           # set/replace a property on a field
    UNSET_PROP = "unsetProp"       # remove a property from a field
    SET_COLUMN_PROP = "setColumnProp"    # set/replace a property on a table column
    UNSET_COLUMN_PROP = "unsetColumnProp"  # remove a property from a table column
    ADD_OPTION = "addOption"       # append an option to a select/radio field
    REMOVE_OPTION = "removeOption" # remove an option (by value) from a field
    ADD_FIELD = "addField"         # add a new field to a section
    REMOVE_FIELD = "removeField"   # remove an existing field


#: Top-level keys the change-set must never touch (backend-owned metadata).
PROTECTED_TOP_LEVEL_KEYS = frozenset({
    "serviceCode", "title", "description", "category", "version",
})

#: Properties the change-set must never set directly on a field.
#: `order` is store-derived; `id` renames would break every reference.
PROTECTED_FIELD_PROPS = frozenset({"order", "id"})


@dataclass
class Op:
    """A single parsed, shape-valid operation."""

    type: OpType
    field_id: str = ""
    table_id: str = ""          # for column ops
    property: str = ""          # for prop ops
    value: Any = None           # for setProp / addOption / addField payloads
    option_value: str = ""      # for removeOption
    section_id: str = ""        # for addField (which section to add into)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    """Outcome of parsing an LLM change-set payload."""

    ops: list[Op] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ApplyResult:
    """Outcome of applying a change-set to a form."""

    form: dict[str, Any]                 # the resulting form (copy) if ok, else original
    errors: list[str] = field(default_factory=list)
    applied_field_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --- Parsing ----------------------------------------------------------------


def parse_change_set(payload: Any) -> ParseResult:
    """
    Turn raw LLM output into a list of validated Ops.

    Accepts:
      - a raw JSON STRING (LLM text; markdown fences are stripped),
      - a bare list of ops, or
      - `{ "changes": [...] }` (the shape used in the plan examples).
    Each op is shape-checked; unknown op types and protected targets are
    rejected here, before anything is applied.
    """
    result = ParseResult()

    # Raw model text -> parse to an object first (fence-tolerant).
    if isinstance(payload, str):
        obj, err = _loads_tolerant(payload)
        if err:
            result.errors.append(err)
            return result
        payload = obj

    if isinstance(payload, dict) and "changes" in payload:
        payload = payload.get("changes")

    if not isinstance(payload, list):
        result.errors.append("change-set must be a list of operations (or {changes: [...]})")
        return result

    for i, raw in enumerate(payload):
        if not isinstance(raw, dict):
            result.errors.append(f"op[{i}] must be an object")
            continue
        op_name = raw.get("op") or raw.get("type")
        try:
            op_type = OpType(op_name)
        except ValueError:
            result.errors.append(f"op[{i}] has unknown op '{op_name}'")
            continue

        parsed, err = _parse_one(op_type, raw, i)
        if err:
            result.errors.append(err)
        else:
            result.ops.append(parsed)

    return result


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)
_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def _loads_tolerant(text: str) -> tuple[Any, str]:
    """
    Parse JSON from possibly-fenced model text. Returns (obj, "") on success or
    (None, error_message) on failure. Mirrors the provider's recovery so the
    core stays self-contained (no provider import).
    """
    if not text or not text.strip():
        return None, "change-set was empty"
    s = text.strip()
    fence = _FENCE_RE.match(s)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s), ""
    except (TypeError, ValueError):
        pass
    block = _BLOCK_RE.search(s)
    if block:
        try:
            return json.loads(block.group(1)), ""
        except (TypeError, ValueError) as exc:
            return None, f"could not parse change-set JSON: {exc}"
    return None, "change-set contained no JSON"


def _parse_one(op_type: OpType, raw: dict, i: int) -> tuple[Op | None, str]:
    """Shape-check a single op and build the Op, or return an error string."""
    fid = raw.get("fieldId", "")
    prop = raw.get("property", "")

    if op_type in (OpType.SET_PROP, OpType.UNSET_PROP):
        if not fid:
            return None, f"op[{i}] {op_type.value} requires 'fieldId'"
        if not prop:
            return None, f"op[{i}] {op_type.value} requires 'property'"
        if prop in PROTECTED_FIELD_PROPS:
            return None, f"op[{i}] cannot modify protected property '{prop}'"
        if op_type is OpType.SET_PROP and "value" not in raw:
            return None, f"op[{i}] setProp requires 'value'"
        return Op(type=op_type, field_id=fid, property=prop,
                  value=raw.get("value"), raw=raw), ""

    if op_type in (OpType.SET_COLUMN_PROP, OpType.UNSET_COLUMN_PROP):
        tid = raw.get("tableId", "")
        if not tid or not fid:
            return None, f"op[{i}] {op_type.value} requires 'tableId' and 'fieldId' (column id)"
        if not prop:
            return None, f"op[{i}] {op_type.value} requires 'property'"
        if op_type is OpType.SET_COLUMN_PROP and "value" not in raw:
            return None, f"op[{i}] setColumnProp requires 'value'"
        return Op(type=op_type, table_id=tid, field_id=fid, property=prop,
                  value=raw.get("value"), raw=raw), ""

    if op_type is OpType.ADD_OPTION:
        if not fid:
            return None, f"op[{i}] addOption requires 'fieldId'"
        opt = raw.get("value")
        if not isinstance(opt, dict) or "value" not in opt or "label" not in opt:
            return None, f"op[{i}] addOption 'value' must be an option object with value+label"
        return Op(type=op_type, field_id=fid, value=opt, raw=raw), ""

    if op_type is OpType.REMOVE_OPTION:
        if not fid:
            return None, f"op[{i}] removeOption requires 'fieldId'"
        ov = raw.get("optionValue")
        if not isinstance(ov, str) or not ov:
            return None, f"op[{i}] removeOption requires string 'optionValue'"
        return Op(type=op_type, field_id=fid, option_value=ov, raw=raw), ""

    if op_type is OpType.ADD_FIELD:
        sec = raw.get("sectionId", "")
        newf = raw.get("value")
        if not sec:
            return None, f"op[{i}] addField requires 'sectionId'"
        if not isinstance(newf, dict) or not newf.get("id") or not newf.get("type"):
            return None, f"op[{i}] addField 'value' must be a field object with id+type"
        return Op(type=op_type, section_id=sec, field_id=str(newf["id"]),
                  value=newf, raw=raw), ""

    if op_type is OpType.REMOVE_FIELD:
        if not fid:
            return None, f"op[{i}] removeField requires 'fieldId'"
        return Op(type=op_type, field_id=fid, raw=raw), ""

    return None, f"op[{i}] unsupported op '{op_type.value}'"


# --- Applying ---------------------------------------------------------------


def apply_change_set(form: dict[str, Any], ops: list[Op]) -> ApplyResult:
    """
    Apply `ops` to a DEEP COPY of `form`, ID-anchored.

    Validates every op against the current form state, then mutates. If ANY op
    fails, returns the untouched original with the collected errors — nothing is
    partially applied (plan §9). Structural ops (addField/removeField) rebuild
    the index so later ops see the updated form.
    """
    working = copy.deepcopy(form)
    errors: list[str] = []
    applied: list[str] = []

    # First pass: validate all ops against the ORIGINAL state so we can bail
    # before mutating. addField ids are excluded from the "must exist" check.
    index = build_index(working)
    pending_new_ids = {op.field_id for op in ops if op.type is OpType.ADD_FIELD}

    for op in ops:
        err = _validate_op_targets(op, index, working, pending_new_ids)
        if err:
            errors.append(err)

    if errors:
        return ApplyResult(form=form, errors=errors)

    # Second pass: mutate. Rebuild the index after structural changes.
    for op in ops:
        try:
            changed_id = _apply_one(op, working)
            if changed_id:
                applied.append(changed_id)
            if op.type in (OpType.ADD_FIELD, OpType.REMOVE_FIELD):
                index = build_index(working)
        except Exception as exc:  # defensive: never leak a half-applied form
            return ApplyResult(
                form=form,
                errors=[f"failed applying {op.type.value} on '{op.field_id}': {exc}"],
            )

    return ApplyResult(form=working, applied_field_ids=applied)


def _validate_op_targets(op: Op, index, form: dict, pending_new_ids: set) -> str:
    """Check that an op's target ids resolve in the current form."""
    if op.type in (OpType.SET_PROP, OpType.UNSET_PROP, OpType.REMOVE_FIELD,
                   OpType.ADD_OPTION, OpType.REMOVE_OPTION):
        if not index.has_id(op.field_id):
            return f"{op.type.value} targets unknown field '{op.field_id}'"

    if op.type in (OpType.ADD_OPTION, OpType.REMOVE_OPTION):
        entry = index.get_field(op.field_id)
        if entry and entry.node.get("type") not in ("select", "multiselect", "radio"):
            return f"{op.type.value} field '{op.field_id}' is not an option-bearing type"

    if op.type in (OpType.SET_COLUMN_PROP, OpType.UNSET_COLUMN_PROP):
        if not index.get_column(op.table_id, op.field_id):
            return (f"{op.type.value} targets unknown column "
                    f"'{op.table_id}.{op.field_id}'")

    if op.type is OpType.ADD_FIELD:
        # Section must exist; new id must not collide with an existing one.
        if not _find_section(form, op.section_id):
            return f"addField targets unknown section '{op.section_id}'"
        existing = index.all_field_ids() | (pending_new_ids - {op.field_id})
        if op.field_id in existing:
            return f"addField id '{op.field_id}' already exists"

    return ""


def _apply_one(op: Op, form: dict) -> str:
    """Mutate `form` for a single (already-validated) op. Returns changed id."""
    index = build_index(form)

    if op.type is OpType.SET_PROP:
        index.get_field(op.field_id).node[op.property] = op.value
        return op.field_id

    if op.type is OpType.UNSET_PROP:
        index.get_field(op.field_id).node.pop(op.property, None)
        return op.field_id

    if op.type is OpType.SET_COLUMN_PROP:
        index.get_column(op.table_id, op.field_id).node[op.property] = op.value
        return op.table_id

    if op.type is OpType.UNSET_COLUMN_PROP:
        index.get_column(op.table_id, op.field_id).node.pop(op.property, None)
        return op.table_id

    if op.type is OpType.ADD_OPTION:
        node = index.get_field(op.field_id).node
        node.setdefault("options", []).append(op.value)
        return op.field_id

    if op.type is OpType.REMOVE_OPTION:
        node = index.get_field(op.field_id).node
        opts = node.get("options", [])
        node["options"] = [o for o in opts if o.get("value") != op.option_value]
        return op.field_id

    if op.type is OpType.ADD_FIELD:
        section = _find_section(form, op.section_id)
        section.setdefault("fields", []).append(op.value)
        return op.field_id

    if op.type is OpType.REMOVE_FIELD:
        _remove_field_everywhere(form, op.field_id)
        return op.field_id

    return ""


def _find_section(form: dict, section_id: str) -> dict | None:
    for section in form.get("sections", []):
        if isinstance(section, dict) and section.get("id") == section_id:
            return section
    return None


def _remove_field_everywhere(form: dict, field_id: str) -> None:
    """Remove a field by id from any section.fields or subSection.fields list."""
    for section in form.get("sections", []):
        if not isinstance(section, dict):
            continue
        if isinstance(section.get("fields"), list):
            section["fields"] = [
                f for f in section["fields"]
                if not (isinstance(f, dict) and f.get("id") == field_id)
            ]
        for sub in section.get("subSections", []) or []:
            if isinstance(sub, dict) and isinstance(sub.get("fields"), list):
                sub["fields"] = [
                    f for f in sub["fields"]
                    if not (isinstance(f, dict) and f.get("id") == field_id)
                ]


# --- Diff -------------------------------------------------------------------


@dataclass
class FieldDiff:
    """Before/after snapshot for one affected field id."""

    field_id: str
    before: dict[str, Any] | None   # None => the field was added
    after: dict[str, Any] | None    # None => the field was removed


def build_diff(
    original: dict[str, Any],
    updated: dict[str, Any],
    changed_ids: list[str],
) -> list[FieldDiff]:
    """
    Produce a per-field before/after diff for the reviewer.

    Uses the two indexes so a changed id is compared by its resolved node,
    regardless of where it lives. Table ids diff the whole table node (column
    changes show up inside it).
    """
    before_index = build_index(original)
    after_index = build_index(updated)

    diffs: list[FieldDiff] = []
    for fid in dict.fromkeys(changed_ids):  # de-dupe, preserve order
        before = before_index.get_field(fid)
        after = after_index.get_field(fid)
        diffs.append(FieldDiff(
            field_id=fid,
            before=copy.deepcopy(before.node) if before else None,
            after=copy.deepcopy(after.node) if after else None,
        ))
    return diffs


def diff_to_dicts(diffs: list[FieldDiff]) -> list[dict[str, Any]]:
    """Serialize diffs for the API response `diff` field."""
    return [
        {"fieldId": d.field_id, "before": d.before, "after": d.after}
        for d in diffs
    ]
