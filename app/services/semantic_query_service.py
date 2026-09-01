from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger
from app.models.context import DatabaseKnowledgeMap, RetrievalContext
from app.models.query import (
    QueryIntent,
    RelativeTimeRange,
    SemanticFilter,
    SemanticQueryPlan,
    TimeRange,
)
from app.models.semantic import (
    DateSemantic,
    DateSemanticRole,
)

logger = get_logger(__name__)

_DATE_ROLE_PRIORITY: list[DateSemanticRole] = [
    DateSemanticRole.HIRE_DATE,
    DateSemanticRole.ORDER_DATE,
    DateSemanticRole.INVOICE_DATE,
    DateSemanticRole.PAYMENT_DATE,
    DateSemanticRole.EVENT_DATE,
    DateSemanticRole.CREATED_AT,
    DateSemanticRole.UPDATED_AT,
    DateSemanticRole.OTHER,
    DateSemanticRole.DELETED_AT,
    DateSemanticRole.TERMINATION_DATE,
    DateSemanticRole.BIRTH_DATE,
]


class SemanticQueryService:
    def __init__(self) -> None:
        self._date_role_priority = _DATE_ROLE_PRIORITY

    def build_semantic_plan(
        self,
        question: str,
        intent: QueryIntent,
        retrieval_context: RetrievalContext,
        knowledge_map: DatabaseKnowledgeMap,
    ) -> SemanticQueryPlan:
        entity_refs = self._map_entity_refs(intent.entities, knowledge_map, retrieval_context)
        metric_refs = self._map_metric_refs(intent.metrics, knowledge_map, retrieval_context)
        concept_refs = self._map_concept_refs(intent.concepts, knowledge_map, retrieval_context)
        dimension_refs = self._map_dimension_refs(intent.dimensions, knowledge_map, retrieval_context)
        filter_refs = self._build_filter_refs(intent.filters)
        time_range, time_filters = self._apply_time_range_to_filters(
            intent.time_range,
            knowledge_map,
            retrieval_context,
            entity_refs,
        )
        if time_filters:
            for tf in time_filters:
                if not any(f.field == tf.field and f.operator == tf.operator for f in filter_refs):
                    filter_refs.append(tf)
        tables_hint = self._collect_tables_hint(
            retrieval_context=retrieval_context,
            entity_refs=entity_refs,
            metric_refs=metric_refs,
            concept_refs=concept_refs,
            knowledge_map=knowledge_map,
        )
        semantic_description = self._build_semantic_description(
            question=question,
            intent=intent,
            entity_refs=entity_refs,
            metric_refs=metric_refs,
            concept_refs=concept_refs,
            dimension_refs=dimension_refs,
            time_range=time_range,
            filter_count=len(filter_refs),
        )
        return SemanticQueryPlan(
            intent=intent,
            entity_refs=entity_refs,
            metric_refs=metric_refs,
            concept_refs=concept_refs,
            dimension_refs=dimension_refs,
            filter_refs=filter_refs,
            time_range=time_range,
            order_by=None,
            limit=None,
            semantic_description=semantic_description,
            tables_hint=tables_hint,
        )

    def _map_entity_refs(
        self,
        intent_entities: list[str],
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        retrieval_entities = {e.code for e in retrieval_context.relevant_entities if e.code}
        km_entities = {e.code for e in knowledge_map.entities if e.code}
        for raw in intent_entities:
            code = self._resolve_code(raw, retrieval_entities, km_entities, knowledge_map, "ENTITY")
            if code and code not in seen:
                seen.add(code)
                refs.append(code)
        for e in retrieval_context.relevant_entities:
            if e.code and e.code not in seen:
                seen.add(e.code)
                refs.append(e.code)
        return refs

    def _map_metric_refs(
        self,
        intent_metrics: list[str],
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        retrieval_metrics = {m.code for m in retrieval_context.relevant_metrics if m.code}
        km_metrics = {m.code for m in knowledge_map.metrics if m.code}
        for raw in intent_metrics:
            code = self._resolve_code(raw, retrieval_metrics, km_metrics, knowledge_map, "METRIC")
            if code and code not in seen:
                seen.add(code)
                refs.append(code)
        for m in retrieval_context.relevant_metrics:
            if m.code and m.code not in seen:
                seen.add(m.code)
                refs.append(m.code)
        return refs

    def _map_concept_refs(
        self,
        intent_concepts: list[str],
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        retrieval_concepts = {c.code for c in retrieval_context.relevant_concepts if c.code}
        km_concepts = {c.code for c in knowledge_map.concepts if c.code}
        for raw in intent_concepts:
            code = self._resolve_code(raw, retrieval_concepts, km_concepts, knowledge_map, "CONCEPT")
            if code and code not in seen:
                seen.add(code)
                refs.append(code)
        for c in retrieval_context.relevant_concepts:
            if c.code and c.code not in seen:
                seen.add(c.code)
                refs.append(c.code)
        return refs

    def _map_dimension_refs(
        self,
        intent_dimensions: list[str],
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        retrieval_dims = {d.code for d in retrieval_context.relevant_dimensions if d.code}
        km_dims = {d.code for d in knowledge_map.dimensions if d.code}
        for raw in intent_dimensions:
            code = self._resolve_code(raw, retrieval_dims, km_dims, knowledge_map, "DIMENSION")
            if code and code not in seen:
                seen.add(code)
                refs.append(code)
        for d in retrieval_context.relevant_dimensions:
            if d.code and d.code not in seen:
                seen.add(d.code)
                refs.append(d.code)
        return refs

    def _resolve_code(
        self,
        raw: str,
        retrieval_codes: set[str],
        km_codes: set[str],
        knowledge_map: DatabaseKnowledgeMap,
        target_type: str,
    ) -> Optional[str]:
        raw_upper = raw.strip().upper()
        raw_normalized = raw.strip()
        if raw_upper in retrieval_codes:
            return raw_upper
        if raw_upper in km_codes:
            return raw_upper
        for t in knowledge_map.terminology:
            if not t.target_code:
                continue
            t_type = getattr(t, "type", None)
            if t_type and str(t_type).upper() != target_type:
                continue
            if t.term and raw_normalized.lower() == t.term.lower():
                return t.target_code
            for alias in t.aliases:
                if raw_normalized.lower() == alias.lower():
                    return t.target_code
        if raw_upper in km_codes:
            return raw_upper
        return None

    def _build_filter_refs(self, intent_filters: list[SemanticFilter]) -> list[SemanticFilter]:
        return [SemanticFilter(**f.model_dump()) for f in intent_filters]

    def _apply_time_range_to_filters(
        self,
        time_range: Optional[TimeRange],
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
        entity_refs: list[str],
    ) -> tuple[Optional[TimeRange], list[SemanticFilter]]:
        if time_range is None:
            return None, []
        relevant_tables = [t.table_name for t in retrieval_context.relevant_tables]
        date_semantics = self._find_relevant_date_semantics(
            knowledge_map,
            relevant_tables,
            entity_refs,
        )
        if not date_semantics:
            return time_range, []
        filters: list[SemanticFilter] = []
        for ds in date_semantics[:2]:
            field_identifier = f"{ds.table_name}.{ds.column_name}"
            if time_range.relative is not None:
                filters.append(
                    SemanticFilter(
                        field=field_identifier,
                        operator=self._relative_operator(time_range.relative),
                        values=[time_range.relative.value],
                        is_negated=False,
                    )
                )
            else:
                if time_range.start is not None and time_range.end is not None:
                    filters.append(
                        SemanticFilter(
                            field=field_identifier,
                            operator="BETWEEN",  # type: ignore[arg-type]
                            values=[
                                time_range.start.isoformat(),
                                time_range.end.isoformat(),
                            ],
                            is_negated=False,
                        )
                    )
                elif time_range.start is not None:
                    filters.append(
                        SemanticFilter(
                            field=field_identifier,
                            operator="GTE",  # type: ignore[arg-type]
                            values=[time_range.start.isoformat()],
                            is_negated=False,
                        )
                    )
                elif time_range.end is not None:
                    filters.append(
                        SemanticFilter(
                            field=field_identifier,
                            operator="LTE",  # type: ignore[arg-type]
                            values=[time_range.end.isoformat()],
                            is_negated=False,
                        )
                    )
        return time_range, filters

    def _find_relevant_date_semantics(
        self,
        knowledge_map: DatabaseKnowledgeMap,
        relevant_tables: list[str],
        entity_refs: list[str],
    ) -> list[DateSemantic]:
        candidates: list[DateSemantic] = []
        relevant_tables_lower = {t.lower() for t in relevant_tables}
        entity_tables: set[str] = set()
        for e in knowledge_map.entities:
            if e.code in entity_refs:
                for t in e.tables:
                    entity_tables.add(t.lower())
                if e.primary_table:
                    entity_tables.add(e.primary_table.lower())
        for ds in knowledge_map.date_semantics:
            tbl_lower = ds.table_name.lower()
            if tbl_lower in relevant_tables_lower or tbl_lower in entity_tables:
                candidates.append(ds)
        if not candidates:
            for ds in knowledge_map.date_semantics:
                if ds.semantic_role in {
                    DateSemanticRole.ORDER_DATE,
                    DateSemanticRole.HIRE_DATE,
                    DateSemanticRole.INVOICE_DATE,
                    DateSemanticRole.EVENT_DATE,
                    DateSemanticRole.CREATED_AT,
                }:
                    candidates.append(ds)
        def _sort_key(ds: DateSemantic) -> int:
            try:
                return self._date_role_priority.index(ds.semantic_role)
            except ValueError:
                return len(self._date_role_priority) + 1
        candidates.sort(key=_sort_key)
        return candidates

    def _relative_operator(self, relative: RelativeTimeRange) -> Any:
        from app.models.query import FilterOperator
        return FilterOperator.IN

    def _collect_tables_hint(
        self,
        retrieval_context: RetrievalContext,
        entity_refs: list[str],
        metric_refs: list[str],
        concept_refs: list[str],
        knowledge_map: DatabaseKnowledgeMap,
    ) -> list[str]:
        tables: list[str] = []
        seen: set[str] = set()
        for t in retrieval_context.relevant_tables:
            qn = f"{t.schema_name}.{t.table_name}"
            if qn not in seen:
                seen.add(qn)
                tables.append(qn)
        for e in knowledge_map.entities:
            if e.code in entity_refs:
                for t in e.tables:
                    if t not in seen:
                        seen.add(t)
                        tables.append(t)
                if e.primary_table and e.primary_table not in seen:
                    seen.add(e.primary_table)
                    tables.append(e.primary_table)
        for m in knowledge_map.metrics:
            if m.code in metric_refs:
                for t in m.source_tables:
                    if t not in seen:
                        seen.add(t)
                        tables.append(t)
        for c in knowledge_map.concepts:
            if c.code in concept_refs:
                for t in c.tables:
                    if t not in seen:
                        seen.add(t)
                        tables.append(t)
        return tables

    def _build_semantic_description(
        self,
        question: str,
        intent: QueryIntent,
        entity_refs: list[str],
        metric_refs: list[str],
        concept_refs: list[str],
        dimension_refs: list[str],
        time_range: Optional[TimeRange],
        filter_count: int,
    ) -> str:
        parts: list[str] = []
        parts.append(f"Query intent: {intent.intent_type.value}")
        if entity_refs:
            parts.append(f"Entities: {', '.join(entity_refs[:5])}")
        if metric_refs:
            parts.append(f"Metrics: {', '.join(metric_refs[:5])}")
        if concept_refs:
            parts.append(f"Concepts: {', '.join(concept_refs[:5])}")
        if dimension_refs:
            parts.append(f"Dimensions: {', '.join(dimension_refs[:5])}")
        if time_range is not None:
            if time_range.relative:
                parts.append(f"Time range: {time_range.relative.value}")
            elif time_range.start or time_range.end:
                parts.append(f"Time range: {time_range.start or '...'} to {time_range.end or '...'}")
        if filter_count:
            parts.append(f"Filters applied: {filter_count}")
        parts.append(f"Original question: {question}")
        return " | ".join(parts)


__all__ = ["SemanticQueryService"]
