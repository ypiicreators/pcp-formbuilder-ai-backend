"""Normalize officer action form fields into ActionUiSchema JSON.

Matches Additional Fields Form Builder (flat array stored on the role).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.prompts.workflow_constants import WORKFLOW_FIELD_TYPES

_ALLOWED = set(WORKFLOW_FIELD_TYPES)
_TYPE_ALIASES = {
    "dropdown": "select",
    "select": "select",
    "multi select": "multiselect",
    "multiselect": "multiselect",
    "radio group": "radio",
    "radio": "radio",
    "text input": "text",
    "text field": "text",
    "text": "text",
    "email field": "email",
    "email": "email",
    "number": "number",
    "text area": "textarea",
    "textarea": "textarea",
    "checkbox": "checkbox",
    "file": "file",
    "upload": "file",
    "date": "date",
    "phone": "phone",
}

_PLACEHOLDERS = {
    "text": "Enter text",
    "email": "Enter email",
    "number": "Enter number",
    "textarea": "Enter text",
    "select": "Select",
    "multiselect": "Select",
    "radio": "Select",
    "phone": "Enter phone",
    "url": "Enter URL",
    "password": "Enter password",
    "date": "Select date",
    "time": "Select time",
    "file": "Upload file",
}

_MENTION_ORDER = [
    ("multi select", "multiselect"),
    ("multiselect", "multiselect"),
    ("text area", "textarea"),
    ("textarea", "textarea"),
    ("text field", "text"),
    ("text input", "text"),
    ("email field", "email"),
    ("radio group", "radio"),
    ("dropdown", "select"),
]


def _as_i18n(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        en = str(value.get("en") or value.get("En") or fallback or "")
        pa = str(value.get("pa") or value.get("Pa") or "")
        return {"en": en, "pa": pa}
    if isinstance(value, str) and value.strip():
        return {"en": value.strip(), "pa": ""}
    return {"en": fallback, "pa": ""}


def _field_id(ftype: str, existing: set[str], raw_id: str | None = None) -> str:
    if raw_id:
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_id).strip("_")
        if slug and slug not in existing:
            return slug
    stamp = int(time.time() * 1000)
    candidate = f"{ftype}_{stamp}"
    n = 0
    while candidate in existing:
        n += 1
        candidate = f"{ftype}_{stamp}_{n}"
    return candidate


def _normalize_type(raw: Any) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower().replace("-", " ")
    mapped = _TYPE_ALIASES.get(key, key.replace(" ", "_"))
    if mapped == "input":
        mapped = "text"
    if mapped == "upload":
        mapped = "file"
    return mapped if mapped in _ALLOWED else None


def _email_validators(existing: Any) -> list[dict[str, Any]]:
    if isinstance(existing, list) and existing:
        return existing
    return [{"type": "email", "message": "Invalid email format"}]


def normalize_form_fields(raw: Any) -> list[dict[str, Any]]:
    """Return a flat list of form-builder fields, or []."""
    if raw is None or raw is False:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return fields_from_mentions(text)
    if isinstance(raw, dict):
        if isinstance(raw.get("sections"), list):
            fields: list[Any] = []
            for section in raw["sections"]:
                fields.extend(section.get("fields") or [])
            raw = fields
        elif raw.get("type") or raw.get("id"):
            raw = [raw]
        else:
            return []
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        ftype = _normalize_type(item.get("type"))
        if not ftype:
            continue
        label = _as_i18n(item.get("label"), fallback=f"{ftype} Field")
        if not label["en"]:
            label["en"] = f"{ftype} Field"
        placeholder = _as_i18n(
            item.get("placeholder"), fallback=_PLACEHOLDERS.get(ftype, "")
        )
        fid = _field_id(ftype, seen_ids, item.get("id") if isinstance(item.get("id"), str) else None)
        seen_ids.add(fid)
        field: dict[str, Any] = {
            "id": fid,
            "type": ftype,
            "label": label,
            "required": bool(item.get("required", False)),
            "placeholder": placeholder,
            "validators": item.get("validators") if isinstance(item.get("validators"), list) else [],
        }
        if ftype == "email":
            field["validators"] = _email_validators(item.get("validators"))
        options = item.get("options")
        if ftype in {"select", "multiselect", "radio"}:
            if isinstance(options, list) and options:
                field["options"] = options
            else:
                field["options"] = [
                    {"value": "option_1", "label": {"en": "Option 1", "pa": ""}},
                    {"value": "option_2", "label": {"en": "Option 2", "pa": ""}},
                ]
        reserved = {
            "id",
            "type",
            "label",
            "required",
            "placeholder",
            "validators",
            "options",
        }
        for key, value in item.items():
            if key not in reserved and value is not None:
                field[key] = value
        out.append(field)
    return out


def fields_to_schema_json(raw: Any) -> str | None:
    fields = normalize_form_fields(raw)
    if not fields:
        return None
    return json.dumps(fields, ensure_ascii=False)


def extract_json_fields(text: str) -> Any | None:
    """If the user pasted a form-builder JSON array/object, return it."""
    s = (text or "").strip()
    start_arr, end_arr = s.find("["), s.rfind("]")
    start_obj, end_obj = s.find("{"), s.rfind("}")
    candidates: list[str] = []
    if start_arr >= 0 and end_arr > start_arr:
        candidates.append(s[start_arr : end_arr + 1])
    if start_obj >= 0 and end_obj > start_obj:
        candidates.append(s[start_obj : end_obj + 1])
    for blob in candidates:
        try:
            parsed = json.loads(blob)
        except (TypeError, ValueError):
            continue
        fields = normalize_form_fields(parsed)
        if fields:
            return fields
    return None


def fields_from_mentions(text: str) -> list[dict[str, Any]]:
    """Build simple fields when the user names types (text, email, …)."""
    lower = (text or "").lower()
    if not re.search(
        r"form field|custom action|action form|additional field|text field|email field",
        lower,
    ) and not re.search(
        r"\b(text|email|number|textarea|dropdown|checkbox|radio|file|date)\b.+\bfield",
        lower,
    ):
        # still allow "add text and email fields"
        if not re.search(r"\bfields?\b", lower):
            return []

    found: list[str] = []
    consumed = lower
    for phrase, ftype in _MENTION_ORDER:
        if phrase in consumed:
            found.append(ftype)
            consumed = consumed.replace(phrase, " ", 1)
    for token in (
        "email",
        "number",
        "textarea",
        "dropdown",
        "select",
        "checkbox",
        "radio",
        "file",
        "date",
        "phone",
        "text",
    ):
        if re.search(rf"\b{re.escape(token)}\b", consumed):
            mapped = _normalize_type(token)
            if mapped and mapped not in found:
                found.append(mapped)

    return normalize_form_fields([{"type": t} for t in found])


FORM_PRESETS: dict[int, list[dict[str, Any]]] = {
    1: [{"type": "text"}],
    2: [{"type": "email"}],
    3: [{"type": "text"}, {"type": "email"}],
    4: [{"type": "textarea", "label": {"en": "Remarks", "pa": ""}}],
    5: [
        {"type": "text"},
        {"type": "email"},
        {"type": "number"},
    ],
}

FORM_PRESET_OPTIONS = [
    (1, "Text field"),
    (2, "Email field"),
    (3, "Text + Email"),
    (4, "Remarks (textarea)"),
    (5, "Text + Email + Number"),
]


def schema_from_preset(preset_id: int | None) -> str | None:
    if preset_id is None:
        return None
    return fields_to_schema_json(FORM_PRESETS.get(int(preset_id)))


_FORM_FIELDS_PROMPT = """You convert an officer's action-form request into a flat JSON array
for the Workflow Additional Fields Form Builder. Output JSON only. No markdown.

Shape: [ { "id", "type", "label": {"en","pa"}, "required", "placeholder": {"en","pa"}, "validators": [] }, ... ]

Rules:
- Use types: text, email, number, textarea, select, multiselect, radio, checkbox, date, time, file, phone, url.
- Email fields include validators: [{"type":"email","message":"Invalid email format"}].
- select/radio/multiselect need options: [{"value":"...","label":{"en":"...","pa":""}}].
- Conditions use maps, not operator objects:
  visibleWhen: { "other_field_id": ["value1"] }
  hiddenWhen / disabledWhen: same shape
  clearOnHide: true when hiding should clear the value
- Keep ids unique, lowercase with underscores or type_timestamp.
- If the user pasted JSON, return that array (normalized), including any condition keys.
"""


def schema_from_user_spec(text: str) -> str | None:
    pasted = extract_json_fields(text)
    if pasted:
        return fields_to_schema_json(pasted)
    mentioned = fields_from_mentions(text)
    if mentioned:
        return fields_to_schema_json(mentioned)
    return None


async def interpret_form_fields(text: str) -> str | None:
    direct = schema_from_user_spec(text)
    if direct:
        return direct
    if not (text or "").strip():
        return None
    try:
        from app.providers.factory import get_provider

        provider = get_provider()
        raw = await provider.generate(
            _FORM_FIELDS_PROMPT,
            f"User request:\n{text.strip()}\n",
        )
        return schema_from_user_spec(raw) or fields_to_schema_json(raw)
    except Exception:
        return None
