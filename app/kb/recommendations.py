"""KB follow-up questions: ask Yes/No, then insert field templates.

Published `recommendation` entries: missing field id, question, template keys.
Yes copies those `template` entries onto the form. The LLM never sees
recommendations (see `match.select_kb_entries`).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from app.core.form_index import build_index
from app.kb.binder import bind_kb_to_form, _walk_fields
from app.validation.business_rules import CONDITION_MAP_KEYS, ID_LIST_KEYS, SINGLE_ID_KEYS

#: Tokens the assist UI sends when the admin picks a follow-up choice.
#: field_id format: kbrec|<recommendation-key>|<choice-id>
CHOICE_RE = re.compile(r"kbrec\|([a-z][a-z0-9._-]*)\|([a-z0-9_-]+)", re.IGNORECASE)


def loc_en(value: Any, default: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        en = value.get("en")
        if isinstance(en, str) and en.strip():
            return en.strip()
    return default


def parse_followup_choice(
    prompt: str,
    target_field_ids: list[str] | None = None,
) -> tuple[str, str] | None:
    """Return (recommendation_key, choice_id) from chat text / selected chips."""
    parts = [prompt or ""]
    if target_field_ids:
        parts.extend(target_field_ids)
    hay = " ".join(parts)
    matches = CHOICE_RE.findall(hay)
    if not matches:
        return None
    rec_key, choice_id = matches[-1]
    rec_key = rec_key.strip()
    # Prefer "no" if both yes and no were ticked for the same recommendation.
    nos = [m for m in matches if m[0].lower() == rec_key.lower() and m[1].lower() == "no"]
    if nos:
        return nos[-1][0], nos[-1][1]
    return rec_key, choice_id


def _entries_by_kind(entries: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in entries or [] if e.get("kind") == kind]


def _entry_by_key(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for e in entries or []:
        if e.get("key") == key:
            return e
    return None


def _eval_atom(atom: dict[str, Any], form: dict[str, Any]) -> bool:
    index = build_index(form)
    ids = index.all_field_ids()

    if "missingFieldId" in atom:
        fid = str(atom["missingFieldId"])
        if fid in ids:
            return False
        # The applying-for gate may be generated with a different id but the
        # reserved type; treat that as present so we do not ask twice.
        if fid == "applying_for_yourself":
            if any(e.node.get("type") == "applying_for" for e in index.fields.values()):
                return False
    if "presentFieldId" in atom:
        if str(atom["presentFieldId"]) not in ids:
            return False
    if "fieldIdPrefix" in atom:
        prefix = str(atom["fieldIdPrefix"])
        if not any(i.startswith(prefix) for i in ids):
            return False
    if "fieldType" in atom:
        wanted = str(atom["fieldType"])
        if not any(e.node.get("type") == wanted for e in index.fields.values()):
            return False
    if "sectionIdContains" in atom:
        needle = str(atom["sectionIdContains"]).lower()
        sections = form.get("sections") or []
        if not any(
            isinstance(s, dict) and needle in str(s.get("id") or "").lower()
            for s in sections
        ):
            return False
    if "referencesFieldId" in atom:
        if not _form_references_id(form, str(atom["referencesFieldId"])):
            return False
    if atom.get("always") is True:
        return True
    # A dict that only nested allOf/anyOf/not is handled by _eval_when.
    known = {
        "missingFieldId",
        "presentFieldId",
        "fieldIdPrefix",
        "fieldType",
        "sectionIdContains",
        "referencesFieldId",
        "always",
        "allOf",
        "anyOf",
        "not",
    }
    if not (set(atom) & known):
        return False
    return True


def _eval_when(when: Any, form: dict[str, Any]) -> bool:
    if not isinstance(when, dict) or not when:
        return False
    if "allOf" in when:
        items = when["allOf"]
        if not isinstance(items, list) or not items:
            return False
        if not all(_eval_when(i, form) if isinstance(i, dict) else False for i in items):
            return False
        rest = {k: v for k, v in when.items() if k != "allOf"}
        return _eval_when(rest, form) if rest else True
    if "anyOf" in when:
        items = when["anyOf"]
        if not isinstance(items, list) or not any(
            isinstance(i, dict) and _eval_when(i, form) for i in items
        ):
            return False
        rest = {k: v for k, v in when.items() if k != "anyOf"}
        return _eval_when(rest, form) if rest else True
    if "not" in when:
        inner = when["not"]
        if isinstance(inner, dict) and _eval_when(inner, form):
            return False
        rest = {k: v for k, v in when.items() if k != "not"}
        return _eval_when(rest, form) if rest else True
    return _eval_atom(when, form)


def _form_references_id(form: dict[str, Any], fid: str) -> bool:
    for field in _walk_fields(form):
        for key in CONDITION_MAP_KEYS:
            cond = field.get(key)
            if isinstance(cond, dict) and fid in cond:
                return True
        for key in ID_LIST_KEYS:
            val = field.get(key)
            if isinstance(val, list) and fid in val:
                return True
        for key in SINGLE_ID_KEYS:
            if field.get(key) == fid:
                return True
        auto = field.get("autoFillWhen")
        if isinstance(auto, dict) and auto.get("field") == fid:
            return True
    blob = json.dumps(form.get("crossFieldValidators") or [])
    return f"'{fid}'" in blob or f'"{fid}"' in blob or f"{fid} " in blob


def matching_recommendations(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recs = _entries_by_kind(entries, "recommendation")

    def prio(e: dict[str, Any]) -> int:
        payload = e.get("payload") or {}
        try:
            return int(payload.get("priority") or 0)
        except (TypeError, ValueError):
            return 0

    out: list[dict[str, Any]] = []
    for rec in sorted(recs, key=prio, reverse=True):
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        if _eval_when(_followup_when(payload), form):
            out.append(rec)
    return out


def _followup_when(payload: dict[str, Any]) -> dict[str, Any]:
    missing = payload.get("missingFieldId")
    if isinstance(missing, str) and missing.strip():
        return {"missingFieldId": missing.strip()}
    when = payload.get("when")
    return when if isinstance(when, dict) else {}


def first_recommendation(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    matched = matching_recommendations(form, entries)
    return matched[0] if matched else None


def clarification_options(rec: dict[str, Any]) -> list[dict[str, str]]:
    payload = rec.get("payload") or {}
    follow = payload.get("followUp") if isinstance(payload.get("followUp"), dict) else {}
    choices = follow.get("choices") if isinstance(follow.get("choices"), list) else []
    rec_key = str(rec.get("key") or "")
    title = str(rec.get("title") or rec_key)
    if not choices:
        choices = [
            {"id": "yes", "label": {"en": "Yes, add the suggested fields"}},
            {"id": "no", "label": {"en": "No, keep the form as it is"}},
        ]
    options: list[dict[str, str]] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        cid = str(choice.get("id") or "").strip()
        if not cid:
            continue
        options.append(
            {
                "field_id": f"kbrec|{rec_key}|{cid}",
                "label": loc_en(choice.get("label"), cid),
                "section_path": title,
            }
        )
    return options


def clarification_question(rec: dict[str, Any]) -> str:
    payload = rec.get("payload") or {}
    follow = payload.get("followUp") if isinstance(payload.get("followUp"), dict) else {}
    return loc_en(payload.get("question")) or loc_en(
        follow.get("question"),
        "Do you want to add the suggested fields?",
    )


def _expand_bundle_field(
    raw: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    field = copy.deepcopy(raw)
    ref = field.pop("ref", None) or field.pop("$ref", None)
    if isinstance(ref, str) and ref.strip():
        tmpl = _entry_by_key(entries, ref.strip())
        payload = (tmpl or {}).get("payload") if tmpl else None
        if isinstance(payload, dict):
            merged = copy.deepcopy(payload)
            merged.update(field)
            merged["kbKey"] = ref.strip()
            field = merged
    fid = field.get("id")
    if not isinstance(fid, str) or not fid.strip():
        return None
    field["id"] = fid.strip()
    if "type" not in field:
        field["type"] = "text"
    if "label" not in field:
        field["label"] = {"en": fid.replace("_", " ").title()}
    return field


def apply_bundle(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
    bundle_key: str,
) -> tuple[dict[str, Any], list[str]]:
    """Insert bundle fields; skip ids already on the form. Bind APIs/templates after."""
    warnings: list[str] = []
    bundle = _entry_by_key(entries, bundle_key)
    if not bundle or bundle.get("kind") != "bundle":
        return form, [f'KB bundle "{bundle_key}" is not in the published knowledge base.']
    payload = bundle.get("payload")
    if not isinstance(payload, dict):
        return form, [f'KB bundle "{bundle_key}" has no payload.']
    placement = payload.get("placement") if isinstance(payload.get("placement"), dict) else {}
    return _insert_fields(
        form,
        entries,
        [f for f in (payload.get("fields") or []) if isinstance(f, dict)],
        placement,
        warnings,
    )


def _resolve_section(form: dict[str, Any], placement: dict[str, Any]) -> dict[str, Any]:
    wanted = str(placement.get("sectionId") or "").strip()
    sections = form["sections"]
    if wanted:
        for sec in sections:
            if isinstance(sec, dict) and sec.get("id") == wanted:
                return sec
    if sections and isinstance(sections[0], dict):
        return sections[0]
    title = placement.get("sectionTitle")
    new_sec = {
        "id": wanted or "applicant_details",
        "title": title if isinstance(title, dict) else {"en": loc_en(title, "Applicant Details")},
        "fields": [],
    }
    sections.insert(0, new_sec)
    return new_sec


def _insert_index(fields: list[Any], placement: dict[str, Any]) -> int:
    after = placement.get("insertAfterFieldId")
    before = placement.get("insertBeforeFieldId")
    if isinstance(after, str) and after:
        for i, f in enumerate(fields):
            if isinstance(f, dict) and f.get("id") == after:
                return i + 1
    if isinstance(before, str) and before:
        for i, f in enumerate(fields):
            if isinstance(f, dict) and f.get("id") == before:
                return i
    return len(fields)


def apply_templates(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
    template_keys: list[str],
    *,
    insert_after_field_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Insert published field templates onto the form (skip ids already present)."""
    warnings: list[str] = []
    raw_fields: list[dict[str, Any]] = []
    for key in template_keys:
        key = str(key).strip()
        if not key:
            continue
        tmpl = _entry_by_key(entries, key)
        if not tmpl or tmpl.get("kind") != "template":
            warnings.append(f'Field template "{key}" is not in the published knowledge base.')
            continue
        payload = tmpl.get("payload")
        if not isinstance(payload, dict):
            warnings.append(f'Field template "{key}" has no payload.')
            continue
        raw_fields.append({"ref": key})

    if not raw_fields:
        return form, warnings or ["No field templates to insert."]

    placement = {
        "sectionId": "whom_you_are_applying_for",
        "insertAfterFieldId": insert_after_field_id or "applicant_name_pa",
        "sectionTitle": {"en": "Applicant Details"},
    }
    return _insert_fields(form, entries, raw_fields, placement, warnings)


def _insert_fields(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
    raw_fields: list[dict[str, Any]],
    placement: dict[str, Any],
    warnings: list[str],
) -> tuple[dict[str, Any], list[str]]:
    out = copy.deepcopy(form)
    if not isinstance(out.get("sections"), list):
        out["sections"] = []

    section = _resolve_section(out, placement)
    existing = {str(f.get("id")) for f in _walk_fields(out)}

    to_insert: list[dict[str, Any]] = []
    for raw in raw_fields:
        expanded = _expand_bundle_field(raw, entries)
        if not expanded:
            continue
        if expanded["id"] in existing:
            warnings.append(f'Skipped "{expanded["id"]}" — already on the form.')
            continue
        to_insert.append(expanded)
        existing.add(expanded["id"])

    if not to_insert:
        warnings.append("No new fields were added.")
        return out, warnings

    fields = section.setdefault("fields", [])
    if not isinstance(fields, list):
        section["fields"] = to_insert
    else:
        idx = _insert_index(fields, placement)
        section["fields"] = fields[:idx] + to_insert + fields[idx:]

    bound, bind_warnings = bind_kb_to_form(out, entries, "follow-up")
    warnings.extend(bind_warnings)
    warnings.append(f"Added {len(to_insert)} field(s) from Knowledge Base templates.")
    return bound, warnings


def apply_followup_choice(
    form: dict[str, Any],
    entries: list[dict[str, Any]],
    choice: tuple[str, str],
) -> tuple[dict[str, Any], list[str], str | None]:
    """Apply Yes/No for a recommendation. Returns (form, warnings, error)."""
    rec_key, choice_id = choice
    rec = _entry_by_key(entries, rec_key)
    if not rec or rec.get("kind") != "recommendation":
        return form, [], f'Unknown Knowledge Base follow-up "{rec_key}".'

    if choice_id.lower() == "no":
        return form, ["Kept the form without the suggested Knowledge Base fields."], None

    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    templates = [
        str(t).strip()
        for t in (payload.get("insertTemplates") or [])
        if isinstance(t, str) and t.strip()
    ]
    after = payload.get("insertAfterFieldId")
    after_id = str(after).strip() if isinstance(after, str) else "applicant_name_pa"
    if templates:
        updated, warnings = apply_templates(
            form, entries, templates, insert_after_field_id=after_id or None
        )
        return updated, warnings, None

    follow = payload.get("followUp") if isinstance(payload.get("followUp"), dict) else {}
    on_choice = follow.get("onChoice") if isinstance(follow.get("onChoice"), dict) else {}
    spec = on_choice.get("yes") if isinstance(on_choice.get("yes"), dict) else {}
    bundle_key = str(spec.get("bundleKey") or "").strip()
    if bundle_key:
        updated, warnings = apply_bundle(form, entries, bundle_key)
        return updated, warnings, None
    return form, [], f'Follow-up "{rec_key}" has no insertTemplates list.'
