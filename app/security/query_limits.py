from __future__ import annotations

from typing import Any, Optional

from sqlglot import exp, parse

from app.core.exceptions import SQLValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueryLimits:
    def apply_row_limit(
        self,
        sql: str,
        dialect: str,
        max_rows: int,
    ) -> str:
        if max_rows <= 0:
            return sql
        try:
            parsed = parse(sql, read=dialect)
        except Exception as exc:
            logger.warning(
                "Could not parse SQL for row limit injection, returning original",
                extra={"error": str(exc)},
            )
            return sql

        if not parsed or len(parsed) != 1:
            return sql

        statement = parsed[0]
        if not isinstance(statement, (exp.Select, exp.With)):
            return sql

        target_select = self._find_outer_select(statement)
        if target_select is None:
            return sql

        existing_limit = target_select.args.get("limit")
        if existing_limit is not None:
            try:
                existing_value = int(str(existing_limit.expression)) if hasattr(existing_limit, "expression") else int(str(existing_limit))
                if existing_value <= max_rows:
                    return sql
            except (ValueError, TypeError):
                pass

        target_select.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return statement.sql(dialect=dialect)

    def detect_non_aggregate(self, sql: str, dialect: str) -> bool:
        try:
            parsed = parse(sql, read=dialect)
        except Exception:
            return True

        if not parsed or len(parsed) != 1:
            return True

        statement = parsed[0]
        target_select = self._find_outer_select(statement)
        if target_select is None:
            return True

        has_group_by = bool(target_select.args.get("group"))
        if has_group_by:
            return False

        has_agg = False
        for _ in target_select.find_all(exp.AggFunc):
            has_agg = True
            break

        if not has_agg:
            for _ in target_select.find_all(exp.Count):
                has_agg = True
                break

        if not has_agg:
            for node in target_select.walk():
                class_name = type(node).__name__.lower()
                if any(agg in class_name for agg in ("sum", "count", "avg", "min", "max", "median", "stddev", "variance")):
                    if isinstance(node, exp.Func) or "agg" in class_name:
                        has_agg = True
                        break

        return not has_agg

    def statement_timeout_sql(self, timeout_seconds: int) -> str:
        timeout_ms = max(1, int(timeout_seconds * 1000))
        return f"SET statement_timeout = {timeout_ms}"

    def readonly_transaction_options(self) -> dict[str, Any]:
        return {
            "postgresql_readonly": True,
            "postgresql_deferrable": True,
            "isolation_level": "REPEATABLE READ",
        }

    def allowed_schemas_default(self) -> list[str]:
        return ["public"]

    def _find_outer_select(self, statement: exp.Expression) -> Optional[exp.Select]:
        if isinstance(statement, exp.Select):
            return statement
        if isinstance(statement, exp.With):
            final = statement.this
            if isinstance(final, exp.Select):
                return final
        if isinstance(statement, (exp.Union, exp.Intersect, exp.Except)):
            return statement if isinstance(statement, exp.Select) else None
        return None


__all__ = ["QueryLimits"]
