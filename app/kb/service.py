"""Knowledge Base business logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.kb import repository as repo
from app.kb.logic import pending_of, snap, to_entry, to_release
from app.kb.schemas import (
    KbChangeType,
    KbCreateInput,
    KbEntry,
    KbEntryVersion,
    KbKind,
    KbListResult,
    KbPendingChange,
    KbPublishInput,
    KbRelease,
    KbStatus,
    KbUpdateInput,
)
from app.kb.validate import same_content, validate_create, validate_update

DEFAULT_ACTOR = "admin"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KbError(Exception):
    def __init__(self, status: int, messages: list[str]):
        self.status = status
        self.messages = messages
        super().__init__(messages[0] if messages else "KB error")


async def ensure_ready(db: AsyncIOMotorDatabase) -> None:
    await repo.ensure_indexes(db)
    await repo.seed_if_empty(db)


async def list_entries(
    db: AsyncIOMotorDatabase,
    *,
    search: str | None,
    kind: str | None,
    status: str | None,
    page: int,
    page_size: int,
) -> KbListResult:
    await ensure_ready(db)
    recs = await repo.list_entries(db)
    q = (search or "").strip().lower()

    def matches(rec: dict[str, Any]) -> bool:
        if q:
            kw = rec.get("appliesTo", {}).get("keywords") or []
            if (
                q not in rec["key"].lower()
                and q not in rec["title"].lower()
                and not any(q in str(k).lower() for k in kw)
            ):
                return False
        if kind and kind != "all" and rec["kind"] != kind:
            return False
        if status and status != "all":
            st = to_entry(rec).status.value
            if st != status:
                return False
        return True

    filtered = [r for r in recs if matches(r)]
    filtered.sort(key=lambda r: r["updatedAt"], reverse=True)
    start = max(0, (page - 1) * page_size)
    page_items = filtered[start : start + page_size]
    pending_count = sum(1 for r in recs if pending_of(r) is not None)
    return KbListResult(
        items=[to_entry(r) for r in page_items],
        total=len(filtered),
        pendingCount=pending_count,
    )


async def get_entry_versions(db: AsyncIOMotorDatabase, key: str) -> list[KbEntryVersion]:
    await ensure_ready(db)
    if not await repo.get_entry(db, key):
        raise KbError(404, [f'Entry "{key}" was not found.'])
    docs = await repo.entry_versions(db, key)
    return [KbEntryVersion.model_validate(d) for d in docs]


async def create_entry(db: AsyncIOMotorDatabase, inp: KbCreateInput, *, actor: str) -> KbEntry:
    await ensure_ready(db)
    errors = validate_create(inp)
    if errors:
        raise KbError(422, errors)
    if await repo.get_entry(db, inp.key):
        raise KbError(409, [f'An entry with key "{inp.key}" already exists.'])

    rec: dict[str, Any] = {
        "key": inp.key,
        "kind": inp.kind.value,
        "title": inp.title,
        "body": inp.body,
        "appliesTo": inp.appliesTo.model_dump(),
        "payload": inp.payload,
        "version": 1,
        "liveVersion": None,
        "archived": False,
        "updatedBy": actor,
        "updatedAt": _now_iso(),
    }
    await repo.insert_entry(db, rec)
    return to_entry(rec)


async def update_entry(
    db: AsyncIOMotorDatabase, key: str, inp: KbUpdateInput, *, actor: str
) -> KbEntry:
    await ensure_ready(db)
    errors = validate_update(inp)
    if errors:
        raise KbError(422, errors)

    rec = await repo.get_entry(db, key)
    if not rec:
        raise KbError(404, [f'Entry "{key}" was not found.'])
    if rec["version"] != inp.expectedVersion:
        raise KbError(
            409,
            [
                f"This entry was changed by someone else (now v{rec['version']}). "
                "Reload it and try again."
            ],
        )
    if rec.get("archived"):
        raise KbError(422, ["Restore this entry before editing it."])
    if same_content(rec, inp):
        return to_entry(rec)

    rec = {
        **rec,
        "kind": inp.kind.value,
        "title": inp.title,
        "body": inp.body,
        "appliesTo": inp.appliesTo.model_dump(),
        "payload": inp.payload,
        "version": rec["version"] + 1,
        "updatedBy": actor,
        "updatedAt": _now_iso(),
    }
    rec.pop("_id", None)
    await repo.replace_entry(db, rec)
    return to_entry(rec)


async def set_archived(db: AsyncIOMotorDatabase, key: str, archived: bool, *, actor: str) -> KbEntry:
    await ensure_ready(db)
    rec = await repo.get_entry(db, key)
    if not rec:
        raise KbError(404, [f'Entry "{key}" was not found.'])
    rec["archived"] = archived
    rec["updatedBy"] = actor
    rec["updatedAt"] = _now_iso()
    rec.pop("_id", None)
    await db[repo.ENTRIES].replace_one({"key": key}, {"_id": key, **rec})
    return to_entry(rec)


async def get_pending_changes(db: AsyncIOMotorDatabase) -> list[KbPendingChange]:
    await ensure_ready(db)
    recs = await repo.list_entries(db)
    pending = [pending_of(r) for r in recs]
    out = [p for p in pending if p is not None]
    out.sort(key=lambda p: p.key)
    return out


async def list_releases(db: AsyncIOMotorDatabase) -> list[KbRelease]:
    await ensure_ready(db)
    current_id = await repo.current_release_id(db)
    releases = await repo.list_releases(db)
    return [
        to_release(r, is_current=r["id"] == current_id) for r in reversed(releases)
    ]


async def publish(
    db: AsyncIOMotorDatabase, inp: KbPublishInput, *, actor: str
) -> KbRelease:
    await ensure_ready(db)
    recs = await repo.list_entries(db)
    pending = [p for p in (pending_of(r) for r in recs) if p is not None]
    if not pending:
        raise KbError(422, ["There are no changes to publish."])
    if not inp.note.strip():
        raise KbError(422, ["A release note is required."])

    pending_keys = {p.key for p in pending}
    keys = inp.keys if inp.keys is not None else list(pending_keys)
    if not keys:
        raise KbError(422, ["Select at least one entry to publish."])
    unknown = [k for k in keys if k not in pending_keys]
    if unknown:
        raise KbError(422, [f"These entries have no pending changes: {', '.join(unknown)}"])

    selected = [p for p in pending if p.key in keys]
    releases = await repo.list_releases(db)
    if releases:
        current = releases[-1]
        live_set = dict(current.get("liveSet") or {})
        base_number = current["number"]
    else:
        live_set = {}
        base_number = 0

    by_key = {r["key"]: r for r in recs}
    for ch in selected:
        r = by_key[ch.key]
        if r.get("archived"):
            live_set.pop(r["key"], None)
            r["liveVersion"] = None
        else:
            live_set[r["key"]] = r["version"]
            r["liveVersion"] = r["version"]
        r.pop("_id", None)
        await db[repo.ENTRIES].replace_one({"key": r["key"]}, {"_id": r["key"], **r})

    number = base_number + 1
    release = {
        "id": f"rel_{number}",
        "number": number,
        "note": inp.note.strip(),
        "publishedBy": actor,
        "publishedAt": _now_iso(),
        "changes": [c.model_dump() for c in selected],
        "liveSet": live_set,
    }
    await repo.append_release(db, release)
    return to_release(release, is_current=True)


async def rollback(db: AsyncIOMotorDatabase, release_id: str, *, actor: str) -> KbRelease:
    await ensure_ready(db)
    releases = await repo.list_releases(db)
    target = next((r for r in releases if r["id"] == release_id), None)
    if not target:
        raise KbError(404, ["Release not found."])
    current_id = await repo.current_release_id(db)
    current = next((r for r in releases if r["id"] == current_id), releases[-1])
    if target["id"] == current["id"]:
        raise KbError(422, ["That release is already current."])

    keys = set(current.get("liveSet", {})) | set(target.get("liveSet", {}))
    changes: list[KbPendingChange] = []
    recs = {r["key"]: r for r in await repo.list_entries(db)}
    for key in keys:
        from_v = current.get("liveSet", {}).get(key)
        to_v = target.get("liveSet", {}).get(key)
        if from_v == to_v:
            continue
        rec = recs.get(key)
        change: KbChangeType
        if from_v is None:
            change = KbChangeType.added
        elif to_v is None:
            change = KbChangeType.archived
        else:
            change = KbChangeType.modified
        changes.append(
            KbPendingChange(
                key=key,
                title=rec["title"] if rec else key,
                kind=KbKind(rec["kind"]) if rec else KbKind.policy,
                change=change,
                fromVersion=from_v,
                toVersion=to_v,
            )
        )

    live_set = dict(target.get("liveSet") or {})
    number = current["number"] + 1
    release = {
        "id": f"rel_{number}",
        "number": number,
        "note": f"Rollback to release #{target['number']}",
        "publishedBy": actor,
        "publishedAt": _now_iso(),
        "changes": [c.model_dump() for c in changes],
        "liveSet": live_set,
    }
    await repo.append_release(db, release)
    await repo.update_entries_live_versions(db, live_set)
    return to_release(release, is_current=True)