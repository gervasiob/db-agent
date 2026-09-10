from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.agents.database_agent import DatabaseAgent
from app.api import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import (
    clear_request_id,
    get_logger,
    get_request_id,
    set_request_id,
    setup_logging,
)
from app.core.security import generate_request_id
from app.database.session import (
    dispose_metadata_db,
    get_async_session_factory,
    init_metadata_db,
)
from app.ui.routes import ui_router

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        incoming = request.headers.get("X-Request-Id")
        req_id = incoming.strip() if incoming and incoming.strip() else generate_request_id()
        set_request_id(req_id)
        try:
            response = await call_next(request)
        finally:
            pass
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-Id"] = req_id
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        clear_request_id()
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(
        log_level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
    )
    logger.info(
        "Starting application",
        extra={
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
        },
    )
    metadata_ok = True
    metadata_error: str | None = None
    session_factory = None
    agent = None
    agent_init_error: str | None = None
    try:
        await init_metadata_db(verify_connection=True)
        logger.info("Metadata database initialized")
    except Exception as exc:
        metadata_ok = False
        metadata_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Metadata database initialization failed; continuing in degraded mode",
            exc_info=exc,
        )
    try:
        if metadata_ok:
            session_factory = get_async_session_factory()
            agent = DatabaseAgent(session_factory)
        else:
            agent = None
            agent_init_error = f"Metadata DB init failed: {metadata_error}"
    except Exception as exc:
        agent_init_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Could not instantiate DatabaseAgent", exc_info=exc)
        agent = None
    app.state.agent = agent
    app.state.metadata_ok = metadata_ok
    app.state.metadata_error = metadata_error
    app.state.agent_init_error = agent_init_error
    logger.info("Application ready", extra={"metadata_ok": metadata_ok, "agent_ok": agent is not None})
    yield
    logger.info("Shutting down application")
    app.state.agent = None
    try:
        await dispose_metadata_db()
    except Exception as exc:
        logger.warning("Error during metadata db disposal", exc_info=exc)
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DB Agent TP Austral",
        description=(
            "Conversational Analytics Platform - Semantic Layer over PostgreSQL "
            "with LLM-powered natural language queries."
        ),
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(ui_router, include_in_schema=False)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/api-home", tags=["root"], include_in_schema=True)
    async def api_root() -> dict[str, Any]:
        return {
            "app": "DB Agent TP Austral",
            "version": settings.APP_VERSION,
            "ui": "/ui",
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()


def get_agent(request: Request) -> DatabaseAgent:
    from app.api.v1._deps import require_agent

    return require_agent(request)
