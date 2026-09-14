"""
Compaction — full form JSON -> compact skeleton.

The corpus forms are 2,600-9,000 lines each. We never send that to the model
(plan v3 §2.3). Compaction produces a small skeleton that preserves SHAPE and
IDIOM while dropping bulk, used in two places:

  1. Few-shot exemplars (NL mode) — a 6,000-line form becomes a few hundred
     lines the model can actually learn from within budget.
  2. Edit-mode base context — the model sees the compact skeleton (ids + types
     + structure) so it can target ids correctly, while the FULL form stays
     server-side for applying the change-set.

What we KEEP (enough to reason and target ids):
  - section id + English title only
  - each field's id, type, and required flag
  - for select/radio/multiselect: the first 1-2 option VALUES as a shape hint
  - PRESENCE markers for conditional/dependency/nested features
    (visibleWhen, conditionalAutoFillWhen, dependsOn, columns, mobile/otp, ...)
    so the model knows the field participates in logic, without the full body
  - table columns compacted the same way (id + type + required)

What we DROP (bulk that adds tokens but not targeting value):
  - pa/hi locale strings (keep en only)
  - full option lists beyond the hint
  - validator bodies (keep only a count/marker)
  - placeholders, descriptions, styling, api_config bodies, boilerplate

Deterministic and pure: same input -> same output, no LLM, no FastAPI.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.form_index import COLUMN_BEARING_TYPES

#: How many option values to keep as a shape hint.
OPTION_HINT_COUNT = 2

#: Field keys that signal the field participates in conditional/dependency
#: logic. We emit a compact marker listing which of these are present, so the
#: model knows to be careful, without paying for the full bodies.
FEATURE_MARKER_KEYS = (
    "visibleWhen", "hiddenWhen", "disabledWhen",
    "dependsOn", "dependsOnAny",
    "conditionalAutoFillWhen", "autoFillWhen",
    "formula", "sourceField", "endDateField",
    "concatenateFields",
    "transliteration", "translation",
    "datasource",
    "api_config",
    "mobileNumberField", "otpField",
)


def compact_form(form: dict[str, Any]) -> dict[str, Any]:
    """Return a compact skeleton of `form`. See module docstring for policy."""
    out: dict[str, Any] = {}

    # Carry a couple of top-level structural hints (cheap, aids context).
    if isinstance(form.get("jurisdiction"), dict):
        out["jurisdiction"] = form["jurisdiction"]

    sections_out: list[dict[str, Any]] = []
    for section in form.get("sections", []):
        if not isinstance(section, dict):
            continue
        sec: dict[str, Any] = {
            "id": section.get("id"),
            "title": _en(section.get("title")),
        }
        if section.get("visibleWhen"):
            sec["hasVisibleWhen"] = True

        if isinstance(section.get("subSections"), list) and section["subSections"]:
            sec["subSections"] = [
                {
                    "id": sub.get("id"),
                    "title": _en(sub.get("title")),
                    "fields": [_compact_field(f) for f in _as_list(sub.get("fields"))],
                }
                for sub in section["subSections"]
                if isinstance(sub, dict)
            ]
        else:
            sec["fields"] = [_compact_field(f) for f in _as_list(section.get("fields"))]

        sections_out.append(sec)

    out["sections"] = sections_out
    return out


def _compact_field(fld: Any) -> dict[str, Any]:
    """Compact a single field to id/type/required + hints + feature markers."""
    if not isinstance(fld, dict):
        return {}

    c: dict[str, Any] = {
        "id": fld.get("id"),
        "type": fld.get("type"),
    }
    if fld.get("required"):
        c["required"] = True

    # Keep the English label only when short-ish; it aids the model in matching
    # an admin's natural-language target to the right id.
    label = _en(fld.get("label"))
    if label:
        c["label"] = label

    # Option shape hint (values only, first few).
    if fld.get("type") in ("select", "multiselect", "radio"):
        opts = fld.get("options")
        if isinstance(opts, list) and opts:
            hint = [o.get("value") for o in opts[:OPTION_HINT_COUNT]
                    if isinstance(o, dict)]
            c["optionsHint"] = hint
            c["optionCount"] = len(opts)
        elif fld.get("datasource"):
            c["optionsFrom"] = "datasource"

    # Validator presence (count only, not bodies).
    validators = fld.get("validators")
    if isinstance(validators, list) and validators:
        c["validatorCount"] = len(validators)

    # Table/computed_table/verification columns, compacted.
    if fld.get("type") in COLUMN_BEARING_TYPES:
        cols = fld.get("columns")
        if isinstance(cols, list):
            c["columns"] = [_compact_column(col) for col in cols
                            if isinstance(col, dict)]

    # Feature markers: which conditional/dependency features this field uses.
    features = [k for k in FEATURE_MARKER_KEYS if fld.get(k)]
    if features:
        c["features"] = features

    return c


def _compact_column(col: dict[str, Any]) -> dict[str, Any]:
    """Compact a table column to id/type/required + option hint + markers."""
    c: dict[str, Any] = {
        "id": col.get("id"),
        "type": col.get("type"),
    }
    if col.get("required"):
        c["required"] = True
    label = _en(col.get("label"))
    if label:
        c["label"] = label
    if col.get("type") in ("select", "multiselect", "radio"):
        opts = col.get("options")
        if isinstance(opts, list) and opts:
            c["optionsHint"] = [o.get("value") for o in opts[:OPTION_HINT_COUNT]
                                if isinstance(o, dict)]
            c["optionCount"] = len(opts)
        elif col.get("datasource"):
            c["optionsFrom"] = "datasource"
    features = [k for k in FEATURE_MARKER_KEYS if col.get(k)]
    if features:
        c["features"] = features
    return c


# --- Serialization / metrics ------------------------------------------------


def compact_to_prompt_text(form: dict[str, Any]) -> str:
    """
    Serialize a compact form to indented JSON text suitable for a prompt.

    Kept as compact JSON (no locale noise) so the model sees a clean, small
    structure. Callers pass this string straight into the user prompt.
    """
    return json.dumps(compact_form(form), ensure_ascii=False, indent=2)


def compaction_stats(form: dict[str, Any]) -> dict[str, int]:
    """
    Rough before/after size metrics for a form.

    Uses character count of pretty-printed JSON as a cheap proxy for token cost.
    Useful for guardrail decisions and tests, not billed token accounting.
    """
    full_text = json.dumps(form, ensure_ascii=False, indent=2)
    compact_text = compact_to_prompt_text(form)
    return {
        "full_chars": len(full_text),
        "full_lines": full_text.count("\n") + 1,
        "compact_chars": len(compact_text),
        "compact_lines": compact_text.count("\n") + 1,
    }


# --- Helpers ----------------------------------------------------------------


def _en(loc: Any) -> str:
    """Extract the English string from a LocalizedText, or ''."""
    if isinstance(loc, dict):
        en = loc.get("en")
        if isinstance(en, str):
            return en
    return ""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []
