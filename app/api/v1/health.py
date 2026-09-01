from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.logging import get_logger
from app.database.session import get_async_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

router = APIRouter(prefix="/health")


async def _metadata_status() -> str:
    try:
        factory: async_sessionmaker[AsyncSession] = get_async_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        try:
            from app.database.session import _metadata_engine
            if _metadata_engine is None:
                return "disconnected"
        except Exception:
            pass
        return "error"


def _llm_status() -> str:
    if settings.OPENAI_API_KEY and str(settings.OPENAI_API_KEY).strip():
        return "configured"
    return "missing_key"


@router.get("", summary="Liveness + dependencies status")
async def health_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "metadata_db": await _metadata_status(),
        "llm": _llm_status(),
    }


@router.get("/ready", summary="Readiness probe")
async def health_ready() -> dict[str, Any]:
    base = await health_status()
    ready = base["metadata_db"] == "connected"
    base["ready"] = ready
    return base


@router.get("/live", summary="Liveness probe")
async def health_live() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "live": True,
    }
