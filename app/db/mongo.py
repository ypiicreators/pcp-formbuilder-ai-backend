"""
MongoDB connection management for the AI backend (KB store).

One AsyncIOMotorClient per process, opened at app startup and closed at
shutdown (see app/main.py lifespan). If MONGO_URI is empty, Mongo is disabled
and the rest of the app keeps working.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

logger = logging.getLogger("app.db.mongo")

_client: AsyncIOMotorClient | None = None


async def connect() -> None:
    """Open the client and verify it with a ping. No-op when MONGO_URI is empty."""
    global _client
    settings = get_settings()
    if not settings.mongo_uri:
        logger.warning("MONGO_URI is empty; MongoDB disabled (KB features off).")
        return

    _client = AsyncIOMotorClient(
        settings.mongo_uri,
        serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
    )
    # Fails fast on bad credentials or an IP that is not allowlisted in Atlas.
    await _client.admin.command("ping")
    logger.info("MongoDB connected (db=%s)", settings.mongo_db)


async def close() -> None:
    """Close the client if it was opened."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def is_configured() -> bool:
    """True when a Mongo URI was provided."""
    return bool(get_settings().mongo_uri)


def get_db() -> AsyncIOMotorDatabase:
    """Return the application database. Raises if Mongo is not connected."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected (set MONGO_URI in .env).")
    return _client[get_settings().mongo_db]


async def is_connected() -> bool:
    """Live check used by the health endpoint."""
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
