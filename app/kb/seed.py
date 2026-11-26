"""Initial KB data when Mongo has no releases yet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.kb.logic import snap

DEFAULT_ACTOR = "System (seed)"


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def build_seed_documents() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Return (entry_docs, versions_by_key, release_doc)."""

    def mk(
        base: dict[str, Any],
        *,
        version: int = 1,
        live_version: int | None = 1,
        updated_at: str | None = None,
        updated_by: str = DEFAULT_ACTOR,
    ) -> dict[str, Any]:
        rec = {
            **base,
            "version": version,
            "liveVersion": live_version,
            "archived": False,
            "updatedBy": updated_by,
            "updatedAt": updated_at or _days_ago(12),
        }
        return rec

    districts_v1 = mk(
        {
            "key": "geo.districts_by_state",
            "kind": "api_endpoint",
            "title": "Districts for a selected State",
            "body": "District dropdown that depends on the State field.",
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["select"],
                "keywords": ["district", "state", "geo"],
            },
            "payload": {
                "url": "api/um/GeoUnit/GetGeoUnitDList",
                "method": "POST",
                "payload": {"parentId": "{state}"},
            },
        },
        version=1,
        live_version=1,
    )

    districts_v2 = mk(
        {
            "key": "geo.districts_by_state",
            "kind": "api_endpoint",
            "title": "Districts for a selected State",
            "body": (
                "District dropdown that depends on the State field. Always set dependsOnAny "
                "to the State field id and pass its value as parentId."
            ),
            "appliesTo": districts_v1["appliesTo"],
            "payload": districts_v1["payload"],
        },
        version=2,
        live_version=1,
        updated_at=_days_ago(1),
        updated_by="Config Team",
    )

    static_lists = mk(
        {
            "key": "policy.static_lists_use_options",
            "kind": "policy",
            "title": "Fixed choice lists use static options, not an API",
            "body": (
                'When the choices are a small fixed list (for example gender or relation), '
                'define them as static "options" on the field. Never add a "datasource" for a '
                'hardcoded list. Use "datasource" only when the options really come from an API.'
            ),
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["select", "multiselect", "radio"],
                "keywords": ["gender", "relation", "options", "dropdown"],
            },
            "payload": None,
        },
    )

    applying_for = mk(
        {
            "key": "policy.applying_for_singleton",
            "kind": "policy",
            "title": 'Only one "Applying for" field, with a fixed id',
            "body": (
                'A form may contain at most one field of type applying_for. Its id must be '
                'exactly "applying_for_yourself" and its options are the fixed pair YES / NO.'
            ),
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["applying_for"],
                "keywords": ["applying for", "self"],
            },
            "payload": None,
        },
    )

    pincode = mk(
        {
            "key": "std.pincode",
            "kind": "template",
            "title": "PIN code field",
            "body": "Use the pincode type with the standard 6-digit validator.",
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["pincode"],
                "keywords": ["pin", "pincode", "postal"],
            },
            "payload": {
                "type": "pincode",
                "validators": [
                    {
                        "type": "regex",
                        "pattern": "^[1-9][0-9]{5}$",
                        "message": "Invalid PIN code",
                    }
                ],
            },
        },
    )

    phone = mk(
        {
            "key": "std.phone",
            "kind": "template",
            "title": "Mobile number field",
            "body": "Use the phone type with the standard 10-digit validator.",
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["phone"],
                "keywords": ["mobile", "phone", "contact"],
            },
            "payload": {
                "type": "phone",
                "validators": [
                    {
                        "type": "regex",
                        "pattern": "^[6-9][0-9]{9}$",
                        "message": "Invalid phone number",
                    }
                ],
            },
        },
    )

    states = mk(
        {
            "key": "geo.states",
            "kind": "api_endpoint",
            "title": "List of States",
            "body": "DRAFT: confirm the exact request payload for the State list before publishing.",
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["select"],
                "keywords": ["state"],
            },
            "payload": {"url": "api/um/GeoUnit/GetGeoUnitDList", "method": "POST"},
        },
        live_version=None,
        updated_at=_days_ago(2),
        updated_by="Config Team",
    )

    entries = [districts_v2, static_lists, applying_for, pincode, phone, states]

    versions: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if e["key"] == "geo.districts_by_state":
            versions[e["key"]] = [
                snap(districts_v1).model_dump(),
                snap(districts_v2).model_dump(),
            ]
        else:
            versions[e["key"]] = [snap(e).model_dump()]

    live_set = {
        "geo.districts_by_state": 1,
        "policy.static_lists_use_options": 1,
        "policy.applying_for_singleton": 1,
        "std.pincode": 1,
        "std.phone": 1,
    }

    changes = []
    by_key = {e["key"]: e for e in entries}
    for k in live_set:
        e = by_key[k]
        changes.append(
            {
                "key": k,
                "title": e["title"],
                "kind": e["kind"],
                "change": "added",
                "fromVersion": None,
                "toVersion": 1,
            }
        )

    release = {
        "id": "rel_1",
        "number": 1,
        "note": "Initial import of existing form-builder rules",
        "publishedBy": DEFAULT_ACTOR,
        "publishedAt": _days_ago(12),
        "liveSet": live_set,
        "changes": changes,
    }

    return entries, versions, release