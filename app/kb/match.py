"""Select KB entries relevant to a user prompt."""

from __future__ import annotations

import json
import re
from typing import Any

MODE_MAP = {
    "generate": "generate",
    "generate_from_description": "generate",
    "edit": "edit",
    "edit_existing": "edit",
    "document": "document",
}

OPTION_FIELD_TYPES = frozenset({"select", "multiselect", "radio", "checkbox"})


def select_kb_entries(
    entries: list[dict[str, Any]],
    text: str,
    *,
    mode: str = "generate",
) -> list[dict[str, Any]]:
    """Return entries whose appliesTo filters match the prompt (and mode)."""
    if not entries:
        return []
    text_l = (text or "").lower()
    mode_key = MODE_MAP.get(mode, mode)

    selected: list[dict[str, Any]] = []
    for e in entries:
        applies = e.get("appliesTo") or {}
        modes = applies.get("modes") or []
        if modes and mode_key not in modes:
            continue

        keywords = [str(k).lower() for k in (applies.get("keywords") or [])]
        field_types = applies.get("fieldTypes") or []

        keyword_hit = any(k in text_l for k in keywords if k)
        field_type_hit = any(ft.lower() in text_l for ft in field_types if ft)

        if e.get("kind") == "policy":
            # Policies with only fieldTypes (no keywords) are global rules for those
            # controls — include them on every form AI call (e.g. integer option values).
            if (
                keyword_hit
                or field_type_hit
                or (not keywords and not field_types)
                or (not keywords and field_types)
            ):
                selected.append(e)
            continue

        if keyword_hit or field_type_hit or e.get("key", "").lower() in text_l:
            selected.append(e)

    return selected


_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def payload_roles(entry: dict[str, Any]) -> set[str]:
    """Parent-field roles an entry's payload depends on, e.g. {"state"} for {state}."""
    return set(_PLACEHOLDER_RE.findall(json.dumps(entry.get("payload") or {})))


def field_text(field: dict[str, Any]) -> str:
    label = field.get("label") or {}
    en = label.get("en") if isinstance(label, dict) else str(label)
    return f"{field.get('id', '')} {en or ''}".lower()


def field_matches_entry(
    field: dict[str, Any],
    entry: dict[str, Any],
    text_l: str,
) -> bool:
    if field.get("kbKey") == entry.get("key"):
        return True

    applies = entry.get("appliesTo") or {}
    field_types = applies.get("fieldTypes") or []
    ftype = field.get("type")
    if field_types and ftype not in field_types:
        return False
    if not field_types and ftype not in OPTION_FIELD_TYPES and entry.get("kind") == "api_endpoint":
        return False

    hay = field_text(field)
    keywords = [str(k).lower() for k in (applies.get("keywords") or [])]
    key_bits = entry.get("key", "").replace(".", " ").replace("_", " ").lower()

    # api_endpoint / template: bind to a specific field by id, label, or entry key —
    # not merely because the user prompt mentioned the keyword (that would attach
    # e.g. the district API to state + district when the prompt says "district").
    if entry.get("kind") in ("api_endpoint", "template"):
        # A parent role (e.g. "state" in {state}) names the field the list depends on,
        # not the field the list is for. Matching on it binds the district list to the
        # State field itself (and to "State of Origin").
        keywords = [k for k in keywords if k not in payload_roles(entry)]
        if keywords and any(k in hay for k in keywords if k):
            return True
        if key_bits and key_bits in hay:
            return True
        title = (entry.get("title") or "").lower()
        if title and title in hay:
            return True
        return False

    if any(k in hay or k in text_l for k in keywords if k):
        return True
    return key_bits in hay if key_bits else False