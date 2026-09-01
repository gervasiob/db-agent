from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.exceptions import ContextNotFoundError
from app.core.logging import get_logger
from app.database.session import get_async_session_factory
from app.models.context import (
    ContextStatus,
    DatabaseContextStatus,
    DatabaseKnowledgeMap,
    KnowledgeMapStatus,
    RetrievalContext,
)
from app.models.database import (
    DatabaseConnectionRequest,
    DatabaseConnectionResponse,
    DatabaseMetadata,
    TableMetadata,
)
from app.models.orm import DatabaseContextVersion as ORMContextVersion
from app.models.query import QueryExplainResponse, QueryResponse
from app.pipelines.database_discovery_pipeline import DatabaseDiscoveryPipeline
from app.pipelines.query_pipeline import QueryPipeline
from app.repositories.context_repository import ContextRepository
from app.services.database_connection_service import DatabaseConnectionService
from app.services.database_context_service import DatabaseContextService

logger = get_logger(__name__)


def _generate_request_id() -> str:
    return f"req-{uuid.uuid4().hex}"


class DatabaseAgent:
    def __init__(
        self,
        session_factory: Any = None,
        *,
        discovery_pipeline: Optional[DatabaseDiscoveryPipeline] = None,
        query_pipeline: Optional[QueryPipeline] = None,
        connection_service: Optional[DatabaseConnectionService] = None,
        context_service: Optional[DatabaseContextService] = None,
        context_repository: Optional[ContextRepository] = None,
    ) -> None:
        self._session_factory = session_factory
        self.connection_service = connection_service or DatabaseConnectionService()
        self.context_repository = context_repository or ContextRepository()
        self.context_service = context_service or DatabaseContextService(
            context_repository=self.context_repository
        )
        self.discovery_pipeline = discovery_pipeline or DatabaseDiscoveryPipeline(
            connection_service=self.connection_service,
            context_service=self.context_service,
            context_repository=self.context_repository,
        )
        self.query_pipeline = query_pipeline or QueryPipeline(
            database_context_service=self.context_service,
            connection_service=self.connection_service,
        )
        self._conn_repo = getattr(self.connection_service, "repository", None)
        self._answer_service = None
        try:
            from app.services.query_answer_service import QueryAnswerService

            self._answer_service = QueryAnswerService()
        except Exception:  # pragma: no cover
            self._answer_service = QueryAnswerService

    async def test_connection(
        self, connection_request: DatabaseConnectionRequest
    ) -> DatabaseConnectionResponse:
        logger.info(
            "DatabaseAgent.test_connection requested",
            extra={
                "database_type": connection_request.database_type,
                "host": connection_request.host,
                "database_name": connection_request.database_name,
            },
        )
        response = await self.connection_service.test_connection(connection_request)
        logger.info(
            "DatabaseAgent.test_connection finished",
            extra={
                "success": getattr(response, "success", None),
                "server_version": getattr(response, "server_version", None),
            },
        )
        return response

    async def connect(
        self,
        connection_request: DatabaseConnectionRequest,
        connection_name: Optional[str] = None,
    ) -> dict[str, Any]:
        logger.info(
            "DatabaseAgent.connect requested",
            extra={
                "database_type": connection_request.database_type,
                "host": connection_request.host,
                "database_name": connection_request.database_name,
            },
        )
        session_factory = self._session_factory or get_async_session_factory()
        async with session_factory() as session:
            connection_id: UUID = await self.connection_service.connect(
                session, connection_request, connection_name=connection_name
            )
            try:
                await session.commit()
            except Exception as exc:
                logger.warning(
                    "Commit after connect failed; rolling back",
                    extra={"connection_id": str(connection_id)},
                    exc_info=exc,
                )
                await session.rollback()
            meta = await self.connection_service.get(session, connection_id)
        return {
            "connection_id": str(connection_id),
            "status": getattr(meta, "status", "CONNECTING"),
            "host": getattr(meta, "host", None),
            "port": getattr(meta, "port", None),
            "database_name": getattr(meta, "database_name", None),
            "schema": getattr(meta, "schema", None),
            "username": getattr(meta, "username", None),
            "database_type": getattr(meta, "database_type", None),
        }

    async def list_connections(self) -> list[dict[str, Any]]:
        session_factory = self._session_factory or get_async_session_factory()
        async with session_factory() as session:
            items = await self.connection_service.list(session, skip=0, limit=100)
        out: list[dict[str, Any]] = []
        for it in items:
            if hasattr(it, "model_dump"):
                d = it.model_dump(mode="json", exclude={"password_ciphertext": True, "encryption_key_id": True})
            else:
                d = {
                    k: v for k, v in it.__dict__.items()
                    if not k.startswith("_") and k not in {"password_ciphertext", "encryption_key_id"}
                }
            if "id" in d:
                d["id"] = str(d["id"])
            out.append(d)
        return out

    async def delete_connection(self, connection_id: UUID) -> bool:
        session_factory = self._session_factory or get_async_session_factory()
        async with session_factory() as session:
            ok = await self.connection_service.delete(session, connection_id)
            try:
                await session.commit()
            except Exception as exc:
                logger.warning(
                    "Commit after delete connection failed",
                    extra={"connection_id": str(connection_id)},
                    exc_info=exc,
                )
                await session.rollback()
        return bool(ok)

    async def list_context_versions(self, connection_id: UUID) -> list[dict[str, Any]]:
        session_factory = self._session_factory or get_async_session_factory()
        async with session_factory() as session:
            stmt = (
                select(ORMContextVersion)
                .where(ORMContextVersion.connection_id == connection_id)
                .order_by(ORMContextVersion.context_version.desc())
                .limit(50)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
        out: list[dict[str, Any]] = []
        for r in rows:
            status_enum = KnowledgeMapStatus.BUILDING
            try:
                status_enum = KnowledgeMapStatus(r.status)
            except Exception:
                pass
            out.append({
                "id": str(r.id),
                "connection_id": str(r.connection_id),
                "context_version_int": r.context_version,
                "semver": f"v0.0.{r.context_version}",
                "schema_hash": r.schema_hash,
                "status": status_enum.value,
                "database_summary": r.database_summary,
                "llm_model": r.llm_model,
                "embedding_model": r.embedding_model,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return out

    async def list_domains(self, connection_id: UUID) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        return [self._dump_pydantic(d) for d in (kmap.domains or [])]

    async def list_entities(self, connection_id: UUID) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        return [self._dump_pydantic(e) for e in (kmap.entities or [])]

    async def list_concepts(self, connection_id: UUID) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        return [self._dump_pydantic(c) for c in (kmap.concepts or [])]

    async def list_metrics(self, connection_id: UUID) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        return [self._dump_pydantic(m) for m in (kmap.metrics or [])]

    async def list_dimensions(self, connection_id: UUID) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        return [self._dump_pydantic(d) for d in (kmap.dimensions or [])]

    async def list_terminology(
        self, connection_id: UUID, query: Optional[str] = None
    ) -> list[dict[str, Any]]:
        kmap = await self.get_knowledge_map(connection_id)
        items = kmap.terminology or []
        if query:
            q = (query or "").strip().lower()
            items = [
                t for t in items
                if q in (getattr(t, "term", "") or "").lower()
                or q in (getattr(t, "definition", "") or "").lower()
                or q in (getattr(t, "synonyms_text", "") or "").lower()
            ]
        return [self._dump_pydantic(t) for t in items]

    @staticmethod
    def _dump_pydantic(obj: Any) -> dict[str, Any]:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in obj.items()}
        d: dict[str, Any] = {}
        for k in dir(obj):
            if k.startswith("_"):
                continue
            try:
                v = getattr(obj, k)
            except Exception:
                continue
            if callable(v):
                continue
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            d[k] = v
        return d

    async def _connection_request_from_stored(
        self, session: Any, connection_id: UUID
    ) -> DatabaseConnectionRequest:
        meta = await self.connection_service.get(session, connection_id, decrypt_password=True)
        if meta is None:
            raise ContextNotFoundError(
                detail=f"Connection {connection_id} not found when building request for discovery",
                resource_type="database_connection",
                resource_id=str(connection_id),
            )
        plain_pw = getattr(meta, "password", None)
        if not plain_pw and getattr(meta, "encrypted_password", None):
            try:
                plain_pw = bytes(meta.encrypted_password).decode("utf-8")
            except Exception:
                plain_pw = ""
        host = getattr(meta, "host", "127.0.0.1")
        if isinstance(host, str) and host.strip().lower() == "localhost":
            host = "127.0.0.1"
        return DatabaseConnectionRequest(
            database_type=getattr(meta, "database_type", "postgresql"),
            host=host,
            port=int(getattr(meta, "port", 5432) or 5432),
            database_name=getattr(meta, "database_name", ""),
            username=getattr(meta, "username", ""),
            password=plain_pw or "",
            schema=getattr(meta, "schema", "public"),
            ssl_mode=getattr(meta, "ssl_mode", None),
        )

    async def start_discovery(
        self, connection_id: UUID, force: bool = False
    ) -> tuple[bool, str]:
        logger.info(
            "DatabaseAgent.start_discovery scheduling BG task",
            extra={"connection_id": str(connection_id), "force": force},
        )
        session_factory = self._session_factory or get_async_session_factory()
        async with session_factory() as session:
            request = await self._connection_request_from_stored(session, connection_id)

        try:
            from app.pipelines.database_discovery_pipeline import (
                _init_steps,
            )

            pipeline = self.discovery_pipeline
            steps = _init_steps()
            async with session_factory() as prep_session:
                try:
                    await getattr(pipeline, "_persist_run")(
                        prep_session,
                        connection_id,
                        steps,
                        started_at=datetime.now(timezone.utc),
                    )
                    await prep_session.commit()
                except Exception as prep_exc:
                    logger.warning(
                        "start_discovery initial persist failed",
                        extra={"connection_id": str(connection_id)},
                        exc_info=prep_exc,
                    )
                    try:
                        await prep_session.rollback()
                    except Exception:
                        pass
        except Exception as prep_e:
            logger.warning(
                "start_discovery initial persist skipped",
                extra={"connection_id": str(connection_id)},
                exc_info=prep_e,
            )

        async def _bg_run() -> None:
            try:
                await self.discovery_pipeline.run(
                    connection_id=connection_id,
                    request=request,
                    force_refresh=force,
                )
                logger.info(
                    "BG discovery pipeline finished",
                    extra={"connection_id": str(connection_id)},
                )
            except Exception as exc:
                logger.error(
                    "BG discovery pipeline failed",
                    extra={"connection_id": str(connection_id)},
                    exc_info=exc,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.create_task(_bg_run())
        return True, ContextStatus.DISCOVERING.value

    async def refresh_context(
        self,
        connection_id: UUID,
        connection_request: Optional[DatabaseConnectionRequest] = None,
    ) -> dict[str, Any]:
        logger.info(
            "DatabaseAgent.refresh_context started",
            extra={
                "connection_id": str(connection_id),
                "provided_request": connection_request is not None,
            },
        )
        session_factory = self._session_factory or get_async_session_factory()
        if connection_request is None:
            async with session_factory() as session:
                connection_request = await self._connection_request_from_stored(
                    session, connection_id
                )
        knowledge_map, ctx_status = await self.discovery_pipeline.run(
            connection_id, connection_request, force_refresh=True
        )
        logger.info(
            "DatabaseAgent.refresh_context finished",
            extra={
                "connection_id": str(connection_id),
                "context_version": knowledge_map.context_version,
            },
        )
        return {
            "connection_id": str(connection_id),
            "context_version": knowledge_map.context_version,
            "status": ctx_status.status.value if hasattr(ctx_status.status, "value") else str(ctx_status.status),
            "completed_steps": ctx_status.completed_steps,
            "total_steps": ctx_status.total_steps,
        }

    async def connect_and_discover(
        self,
        connection_request: DatabaseConnectionRequest,
        force: bool = False,
    ) -> DatabaseKnowledgeMap:
        logger.info(
            "DatabaseAgent.connect_and_discover started",
            extra={
                "force_refresh": force,
                "database_type": connection_request.database_type,
                "host": connection_request.host,
                "database_name": connection_request.database_name,
            },
        )
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            connection_id: UUID = await self.connection_service.connect(
                session, connection_request
            )
            try:
                await session.commit()
            except Exception as exc:
                logger.warning(
                    "Commit after connect failed; proceeding with discovery anyway",
                    extra={"connection_id": str(connection_id)},
                    exc_info=exc,
                )
                try:
                    await session.rollback()
                except Exception:
                    pass
        knowledge_map, _ctx_status = await self.discovery_pipeline.run(
            connection_id, connection_request, force_refresh=force
        )
        logger.info(
            "DatabaseAgent.connect_and_discover finished",
            extra={
                "connection_id": str(connection_id),
                "context_version": knowledge_map.context_version,
                "domains": len(knowledge_map.domains),
                "entities": len(knowledge_map.entities),
                "metrics": len(knowledge_map.metrics),
                "tables": len(knowledge_map.tables),
            },
        )
        return knowledge_map

    async def refresh_context(
        self,
        connection_id: UUID,
        connection_request: DatabaseConnectionRequest,
    ) -> DatabaseKnowledgeMap:
        logger.info(
            "DatabaseAgent.refresh_context started",
            extra={"connection_id": str(connection_id)},
        )
        knowledge_map, _ctx_status = await self.discovery_pipeline.run(
            connection_id, connection_request, force_refresh=True
        )
        logger.info(
            "DatabaseAgent.refresh_context finished",
            extra={
                "connection_id": str(connection_id),
                "context_version": knowledge_map.context_version,
            },
        )
        return knowledge_map

    async def get_discovery_status(
        self, connection_id: UUID
    ) -> DatabaseContextStatus:
        return await self.discovery_pipeline.get_status(connection_id)

    async def get_knowledge_map(
        self, connection_id: UUID
    ) -> DatabaseKnowledgeMap:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            kmap = await self.context_service.get_latest(connection_id, session)
            if kmap is None:
                raise ContextNotFoundError(
                    detail=f"No knowledge map found for connection {connection_id}",
                    resource_type="knowledge_map",
                    resource_id=str(connection_id),
                )
            if kmap.id is not None:
                full_kmap = await self.context_repository.get_knowledge_map(
                    session, kmap.id
                )
                if full_kmap is not None:
                    return full_kmap
            return kmap

    async def get_schema(
        self, connection_id: UUID
    ) -> DatabaseMetadata:
        kmap = await self.get_knowledge_map(connection_id)
        schemas: dict[str, list[TableMetadata]] = {}
        for t in kmap.tables:
            schema_name = getattr(t, "schema_name", "")
            if schema_name not in schemas:
                schemas[schema_name] = []
            schemas[schema_name].append(t)
        metadata = DatabaseMetadata(
            schemas=schemas,
            relationships=list(kmap.relationships),
        )
        return metadata

    async def get_context_summary(
        self, connection_id: UUID
    ) -> RetrievalContext:
        kmap = await self.get_knowledge_map(connection_id)
        preview_question = "Resumen general del contexto disponible"
        top_k_preview = min(
            12,
            max(
                3,
                len(kmap.tables) // 4
                + len(kmap.metrics) // 2
                + len(kmap.entities) // 2,
            ),
        )
        from app.services.schema_retrieval_service import SchemaRetrievalService

        retrieval_service = SchemaRetrievalService()
        ctx = retrieval_service.retrieve_context(
            preview_question, kmap, top_k_preview
        )
        ctx.question = preview_question
        return ctx

    async def ask_question(
        self,
        connection_id: UUID,
        question: str,
        *,
        request_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> QueryResponse:
        final_rid = request_id or _generate_request_id()
        logger.info(
            "DatabaseAgent.ask_question",
            extra={
                "request_id": final_rid,
                "connection_id": str(connection_id),
                "question_length": len(question or ""),
            },
        )
        return await self.query_pipeline.run(
            connection_id=connection_id,
            question=question,
            request_id=final_rid,
        )

    async def explain_question(
        self,
        connection_id: UUID,
        question: str,
        *,
        request_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> QueryExplainResponse:
        final_rid = request_id or _generate_request_id()
        logger.info(
            "DatabaseAgent.explain_question",
            extra={
                "request_id": final_rid,
                "connection_id": str(connection_id),
                "question_length": len(question or ""),
            },
        )
        return await self.query_pipeline.explain(
            connection_id=connection_id,
            question=question,
            request_id=final_rid,
        )


__all__ = ["DatabaseAgent"]
