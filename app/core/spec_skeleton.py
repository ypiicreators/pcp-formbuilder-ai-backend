"""
Deterministic Form Builder skeleton from a parsed numbered field spec (0 LLM tokens).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable

from app.core.doc_spec_parser import SpecCrossFieldValidator, SpecSection
from app.prompts.constants import ALLOWED_FIELD_TYPES

_ALLOWED = {t.lower(): t for t in ALLOWED_FIELD_TYPES}
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_EN_RE = re.compile(r"\s*\(english\)\s*", re.IGNORECASE)
_PA_RE = re.compile(r"\s*\((?:punjabi|ਪੰਜਾਬੀ)\)\s*", re.IGNORECASE)
_TYPES_NEEDING_OPTIONS = frozenset(
    {"select", "radio", "multiselect", "applying_for"}
)
_EMPTY_DEP = frozenset({"—", "-", "–", "", "n/a", "na", "none"})


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = _SLUG_RE.sub("_", text)
    return text.strip("_") or "field"


def normalize_field_type(raw: str) -> str:
    key = (raw or "text").strip().lower()
    if key in _ALLOWED:
        return _ALLOWED[key]
    aliases = {
        "mobile": "phone",
        "mobile_number": "phone",
        "dropdown": "select",
        "textbox": "text",
        "text box": "text",
        "calender": "date",
        "calendar": "date",
    }
    return aliases.get(key, _ALLOWED.get(key, "text"))


def parse_mandatory_required(raw: str) -> bool | None:
    """Map Mandatory? / Required column text to required=true/false (None = omit)."""
    low = (raw or "").strip().lower()
    if not low or low in _EMPTY_DEP:
        return None
    if "non" in low and "mand" in low:
        return False
    if "optional" in low:
        return False
    if "not mandatory" in low or low == "no":
        return False
    if "mandatory" in low or "required" in low or low in ("yes", "y", "true"):
        return True
    return None


def _label_base_for_pairing(name: str) -> str | None:
    low = name.lower()
    if "(english)" in low or "(punjabi)" in low or "(ਪੰਜਾਬੀ)" in name:
        base = _EN_RE.sub(" ", name)
        base = _PA_RE.sub(" ", base)
        base = " ".join(base.split()).strip().lower()
        return base or None
    return None


def _is_punjabi_row(name: str) -> bool:
    low = name.lower()
    return "(punjabi)" in low or "(ਪੰਜਾਬੀ)" in name


def _is_english_row(name: str) -> bool:
    return "(english)" in name.lower()


def _cross_field_form_id(raw_id: str, used_ids: set[str]) -> str:
    candidate = raw_id.strip()
    if not candidate:
        candidate = "cross_field_rule"
    elif not re.match(r"^[a-zA-Z0-9_-]+$", candidate):
        candidate = slugify(candidate) or "cross_field_rule"
    return _unique_id(candidate, used_ids)


def build_cross_field_validators(
    rows: list[SpecCrossFieldValidator],
    used_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        fid = _cross_field_form_id(row.validator_id, used_ids)
        vtype = (row.validator_type or "custom").strip().lower()
        if vtype != "custom":
            vtype = "custom"
        msg = (row.error_message or "").strip()
        err: str | dict[str, str]
        if msg:
            err = {"en": msg}
        else:
            err = {"en": "Validation failed."}
        out.append(
            {
                "id": fid,
                "type": vtype,
                "expression": row.expression.strip(),
                "errorMessage": err,
            }
        )
    return out


def build_spec_skeleton(
    sections: list[SpecSection],
    *,
    service_name: str | None = None,
    cross_field_validators: list[SpecCrossFieldValidator] | None = None,
    identity: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    """
    Returns (form, dependency_manifest, warnings).

    dependency_manifest entries: sectionId, fieldId, dependency (for enrich prompt).

    `identity(label, type)` may return a KB template payload ({"id", "type", "options"})
    that pins a field's canonical id/type, so the model, dependency text and validators
    all work with the KB id from the start.
    """
    used_ids: set[str] = set()
    warnings: list[str] = []
    manifest: list[dict[str, str]] = []
    out_sections: list[dict[str, Any]] = []

    for sec in sections:
        sec_id = _unique_id(slugify(sec.title), used_ids)
        fields_out: list[dict[str, Any]] = []
        english_ids_by_base: dict[str, str] = {}
        pending_punjabi: list[tuple[str, dict[str, Any]]] = []

        for row in sec.fields:
            ftype = normalize_field_type(row.field_type)
            pinned = identity(row.name, ftype) if identity else None
            if pinned:
                wanted = str(pinned["id"]).strip()
                fid = _unique_id(wanted, used_ids)
                ftype = normalize_field_type(str(pinned.get("type") or ftype))
                note = f"KB identity: '{row.name}' -> id '{fid}', type '{ftype}'."
                if fid != wanted:
                    note += f" (id '{wanted}' was already taken)"
                warnings.append(note)
            else:
                fid = _unique_id(slugify(row.name), used_ids)
            if ftype == "text" and row.field_type.strip().lower() not in _ALLOWED:
                warnings.append(
                    f"Mapped unknown type '{row.field_type}' on '{row.name}' to text."
                )

            field: dict[str, Any] = {
                "id": fid,
                "type": ftype,
                "label": {"en": row.name.strip()},
            }

            required = parse_mandatory_required(row.mandatory)
            if required is True:
                field["required"] = True
            elif required is False:
                field["required"] = False

            if pinned and isinstance(pinned.get("options"), list) and pinned["options"]:
                field["options"] = copy.deepcopy(pinned["options"])
            elif ftype in _TYPES_NEEDING_OPTIONS:
                field["options"] = _default_options_for_field(row.name, ftype)

            fields_out.append(field)

            pair_base = _label_base_for_pairing(row.name)
            if pair_base and _is_english_row(row.name):
                english_ids_by_base[pair_base] = fid
            elif pair_base and _is_punjabi_row(row.name):
                pending_punjabi.append((pair_base, field))

            dep = (row.dependency or "").strip()
            if dep and dep.lower() not in {d.lower() for d in _EMPTY_DEP}:
                manifest.append(
                    {
                        "sectionId": sec_id,
                        "fieldId": fid,
                        "dependency": dep,
                    }
                )

        for base, pa_field in pending_punjabi:
            en_id = english_ids_by_base.get(base)
            if en_id:
                pa_field["transliteration"] = True
                pa_field["transliterationField"] = en_id
            else:
                warnings.append(
                    f"No English pair found for Punjabi field '{pa_field.get('label', {}).get('en', '')}'."
                )

        out_sections.append(
            {
                "id": sec_id,
                "title": {"en": sec.title.strip()},
                "fields": fields_out,
            }
        )

    form: dict[str, Any] = {"sections": out_sections}
    if service_name:
        form["metadata"] = {"serviceName": service_name}
    if cross_field_validators:
        cfv = build_cross_field_validators(cross_field_validators, used_ids)
        if cfv:
            form["crossFieldValidators"] = cfv
    return form, manifest, warnings


def _default_options_for_field(label: str, ftype: str) -> list[dict]:
    """Minimal options so L1 validation passes; enrich replaces with real values."""
    low = label.lower()
    if ftype == "applying_for" or ("yourself" in low and ftype == "radio"):
        return [
            {"value": "YES", "label": {"en": "Yes"}},
            {"value": "NO", "label": {"en": "No"}},
        ]
    if "yes" in low and "no" in low:
        return [
            {"value": "YES", "label": {"en": "Yes"}},
            {"value": "NO", "label": {"en": "No"}},
        ]
    return [{"value": "option_1", "label": {"en": "Option 1"}}]


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def dependency_manifest_json(manifest: list[dict[str, str]]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=0)


def skeleton_field_counts(spec: list[SpecSection], form: dict[str, Any]) -> list[str]:
    """Errors when section or per-section field counts diverge from the spec."""
    errors: list[str] = []
    secs = form.get("sections")
    if not isinstance(secs, list):
        return ["output must have a sections array"]
    if len(secs) != len(spec):
        errors.append(
            f"Section count mismatch: document has {len(spec)}, output has {len(secs)}."
        )
        return errors
    for i, (spec_sec, out_sec) in enumerate(zip(spec, secs, strict=False)):
        spec_n = len(spec_sec.fields)
        out_fields = out_sec.get("fields") if isinstance(out_sec, dict) else None
        out_n = len(out_fields) if isinstance(out_fields, list) else 0
        if spec_n != out_n:
            errors.append(
                f"Section '{spec_sec.title}' (#{spec_sec.number}): expected "
                f"{spec_n} fields, got {out_n}."
            )
    return errors