"""
Canonical field identity from the Knowledge Base.

A KB `template` entry whose payload carries an `id` pins a field's id (and, via the
rest of the payload, its type/options). Example payload:

    {"id": "applying_for_yourself", "type": "applying_for", "options": [...]}

Matching uses the entry's `appliesTo.keywords` against the field LABEL (and id), and
ignores `appliesTo.fieldTypes`: a document may call the control "radio" while the KB
says it is an `applying_for`.

Used by the document flow in two places:
  - skeleton build: the canonical id/type are set before the model sees the form, so
    dependency text, visibleWhen and cross-field validators all use the KB id;
  - after generation: a safety net that renames a matching field and rewrites every
    reference to the old id (covers the section-by-section fallback and repairs).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.kb.binder import _walk_fields
from app.prompts.constants import ALLOWED_FIELD_TYPES, APPLYING_FOR_FIXED_ID, APPLYING_FOR_TYPE

_FIELD_TYPES = {t.lower() for t in ALLOWED_FIELD_TYPES}

#: Keys whose string values are expressions over field ids (token-rewritten on rename).
_EXPRESSION_KEY_HINTS = ("expression", "formula", "calculation", "condition")

#: Policy bodies declare a pinned field as: `applying_for_yourself: type applying_for`.
_BODY_TYPE_LINE = re.compile(
    r"\b(?P<id>[a-z][a-z0-9_]{2,})\s*:\s*type\s+(?P<type>[a-z][a-z0-9_]*)",
    re.IGNORECASE,
)
_BODY_ID_EXACTLY = re.compile(r"id exactly\s+([a-z][a-z0-9_]*)", re.IGNORECASE)
#: Applicant identity block: `applicant_name (text, English) + applicant_name_pa (text, Punjabi)`.
_BODY_PAREN = re.compile(
    r"\b(?P<id>[a-z][a-z0-9_]{2,})\s*\(\s*(?P<type>[a-z][a-z0-9_]*)"
    r"(?:\s*,\s*(?P<lang>English|Punjabi|Gurmukhi))?\s*\)",
    re.IGNORECASE,
)
_STOP_TOKENS = frozenset({"for", "of", "the", "a", "an", "and", "or", "to", "with", "from", "by"})
_PA_MARKERS = ("punjabi", "ਪੰਜਾਬੀ")
_EN_MARKERS = ("english",)

_YES_NO = [
    {"value": "YES", "label": {"en": "Yes"}},
    {"value": "NO", "label": {"en": "No"}},
]


def identity_entries(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """
    KB entries that pin a field's canonical id.

    Sources (live KB uses both):
      - a template/policy/api whose payload has an `id`;
      - a policy body that names the field (`applying_for_yourself: type applying_for`,
        `id exactly applying_for_yourself`). The published Self vs beneficiary policy
        is this shape: payload is empty, the id lives in the body.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in entries or []:
        payload = e.get("payload")
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("id"), str)
            and payload["id"].strip()
        ):
            _append_identity(out, seen, e, payload)
            continue
        for payload in _payloads_from_body(e):
            _append_identity(out, seen, e, payload)
    return out


def _append_identity(
    out: list[dict[str, Any]],
    seen: set[str],
    entry: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    canonical = str(payload["id"]).strip()
    if not canonical or canonical in seen:
        return
    seen.add(canonical)
    merged = dict(entry)
    merged["payload"] = copy.deepcopy(payload)
    merged["payload"]["id"] = canonical
    # Drop generic policy keywords ("applying for") so they cannot pin this id
    # onto a different field (e.g. "applying for Aadhaar eKYC").
    phrase = canonical.replace("_", " ")
    existing = [str(k) for k in (entry.get("appliesTo") or {}).get("keywords") or []]
    kept = [k for k in existing if phrase in k.lower() or len(k.split()) >= 3]
    applies = dict(entry.get("appliesTo") or {})
    applies["keywords"] = list(dict.fromkeys([phrase] + kept))
    merged["appliesTo"] = applies
    out.append(merged)


def _payloads_from_body(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull `{id, type, options}` declarations out of a policy/template body."""
    body = str(entry.get("body") or "")
    if not body:
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    exact = {m.group(1) for m in _BODY_ID_EXACTLY.finditer(body)}
    for match in _BODY_TYPE_LINE.finditer(body):
        fid = match.group("id")
        ftype = match.group("type").lower()
        # Colon form is used for the applying_for singleton; skip other `id: type`
        # mentions unless the body also says `id exactly <id>`.
        if ftype != APPLYING_FOR_TYPE and fid not in exact:
            continue
        _take_body_payload(found, seen, fid, ftype)

    for match in _BODY_PAREN.finditer(body):
        fid = match.group("id")
        ftype = match.group("type").lower()
        # "applicant (autofill)" in running text is not a field declaration.
        # Real KB ids are snake_case (`applicant_name`) and the paren is a FieldType.
        if "_" not in fid or ftype not in _FIELD_TYPES:
            continue
        _take_body_payload(
            found,
            seen,
            fid,
            ftype,
            lang=(match.group("lang") or "").lower() or None,
        )
    # `id exactly applying_for_yourself` with type named elsewhere in the body.
    if APPLYING_FOR_FIXED_ID not in seen and APPLYING_FOR_FIXED_ID in body:
        if APPLYING_FOR_TYPE in body.lower() or APPLYING_FOR_TYPE in (
            (entry.get("appliesTo") or {}).get("fieldTypes") or []
        ):
            _take_body_payload(found, seen, APPLYING_FOR_FIXED_ID, APPLYING_FOR_TYPE)
    return found


def _take_body_payload(
    found: list[dict[str, Any]],
    seen: set[str],
    fid: str,
    ftype: str,
    lang: str | None = None,
) -> None:
    if not fid or fid in seen:
        return
    seen.add(fid)
    payload: dict[str, Any] = {"id": fid, "type": ftype}
    if ftype == APPLYING_FOR_TYPE:
        payload["options"] = copy.deepcopy(_YES_NO)
    if lang:
        payload["_lang"] = lang
    elif fid.endswith("_pa"):
        payload["_lang"] = "punjabi"
    found.append(payload)


def _hay(label: str, field_id: str = "") -> str:
    return f"{label} {field_id.replace('_', ' ')}".lower()


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in payload.items() if not str(k).startswith("_")}


def _label_lang(hay: str) -> str | None:
    if any(m in hay for m in _PA_MARKERS):
        return "pa"
    if any(m in hay for m in _EN_MARKERS):
        return "en"
    return None


def _stem_tokens(canonical: str) -> list[str]:
    stem = re.sub(r"_(pa|en)$", "", canonical)
    return [t for t in stem.split("_") if t and t not in _STOP_TOKENS]


def _tokens_in_order(tokens: list[str], hay: str) -> bool:
    pos = 0
    for token in tokens:
        match = re.search(rf"(^|[^a-z0-9]){re.escape(token)}(?![a-z0-9])", hay[pos:])
        if not match:
            return False
        pos += match.end()
    return True


def _lang_ok(canonical: str, hay: str, payload: dict[str, Any], all_ids: set[str]) -> bool:
    """Keep English/Punjabi name pairs from claiming each other's rows."""
    label_lang = _label_lang(hay)
    lang = str(payload.get("_lang") or "")
    is_pa = canonical.endswith("_pa") or lang in ("punjabi", "gurmukhi")
    is_en = lang == "english" or f"{re.sub(r'_pa$', '', canonical)}_pa" in all_ids and not is_pa
    if is_pa:
        return label_lang != "en"
    if is_en:
        return label_lang != "pa"
    return True


def _matches(entry: dict[str, Any], hay: str, all_ids: set[str] | None = None) -> bool:
    """
    Match the field label/id to a KB identity.

    Exact phrase (`applying for yourself`) and slug suffix first. Document labels
    often insert extra words (`Applicant Full Name (English)` vs `applicant_name`),
    so we also accept the id's stem tokens in order, with English/Punjabi pairing.
    """
    all_ids = all_ids or set()
    payload = entry.get("payload") or {}
    canonical = str(payload.get("id") or "").strip()
    if canonical and not _lang_ok(canonical, hay, payload, all_ids):
        return False
    if canonical:
        phrase = canonical.replace("_", " ").lower()
        if phrase and phrase in hay:
            return True
        slug = re.sub(r"[^a-z0-9]+", "_", hay).strip("_")
        if slug == canonical or slug.endswith("_" + canonical):
            return True
        tokens = _stem_tokens(canonical)
        if len(tokens) >= 2 and _tokens_in_order(tokens, hay):
            return True
    keywords = [str(k).lower() for k in (entry.get("appliesTo") or {}).get("keywords") or []]
    return any(k and " " in k and k in hay for k in keywords)


def resolve_identity(
    entries: list[dict[str, Any]] | None,
    label: str,
    field_type: str | None = None,  # accepted for symmetry; identity ignores the source type
) -> dict[str, Any] | None:
    """Canonical id/type pinned by the KB for a field with this label, if any."""
    identities = identity_entries(entries)
    all_ids = {str(e["payload"]["id"]) for e in identities}
    hay = _hay(label)
    for entry in identities:
        if _matches(entry, hay, all_ids):
            return _public_payload(entry["payload"])
    return None


def form_field_ids(form: dict[str, Any] | None) -> set[str]:
    if not isinstance(form, dict):
        return set()
    return {str(f.get("id")) for f in _walk_fields(form) if f.get("id")}


def _token_pattern(field_id: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(field_id)}(?![A-Za-z0-9_])")


def rename_field_id(form: Any, old: str, new: str) -> Any:
    """
    Rename a field id everywhere it is referenced and return the rewritten structure:
    the field's own `id`, dict keys (e.g. visibleWhen), exact string values (dependsOn,
    transliterationField, ...) and identifiers inside expression strings. Human text
    (labels, descriptions) is never touched.
    """
    token = _token_pattern(old)

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            return {(new if k == old else k): walk(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key) for v in node]
        if isinstance(node, str):
            if node == old:
                return new
            if any(h in key.lower() for h in _EXPRESSION_KEY_HINTS):
                return token.sub(new, node)
        return node

    return walk(form)


def apply_kb_field_ids(
    form: dict[str, Any],
    entries: list[dict[str, Any]] | None,
    *,
    reserved_ids: set[str] | frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[str]]:
    """
    Give every field that matches a KB identity template its canonical id.
    `reserved_ids` are ids already taken elsewhere (e.g. fields already on the canvas).
    Returns (form, warnings). Never creates a duplicate id: a conflicting rename is skipped.
    """
    templates = identity_entries(entries)
    if not templates:
        return form, []

    all_ids = {str(e["payload"]["id"]) for e in templates}
    warnings: list[str] = []
    for entry in templates:
        canonical = entry["payload"]["id"].strip()
        for field in list(_walk_fields(form)):
            current = str(field.get("id", ""))
            if current == canonical:
                _merge_payload(field, entry)  # id already correct; enforce type/options
                break
            label = field.get("label")
            en = label.get("en") if isinstance(label, dict) else str(label or "")
            if not _matches(entry, _hay(en or "", current), all_ids):
                continue
            taken = form_field_ids(form) | set(reserved_ids)
            if canonical in taken:
                warnings.append(
                    f'KB identity "{entry["key"]}": field "{current}" should be "{canonical}", '
                    "but that id is already used; left unchanged."
                )
                break
            form = rename_field_id(form, current, canonical)
            renamed = next(
                (f for f in _walk_fields(form) if f.get("id") == canonical), None
            )
            if renamed is not None:
                _merge_payload(renamed, entry)
            warnings.append(
                f'KB identity "{entry["key"]}": renamed field "{current}" to "{canonical}".'
            )
            break  # one field per canonical id
    return form, warnings


def _merge_payload(field: dict[str, Any], entry: dict[str, Any]) -> None:
    """Apply the template's type/options/etc. (never the id/label) to the field."""
    for k, v in entry["payload"].items():
        if k not in ("id", "label") and not str(k).startswith("_"):
            field[k] = copy.deepcopy(v)
    field["kbKey"] = entry["key"]
