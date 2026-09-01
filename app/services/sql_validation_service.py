from __future__ import annotations

from typing import Optional

from app.core.exceptions import SQLValidationError
from app.core.logging import get_logger
from app.models.context import DatabaseKnowledgeMap, RetrievalContext
from app.models.database import TableMetadata
from app.models.query import SQLQueryPlan, ValidatedSQLQuery
from app.security.sql_validator import SqlValidator

logger = get_logger(__name__)


class SQLValidationService:
    def __init__(self, sql_validator: Optional[SqlValidator] = None) -> None:
        self._sql_validator = sql_validator or SqlValidator()

    def validate(
        self,
        query_plan: SQLQueryPlan,
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> ValidatedSQLQuery:
        dialect_map = {
            "postgresql": "postgres",
            "sqlserver": "tsql",
            "mysql": "mysql",
        }
        sqlglot_dialect = dialect_map.get(query_plan.dialect, "postgres")
        allowed_tables = [
            f"{t.schema_name}.{t.table_name}" for t in retrieval_context.relevant_tables
        ] + [t.table_name for t in retrieval_context.relevant_tables]
        allowed_schemas = self._collect_unique_schemas(retrieval_context.relevant_tables)
        if not allowed_schemas:
            allowed_schemas = ["public"]
        if not allowed_tables:
            allowed_tables = [
                f"{t.schema_name}.{t.table_name}" for t in knowledge_map.tables
            ] + [t.table_name for t in knowledge_map.tables]
        try:
            validated = self._sql_validator.validate_read_only(
                sql=query_plan.sql,
                dialect=sqlglot_dialect,
                allowed_schemas=allowed_schemas,
                allowed_tables=allowed_tables,
            )
        except SQLValidationError:
            raise
        except Exception as exc:
            raise SQLValidationError(
                reason=f"Unexpected SQL validation error: {exc!s}",
                raw_sql=query_plan.sql,
                extra={"dialect": query_plan.dialect},
            ) from exc
        tables_metadata = retrieval_context.relevant_tables or knowledge_map.tables
        if tables_metadata:
            try:
                self._sql_validator.validate_columns_exist(
                    validated=validated,
                    tables_metadata=tables_metadata,
                )
            except SQLValidationError:
                raise
            except Exception as exc:
                logger.warning(
                    "Column existence check failed unexpectedly, proceeding with caution",
                    extra={"error": str(exc)},
                    exc_info=exc,
                )
        validated.dialect = query_plan.dialect  # type: ignore[assignment]
        return validated

    def _collect_unique_schemas(self, tables: list[TableMetadata]) -> list[str]:
        schemas: list[str] = []
        seen: set[str] = set()
        for t in tables:
            schema_name = t.schema_name or "public"
            if schema_name not in seen:
                seen.add(schema_name)
                schemas.append(schema_name)
        return schemas


__all__ = ["SQLValidationService"]
