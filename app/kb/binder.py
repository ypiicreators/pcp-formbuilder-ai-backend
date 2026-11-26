"""Apply live KB api_endpoint / template entries onto a generated form."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from app.kb.match import OPTION_FIELD_TYPES, field_matches_entry

PLACEHOLDER_RE = re.compile(r"^\{([a-z_]+)\}$")


def _walk_fields(form: dict[str, Any]):
    for section in form.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for field in section.get("fields") or []:
            if isinstance(field, dict):
                yield field
        for sub in section.get("subSections") or []:
            if not isinstance(sub, dict):
                continue
            for field in sub.get("fields") or []:
                if isinstance(field, dict):
                    yield field


#: Placeholder role -> substrings that identify that parent field by id/label.
#: Production ids contain typos (e.g. "muncipal_coorperation"), so list both.
ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "state": ("state",),
    "district": ("district",),
    "tehsil": ("tehsil",),
    "block": ("block",),
    "municipality": ("muncipal", "municipal"),
    "village": ("village",),
}


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _find_role_field(
    form: dict[str, Any], role: str, target_id: str = ""
) -> str | None:
    """Parent select for `role`; prefers the one sharing an id prefix with the target.

    e.g. `cor_tehsil` resolves {district} to `cor_district`, not the permanent
    `district`.
    """
    aliases = ROLE_ALIASES.get(role.lower(), (role.lower(),))
    target_l = target_id.lower()
    best: tuple[int, int, str] | None = None
    for field in _walk_fields(form):
        if field.get("type") not in OPTION_FIELD_TYPES:
            continue
        raw_id = str(field.get("id", ""))
        fid = raw_id.lower()
        if fid == target_l:
            continue
        label_l = str((field.get("label") or {}).get("en", "")).lower()
        if not any(a in fid or a in label_l for a in aliases):
            continue
        score = (_common_prefix_len(fid, target_l), -len(fid), raw_id)
        if best is None or score[:2] > best[:2]:
            best = score
    return best[2] if best else None


def _resolve_placeholders(obj: Any, roles: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, roles) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_placeholders(v, roles) for v in obj]
    if isinstance(obj, str):
        m = PLACEHOLDER_RE.match(obj.strip())
        if m:
            role = m.group(1)
            if role in roles:
                return roles[role]
        return obj
    return obj


def _roles_for_entry(
    entry: dict[str, Any], form: dict[str, Any], target_id: str = ""
) -> dict[str, str]:
    roles: dict[str, str] = {}
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return roles

    blob = json.dumps(payload)

    for role in ROLE_ALIASES:
        if f"{{{role}}}" in blob:
            fid = _find_role_field(form, role, target_id)
            if fid:
                roles[role] = fid
    return roles


def bind_kb_to_form(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
    user_text: str,
) -> tuple[dict[str, Any], list[str]]:
    """Merge templates and datasources from KB; returns (form, warnings)."""
    if not entries:
        return form, []

    text_l = (user_text or "").lower()
    warnings: list[str] = []
    apis = [e for e in entries if e.get("kind") == "api_endpoint" and e.get("payload")]
    templates = [e for e in entries if e.get("kind") == "template" and e.get("payload")]

    for field in _walk_fields(form):
        ftype = field.get("type")
        for entry in apis:
            if not field_matches_entry(field, entry, text_l):
                continue
            roles = _roles_for_entry(entry, form, str(field.get("id", "")))
            ds = copy.deepcopy(entry["payload"])
            ds = _resolve_placeholders(ds, roles)
            field["datasource"] = ds
            field.pop("options", None)
            field["kbKey"] = entry["key"]
            if roles and ftype in OPTION_FIELD_TYPES:
                # Cascade: this field reloads/resets when any resolved parent changes.
                field["dependsOnAny"] = list(dict.fromkeys(roles.values()))
            warnings.append(f'KB bound datasource "{entry["key"]}" onto field "{field.get("id")}".')
            break

        for entry in templates:
            if not field_matches_entry(field, entry, text_l):
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if k not in ("id", "label"):
                        field[k] = copy.deepcopy(v)
                field["kbKey"] = entry["key"]
                warnings.append(f'KB merged template "{entry["key"]}" onto field "{field.get("id")}".')
            break

    return form, warnings