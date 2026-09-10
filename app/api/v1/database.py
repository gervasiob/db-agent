from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.exceptions import ContextNotFoundError
from app.core.logging import get_logger
from app.models.database import (
    DatabaseConnectionRequest,
    DatabaseConnectionResponse,
    DatabaseMetadata,
)
from app.api.v1._deps import require_agent

if TYPE_CHECKING:
    from app.agents.database_agent import DatabaseAgent

logger = get_logger(__name__)

router = APIRouter(prefix="/database")


def get_agent(request: Request) -> "DatabaseAgent":
    return require_agent(request)


@router.post(
    "/test-connection",
    response_model=DatabaseConnectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Test connectivity without saving credentials",
)
async def test_connection(
    body: DatabaseConnectionRequest,
    request: Request,
) -> DatabaseConnectionResponse:
    agent = get_agent(request)
    return await agent.test_connection(body)


@router.post(
    "/connect",
    status_code=status.HTTP_201_CREATED,
    summary="Persist a connection after testing it",
)
async def connect(
    body: DatabaseConnectionRequest,
    request: Request,
) -> dict[str, Any]:
    agent = get_agent(request)
    return await agent.connect(body)


@router.get(
    "/schema",
    response_model=DatabaseMetadata,
    summary="Get schema metadata for a saved connection",
)
async def get_schema(
    connection_id: UUID = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> DatabaseMetadata:
    agent = get_agent(request)
    return await agent.get_schema(connection_id)


@router.get(
    "/connections",
    summary="List saved connection summaries (no secrets)",
)
async def list_connections(
    request: Request,
) -> list[dict[str, Any]]:
    agent = get_agent(request)
    return await agent.list_connections()


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a saved connection by ID",
)
async def delete_connection(
    connection_id: UUID,
    request: Request,
) -> dict[str, Any]:
    agent = get_agent(request)
    ok = await agent.delete_connection(connection_id)
    if not ok:
        raise ContextNotFoundError(
            detail="Connection not found",
            resource_type="connection",
            resource_id=str(connection_id),
        )
    return {"ok": True, "connection_id": str(connection_id)}
