from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import Inspector, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker, create_async_engine

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
    ConstraintMetadata,
    ConstraintType,
    IndexMetadata,
    RelationshipType,
    TableType,
)


logger = get_logger(__name__)


_MSSQL_TYPE_MAP: dict[str, ColumnType] = {
    "tinyint": ColumnType.SMALLINT,
    "smallint": ColumnType.SMALLINT,
    "int": ColumnType.INTEGER,
    "integer": ColumnType.INTEGER,
    "bigint": ColumnType.BIGINT,
    "bit": ColumnType.BOOLEAN,
    "bool": ColumnType.BOOLEAN,
    "boolean": ColumnType.BOOLEAN,
    "decimal": ColumnType.NUMERIC,
    "dec": ColumnType.NUMERIC,
    "numeric": ColumnType.NUMERIC,
    "number": ColumnType.NUMERIC,
    "money": ColumnType.NUMERIC,
    "smallmoney": ColumnType.NUMERIC,
    "float": ColumnType.FLOAT,
    "real": ColumnType.FLOAT,
    "double": ColumnType.FLOAT,
    "double precision": ColumnType.FLOAT,
    "char": ColumnType.CHAR,
    "character": ColumnType.CHAR,
    "nchar": ColumnType.CHAR,
    "national character": ColumnType.CHAR,
    "varchar": ColumnType.VARCHAR,
    "character varying": ColumnType.VARCHAR,
    "nvarchar": ColumnType.TEXT,
    "national character varying": ColumnType.TEXT,
    "text": ColumnType.TEXT,
    "ntext": ColumnType.TEXT,
    "varchar(max)": ColumnType.TEXT,
    "nvarchar(max)": ColumnType.TEXT,
    "date": ColumnType.DATE,
    "time": ColumnType.TIME,
    "datetime": ColumnType.TIMESTAMP,
    "datetime2": ColumnType.TIMESTAMP,
    "smalldatetime": ColumnType.TIMESTAMP,
    "timestamp": ColumnType.TIMESTAMP,
    "datetimeoffset": ColumnType.TIMESTAMPTZ,
    "rowversion": ColumnType.BYTEA,
    "binary": ColumnType.BYTEA,
    "varbinary": ColumnType.BYTEA,
    "varbinary(max)": ColumnType.BYTEA,
    "image": ColumnType.BYTEA,
    "blob": ColumnType.BYTEA,
    "uniqueidentifier": ColumnType.UUID,
    "guid": ColumnType.UUID,
    "xml": ColumnType.TEXT,
    "json": ColumnType.JSON,
    "sql_variant": ColumnType.TEXT,
    "table": ColumnType.TEXT,
    "cursor": ColumnType.TEXT,
    "hierarchyid": ColumnType.TEXT,
    "geometry": ColumnType.TEXT,
    "geography": ColumnType.TEXT,
}


class SQLServerAdapter(DatabaseAdapter):
    _engine: Optional[AsyncEngine] = None
    _connection: Optional[AsyncConnection] = None

    def __init__(self, config: DatabaseConfig) -> None:
        super().__init__(config)

    async def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            try:
                cs = build_connection_string(self.config)
                connect_args = build_connect_args(self.config)
                default_schema = self.config.schema or "dbo"
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
                        "schema_translate_map": {None: default_schema},
                        "isolation_level": "READ COMMITTED",
                    },
                )
            except ImportError as exc:
                logger.error(
                    "Missing optional MSSQL driver dependency (aioodbc/pyodbc)",
                    extra={"host": self.config.host, "database": self.config.database_name},
                    exc_info=exc,
                )
                raise DatabaseConnectionError(
                    detail=(
                        "Falta instalar el driver ODBC 18 para SQL Server en el host y las dependencias "
                        "opcionales aioodbc+pyodbc (ya incluidas en pyproject.toml). Ubuntu/Debian: "
                        "sudo ACCEPT_EULA=Y apt-get install msodbcsql18 unixodbc unixodbc-dev. "
                        "Windows: winget install Microsoft.ODBC.Driver.18.SQLServer. "
                        "macOS: brew install unixodbc microsoft/mssql-release/msodbcsql18."
                    ),
                    extra={
                        "host": self.config.host,
                        "database": self.config.database_name,
                    },
                ) from exc
            except Exception as exc:
                logger.error(
                    "Failed to create SQL Server async engine",
                    extra={"host": self.config.host, "database": self.config.database_name},
                    exc_info=exc,
                )
                raise DatabaseConnectionError(
                    detail=f"Failed to create SQL Server engine: {exc}",
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
                ver_result = await conn.execute(text("SELECT @@VERSION"))
                version_row = ver_result.fetchone()
                if version_row and version_row[0]:
                    version_str = str(version_row[0])
                    server_info["version"] = version_str
                    m = re.search(r"Microsoft SQL Server\s+(\d+\.\d+)", version_str)
                    if m:
                        server_info["version_number"] = m.group(1)
                    else:
                        m2 = re.search(r"SQL Server\s+(\d+)", version_str)
                        if m2:
                            server_info["version_number"] = m2.group(1)

                try:
                    compat_result = await conn.execute(
                        text("SELECT name, value, value_in_use FROM sys.configurations WHERE name IN ('show advanced options', 'max degree of parallelism', 'cost threshold for parallelism')")
                    )
                    for row in compat_result.mappings().all():
                        server_info[f"cfg_{row['name']}"] = row["value_in_use"]
                except Exception:
                    pass

                curr_result = await conn.execute(
                    text("SELECT DB_NAME(), SUSER_SNAME(), SCHEMA_NAME(), @@SERVERNAME, @@LANGUAGE, @@SPID")
                )
                curr_row = curr_result.fetchone()
                if curr_row:
                    server_info["database"] = curr_row[0]
                    server_info["user"] = curr_row[1]
                    server_info["schema"] = curr_row[2] or self.config.schema or "dbo"
                    server_info["server"] = curr_row[3]
                    server_info["language"] = curr_row[4]
                    server_info["spid"] = curr_row[5]

                return True, server_info, None
        except Exception as exc:
            logger.warning(
                "SQL Server connection test failed",
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
                "SQL Server adapter connected",
                extra={"host": self.config.host, "database": self.config.database_name},
            )
        except Exception as exc:
            logger.error(
                "SQL Server adapter connect failed",
                extra={"host": self.config.host, "database": self.config.database_name},
                exc_info=exc,
            )
            raise DatabaseConnectionError(
                detail=f"Failed to connect to SQL Server: {exc}",
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
                logger.warning("Error closing SQL Server connection", exc_info=exc)
            self._connection = None
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception as exc:
                logger.warning("Error disposing SQL Server engine", exc_info=exc)
            self._engine = None
        self._is_connected = False
        logger.info("SQL Server adapter closed")

    async def _inspector(self, conn: AsyncConnection) -> Inspector:
        def _get_inspector(sync_conn):
            from sqlalchemy import inspect as sa_inspect
            return sa_inspect(sync_conn)
        return await conn.run_sync(_get_inspector)

    def map_column_type(self, raw_type_str: str) -> ColumnType:
        if raw_type_str is None:
            return ColumnType.TEXT
        normalized = str(raw_type_str).strip().lower().split("(")[0].strip()
        mapped = _MSSQL_TYPE_MAP.get(normalized)
        if mapped is not None:
            return mapped
        if "char" in normalized:
            return ColumnType.VARCHAR
        if "int" in normalized:
            return ColumnType.BIGINT
        if normalized.startswith("datetime"):
            return ColumnType.TIMESTAMP
        if normalized.endswith("binary") or normalized in {"rowversion"}:
            return ColumnType.BYTEA
        return ColumnType.TEXT

    async def get_schemas(self) -> list[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT s.name AS schema_name
                        FROM sys.schemas s
                        WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner',
                                             'db_accessadmin', 'db_securityadmin', 'db_ddladmin',
                                             'db_backupoperator', 'db_datareader', 'db_datawriter',
                                             'db_denydatareader', 'db_denydatawriter')
                          AND s.name NOT LIKE 'db_%'
                          AND s.name NOT LIKE 'MS_%'
                          AND s.name NOT LIKE '##%'
                          AND s.name NOT LIKE '#%'
                        ORDER BY s.name
                        """
                    )
                )
                schemas = [row[0] for row in result.fetchall()]
                if not schemas:
                    schemas = [self.config.schema or "dbo"]
                return schemas
        except Exception as exc:
            logger.error("Failed to list SQL Server schemas", exc_info=exc)
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
                        SELECT t.name AS table_name,
                               CASE WHEN t.type = 'V' THEN 'VIEW'
                                    WHEN t.type = 'U' THEN 'TABLE'
                                    ELSE 'TABLE' END AS table_type
                        FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        WHERE s.name = :schema
                          AND t.is_ms_shipped = 0
                        UNION ALL
                        SELECT v.name AS table_name, 'VIEW' AS table_type
                        FROM sys.views v
                        JOIN sys.schemas s ON v.schema_id = s.schema_id
                        WHERE s.name = :schema
                          AND v.is_ms_shipped = 0
                        ORDER BY table_name
                        """
                    ),
                    {"schema": schema},
                )
                tables: list[tuple[str, str]] = []
                for row in result.fetchall():
                    tname = row[0]
                    ttype = row[1] or "TABLE"
                    tt = TableType.TABLE if ttype.upper() == "TABLE" else TableType.VIEW
                    tables.append((tname, str(tt.value if hasattr(tt, "value") else tt)))
                return tables
        except Exception as exc:
            logger.error(
                "Failed to list SQL Server tables for schema",
                extra={"schema": schema},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve tables in schema {schema!r}: {exc}",
                extra={"database": self.config.database_name, "schema": schema},
            ) from exc

    async def get_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            c.column_ordinal AS ordinal_position,
                            c.name AS column_name,
                            TYPE_NAME(c.system_type_id) AS data_type,
                            c.max_length AS character_maximum_length,
                            c.precision AS numeric_precision,
                            c.scale AS numeric_scale,
                            CASE c.is_nullable WHEN 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
                            c.is_identity AS is_identity,
                            c.is_computed AS is_computed,
                            OBJECT_DEFINITION(c.default_object_id) AS column_default,
                            dc.definition AS default_definition,
                            ic.is_included_column AS is_included_column
                        FROM sys.columns c
                        JOIN sys.tables t ON c.object_id = t.object_id
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        LEFT JOIN sys.default_constraints dc
                               ON c.default_object_id = dc.object_id
                              AND c.column_id = dc.parent_column_id
                        LEFT JOIN sys.index_columns ic
                               ON c.object_id = ic.object_id
                              AND c.column_id = ic.column_id
                        WHERE s.name = :schema
                          AND t.name = :table
                        UNION ALL
                        SELECT
                            c.column_ordinal AS ordinal_position,
                            c.name AS column_name,
                            TYPE_NAME(c.system_type_id) AS data_type,
                            c.max_length AS character_maximum_length,
                            c.precision AS numeric_precision,
                            c.scale AS numeric_scale,
                            CASE c.is_nullable WHEN 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
                            CAST(0 AS BIT) AS is_identity,
                            c.is_computed AS is_computed,
                            NULL AS column_default,
                            NULL AS default_definition,
                            NULL AS is_included_column
                        FROM sys.columns c
                        JOIN sys.views v ON c.object_id = v.object_id
                        JOIN sys.schemas s ON v.schema_id = s.schema_id
                        WHERE s.name = :schema
                          AND v.name = :table
                          AND NOT EXISTS (
                              SELECT 1
                              FROM sys.tables t2
                              JOIN sys.schemas s2 ON t2.schema_id = s2.schema_id
                              WHERE s2.name = :schema AND t2.name = :table
                          )
                        ORDER BY ordinal_position
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                columns: list[dict[str, Any]] = []
                seen: set[str] = set()
                for row in result.mappings().all():
                    name = str(row["column_name"])
                    if name in seen:
                        continue
                    seen.add(name)
                    raw_type = str(row["data_type"] or "").lower()
                    char_len = row["character_maximum_length"]
                    try:
                        if char_len is not None and raw_type in {"varchar", "char", "nvarchar", "nchar"}:
                            n = int(char_len)
                            if raw_type in {"nvarchar", "nchar"}:
                                n = n // 2 if n > 0 else n
                            char_len_out = None if n == -1 else n
                        else:
                            char_len_out = None if char_len is None else int(char_len)
                    except Exception:
                        char_len_out = None
                    try:
                        num_prec = None if row["numeric_precision"] is None else int(row["numeric_precision"])
                        num_scale = None if row["numeric_scale"] is None else int(row["numeric_scale"])
                    except Exception:
                        num_prec = None
                        num_scale = None
                    nullable = str(row["is_nullable"] or "NO").strip().upper() == "YES"
                    is_identity = bool(row["is_identity"])
                    is_computed = bool(row["is_computed"])
                    default_val = row["default_definition"] or row["column_default"]
                    col_type = self.map_column_type(raw_type)
                    columns.append(
                        {
                            "name": name,
                            "raw_type": raw_type,
                            "type": col_type,
                            "ordinal_position": int(row["ordinal_position"] or 0),
                            "nullable": nullable,
                            "max_length": char_len_out,
                            "numeric_precision": num_prec,
                            "numeric_scale": num_scale,
                            "is_identity": is_identity,
                            "is_computed": is_computed,
                            "default": str(default_val).strip() if default_val is not None else None,
                            "description": None,
                        }
                    )
                return columns
        except Exception as exc:
            logger.error(
                "Failed to list SQL Server columns",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Failed to retrieve columns for {schema}.{table!r}: {exc}",
                extra={"database": self.config.database_name, "schema": schema, "table": table},
            ) from exc

    async def get_primary_keys(self, schema: str, table: str) -> list[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT c.name AS column_name, ic.key_ordinal AS ordinal_position
                        FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        JOIN sys.indexes i ON t.object_id = i.object_id
                         AND i.is_primary_key = 1
                        JOIN sys.index_columns ic
                          ON i.object_id = ic.object_id
                         AND i.index_id = ic.index_id
                        JOIN sys.columns c
                          ON ic.object_id = c.object_id
                         AND ic.column_id = c.column_id
                        WHERE s.name = :schema
                          AND t.name = :table
                        ORDER BY ic.key_ordinal
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                return [row[0] for row in result.fetchall()]
        except Exception as exc:
            logger.warning(
                "Failed to retrieve SQL Server primary keys",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            return []

    async def get_foreign_keys(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            fk.name AS constraint_name,
                            OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
                            OBJECT_NAME(fk.referenced_object_id) AS parent_table,
                            rc.name AS parent_column,
                            OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
                            OBJECT_NAME(fk.parent_object_id) AS child_table,
                            cc.name AS child_column,
                            fkc.constraint_column_id AS ordinal_position
                        FROM sys.foreign_keys fk
                        JOIN sys.foreign_key_columns fkc
                          ON fk.object_id = fkc.constraint_object_id
                        JOIN sys.columns rc
                          ON fkc.referenced_object_id = rc.object_id
                         AND fkc.referenced_column_id = rc.column_id
                        JOIN sys.columns cc
                          ON fkc.parent_object_id = cc.object_id
                         AND fkc.parent_column_id = cc.column_id
                        WHERE (OBJECT_SCHEMA_NAME(fk.parent_object_id) = :schema
                           AND OBJECT_NAME(fk.parent_object_id) = :table)
                           OR
                              (OBJECT_SCHEMA_NAME(fk.referenced_object_id) = :schema
                           AND OBJECT_NAME(fk.referenced_object_id) = :table)
                        ORDER BY fk.name, fkc.constraint_column_id
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                out: list[dict[str, Any]] = []
                for row in result.mappings().all():
                    rel_type = (
                        RelationshipType.MANY_TO_ONE
                        if (
                            str(row["child_schema"]) == schema
                            and str(row["child_table"]) == table
                        )
                        else RelationshipType.ONE_TO_MANY
                    )
                    out.append(
                        {
                            "constraint_name": str(row["constraint_name"]),
                            "parent_schema": str(row["parent_schema"]),
                            "parent_table": str(row["parent_table"]),
                            "parent_column": str(row["parent_column"]),
                            "child_schema": str(row["child_schema"]),
                            "child_table": str(row["child_table"]),
                            "child_column": str(row["child_column"]),
                            "ordinal_position": int(row["ordinal_position"] or 1),
                            "relationship_type": str(
                                rel_type.value if hasattr(rel_type, "value") else rel_type
                            ),
                        }
                    )
                return out
        except Exception as exc:
            logger.warning(
                "Failed to retrieve SQL Server foreign keys",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            return []

    async def get_indexes(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            i.name AS index_name,
                            i.index_id,
                            CASE i.type_desc
                                WHEN 'CLUSTERED' THEN 'CLUSTERED'
                                WHEN 'NONCLUSTERED' THEN 'NONCLUSTERED'
                                WHEN 'UNIQUE CLUSTERED' THEN 'UNIQUE CLUSTERED'
                                WHEN 'UNIQUE NONCLUSTERED' THEN 'UNIQUE NONCLUSTERED'
                                ELSE i.type_desc END AS index_type,
                            CASE WHEN i.is_unique = 1 THEN 1 ELSE 0 END AS is_unique,
                            CASE WHEN i.is_primary_key = 1 THEN 1 ELSE 0 END AS is_primary,
                            STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS columns,
                            STRING_AGG(CASE ic.is_descending_key WHEN 1 THEN 'DESC' ELSE 'ASC' END, ', ')
                                WITHIN GROUP (ORDER BY ic.key_ordinal) AS orders
                        FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        JOIN sys.indexes i ON t.object_id = i.object_id
                        LEFT JOIN sys.index_columns ic
                               ON i.object_id = ic.object_id
                              AND i.index_id = ic.index_id
                              AND ic.is_included_column = 0
                        LEFT JOIN sys.columns c
                               ON ic.object_id = c.object_id
                              AND ic.column_id = c.column_id
                        WHERE s.name = :schema
                          AND t.name = :table
                          AND i.name IS NOT NULL
                        GROUP BY i.name, i.index_id, i.type_desc, i.is_unique, i.is_primary_key
                        ORDER BY i.is_primary_key DESC, i.index_id
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                out: list[dict[str, Any]] = []
                for row in result.mappings().all():
                    cols = [c.strip() for c in str(row["columns"] or "").split(",") if c.strip()]
                    orders = [x.strip() for x in str(row["orders"] or "").split(",") if x.strip()]
                    index_type_raw = str(row["index_type"] or "INDEX").upper()
                    index_type_enum = (
                        "PRIMARY" if int(row["is_primary"] or 0)
                        else ("UNIQUE" if int(row["is_unique"] or 0)
                        else ("CLUSTERED" if "CLUSTERED" in index_type_raw else "INDEX"))
                    )
                    im = IndexMetadata(
                        name=str(row["index_name"]),
                        columns=cols,
                        unique=bool(int(row["is_unique"] or 0)),
                        type=index_type_enum,
                        definition=f"{index_type_raw} ({', '.join(f'{c} {o}' for c, o in zip(cols, orders, strict=False))})" if cols else None,
                    )
                    out.append(im.model_dump(mode="python") if hasattr(im, "model_dump") else {
                        "name": im.name,
                        "columns": im.columns,
                        "unique": im.unique,
                        "type": im.type,
                        "definition": im.definition,
                    })
                return out
        except Exception as exc:
            logger.warning(
                "Failed to retrieve SQL Server indexes",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            return []

    async def get_constraints(self, schema: str, table: str) -> list[dict[str, Any]]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT
                            tc.CONSTRAINT_NAME,
                            tc.CONSTRAINT_TYPE,
                            tc.TABLE_SCHEMA,
                            tc.TABLE_NAME,
                            cc.CHECK_CLAUSE
                        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                        LEFT JOIN INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
                               ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
                              AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
                        WHERE tc.TABLE_SCHEMA = :schema
                          AND tc.TABLE_NAME = :table
                        ORDER BY tc.CONSTRAINT_TYPE, tc.CONSTRAINT_NAME
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                out: list[dict[str, Any]] = []
                for row in result.mappings().all():
                    raw_type = str(row["CONSTRAINT_TYPE"] or "").upper()
                    if raw_type == "PRIMARY KEY":
                        ctype = ConstraintType.PRIMARY_KEY
                    elif raw_type == "FOREIGN KEY":
                        ctype = ConstraintType.FOREIGN_KEY
                    elif raw_type == "UNIQUE":
                        ctype = ConstraintType.UNIQUE
                    elif raw_type == "CHECK":
                        ctype = ConstraintType.CHECK
                    elif raw_type == "DEFAULT":
                        ctype = ConstraintType.DEFAULT
                    else:
                        ctype = ConstraintType.UNIQUE
                    cm = ConstraintMetadata(
                        name=str(row["CONSTRAINT_NAME"]),
                        type=ctype,
                        columns=[],
                        expression=str(row["CHECK_CLAUSE"]) if row["CHECK_CLAUSE"] else None,
                    )
                    out.append(cm.model_dump(mode="python") if hasattr(cm, "model_dump") else {
                        "name": cm.name, "type": cm.type, "columns": [], "expression": cm.expression
                    })
                return out
        except Exception as exc:
            logger.warning(
                "Failed to retrieve SQL Server constraints",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            return []

    async def get_table_comment(self, schema: str, table: str) -> Optional[str]:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT CAST(p.value AS NVARCHAR(MAX)) AS table_comment
                        FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        LEFT JOIN sys.extended_properties p
                               ON p.major_id = t.object_id
                              AND p.minor_id = 0
                              AND p.class = 1
                              AND p.name = 'MS_Description'
                        WHERE s.name = :schema
                          AND t.name = :table
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                row = result.fetchone()
                return str(row[0]).strip() if row and row[0] else None
        except Exception:
            return None

    async def get_estimated_row_count(self, schema: str, table: str) -> int:
        try:
            engine = await self._get_engine()
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """
                        SELECT ISNULL(SUM(p.rows), 0) AS row_count
                        FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        JOIN sys.partitions p ON t.object_id = p.object_id
                         AND p.index_id IN (0, 1)
                        WHERE s.name = :schema
                          AND t.name = :table
                        GROUP BY t.object_id
                        """
                    ),
                    {"schema": schema, "table": table},
                )
                row = result.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
                return 0
        except Exception as exc:
            logger.warning(
                "Failed to estimate SQL Server row count",
                extra={"schema": schema, "table": table},
                exc_info=exc,
            )
            return 0

    async def execute_raw(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        engine = await self._get_engine()
        timeout_ms = int(timeout * 1000) if timeout else None
        async with engine.connect() as conn:
            if timeout_ms is not None:
                try:
                    await conn.execute(text(f"SET LOCK_TIMEOUT = {timeout_ms}"))
                except Exception:
                    pass
            result = await conn.execute(text(query), params or {})
            columns: list[str] = list(result.keys())
            rows = [dict(r) for r in result.mappings().all()]
            return columns, rows

    async def execute_readonly_sql(
        self,
        sql: str,
        timeout: Optional[int] = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        engine = await self._get_engine()
        timeout_ms = int(timeout * 1000) if timeout else None
        async with engine.connect() as conn:
            try:
                await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"))
            except Exception:
                pass
            if timeout_ms is not None:
                try:
                    await conn.execute(text(f"SET LOCK_TIMEOUT = {timeout_ms}"))
                except Exception:
                    pass
            try:
                result = await conn.execute(text(sql))
                columns = list(result.keys())
                rows_mapped = result.mappings().fetchmany(max_rows + 1)
                truncated = len(rows_mapped) > max_rows
                if truncated:
                    rows_mapped = rows_mapped[:max_rows]
                rows: list[dict[str, Any]] = [dict(r) for r in rows_mapped]
                return columns, rows, truncated
            finally:
                try:
                    await conn.rollback()
                except Exception:
                    pass

    async def sample_rows(
        self,
        schema: str,
        table: str,
        order_by_col: Optional[str] = None,
        direction: str = "DESC",
        limit: int = 50,
        exclude_cols: Optional[list[str]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        exclude_set = {c.lower() for c in (exclude_cols or [])}
        quoted_schema = f"[{schema}]"
        quoted_table = f"[{table}]"
        safe_direction = "DESC" if (direction or "").upper().startswith("DESC") else "ASC"
        safe_limit = max(1, min(limit, 10000))
        order_clause = ""
        if order_by_col:
            safe_col = re.sub(r"[^A-Za-z0-9_]", "", str(order_by_col))
            if safe_col:
                order_clause = f"ORDER BY [{safe_col}] {safe_direction}"
        else:
            order_clause = "ORDER BY (SELECT NULL)"
        query = (
            f"SELECT TOP {safe_limit} * FROM {quoted_schema}.{quoted_table} WITH (NOLOCK) "
            f"{order_clause} OPTION (MAXDOP 1, RECOMPILE)"
        )
        columns, rows = await self.execute_raw(query, timeout=30)
        if exclude_set:
            cols_out = [c for c in columns if c.lower() not in exclude_set]
            rows_out = [{k: r[k] for k in cols_out if k in r} for r in rows]
            return cols_out, rows_out
        return columns, rows

    async def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        approx_mode: bool = True,
    ) -> ColumnProfile:
        safe_schema = f"[{schema}]"
        safe_table = f"[{table}]"
        safe_col = f"[{re.sub(r'[^A-Za-z0-9_]', '', str(column))}]"
        if approx_mode:
            return ColumnProfile(
                non_null_count=0,
                null_count=0,
                distinct_count=0,
                avg_length=None,
                min_value=None,
                max_value=None,
                numeric_avg=None,
                numeric_sum=None,
                top_values=None,
                mode=None,
            )
        try:
            query = f"""
                SELECT
                    COUNT_BIG(*) AS total_rows,
                    COUNT_BIG({safe_col}) AS non_null_count,
                    COUNT_BIG(*) - COUNT_BIG({safe_col}) AS null_count,
                    COUNT(DISTINCT {safe_col}) AS distinct_count,
                    MIN({safe_col}) AS min_val,
                    MAX({safe_col}) AS max_val,
                    AVG(CAST({safe_col} AS FLOAT)) AS numeric_avg,
                    SUM(CAST({safe_col} AS FLOAT)) AS numeric_sum
                FROM {safe_schema}.{safe_table} WITH (NOLOCK, INDEX(0)) OPTION (MAXDOP 1, RECOMPILE);
            """
            _cols, rows = await self.execute_raw(query, timeout=30)
            if not rows:
                return ColumnProfile(
                    non_null_count=0, null_count=0, distinct_count=0, avg_length=None,
                    min_value=None, max_value=None, numeric_avg=None, numeric_sum=None,
                    top_values=None, mode=None,
                )
            r = rows[0]
            return ColumnProfile(
                non_null_count=int(r.get("non_null_count") or 0),
                null_count=int(r.get("null_count") or 0),
                distinct_count=int(r.get("distinct_count") or 0),
                avg_length=None,
                min_value=r.get("min_val"),
                max_value=r.get("max_val"),
                numeric_avg=None if r.get("numeric_avg") is None else float(r["numeric_avg"]),
                numeric_sum=None if r.get("numeric_sum") is None else float(r["numeric_sum"]),
                top_values=None,
                mode=None,
            )
        except Exception as exc:
            logger.debug(
                "SQL Server column profile fallback approx",
                extra={"schema": schema, "table": table, "column": column},
                exc_info=exc,
            )
            return ColumnProfile(
                non_null_count=0, null_count=0, distinct_count=0, avg_length=None,
                min_value=None, max_value=None, numeric_avg=None, numeric_sum=None,
                top_values=None, mode=None,
            )
