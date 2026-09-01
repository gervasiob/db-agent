from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from app.core.logging import get_logger, get_request_id
from app.core.security import generate_request_id
from app.models.query import QueryExplainResponse, QueryResponse

if TYPE_CHECKING:
    from app.agents.database_agent import DatabaseAgent

logger = get_logger(__name__)

router = APIRouter(prefix="/query")


def get_agent(request: Request) -> "DatabaseAgent":
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DatabaseAgent is not initialized on the application. "
            "Verify the server lifespan started correctly and metadata DB is reachable.",
        )
    return agent


@router.post(
    "",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Answer a natural-language question over the target DB",
)
async def run_query(
    question: str = Body(..., embed=True),
    connection_id: UUID = Body(..., embed=True),
    request: Request = None,  # type: ignore[assignment]
) -> QueryResponse:
    start = time.perf_counter()
    agent = get_agent(request)
    req_id = get_request_id() or generate_request_id()
    answer = await agent.ask_question(
        question=question,
        connection_id=connection_id,
        request_id=req_id,
    )
    total_ms = int((time.perf_counter() - start) * 1000)
    answer.execution_time_ms = total_ms
    if not answer.request_id:
        answer.request_id = req_id
    return answer


@router.post(
    "/explain",
    response_model=QueryExplainResponse,
    status_code=status.HTTP_200_OK,
    summary="Explain the semantic plan that would answer a question",
)
async def explain_query(
    question: str = Body(..., embed=True),
    connection_id: UUID = Body(..., embed=True),
    request: Request = None,  # type: ignore[assignment]
) -> QueryExplainResponse:
    agent = get_agent(request)
    return await agent.explain_question(
        question=question,
        connection_id=connection_id,
    )
