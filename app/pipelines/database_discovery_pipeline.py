from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import settings
from app.core.exceptions import DatabaseDiscoveryError, DBAgentException
from app.core.logging import get_logger
from app.database.connection import (
    DatabaseConfig,
    build_config_from_request,
    close_engine,
    create_engine_from_config,
)
from app.database.session import get_async_session_factory, get_metadata_engine
from app.llm.embeddings import async_embed_texts, build_document_for_embedding
from app.models.context import (
    ContextStatus,
    DatabaseContextStatus,
    DatabaseKnowledgeMap,
    KnowledgeMapStatus,
)
from app.models.database import (
    DatabaseConnectionRequest,
    DatabaseConnectionStatus,
    DatabaseMetadata,
    TableMetadata,
)
from app.models.orm import DatabaseDiscoveryRun as ORMDiscoveryRun
from app.models.semantic import (
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
    ColumnSemantic,
    DateSemantic,
    Dimension,
    EnumValue,
    RelationshipGraph,
    TerminologyEntry,
)
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.database_repository import DatabaseConnectionRepository
from app.services.business_concept_service import BusinessConceptService
from app.services.business_domain_service import BusinessDomainService
from app.services.business_entity_service import BusinessEntityService
from app.services.business_metric_service import BusinessMetricService
from app.services.column_profiling_service import ColumnProfilingService
from app.services.data_sanitization_service import DataSanitizationService
from app.services.database_connection_service import DatabaseConnectionService
from app.services.database_context_service import DatabaseContextService
from app.services.database_discovery_service import DatabaseDiscoveryService
from app.services.database_sampling_service import DatabaseSamplingService
from app.services.database_semantic_analyzer import DatabaseSemanticAnalyzer
from app.services.date_semantic_service import DateSemanticService
from app.services.dimension_service import DimensionService
from app.services.relationship_graph_service import RelationshipGraphService
from app.services.terminology_service import TerminologyService

logger = get_logger(__name__)


_STEP_NAMES: list[str] = [
    "ConnectionValidation",
    "SchemaDiscovery",
    "RelationshipDiscovery",
    "SampleDiscovery",
    "ColumnProfiling",
    "DataSanitization",
    "TableSemanticAnalysis",
    "BusinessDomainDiscovery",
    "BusinessEntityDiscovery",
    "BusinessConceptDiscovery",
    "MetricDiscovery",
    "DimensionDiscovery",
    "DateSemanticDiscovery",
    "TerminologyDiscovery",
    "RelationshipGraphCreation",
    "KnowledgeMapGeneration",
    "EmbeddingGeneration",
    "VectorIndexCreation",
    "ContextPersistence",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started_at: Optional[datetime], finished_at: Optional[datetime]) -> int:
    if started_at is None or finished_at is None:
        return 0
    delta = finished_at - started_at
    return max(0, int(delta.total_seconds() * 1000))


def _init_steps() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "elapsed_ms": 0,
        }
        for name in _STEP_NAMES
    ]


def _mark_step_running(step: dict[str, Any]) -> None:
    step["status"] = "running"
    step["started_at"] = _now_utc()
    step["error"] = None


def _mark_step_done(step: dict[str, Any]) -> None:
    step["status"] = "done"
    step["finished_at"] = _now_utc()
    step["elapsed_ms"] = _elapsed_ms(step["started_at"], step["finished_at"])


def _mark_step_error(step: dict[str, Any], error: Exception) -> None:
    step["status"] = "error"
    step["finished_at"] = _now_utc()
    step["error"] = str(error)
    step["elapsed_ms"] = _elapsed_ms(step["started_at"], step["finished_at"])


def _jsonable_for_jsonb(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable_for_jsonb(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable_for_jsonb(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, timezone):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def _extract_all_tables(metadata: Any) -> list[Any]:
    tables: list[Any] = []
    schemas_raw = getattr(metadata, "schemas", None)
    if isinstance(schemas_raw, dict):
        for schema_tables in schemas_raw.values():
            if isinstance(schema_tables, list):
                tables.extend(schema_tables)
            elif hasattr(schema_tables, "tables") and isinstance(schema_tables.tables, list):
                tables.extend(schema_tables.tables)
                if hasattr(schema_tables, "views") and isinstance(schema_tables.views, list):
                    tables.extend(schema_tables.views)
    elif isinstance(schemas_raw, list):
        for schema_obj in schemas_raw:
            if hasattr(schema_obj, "tables") and isinstance(schema_obj.tables, list):
                tables.extend(schema_obj.tables)
            if hasattr(schema_obj, "views") and isinstance(schema_obj.views, list):
                tables.extend(schema_obj.views)
    tables_attr = getattr(metadata, "tables", None)
    if not tables:
        if callable(tables_attr):
            try:
                t = tables_attr()
                if isinstance(t, list):
                    tables = list(t)
            except Exception:
                tables = []
        elif isinstance(tables_attr, list):
            tables = list(tables_attr)
    return tables


def _steps_to_context_status(steps: list[dict[str, Any]]) -> ContextStatus:
    running_idx = next((i for i, s in enumerate(steps) if s["status"] == "running"), None)
    if running_idx is not None:
        name = steps[running_idx]["name"]
        mapping: dict[str, ContextStatus] = {
            "ConnectionValidation": ContextStatus.CONNECTING,
            "SchemaDiscovery": ContextStatus.DISCOVERING,
            "RelationshipDiscovery": ContextStatus.DISCOVERING,
            "SampleDiscovery": ContextStatus.SAMPLING,
            "ColumnProfiling": ContextStatus.PROFILING,
            "DataSanitization": ContextStatus.PROFILING,
            "TableSemanticAnalysis": ContextStatus.SEMANTIC_ANALYZING,
            "BusinessDomainDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "BusinessEntityDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "BusinessConceptDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "MetricDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "DimensionDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "DateSemanticDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "TerminologyDiscovery": ContextStatus.SEMANTIC_ANALYZING,
            "RelationshipGraphCreation": ContextStatus.BUILDING_KNOWLEDGE_MAP,
            "KnowledgeMapGeneration": ContextStatus.BUILDING_KNOWLEDGE_MAP,
            "EmbeddingGeneration": ContextStatus.GENERATING_EMBEDDINGS,
            "VectorIndexCreation": ContextStatus.GENERATING_EMBEDDINGS,
            "ContextPersistence": ContextStatus.BUILDING_KNOWLEDGE_MAP,
        }
        return mapping.get(name, ContextStatus.DISCOVERING)
    has_error = any(s["status"] == "error" for s in steps)
    if has_error:
        return ContextStatus.ERROR
    all_done = all(s["status"] == "done" for s in steps)
    if all_done:
        return ContextStatus.READY
    return ContextStatus.IDLE


class TableSemanticAnalysis:
    def __init__(self, table: TableMetadata, column_semantics: list[ColumnSemantic]) -> None:
        self.table = table
        self.column_semantics = column_semantics


class DatabaseDiscoveryPipeline:
    def __init__(
        self,
        *,
        connection_service: Optional[DatabaseConnectionService] = None,
        discovery_service: Optional[DatabaseDiscoveryService] = None,
        sampling_service: Optional[DatabaseSamplingService] = None,
        profiling_service: Optional[ColumnProfilingService] = None,
        sanitization_service: Optional[DataSanitizationService] = None,
        semantic_analyzer: Optional[DatabaseSemanticAnalyzer] = None,
        domain_service: Optional[BusinessDomainService] = None,
        entity_service: Optional[BusinessEntityService] = None,
        concept_service: Optional[BusinessConceptService] = None,
        metric_service: Optional[BusinessMetricService] = None,
        dimension_service: Optional[DimensionService] = None,
        date_semantic_service: Optional[DateSemanticService] = None,
        terminology_service: Optional[TerminologyService] = None,
        graph_service: Optional[RelationshipGraphService] = None,
        context_service: Optional[DatabaseContextService] = None,
        context_repository: Optional[Any] = None,
    ) -> None:
        self.connection_service = connection_service or DatabaseConnectionService()
        self.discovery_service = discovery_service or DatabaseDiscoveryService()
        self.sampling_service = sampling_service or DatabaseSamplingService()
        self.profiling_service = profiling_service or ColumnProfilingService()
        self.sanitization_service = sanitization_service or DataSanitizationService()
        self.semantic_analyzer = semantic_analyzer or DatabaseSemanticAnalyzer()
        self.domain_service = domain_service or BusinessDomainService()
        self.entity_service = entity_service or BusinessEntityService()
        self.concept_service = concept_service or BusinessConceptService()
        self.metric_service = metric_service or BusinessMetricService()
        self.dimension_service = dimension_service or DimensionService()
        self.date_semantic_service = date_semantic_service or DateSemanticService()
        self.terminology_service = terminology_service or TerminologyService()
        self.graph_service = graph_service or RelationshipGraphService()
        self.context_repository = context_repository or ContextRepository()
        self.context_service = context_service or DatabaseContextService(
            context_repository=self.context_repository
        )
        self.connection_repository = (
            getattr(connection_service, "repository", None)
            or DatabaseConnectionRepository()
        )

    async def _persist_run(
        self,
        session: AsyncSession,
        connection_id: UUID,
        steps: list[dict[str, Any]],
        *,
        started_at: datetime,
        finished_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> None:
        status = _steps_to_context_status(steps)
        if finished_at is None and status in {ContextStatus.READY, ContextStatus.ERROR}:
            finished_at = _now_utc()
        steps_jsonable = _jsonable_for_jsonb(steps)
        try:
            from app.models.orm import DatabaseConnectionMetadata as ORMConn
            from app.core.security import encrypt_secret
            from app.models.database import DatabaseConnectionStatus
            import secrets
            exists_stmt = select(ORMConn).where(ORMConn.id == connection_id)
            existing = (await session.execute(exists_stmt)).scalar_one_or_none()
            if existing is None:
                salt = secrets.token_urlsafe(16)
                placeholder = encrypt_secret("UNKNOWN_PLACEHOLDER_PASSWORD")
                name = f"orphan-{connection_id!s:.8}"
                orm_placeholder = ORMConn(
                    id=connection_id,
                    connection_name=name,
                    database_type="unknown",
                    host="unknown",
                    port=0,
                    database_name="unknown",
                    username="unknown",
                    schema="public",
                    encrypted_password=placeholder,
                    encryption_salt=salt,
                    status=DatabaseConnectionStatus.CONNECTED.value,
                    last_connected_at=None,
                )
                session.add(orm_placeholder)
                try:
                    await session.flush()
                    logger.info(
                        "Created placeholder connection metadata row to satisfy FK during discovery run persist",
                        extra={"connection_id": str(connection_id)},
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to flush placeholder connection metadata during _persist_run; rolling back and retrying select",
                        exc_info=exc,
                        extra={"connection_id": str(connection_id)},
                    )
                    await session.rollback()
                    existing = (await session.execute(exists_stmt)).scalar_one_or_none()
                    if existing is None:
                        raise
        except Exception as _place_err:
            logger.warning(
                "Could not create placeholder connection metadata for discovery run persist",
                exc_info=_place_err,
                extra={"connection_id": str(connection_id)},
            )
        orm_run = ORMDiscoveryRun(
            connection_id=connection_id,
            steps={"steps": steps_jsonable},
            status=status.value,
            started_at=started_at,
            finished_at=finished_at,
            error_message=error,
        )
        session.add(orm_run)
        try:
            await session.flush()
        except Exception as exc:
            logger.warning(
                "Could not flush discovery run to DB",
                extra={"connection_id": str(connection_id)},
                exc_info=exc,
            )
        try:
            conn_status = (
                DatabaseConnectionStatus.READY
                if status == ContextStatus.READY
                else DatabaseConnectionStatus.ERROR
                if status == ContextStatus.ERROR
                else DatabaseConnectionStatus.DISCOVERED
            )
            await self.connection_repository.update_status(
                session, connection_id, conn_status
            )
        except Exception as exc:
            logger.warning(
                "Could not update connection status for discovery run",
                extra={"connection_id": str(connection_id)},
                exc_info=exc,
            )

    async def _run_step_body(
        self,
        idx: int,
        steps: list[dict[str, Any]],
        session: AsyncSession,
        connection_id: UUID,
        request: DatabaseConnectionRequest,
        force_refresh: bool,
        run_globals: dict[str, Any],
    ) -> None:
        step = steps[idx]
        _mark_step_running(step)
        name = step["name"]
        try:
            if name == "ConnectionValidation":
                await self.connection_service.test_connection(request)

            elif name == "SchemaDiscovery":
                metadata: DatabaseMetadata = await self.discovery_service.discover_schema(
                    session, connection_id
                )
                run_globals["metadata"] = metadata

            elif name == "RelationshipDiscovery":
                metadata = run_globals["metadata"]
                relationships = metadata.relationships or []
                logger.info(
                    "RelationshipDiscovery extracted relationships",
                    extra={"connection_id": str(connection_id), "count": len(relationships)},
                )

            elif name == "SampleDiscovery":
                metadata = run_globals["metadata"]
                conn_meta = await self.connection_service.get(
                    session, connection_id, decrypt_password=True
                )
                target_engine: Optional[AsyncEngine] = None
                target_config: Optional[DatabaseConfig] = None
                if conn_meta is not None:
                    try:
                        password = (
                            conn_meta.encrypted_password.decode("utf-8")
                            if isinstance(conn_meta.encrypted_password, (bytes, bytearray))
                            else conn_meta.encrypted_password
                            if isinstance(conn_meta.encrypted_password, str)
                            else request.password
                        )
                        connection_name = getattr(request, "connection_name", None) or getattr(
                            conn_meta, "connection_name", None
                        )
                        temp_req = DatabaseConnectionRequest(
                            database_type=conn_meta.database_type,
                            host=conn_meta.host,
                            port=conn_meta.port,
                            database_name=conn_meta.database_name,
                            username=conn_meta.username,
                            password=password,
                            schema=conn_meta.schema or request.schema or "public",
                            ssl_mode=request.ssl_mode,
                            connection_name=connection_name,
                        )
                        target_config = build_config_from_request(temp_req)
                        target_engine = await create_engine_from_config(
                            target_config, pool_size=4, max_overflow=8, pool_recycle=600
                        )
                    except Exception as exc:
                        logger.warning(
                            "Could not build target engine for sampling; attempting request-based fallback",
                            extra={"connection_id": str(connection_id)},
                            exc_info=exc,
                        )
                        target_engine = None
                if target_engine is None:
                    target_config = build_config_from_request(request)
                    target_engine = await create_engine_from_config(
                        target_config, pool_size=4, max_overflow=8, pool_recycle=600
                    )
                run_globals["target_engine"] = target_engine
                run_globals["target_config"] = target_config
                metadata = await self.sampling_service.sample_database(target_engine, metadata)
                run_globals["metadata"] = metadata

            elif name == "ColumnProfiling":
                metadata = run_globals["metadata"]
                target_engine = run_globals.get("target_engine")
                if target_engine is None:
                    conn_meta = await self.connection_service.get(
                        session, connection_id, decrypt_password=True
                    )
                    target_config = build_config_from_request(request)
                    target_engine = await create_engine_from_config(target_config)
                    run_globals["target_engine"] = target_engine
                metadata = await self.profiling_service.profile_database(target_engine, metadata)
                run_globals["metadata"] = metadata

            elif name == "DataSanitization":
                metadata = run_globals["metadata"]
                if settings.LLM_SANITIZE_SAMPLE_DATA:
                    metadata = self.sanitization_service.sanitize_table_samples(metadata)
                    run_globals["metadata"] = metadata

            elif name == "TableSemanticAnalysis":
                from app.models.semantic import ColumnSemantic as CanonicalColumnSemantic
                from app.models.semantic import SemanticType, PIILevel

                metadata = run_globals["metadata"]
                tables = _extract_all_tables(metadata)
                table_analyses: list[TableSemanticAnalysis] = []
                all_column_semantics: list[CanonicalColumnSemantic] = []
                for table in tables:
                    analysis = await self.semantic_analyzer.analyze_table_semantics(table)
                    raw_cols = getattr(analysis, "column_semantics", []) or []
                    normalized: list[CanonicalColumnSemantic] = []
                    for cs in raw_cols:
                        if isinstance(cs, CanonicalColumnSemantic):
                            normalized.append(cs)
                            continue
                        cname = getattr(cs, "name", None) or getattr(cs, "column_name", None)
                        if not cname:
                            continue
                        try:
                            stype_raw = getattr(cs, "semantic_type", "ATTRIBUTE")
                            if isinstance(stype_raw, SemanticType):
                                stype = stype_raw
                            else:
                                try:
                                    stype = SemanticType(str(stype_raw).upper())
                                except Exception:
                                    stype = SemanticType.ATTRIBUTE
                        except Exception:
                            stype = SemanticType.ATTRIBUTE
                        try:
                            pii_raw = getattr(cs, "pii_level", PIILevel.NONE)
                            if isinstance(pii_raw, PIILevel):
                                pii = pii_raw
                            else:
                                try:
                                    pii = PIILevel(str(pii_raw).upper())
                                except Exception:
                                    pii = PIILevel.NONE
                        except Exception:
                            pii = PIILevel.NONE
                        normalized.append(
                            CanonicalColumnSemantic(
                                schema_name=getattr(
                                    cs,
                                    "schema_name",
                                    getattr(table, "schema_name", ""),
                                ),
                                table_name=getattr(
                                    cs,
                                    "table_name",
                                    getattr(table, "table_name", ""),
                                ),
                                column_name=cname,
                                business_name=getattr(
                                    cs,
                                    "business_name",
                                    getattr(cs, "name", cname),
                                ),
                                description=getattr(cs, "description", ""),
                                semantic_type=stype,
                                entity_code=getattr(cs, "entity_code", None),
                                domain_code=getattr(cs, "domain_code", None),
                                concept_code=getattr(cs, "concept_code", None),
                                pii_level=pii,
                                confidence=float(getattr(cs, "confidence", 1.0) or 1.0),
                            )
                        )
                    all_column_semantics.extend(normalized)
                    try:
                        analysis.column_semantics = list(normalized)
                    except Exception:
                        pass
                    table_analyses.append(
                        TableSemanticAnalysis(table, list(normalized))
                    )
                run_globals["table_analyses"] = table_analyses
                run_globals["column_semantics"] = all_column_semantics

            elif name == "BusinessDomainDiscovery":
                metadata = run_globals["metadata"]
                table_analyses = run_globals.get("table_analyses", [])
                domains: list[BusinessDomain] = await self.domain_service.detect_domains(
                    metadata, table_analyses
                )
                run_globals["domains"] = domains

            elif name == "BusinessEntityDiscovery":
                metadata = run_globals["metadata"]
                domains = run_globals.get("domains", [])
                table_analyses = run_globals.get("table_analyses", [])
                entities: list[BusinessEntity] = await self.entity_service.detect_entities(
                    metadata, domains, table_analyses
                )
                run_globals["entities"] = entities

            elif name == "BusinessConceptDiscovery":
                metadata = run_globals["metadata"]
                entities = run_globals.get("entities", [])
                domains = run_globals.get("domains", [])
                table_analyses = run_globals.get("table_analyses", [])
                metrics_seed: list[BusinessMetric] = run_globals.get("metrics", [])
                concepts: list[BusinessConcept] = await self.concept_service.detect_concepts(
                    metadata, entities, domains, table_analyses, metrics_seed
                )
                run_globals["concepts"] = concepts

            elif name == "MetricDiscovery":
                metadata = run_globals["metadata"]
                entities = run_globals.get("entities", [])
                concepts = run_globals.get("concepts", [])
                domains = run_globals.get("domains", [])
                metrics: list[BusinessMetric] = await self.metric_service.detect_metrics(
                    metadata, entities, concepts, domains
                )
                run_globals["metrics"] = metrics
                if run_globals.get("concepts") and not run_globals.get("_concepts_second_pass_done"):
                    concepts2 = await self.concept_service.detect_concepts(
                        metadata, entities, domains, run_globals.get("table_analyses", []), metrics
                    )
                    if concepts2:
                        codes_existing = {c.code for c in run_globals["concepts"]}
                        for c in concepts2:
                            if c.code not in codes_existing:
                                run_globals["concepts"].append(c)
                    run_globals["_concepts_second_pass_done"] = True

            elif name == "DimensionDiscovery":
                metadata = run_globals["metadata"]
                entities = run_globals.get("entities", [])
                domains = run_globals.get("domains", [])
                profiles: dict[str, Any] = {}
                tables_list: list[TableMetadata] = list(_extract_all_tables(metadata))
                for t in tables_list:
                    if hasattr(t, "column_profiles") and t.column_profiles:
                        profiles[f"{getattr(t, 'schema_name', '')}.{getattr(t, 'table_name', '')}"] = t.column_profiles
                relationships = getattr(metadata, "relationships", []) or []
                dimensions: list[Dimension] = await self.dimension_service.detect_dimensions(
                    metadata, profiles, entities, domains, relationships
                )
                run_globals["dimensions"] = dimensions

            elif name == "DateSemanticDiscovery":
                metadata = run_globals["metadata"]
                table_analyses = run_globals.get("table_analyses", [])
                profiles: dict[str, Any] = {}
                samples: dict[str, Any] = {}
                tables_list = list(_extract_all_tables(metadata))
                for t in tables_list:
                    key = f"{getattr(t, 'schema_name', '')}.{getattr(t, 'table_name', '')}"
                    if hasattr(t, "column_profiles") and t.column_profiles:
                        profiles[key] = t.column_profiles
                    if hasattr(t, "sample_rows") and t.sample_rows:
                        samples[key] = t.sample_rows
                date_semantics: list[DateSemantic] = await self.date_semantic_service.detect_date_semantics(
                    metadata, table_analyses, profiles, samples
                )
                run_globals["date_semantics"] = date_semantics

            elif name == "TerminologyDiscovery":
                domains = run_globals.get("domains", [])
                entities = run_globals.get("entities", [])
                concepts = run_globals.get("concepts", [])
                metrics = run_globals.get("metrics", [])
                dimensions = run_globals.get("dimensions", [])
                date_semantics = run_globals.get("date_semantics", [])
                terminology: list[TerminologyEntry] = await self.terminology_service.build_terminology(
                    domains, entities, concepts, metrics, dimensions, date_semantics
                )
                run_globals["terminology"] = terminology

            elif name == "RelationshipGraphCreation":
                metadata = run_globals["metadata"]
                relationships = getattr(metadata, "relationships", []) or []
                graph: RelationshipGraph = await self.graph_service.build_graph(
                    metadata, relationships
                )
                run_globals["graph"] = graph

            elif name == "KnowledgeMapGeneration":
                metadata = run_globals["metadata"]
                domains = run_globals.get("domains", [])
                entities = run_globals.get("entities", [])
                concepts = run_globals.get("concepts", [])
                metrics = run_globals.get("metrics", [])
                dimensions = run_globals.get("dimensions", [])
                column_semantics = run_globals.get("column_semantics", [])
                relationships = getattr(metadata, "relationships", []) or []
                graph = run_globals.get("graph", RelationshipGraph(nodes=[], edges=[]))
                date_semantics = run_globals.get("date_semantics", [])
                enum_values: list[EnumValue] = []
                terminology = run_globals.get("terminology", [])
                tables_list: list[TableMetadata] = list(_extract_all_tables(metadata))
                metadata_for_kmap = DatabaseMetadata(
                    database_type=getattr(metadata, "database_type", "postgresql"),
                    database_name=getattr(metadata, "database_name", ""),
                    schemas=[],
                    relationships=relationships,
                )
                schemas_by_name: dict[str, list[TableMetadata]] = {}
                for t in tables_list:
                    schema = getattr(t, "schema_name", "") or "public"
                    if schema not in schemas_by_name:
                        schemas_by_name[schema] = []
                    schemas_by_name[schema].append(t)
                if hasattr(metadata_for_kmap, "schemas") and isinstance(metadata_for_kmap.schemas, list):
                    from app.models.database import SchemaMetadata

                    metadata_for_kmap.schemas = [
                        SchemaMetadata(schema_name=schema_name, tables=tables)
                        for schema_name, tables in schemas_by_name.items()
                    ]
                elif hasattr(metadata_for_kmap, "schemas"):
                    metadata_for_kmap.schemas = schemas_by_name
                knowledge_map: DatabaseKnowledgeMap = await self.context_service.build_knowledge_map(
                    connection_id,
                    metadata_for_kmap,
                    domains,
                    entities,
                    concepts,
                    metrics,
                    dimensions,
                    column_semantics,
                    relationships,
                    graph,
                    date_semantics,
                    enum_values,
                    terminology,
                )
                run_globals["knowledge_map"] = knowledge_map

            elif name == "EmbeddingGeneration":
                knowledge_map = run_globals["knowledge_map"]
                context_version_id = knowledge_map.id
                if context_version_id is None:
                    logger.warning(
                        "KnowledgeMap missing id; skipping embedding persistence",
                        extra={"connection_id": str(connection_id)},
                    )
                else:
                    embedding_repo = EmbeddingRepository(session)
                    texts: list[str] = []
                    rows: list[dict[str, Any]] = []
                    for d in knowledge_map.domains:
                        txt = build_document_for_embedding(
                            type="DOMAIN",
                            name=d.name,
                            description=d.description or "",
                            aliases=d.aliases,
                            tables=d.tables if isinstance(d.tables, list) else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "domain",
                            "content_id": str(d.id) if d.id else d.code,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {"code": d.code},
                        })
                    for e in knowledge_map.entities:
                        txt = build_document_for_embedding(
                            type="ENTITY",
                            name=e.name,
                            description=e.description or "",
                            aliases=e.aliases,
                            tables=e.tables if isinstance(e.tables, list) else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "entity",
                            "content_id": str(e.id) if e.id else e.code,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {"code": e.code, "domain_code": e.domain_code},
                        })
                    for c in knowledge_map.concepts:
                        txt = build_document_for_embedding(
                            type="CONCEPT",
                            name=c.name,
                            description=c.description or "",
                            aliases=c.aliases,
                            tables=c.tables if isinstance(c.tables, list) else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "concept",
                            "content_id": str(c.id) if c.id else c.code,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {"code": c.code, "entity_code": c.entity_code},
                        })
                    for m in knowledge_map.metrics:
                        txt = build_document_for_embedding(
                            type="METRIC",
                            name=m.name,
                            description=m.description or "",
                            aliases=m.aliases,
                            tables=m.source_tables if isinstance(m.source_tables, list) else None,
                            columns=[m.source_column] if m.source_column else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "metric",
                            "content_id": str(m.id) if m.id else m.code,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {
                                "code": m.code,
                                "aggregation": (
                                    m.aggregation.value
                                    if hasattr(m.aggregation, "value")
                                    else str(m.aggregation)
                                ),
                            },
                        })
                    for dim in knowledge_map.dimensions:
                        txt = build_document_for_embedding(
                            type="DIMENSION",
                            name=dim.name,
                            description=dim.description or "",
                            aliases=dim.aliases if isinstance(dim.aliases, list) else None,
                            tables=[dim.source_table] if dim.source_table else None,
                            columns=[dim.source_column] if dim.source_column else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "dimension",
                            "content_id": str(dim.id) if dim.id else dim.code,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {
                                "code": dim.code,
                                "dimension_type": (
                                    dim.dimension_type.value
                                    if hasattr(dim.dimension_type, "value")
                                    else str(dim.dimension_type)
                                ),
                            },
                        })
                    for table in knowledge_map.tables:
                        schema = getattr(table, "schema_name", "")
                        tname = getattr(table, "table_name", "")
                        full_table = f"{schema}.{tname}" if schema else tname
                        txt = build_document_for_embedding(
                            type="TABLE",
                            name=full_table,
                            description=getattr(table, "comment", "") or "",
                            aliases=[],
                            tables=[full_table],
                            columns=[getattr(c, "name", "") for c in getattr(table, "columns", [])],
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "table",
                            "content_id": full_table,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {"schema": schema, "table": tname},
                        })
                        for col in getattr(table, "columns", []) or []:
                            col_name = getattr(col, "name", "")
                            ctxt = build_document_for_embedding(
                                type="COLUMN",
                                name=f"{full_table}.{col_name}",
                                description=getattr(col, "comment", "") or "",
                                aliases=[],
                                tables=[full_table],
                                columns=[col_name],
                            )
                            texts.append(ctxt)
                            rows.append({
                                "context_version_id": context_version_id,
                                "content_type": "column",
                                "content_id": f"{full_table}.{col_name}",
                                "content_text": ctxt,
                                "embedding": [],
                                "metadata": {
                                    "schema": schema,
                                    "table": tname,
                                    "column": col_name,
                                    "type": (
                                        col.type.value
                                        if hasattr(col.type, "value")
                                        else str(col.type)
                                    ),
                                },
                            })
                    for t in knowledge_map.terminology:
                        txt = build_document_for_embedding(
                            type="TERMINOLOGY",
                            name=t.term,
                            description=(
                                f"Canonical: {t.canonical_form}. Target: {t.target_code}"
                            ),
                            aliases=t.aliases if isinstance(t.aliases, list) else None,
                        )
                        texts.append(txt)
                        rows.append({
                            "context_version_id": context_version_id,
                            "content_type": "terminology",
                            "content_id": str(t.id) if t.id else t.term,
                            "content_text": txt,
                            "embedding": [],
                            "metadata": {
                                "canonical_form": t.canonical_form,
                                "target_code": t.target_code,
                            },
                        })
                    if texts:
                        vectors = await async_embed_texts(texts)
                        for i, vec in enumerate(vectors):
                            if i < len(rows):
                                rows[i]["embedding"] = list(vec)
                        await embedding_repo.bulk_upsert(rows)

            elif name == "VectorIndexCreation":
                metadata_engine: Optional[AsyncEngine] = None
                try:
                    metadata_engine = get_metadata_engine()
                except DBAgentException:
                    metadata_engine = None
                if metadata_engine is None:
                    logger.warning(
                        "Metadata engine not initialized; skipping vector index creation",
                        extra={"connection_id": str(connection_id)},
                    )
                else:
                    indexes = [
                        ("database_tables_metadata", "embedding", "ix_tables_metadata_embedding_hnsw"),
                        ("database_columns_metadata", "embedding", "ix_columns_metadata_embedding_hnsw"),
                        ("database_business_domains", "embedding", "ix_business_domains_embedding_hnsw"),
                        ("database_business_entities", "embedding", "ix_business_entities_embedding_hnsw"),
                        ("database_business_concepts", "embedding", "ix_business_concepts_embedding_hnsw"),
                        ("database_metrics", "embedding", "ix_metrics_embedding_hnsw"),
                        ("database_dimensions", "embedding", "ix_dimensions_embedding_hnsw"),
                        ("database_terminology", "embedding", "ix_terminology_embedding_hnsw"),
                        ("database_embeddings", "embedding", "ix_embeddings_embedding_hnsw"),
                    ]
                    async with metadata_engine.begin() as conn:
                        for table, column, idx_name in indexes:
                            try:
                                sql = text(
                                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON public.{table} "
                                    f"USING hnsw ({column} vector_cosine_ops) "
                                    f"WITH (m = 16, ef_construction = 64)"
                                )
                                await conn.execute(sql)
                            except Exception as exc:
                                logger.warning(
                                    "Could not create vector HNSW index",
                                    extra={
                                        "connection_id": str(connection_id),
                                        "table": table,
                                        "column": column,
                                        "index": idx_name,
                                    },
                                    exc_info=exc,
                                )
                        await conn.commit()

            elif name == "ContextPersistence":
                knowledge_map = run_globals.get("knowledge_map")
                if knowledge_map is not None:
                    knowledge_map.status = KnowledgeMapStatus.READY
                    saved = False
                    try:
                        saved = await self.context_service.save_knowledge_map(
                            knowledge_map, session=session
                        )
                    except Exception as exc:
                        logger.warning(
                            "save_knowledge_map failed; attempting repository fallback",
                            extra={"connection_id": str(connection_id)},
                            exc_info=exc,
                        )
                    if not saved and self.context_repository is not None:
                        try:
                            if hasattr(self.context_repository, "save_context_full"):
                                await self.context_repository.save_context_full(
                                    session=session,
                                    connection_id=connection_id,
                                    knowledge_map=knowledge_map,
                                )
                                await session.flush()
                                saved = True
                            elif hasattr(self.context_repository, "save_knowledge_map"):
                                await self.context_repository.save_knowledge_map(
                                    session=session,
                                    knowledge_map=knowledge_map,
                                )
                                await session.flush()
                                saved = True
                        except Exception as exc:
                            logger.warning(
                                "Repository fallback save failed",
                                extra={"connection_id": str(connection_id)},
                                exc_info=exc,
                            )
                    if not saved:
                        logger.warning(
                            "Context persistence did not persist knowledge map to repository; run may not be queryable via endpoints",
                            extra={"connection_id": str(connection_id)},
                        )

            else:
                logger.warning(
                    "Unknown pipeline step name; no-op",
                    extra={"step_name": name, "connection_id": str(connection_id)},
                )

            _mark_step_done(step)
        except Exception as exc:
            _mark_step_error(step, exc)
            raise

    async def run(
        self,
        connection_id: UUID,
        request: DatabaseConnectionRequest,
        force_refresh: bool = False,
    ) -> tuple[DatabaseKnowledgeMap, DatabaseContextStatus]:
        started_at = _now_utc()
        steps = _init_steps()
        run_globals: dict[str, Any] = {}
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            try:
                try:
                    await self._persist_run(session, connection_id, steps, started_at=started_at)
                    await session.commit()
                except Exception as prep_exc:
                    logger.warning(
                        "Could not persist initial discovery run",
                        extra={"connection_id": str(connection_id)},
                        exc_info=prep_exc,
                    )
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                for idx in range(len(_STEP_NAMES)):
                    await self._run_step_body(
                        idx, steps, session, connection_id, request, force_refresh, run_globals
                    )
                    try:
                        await self._persist_run(
                            session, connection_id, steps, started_at=started_at
                        )
                        await session.commit()
                    except Exception as persist_exc:
                        logger.warning(
                            "Per-step discovery persist/commit failed",
                            extra={"connection_id": str(connection_id), "step": _STEP_NAMES[idx]},
                            exc_info=persist_exc,
                        )
                        try: await session.rollback()
                        except Exception: pass
                knowledge_map: Optional[DatabaseKnowledgeMap] = run_globals.get("knowledge_map")
                if knowledge_map is None:
                    raise DatabaseDiscoveryError(
                        detail="Knowledge map was not generated by pipeline",
                        extra={"connection_id": str(connection_id)},
                    )
                status_enum = _steps_to_context_status(steps)
                ctx_status = DatabaseContextStatus(
                    status=status_enum,
                    current_step=None,
                    completed_steps=sum(1 for s in steps if s["status"] == "done"),
                    total_steps=len(steps),
                    error_message=None,
                    started_at=started_at,
                    finished_at=_now_utc(),
                    context_version=knowledge_map.context_version,
                )
                await self._persist_run(session, connection_id, steps, started_at=started_at)
                await session.commit()
                return knowledge_map, ctx_status
            except Exception as exc:
                target_engine = run_globals.get("target_engine")
                if target_engine is not None:
                    try:
                        await close_engine(target_engine)
                    except Exception:
                        pass
                error_msg = str(exc)
                try:
                    await session.rollback()
                except Exception:
                    pass
                try:
                    await self._persist_run(
                        session,
                        connection_id,
                        steps,
                        started_at=started_at,
                        error=error_msg,
                    )
                    await session.commit()
                except Exception as inner_exc:
                    logger.warning(
                        "Failed to persist failed discovery run",
                        extra={"connection_id": str(connection_id)},
                        exc_info=inner_exc,
                    )
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                if isinstance(exc, DBAgentException):
                    raise
                raise DatabaseDiscoveryError(
                    detail=f"Discovery pipeline failed: {error_msg}",
                    extra={
                        "connection_id": str(connection_id),
                        "failed_step": next(
                            (s["name"] for s in steps if s["status"] == "error"), None
                        ),
                    },
                ) from exc
            finally:
                target_engine = run_globals.get("target_engine")
                if target_engine is not None:
                    try:
                        await close_engine(target_engine)
                    except Exception:
                        pass

    async def get_status(self, connection_id: UUID) -> DatabaseContextStatus:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            try:
                stmt = (
                    select(ORMDiscoveryRun)
                    .where(ORMDiscoveryRun.connection_id == connection_id)
                    .order_by(ORMDiscoveryRun.started_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                run = result.scalar_one_or_none()
                steps_list: list[dict[str, Any]] = []
                started_at: Optional[datetime] = None
                finished_at: Optional[datetime] = None
                error_message: Optional[str] = None
                if run is None:
                    steps_list = _init_steps()
                    status_enum = ContextStatus.IDLE
                else:
                    steps_raw = run.steps or {}
                    steps_list = steps_raw.get("steps", []) if isinstance(steps_raw, dict) else []
                    if not steps_list:
                        steps_list = _init_steps()
                    status_enum = _steps_to_context_status(steps_list)
                    started_at = run.started_at
                    finished_at = run.finished_at
                    error_message = run.error_message or next(
                        (s["error"] for s in steps_list if s["status"] == "error"), None
                    )

                context_version: Optional[str] = None
                try:
                    ctx_service: Optional[DatabaseContextService] = getattr(self, "context_service", None)
                    ctx_repo: Any = getattr(self, "context_repository", None)
                    km: Optional[DatabaseKnowledgeMap] = None
                    if ctx_service is not None and hasattr(ctx_service, "get_latest"):
                        km = await ctx_service.get_latest(connection_id, session)
                    elif ctx_repo is not None and hasattr(ctx_repo, "get_latest_context_version"):
                        km = await ctx_repo.get_latest_context_version(session, connection_id)
                    if km is not None:
                        context_version = getattr(km, "context_version", None)
                        km_status = getattr(km, "status", None)
                        km_ready = (
                            km_status == KnowledgeMapStatus.READY
                            or km_status == ContextStatus.READY
                            or (hasattr(km_status, "value") and km_status.value == "READY")
                            or str(km_status).upper() == "READY"
                            or (getattr(km, "id", None) is not None)
                        )
                        if km_ready and status_enum in {ContextStatus.IDLE, ContextStatus.CONNECTING}:
                            status_enum = ContextStatus.READY
                            completed = len(_STEP_NAMES)
                            for s in steps_list:
                                if s.get("status") != "done":
                                    s["status"] = "done"
                            finished_at = finished_at or _now_utc()
                            error_message = None
                except Exception as km_exc:
                    logger.warning(
                        "get_status kmap lookup failed",
                        extra={"connection_id": str(connection_id)},
                        exc_info=km_exc,
                    )

                current_step = next(
                    (s["name"] for s in steps_list if s.get("status") == "running"), None
                )
                if not error_message:
                    error_message = next(
                        (s.get("error") for s in steps_list if s.get("status") == "error"), None
                    )
                completed_steps = sum(1 for s in steps_list if s.get("status") == "done")
                return DatabaseContextStatus(
                    status=status_enum,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    total_steps=len(_STEP_NAMES),
                    error_message=error_message,
                    started_at=started_at,
                    finished_at=finished_at,
                    context_version=context_version,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to get discovery status",
                    extra={"connection_id": str(connection_id)},
                    exc_info=exc,
                )
                return DatabaseContextStatus(
                    status=ContextStatus.ERROR,
                    total_steps=len(_STEP_NAMES),
                    error_message=str(exc),
                )


__all__ = ["DatabaseDiscoveryPipeline"]
