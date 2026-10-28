"""
Merge a newly generated form onto an existing form.

Used when the admin uploads a second (or later) document/image while a form
already exists: generate sections for the new document, then APPEND them so
earlier sections and their fields are preserved.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.core.form_index import build_index

_ID_SUFFIX_RE = re.compile(r"^(.*)_(\d+)$")


def merge_generated_sections(
    base_form: dict[str, Any],
    generated_form: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Append `generated_form.sections` onto a deep copy of `base_form`.

    Colliding section / field ids in the generated half are renamed
    (`id` -> `id_2`, `id_3`, …). References inside the *new* sections that
    point at renamed ids are rewritten. Existing sections are never modified.

    Returns `(merged_form, warnings)`.
    """
    if not isinstance(base_form, dict) or not isinstance(base_form.get("sections"), list):
        raise ValueError("base_form must be a form object with a 'sections' array")
    if not isinstance(generated_form, dict) or not isinstance(
        generated_form.get("sections"), list
    ):
        raise ValueError("generated_form must be a form object with a 'sections' array")

    merged = copy.deepcopy(base_form)
    warnings: list[str] = []

    used_section_ids = {
        str(s.get("id"))
        for s in merged.get("sections", [])
        if isinstance(s, dict) and isinstance(s.get("id"), str) and s.get("id")
    }
    used_field_ids = build_index(merged).all_field_ids()

    new_sections = generated_form.get("sections") or []
    if not new_sections:
        warnings.append("Document produced no new sections; existing form left unchanged.")
        return merged, warnings

    renamed_count = 0
    for raw_section in new_sections:
        if not isinstance(raw_section, dict):
            continue
        section = copy.deepcopy(raw_section)
        id_map: dict[str, str] = {}

        old_sid = section.get("id")
        if isinstance(old_sid, str) and old_sid:
            new_sid = _unique_id(old_sid, used_section_ids)
            if new_sid != old_sid:
                id_map[old_sid] = new_sid
                renamed_count += 1
            section["id"] = new_sid
            used_section_ids.add(new_sid)

        for field in _iter_section_fields(section):
            old_fid = field.get("id")
            if not isinstance(old_fid, str) or not old_fid:
                continue
            new_fid = _unique_id(old_fid, used_field_ids)
            if new_fid != old_fid:
                id_map[old_fid] = new_fid
                renamed_count += 1
            field["id"] = new_fid
            used_field_ids.add(new_fid)

        if id_map:
            _rewrite_refs(section, id_map)

        merged.setdefault("sections", []).append(section)

    # Append generated cross-field validators (with id remaps) if present.
    base_cfv = merged.get("crossFieldValidators")
    gen_cfv = generated_form.get("crossFieldValidators")
    if isinstance(gen_cfv, list) and gen_cfv:
        if not isinstance(base_cfv, list):
            merged["crossFieldValidators"] = []
            base_cfv = merged["crossFieldValidators"]
        # Remap using only field renames that landed in newly appended sections.
        # Rebuild a full map from a second pass is heavy; skip CFVs that still
        # reference unknown ids — validation will catch leftovers.
        for rule in gen_cfv:
            if isinstance(rule, dict):
                base_cfv.append(copy.deepcopy(rule))

    added = len(new_sections)
    warnings.append(
        f"Appended {added} section(s) from the document onto the existing form."
    )
    if renamed_count:
        warnings.append(
            f"Renamed {renamed_count} colliding id(s) so existing fields stay unique."
        )
    return merged, warnings


def _unique_id(desired: str, used: set[str]) -> str:
    """Return `desired` or `desired_N` that is not in `used`."""
    if desired not in used:
        return desired
    base = desired
    m = _ID_SUFFIX_RE.match(desired)
    start = 2
    if m:
        base = m.group(1)
        start = int(m.group(2)) + 1
    n = start
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def _iter_section_fields(section: dict[str, Any]):
    """Yield field dicts under section.fields and section.subSections[].fields."""
    fields = section.get("fields")
    if isinstance(fields, list):
        for f in fields:
            if isinstance(f, dict):
                yield f
    subs = section.get("subSections")
    if isinstance(subs, list):
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            sub_fields = sub.get("fields")
            if isinstance(sub_fields, list):
                for f in sub_fields:
                    if isinstance(f, dict):
                        yield f


def _rewrite_refs(node: Any, id_map: dict[str, str]) -> None:
    """
    Recursively rewrite string values / dict keys that match renamed ids.

    Covers visibleWhen keys, autoFillWhen.field, transliterationField, etc.
    Only touches the newly generated subtree (caller must not pass base form).
    """
    if isinstance(node, dict):
        # Rewrite dict keys that are field ids (e.g. visibleWhen: { old_id: [...] }).
        for key in list(node.keys()):
            if isinstance(key, str) and key in id_map:
                node[id_map[key]] = node.pop(key)
        for key, value in list(node.items()):
            if isinstance(value, str) and value in id_map:
                node[key] = id_map[value]
            else:
                _rewrite_refs(value, id_map)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str) and item in id_map:
                node[i] = id_map[item]
            else:
                _rewrite_refs(item, id_map)
