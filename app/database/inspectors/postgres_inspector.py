from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import DatabaseDiscoveryError
from app.core.logging import get_logger
from app.database.adapters.postgresql import PostgreSQLAdapter
from app.database.inspectors.schema_inspector import SchemaInspector

logger = get_logger(__name__)


class PostgresInspector(SchemaInspector):
    def __init__(self, adapter: PostgreSQLAdapter) -> None:
        super().__init__(adapter)
        self.pg_adapter = adapter

    async def get_schemas(self) -> list[str]:
        try:
            columns, rows = await self.pg_adapter.execute_raw(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
                  AND schema_name NOT LIKE 'pg_toast%'
                  AND schema_name NOT LIKE 'pg_temp_%'
                ORDER BY schema_name
                """
            )
            schemas = [str(row["schema_name"]) for row in rows]
            if not schemas and self.pg_adapter.config.schema:
                schemas = [self.pg_adapter.config.schema]
            return schemas
        except Exception as exc:
            logger.error("PostgresInspector.get_schemas failed", exc_info=exc)
            raise DatabaseDiscoveryError(
                detail=f"Failed to list PostgreSQL schemas: {exc}",
                extra={"database": self.pg_adapter.config.database_name},
            ) from exc

    async def get_tables(
        self,
        schema: str,
    ) -> list[tuple[str, str, int, Optional[str]]]:
        try:
            sql = """
                SELECT
                    t.table_name,
                    t.table_type,
                    COALESCE(c.reltuples, 0)::bigint AS row_estimate,
                    obj_description((quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass) AS table_comment
                FROM information_schema.tables t
                LEFT JOIN pg_class c
                       ON c.relname = t.table_name
                      AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = t.table_schema)
                WHERE t.table_schema = :schema
                  AND t.table_type IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW')
                ORDER BY t.table_name
            """
            columns, rows = await self.pg_adapter.execute_raw(sql, {"schema": schema})

            results: list[tuple[str, str, int, Optional[str]]] = []
            for row in rows:
                raw_type = str(row["table_type"]) if row["table_type"] else ""
                if raw_type == "BASE TABLE":
                    t_type = "TABLE"
                elif raw_type == "VIEW":
                    t_type = "VIEW"
                elif raw_type == "MATERIALIZED VIEW":
                    t_type = "MATERIALIZED_VIEW"
                else:
                    t_type = "TABLE"
                row_est = int(row["row_estimate"]) if row["row_estimate"] is not None else 0
                comment = str(row["table_comment"]) if row["table_comment"] is not None else None
                results.append((str(row["table_name"]), t_type, row_est, comment))

            mat_sql = """
                SELECT
                    matviewname AS table_name,
                    'MATERIALIZED_VIEW' AS table_type,
                    COALESCE(c.reltuples, 0)::bigint AS row_estimate,
                    obj_description((quote_ident(schemaname) || '.' || quote_ident(matviewname))::regclass) AS table_comment
                FROM pg_matviews m
                LEFT JOIN pg_class c
                       ON c.relname = m.matviewname
                      AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = m.schemaname)
                WHERE m.schemaname = :schema
                ORDER BY matviewname
            """
            _, mat_rows = await self.pg_adapter.execute_raw(mat_sql, {"schema": schema})
            existing_mat_views = {
                t[0] for t in results if t[1] == "MATERIALIZED_VIEW"
            }
            for row in mat_rows:
                tname = str(row["table_name"])
                if tname in existing_mat_views:
                    continue
                row_est = int(row["row_estimate"]) if row["row_estimate"] is not None else 0
                comment = str(row["table_comment"]) if row["table_comment"] is not None else None
                results.append((tname, "MATERIALIZED_VIEW", row_est, comment))

            return results
        except Exception as exc:
            logger.error(
                "PostgresInspector.get_tables failed",
                extra={"schema": schema},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve tables for schema '{schema}': {exc}",
                extra={"schema": schema},
            ) from exc

    async def get_columns(
        self,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        try:
            sql = """
                SELECT
                    c.column_name,
                    c.data_type,
                    c.udt_name,
                    c.character_maximum_length,
                    c.numeric_precision,
                    c.numeric_scale,
                    c.is_nullable,
                    c.column_default,
                    c.ordinal_position,
                    c.collation_name,
                    c.domain_name,
                    col_description(
                        (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                        c.ordinal_position
                    ) AS column_comment,
                    t.oid AS type_oid,
                    t.typtype AS type_kind,
                    n.nspname AS type_schema
                FROM information_schema.columns c
                LEFT JOIN pg_type t ON t.typname = c.udt_name
                LEFT JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE c.table_schema = :schema
                  AND c.table_name = :table
                ORDER BY c.ordinal_position
            """
            _, rows = await self.pg_adapter.execute_raw(sql, {"schema": schema, "table": table})

            columns: list[dict[str, Any]] = []
            for row in rows:
                raw_udt = str(row["udt_name"] or row["data_type"] or "")
                raw_udt_lower = raw_udt.lower()
                if raw_udt_lower.startswith("_"):
                    raw_type_display = f"{raw_udt_lower[1:]}[]"
                else:
                    raw_type_display = str(row["data_type"] or raw_udt_lower)

                enum_values: Optional[list[str]] = None
                type_kind = row["type_kind"]
                type_oid = row["type_oid"]
                if type_kind == "e" and type_oid is not None:
                    enum_sql = """
                        SELECT enumlabel
                        FROM pg_enum
                        WHERE enumtypid = :oid
                        ORDER BY enumsortorder
                    """
                    _, enum_rows = await self.pg_adapter.execute_raw(enum_sql, {"oid": type_oid})
                    enum_values = [str(r["enumlabel"]) for r in enum_rows]

                columns.append(
                    {
                        "name": str(row["column_name"]),
                        "raw_type": raw_type_display,
                        "udt_name": raw_udt_lower,
                        "data_type": str(row["data_type"]) if row["data_type"] else None,
                        "type_oid": type_oid,
                        "type_kind": str(type_kind) if type_kind else None,
                        "type_schema": str(row["type_schema"]) if row["type_schema"] else None,
                        "domain_name": str(row["domain_name"]) if row["domain_name"] else None,
                        "character_maximum_length": (
                            int(row["character_maximum_length"])
                            if row["character_maximum_length"] is not None
                            else None
                        ),
                        "numeric_precision": (
                            int(row["numeric_precision"])
                            if row["numeric_precision"] is not None
                            else None
                        ),
                        "numeric_scale": (
                            int(row["numeric_scale"])
                            if row["numeric_scale"] is not None
                            else None
                        ),
                        "nullable": str(row["is_nullable"]) == "YES",
                        "default_value": (
                            str(row["column_default"])
                            if row["column_default"] is not None
                            else None
                        ),
                        "ordinal_position": (
                            int(row["ordinal_position"])
                            if row["ordinal_position"] is not None
                            else 0
                        ),
                        "collation": (
                            str(row["collation_name"])
                            if row["collation_name"] is not None
                            else None
                        ),
                        "comment": (
                            str(row["column_comment"])
                            if row["column_comment"] is not None
                            else None
                        ),
                        "enum_values": enum_values,
                    }
                )
            return columns
        except Exception as exc:
            logger.error(
                "PostgresInspector.get_columns failed",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve columns for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_constraints(
        self,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        try:
            sql = """
                WITH pk_cols AS (
                    SELECT
                        tc.constraint_name,
                        tc.constraint_type,
                        ARRAY(
                            SELECT kcu.column_name
                            FROM information_schema.key_column_usage kcu
                            WHERE kcu.constraint_name = tc.constraint_name
                              AND kcu.table_schema = tc.table_schema
                              AND kcu.table_name = tc.table_name
                            ORDER BY kcu.ordinal_position
                        ) AS column_names,
                        pg_get_constraintdef(c.oid) AS definition
                    FROM information_schema.table_constraints tc
                    JOIN pg_constraint c
                         ON c.conname = tc.constraint_name
                         AND c.conrelid = (quote_ident(tc.table_schema) || '.' || quote_ident(tc.table_name))::regclass
                    JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE tc.table_schema = :schema
                      AND tc.table_name = :table
                      AND n.nspname = :schema
                      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'CHECK', 'EXCLUDE')
                ),
                fk_details AS (
                    SELECT
                        tc.constraint_name,
                        'FOREIGN_KEY' AS constraint_type,
                        json_agg(
                            json_build_object(
                                'constrained_column', kcu.column_name,
                                'referred_schema', ccu.table_schema,
                                'referred_table', ccu.table_name,
                                'referred_column', ccu.column_name,
                                'ordinal_position', kcu.ordinal_position
                            ) ORDER BY kcu.ordinal_position
                        ) AS fk_parts,
                        pg_get_constraintdef(c.oid) AS definition
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                     AND tc.table_name = kcu.table_name
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema = tc.table_schema
                    JOIN pg_constraint c
                         ON c.conname = tc.constraint_name
                         AND c.conrelid = (quote_ident(tc.table_schema) || '.' || quote_ident(tc.table_name))::regclass
                    JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = :schema
                      AND tc.table_name = :table
                      AND n.nspname = :schema
                    GROUP BY tc.constraint_name, c.oid
                )
                SELECT
                    constraint_name,
                    constraint_type,
                    column_names,
                    NULL::jsonb AS fk_parts,
                    definition
                FROM pk_cols
                UNION ALL
                SELECT
                    constraint_name,
                    constraint_type,
                    NULL::text[] AS column_names,
                    fk_parts,
                    definition
                FROM fk_details
                ORDER BY constraint_name
            """
            _, rows = await self.pg_adapter.execute_raw(sql, {"schema": schema, "table": table})

            constraints: list[dict[str, Any]] = []
            type_map = {
                "PRIMARY KEY": "PRIMARY_KEY",
                "FOREIGN KEY": "FOREIGN_KEY",
                "UNIQUE": "UNIQUE",
                "CHECK": "CHECK",
                "EXCLUDE": "EXCLUDE",
                "PRIMARY_KEY": "PRIMARY_KEY",
                "FOREIGN_KEY": "FOREIGN_KEY",
            }
            for row in rows:
                raw_type = str(row["constraint_type"])
                c_type = type_map.get(raw_type, raw_type)
                base = {
                    "name": str(row["constraint_name"]),
                    "type": c_type,
                    "definition": (
                        str(row["definition"])
                        if row["definition"] is not None
                        else None
                    ),
                }
                if c_type == "FOREIGN_KEY":
                    fk_parts = row["fk_parts"] or []
                    constrained_cols: list[str] = []
                    targets: list[tuple[str, str, str]] = []
                    for part in fk_parts:
                        constrained_cols.append(str(part["constrained_column"]))
                        targets.append(
                            (
                                str(part["referred_schema"]),
                                str(part["referred_table"]),
                                str(part["referred_column"]),
                            )
                        )
                    if targets:
                        first = targets[0]
                        referred_schema = first[0]
                        referred_table = first[1]
                        referred_cols = [t[2] for t in targets]
                    else:
                        referred_schema = schema
                        referred_table = ""
                        referred_cols = []
                    base["columns"] = constrained_cols
                    base["constrained_columns"] = constrained_cols
                    base["referred_schema"] = referred_schema
                    base["referred_table"] = referred_table
                    base["referred_columns"] = referred_cols
                    base["target_qualified"] = (
                        f"{referred_schema}.{referred_table}"
                        if referred_schema and referred_table
                        else referred_table
                    )
                else:
                    cols = list(row["column_names"]) if isinstance(row["column_names"], (list, tuple)) else []
                    base["columns"] = [str(c) for c in cols]
                constraints.append(base)
            return constraints
        except Exception as exc:
            logger.error(
                "PostgresInspector.get_constraints failed",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve constraints for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_indexes(
        self,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        try:
            sql = """
                SELECT
                    i.relname AS index_name,
                    ARRAY(
                        SELECT pg_get_indexdef(idx.indexrelid, k + 1, true)
                        FROM generate_subscripts(idx.indkey, 1) AS k
                        ORDER BY k
                    ) AS column_names,
                    idx.indisunique AS is_unique,
                    idx.indisprimary AS is_primary,
                    idx.indisvalid AS is_valid,
                    am.amname AS access_method,
                    pg_get_indexdef(idx.indexrelid) AS definition
                FROM pg_index AS idx
                JOIN pg_class AS i ON i.oid = idx.indexrelid
                JOIN pg_am AS am ON am.oid = i.relam
                JOIN pg_namespace AS ns ON ns.oid = i.relnamespace
                WHERE idx.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
                  AND ns.nspname = :schema
                ORDER BY i.relname
            """
            _, rows = await self.pg_adapter.execute_raw(sql, {"schema": schema, "table": table})
            indexes: list[dict[str, Any]] = []
            for row in rows:
                col_names = list(row["column_names"]) if isinstance(row["column_names"], (list, tuple)) else []
                indexes.append(
                    {
                        "name": str(row["index_name"]),
                        "column_names": [str(c) for c in col_names],
                        "unique": bool(row["is_unique"]),
                        "is_primary": bool(row["is_primary"]),
                        "is_valid": bool(row["is_valid"]) if row["is_valid"] is not None else True,
                        "access_method": str(row["access_method"]) if row["access_method"] else None,
                        "definition": (
                            str(row["definition"])
                            if row["definition"] is not None
                            else None
                        ),
                    }
                )
            return indexes
        except Exception as exc:
            logger.error(
                "PostgresInspector.get_indexes failed",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve indexes for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_enum_values(
        self,
        schema: str,
        type_name: str,
    ) -> list[str]:
        try:
            sql = """
                SELECT e.enumlabel
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = :schema
                  AND t.typname = :type_name
                ORDER BY e.enumsortorder
            """
            _, rows = await self.pg_adapter.execute_raw(
                sql, {"schema": schema, "type_name": type_name}
            )
            return [str(r["enumlabel"]) for r in rows]
        except Exception as exc:
            logger.error(
                "PostgresInspector.get_enum_values failed",
                extra={"schema": schema, "type_name": type_name},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve enum values for {schema}.{type_name}: {exc}",
                extra={"schema": schema, "type_name": type_name},
            ) from exc
