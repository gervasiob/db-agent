from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.config import settings
from app.core.exceptions import LLMGenerationError
from app.core.logging import get_logger
from app.llm.client import async_generate_structured
from app.llm.schemas import LLMSQLPlan
from app.models.context import DatabaseKnowledgeMap, RetrievalContext
from app.models.database import ColumnMetadata, TableMetadata
from app.models.query import SQLQueryPlan, SemanticQueryPlan

logger = get_logger(__name__)

_SQL_GENERATION_SYSTEM_PROMPT = """You are a senior SQL query planner for PostgreSQL. Generate READ-ONLY SQL exclusively. Only SELECT statements and WITH/CTE allowed. NEVER INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, REVOKE, MERGE, CALL, EXEC, COPY, VACUUM, SET, DO.
Never modify data or schema. Never invent tables or columns. Use only supplied schema.
Use explicit JOIN syntax. Prefer explicit column lists over SELECT *. Add LIMIT to unbounded result sets.
Never include multiple statements. Do not include trailing semicolons or comments with DDL hints.
Reference the business concepts, metrics, entities, and date semantics provided to map the user's intent into the correct physical tables and columns."""


class SQLGenerationService:
    def __init__(self) -> None:
        self._system_prompt = _SQL_GENERATION_SYSTEM_PROMPT
        self._max_rows_hint = settings.MAX_QUERY_ROWS
        self._include_sample_data = settings.LLM_INCLUDE_SAMPLE_DATA
        self._sanitize_sample_data = settings.LLM_SANITIZE_SAMPLE_DATA

    async def generate_sql(
        self,
        semantic_plan: SemanticQueryPlan,
        retrieval_context: RetrievalContext,
        knowledge_map: DatabaseKnowledgeMap,
        dialect: str = "postgresql",
        previous_sql: Optional[str] = None,
        previous_error: Optional[str] = None,
        original_question: Optional[str] = None,
    ) -> SQLQueryPlan:
        messages = self._build_prompt_messages(
            semantic_plan=semantic_plan,
            retrieval_context=retrieval_context,
            knowledge_map=knowledge_map,
            dialect=dialect,
        )
        if previous_sql or previous_error:
            messages.append({
                "role": "user",
                "content": (
                    "Antecedent:\n"
                    f"- Previous SQL: {previous_sql or '(none)'}\n"
                    f"- Previous error: {previous_error or '(none)'}\n"
                    f"- Original question: {original_question or '(none)'}\n"
                    "Please generate a corrected, equivalent PostgreSQL query that fixes the error while keeping the same business semantics (same grain, columns, filters, aggregations). Output only the corrected plan."
                )
            })
        try:
            llm_plan = await async_generate_structured(messages, LLMSQLPlan, retries=2)
        except LLMGenerationError:
            raise
        except Exception as exc:
            raise LLMGenerationError(
                detail=f"SQL structured generation failed: {exc!s}",
                extra={"dialect": dialect},
            ) from exc
        sql = self._clean_sql(llm_plan.sql)
        non_aggregate = self._is_non_aggregate_hint(semantic_plan, llm_plan)
        if non_aggregate and not self._sql_has_limit(sql):
            sql = f"{sql.rstrip()} LIMIT {self._max_rows_hint}"
        tables_list = list(llm_plan.tables) if llm_plan.tables else list(semantic_plan.tables_hint)
        limit_value: Optional[int] = None
        try:
            parsed_limit = int(llm_plan.limit) if llm_plan.limit else None
            if parsed_limit and parsed_limit > 0:
                limit_value = parsed_limit
        except (ValueError, TypeError):
            limit_value = None
        return SQLQueryPlan(
            sql=sql,
            dialect=dialect,  # type: ignore[arg-type]
            tables=tables_list,
            joins=[j.model_dump(mode="json") for j in (llm_plan.joins or [])],
            ctes=[c.model_dump(mode="json") for c in (llm_plan.ctes or [])],
            where=list(llm_plan.where),
            group_by=list(llm_plan.group_by),
            having=list(llm_plan.having),
            order_by=[o.model_dump(mode="json") for o in (llm_plan.order_by or [])],
            limit=limit_value,
            parameters=[],
        )

    def _build_prompt_messages(
        self,
        semantic_plan: SemanticQueryPlan,
        retrieval_context: RetrievalContext,
        knowledge_map: DatabaseKnowledgeMap,
        dialect: str,
    ) -> list[BaseMessage]:
        semantic_dump = self._serialize_semantic_plan(semantic_plan)
        relevant_tables_text = self._serialize_relevant_tables(retrieval_context.relevant_tables)
        relevant_relationships_text = self._serialize_relevant_relationships(
            retrieval_context.relevant_relationships
        )
        relevant_concepts_text = self._serialize_relevant_concepts(retrieval_context.relevant_concepts)
        relevant_metrics_text = self._serialize_relevant_metrics(retrieval_context.relevant_metrics)
        date_semantics_text = self._serialize_date_semantics(
            knowledge_map,
            [t.table_name for t in retrieval_context.relevant_tables],
        )
        sample_data_text = ""
        if self._include_sample_data:
            sample_data_text = self._serialize_sample_rows(
                retrieval_context.relevant_tables,
                sanitize=self._sanitize_sample_data,
            )
        human_sections = [
            f"## SQL Dialect\n{dialect}",
            f"## Semantic Query Plan\n```json\n{semantic_dump}\n```",
            f"## Relevant Tables (name, columns, types, PK/FK)\n{relevant_tables_text}",
            f"## Relevant Relationships\n{relevant_relationships_text}",
            f"## Relevant Concepts (with condition_sql)\n{relevant_concepts_text}",
            f"## Relevant Metrics (with aggregation)\n{relevant_metrics_text}",
            f"## Date Semantics per Table\n{date_semantics_text}",
        ]
        if sample_data_text:
            human_sections.append(f"## Sanitized Sample Rows\n{sample_data_text}")
        human_sections.append(
            f"## LIMIT Hint\nFor non-aggregate queries (no GROUP BY, no COUNT/SUM/AVG/MIN/MAX), apply LIMIT {self._max_rows_hint} unless the semantic plan specifies a smaller limit."
        )
        return [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content="\n\n".join(human_sections)),
        ]

    def _serialize_semantic_plan(self, plan: SemanticQueryPlan) -> str:
        data = plan.model_dump(mode="json")
        return json.dumps(data, indent=2, default=str, ensure_ascii=False)

    def _serialize_relevant_tables(self, tables: list[TableMetadata]) -> str:
        if not tables:
            return "(no relevant tables available)"
        lines = []
        for t in tables:
            qualified = f"{t.schema_name}.{t.table_name}"
            col_lines: list[str] = []
            for c in t.columns:
                flags: list[str] = []
                if c.is_pk:
                    flags.append("PK")
                if c.is_fk:
                    if c.fk_target_table and c.fk_target_column:
                        flags.append(f"FK->{c.fk_target_table}.{c.fk_target_column}")
                    else:
                        flags.append("FK")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                col_lines.append(f"  - {c.name}: {c.type.value}{flag_str}")
            body = "\n".join(col_lines) if col_lines else "  (no columns)"
            lines.append(f"### {qualified} ({t.table_type.value})\n{body}")
        return "\n\n".join(lines)

    def _serialize_relevant_relationships(self, relationships: list[Any]) -> str:
        if not relationships:
            return "(no relationships provided)"
        lines = []
        for rel in relationships:
            source = f"{rel.source_schema}.{rel.source_table}({', '.join(rel.source_columns)})"
            target = f"{rel.target_schema}.{rel.target_table}({', '.join(rel.target_columns)})"
            lines.append(f"- {rel.relationship_type.value}: {source} -> {target}")
        return "\n".join(lines) if lines else "(no relationships)"

    def _serialize_relevant_concepts(self, concepts: list[Any]) -> str:
        if not concepts:
            return "(no relevant concepts)"
        lines = []
        for c in concepts:
            aliases = ", ".join(c.aliases) if c.aliases else "-"
            cond = c.condition_sql or "(no SQL fragment)"
            lines.append(f"- {c.code} ({c.name}): {c.description} | aliases: {aliases} | condition_sql: {cond}")
        return "\n".join(lines) if lines else "(no concepts)"

    def _serialize_relevant_metrics(self, metrics: list[Any]) -> str:
        if not metrics:
            return "(no relevant metrics)"
        lines = []
        for m in metrics:
            agg = m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation)
            src = m.source_column or "(no source column)"
            tables = ", ".join(m.source_tables) if m.source_tables else "(no tables)"
            filt = m.filter_condition or "(no filter)"
            lines.append(
                f"- {m.code} ({m.name}): aggregation={agg}, column={src}, tables=[{tables}], filter={filt}"
            )
        return "\n".join(lines) if lines else "(no metrics)"

    def _serialize_date_semantics(self, knowledge_map: DatabaseKnowledgeMap, table_names: list[str]) -> str:
        tbl_set_lower = {n.lower() for n in table_names}
        lines = []
        for ds in knowledge_map.date_semantics:
            if ds.table_name.lower() not in tbl_set_lower and table_names:
                continue
            role = ds.semantic_role.value if hasattr(ds.semantic_role, "value") else str(ds.semantic_role)
            aliases = ", ".join(ds.aliases) if ds.aliases else "-"
            lines.append(f"- {ds.table_name}.{ds.column_name}: {role} (aliases: {aliases})")
        if not lines:
            return "(no date semantics annotated)"
        return "\n".join(lines)

    def _serialize_sample_rows(
        self,
        tables: list[TableMetadata],
        *,
        sanitize: bool,
    ) -> str:
        sections = []
        for t in tables:
            rows = t.sample_rows or []
            if not rows:
                continue
            display_rows = rows[: settings.DATABASE_SAMPLE_ROWS] if hasattr(settings, "DATABASE_SAMPLE_ROWS") else rows[:5]
            if sanitize:
                display_rows = [self._sanitize_row(r, t.columns) for r in display_rows]
            sections.append(
                f"### {t.schema_name}.{t.table_name} ({len(display_rows)} rows)\n```json\n{json.dumps(display_rows, indent=2, default=str, ensure_ascii=False)}\n```"
            )
        if not sections:
            return ""
        return "\n\n".join(sections)

    def _sanitize_row(self, row: dict[str, Any], columns: list[ColumnMetadata]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        pii_columns: set[str] = set()
        for c in columns:
            col_lower = c.name.lower()
            heuristics = getattr(c, "heuristics", None)
            is_email = bool(getattr(heuristics, "is_email", False))
            is_phone = bool(getattr(heuristics, "is_phone", False))
            is_address = bool(getattr(heuristics, "is_address", False))
            if is_email or is_phone or is_address:
                pii_columns.add(c.name)
            if any(word in col_lower for word in ("email", "phone", "tel", "address", "ssn", "passport", "credit_card", "document")):
                pii_columns.add(c.name)
        for k, v in row.items():
            if k in pii_columns and isinstance(v, str):
                if "@" in v:
                    parts = v.split("@")
                    sanitized[k] = f"***@{parts[-1]}" if len(parts) == 2 else "***"
                else:
                    sanitized[k] = "*" * min(8, len(v))
            else:
                sanitized[k] = v
        return sanitized

    def _clean_sql(self, sql: str) -> str:
        s = sql.strip()
        if s.startswith("```"):
            s = s[3:]
            if s.startswith("sql"):
                s = s[3:]
            if s.endswith("```"):
                s = s[:-3]
            s = s.strip()
        s = s.rstrip(";").strip()
        return s

    def _is_non_aggregate_hint(self, semantic_plan: SemanticQueryPlan, llm_plan: LLMSQLPlan) -> bool:
        has_group_by = bool(llm_plan.group_by)
        if has_group_by:
            return False
        metric_aggs: set[str] = set()
        for mr in semantic_plan.metric_refs:
            metric_aggs.add(mr.upper())
        if any("COUNT" in m or "SUM" in m or "AVG" in m or "MIN" in m or "MAX" in m for m in metric_aggs):
            return False
        return True

    def _sql_has_limit(self, sql: str) -> bool:
        upper = sql.upper()
        return "LIMIT " in upper or "FETCH FIRST" in upper or "TOP " in upper


__all__ = ["SQLGenerationService"]
