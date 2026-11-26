"""Load the live Knowledge Base snapshot for AI generation."""

from __future__ import annotations

import logging
from typing import Any

from app.db import mongo
from app.kb import repository as repo

logger = logging.getLogger("app.kb.loader")

RELEASES = repo.RELEASES
VERSIONS = repo.VERSIONS


async def fetch_live_kb() -> list[dict[str, Any]]:
    """Published entries from the current release, or [] if Mongo/KB unavailable."""
    if not mongo.is_configured():
        return []
    try:
        db = mongo.get_db()
    except RuntimeError:
        return []

    try:
        current_id = await repo.current_release_id(db)
        if not current_id:
            return []
        release = await db[RELEASES].find_one({"id": current_id})
        if not release:
            return []

        live_set: dict[str, int] = release.get("liveSet") or {}
        out: list[dict[str, Any]] = []
        for key, ver in live_set.items():
            vdoc = await db[VERSIONS].find_one({"key": key, "version": ver})
            if not vdoc:
                logger.warning("KB live entry %s v%s missing in versions", key, ver)
                continue
            edoc = await repo.get_entry(db, key)
            kind = (edoc or {}).get("kind") or "policy"
            out.append(
                {
                    "key": key,
                    "kind": kind,
                    "title": vdoc.get("title", key),
                    "body": vdoc.get("body", ""),
                    "appliesTo": vdoc.get("appliesTo") or {"modes": [], "fieldTypes": [], "keywords": []},
                    "payload": vdoc.get("payload"),
                }
            )
        return out
    except Exception:  # noqa: BLE001 — generation must continue if KB read fails
        logger.exception("KB live snapshot read failed")
        return []