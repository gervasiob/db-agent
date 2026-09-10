from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.v1._deps import require_agent
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.agents.database_agent import DatabaseAgent

logger = get_logger(__name__)

router = APIRouter(prefix="/context")


def get_agent(request: Request) -> "DatabaseAgent":
    return require_agent(request)


@router.get(
    "/versions",
    summary="List persisted context versions for a connection",
)
async def list_versions(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    agent = get_agent(request)
    return await agent.list_context_versions(connection_id)


@router.get(
    "/domains",
    summary="List all business domains in the latest context",
)
async def list_domains(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_domains(connection_id)


@router.get(
    "/entities",
    summary="List all business entities in the latest context",
)
async def list_entities(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_entities(connection_id)


@router.get(
    "/concepts",
    summary="List all business concepts in the latest context",
)
async def list_concepts(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_concepts(connection_id)


@router.get(
    "/metrics",
    summary="List all business metrics in the latest context",
)
async def list_metrics(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_metrics(connection_id)


@router.get(
    "/dimensions",
    summary="List all dimension definitions in the latest context",
)
async def list_dimensions(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_dimensions(connection_id)


@router.get(
    "/terminology",
    summary="Search/list glossary terminology entries",
)
async def list_terminology(
    connection_id: UUID = Query(...),
    q: Optional[str] = Query(default=None, description="Optional search query"),
    request: Request = None,  # type: ignore[assignment]
):
    agent = get_agent(request)
    return await agent.list_terminology(connection_id, query=q)
