"""Pure helpers for KB entry/release derivations (mirrors admin mock)."""

from __future__ import annotations

from typing import Any

from app.kb.schemas import (
    KbAppliesTo,
    KbChangeType,
    KbEntry,
    KbEntryVersion,
    KbKind,
    KbPendingChange,
    KbRelease,
    KbStatus,
)


def status_of(rec: dict[str, Any]) -> KbStatus:
    if rec.get("archived"):
        return KbStatus.archived
    if rec.get("liveVersion") is not None:
        return KbStatus.published
    return KbStatus.draft


def pending_of(rec: dict[str, Any]) -> KbPendingChange | None:
    change: KbChangeType | None = None
    to_v: int | None = rec["version"]
    live = rec.get("liveVersion")

    if rec.get("archived"):
        if live is not None:
            change = KbChangeType.archived
            to_v = None
    elif live is None:
        change = KbChangeType.added
    elif live != rec["version"]:
        change = KbChangeType.modified

    if change is None:
        return None

    return KbPendingChange(
        key=rec["key"],
        title=rec["title"],
        kind=KbKind(rec["kind"]),
        change=change,
        fromVersion=live,
        toVersion=to_v,
    )


def snap(rec: dict[str, Any]) -> KbEntryVersion:
    return KbEntryVersion(
        version=rec["version"],
        title=rec["title"],
        body=rec["body"],
        appliesTo=KbAppliesTo.model_validate(rec["appliesTo"]),
        payload=rec.get("payload"),
        updatedBy=rec["updatedBy"],
        updatedAt=rec["updatedAt"],
    )


def to_entry(rec: dict[str, Any]) -> KbEntry:
    return KbEntry(
        key=rec["key"],
        kind=KbKind(rec["kind"]),
        title=rec["title"],
        body=rec["body"],
        appliesTo=KbAppliesTo.model_validate(rec["appliesTo"]),
        payload=rec.get("payload"),
        status=status_of(rec),
        version=rec["version"],
        publishedVersion=rec.get("liveVersion"),
        hasPendingChanges=pending_of(rec) is not None,
        updatedBy=rec["updatedBy"],
        updatedAt=rec["updatedAt"],
    )


def to_release(rel: dict[str, Any], *, is_current: bool) -> KbRelease:
    live_set = rel.get("liveSet") or {}
    return KbRelease(
        id=rel["id"],
        number=rel["number"],
        note=rel["note"],
        publishedBy=rel["publishedBy"],
        publishedAt=rel["publishedAt"],
        entryCount=len(live_set),
        changes=[KbPendingChange.model_validate(c) for c in rel.get("changes", [])],
        isCurrent=is_current,
    )