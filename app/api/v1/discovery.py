from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from app.api.v1._deps import require_agent
from app.core.logging import get_logger
from app.models.context import (
    ContextStatus,
    DatabaseContextStatus,
    DatabaseKnowledgeMap,
)

if TYPE_CHECKING:
    from app.agents.database_agent import DatabaseAgent

logger = get_logger(__name__)

router = APIRouter(prefix="/database")


def get_agent(request: Request) -> "DatabaseAgent":
    return require_agent(request)


@router.post(
    "/discover",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger async schema/context discovery for a saved connection",
)
async def discover(
    connection_id: UUID = Body(..., embed=True),
    force: bool = Body(default=False, embed=False),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agent = get_agent(request)
    started, status_state = await agent.start_discovery(connection_id, force=force)
    return {
        "discovery_started": started,
        "status": status_state,
        "connection_id": str(connection_id),
    }


@router.get(
    "/discovery/status",
    response_model=DatabaseContextStatus,
    summary="Get the current discovery status for a connection",
)
async def discovery_status(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> DatabaseContextStatus:
    agent = get_agent(request)
    return await agent.get_discovery_status(connection_id)


@router.post(
    "/refresh-context",
    status_code=status.HTTP_200_OK,
    summary="Force-refresh the semantic context (re-discover)",
)
async def refresh_context(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agent = get_agent(request)
    return await agent.refresh_context(connection_id)


@router.get(
    "/knowledge-map",
    response_model=DatabaseKnowledgeMap,
    summary="Retrieve the full built knowledge map for a connection",
)
async def knowledge_map(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> DatabaseKnowledgeMap:
    agent = get_agent(request)
    return await agent.get_knowledge_map(connection_id)


@router.get(
    "/context",
    summary="Lightweight context summary (counts, tables, version)",
)
async def context_summary(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agent = get_agent(request)
    return await agent.get_context_summary(connection_id)
