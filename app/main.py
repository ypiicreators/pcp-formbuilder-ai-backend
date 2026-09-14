"""
FastAPI application entry point for the PCP AI Form Builder backend.

Run locally with:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

settings = get_settings()

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
