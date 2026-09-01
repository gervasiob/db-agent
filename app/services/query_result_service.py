from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.models.query import QueryExecutionResult

logger = get_logger(__name__)


class QueryResultService:
    def __init__(
        self,
        max_rows: int | None = None,
        max_string_len: int = 400,
        max_tokens: int = 6000,
    ) -> None:
        self._default_max_rows = max_rows if max_rows is not None else settings.MAX_QUERY_ROWS
        self._max_string_len = max_string_len
        self._max_tokens = max_tokens

    def process_results(
        self,
        result: QueryExecutionResult,
        max_rows: int | None = None,
        max_string_len: int | None = None,
        max_tokens: int | None = None,
    ) -> QueryExecutionResult:
        effective_max_rows = max_rows if max_rows is not None else self._default_max_rows
        effective_max_string_len = max_string_len if max_string_len is not None else self._max_string_len
        effective_max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        rows = list(result.rows)
        if len(rows) > effective_max_rows:
            rows = rows[:effective_max_rows]
            result.truncated = True
        truncated_rows: list[dict[str, Any]] = []
        for row in rows:
            new_row: dict[str, Any] = {}
            for k, v in row.items():
                new_row[k] = self._truncate_value(v, effective_max_string_len)
            truncated_rows.append(new_row)
        budgeted_rows = self._apply_token_budget(truncated_rows, effective_max_tokens, result.columns)
        row_count = len(budgeted_rows)
        if row_count < len(truncated_rows):
            result.truncated = True
        return QueryExecutionResult(
            columns=list(result.columns),
            rows=budgeted_rows,
            row_count=row_count,
            execution_time_ms=result.execution_time_ms,
            truncated=bool(result.truncated) or (row_count < len(result.rows)),
            applied_limit=result.applied_limit,
            timeout_seconds=result.timeout_seconds,
        )

    def prepare_results_for_llm(
        self,
        result: QueryExecutionResult,
        max_display_rows: int = 20,
        max_string_len: int = 200,
    ) -> dict[str, Any]:
        processed = self.process_results(
            result,
            max_rows=max_display_rows,
            max_string_len=max_string_len,
        )
        columns_summary = [
            {"name": c, "index": idx} for idx, c in enumerate(processed.columns)
        ]
        preview_rows = processed.rows[:max_display_rows]
        total_row_count = max(result.row_count, len(result.rows))
        truncated_note = ""
        if processed.truncated:
            truncated_note = (
                f"Results truncated: showing {len(preview_rows)} of {total_row_count} total rows "
                f"due to max_rows/max_tokens limits."
            )
        elif total_row_count > len(preview_rows):
            truncated_note = (
                f"Showing first {len(preview_rows)} of {total_row_count} total rows."
            )
        return {
            "columns": columns_summary,
            "rows": preview_rows,
            "row_count": total_row_count,
            "preview_row_count": len(preview_rows),
            "execution_time_ms": processed.execution_time_ms,
            "truncated": bool(processed.truncated),
            "truncation_note": truncated_note,
        }

    def _truncate_value(self, value: Any, max_string_len: int) -> Any:
        if isinstance(value, str):
            if len(value) > max_string_len:
                return value[: max_string_len - 3] + "..."
            return value
        if isinstance(value, list):
            return [self._truncate_value(v, max_string_len) for v in value[:50]]
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for i, (k, v) in enumerate(value.items()):
                if i >= 50:
                    break
                out[str(k)] = self._truncate_value(v, max_string_len)
            return out
        return value

    def _apply_token_budget(
        self,
        rows: list[dict[str, Any]],
        max_tokens: int,
        columns: list[str],
    ) -> list[dict[str, Any]]:
        if max_tokens <= 0:
            return rows
        budget_chars = max(500, max_tokens * 4)
        header_cost = sum(len(c) for c in columns) + len(columns) * 4
        running_total = header_cost
        result_rows: list[dict[str, Any]] = []
        for row in rows:
            row_cost = 0
            try:
                row_str = json.dumps(row, default=str, ensure_ascii=False)
                row_cost = len(row_str) + 8
            except Exception:
                row_cost = 500
            if running_total + row_cost > budget_chars and result_rows:
                break
            running_total += row_cost
            result_rows.append(row)
        return result_rows


__all__ = ["QueryResultService"]
