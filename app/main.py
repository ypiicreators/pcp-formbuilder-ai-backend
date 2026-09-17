"""
FastAPI application entry point for the PCP AI Form Builder backend.

Run locally with:
    uvicorn app.main:app --reload
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

settings = get_settings()

# Emit our app.* INFO logs (timing/telemetry) to the console. Without this the
# root logger defaults to WARNING and the timing lines would be swallowed.
logging.basicConfig(
    level=logging.INFO if settings.debug else logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

# CORS -- allow the admin portal origins (mirrors the extractor service policy).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All routes are served under /api (e.g. /api/form-ai/generate, /api/health).
app.include_router(router, prefix="/api")


@app.get("/", tags=["meta"])
async def root() -> dict:
    """Simple landing response."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/health",
    }
