from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.context import (
    DatabaseKnowledgeMap,
    KnowledgeMapStatus,
    RelationshipGraph,
)
from app.models.database import (
    ColumnMetadata,
    ColumnType,
    DatabaseConnectionStatus,
    RelationshipMetadata,
    RelationshipType,
    TableMetadata,
    TableType,
)
from app.models.orm import (
    DatabaseBusinessConcept as ORMConcept,
    DatabaseBusinessDomain as ORMDomain,
    DatabaseBusinessEntity as ORMEntity,
    DatabaseColumnMetadata as ORMColumn,
    DatabaseContextVersion as ORMContextVersion,
    DatabaseDimension as ORMDimension,
    DatabaseEmbedding as ORMEmbedding,
    DatabaseMetric as ORMMetric,
    DatabaseRelationship as ORMRelationship,
    DatabaseTableMetadata as ORMTable,
    DatabaseTerminology as ORMTerminology,
)
from app.models.semantic import (
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
    DateSemantic,
    DateSemanticRole,
    Dimension,
    DimensionType,
    TerminologyEntry,
    TerminologyEntryType,
    AggregationType,
)

logger = get_logger(__name__)


def _shortid(length: int = 8) -> str:
    return secrets.token_hex(length // 2)


def _infer_column_type(raw_type: str) -> ColumnType:
    upper = (raw_type or "").strip().upper()
    mapping = {
        "INT": ColumnType.INTEGER,
        "INTEGER": ColumnType.INTEGER,
        "INT4": ColumnType.INTEGER,
        "BIGINT": ColumnType.BIGINT,
        "INT8": ColumnType.BIGINT,
        "SMALLINT": ColumnType.SMALLINT,
        "INT2": ColumnType.SMALLINT,
        "TEXT": ColumnType.TEXT,
        "VARCHAR": ColumnType.VARCHAR,
        "CHARACTER VARYING": ColumnType.VARCHAR,
        "CHAR": ColumnType.CHAR,
        "CHARACTER": ColumnType.CHAR,
        "BOOL": ColumnType.BOOLEAN,
        "BOOLEAN": ColumnType.BOOLEAN,
        "FLOAT": ColumnType.FLOAT,
        "FLOAT4": ColumnType.FLOAT,
        "FLOAT8": ColumnType.FLOAT,
        "DOUBLE": ColumnType.FLOAT,
        "DOUBLE PRECISION": ColumnType.FLOAT,
        "REAL": ColumnType.FLOAT,
        "NUMERIC": ColumnType.NUMERIC,
        "DECIMAL": ColumnType.NUMERIC,
        "DATE": ColumnType.DATE,
        "TIMESTAMP": ColumnType.TIMESTAMP,
        "TIMESTAMP WITHOUT TIME ZONE": ColumnType.TIMESTAMP,
        "TIMESTAMPTZ": ColumnType.TIMESTAMPTZ,
        "TIMESTAMP WITH TIME ZONE": ColumnType.TIMESTAMPTZ,
        "TIME": ColumnType.TIME,
        "JSON": ColumnType.JSON,
        "JSONB": ColumnType.JSONB,
        "UUID": ColumnType.UUID,
        "BYTEA": ColumnType.BYTEA,
        "ENUM": ColumnType.ENUM,
    }
    for key, value in mapping.items():
        if upper.startswith(key):
            return value
    return ColumnType.UNKNOWN


def _split_schema_table(full: str) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = full.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "public", parts[0]


def _aliases_json_to_list(aliases: Optional[dict[str, Any]]) -> list[str]:
    if not aliases:
        return []
    if isinstance(aliases, list):
        return [str(a) for a in aliases if a is not None]
    if isinstance(aliases, dict):
        values = aliases.get("aliases") or aliases.get("values") or list(aliases.values())
        if isinstance(values, list):
            return [str(v) for v in values if v is not None]
        return []
    return []


def _tables_json_to_list(tables: Optional[dict[str, Any]]) -> list[str]:
    if not tables:
        return []
    if isinstance(tables, list):
        return [str(t) for t in tables if t is not None]
    if isinstance(tables, dict):
        values = tables.get("tables") or tables.get("names") or list(tables.values())
        if isinstance(values, list):
            return [str(v) for v in values if v is not None]
        return []
    return []


def _list_to_aliases_json(items: list[str]) -> dict[str, Any]:
    return {"aliases": list(items) if items else []}


def _list_to_tables_json(items: list[str]) -> dict[str, Any]:
    return {"tables": list(items) if items else []}


class ContextRepository:
    @staticmethod
    def _orm_table_to_pydantic(
        orm_table: ORMTable,
        columns: list[ORMColumn],
    ) -> TableMetadata:
        return TableMetadata(
            schema_name=orm_table.schema_name,
            table_name=orm_table.table_name,
            table_type=TableType(orm_table.table_type)
            if orm_table.table_type in {t.value for t in TableType}
            else TableType.TABLE,
            row_count_estimate=orm_table.row_count_estimate or 0,
            comment=orm_table.comment,
            columns=[
                ContextRepository._orm_column_to_pydantic(col)
                for col in sorted(columns, key=lambda c: c.column_name)
            ],
            primary_key=[
                col.column_name
                for col in columns
                if col.is_pk
            ],
            indexes=[],
            constraints=[],
            sample_rows=None,
            column_profiles=None,
        )

    @staticmethod
    def _orm_column_to_pydantic(orm_col: ORMColumn) -> ColumnMetadata:
        fk_table: Optional[str] = None
        fk_column: Optional[str] = None
        if orm_col.fk_target:
            schema, rest = _split_schema_table(orm_col.fk_target)
            inner_table, inner_col = _split_schema_table(rest)
            if inner_col:
                fk_table = f"{schema}.{inner_table}" if schema else inner_table
                fk_column = inner_col
            else:
                fk_table = schema
                fk_column = rest if rest else None
        return ColumnMetadata(
            name=orm_col.column_name,
            type=_infer_column_type(orm_col.raw_type),
            raw_type=orm_col.raw_type,
            nullable=orm_col.nullable,
            default_value=orm_col.default_value,
            character_maximum_length=None,
            numeric_precision=None,
            numeric_scale=None,
            ordinal_position=0,
            comment=orm_col.comment,
            is_pk=orm_col.is_pk,
            is_fk=orm_col.is_fk,
            fk_target_table=fk_table,
            fk_target_column=fk_column,
            enum_values=None,
        )

    @staticmethod
    def _orm_relationship_to_pydantic(orm_rel: ORMRelationship) -> RelationshipMetadata:
        source_schema, source_table = _split_schema_table(orm_rel.source_table)
        target_schema, target_table = _split_schema_table(orm_rel.target_table)
        return RelationshipMetadata(
            source_schema=source_schema,
            source_table=source_table,
            source_columns=[orm_rel.source_column] if orm_rel.source_column else [],
            target_schema=target_schema,
            target_table=target_table,
            target_columns=[orm_rel.target_column] if orm_rel.target_column else [],
            relationship_type=RelationshipType(orm_rel.type)
            if orm_rel.type in {r.value for r in RelationshipType}
            else RelationshipType.MANY_TO_ONE,
            inferred=orm_rel.inferred,
            confidence=orm_rel.confidence or 1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_domain_to_pydantic(orm_dom: ORMDomain) -> BusinessDomain:
        return BusinessDomain(
            id=orm_dom.id,
            name=orm_dom.name,
            code=orm_dom.code,
            description=orm_dom.description or "",
            aliases=_aliases_json_to_list(orm_dom.aliases),
            tables=[],
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_entity_to_pydantic(orm_ent: ORMEntity) -> BusinessEntity:
        return BusinessEntity(
            id=orm_ent.id,
            name=orm_ent.name,
            code=orm_ent.code,
            description=orm_ent.description or "",
            aliases=_aliases_json_to_list(orm_ent.aliases),
            domain_code=orm_ent.domain_code,
            tables=_tables_json_to_list(orm_ent.tables),
            primary_table=None,
            key_columns=[],
            status_column=None,
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_concept_to_pydantic(orm_con: ORMConcept) -> BusinessConcept:
        return BusinessConcept(
            id=orm_con.id,
            name=orm_con.name,
            code=orm_con.code,
            description=orm_con.description or "",
            aliases=_aliases_json_to_list(orm_con.aliases),
            entity_code=orm_con.entity_code,
            domain_code=None,
            tables=_tables_json_to_list(orm_con.tables),
            source_columns=[],
            condition_semantic=orm_con.condition_semantic,
            condition_sql=orm_con.condition_sql,
            value_expression=None,
            is_filter=False,
            is_computed=False,
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_metric_to_pydantic(orm_met: ORMMetric) -> BusinessMetric:
        return BusinessMetric(
            id=orm_met.id,
            name=orm_met.name,
            code=orm_met.code,
            description=orm_met.description or "",
            aliases=_aliases_json_to_list(orm_met.aliases),
            domain_code=None,
            entity_code=None,
            source_tables=_tables_json_to_list(orm_met.source_tables),
            source_column=orm_met.source_column,
            aggregation=AggregationType(orm_met.aggregation)
            if orm_met.aggregation and orm_met.aggregation in {a.value for a in AggregationType}
            else AggregationType.NONE,
            filter_condition=orm_met.filter_condition,
            date_column=orm_met.date_column,
            default_dimensions=[],
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_dimension_to_pydantic(orm_dim: ORMDimension) -> Dimension:
        return Dimension(
            id=orm_dim.id,
            name=orm_dim.name,
            code=orm_dim.code,
            description=orm_dim.description or "",
            aliases=[],
            dimension_type=DimensionType(orm_dim.dimension_type)
            if orm_dim.dimension_type and orm_dim.dimension_type in {d.value for d in DimensionType}
            else DimensionType.CATEGORICAL,
            source_table=orm_dim.source_table,
            source_column=orm_dim.source_column,
            values=None,
            requires_joins=(
                list(orm_dim.requires_joins)
                if isinstance(orm_dim.requires_joins, list)
                else []
            ),
            confidence=1.0,
        )

    @staticmethod
    def _orm_terminology_to_pydantic(orm_term: ORMTerminology) -> TerminologyEntry:
        return TerminologyEntry(
            id=orm_term.id,
            term=orm_term.term,
            canonical_form=orm_term.canonical_form,
            aliases=_aliases_json_to_list(orm_term.aliases),
            type=TerminologyEntryType(orm_term.type)
            if orm_term.type and orm_term.type in {t.value for t in TerminologyEntryType}
            else TerminologyEntryType.OTHER,
            target_code=orm_term.target_code,
            confidence=1.0,
        )

    async def create_context_version(
        self,
        session: AsyncSession,
        connection_id: UUID,
        schema_hash: str,
        status: str,
        llm_model: Optional[str],
        embedding_model: Optional[str],
        database_summary: Optional[str],
    ) -> str:
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
                        "Created placeholder connection metadata row to satisfy FK during context save",
                        extra={"connection_id": str(connection_id)},
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to flush placeholder connection metadata during context save; will attempt select again to recover",
                        exc_info=exc,
                        extra={"connection_id": str(connection_id)},
                    )
                    await session.rollback()
                    existing = (await session.execute(exists_stmt)).scalar_one_or_none()
                    if existing is None:
                        raise
        except Exception as _place_err:
            logger.warning(
                "Could not create placeholder connection metadata for context version",
                exc_info=_place_err,
                extra={"connection_id": str(connection_id)},
            )
        stmt = (
            select(ORMContextVersion)
            .where(ORMContextVersion.connection_id == connection_id)
            .order_by(ORMContextVersion.context_version.desc())
        )
        result = await session.execute(stmt)
        latest = result.scalar_one_or_none()
        next_version_int = 1
        if latest is not None:
            next_version_int = latest.context_version + 1
        short_id = _shortid(6)
        semver = f"v0.0.{next_version_int}-{short_id}"
        orm_ctx = ORMContextVersion(
            connection_id=connection_id,
            context_version=next_version_int,
            schema_hash=schema_hash,
            database_summary=database_summary,
            status=status,
            llm_model=llm_model,
            embedding_model=embedding_model,
        )
        session.add(orm_ctx)
        await session.flush()
        logger.info(
            "Created context version",
            extra={
                "context_version_id": str(orm_ctx.id),
                "connection_id": str(connection_id),
                "version_int": next_version_int,
                "semver": semver,
            },
        )
        return semver

    async def get_latest_context_version(
        self,
        session: AsyncSession,
        connection_id: UUID,
    ) -> Optional[DatabaseKnowledgeMap]:
        stmt = (
            select(ORMContextVersion)
            .where(ORMContextVersion.connection_id == connection_id)
            .order_by(ORMContextVersion.context_version.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        latest = result.scalar_one_or_none()
        if latest is None:
            return None
        return await self.get_knowledge_map(session, latest.id)

    async def update_context_status(
        self,
        session: AsyncSession,
        context_version_id: UUID,
        status: str,
        **fields: Any,
    ) -> bool:
        stmt = select(ORMContextVersion).where(ORMContextVersion.id == context_version_id)
        result = await session.execute(stmt)
        orm_ctx = result.scalar_one_or_none()
        if orm_ctx is None:
            logger.warning(
                "Cannot update status for missing context version",
                extra={"context_version_id": str(context_version_id)},
            )
            return False
        orm_ctx.status = status
        allowed_fields = {
            "database_summary",
            "llm_model",
            "embedding_model",
            "schema_hash",
        }
        for key, value in fields.items():
            if key in allowed_fields and value is not None:
                setattr(orm_ctx, key, value)
        await session.flush()
        logger.debug(
            "Updated context status",
            extra={
                "context_version_id": str(context_version_id),
                "status": status,
                "fields": list(fields.keys()),
            },
        )
        return True

    async def get_knowledge_map(
        self,
        session: AsyncSession,
        context_version_id: UUID,
    ) -> Optional[DatabaseKnowledgeMap]:
        base_stmt = (
            select(ORMContextVersion)
            .options(
                selectinload(ORMContextVersion.tables_metadata).selectinload(ORMTable.columns_metadata),
                selectinload(ORMContextVersion.relationships),
                selectinload(ORMContextVersion.business_domains),
                selectinload(ORMContextVersion.business_entities),
                selectinload(ORMContextVersion.business_concepts),
                selectinload(ORMContextVersion.metrics),
                selectinload(ORMContextVersion.dimensions),
                selectinload(ORMContextVersion.terminology),
                selectinload(ORMContextVersion.embeddings),
            )
            .where(ORMContextVersion.id == context_version_id)
        )
        result = await session.execute(base_stmt)
        orm_ctx = result.unique().scalar_one_or_none()
        if orm_ctx is None:
            return None
        table_map: dict[UUID, TableMetadata] = {}
        for orm_table in orm_ctx.tables_metadata:
            table_model = self._orm_table_to_pydantic(
                orm_table,
                list(orm_table.columns_metadata),
            )
            table_map[orm_table.id] = table_model
        domains = [self._orm_domain_to_pydantic(d) for d in orm_ctx.business_domains]
        entities = [self._orm_entity_to_pydantic(e) for e in orm_ctx.business_entities]
        concepts = [self._orm_concept_to_pydantic(c) for c in orm_ctx.business_concepts]
        metrics = [self._orm_metric_to_pydantic(m) for m in orm_ctx.metrics]
        dimensions = [self._orm_dimension_to_pydantic(d) for d in orm_ctx.dimensions]
        relationships = [self._orm_relationship_to_pydantic(r) for r in orm_ctx.relationships]
        terminology = [self._orm_terminology_to_pydantic(t) for t in orm_ctx.terminology]
        date_semantics: list[DateSemantic] = []
        for orm_table in orm_ctx.tables_metadata:
            for orm_col in orm_table.columns_metadata:
                if orm_col.semantic_role and orm_col.semantic_role.upper() in {
                    r.value for r in DateSemanticRole
                }:
                    date_semantics.append(
                        DateSemantic(
                            id=None,
                            table_name=f"{orm_table.schema_name}.{orm_table.table_name}",
                            column_name=orm_col.column_name,
                            semantic_role=DateSemanticRole(orm_col.semantic_role.upper()),
                            description=f"Column {orm_col.column_name} semantic role: {orm_col.semantic_role}",
                            aliases=[],
                            confidence=1.0,
                        )
                    )
        short_id = _shortid(6)
        semver = f"v0.0.{orm_ctx.context_version}-{short_id}"
        status_enum = (
            KnowledgeMapStatus(orm_ctx.status)
            if orm_ctx.status in {s.value for s in KnowledgeMapStatus}
            else KnowledgeMapStatus.BUILDING
        )
        return DatabaseKnowledgeMap(
            id=orm_ctx.id,
            database_connection_id=orm_ctx.connection_id,
            context_version=semver,
            schema_hash=orm_ctx.schema_hash,
            database_summary=orm_ctx.database_summary or "",
            domains=domains,
            entities=entities,
            concepts=concepts,
            metrics=metrics,
            dimensions=dimensions,
            tables=list(table_map.values()),
            column_semantics=[],
            relationships=relationships,
            relationship_graph=RelationshipGraph(nodes=[], edges=[]),
            date_semantics=date_semantics,
            enum_values=[],
            join_paths=[],
            terminology=terminology,
            llm_model=orm_ctx.llm_model or "",
            embedding_model=orm_ctx.embedding_model or "",
            created_at=orm_ctx.created_at,
            status=status_enum,
        )

    async def persist_tables_metadata(
        self,
        session: AsyncSession,
        context_version_id: UUID,
        tables: list[TableMetadata],
    ) -> list[UUID]:
        inserted_ids: list[UUID] = []
        for table in tables:
            orm_table = ORMTable(
                context_version_id=context_version_id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                table_type=table.table_type.value if isinstance(table.table_type, TableType) else str(table.table_type),
                row_count_estimate=table.row_count_estimate,
                comment=table.comment,
                embedding=None,
            )
            session.add(orm_table)
            await session.flush()
            inserted_ids.append(orm_table.id)
            for col in table.columns:
                fk_target_parts: list[str] = []
                if col.fk_target_table:
                    fk_target_parts.append(col.fk_target_table)
                if col.fk_target_column:
                    fk_target_parts.append(col.fk_target_column)
                fk_target = ".".join(fk_target_parts) if fk_target_parts else None
                orm_col = ORMColumn(
                    table_id=orm_table.id,
                    column_name=col.name,
                    raw_type=col.raw_type,
                    nullable=col.nullable,
                    default_value=col.default_value,
                    is_pk=col.is_pk,
                    is_fk=col.is_fk,
                    fk_target=fk_target,
                    comment=col.comment,
                    semantic_role=None,
                    pii_level=None,
                    embedding=None,
                )
                session.add(orm_col)
        await session.flush()
        logger.info(
            "Persisted tables metadata",
            extra={
                "context_version_id": str(context_version_id),
                "table_count": len(tables),
            },
        )
        return inserted_ids

    async def persist_columns_metadata(
        self,
        session: AsyncSession,
        table_orm_id: UUID,
        columns: list[ColumnMetadata],
        table_schema: str,
        table_name: str,
    ) -> None:
        for col in columns:
            fk_target_parts: list[str] = []
            if col.fk_target_table:
                fk_target_parts.append(col.fk_target_table)
            if col.fk_target_column:
                fk_target_parts.append(col.fk_target_column)
            fk_target = ".".join(fk_target_parts) if fk_target_parts else None
            orm_col = ORMColumn(
                table_id=table_orm_id,
                column_name=col.name,
                raw_type=col.raw_type,
                nullable=col.nullable,
                default_value=col.default_value,
                is_pk=col.is_pk,
                is_fk=col.is_fk,
                fk_target=fk_target,
                comment=col.comment,
                semantic_role=None,
                pii_level=None,
                embedding=None,
            )
            session.add(orm_col)
        await session.flush()
        logger.debug(
            "Persisted columns metadata",
            extra={
                "table_orm_id": str(table_orm_id),
                "table_schema": table_schema,
                "table_name": table_name,
                "column_count": len(columns),
            },
        )

    async def persist_relationships(
        self,
        session: AsyncSession,
        context_version_id: UUID,
        rels: list[RelationshipMetadata],
    ) -> None:
        for rel in rels:
            source_full = f"{rel.source_schema}.{rel.source_table}" if rel.source_schema else rel.source_table
            target_full = f"{rel.target_schema}.{rel.target_table}" if rel.target_schema else rel.target_table
            source_col = rel.source_columns[0] if rel.source_columns else ""
            target_col = rel.target_columns[0] if rel.target_columns else ""
            orm_rel = ORMRelationship(
                context_version_id=context_version_id,
                source_table=source_full,
                source_column=source_col,
                target_table=target_full,
                target_column=target_col,
                type=rel.relationship_type.value
                if isinstance(rel.relationship_type, RelationshipType)
                else str(rel.relationship_type),
                inferred=rel.inferred,
                confidence=rel.confidence,
            )
            session.add(orm_rel)
        await session.flush()
        logger.info(
            "Persisted relationships",
            extra={
                "context_version_id": str(context_version_id),
                "relationship_count": len(rels),
            },
        )

    async def persist_terminology(
        self,
        session: AsyncSession,
        context_version_id: UUID,
        terms: list[TerminologyEntry],
    ) -> None:
        for term in terms:
            orm_term = ORMTerminology(
                context_version_id=context_version_id,
                term=term.term,
                canonical_form=term.canonical_form,
                aliases=_list_to_aliases_json(term.aliases),
                type=term.type.value if isinstance(term.type, TerminologyEntryType) else str(term.type),
                target_code=term.target_code,
                embedding=None,
            )
            session.add(orm_term)
        await session.flush()
        logger.info(
            "Persisted terminology",
            extra={
                "context_version_id": str(context_version_id),
                "term_count": len(terms),
            },
        )

    async def save_context_full(
        self,
        session: AsyncSession,
        connection_id: UUID,
        knowledge_map: DatabaseKnowledgeMap,
    ) -> None:
        try:
            stmt = (
                select(ORMContextVersion)
                .where(ORMContextVersion.connection_id == connection_id)
                .order_by(ORMContextVersion.context_version.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            orm_ctx = result.scalar_one_or_none()
            if orm_ctx is None:
                short_id = _shortid(6)
                semver = f"v0.0.1-{short_id}"
                orm_ctx = ORMContextVersion(
                    connection_id=connection_id,
                    context_version=1,
                    schema_hash=knowledge_map.schema_hash,
                    database_summary=knowledge_map.database_summary,
                    status=knowledge_map.status.value
                    if isinstance(knowledge_map.status, KnowledgeMapStatus)
                    else str(knowledge_map.status),
                    llm_model=knowledge_map.llm_model,
                    embedding_model=knowledge_map.embedding_model,
                )
                session.add(orm_ctx)
                await session.flush()
            else:
                orm_ctx.schema_hash = knowledge_map.schema_hash
                orm_ctx.database_summary = knowledge_map.database_summary
                orm_ctx.status = (
                    knowledge_map.status.value
                    if isinstance(knowledge_map.status, KnowledgeMapStatus)
                    else str(knowledge_map.status)
                )
                orm_ctx.llm_model = knowledge_map.llm_model
                orm_ctx.embedding_model = knowledge_map.embedding_model
                await session.flush()
            context_id = orm_ctx.id
            for table in knowledge_map.tables:
                orm_table = ORMTable(
                    context_version_id=context_id,
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    table_type=table.table_type.value
                    if isinstance(table.table_type, TableType)
                    else str(table.table_type),
                    row_count_estimate=table.row_count_estimate,
                    comment=table.comment,
                    embedding=None,
                )
                session.add(orm_table)
                await session.flush()
                for col in table.columns:
                    fk_target_parts: list[str] = []
                    if col.fk_target_table:
                        fk_target_parts.append(col.fk_target_table)
                    if col.fk_target_column:
                        fk_target_parts.append(col.fk_target_column)
                    fk_target = ".".join(fk_target_parts) if fk_target_parts else None
                    semantic_role_value: Optional[str] = None
                    for ds in knowledge_map.date_semantics:
                        table_full = f"{table.schema_name}.{table.table_name}"
                        if ds.table_name == table_full and ds.column_name == col.name:
                            semantic_role_value = (
                                ds.semantic_role.value
                                if isinstance(ds.semantic_role, DateSemanticRole)
                                else str(ds.semantic_role)
                            )
                            break
                    orm_col = ORMColumn(
                        table_id=orm_table.id,
                        column_name=col.name,
                        raw_type=col.raw_type,
                        nullable=col.nullable,
                        default_value=col.default_value,
                        is_pk=col.is_pk,
                        is_fk=col.is_fk,
                        fk_target=fk_target,
                        comment=col.comment,
                        semantic_role=semantic_role_value,
                        pii_level=None,
                        embedding=None,
                    )
                    session.add(orm_col)
            for rel in knowledge_map.relationships:
                source_full = f"{rel.source_schema}.{rel.source_table}" if rel.source_schema else rel.source_table
                target_full = f"{rel.target_schema}.{rel.target_table}" if rel.target_schema else rel.target_table
                source_col = rel.source_columns[0] if rel.source_columns else ""
                target_col = rel.target_columns[0] if rel.target_columns else ""
                orm_rel = ORMRelationship(
                    context_version_id=context_id,
                    source_table=source_full,
                    source_column=source_col,
                    target_table=target_full,
                    target_column=target_col,
                    type=rel.relationship_type.value
                    if isinstance(rel.relationship_type, RelationshipType)
                    else str(rel.relationship_type),
                    inferred=rel.inferred,
                    confidence=rel.confidence,
                )
                session.add(orm_rel)
            for domain in knowledge_map.domains:
                orm_dom = ORMDomain(
                    context_version_id=context_id,
                    name=domain.name,
                    code=domain.code,
                    description=domain.description,
                    aliases=_list_to_aliases_json(domain.aliases),
                    embedding=None,
                )
                session.add(orm_dom)
            for entity in knowledge_map.entities:
                orm_ent = ORMEntity(
                    context_version_id=context_id,
                    name=entity.name,
                    code=entity.code,
                    description=entity.description,
                    domain_code=entity.domain_code,
                    aliases=_list_to_aliases_json(entity.aliases),
                    tables=_list_to_tables_json(entity.tables),
                    embedding=None,
                )
                session.add(orm_ent)
            for concept in knowledge_map.concepts:
                orm_con = ORMConcept(
                    context_version_id=context_id,
                    name=concept.name,
                    code=concept.code,
                    description=concept.description,
                    aliases=_list_to_aliases_json(concept.aliases),
                    entity_code=concept.entity_code,
                    tables=_list_to_tables_json(concept.tables),
                    condition_semantic=concept.condition_semantic,
                    condition_sql=concept.condition_sql,
                    embedding=None,
                )
                session.add(orm_con)
            for metric in knowledge_map.metrics:
                orm_met = ORMMetric(
                    context_version_id=context_id,
                    name=metric.name,
                    code=metric.code,
                    description=metric.description,
                    aliases=_list_to_aliases_json(metric.aliases),
                    source_tables=_list_to_tables_json(metric.source_tables),
                    source_column=metric.source_column,
                    aggregation=metric.aggregation.value
                    if isinstance(metric.aggregation, AggregationType)
                    else str(metric.aggregation),
                    filter_condition=metric.filter_condition,
                    date_column=metric.date_column,
                    embedding=None,
                )
                session.add(orm_met)
            for dim in knowledge_map.dimensions:
                orm_dim = ORMDimension(
                    context_version_id=context_id,
                    name=dim.name,
                    code=dim.code,
                    description=dim.description,
                    dimension_type=dim.dimension_type.value
                    if isinstance(dim.dimension_type, DimensionType)
                    else str(dim.dimension_type),
                    source_table=dim.source_table,
                    source_column=dim.source_column,
                    requires_joins=list(dim.requires_joins) if dim.requires_joins else None,
                    embedding=None,
                )
                session.add(orm_dim)
            for term in knowledge_map.terminology:
                orm_term = ORMTerminology(
                    context_version_id=context_id,
                    term=term.term,
                    canonical_form=term.canonical_form,
                    aliases=_list_to_aliases_json(term.aliases),
                    type=term.type.value if isinstance(term.type, TerminologyEntryType) else str(term.type),
                    target_code=term.target_code,
                    embedding=None,
                )
                session.add(orm_term)
            await session.flush()
            logger.info(
                "Saved full context knowledge map",
                extra={
                    "context_version_id": str(context_id),
                    "connection_id": str(connection_id),
                    "tables": len(knowledge_map.tables),
                    "relationships": len(knowledge_map.relationships),
                    "domains": len(knowledge_map.domains),
                    "entities": len(knowledge_map.entities),
                    "concepts": len(knowledge_map.concepts),
                    "metrics": len(knowledge_map.metrics),
                    "dimensions": len(knowledge_map.dimensions),
                    "terminology": len(knowledge_map.terminology),
                },
            )
        except SQLAlchemyError as exc:
            logger.error(
                "Failed to save full context",
                extra={
                    "connection_id": str(connection_id),
                },
                exc_info=exc,
            )
            raise
