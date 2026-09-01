from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.config import settings
from app.core.exceptions import LLMGenerationError
from app.core.logging import get_logger
from app.core.logging import get_request_id
from app.llm.client import async_generate
from app.models.context import DatabaseKnowledgeMap
from app.models.query import (
    QueryExecutionResult,
    QueryExplainResponse,
    QueryResponse,
    SemanticQueryPlan,
    QueryIntent,
    IntentType,
)

logger = get_logger(__name__)

_ANSWER_SYSTEM_PROMPT = """You are an analytics assistant. Answer in the same language as the question using ONLY the provided query results. Do NOT cite external knowledge. If the result is empty, state that no data matches. If data is truncated, indicate that. Include specific numbers. Never invent values.
If query was COUNT/SUM aggregation, state the number. If it was a listing, show up to 10 items and note the total. Use plain paragraphs and bullet points when appropriate.
Keep short_answer under 30 words. Long answer under 400 words."""

DEFAULT_DISCLAIMERS: list[str] = [
    "Data based on current DB snapshot",
    "Results read-only",
    "Max rows applied where applicable.",
]


class QueryAnswerService:
    def __init__(
        self,
        disclaimers: Optional[list[str]] = None,
        temperature: float = 0.1,
    ) -> None:
        self._system_prompt = _ANSWER_SYSTEM_PROMPT
        self._disclaimers = list(disclaimers) if disclaimers is not None else list(DEFAULT_DISCLAIMERS)
        self._temperature = temperature

    async def answer_question(
        self,
        question: str,
        semantic_plan: SemanticQueryPlan,
        sql: str,
        execution_result: QueryExecutionResult,
        knowledge_map: Optional[DatabaseKnowledgeMap] = None,
    ) -> tuple[str, str]:
        _ = sql
        _ = knowledge_map
        if execution_result.row_count == 0 and not execution_result.rows:
            empty_long = (
                f"No se encontraron datos que coincidan con la consulta: '{question}'. "
                "Verifica los filtros aplicados o intenta con otros criterios."
            )
            empty_short = "Sin datos para la consulta."
            return empty_long, empty_short

        user_prompt = self._build_user_prompt(
            question=question,
            semantic_plan=semantic_plan,
            execution_result=execution_result,
        )
        messages: list[BaseMessage] = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=user_prompt),
        ]
        try:
            response = await async_generate(
                messages,
                temperature=self._temperature,
            )
        except LLMGenerationError:
            return self._fallback_answer(question, execution_result, semantic_plan)
        except Exception as exc:
            logger.warning(
                "LLM answer generation failed, using fallback",
                extra={"error": str(exc)},
                exc_info=exc,
            )
            return self._fallback_answer(question, execution_result, semantic_plan)

        response_text = self._extract_text_from_response(response)
        long_answer, short_answer = self._split_answers(response_text, question, execution_result)
        if self._disclaimers and self._disclaimers[0] not in long_answer:
            disclaimer_text = " ".join(f"- {d}." for d in self._disclaimers)
            if len(long_answer) + len(disclaimer_text) < 500:
                long_answer = long_answer.rstrip() + "\n\nDisclaimers: " + disclaimer_text
        return long_answer, short_answer

    def _build_user_prompt(
        self,
        question: str,
        semantic_plan: SemanticQueryPlan,
        execution_result: QueryExecutionResult,
    ) -> str:
        preview_rows = execution_result.rows[:15]
        total_count = max(execution_result.row_count, len(execution_result.rows))
        truncation_flag = bool(execution_result.truncated)
        columns_display = ", ".join(execution_result.columns) if execution_result.columns else "(no columns)"
        truncation_note = ""
        if truncation_flag:
            truncation_note = (
                f"\n\nTRUNCATION WARNING: Results were limited to {len(preview_rows)} rows out of {total_count} total. "
                "Mention this limitation in your answer."
            )
        sample_json = json.dumps(
            preview_rows,
            indent=2,
            default=str,
            ensure_ascii=False,
        )
        parts = [
            f"## User Question\n{question}",
            f"## Semantic Plan Summary\n{semantic_plan.semantic_description}",
            f"## Columns Returned\n[{columns_display}]",
            f"## Query Results ({len(preview_rows)} rows shown, {total_count} total)\n```json\n{sample_json}\n```",
            f"## Row Count\n{total_count}",
            f"## Execution Time\n{execution_result.execution_time_ms} ms",
        ]
        if truncation_note:
            parts.append(truncation_note)
        parts.append(
            "## Output Format\n"
            "First line: LONG ANSWER (full explanation, up to 400 words, plain text, same language as question).\n"
            "Second line or final sentence: SHORT ANSWER (concise summary, under 30 words, same language).\n"
            "Label these sections explicitly with 'LONG:' and 'SHORT:' markers."
        )
        return "\n\n".join(parts)

    def _extract_text_from_response(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        content = getattr(response, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    txt = item.get("text") or item.get("content")
                    if txt:
                        parts.append(txt)
            return "\n".join(parts)
        return str(response)

    def _split_answers(
        self,
        raw_text: str,
        question: str,
        execution_result: QueryExecutionResult,
    ) -> tuple[str, str]:
        text = raw_text.strip()
        long_answer: str = ""
        short_answer: str = ""
        long_match = re.search(r"LONG\s*[:\-]\s*(.+?)(?=\n\s*SHORT\s*[:\-]|$)", text, re.IGNORECASE | re.DOTALL)
        short_match = re.search(r"SHORT\s*[:\-]\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if long_match:
            long_answer = long_match.group(1).strip()
        if short_match:
            short_answer = short_match.group(1).strip().splitlines()[0]
        if not long_answer:
            if short_match:
                long_answer = text
            else:
                long_answer = text
        if not short_answer:
            fallback_long, fallback_short = self._fallback_answer(question, execution_result, None)
            short_answer = fallback_short
        short_answer = self._truncate_short(short_answer, 30)
        if len(long_answer) > 2800:
            long_answer = long_answer[:2797] + "..."
        return long_answer, short_answer

    def _truncate_short(self, text: str, max_words: int) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text.rstrip(".") + "." if text and not text.endswith(".") else text
        return " ".join(words[:max_words]) + "..."

    def _fallback_answer(
        self,
        question: str,
        execution_result: QueryExecutionResult,
        semantic_plan: Optional[SemanticQueryPlan],
    ) -> tuple[str, str]:
        total_count = max(execution_result.row_count, len(execution_result.rows))
        columns = execution_result.columns or []
        if total_count == 0:
            long_answer = (
                f"No se encontraron resultados para la consulta '{question}' en la base de datos. "
                "Es posible que los filtros aplicados no coincidan con ningún registro."
            )
            short_answer = "Sin resultados para su consulta."
            return long_answer, short_answer
        first_row = execution_result.rows[0] if execution_result.rows else {}
        numeric_value = self._extract_first_numeric(first_row)
        preview_items: list[str] = []
        for row in execution_result.rows[:10]:
            first_col = columns[0] if columns else (next(iter(row.keys())) if row else "")
            if first_col and first_col in row:
                preview_items.append(f"- {row[first_col]}")
        long_parts = [
            f"Resultados para: '{question}'.",
            f"Se recuperaron {total_count} fila(s) en {execution_result.execution_time_ms} ms.",
        ]
        if columns:
            long_parts.append(f"Columnas: {', '.join(columns)}.")
        if numeric_value is not None:
            long_parts.append(f"Valor numérico principal: {numeric_value}.")
        if preview_items:
            long_parts.append("Primeros elementos:")
            long_parts.extend(preview_items[:10])
        if execution_result.truncated:
            long_parts.append("Nota: resultados truncados por límite de filas.")
        if semantic_plan and semantic_plan.semantic_description:
            long_parts.append(f"Contexto semántico: {semantic_plan.semantic_description[:200]}.")
        long_parts.append("Disclaimers: " + " ".join(f"- {d}." for d in self._disclaimers))
        long_answer = "\n".join(long_parts)
        if numeric_value is not None:
            short_answer = f"{total_count} registros, valor: {numeric_value}."
        else:
            short_answer = f"{total_count} resultado(s) recuperado(s)."
        return long_answer, self._truncate_short(short_answer, 30)

    def _extract_first_numeric(self, row: dict[str, Any]) -> Optional[Any]:
        for v in row.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            if isinstance(v, str):
                try:
                    if "." in v or "," in v:
                        f = float(v.replace(",", "."))
                        return f
                    i = int(v)
                    return i
                except (ValueError, TypeError):
                    continue
        return None

    async def answer(
        self,
        *,
        question: str,
        connection_id: Any,
        request_id: str,
        session_factory: Any = None,
    ) -> QueryResponse:
        start = time.perf_counter()
        intent = QueryIntent(
            intent_type=IntentType.DATABASE_QUERY,
            entities=[],
            metrics=[],
            concepts=[],
            dimensions=[],
            time_range=None,
            filters=[],
            requires_clarification=False,
            clarification_questions=[],
            raw_confidence=0.5,
        )
        semantic_plan = SemanticQueryPlan(
            intent=intent,
            semantic_description=f"Answering question: {question}",
        )
        execution = QueryExecutionResult(
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=0,
            truncated=False,
        )
        answer_long, answer_short = self._fallback_answer(question, execution, semantic_plan)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return QueryResponse(
            question=question,
            answer=answer_long,
            short_answer=answer_short,
            semantic_plan=semantic_plan,
            sql=None,
            columns=[],
            data=[],
            row_count=0,
            execution_time_ms=elapsed_ms,
            retries=[],
            context_version=None,
            request_id=request_id or get_request_id() or uuid.uuid4().hex,
            disclaimers=list(self._disclaimers),
        )

    async def explain(
        self,
        *,
        question: str,
        connection_id: Any,
        session_factory: Any = None,
    ) -> QueryExplainResponse:
        intent_summary = (
            f"La pregunta '{question}' busca datos relacionados. "
            "Se interpretará como una consulta analítica de solo lectura."
        )
        semantic_plan_summary = (
            "El plan semántico identifica entidades relevantes, construye un mapa "
            "conceptual, genera SQL solo-lectura, valida contra políticas de seguridad "
            "y ejecuta con límites de filas y tiempo."
        )
        return QueryExplainResponse(
            question=question,
            intent_summary=intent_summary,
            semantic_plan_summary=semantic_plan_summary,
            relevant_concepts=[],
            relevant_tables=[],
            sql_summary=(
                "Se generará una consulta SQL SELECT compatible con el dialecto "
                "de la base de datos, incluyendo solo las tablas autorizadas y "
                "aplicando los filtros semánticos correspondientes."
            ),
            execution_notes=[
                "Tiempo máximo de ejecución configurable por QUERY_TIMEOUT_SECONDS.",
                f"Límite máximo de filas: {settings.MAX_QUERY_ROWS}.",
                "Validación SQL estricta: solo sentencias SELECT de solo lectura.",
                "Usuario de BD recomendado: RO de solo lectura para minimizar riesgos.",
            ],
            disclaimer=(
                "Este es un servicio de analítica de solo lectura. La información "
                "se entrega según los datos actuales de la base de datos y está sujeta "
                "a políticas de privacidad y PII."
            ),
        )


__all__ = ["QueryAnswerService"]
