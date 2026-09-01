from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import InvalidInputError, SemanticAnalysisError, ContextNotFoundError
from app.core.logging import get_logger
from app.database.connection import close_engine
from app.models.context import (
    DatabaseKnowledgeMap,
    KnowledgeMapStatus,
)
from app.models.database import (
    ColumnMetadata,
    DatabaseMetadata,
    RelationshipMetadata,
    TableMetadata,
)
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

logger = get_logger(__name__)


def _table_sort_key(table: TableMetadata) -> tuple[str, str]:
    return (table.schema_name or "", table.table_name or "")


def _column_sort_key(col: ColumnMetadata) -> str:
    return col.name or ""


class DatabaseContextService:
    def __init__(
        self,
        context_repository: Any = None,
        llm_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        self.context_repository = context_repository
        self.llm_model = llm_model or settings.OPENAI_MODEL
        self.embedding_model = embedding_model or settings.OPENAI_EMBEDDING_MODEL
        logger.info(
            "DatabaseContextService initialized",
            extra={
                "has_repository": context_repository is not None,
                "llm_model": self.llm_model,
                "embedding_model": self.embedding_model,
            },
        )

    async def get_schema(self, config: Any) -> DatabaseMetadata:
        from app.database.adapters.base import DatabaseAdapter

        adapter_cls = DatabaseAdapter.by_database_type(config.database_type)
        adapter = adapter_cls(config)
        try:
            return await adapter.extract_schema()
        finally:
            engine = getattr(adapter, "_engine", None)
            if engine is not None:
                await close_engine(engine)

    async def sample_rows(
        self,
        metadata: DatabaseMetadata,
        config: Any,
        *,
        max_rows: Optional[int] = None,
    ) -> DatabaseMetadata:
        rows = max_rows or settings.DATABASE_SAMPLE_ROWS
        for schema in metadata.schemas:
            for collection in (schema.tables, schema.views):
                for table in collection:
                    table.sample_rows = []
        return metadata

    def _serialize_structure(
        self,
        metadata: DatabaseMetadata,
        domains: list[BusinessDomain] | None,
        entities: list[BusinessEntity] | None,
        concepts: list[BusinessConcept] | None,
        metrics: list[BusinessMetric] | None,
        dimensions: list[Dimension] | None,
        column_semantics: list[ColumnSemantic] | None,
        relationships: list[RelationshipMetadata] | None,
        date_semantics: list[DateSemantic] | None,
        enum_values: list[EnumValue] | None,
    ) -> dict[str, Any]:
        table_structures: list[dict[str, Any]] = []
        for schema in sorted(metadata.schemas, key=lambda s: s.schema_name or ""):
            all_sch_tables: list[TableMetadata] = []
            all_sch_tables.extend(schema.tables or [])
            all_sch_tables.extend(schema.views or [])
            for t in sorted(all_sch_tables, key=_table_sort_key):
                column_structs: list[dict[str, Any]] = []
                for col in sorted(t.columns, key=_column_sort_key):
                    column_structs.append(
                        {
                            "name": col.name,
                            "type": col.type.value if hasattr(col.type, "value") else str(col.type),
                            "pk": bool(col.is_pk),
                            "fk": bool(col.is_fk),
                            "fk_target": col.fk_target_table,
                        }
                    )
                table_structures.append(
                    {
                        "schema": t.schema_name,
                        "table": t.table_name,
                        "cols": column_structs,
                    }
                )

        rel_structures: list[dict[str, Any]] = []
        for rel in sorted(
            relationships or [],
            key=lambda r: (
                r.source_schema, r.source_table,
                ",".join(r.source_columns or []),
                r.target_schema, r.target_table,
            ),
        ):
            rel_structures.append(
                {
                    "src": f"{rel.source_schema}.{rel.source_table}",
                    "src_cols": list(rel.source_columns or []),
                    "tgt": f"{rel.target_schema}.{rel.target_table}",
                    "tgt_cols": list(rel.target_columns or []),
                }
            )

        semantic_hash_parts: list[dict[str, Any]] = []
        for d in sorted(domains or [], key=lambda x: x.code):
            semantic_hash_parts.append({"kind": "domain", "code": d.code, "tables": sorted(d.tables)})
        for e in sorted(entities or [], key=lambda x: x.code):
            semantic_hash_parts.append({"kind": "entity", "code": e.code, "tables": sorted(e.tables), "primary": e.primary_table})
        for c in sorted(concepts or [], key=lambda x: x.code):
            semantic_hash_parts.append({"kind": "concept", "code": c.code, "entity": c.entity_code, "cond": c.condition_sql})
        for m in sorted(metrics or [], key=lambda x: x.code):
            semantic_hash_parts.append({"kind": "metric", "code": m.code, "agg": m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation), "col": m.source_column})
        for dim in sorted(dimensions or [], key=lambda x: x.code):
            semantic_hash_parts.append({"kind": "dimension", "code": dim.code, "type": dim.dimension_type.value if hasattr(dim.dimension_type, "value") else str(dim.dimension_type), "table": dim.source_table, "col": dim.source_column})
        for ds in sorted(date_semantics or [], key=lambda d: (getattr(d, "table_name", ""), getattr(d, "column_name", ""))):
            role = getattr(ds, "semantic_role", "")
            role_val = getattr(role, "value", role) if hasattr(role, "value") else str(role)
            semantic_hash_parts.append({"kind": "date_sem", "table": getattr(ds, "table_name", ""), "col": getattr(ds, "column_name", ""), "role": role_val})

        def _cs_sort_key(c: Any) -> tuple[str, str, str]:
            if hasattr(c, "schema_name"):
                return (
                    str(getattr(c, "schema_name", "")),
                    str(getattr(c, "table_name", "")),
                    str(getattr(c, "column_name", "")),
                )
            return (
                str(getattr(c, "schema_json", "")),
                str(getattr(c, "table_json", "")),
                str(getattr(c, "name", "")),
            )

        def _cs_stype(c: Any) -> str:
            st = getattr(c, "semantic_type", "ATTRIBUTE")
            if hasattr(st, "value"):
                return str(st.value)
            return str(st)

        for cs in sorted(column_semantics or [], key=_cs_sort_key):
            sname = _cs_sort_key(cs)
            semantic_hash_parts.append({
                "kind": "col_sem",
                "table": f"{sname[0]}.{sname[1]}",
                "col": sname[2],
                "type": _cs_stype(cs),
            })
        for ev in sorted(enum_values or [], key=lambda e: (e.table_name, e.column_name)):
            semantic_hash_parts.append({"kind": "enum", "table": ev.table_name, "col": ev.column_name, "values": sorted(ev.values)})

        return {
            "db_name": metadata.database_name,
            "db_type": metadata.database_type,
            "tables": table_structures,
            "relationships": rel_structures,
            "semantics": semantic_hash_parts,
        }

    def _compute_schema_hash(
        self,
        structure: dict[str, Any],
    ) -> str:
        raw = json.dumps(structure, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return digest

    def _build_context_version(self, schema_hash: str) -> str:
        short = schema_hash[:8]
        return f"v1-{short}"

    async def build_knowledge_map(
        self,
        connection_id: UUID,
        metadata: DatabaseMetadata,
        domains: list[BusinessDomain] | None = None,
        entities: list[BusinessEntity] | None = None,
        concepts: list[BusinessConcept] | None = None,
        metrics: list[BusinessMetric] | None = None,
        dimensions: list[Dimension] | None = None,
        column_semantics: list[ColumnSemantic] | None = None,
        relationships: list[RelationshipMetadata] | None = None,
        graph: RelationshipGraph | None = None,
        date_semantics: list[DateSemantic] | None = None,
        enum_values: list[EnumValue] | None = None,
        terminology: list[TerminologyEntry] | None = None,
        **_extra: Any,
    ) -> DatabaseKnowledgeMap:
        if not connection_id:
            raise InvalidInputError(
                detail="connection_id is required to build knowledge map",
                field="connection_id",
            )
        if not metadata:
            raise InvalidInputError(
                detail="metadata is required to build knowledge map",
                field="metadata",
            )

        logger.info(
            "Building database knowledge map",
            extra={
                "connection_id": str(connection_id),
                "db_name": metadata.database_name,
                "domains": len(domains or []),
                "entities": len(entities or []),
                "concepts": len(concepts or []),
                "metrics": len(metrics or []),
                "dimensions": len(dimensions or []),
            },
        )

        try:
            structure = self._serialize_structure(
                metadata=metadata,
                domains=domains,
                entities=entities,
                concepts=concepts,
                metrics=metrics,
                dimensions=dimensions,
                column_semantics=column_semantics,
                relationships=relationships,
                date_semantics=date_semantics,
                enum_values=enum_values,
            )
            schema_hash = self._compute_schema_hash(structure)
            context_version = self._build_context_version(schema_hash)

            tables_flat: list[TableMetadata] = []
            for schema in metadata.schemas:
                tables_flat.extend(schema.tables or [])
                tables_flat.extend(schema.views or [])

            all_relationships: list[RelationshipMetadata] = list(relationships or []) + list(metadata.relationships or [])

            if graph is None:
                graph = RelationshipGraph(nodes=[], edges=[])

            kmap = DatabaseKnowledgeMap(
                id=None,
                database_connection_id=connection_id,
                context_version=context_version,
                schema_hash=schema_hash,
                database_summary="",
                domains=list(domains or []),
                entities=list(entities or []),
                concepts=list(concepts or []),
                metrics=list(metrics or []),
                dimensions=list(dimensions or []),
                tables=tables_flat,
                column_semantics=list(column_semantics or []),
                relationships=all_relationships,
                relationship_graph=graph,
                date_semantics=list(date_semantics or []),
                enum_values=list(enum_values or []),
                join_paths=[],
                terminology=list(terminology or []),
                llm_model=self.llm_model,
                embedding_model=self.embedding_model,
                created_at=datetime.now(timezone.utc),
                status=KnowledgeMapStatus.BUILDING,
            )

            kmap.database_summary = self.generate_summary(kmap)
            kmap.status = KnowledgeMapStatus.READY

            logger.info(
                "Built knowledge map",
                extra={
                    "connection_id": str(connection_id),
                    "context_version": kmap.context_version,
                    "schema_hash": kmap.schema_hash[:12] + "...",
                    "tables": len(kmap.tables),
                    "relationships": len(kmap.relationships),
                },
            )
            return kmap

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Failed to build knowledge map",
                exc_info=exc,
                extra={"error_type": type(exc).__name__, "connection_id": str(connection_id)},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to build knowledge map: {exc!s}",
                extra={"stage": "knowledge_map_build", "connection_id": str(connection_id)},
            ) from exc

    async def save_knowledge_map(
        self,
        knowledge_map: DatabaseKnowledgeMap,
        session: Any = None,
        **kwargs: Any,
    ) -> bool:
        if not knowledge_map:
            raise InvalidInputError(
                detail="knowledge_map is required to persist",
                field="knowledge_map",
            )
        if not self.context_repository:
            logger.warning("save_knowledge_map skipped: no context_repository configured")
            return False

        async def _save(sess: Any) -> None:
            if hasattr(self.context_repository, "save_context_full"):
                await self.context_repository.save_context_full(
                    session=sess,
                    connection_id=knowledge_map.database_connection_id,
                    knowledge_map=knowledge_map,
                    **kwargs,
                )
            elif hasattr(self.context_repository, "save_knowledge_map"):
                repo = self.context_repository
                m = getattr(repo, "save_knowledge_map")
                sig_params = set()
                try:
                    import inspect

                    sig = inspect.signature(m)
                    sig_params = set(sig.parameters.keys())
                except Exception:
                    pass
                if "session" in sig_params:
                    await m(
                        session=sess,
                        knowledge_map=knowledge_map,
                        **kwargs,
                    )
                else:
                    await m(knowledge_map=knowledge_map, **kwargs)
            else:
                logger.debug(
                    "context_repository has no save_context_full/save_knowledge_map; stored in memory only"
                )

        try:
            if session is not None:
                await _save(session)
            else:
                from app.database.session import get_async_session_factory

                sf = get_async_session_factory()
                async with sf() as inner:
                    await _save(inner)
                    await inner.commit()
            logger.info(
                "Persisted knowledge map",
                extra={
                    "connection_id": str(knowledge_map.database_connection_id),
                    "context_version": knowledge_map.context_version,
                },
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist knowledge map",
                exc_info=exc,
                extra={
                    "error_type": type(exc).__name__,
                    "connection_id": str(knowledge_map.database_connection_id),
                },
            )
            raise SemanticAnalysisError(
                detail=f"Failed to save knowledge map: {exc!s}",
                extra={"stage": "knowledge_map_save"},
            ) from exc

    async def get_latest(
        self,
        connection_id: UUID,
        session: Any = None,
    ) -> Optional[DatabaseKnowledgeMap]:
        if not connection_id:
            raise InvalidInputError(
                detail="connection_id is required to get latest knowledge map",
                field="connection_id",
            )
        if not self.context_repository:
            logger.warning("get_latest: no context_repository configured, returning None")
            return None
        try:
            if hasattr(self.context_repository, "get_latest_context_version"):
                return await self.context_repository.get_latest_context_version(
                    session=session,
                    connection_id=connection_id,
                )
            if hasattr(self.context_repository, "get_latest_knowledge_map"):
                return await self.context_repository.get_latest_knowledge_map(
                    session=session,
                    connection_id=connection_id,
                )
            logger.debug("context_repository has no getter for latest knowledge map")
            return None
        except ContextNotFoundError:
            raise
        except Exception as exc:
            logger.error(
                "Failed to retrieve latest knowledge map",
                exc_info=exc,
                extra={
                    "error_type": type(exc).__name__,
                    "connection_id": str(connection_id),
                },
            )
            raise ContextNotFoundError(
                detail=f"Failed to retrieve latest knowledge map: {exc!s}",
                resource_type="DatabaseKnowledgeMap",
                resource_id=str(connection_id),
            ) from exc

    def generate_summary(self, kmap: DatabaseKnowledgeMap) -> str:
        if not kmap:
            return "Empty knowledge map"
        tables = kmap.tables or []
        total_columns = sum(len(t.columns) for t in tables)
        rels = len(kmap.relationships or [])
        top_domains = sorted(kmap.domains or [], key=lambda d: -len(d.tables))[:3]
        top_entities = sorted(kmap.entities or [], key=lambda e: -len(e.tables))[:3]
        top_metrics = sorted(kmap.metrics or [], key=lambda m: -m.confidence)[:4]
        top_concepts = sorted(kmap.concepts or [], key=lambda c: -c.confidence)[:4]

        parts: list[str] = []
        parts.append(
            f"Database '{kmap.database_connection_id}' contains {len(tables)} tables "
            f"with {total_columns} columns and {rels} foreign-key relationships."
        )
        if top_domains:
            names = ", ".join(d.name for d in top_domains)
            parts.append(f"Business domains: {names}.")
        if top_entities:
            names = ", ".join(e.name for e in top_entities)
            parts.append(f"Key entities: {names}.")
        if top_metrics:
            names = ", ".join(m.name for m in top_metrics)
            parts.append(f"Core metrics: {names}.")
        if top_concepts:
            names = ", ".join(c.name for c in top_concepts)
            parts.append(f"Concepts: {names}.")
        parts.append(f"Context version: {kmap.context_version}.")
        return " ".join(parts)
