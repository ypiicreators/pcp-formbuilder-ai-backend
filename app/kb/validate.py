"""Client-aligned validation for KB entry writes."""

from __future__ import annotations

import json
import re
from typing import Any

from app.kb.schemas import KbCreateInput, KbEntryInput, KbKind, KbUpdateInput

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def validate_key(key: str) -> list[str]:
    errors: list[str] = []
    if not key:
        errors.append("Key is required")
    elif len(key) > 80:
        errors.append("Key must be 80 characters or fewer")
    elif not KEY_PATTERN.match(key):
        errors.append(
            "Use lowercase letters, digits and . _ - (e.g. geo.districts_by_state)"
        )
    return errors


def _validate_payload(kind: KbKind, payload: dict[str, Any] | None) -> list[str]:
    if kind == KbKind.policy:
        return []
    if payload is None:
        return ["Payload is required for this entry kind"]
    if kind == KbKind.api_endpoint:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            return ['Datasource needs a non-empty "url"']
        method = payload.get("method")
        if method is not None and method not in ("GET", "POST"):
            return ['"method" must be "GET" or "POST"']
    if kind == KbKind.template:
        field_type = payload.get("type")
        if not isinstance(field_type, str) or not field_type.strip():
            return ['Field template needs a "type" (e.g. "pincode")']
    return []


def validate_create(inp: KbCreateInput) -> list[str]:
    errors = validate_key(inp.key)
    if not inp.title.strip():
        errors.append("Title is required")
    elif len(inp.title) > 120:
        errors.append("Title must be 120 characters or fewer")
    if inp.kind == KbKind.policy and len(inp.body.strip()) < 20:
        errors.append("Guidance text is required (at least 20 characters)")
    errors.extend(_validate_payload(inp.kind, inp.payload))
    return errors


def validate_update(inp: KbUpdateInput) -> list[str]:
    errors: list[str] = []
    if not inp.title.strip():
        errors.append("Title is required")
    elif len(inp.title) > 120:
        errors.append("Title must be 120 characters or fewer")
    if inp.kind == KbKind.policy and len(inp.body.strip()) < 20:
        errors.append("Guidance text is required (at least 20 characters)")
    errors.extend(_validate_payload(inp.kind, inp.payload))
    return errors


def same_content(
    stored: dict[str, Any],
    inp: KbEntryInput | KbUpdateInput,
) -> bool:
    """Whether an update would be a no-op (no new version)."""
    return (
        stored["kind"] == inp.kind.value
        and stored["title"] == inp.title
        and stored["body"] == inp.body
        and json.dumps(stored["appliesTo"], sort_keys=True)
        == json.dumps(inp.appliesTo.model_dump(), sort_keys=True)
        and json.dumps(stored.get("payload"), sort_keys=True)
        == json.dumps(inp.payload, sort_keys=True)
    )