"""Knowledge Base admin API (`/api/kb/*`)."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.db import mongo
from app.kb import service
from app.kb.schemas import (
    KbCreateInput,
    KbEntry,
    KbEntryVersion,
    KbListResult,
    KbPendingChange,
    KbPublishInput,
    KbRelease,
    KbUpdateInput,
)

router = APIRouter(tags=["knowledge-base"])


def _db():
    if not mongo.is_configured():
        raise HTTPException(
            status_code=503,
            detail=["MongoDB is not configured. Set MONGO_URI in the AI backend .env."],
        )
    try:
        return mongo.get_db()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=[str(exc)]) from exc


def _actor(x_kb_actor: str | None = Header(default=None)) -> str:
    return (x_kb_actor or "admin").strip() or "admin"


def _http_from_kb(err: service.KbError) -> HTTPException:
    return HTTPException(status_code=err.status, detail=err.messages)


@router.get("/entries", response_model=KbListResult)
async def list_entries(
    search: str | None = None,
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=100),
) -> KbListResult:
    db = _db()
    try:
        return await service.list_entries(
            db, search=search, kind=kind, status=status, page=page, page_size=pageSize
        )
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.post("/entries", response_model=KbEntry, status_code=201)
async def create_entry(
    body: KbCreateInput,
    x_kb_actor: str | None = Header(default=None),
) -> KbEntry:
    db = _db()
    try:
        return await service.create_entry(db, body, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.put("/entries/{key}", response_model=KbEntry)
async def update_entry(
    key: str,
    body: KbUpdateInput,
    x_kb_actor: str | None = Header(default=None),
) -> KbEntry:
    db = _db()
    try:
        return await service.update_entry(db, key, body, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.post("/entries/{key}/archive", response_model=KbEntry)
async def archive_entry(
    key: str,
    x_kb_actor: str | None = Header(default=None),
) -> KbEntry:
    db = _db()
    try:
        return await service.set_archived(db, key, True, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.post("/entries/{key}/restore", response_model=KbEntry)
async def restore_entry(
    key: str,
    x_kb_actor: str | None = Header(default=None),
) -> KbEntry:
    db = _db()
    try:
        return await service.set_archived(db, key, False, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.get("/entries/{key}/versions", response_model=list[KbEntryVersion])
async def get_entry_versions(key: str) -> list[KbEntryVersion]:
    db = _db()
    try:
        return await service.get_entry_versions(db, key)
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.get("/releases/pending", response_model=list[KbPendingChange])
async def get_pending_changes() -> list[KbPendingChange]:
    db = _db()
    try:
        return await service.get_pending_changes(db)
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.get("/releases", response_model=list[KbRelease])
async def list_releases() -> list[KbRelease]:
    db = _db()
    try:
        return await service.list_releases(db)
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.post("/releases", response_model=KbRelease)
async def publish_release(
    body: KbPublishInput,
    x_kb_actor: str | None = Header(default=None),
) -> KbRelease:
    db = _db()
    try:
        return await service.publish(db, body, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e


@router.post("/releases/{release_id}/rollback", response_model=KbRelease)
async def rollback_release(
    release_id: str,
    x_kb_actor: str | None = Header(default=None),
) -> KbRelease:
    db = _db()
    try:
        return await service.rollback(db, release_id, actor=_actor(x_kb_actor))
    except service.KbError as e:
        raise _http_from_kb(e) from e