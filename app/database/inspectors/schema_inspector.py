from __future__ import annotations

from typing import Optional

from app.core.exceptions import DatabaseDiscoveryError
from app.core.logging import get_logger
from app.database.adapters.base import DatabaseAdapter
from app.models.database import (
    ColumnMetadata,
    ColumnProfile,
    ConstraintMetadata,
    ConstraintType,
    DatabaseMetadata,
    IndexMetadata,
    RelationshipMetadata,
    RelationshipType,
    SchemaMetadata,
    TableMetadata,
    TableType,
)


logger = get_logger(__name__)


class SchemaInspector:
    def __init__(self, adapter: DatabaseAdapter) -> None:
        self.adapter = adapter

    async def build_database_metadata(
        self,
        schemas_filter: Optional[list[str]] = None,
        include_views: bool = True,
    ) -> DatabaseMetadata:
        try:
            all_schemas = await self.adapter.get_schemas()
            if schemas_filter:
                target_schemas = [s for s in all_schemas if s in schemas_filter]
                if not target_schemas:
                    target_schemas = list(schemas_filter)
            else:
                target_schemas = all_schemas

            db_meta = DatabaseMetadata(
                database_type=self.adapter.config.database_type,
                database_name=self.adapter.config.database_name,
            )
            all_relationships: list[RelationshipMetadata] = []
            total_rows = 0

            for schema_name in target_schemas:
                schema_meta = SchemaMetadata(schema_name=schema_name)
                try:
                    tables_and_types = await self.adapter.get_tables(schema_name)
                except Exception as exc:
                    logger.warning(
                        "Failed to get tables for schema, skipping",
                        extra={"schema": schema_name},
                        exc_info=exc,
                    )
                    continue

                for table_name, table_type_str in tables_and_types:
                    try:
                        is_view = table_type_str in {"VIEW", "MATERIALIZED_VIEW"}
                        if is_view and not include_views:
                            continue

                        table_enum = TableType.TABLE
                        if table_type_str == "VIEW":
                            table_enum = TableType.VIEW
                        elif table_type_str == "MATERIALIZED_VIEW":
                            table_enum = TableType.MATERIALIZED_VIEW

                        estimated_rows = await self.adapter.get_estimated_row_count(schema_name, table_name)
                        total_rows += max(estimated_rows, 0)
                        comment = await self.adapter.get_table_comment(schema_name, table_name)

                        table_meta = TableMetadata(
                            schema_name=schema_name,
                            table_name=table_name,
                            table_type=table_enum,
                            row_count_estimate=max(estimated_rows, 0),
                            comment=comment,
                        )

                        raw_columns = await self.adapter.get_columns(schema_name, table_name)
                        primary_keys = await self.adapter.get_primary_keys(schema_name, table_name)
                        foreign_keys_raw = await self.adapter.get_foreign_keys(schema_name, table_name)
                        indexes_raw = await self.adapter.get_indexes(schema_name, table_name)
                        constraints_raw = await self.adapter.get_constraints(schema_name, table_name)

                        table_meta.primary_key = list(primary_keys)

                        fk_targets_by_col: dict[str, tuple[str, str, str]] = {}
                        for fk in foreign_keys_raw:
                            t_schema = fk.get("referred_schema") or schema_name
                            t_table = fk.get("referred_table", "")
                            constrained_cols = fk.get("constrained_columns", [])
                            referred_cols = fk.get("referred_columns", [])
                            for i, src_col in enumerate(constrained_cols):
                                if i < len(referred_cols):
                                    fk_targets_by_col[src_col] = (
                                        t_schema,
                                        t_table,
                                        referred_cols[i],
                                    )

                        columns_info_for_heuristics = []
                        for raw_col in raw_columns:
                            col_name = raw_col.get("name", "")
                            raw_type = raw_col.get("raw_type", "")
                            mapped_type = self.adapter.map_column_type(raw_type)
                            ci = {
                                "name": col_name,
                                "raw_type": raw_type,
                                "type": mapped_type,
                            }
                            columns_info_for_heuristics.append(ci)
                        heuristics_map = self.adapter.column_heuristics(columns_info_for_heuristics)

                        for raw_col in raw_columns:
                            col_name = raw_col.get("name", "")
                            raw_type = raw_col.get("raw_type", "")
                            mapped_type = self.adapter.map_column_type(raw_type)
                            is_pk = col_name in primary_keys
                            fk_info = fk_targets_by_col.get(col_name)
                            is_fk = fk_info is not None
                            fk_target_table = None
                            fk_target_column = None
                            if fk_info:
                                fk_t_schema, fk_t_table, fk_t_col = fk_info
                                if fk_t_schema == schema_name:
                                    fk_target_table = fk_t_table
                                else:
                                    fk_target_table = f"{fk_t_schema}.{fk_t_table}"
                                fk_target_column = fk_t_col

                            col_meta = ColumnMetadata(
                                name=col_name,
                                type=mapped_type,
                                raw_type=raw_type,
                                nullable=bool(raw_col.get("nullable", True)),
                                default_value=raw_col.get("default_value"),
                                character_maximum_length=raw_col.get("character_maximum_length"),
                                numeric_precision=raw_col.get("numeric_precision"),
                                numeric_scale=raw_col.get("numeric_scale"),
                                ordinal_position=int(raw_col.get("ordinal_position", 0)),
                                comment=raw_col.get("comment"),
                                is_pk=is_pk,
                                is_fk=is_fk,
                                fk_target_table=fk_target_table,
                                fk_target_column=fk_target_column,
                                enum_values=raw_col.get("enum_values"),
                                heuristics=heuristics_map.get(col_name) or heuristics_map.get(col_name.lower()) or type(
                                    heuristics_map[next(iter(heuristics_map))]
                                )()
                                if heuristics_map
                                else type(heuristics_map.get(col_name, type(heuristics_map.get("__dummy__", None))))(),
                            )
                            table_meta.columns.append(col_meta)

                        for idx in indexes_raw:
                            idx_meta = IndexMetadata(
                                name=idx.get("name", ""),
                                columns=list(idx.get("column_names", [])),
                                is_unique=bool(idx.get("unique", False)),
                                is_primary=bool(idx.get("is_primary", False)),
                            )
                            table_meta.indexes.append(idx_meta)

                        for con in constraints_raw:
                            con_type_str = con.get("type", "")
                            try:
                                con_type = ConstraintType(con_type_str)
                            except Exception:
                                con_type = ConstraintType.CHECK
                            con_meta = ConstraintMetadata(
                                name=con.get("name", ""),
                                type=con_type,
                                columns=list(con.get("columns", [])),
                                definition=con.get("definition"),
                            )
                            table_meta.constraints.append(con_meta)

                        for fk in foreign_keys_raw:
                            src_schema = schema_name
                            src_table = table_name
                            src_cols = list(fk.get("constrained_columns", []))
                            tgt_schema = fk.get("referred_schema") or schema_name
                            tgt_table = fk.get("referred_table", "")
                            tgt_cols = list(fk.get("referred_columns", []))
                            if not src_cols or not tgt_cols or not tgt_table:
                                continue

                            rel_type = RelationshipType.MANY_TO_ONE
                            try:
                                src_pks = await self.adapter.get_primary_keys(src_schema, src_table)
                                if set(src_cols) == set(src_pks) and len(src_cols) > 0:
                                    rel_type = RelationshipType.ONE_TO_ONE
                                else:
                                    tgt_pks = await self.adapter.get_primary_keys(tgt_schema, tgt_table)
                                    if set(tgt_cols) == set(tgt_pks) and len(tgt_cols) > 0:
                                        rel_type = RelationshipType.MANY_TO_ONE
                            except Exception:
                                pass

                            rel_meta = RelationshipMetadata(
                                source_schema=src_schema,
                                source_table=src_table,
                                source_columns=src_cols,
                                target_schema=tgt_schema,
                                target_table=tgt_table,
                                target_columns=tgt_cols,
                                relationship_type=rel_type,
                                inferred=False,
                                confidence=1.0,
                                evidence=f"Foreign key constraint: {fk.get('name', '')}",
                            )
                            all_relationships.append(rel_meta)

                        if is_view:
                            schema_meta.views.append(table_meta)
                        else:
                            schema_meta.tables.append(table_meta)

                    except Exception as exc:
                        logger.warning(
                            "Failed to process table, skipping",
                            extra={"schema": schema_name, "table": table_name},
                            exc_info=exc,
                        )
                        continue

                db_meta.schemas.append(schema_meta)

            db_meta.relationships = all_relationships
            db_meta.estimated_total_rows = total_rows
            return db_meta

        except Exception as exc:
            logger.error("Failed to build database metadata", exc_info=exc)
            raise DatabaseDiscoveryError(
                detail=f"Failed to build database metadata: {exc}",
                extra={"database": self.adapter.config.database_name},
            ) from exc
