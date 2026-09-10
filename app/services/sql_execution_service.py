from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.exceptions import SQLExecutionError
from app.core.logging import get_logger
from app.database.connection import (
    DatabaseConfig,
    build_connect_args,
    build_connection_string,
    close_engine,
    create_engine_from_config,
)
from app.models.query import QueryExecutionResult, ValidatedSQLQuery
from app.security.query_limits import QueryLimits

logger = get_logger(__name__)


class SQLExecutionService:
    def __init__(self, query_limits: Optional[QueryLimits] = None) -> None:
        self._query_limits = query_limits or QueryLimits()

    async def execute(
        self,
        connection_config: DatabaseConfig,
        validated: ValidatedSQLQuery,
        timeout_seconds: int = 15,
        max_rows: int = 500,
    ) -> QueryExecutionResult:
        if not isinstance(validated, ValidatedSQLQuery):
            raise SQLExecutionError(
                detail="Only ValidatedSQLQuery instances are accepted. Raw SQL is forbidden.",
                extra={"received_type": type(validated).__name__},
            )
        if not validated.is_readonly:
            raise SQLExecutionError(
                detail="ValidatedSQLQuery must be marked read-only before execution.",
            )

        dialect_map = {
            "postgresql": "postgres",
            "sqlserver": "tsql",
            "mysql": "mysql",
        }
        sqlglot_dialect = dialect_map.get(validated.dialect, "postgres")
        is_non_aggregate = self._query_limits.detect_non_aggregate(validated.validated_sql, sqlglot_dialect)
        sql_to_run = validated.validated_sql
        applied_limit: Optional[int] = None
        if is_non_aggregate and not self._sql_has_limit(sql_to_run):
            limit_to_apply = max_rows + 1
            sql_to_run = self._query_limits.apply_row_limit(sql_to_run, sqlglot_dialect, limit_to_apply)
            applied_limit = limit_to_apply
            validated.max_rows_applied = True

        engine: Optional[AsyncEngine] = None
        start_time = time.perf_counter()
        try:
            engine = await self._create_temporary_engine(connection_config)
            session_factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )
            async with session_factory() as session:
                await self._set_session_statement_timeout(session, timeout_seconds, connection_config)
                columns, rows = await self._execute_query(session, sql_to_run, max_rows, applied_limit)
                row_count = len(rows)
                truncated = False
                if applied_limit is not None and row_count > max_rows:
                    rows = rows[:max_rows]
                    row_count = max_rows
                    truncated = True
                execution_time_ms = int((time.perf_counter() - start_time) * 1000)
                return QueryExecutionResult(
                    columns=columns,
                    rows=rows,
                    row_count=row_count,
                    execution_time_ms=execution_time_ms,
                    truncated=truncated,
                    applied_limit=applied_limit,
                    timeout_seconds=timeout_seconds,
                )
        except SQLAlchemyError as exc:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error(
                "SQL execution failed due to database error",
                extra={
                    "elapsed_ms": elapsed_ms,
                    "dialect": validated.dialect,
                    "database_type": connection_config.database_type,
                    "host": connection_config.host,
                },
                exc_info=exc,
            )
            raise SQLExecutionError(
                detail=f"SQL execution failed: {exc!s}",
                extra={
                    "elapsed_ms": elapsed_ms,
                    "database_type": connection_config.database_type,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        except SQLExecutionError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error(
                "Unexpected error during SQL execution",
                extra={"elapsed_ms": elapsed_ms},
                exc_info=exc,
            )
            raise SQLExecutionError(
                detail=f"Unexpected error during SQL execution: {exc!s}",
                extra={
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        finally:
            if engine is not None:
                await close_engine(engine)

    async def _create_temporary_engine(self, config: DatabaseConfig) -> AsyncEngine:
        return await create_engine_from_config(
            config,
            pool_size=2,
            max_overflow=5,
            pool_recycle=1800,
            pool_pre_ping=True,
            echo=False,
            future=True,
        )

    async def _set_session_statement_timeout(
        self,
        session: AsyncSession,
        timeout_seconds: int,
        config: DatabaseConfig,
    ) -> None:
        timeout_seconds = max(1, int(timeout_seconds or settings.QUERY_TIMEOUT_SECONDS))
        if config.database_type == "postgresql":
            timeout_ms = timeout_seconds * 1000
            try:
                await session.execute(text(f"SET statement_timeout = {timeout_ms}"))
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.commit()
            except Exception as exc:
                logger.warning(
                    "Could not set statement_timeout or read-only transaction mode",
                    extra={"error": str(exc)},
                )
        elif config.database_type == "sqlserver":
            timeout_ms = int(timeout_seconds * 1000)
            try:
                await session.execute(text(f"SET LOCK_TIMEOUT = {timeout_ms}"))
            except Exception as exc:
                logger.warning("Could not set SQL Server LOCK_TIMEOUT", extra={"error": str(exc)})
            try:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"))
            except Exception:
                try:
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                except Exception:
                    pass

    async def _execute_query(
        self,
        session: AsyncSession,
        sql: str,
        max_rows: int,
        applied_limit: Optional[int],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            cursor_result = await session.execute(text(sql))
        except Exception as exc:
            raise SQLExecutionError(
                detail=f"Query execution returned error: {exc!s}",
                extra={"sql_length": len(sql)},
            ) from exc
        try:
            rows_raw = cursor_result.fetchall()
        except Exception:
            rows_raw = []
        columns: list[str] = []
        try:
            keys = list(cursor_result.keys())
            columns = [str(k) for k in keys]
        except Exception:
            columns = []
        rows: list[dict[str, Any]] = []
        for row in rows_raw:
            try:
                if columns:
                    row_dict: dict[str, Any] = {}
                    for idx, col in enumerate(columns):
                        try:
                            row_dict[col] = row[idx]
                        except IndexError:
                            row_dict[col] = None
                    rows.append(row_dict)
                else:
                    mapping = row._mapping if hasattr(row, "_mapping") else row
                    rows.append({str(k): v for k, v in dict(mapping).items()})
            except Exception as row_exc:
                logger.warning(
                    "Could not map row to dict, skipping",
                    extra={"error": str(row_exc)},
                )
                continue
        return columns, rows

    def _sql_has_limit(self, sql: str) -> bool:
        upper = sql.upper()
        return "LIMIT " in upper or "FETCH FIRST" in upper or "TOP " in upper


__all__ = ["SQLExecutionService"]
