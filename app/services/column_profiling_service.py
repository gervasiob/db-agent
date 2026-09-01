from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import DatabaseDiscoveryError
from app.core.logging import get_logger
from app.models.database import (
    ColumnMetadata,
    ColumnProfile,
    ColumnType,
    DatabaseMetadata,
    TableMetadata,
)

logger = get_logger(__name__)


_SORTABLE_TYPES = {
    ColumnType.INTEGER,
    ColumnType.BIGINT,
    ColumnType.SMALLINT,
    ColumnType.NUMERIC,
    ColumnType.FLOAT,
    ColumnType.DATE,
    ColumnType.TIMESTAMP,
    ColumnType.TIMESTAMPTZ,
    ColumnType.TIME,
    ColumnType.VARCHAR,
    ColumnType.CHAR,
    ColumnType.TEXT,
    ColumnType.UUID,
}

_LARGE_TABLE_THRESHOLD = 100_000
_SAMPLE_PERCENTAGE = 5
_APPROX_DISTINCT_LIMIT = 50_000


class ColumnProfilingService:
    def __init__(
        self,
        *,
        sample_distinct_limit: int = 20,
        large_table_threshold: int = _LARGE_TABLE_THRESHOLD,
        sample_percentage: int = _SAMPLE_PERCENTAGE,
    ) -> None:
        self.sample_distinct_limit = sample_distinct_limit
        self.large_table_threshold = large_table_threshold
        self.sample_percentage = max(1, min(sample_percentage, 50))

    def _is_sortable(self, col: ColumnMetadata) -> bool:
        return col.type in _SORTABLE_TYPES

    async def _profile_from_pg_stats(
        self,
        conn,
        schema: str,
        table: str,
        column: str,
    ) -> Optional[ColumnProfile]:
        try:
            result = await conn.execute(
                text(
                    """
                    SELECT
                        s.n_distinct,
                        s.null_frac,
                        s.most_common_vals,
                        s.min_val,
                        s.max_val
                    FROM pg_stats s
                    WHERE s.schemaname = :schema
                      AND s.tablename = :table
                      AND s.attname = :column
                    """
                ),
                {"schema": schema, "table": table, "column": column},
            )
            row = result.fetchone()
            if row is None:
                return None

            n_distinct_raw = row[0]
            null_frac = row[1]
            mcv = row[2]
            min_val = row[3]
            max_val = row[4]

            profile = ColumnProfile()

            if isinstance(n_distinct_raw, (int, float)):
                if n_distinct_raw < 0:
                    reltuples_result = await conn.execute(
                        text(
                            """
                            SELECT reltuples::bigint
                            FROM pg_class c
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema AND c.relname = :table
                            """
                        ),
                        {"schema": schema, "table": table},
                    )
                    rt_row = reltuples_result.fetchone()
                    if rt_row and rt_row[0] is not None:
                        reltuples = max(int(rt_row[0]), 0)
                        profile.distinct_count_approx = max(int(abs(n_distinct_raw) * reltuples), 0)
                else:
                    profile.distinct_count_approx = max(int(n_distinct_raw), 0)

            if isinstance(null_frac, (int, float)):
                profile.null_percentage = round(max(0.0, min(100.0, float(null_frac) * 100.0)), 4)

            if min_val is not None:
                profile.min_value = min_val
            if max_val is not None:
                profile.max_value = max_val

            if mcv is not None:
                distinct_vals: list[Any] = []
                try:
                    if isinstance(mcv, list):
                        items = list(mcv)
                    else:
                        items = [str(v) for v in str(mcv).split(",") if v]
                    for v in items:
                        if v is None:
                            continue
                        distinct_vals.append(v)
                        if len(distinct_vals) >= self.sample_distinct_limit:
                            break
                except Exception:
                    distinct_vals = []
                if distinct_vals:
                    profile.sample_distinct_values = distinct_vals

            return profile
        except Exception as exc:
            logger.debug(
                "pg_stats profile lookup failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )
            return None

    async def _profile_with_tablesample(
        self,
        conn,
        schema: str,
        table: str,
        column: str,
        is_sortable: bool,
    ) -> ColumnProfile:
        profile = ColumnProfile()
        sample_pct = self.sample_percentage
        sample_expr = f'TABLESAMPLE BERNOULLI({sample_pct})'

        try:
            sample_q = f'''
                SELECT
                    COUNT(*) AS sample_total,
                    COUNT("{column}") AS sample_non_null,
                    {'MIN("{column}") AS min_val,' if is_sortable else ''}
                    {'MAX("{column}") AS max_val' if is_sortable else ''}
                FROM "{schema}"."{table}" {sample_expr}
            '''.rstrip().rstrip(",")
            result = await conn.execute(text(sample_q))
            row = result.fetchone()
            if row is not None:
                sample_total = int(row[0] or 0)
                sample_non_null = int(row[1] or 0)
                if sample_total > 0:
                    null_ratio = 1.0 - (sample_non_null / sample_total)
                    profile.null_percentage = round(null_ratio * 100.0, 4)
                if is_sortable:
                    profile.min_value = row[2]
                    profile.max_value = row[3]
        except Exception as exc:
            logger.debug(
                "TABLESAMPLE base scan failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )

        try:
            distinct_q = f'''
                SELECT DISTINCT "{column}"
                FROM "{schema}"."{table}" {sample_expr}
                WHERE "{column}" IS NOT NULL
                LIMIT :limit
            '''
            result = await conn.execute(text(distinct_q), {"limit": self.sample_distinct_limit})
            vals = [r[0] for r in result.fetchall()]
            if vals:
                profile.sample_distinct_values = vals

            approx_q = f'''
                SELECT COUNT(*) AS approx_count
                FROM (
                    SELECT DISTINCT "{column}"
                    FROM "{schema}"."{table}" {sample_expr}
                    WHERE "{column}" IS NOT NULL
                    LIMIT :limit
                ) sub
            '''
            result = await conn.execute(text(approx_q), {"limit": _APPROX_DISTINCT_LIMIT})
            d_row = result.fetchone()
            if d_row and d_row[0] is not None:
                distinct_in_sample = int(d_row[0])
                scale_factor = 100.0 / sample_pct
                profile.distinct_count_approx = max(int(distinct_in_sample * scale_factor), 0)
        except Exception as exc:
            logger.debug(
                "TABLESAMPLE distinct scan failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )

        return profile

    async def _profile_full_small_table(
        self,
        conn,
        schema: str,
        table: str,
        column: str,
        is_sortable: bool,
    ) -> ColumnProfile:
        profile = ColumnProfile()
        try:
            nulls_sql = f'''
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN "{column}" IS NULL THEN 1 ELSE 0 END) AS nulls
                    {f', MIN("{column}") AS min_val' if is_sortable else ''}
                    {f', MAX("{column}") AS max_val' if is_sortable else ''}
                FROM "{schema}"."{table}"
            '''
            result = await conn.execute(text(nulls_sql))
            row = result.fetchone()
            if row is not None:
                total = int(row[0] or 0)
                nulls = int(row[1] or 0)
                if total > 0:
                    profile.null_percentage = round(nulls * 100.0 / total, 4)
                if is_sortable:
                    profile.min_value = row[2]
                    profile.max_value = row[3]
        except Exception as exc:
            logger.debug(
                "Small table null/min/max scan failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )

        try:
            distinct_q = f'''
                SELECT COUNT(DISTINCT "{column}")
                FROM "{schema}"."{table}"
            '''
            result = await conn.execute(text(distinct_q))
            d_row = result.fetchone()
            if d_row and d_row[0] is not None:
                profile.distinct_count_approx = int(d_row[0])
        except Exception as exc:
            logger.debug(
                "Small table COUNT DISTINCT failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )

        try:
            values_q = f'''
                SELECT DISTINCT "{column}"
                FROM "{schema}"."{table}"
                WHERE "{column}" IS NOT NULL
                LIMIT :limit
            '''
            result = await conn.execute(text(values_q), {"limit": self.sample_distinct_limit})
            vals = [r[0] for r in result.fetchall()]
            if vals:
                profile.sample_distinct_values = vals
        except Exception as exc:
            logger.debug(
                "Small table sample values scan failed",
                extra={"column": f"{schema}.{table}.{column}"},
                exc_info=exc,
            )

        return profile

    async def profile_column(
        self,
        engine: AsyncEngine,
        table: TableMetadata,
        column: ColumnMetadata,
    ) -> ColumnProfile:
        is_sortable = self._is_sortable(column)
        large_table = table.row_count_estimate is not None and table.row_count_estimate >= self.large_table_threshold

        try:
            async with engine.connect() as conn:
                from_stats = await self._profile_from_pg_stats(
                    conn,
                    table.schema_name,
                    table.table_name,
                    column.name,
                )

                if from_stats is not None:
                    has_all = (
                        from_stats.distinct_count_approx is not None
                        and from_stats.null_percentage is not None
                        and from_stats.sample_distinct_values is not None
                    )
                    if is_sortable and (from_stats.min_value is None or from_stats.max_value is None):
                        has_all = False
                    if has_all:
                        return from_stats

                    fallback_profile: ColumnProfile
                    if large_table:
                        fallback_profile = await self._profile_with_tablesample(
                            conn,
                            table.schema_name,
                            table.table_name,
                            column.name,
                            is_sortable,
                        )
                    else:
                        fallback_profile = await self._profile_full_small_table(
                            conn,
                            table.schema_name,
                            table.table_name,
                            column.name,
                            is_sortable,
                        )

                    merged = from_stats.model_dump()
                    for k, v in fallback_profile.model_dump().items():
                        if v is not None and merged.get(k) is None:
                            merged[k] = v
                    return ColumnProfile(**merged)

                if large_table:
                    return await self._profile_with_tablesample(
                        conn,
                        table.schema_name,
                        table.table_name,
                        column.name,
                        is_sortable,
                    )
                return await self._profile_full_small_table(
                    conn,
                    table.schema_name,
                    table.table_name,
                    column.name,
                    is_sortable,
                )
        except Exception as exc:
            logger.warning(
                "Column profiling failed",
                extra={"column": f"{table.schema_name}.{table.table_name}.{column.name}"},
                exc_info=exc,
            )
            return ColumnProfile()

    async def profile_table(
        self,
        engine: AsyncEngine,
        table: TableMetadata,
    ) -> dict[str, ColumnProfile]:
        profiles: dict[str, ColumnProfile] = {}
        for column in table.columns:
            try:
                profiles[column.name] = await self.profile_column(engine, table, column)
            except Exception as exc:
                logger.warning(
                    "Column profiling skipped due to error",
                    extra={"column": f"{table.schema_name}.{table.table_name}.{column.name}"},
                    exc_info=exc,
                )
                profiles[column.name] = ColumnProfile()
        table.column_profiles = profiles
        return profiles

    async def profile_database(
        self,
        engine: AsyncEngine,
        metadata: DatabaseMetadata,
    ) -> DatabaseMetadata:
        for schema in metadata.schemas:
            for table in schema.tables:
                await self.profile_table(engine, table)
            for view in schema.views:
                await self.profile_table(engine, view)
        return metadata


__all__ = ["ColumnProfilingService"]
