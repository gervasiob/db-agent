from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.exceptions import SemanticAnalysisError
from app.core.logging import get_logger
from app.llm.client import async_generate_structured
from app.llm.prompts.system_prompts import QUERY_INTENT_SYSTEM_PROMPT
from app.llm.schemas import LLMIntent
from app.models.context import DatabaseKnowledgeMap, RetrievalContext
from app.models.query import (
    FilterOperator,
    IntentType,
    QueryIntent,
    RelativeTimeRange,
    SemanticFilter,
    TimeRange,
)

logger = get_logger(__name__)

_RELATIVE_TIME_KEYWORDS: dict[str, RelativeTimeRange] = {
    "today": RelativeTimeRange.TODAY,
    "hoy": RelativeTimeRange.TODAY,
    "yesterday": RelativeTimeRange.YESTERDAY,
    "ayer": RelativeTimeRange.YESTERDAY,
    "this week": RelativeTimeRange.THIS_WEEK,
    "esta semana": RelativeTimeRange.THIS_WEEK,
    "last week": RelativeTimeRange.LAST_WEEK,
    "la semana pasada": RelativeTimeRange.LAST_WEEK,
    "semana pasada": RelativeTimeRange.LAST_WEEK,
    "this month": RelativeTimeRange.THIS_MONTH,
    "este mes": RelativeTimeRange.THIS_MONTH,
    "last month": RelativeTimeRange.LAST_MONTH,
    "el mes pasado": RelativeTimeRange.LAST_MONTH,
    "mes pasado": RelativeTimeRange.LAST_MONTH,
    "this quarter": RelativeTimeRange.THIS_QUARTER,
    "este trimestre": RelativeTimeRange.THIS_QUARTER,
    "this year": RelativeTimeRange.THIS_YEAR,
    "este año": RelativeTimeRange.THIS_YEAR,
    "este anio": RelativeTimeRange.THIS_YEAR,
    "last year": RelativeTimeRange.LAST_YEAR,
    "el año pasado": RelativeTimeRange.LAST_YEAR,
    "el anio pasado": RelativeTimeRange.LAST_YEAR,
    "año pasado": RelativeTimeRange.LAST_YEAR,
    "anio pasado": RelativeTimeRange.LAST_YEAR,
    "last 7 days": RelativeTimeRange.LAST_7_DAYS,
    "ultimos 7 dias": RelativeTimeRange.LAST_7_DAYS,
    "últimos 7 días": RelativeTimeRange.LAST_7_DAYS,
    "ultimos 7 dias": RelativeTimeRange.LAST_7_DAYS,
    "last 30 days": RelativeTimeRange.LAST_30_DAYS,
    "ultimos 30 dias": RelativeTimeRange.LAST_30_DAYS,
    "últimos 30 días": RelativeTimeRange.LAST_30_DAYS,
    "ultimos 30 dias": RelativeTimeRange.LAST_30_DAYS,
    "last 90 days": RelativeTimeRange.LAST_90_DAYS,
    "ultimos 90 dias": RelativeTimeRange.LAST_90_DAYS,
    "últimos 90 días": RelativeTimeRange.LAST_90_DAYS,
    "month to date": RelativeTimeRange.CURRENT_MONTH_TO_DATE,
    "mtd": RelativeTimeRange.CURRENT_MONTH_TO_DATE,
    "mes a la fecha": RelativeTimeRange.CURRENT_MONTH_TO_DATE,
    "year to date": RelativeTimeRange.CURRENT_YEAR_TO_DATE,
    "ytd": RelativeTimeRange.CURRENT_YEAR_TO_DATE,
    "año a la fecha": RelativeTimeRange.CURRENT_YEAR_TO_DATE,
    "anio a la fecha": RelativeTimeRange.CURRENT_YEAR_TO_DATE,
}

_AGGREGATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "cuantos",
        "cuántos",
        "cuantas",
        "cuántas",
        "how many",
        "how much",
        "total",
        "totales",
        "sum",
        "suma",
        "sumar",
        "promedio",
        "average",
        "avg",
        "count",
        "contar",
        "conteo",
        "max",
        "maximo",
        "máximo",
        "min",
        "minimo",
        "mínimo",
        "cantidad",
    }
)

_EXPLANATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "explica",
        "explicame",
        "explícame",
        "explain",
        "que es",
        "qué es",
        "que significa",
        "qué significa",
        "como funciona",
        "cómo funciona",
        "describe",
        "describeme",
        "descríbeme",
    }
)

_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "como funciona el sistema",
        "cómo funciona el sistema",
        "how does the system work",
        "modificar",
        "eliminar",
        "borrar",
        "actualizar",
        "insertar",
        "crear tabla",
        "drop table",
        "update",
        "delete",
        "insert",
    }
)

_CHITCHAT_KEYWORDS: frozenset[str] = frozenset(
    {
        "hola",
        "hello",
        "hi",
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        "gracias",
        "thank you",
        "thanks",
        "chau",
        "adios",
        "adiós",
        "bye",
        "goodbye",
        "que tal",
        "qué tal",
        "how are you",
        "como estas",
        "cómo estás",
    }
)


class QueryIntentService:
    def __init__(self) -> None:
        self._relative_time_keywords = _RELATIVE_TIME_KEYWORDS
        self._aggregation_keywords = _AGGREGATION_KEYWORDS
        self._explanation_keywords = _EXPLANATION_KEYWORDS
        self._unsupported_keywords = _UNSUPPORTED_KEYWORDS
        self._chitchat_keywords = _CHITCHAT_KEYWORDS

    async def detect_intent(
        self,
        question: str,
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> QueryIntent:
        normalized_question = question.strip()
        if not normalized_question:
            raise SemanticAnalysisError(
                detail="Question cannot be empty",
                extra={"question_length": 0},
            )

        try:
            llm_intent = await self._llm_detect_intent(
                question=normalized_question,
                knowledge_map=knowledge_map,
                retrieval_context=retrieval_context,
            )
            intent = self._map_llm_intent(llm_intent, normalized_question)
            if intent.time_range is None:
                intent.time_range = self._rule_based_time_range(normalized_question)
            return intent
        except Exception as exc:
            logger.warning(
                "LLM intent detection failed, falling back to rule-based",
                extra={"error": str(exc)},
                exc_info=exc,
            )
            return self._rule_based_intent(normalized_question)

    async def _llm_detect_intent(
        self,
        question: str,
        knowledge_map: DatabaseKnowledgeMap,
        retrieval_context: RetrievalContext,
    ) -> LLMIntent:
        km_summary = self._build_knowledge_map_summary(knowledge_map)
        rc_summary = self._build_retrieval_context_summary(retrieval_context)
        system_text = QUERY_INTENT_SYSTEM_PROMPT.messages[0].content
        human_parts = [
            f"## User Question\n{question}",
            f"## Knowledge Map Summary\n{km_summary}",
            f"## Retrieval Context Matches\n{rc_summary}",
        ]
        messages: list[BaseMessage] = [
            SystemMessage(content=str(system_text)),
            HumanMessage(content="\n\n".join(human_parts)),
        ]
        return await async_generate_structured(messages, LLMIntent, retries=2)

    def _build_knowledge_map_summary(self, km: DatabaseKnowledgeMap) -> str:
        lines: list[str] = []
        lines.append(f"Domains ({len(km.domains)}): " + ", ".join(d.code for d in km.domains[:10]))
        lines.append(f"Entities ({len(km.entities)}): " + ", ".join(e.code for e in km.entities[:20]))
        lines.append(f"Metrics ({len(km.metrics)}): " + ", ".join(m.code for m in km.metrics[:20]))
        lines.append(f"Concepts ({len(km.concepts)}): " + ", ".join(c.code for c in km.concepts[:20]))
        lines.append(f"Dimensions ({len(km.dimensions)}): " + ", ".join(d.code for d in km.dimensions[:20]))
        return "\n".join(lines)

    def _build_retrieval_context_summary(self, rc: RetrievalContext) -> str:
        if not rc.matches:
            return "No retrieval matches found."
        lines = []
        for m in rc.matches[:15]:
            lines.append(f"- [{m.match_type.value}] {m.code or m.name}: {m.description}")
        return "\n".join(lines) if lines else "No retrieval matches."

    def _map_llm_intent(self, llm: LLMIntent, question: str) -> QueryIntent:
        try:
            intent_type = IntentType(llm.intent_type.upper())
        except ValueError:
            intent_type = self._rule_based_intent_type(question)
        time_range = self._map_llm_time_range(llm.time_range, question)
        filters = self._map_llm_filters(llm.filters)
        return QueryIntent(
            intent_type=intent_type,
            entities=list(llm.entities),
            metrics=list(llm.metrics),
            concepts=list(llm.concepts),
            dimensions=list(llm.dimensions),
            time_range=time_range,
            filters=filters,
            requires_clarification=bool(llm.requires_clarification),
            clarification_questions=list(llm.clarification_questions),
            raw_confidence=max(0.0, min(1.0, float(llm.confidence))),
        )

    def _map_llm_time_range(self, tr_dict: Any, question: str) -> Optional[TimeRange]:
        if tr_dict is None:
            return self._rule_based_time_range(question)
        if hasattr(tr_dict, "model_dump"):
            try:
                tr_dict = tr_dict.model_dump(mode="json")
            except Exception:
                tr_dict = {}
        if not isinstance(tr_dict, dict) or not tr_dict:
            return self._rule_based_time_range(question)
        relative_str = tr_dict.get("relative")
        start_str = tr_dict.get("start")
        end_str = tr_dict.get("end")
        relative: Optional[RelativeTimeRange] = None
        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None
        if relative_str:
            try:
                relative = RelativeTimeRange(str(relative_str).upper())
            except ValueError:
                relative = None
        if start_str:
            try:
                start_dt = datetime.fromisoformat(str(start_str).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                start_dt = None
        if end_str:
            try:
                end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                end_dt = None
        if relative is None and start_dt is None and end_dt is None:
            return self._rule_based_time_range(question)
        return TimeRange(start=start_dt, end=end_dt, relative=relative)

    def _map_llm_filters(self, filters_list: Any) -> list[SemanticFilter]:
        result: list[SemanticFilter] = []
        if not filters_list:
            return result
        normalized = []
        for item in filters_list:
            if hasattr(item, "model_dump"):
                try:
                    normalized.append(item.model_dump(mode="json"))
                except Exception:
                    continue
            elif isinstance(item, dict):
                normalized.append(item)
        for f in normalized:
            try:
                field = str(f.get("field", "")).strip()
                if not field:
                    continue
                op_str = str(f.get("operator", "EQ")).upper()
                try:
                    operator = FilterOperator(op_str)
                except ValueError:
                    operator = FilterOperator.EQ
                values = f.get("values", [])
                if not isinstance(values, list):
                    values = [values]
                is_negated = bool(f.get("is_negated", False))
                result.append(
                    SemanticFilter(
                        field=field,
                        operator=operator,
                        values=list(values),
                        is_negated=is_negated,
                    )
                )
            except Exception:
                continue
        return result

    def _rule_based_intent(self, question: str) -> QueryIntent:
        intent_type = self._rule_based_intent_type(question)
        time_range = self._rule_based_time_range(question)
        return QueryIntent(
            intent_type=intent_type,
            entities=[],
            metrics=[],
            concepts=[],
            dimensions=[],
            time_range=time_range,
            filters=[],
            requires_clarification=False,
            clarification_questions=[],
            raw_confidence=0.5,
        )

    def _rule_based_intent_type(self, question: str) -> IntentType:
        q_lower = question.lower().strip()
        has_qmark = "?" in question or "¿" in question

        for kw in self._chitchat_keywords:
            if kw in q_lower and len(q_lower.split()) <= 6:
                return IntentType.CHITCHAT

        for kw in self._unsupported_keywords:
            if kw in q_lower:
                return IntentType.UNSUPPORTED

        if any(kw in q_lower for kw in self._explanation_keywords):
            return IntentType.EXPLANATION

        if has_qmark or any(kw in q_lower for kw in self._aggregation_keywords):
            return IntentType.DATABASE_QUERY

        if any(kw in q_lower for kw in self._aggregation_keywords):
            return IntentType.DATABASE_QUERY

        if len(q_lower.split()) >= 3:
            return IntentType.DATABASE_QUERY

        return IntentType.CLARIFICATION

    def _rule_based_time_range(self, question: str) -> Optional[TimeRange]:
        q_lower = question.lower()
        longest_match: Optional[tuple[int, RelativeTimeRange]] = None
        for keyword, tr_value in self._relative_time_keywords.items():
            if keyword in q_lower:
                length = len(keyword)
                if longest_match is None or length > longest_match[0]:
                    longest_match = (length, tr_value)
        if longest_match is not None:
            return TimeRange(relative=longest_match[1])

        explicit = self._try_parse_explicit_range(q_lower)
        if explicit is not None:
            return explicit

        last_n_match = re.search(r"(?:last|últimos|ultimos)\s+(\d+)\s+(?:days|dias|días|semanas|weeks|meses|months|años|years|anios)", q_lower)
        if last_n_match:
            try:
                n = int(last_n_match.group(1))
                unit = last_n_match.group(0)
                if any(word in unit for word in ("day", "dias", "días")):
                    start = datetime.utcnow() - timedelta(days=n)
                    return TimeRange(start=start, end=datetime.utcnow())
            except (ValueError, TypeError):
                pass

        return None

    def _try_parse_explicit_range(self, q_lower: str) -> Optional[TimeRange]:
        iso_patterns = [
            re.compile(r"(\d{4}-\d{2}-\d{2})\s+(?:a|to|hasta|-)\s+(\d{4}-\d{2}-\d{2})"),
            re.compile(r"desde\s+(\d{4}-\d{2}-\d{2})\s+(?:hasta|a)\s+(\d{4}-\d{2}-\d{2})"),
            re.compile(r"from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})"),
        ]
        for pattern in iso_patterns:
            m = pattern.search(q_lower)
            if m:
                try:
                    start = datetime.strptime(m.group(1), "%Y-%m-%d")
                    end = datetime.strptime(m.group(2), "%Y-%m-%d")
                    return TimeRange(start=start, end=end)
                except ValueError:
                    continue
        return None


__all__ = ["QueryIntentService"]
