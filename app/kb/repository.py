"""MongoDB persistence for the Knowledge Base."""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.kb.logic import snap
from app.kb.seed import build_seed_documents

logger = logging.getLogger("app.kb.repository")

ENTRIES = "kb_entries"
VERSIONS = "kb_entry_versions"
RELEASES = "kb_releases"
META = "kb_meta"
META_ID = "singleton"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[ENTRIES].create_index("key", unique=True)
    await db[VERSIONS].create_index([("key", 1), ("version", 1)], unique=True)
    await db[RELEASES].create_index("id", unique=True)
    await db[RELEASES].create_index("number", unique=True)


async def seed_if_empty(db: AsyncIOMotorDatabase) -> None:
    if not get_settings().kb_seed_on_empty:
        return
    if await db[RELEASES].count_documents({}) > 0:
        return

    entries, versions_map, release = build_seed_documents()
    for rec in entries:
        await db[ENTRIES].insert_one({"_id": rec["key"], **rec})
    for key, vers in versions_map.items():
        for v in vers:
            await db[VERSIONS].insert_one({"_id": f"{key}:v{v['version']}", "key": key, **v})
    await db[RELEASES].insert_one({"_id": release["id"], **release})
    await db[META].update_one(
        {"_id": META_ID},
        {"$set": {"currentReleaseId": release["id"]}},
        upsert=True,
    )
    logger.info("KB seeded with release #%s (%d entries)", release["number"], len(release["liveSet"]))


async def list_entries(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[ENTRIES].find({})
    return [doc async for doc in cursor]


async def get_entry(db: AsyncIOMotorDatabase, key: str) -> dict[str, Any] | None:
    return await db[ENTRIES].find_one({"key": key})


async def insert_entry(db: AsyncIOMotorDatabase, rec: dict[str, Any]) -> None:
    await db[ENTRIES].insert_one({"_id": rec["key"], **rec})
    v = snap(rec).model_dump()
    await db[VERSIONS].insert_one({"_id": f"{rec['key']}:v{v['version']}", "key": rec["key"], **v})


async def replace_entry(db: AsyncIOMotorDatabase, rec: dict[str, Any]) -> None:
    key = rec["key"]
    await db[ENTRIES].replace_one({"key": key}, {"_id": key, **rec})
    v = snap(rec).model_dump()
    await db[VERSIONS].insert_one({"_id": f"{key}:v{v['version']}", "key": key, **v})


async def entry_versions(db: AsyncIOMotorDatabase, key: str) -> list[dict[str, Any]]:
    cursor = db[VERSIONS].find({"key": key}).sort("version", -1)
    docs = [doc async for doc in cursor]
    for d in docs:
        d.pop("_id", None)
        d.pop("key", None)
    return docs


async def list_releases(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[RELEASES].find({}).sort("number", 1)
    return [doc async for doc in cursor]


async def current_release_id(db: AsyncIOMotorDatabase) -> str | None:
    meta = await db[META].find_one({"_id": META_ID})
    return meta.get("currentReleaseId") if meta else None


async def append_release(db: AsyncIOMotorDatabase, release: dict[str, Any]) -> None:
    await db[RELEASES].insert_one({"_id": release["id"], **release})
    await db[META].update_one(
        {"_id": META_ID},
        {"$set": {"currentReleaseId": release["id"]}},
        upsert=True,
    )


async def update_entries_live_versions(
    db: AsyncIOMotorDatabase, live_set: dict[str, int]
) -> None:
    """After publish/rollback, sync liveVersion on every entry document."""
    keys = await db[ENTRIES].distinct("key")
    for key in keys:
        lv = live_set.get(key)
        await db[ENTRIES].update_one({"key": key}, {"$set": {"liveVersion": lv}})