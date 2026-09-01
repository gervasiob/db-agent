from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.exceptions import ContextNotFoundError, DatabaseDiscoveryError
from app.core.logging import get_logger
from app.database.adapters.base import DatabaseAdapter
from app.database.inspectors.schema_inspector import SchemaInspector
from app.models.database import (
    ColumnHeuristicFlags,
    ColumnMetadata,
    ColumnType,
    ConstraintType,
    DatabaseMetadata,
    RelationshipMetadata,
    RelationshipType,
    TableMetadata,
)
from app.services.database_connection_service import DatabaseConnectionService

logger = get_logger(__name__)


class DatabaseDiscoveryService:
    def __init__(
        self,
        connection_service: Optional[DatabaseConnectionService] = None,
    ) -> None:
        self.connection_service = connection_service or DatabaseConnectionService()

    def _apply_heuristic_flags(self, column: ColumnMetadata) -> ColumnHeuristicFlags:
        name = column.name.lower()
        raw_type = column.raw_type.lower()
        col_type = column.type

        is_id = False
        is_name = False
        is_description = False
        is_status = False
        is_date = False
        is_timestamp = False
        is_monetary = False
        is_soft_delete = False
        is_created_at = False
        is_updated_at = False
        is_audit = False

        if (
            name == "id"
            or name.endswith("_id")
            or name.endswith("_uuid")
            or name.endswith("_pk")
            or re.match(r"^id_[a-z]", name)
        ):
            is_id = True

        name_matches = {
            "name",
            "nombre",
            "title",
            "titulo",
            "label",
            "etiqueta",
            "display_name",
            "full_name",
            "nombre_completo",
            "first_name",
            "last_name",
        }
        if name in name_matches or name in {"description", "descripcion", "desc", "detalle", "detail"}:
            if name in {"description", "descripcion", "desc", "detalle", "detail"}:
                is_description = True
            else:
                is_name = True

        status_words = (
            "status",
            "estado",
            "state",
            "estatus",
            "enabled",
            "disabled",
            "active",
            "inactive",
            "activo",
            "inactivo",
            "approved",
            "pending",
            "cancelled",
        )
        if (
            any(name == w or name.startswith(f"{w}_") for w in status_words)
            or (col_type == ColumnType.BOOLEAN and (name.startswith("is_") or name.startswith("has_")))
        ):
            is_status = True

        date_suffixes = ("_dt", "_date", "_ts", "_at")
        if (
            any(name.endswith(suf) for suf in date_suffixes)
            or name in {"date", "fecha", "day", "dia", "month", "mes", "year", "anio", "period", "periodo"}
        ):
            is_date = True
            if col_type in {ColumnType.TIMESTAMP, ColumnType.TIMESTAMPTZ} or name.endswith(
                ("_ts", "_at", "_timestamp", "_datetime")
            ):
                is_timestamp = True

        created_names = (
            "created_at",
            "created_date",
            "creation_date",
            "inserted_at",
            "fecha_creacion",
            "fecha_creado",
            "date_created",
            "created_on",
        )
        if name in created_names:
            is_created_at = True
            is_timestamp = True
            is_date = True

        updated_names = (
            "updated_at",
            "modified_at",
            "last_modified",
            "modified_date",
            "fecha_actualizacion",
            "fecha_modificado",
            "date_updated",
            "updated_on",
        )
        if name in updated_names:
            is_updated_at = True
            is_timestamp = True
            is_date = True

        audit_names = (
            "created_by",
            "modified_by",
            "updated_by",
            "deleted_by",
            "creado_por",
            "modificado_por",
            "actualizado_por",
            "user_id",
            "usuario_id",
            "ip_address",
            "ip",
        )
        if name in audit_names or name.endswith(("_by", "_user_id", "_usuario_id")):
            is_audit = True

        soft_delete_names = (
            "deleted",
            "is_deleted",
            "is_removed",
            "removed_at",
            "deleted_at",
            "fecha_eliminacion",
            "eliminado",
            "is_active",
            "archived",
        )
        if name in soft_delete_names:
            is_soft_delete = True

        money_words = (
            "amount",
            "monto",
            "price",
            "precio",
            "cost",
            "costo",
            "total",
            "subtotal",
            "tax",
            "impuesto",
            "iva",
            "discount",
            "descuento",
            "fee",
            "tarifa",
            "rate",
            "tasa",
            "salary",
            "saldo",
            "balance",
            "revenue",
            "ingreso",
            "profit",
            "ganancia",
            "value",
            "valor",
            "income",
            "expense",
        )
        if (
            any(name == w or name.endswith(f"_{w}") or name.startswith(f"{w}_") for w in money_words)
        ) and col_type in {
            ColumnType.NUMERIC,
            ColumnType.FLOAT,
            ColumnType.INTEGER,
            ColumnType.BIGINT,
            ColumnType.SMALLINT,
        }:
            is_monetary = True

        return ColumnHeuristicFlags(
            is_id=is_id,
            is_name=is_name,
            is_description=is_description,
            is_status=is_status,
            is_category=False,
            is_date=is_date,
            is_timestamp=is_timestamp,
            is_monetary=is_monetary,
            is_created_at=is_created_at,
            is_updated_at=is_updated_at,
            is_audit=is_audit,
            is_soft_delete=is_soft_delete,
            is_email=False,
            is_phone=False,
            is_address=False,
        )

    def _iter_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            tables.extend(schema.tables)
            tables.extend(schema.views)
        return tables

    def _infer_relationships(
        self,
        metadata: DatabaseMetadata,
    ) -> list[RelationshipMetadata]:
        inferred: list[RelationshipMetadata] = []
        existing_keys = {
            (r.source_schema, r.source_table, tuple(r.source_columns), r.target_schema, r.target_table, tuple(r.target_columns))
            for r in metadata.relationships
        }

        all_tables = self._iter_tables(metadata)
        table_by_name: dict[str, TableMetadata] = {}
        for t in all_tables:
            key = f"{t.schema_name}.{t.table_name}"
            table_by_name[key] = t
            table_by_name[t.table_name] = t

        for table in all_tables:
            for col in table.columns:
                if not col.name.lower().endswith("_id") or col.is_fk:
                    continue

                target_candidate_stem = col.name.lower()[:-3]
                if not target_candidate_stem:
                    continue

                target_table = None
                target_col_candidates: list[str] = ["id", f"{target_candidate_stem}_id"]
                if "_" in target_candidate_stem:
                    singular = target_candidate_stem
                    if singular.endswith("ies"):
                        singular = singular[:-3] + "y"
                    elif singular.endswith("s"):
                        singular = singular[:-1]
                    target_table = table_by_name.get(f"{table.schema_name}.{singular}")
                    if target_table is None:
                        target_table = table_by_name.get(f"{table.schema_name}.{target_candidate_stem}")
                else:
                    stem = target_candidate_stem
                    if stem.endswith("s"):
                        stem_singular = stem[:-1]
                        target_table = table_by_name.get(f"{table.schema_name}.{stem_singular}")
                    if target_table is None:
                        target_table = table_by_name.get(f"{table.schema_name}.{stem}")

                if target_table is None or target_table is table:
                    continue

                target_col = None
                for tc in target_col_candidates:
                    for tcol in target_table.columns:
                        if tcol.name.lower() == tc and tcol.is_pk:
                            target_col = tcol.name
                            break
                    if target_col:
                        break

                if target_col is None:
                    continue

                key = (
                    table.schema_name,
                    table.table_name,
                    (col.name,),
                    target_table.schema_name,
                    target_table.table_name,
                    (target_col,),
                )
                if key in existing_keys:
                    continue
                existing_keys.add(key)

                confidence = 0.75
                if col.heuristics and col.heuristics.is_id:
                    confidence = 0.9
                if target_table.columns and target_table.columns[0].is_pk:
                    confidence = min(1.0, confidence + 0.05)

                inferred.append(
                    RelationshipMetadata(
                        source_schema=table.schema_name,
                        source_table=table.table_name,
                        source_columns=[col.name],
                        target_schema=target_table.schema_name,
                        target_table=target_table.table_name,
                        target_columns=[target_col],
                        relationship_type=RelationshipType.MANY_TO_ONE,
                        inferred=True,
                        confidence=confidence,
                        evidence=f"Heuristic FK match: {table.table_name}.{col.name} -> {target_table.table_name}.{target_col}",
                    )
                )

        return inferred

    async def _augment_row_counts_pg_catalog(
        self,
        engine: AsyncEngine,
        metadata: DatabaseMetadata,
    ) -> None:
        try:
            async with engine.connect() as conn:
                for schema in metadata.schemas:
                    for collection in (schema.tables, schema.views):
                        for table in collection:
                            try:
                                result = await conn.execute(
                                    text(
                                        """
                                        SELECT c.reltuples::bigint
                                        FROM pg_class c
                                        JOIN pg_namespace n ON n.oid = c.relnamespace
                                        WHERE n.nspname = :schema
                                          AND c.relname = :table
                                        """
                                    ),
                                    {"schema": table.schema_name, "table": table.table_name},
                                )
                                row = result.fetchone()
                                if row and row[0] is not None:
                                    est = int(row[0])
                                    table.row_count_estimate = max(est, 0)
                            except Exception as exc:
                                logger.debug(
                                    "Could not augment row count for table",
                                    extra={"table": f"{table.schema_name}.{table.table_name}"},
                                    exc_info=exc,
                                )
                                continue
        except Exception as exc:
            logger.warning("Failed to augment row counts via pg_catalog", exc_info=exc)

    async def discover_schema(
        self,
        session: AsyncSession,
        connection_id: UUID,
        *,
        schemas_filter: Optional[list[str]] = None,
        include_views: bool = True,
    ) -> DatabaseMetadata:
        logger.info(
            "Starting database schema discovery",
            extra={"connection_id": str(connection_id)},
        )
        conn_meta = await self.connection_service.get(
            session,
            connection_id,
            decrypt_password=True,
        )
        engine, db_config = await self.connection_service.build_engine_for(
            conn_meta,
            pool_size=3,
            max_overflow=8,
            pool_recycle=1200,
        )
        adapter: Optional[DatabaseAdapter] = None
        try:
            adapter_cls = DatabaseAdapter.by_database_type(conn_meta.database_type)
            adapter = adapter_cls(db_config)
            adapter._engine = engine

            inspector = SchemaInspector(adapter)
            metadata = await inspector.build_database_metadata(
                schemas_filter=schemas_filter,
                include_views=include_views,
            )

            for schema in metadata.schemas:
                for collection in (schema.tables, schema.views):
                    for table in collection:
                        for col in table.columns:
                            heur = self._apply_heuristic_flags(col)
                            if col.heuristics:
                                merged = col.heuristics.model_dump()
                                for k, v in heur.model_dump().items():
                                    if v and not merged.get(k):
                                        merged[k] = v
                                col.heuristics = ColumnHeuristicFlags(**merged)
                            else:
                                col.heuristics = heur

            if conn_meta.database_type.lower() == "postgresql":
                await self._augment_row_counts_pg_catalog(engine, metadata)

            inferred_rels = self._infer_relationships(metadata)
            if inferred_rels:
                metadata.relationships = [*metadata.relationships, *inferred_rels]

            total = 0
            for schema in metadata.schemas:
                for t in schema.tables:
                    total += max(t.row_count_estimate, 0)
                for v in schema.views:
                    total += max(v.row_count_estimate, 0)
            metadata.estimated_total_rows = total

            logger.info(
                "Database schema discovery completed",
                extra={
                    "connection_id": str(connection_id),
                    "schemas": len(metadata.schemas),
                    "relationships": len(metadata.relationships),
                    "estimated_total_rows": metadata.estimated_total_rows,
                },
            )
            return metadata
        except ContextNotFoundError:
            raise
        except DatabaseDiscoveryError:
            raise
        except Exception as exc:
            logger.error(
                "Database discovery failed",
                extra={"connection_id": str(connection_id)},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Database discovery failed: {exc!s}",
                extra={"connection_id": str(connection_id)},
            ) from exc
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:
                    pass
            try:
                from app.database.connection import close_engine

                await close_engine(engine)
            except Exception:
                pass


__all__ = ["DatabaseDiscoveryService"]
