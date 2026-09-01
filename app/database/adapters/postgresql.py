from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import Inspector, MetaData, Table, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.exceptions import DatabaseConnectionError, DatabaseDiscoveryError
from app.core.logging import get_logger
from app.database.adapters.base import DatabaseAdapter
from app.database.connection import (
    DatabaseConfig,
    build_connect_args,
    build_connection_string,
)
from app.models.database import (
    ColumnProfile,
    ColumnType,
)


logger = get_logger(__name__)


_PG_TYPE_MAP: dict[str, ColumnType] = {
    "int2": ColumnType.SMALLINT,
    "smallint": ColumnType.SMALLINT,
    "int4": ColumnType.INTEGER,
    "integer": ColumnType.INTEGER,
    "int": ColumnType.INTEGER,
    "int8": ColumnType.BIGINT,
    "bigint": ColumnType.BIGINT,
    "serial": ColumnType.INTEGER,
    "bigserial": ColumnType.BIGINT,
    "smallserial": ColumnType.SMALLINT,
    "bool": ColumnType.BOOLEAN,
    "boolean": ColumnType.BOOLEAN,
    "float4": ColumnType.FLOAT,
    "real": ColumnType.FLOAT,
    "float8": ColumnType.FLOAT,
    "double precision": ColumnType.FLOAT,
    "numeric": ColumnType.NUMERIC,
    "decimal": ColumnType.NUMERIC,
    "money": ColumnType.NUMERIC,
    "text": ColumnType.TEXT,
    "varchar": ColumnType.VARCHAR,
    "character varying": ColumnType.VARCHAR,
    "char": ColumnType.CHAR,
    "character": ColumnType.CHAR,
    "bpchar": ColumnType.CHAR,
    "citext": ColumnType.TEXT,
    "timestamptz": ColumnType.TIMESTAMPTZ,
    "timestamp with time zone": ColumnType.TIMESTAMPTZ,
    "timestamp": ColumnType.TIMESTAMP,
    "timestamp without time zone": ColumnType.TIMESTAMP,
    "date": ColumnType.DATE,
    "time": ColumnType.TIME,
    "timetz": ColumnType.TIME,
    "time without time zone": ColumnType.TIME,
    "time with time zone": ColumnType.TIME,
    "interval": ColumnType.TEXT,
    "json": ColumnType.JSON,
    "jsonb": ColumnType.JSONB,
    "uuid": ColumnType.UUID,
    "bytea": ColumnType.BYTEA,
    "blob": ColumnType.BYTEA,
    "regclass": ColumnType.TEXT,
    "regproc": ColumnType.TEXT,
    "regprocedure": ColumnType.TEXT,
    "regoper": ColumnType.TEXT,
    "regoperator": ColumnType.TEXT,
    "regrole": ColumnType.TEXT,
    "regnamespace": ColumnType.TEXT,
    "regtype": ColumnType.TEXT,
    "oid": ColumnType.BIGINT,
    "xid": ColumnType.BIGINT,
    "cid": ColumnType.INTEGER,
    "tid": ColumnType.TEXT,
    "inet": ColumnType.TEXT,
    "cidr": ColumnType.TEXT,
    "macaddr": ColumnType.TEXT,
    "macaddr8": ColumnType.TEXT,
    "point": ColumnType.TEXT,
    "line": ColumnType.TEXT,
    "lseg": ColumnType.TEXT,
    "box": ColumnType.TEXT,
    "path": ColumnType.TEXT,
    "polygon": ColumnType.TEXT,
    "circle": ColumnType.TEXT,
    "bit": ColumnType.TEXT,
    "bit varying": ColumnType.TEXT,
    "varbit": ColumnType.TEXT,
    "tsvector": ColumnType.TEXT,
    "tsquery": ColumnType.TEXT,
    "xml": ColumnType.TEXT,
    "pg_lsn": ColumnType.TEXT,
    "pg_snapshot": ColumnType.TEXT,
    "name": ColumnType.TEXT,
    "anyarray": ColumnType.TEXT,
    "_aclitem": ColumnType.TEXT,
    "array": ColumnType.TEXT,
    "hstore": ColumnType.JSON,
    "vector": ColumnType.TEXT,
}


class PostgreSQLAdapter(DatabaseAdapter):
    _engine: Optional[AsyncEngine] = None
    _connection: Optional[AsyncConnection] = None

    def __init__(self, config: DatabaseConfig) -> None:
        super().__init__(config)

    async def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            try:
                cs = build_connection_string(self.config)
                connect_args = build_connect_args(self.config)
                self._engine = create_async_engine(
                    cs.raw,
                    pool_size=5,
                    max_overflow=10,
                    pool_recycle=1800,
                    pool_pre_ping=True,
                    echo=False,
                    future=True,
                    connect_args=connect_args,
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                    },
                )
            except Exception as exc:
                logger.error(
                    "Failed to create PostgreSQL async engine",
                    extra={"host": self.config.host, "database": self.config.database_name},
                    exc_info=exc,
                )
                raise DatabaseConnectionError(
                    detail=f"Failed to create PostgreSQL engine: {exc}",
                    extra={
                        "host": self.config.host,
                        "database": self.config.database_name,
                    },
                ) from exc
        return self._engine

    async def test_connection(
        self,
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        server_info: dict[str, Any] = {}
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                ver_result = await conn.execute(text("SELECT version()"))
                version_row = ver_result.fetchone()
                if version_row:
                    version_str = version_row[0]
                    server_info["version"] = version_str
                    m = re.search(r"PostgreSQL\s+(\d+\.\d+)", version_str)
                    if m:
                        server_info["version_number"] = m.group(1)

                settings_result = await conn.execute(
                    text("SELECT name, setting FROM pg_settings WHERE name IN ('server_encoding', 'client_encoding', 'TimeZone', 'max_connections')")
                )
                for row in settings_result.mappings().all():
                    server_info[row["name"]] = row["setting"]

                curr_result = await conn.execute(text("SELECT current_database(), current_user, current_schema()"))
                curr_row = curr_result.fetchone()
                if curr_row:
                    server_info["database"] = curr_row[0]
                    server_info["user"] = curr_row[1]
                    server_info["schema"] = curr_row[2]

                return True, server_info, None
        except Exception as exc:
            logger.warning(
                "PostgreSQL connection test failed",
                extra={"host": self.config.host, "database": self.config.database_name},
                exc_info=exc,
            )
            return False, server_info, str(exc)

    async def connect(self) -> None:
        if self._is_connected and self._connection is not None:
            return
        try:
            engine = await self._get_engine()
            self._connection = await engine.connect()
            self._is_connected = True
            logger.info(
                "PostgreSQL adapter connected",
                extra={"host": self.config.host, "database": self.config.database_name},
            )
        except Exception as exc:
            logger.error(
                "PostgreSQL adapter connect failed",
                extra={"host": self.config.host, "database": self.config.database_name},
                exc_info=exc,
            )
            raise DatabaseConnectionError(
                detail=f"Failed to connect to PostgreSQL: {exc}",
                extra={
                    "host": self.config.host,
                    "database": self.config.database_name,
                },
            ) from exc

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as exc:
                logger.warning("Error closing PostgreSQL connection", exc_info=exc)
            self._connection = None
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception as exc:
                logger.warning("Error disposing PostgreSQL engine", exc_info=exc)
            self._engine = None
        self._is_connected = False
        logger.info("PostgreSQL adapter closed")

    async def _inspector(self, conn: AsyncConnection) -> Inspector:
        def _get_inspector(sync_conn):
            from sqlalchemy import inspect as sa_inspect
            return sa_inspect(sync_conn)
        return await conn.run_sync(_get_inspector)

    async def get_schemas(self) -> list[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT schema_name
                        FROM information_schema.schemata
                        WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
                          AND schema_name NOT LIKE 'pg_toast%'
                          AND schema_name NOT LIKE 'pg_temp_%'
                        ORDER BY schema_name
                        """
                    )
                )
                schemas = [row[0] for row in result.fetchall()]
                if not schemas:
                    schemas = [self.config.schema]
                return schemas
        except Exception as exc:
            logger.error("Failed to list PostgreSQL schemas", exc_info=exc)
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve schemas: {exc}",
                extra={"database": self.config.database_name},
            ) from exc

    async def get_tables(self, schema: str) -> list[tuple[str, str]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT table_name, table_type
                        FROM information_schema.tables
                        WHERE table_schema = :schema
                          AND table_type IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW')
                        ORDER BY table_name
                        """
                    ),
                    {"schema": schema},
                )
                tables: list[tuple[str, str]] = []
                for row in result.fetchall():
                    t_type = "TABLE"
                    if row[1] == "VIEW":
                        t_type = "VIEW"
                    elif row[1] == "MATERIALIZED VIEW":
                        t_type = "MATERIALIZED_VIEW"
                    tables.append((row[0], t_type))

                mat_view_result = await conn.execute(
                    text(
                        """
                        SELECT matviewname
                        FROM pg_matviews
                        WHERE schemaname = :schema
                        ORDER BY matviewname
                        """
                    ),
                    {"schema": schema},
                )
                existing_mat_views = {t[0] for t in tables if t[1] == "MATERIALIZED_VIEW"}
                for row in mat_view_result.fetchall():
                    if row[0] not in existing_mat_views:
                        tables.append((row[0], "MATERIALIZED_VIEW"))

                return tables
        except Exception as exc:
            logger.error(
                "Failed to list PostgreSQL tables",
                extra={"schema": schema},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve tables for schema '{schema}': {exc}",
                extra={"schema": schema},
            ) from exc

    async def get_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
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
                            col_description(
                                (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                                c.ordinal_position
                            ) as column_comment
                        FROM information_schema.columns c
                        WHERE c.table_schema = :schema
                          AND c.table_name = :table
                        ORDER BY c.ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                columns: list[dict[str, Any]] = []
                for row in result.mappings().all():
                    raw_type = row["udt_name"] or row["data_type"] or ""
                    raw_type_lower = raw_type.lower()
                    if raw_type_lower.startswith("_"):
                        raw_type_display = f"{raw_type_lower[1:]}[]"
                    else:
                        raw_type_display = row["data_type"] or raw_type_lower

                    enum_values: Optional[list[str]] = None
                    type_oid_result = await conn.execute(
                        text(
                            """
                            SELECT t.oid, t.typtype
                            FROM pg_type t
                            JOIN pg_namespace n ON n.oid = t.typnamespace
                            WHERE t.typname = :udt_name
                            LIMIT 1
                            """
                        ),
                        {"udt_name": raw_type_lower},
                    )
                    type_row = type_oid_result.fetchone()
                    if type_row and type_row[1] == "e":
                        enum_result = await conn.execute(
                            text(
                                """
                                SELECT enumlabel
                                FROM pg_enum
                                WHERE enumtypid = :oid
                                ORDER BY enumsortorder
                                """
                            ),
                            {"oid": type_row[0]},
                        )
                        enum_values = [r[0] for r in enum_result.fetchall()]

                    columns.append(
                        {
                            "name": row["column_name"],
                            "raw_type": raw_type_display,
                            "udt_name": raw_type_lower,
                            "character_maximum_length": row["character_maximum_length"],
                            "numeric_precision": row["numeric_precision"],
                            "numeric_scale": row["numeric_scale"],
                            "nullable": row["is_nullable"] == "YES",
                            "default_value": row["column_default"],
                            "ordinal_position": row["ordinal_position"],
                            "collation": row["collation_name"],
                            "comment": row["column_comment"],
                            "enum_values": enum_values,
                        }
                    )
                return columns
        except Exception as exc:
            logger.error(
                "Failed to list PostgreSQL columns",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve columns for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_primary_keys(self, schema: str, table: str) -> list[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                        WHERE i.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
                          AND i.indisprimary
                        ORDER BY array_position(i.indkey, a.attnum)
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                return [row[0] for row in result.fetchall()]
        except Exception as exc:
            logger.error(
                "Failed to get PostgreSQL primary keys",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve primary keys for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_foreign_keys(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            tc.constraint_name,
                            kcu.column_name,
                            ccu.table_schema AS foreign_table_schema,
                            ccu.table_name AS foreign_table_name,
                            ccu.column_name AS foreign_column_name,
                            ordinal_position
                        FROM information_schema.table_constraints AS tc
                        JOIN information_schema.key_column_usage AS kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.constraint_column_usage AS ccu
                          ON ccu.constraint_name = tc.constraint_name
                          AND ccu.table_schema = tc.table_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND tc.table_schema = :schema
                          AND tc.table_name = :table
                        ORDER BY tc.constraint_name, ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                fk_map: dict[str, dict[str, Any]] = {}
                for row in result.fetchall():
                    name = row[0]
                    if name not in fk_map:
                        fk_map[name] = {
                            "name": name,
                            "constrained_columns": [],
                            "referred_schema": row[2],
                            "referred_table": row[3],
                            "referred_columns": [],
                        }
                    fk_map[name]["constrained_columns"].append(row[1])
                    fk_map[name]["referred_columns"].append(row[4])
                return list(fk_map.values())
        except Exception as exc:
            logger.error(
                "Failed to get PostgreSQL foreign keys",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve foreign keys for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_indexes(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            i.relname AS index_name,
                            ARRAY(
                                SELECT pg_get_indexdef(idx.indexrelid, k + 1, true)
                                FROM generate_subscripts(idx.indkey, 1) AS k
                                ORDER BY k
                            ) AS column_names,
                            idx.indisunique AS is_unique,
                            idx.indisprimary AS is_primary
                        FROM pg_index AS idx
                        JOIN pg_class AS i ON i.oid = idx.indexrelid
                        JOIN pg_namespace AS ns ON ns.oid = i.relnamespace
                        WHERE idx.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
                          AND ns.nspname = :schema
                        ORDER BY i.relname
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                indexes: list[dict[str, Any]] = []
                for row in result.fetchall():
                    col_names = list(row[1]) if isinstance(row[1], (list, tuple)) else []
                    indexes.append(
                        {
                            "name": row[0],
                            "column_names": col_names,
                            "unique": bool(row[2]),
                            "is_primary": bool(row[3]),
                        }
                    )
                return indexes
        except Exception as exc:
            logger.error(
                "Failed to get PostgreSQL indexes",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve indexes for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_constraints(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
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
                        JOIN pg_constraint c ON c.conname = tc.constraint_name
                        JOIN pg_namespace n ON n.oid = c.connamespace
                        WHERE tc.table_schema = :schema
                          AND tc.table_name = :table
                          AND n.nspname = :schema
                        ORDER BY tc.constraint_name
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                constraints: list[dict[str, Any]] = []
                for row in result.fetchall():
                    c_type_map = {
                        "PRIMARY KEY": "PRIMARY_KEY",
                        "FOREIGN KEY": "FOREIGN_KEY",
                        "UNIQUE": "UNIQUE",
                        "CHECK": "CHECK",
                        "EXCLUDE": "EXCLUDE",
                    }
                    constraints.append(
                        {
                            "name": row[0],
                            "type": c_type_map.get(row[1], row[1]),
                            "columns": list(row[2]) if isinstance(row[2], (list, tuple)) else [],
                            "definition": row[3],
                        }
                    )
                return constraints
        except Exception as exc:
            logger.error(
                "Failed to get PostgreSQL constraints",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve constraints for {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def get_table_comment(self, schema: str, table: str) -> Optional[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT obj_description((quote_ident(:schema) || '.' || quote_ident(:table))::regclass)
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                row = result.fetchone()
                if row and row[0]:
                    return str(row[0])
                return None
        except Exception:
            return None

    async def get_estimated_row_count(self, schema: str, table: str) -> int:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT COALESCE(c.reltuples, 0)::bigint AS estimate
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = :schema
                          AND c.relname = :table
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                row = result.fetchone()
                if row:
                    return int(row[0])
                return 0
        except Exception:
            return 0

    async def execute_raw(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                if timeout:
                    await conn.execute(text(f"SET statement_timeout = :timeout_ms"), {"timeout_ms": timeout * 1000})
                bound_params = params or {}
                result = await conn.execute(text(query), bound_params)
                columns = list(result.keys()) if result.returns_rows else []
                rows: list[dict[str, Any]] = []
                if result.returns_rows:
                    for row in result.mappings().all():
                        rows.append(dict(row))
                return columns, rows
        except Exception as exc:
            logger.error(
                "PostgreSQL raw query execution failed",
                extra={"query_preview": query[:200]},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Query execution failed: {exc}",
                extra={"query_preview": query[:200]},
            ) from exc

    async def execute_readonly_sql(
        self,
        sql: str,
        timeout: Optional[int] = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        effective_timeout = timeout or self.config.connect_timeout
        was_truncated = False
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(
                    text("SET statement_timeout = :timeout_ms"),
                    {"timeout_ms": effective_timeout * 1000},
                )

                limited_sql = sql.rstrip().rstrip(";")
                if not re.search(r"\bLIMIT\s+\d+", limited_sql, re.IGNORECASE):
                    limited_sql = f"{limited_sql} LIMIT {max_rows + 1}"

                result = await conn.execute(text(limited_sql))
                columns = list(result.keys()) if result.returns_rows else []
                rows: list[dict[str, Any]] = []
                if result.returns_rows:
                    all_rows = result.mappings().all()
                    if len(all_rows) > max_rows:
                        was_truncated = True
                        all_rows = all_rows[:max_rows]
                    for row in all_rows:
                        rows.append(dict(row))
                await conn.rollback()
                return columns, rows, was_truncated
        except Exception as exc:
            logger.error(
                "PostgreSQL readonly SQL execution failed",
                extra={"sql_preview": sql[:200]},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Readonly SQL execution failed: {exc}",
                extra={"sql_preview": sql[:200]},
            ) from exc

    async def sample_rows(
        self,
        schema: str,
        table: str,
        order_by_col: Optional[str] = None,
        direction: str = "DESC",
        limit: int = 50,
        exclude_cols: Optional[list[str]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                cols_result = await conn.execute(
                    text(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = :schema AND table_name = :table
                        ORDER BY ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                all_cols: list[tuple[str, str]] = [(r[0], r[1]) for r in cols_result.fetchall()]

                exclude_set = set(exclude_cols or [])
                blob_types = {"bytea", "blob", "oid", "raw", "long raw"}
                selected_cols: list[str] = []
                for cname, ctype in all_cols:
                    if cname in exclude_set:
                        continue
                    if ctype.lower() in blob_types:
                        continue
                    selected_cols.append(cname)

                if not selected_cols:
                    return [], []

                effective_order_col: Optional[str] = None
                effective_dir: str = direction.upper() if direction.upper() in {"ASC", "DESC"} else "DESC"

                if order_by_col and order_by_col in selected_cols:
                    effective_order_col = order_by_col
                else:
                    pk_result = await conn.execute(
                        text(
                            """
                            SELECT a.attname
                            FROM pg_index i
                            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                            WHERE i.indrelid = (quote_ident(:schema) || '.' || quote_ident(:table))::regclass
                              AND i.indisprimary
                            ORDER BY array_position(i.indkey, a.attnum)
                            LIMIT 1
                            """
                        ),
                        {"schema": schema, "table": table},
                    )
                    pk_row = pk_result.fetchone()
                    if pk_row and pk_row[0] in selected_cols:
                        effective_order_col = pk_row[0]
                    else:
                        lower_cols = {c.lower(): c for c in selected_cols}
                        for candidate in ("created_at", "updated_at", "inserted_at", "modified_at", "fecha_creacion", "fecha_actualizacion"):
                            if candidate in lower_cols:
                                effective_order_col = lower_cols[candidate]
                                break
                        if effective_order_col is None and selected_cols:
                            effective_order_col = selected_cols[0]

                col_list_sql = ", ".join(f'"{c}"' for c in selected_cols)
                order_sql = ""
                if effective_order_col:
                    order_sql = f' ORDER BY "{effective_order_col}" {effective_dir}'
                query = f'SELECT {col_list_sql} FROM "{schema}"."{table}"{order_sql} LIMIT :limit'

                result = await conn.execute(text(query), {"limit": limit})
                rows: list[dict[str, Any]] = []
                for row in result.mappings().all():
                    rows.append(dict(row))
                return selected_cols, rows
        except Exception as exc:
            logger.error(
                "PostgreSQL sample rows failed",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to sample rows from {schema}.{table}: {exc}",
                extra={"schema": schema, "table": table},
            ) from exc

    async def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        approx_mode: bool = True,
    ) -> ColumnProfile:
        profile = ColumnProfile()
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                approx_result = await conn.execute(
                    text(
                        """
                        SELECT
                            s.n_distinct,
                            s.null_frac,
                            s.most_common_vals
                        FROM pg_stats s
                        WHERE s.schemaname = :schema
                          AND s.tablename = :table
                          AND s.attname = :column
                        """
                    ),
                    {"schema": schema, "table": table, "column": column},
                )
                approx_row = approx_result.fetchone()
                if approx_row:
                    n_distinct_raw = approx_row[0]
                    if isinstance(n_distinct_raw, float):
                        if n_distinct_raw < 0:
                            n_tuples_result = await conn.execute(
                                text(
                                    """
                                    SELECT reltuples::bigint FROM pg_class c
                                    JOIN pg_namespace n ON n.oid = c.relnamespace
                                    WHERE n.nspname = :schema AND c.relname = :table
                                    """
                                ),
                                {"schema": schema, "table": table},
                            )
                            n_tuples_row = n_tuples_result.fetchone()
                            if n_tuples_row:
                                reltuples = max(int(n_tuples_row[0]), 0)
                                profile.distinct_count_approx = max(int(abs(n_distinct_raw) * reltuples), 0)
                        else:
                            profile.distinct_count_approx = max(int(n_distinct_raw), 0)
                    elif isinstance(n_distinct_raw, int):
                        profile.distinct_count_approx = max(n_distinct_raw, 0)

                    null_frac = approx_row[1]
                    if isinstance(null_frac, float):
                        profile.null_percentage = round(max(0.0, min(100.0, null_frac * 100.0)), 4)

                    mcv = approx_row[2]
                    if mcv is not None:
                        try:
                            if isinstance(mcv, list):
                                mcv_list = list(mcv)
                            else:
                                mcv_list = [str(mcv)]
                            profile.sample_distinct_values = [str(v) for v in mcv_list[:10]]
                        except Exception:
                            pass

                if not approx_mode:
                    try:
                        est_count = await self.get_estimated_row_count(schema, table)
                        if est_count < 100000:
                            min_max_result = await conn.execute(
                                text(
                                    f'SELECT MIN("{column}") AS min_val, MAX("{column}") AS max_val '
                                    f'FROM "{schema}"."{table}"'
                                )
                            )
                            mm_row = min_max_result.fetchone()
                            if mm_row:
                                profile.min_value = mm_row[0]
                                profile.max_value = mm_row[1]

                            null_result = await conn.execute(
                                text(
                                    f'SELECT COUNT(*) AS total, '
                                    f'SUM(CASE WHEN "{column}" IS NULL THEN 1 ELSE 0 END) AS nulls '
                                    f'FROM "{schema}"."{table}"'
                                )
                            )
                            null_row = null_result.fetchone()
                            if null_row and null_row[0] and null_row[0] > 0:
                                total = int(null_row[0])
                                nulls = int(null_row[1])
                                profile.null_percentage = round(nulls * 100.0 / total, 4)

                            distinct_result = await conn.execute(
                                text(
                                    f'SELECT COUNT(DISTINCT "{column}") FROM "{schema}"."{table}"'
                                )
                            )
                            d_row = distinct_result.fetchone()
                            if d_row:
                                profile.distinct_count_approx = int(d_row[0])

                            sample_vals_result = await conn.execute(
                                text(
                                    f'SELECT DISTINCT "{column}" FROM "{schema}"."{table}" '
                                    f'WHERE "{column}" IS NOT NULL LIMIT 10'
                                )
                            )
                            vals = [r[0] for r in sample_vals_result.fetchall()]
                            if vals:
                                profile.sample_distinct_values = [str(v) for v in vals]
                    except Exception as exc_inner:
                        logger.warning(
                            "Full column profile mode failed, using approx",
                            extra={"column": column, "table": f"{schema}.{table}"},
                            exc_info=exc_inner,
                        )
                return profile
        except Exception as exc:
            logger.error(
                "PostgreSQL column profiling failed",
                extra={"schema": schema, "table": table, "column": column},
                exc_info=exc,
            )
            return profile

    def map_column_type(self, raw_type_str: str) -> ColumnType:
        if not raw_type_str:
            return ColumnType.UNKNOWN
        raw = raw_type_str.strip().lower()
        if raw.startswith("_"):
            base = raw[1:]
            if base.endswith("[]"):
                base = base[:-2]
            mapped = _PG_TYPE_MAP.get(base, ColumnType.UNKNOWN)
            return mapped if mapped != ColumnType.UNKNOWN else ColumnType.TEXT
        if raw.endswith("[]"):
            base = raw[:-2]
            mapped = _PG_TYPE_MAP.get(base, ColumnType.UNKNOWN)
            return mapped if mapped != ColumnType.UNKNOWN else ColumnType.TEXT
        mapped = _PG_TYPE_MAP.get(raw)
        if mapped:
            return mapped
        if raw in {"_aclitem", "aclitem"}:
            return ColumnType.TEXT
        if "numeric" in raw or "decimal" in raw:
            return ColumnType.NUMERIC
        if "int" in raw:
            if "big" in raw or "int8" in raw:
                return ColumnType.BIGINT
            if "small" in raw or "int2" in raw:
                return ColumnType.SMALLINT
            return ColumnType.INTEGER
        if "char" in raw or "text" in raw or "string" in raw:
            if "var" in raw or "varying" in raw:
                return ColumnType.VARCHAR
            return ColumnType.CHAR
        if "bool" in raw:
            return ColumnType.BOOLEAN
        if "float" in raw or "double" in raw or "real" in raw:
            return ColumnType.FLOAT
        if "json" in raw:
            if "jsonb" in raw:
                return ColumnType.JSONB
            return ColumnType.JSON
        if "uuid" in raw:
            return ColumnType.UUID
        if "bytea" in raw or "blob" in raw:
            return ColumnType.BYTEA
        if "timestamptz" in raw or "timestamp with time" in raw:
            return ColumnType.TIMESTAMPTZ
        if "timestamp" in raw:
            return ColumnType.TIMESTAMP
        if "date" in raw:
            return ColumnType.DATE
        if "time" in raw:
            return ColumnType.TIME
        return ColumnType.UNKNOWN
