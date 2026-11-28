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
    if kind == KbKind.recommendation:
        return _validate_recommendation_payload(payload)
    if kind == KbKind.bundle:
        return _validate_bundle_payload(payload)
    return []


def _validate_recommendation_payload(payload: dict[str, Any]) -> list[str]:
    """Flat follow-up shape: missingFieldId, question, insertTemplates."""
    errors: list[str] = []
    missing = payload.get("missingFieldId")
    nested_when = payload.get("when") if isinstance(payload.get("when"), dict) else {}
    if not (isinstance(missing, str) and missing.strip()) and not nested_when.get(
        "missingFieldId"
    ):
        errors.append('Follow-up needs "missingFieldId" (the field id to look for)')
    question = payload.get("question")
    follow = payload.get("followUp") if isinstance(payload.get("followUp"), dict) else {}
    if not _has_question_text(question) and not _has_question_text(follow.get("question")):
        errors.append('Follow-up needs a "question" (plain text)')
    templates = payload.get("insertTemplates")
    if templates is None:
        # Legacy nested onChoice.bundleKey is still accepted.
        on_choice = follow.get("onChoice") if isinstance(follow.get("onChoice"), dict) else {}
        yes = on_choice.get("yes") if isinstance(on_choice.get("yes"), dict) else {}
        if not str(yes.get("bundleKey") or "").strip():
            errors.append(
                'Follow-up needs "insertTemplates" (list of field-template keys to add on Yes)'
            )
    elif not isinstance(templates, list) or not all(
        isinstance(t, str) and t.strip() for t in templates
    ):
        errors.append('"insertTemplates" must be a list of template keys (e.g. std.applying_for_yourself)')
    elif not templates:
        errors.append('"insertTemplates" must list at least one field template')
    return errors


def _validate_bundle_payload(payload: dict[str, Any]) -> list[str]:
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        return ['Bundle needs a non-empty "fields" array']
    errors: list[str] = []
    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            errors.append(f"fields[{i}] must be an object")
            continue
        has_id = isinstance(field.get("id"), str) and field["id"].strip()
        has_ref = any(
            isinstance(field.get(k), str) and field[k].strip()
            for k in ("ref", "$ref")
        )
        if not has_id and not has_ref:
            errors.append(f'fields[{i}] needs "id" or "ref" (a template key)')
    return errors


def _has_question_text(question: Any) -> bool:
    if isinstance(question, str) and question.strip():
        return True
    if isinstance(question, dict) and str(question.get("en") or "").strip():
        return True
    return False


def validate_create(inp: KbCreateInput) -> list[str]:
    errors = validate_key(inp.key)
    if not inp.title.strip():
        errors.append("Title is required")
    elif len(inp.title) > 120:
        errors.append("Title must be 120 characters or fewer")
    if inp.kind in (KbKind.policy, KbKind.recommendation, KbKind.bundle) and len(
        inp.body.strip()
    ) < 20:
        errors.append("Guidance text is required (at least 20 characters)")
    errors.extend(_validate_payload(inp.kind, inp.payload))
    return errors


def validate_update(inp: KbUpdateInput) -> list[str]:
    errors: list[str] = []
    if not inp.title.strip():
        errors.append("Title is required")
    elif len(inp.title) > 120:
        errors.append("Title must be 120 characters or fewer")
    if inp.kind in (KbKind.policy, KbKind.recommendation, KbKind.bundle) and len(
        inp.body.strip()
    ) < 20:
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