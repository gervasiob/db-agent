from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.exceptions import DatabaseDiscoveryError
from app.core.logging import get_logger
from app.models.database import ColumnMetadata, ColumnType, DatabaseMetadata, TableMetadata

logger = get_logger(__name__)


_BLOB_TYPES = {ColumnType.BYTEA, ColumnType.UNKNOWN}


class DatabaseSamplingService:
    def __init__(
        self,
        *,
        default_sample_rows: Optional[int] = None,
        max_columns: int = 30,
        text_truncate_chars: int = 200,
    ) -> None:
        self.default_sample_rows = default_sample_rows or settings.DATABASE_SAMPLE_ROWS
        self.max_columns = max_columns
        self.text_truncate_chars = text_truncate_chars

    _SORT_CANDIDATES_ORDERED = (
        "created_at",
        "created_date",
        "creation_date",
        "inserted_at",
        "updated_at",
        "modified_at",
        "modified_date",
        "updated_date",
        "last_modified",
        "timestamp",
        "date",
        "fecha_creacion",
        "fecha_actualizacion",
        "fecha",
    )

    def _select_columns(self, columns: list[ColumnMetadata]) -> list[str]:
        filtered: list[ColumnMetadata] = []
        for col in columns:
            if col.type in _BLOB_TYPES:
                continue
            if col.raw_type and "bytea" in col.raw_type.lower():
                continue
            if col.raw_type and "blob" in col.raw_type.lower():
                continue
            filtered.append(col)

        if len(filtered) <= self.max_columns:
            return [c.name for c in filtered]

        prioritized: list[ColumnMetadata] = []
        for c in filtered:
            if c.is_pk or c.heuristics.is_id:
                prioritized.append(c)
        for c in filtered:
            if c.heuristics.is_name or c.heuristics.is_description:
                if c not in prioritized:
                    prioritized.append(c)
        for c in filtered:
            if c.heuristics.is_status or c.heuristics.is_date or c.heuristics.is_timestamp:
                if c not in prioritized:
                    prioritized.append(c)
        for c in filtered:
            if c.heuristics.is_monetary:
                if c not in prioritized:
                    prioritized.append(c)
        for c in filtered:
            if c not in prioritized:
                prioritized.append(c)

        selected = prioritized[: self.max_columns]
        return [c.name for c in selected]

    def _detect_sort_column(
        self,
        columns: list[ColumnMetadata],
        selected_names: list[str],
    ) -> Optional[tuple[str, str]]:
        lower_map = {c.name.lower(): (c.name, c.type) for c in columns if c.name in selected_names}

        for candidate in self._SORT_CANDIDATES_ORDERED:
            if candidate in lower_map:
                col_name, _ = lower_map[candidate]
                return col_name, "DESC"

        for c in columns:
            if c.name not in selected_names:
                continue
            if c.heuristics and (c.heuristics.is_created_at or c.heuristics.is_updated_at):
                return c.name, "DESC"

        for c in columns:
            if c.name not in selected_names:
                continue
            if (c.name.lower().endswith("_id") or c.name.lower() == "id") and c.type in {
                ColumnType.BIGINT,
                ColumnType.INTEGER,
                ColumnType.SMALLINT,
                ColumnType.UUID,
            }:
                return c.name, "DESC"

        for c in columns:
            if c.name not in selected_names:
                continue
            if c.is_pk and c.type in {
                ColumnType.BIGINT,
                ColumnType.INTEGER,
                ColumnType.SMALLINT,
                ColumnType.UUID,
            }:
                return c.name, "DESC"

        return None

    def _truncate_value(self, value: Any, column: Optional[ColumnMetadata]) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        if column is not None and column.type not in {ColumnType.TEXT, ColumnType.VARCHAR, ColumnType.CHAR}:
            return value
        if len(value) <= self.text_truncate_chars:
            return value
        return value[: self.text_truncate_chars] + "..."

    async def sample_table(
        self,
        engine: AsyncEngine,
        table: TableMetadata,
        *,
        num_rows: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        limit = num_rows or self.default_sample_rows
        if limit <= 0:
            table.sample_rows = []
            return []

        selected = self._select_columns(table.columns)
        if not selected:
            logger.debug(
                "No sampleable columns for table",
                extra={"table": f"{table.schema_name}.{table.table_name}"},
            )
            table.sample_rows = []
            return []

        sort_info = self._detect_sort_column(table.columns, selected)
        col_by_name = {c.name: c for c in table.columns}

        quoted_cols = ", ".join(f'"{name}"' for name in selected)
        base_sql = f'SELECT {quoted_cols} FROM "{table.schema_name}"."{table.table_name}"'

        if sort_info is not None:
            sort_col, direction = sort_info
            sql = f'{base_sql} ORDER BY "{sort_col}" {direction} LIMIT :limit'
        else:
            sql = f"{base_sql} LIMIT :limit"

        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(sql), {"limit": limit})
                rows_raw = result.mappings().all()
                rows: list[dict[str, Any]] = []
                for mapping in rows_raw:
                    sanitized_row: dict[str, Any] = {}
                    for col_name, raw_val in mapping.items():
                        col_meta = col_by_name.get(col_name)
                        sanitized_row[col_name] = self._truncate_value(raw_val, col_meta)
                    rows.append(sanitized_row)
                table.sample_rows = rows
                logger.debug(
                    "Sampled rows from table",
                    extra={
                        "table": f"{table.schema_name}.{table.table_name}",
                        "rows": len(rows),
                    },
                )
                return rows
        except Exception as exc:
            logger.warning(
                "Failed to sample rows for table",
                extra={"table": f"{table.schema_name}.{table.table_name}"},
                exc_info=exc,
            )
            table.sample_rows = []
            return []

    async def sample_database(
        self,
        engine: AsyncEngine,
        metadata: DatabaseMetadata,
        *,
        num_rows: Optional[int] = None,
    ) -> DatabaseMetadata:
        for schema in metadata.schemas:
            for table in schema.tables:
                await self.sample_table(engine, table, num_rows=num_rows)
            for view in schema.views:
                await self.sample_table(engine, view, num_rows=num_rows)
        return metadata


__all__ = ["DatabaseSamplingService"]
