"""
FastAPI application entry point for the PCP AI Form Builder backend.

Run locally with:
    uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.kb_routes import router as kb_router
from app.api.routes import router
from app.config import get_settings
from app.core.doc_trace import install_trace_filter
from app.db import mongo
from app.graph_agent.api import router as graph_agent_router

# Clients sometimes call https://<host>/kb/... instead of /api/kb/....
_BARE_API_PREFIXES = ("/kb", "/form-ai", "/workflow-ai", "/workflow-graph", "/health")


class ApiPrefixCompatMiddleware:
    """Rewrite unprefixed AI paths onto the FastAPI `/api` mount."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or ""
            if any(path == prefix or path.startswith(prefix + "/") for prefix in _BARE_API_PREFIXES):
                scope = dict(scope)
                new_path = f"/api{path}"
                scope["path"] = new_path
                if "raw_path" in scope:
                    scope["raw_path"] = new_path.encode("utf-8")
        await self.app(scope, receive, send)

settings = get_settings()

# Emit our app.* INFO logs (timing/telemetry) to the console. Without this the
# root logger defaults to WARNING and the timing lines would be swallowed.
_level_name = (settings.log_level or "").strip().upper()
logging.basicConfig(
    level=getattr(logging, _level_name, None)
    if _level_name in logging.getLevelNamesMapping()
    else (logging.INFO if settings.debug else logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Prefix every line of one document upload with [doc=<id>] so a single grep follows it.
install_trace_filter()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Open MongoDB on startup and close it on shutdown (no-op if MONGO_URI is empty)."""
    await mongo.connect()
    yield
    await mongo.close()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

# CORS -- allow the admin portal origins (mirrors the extractor service policy).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Last added runs first: rewrite /kb -> /api/kb before routing.
app.add_middleware(ApiPrefixCompatMiddleware)

# All routes are served under /api (e.g. /api/form-ai/generate, /api/health).
app.include_router(router, prefix="/api")
app.include_router(kb_router, prefix="/api/kb")
app.include_router(graph_agent_router, prefix="/api")


@app.get("/", tags=["meta"])
async def root() -> dict:
    """Simple landing response."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/health",
    }
