from __future__ import annotations

from typing import Any

from app.agents.database_agent import DatabaseAgent


class DatabaseDiscoveryPipeline:
    def __init__(self, agent: DatabaseAgent) -> None:
        self.agent = agent

    async def run(self, connection_id: Any, *, force: bool = False) -> Any:
        return await self.agent.start_discovery(connection_id, force=force)


class QueryPipeline:
    def __init__(self, agent: DatabaseAgent) -> None:
        self.agent = agent

    async def run(self, *, question: str, connection_id: Any, request_id: str) -> Any:
        return await self.agent.answer_question(
            question=question,
            connection_id=connection_id,
            request_id=request_id,
        )


__all__ = ["DatabaseDiscoveryPipeline", "QueryPipeline"]
