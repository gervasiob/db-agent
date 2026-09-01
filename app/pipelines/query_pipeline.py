from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ContextNotFoundError,
    SQLExecutionError,
    SQLValidationError,
)
from app.core.logging import get_logger
from app.database.connection import (
    DatabaseConfig,
    build_config_from_request,
)
from app.database.session import get_async_session_factory
from app.models.context import (
    DatabaseKnowledgeMap,
    KnowledgeMapStatus,
    RetrievalContext,
)
from app.models.database import DatabaseConnectionRequest
from app.models.query import (
    IntentType,
    QueryExplainResponse,
    QueryExecutionResult,
    QueryIntent,
    QueryResponse,
    QueryRetryAttempt,
    SemanticQueryPlan,
    SQLQueryPlan,
    ValidatedSQLQuery,
)
from app.services.database_connection_service import DatabaseConnectionService
from app.services.database_context_service import DatabaseContextService
from app.services.query_answer_service import QueryAnswerService
from app.services.query_intent_service import QueryIntentService
from app.services.query_result_service import QueryResultService
from app.services.schema_retrieval_service import SchemaRetrievalService
from app.services.semantic_query_service import SemanticQueryService
from app.services.sql_execution_service import SQLExecutionService
from app.services.sql_generation_service import SQLGenerationService
from app.services.sql_validation_service import SQLValidationService

logger = get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started: datetime) -> int:
    delta = _now_utc() - started
    return max(0, int(delta.total_seconds() * 1000))


_DEFAULT_DISCLAIMERS: list[str] = [
    "Los resultados pueden contener aproximaciones o datos de muestra.",
    "Verifica siempre cifras críticas directamente en la base de datos.",
    "Esta respuesta no constituye asesoramiento legal, financiero o médico.",
    "Los resultados están limitados por la capacidad de cómputo y el tiempo de consulta.",
]


class QueryPipeline:
    def __init__(
        self,
        *,
        database_context_service: Optional[DatabaseContextService] = None,
        schema_retrieval: Optional[SchemaRetrievalService] = None,
        query_intent_service: Optional[QueryIntentService] = None,
        semantic_query_service: Optional[SemanticQueryService] = None,
        sql_generation_service: Optional[SQLGenerationService] = None,
        sql_validation_service: Optional[SQLValidationService] = None,
        sql_execution_service: Optional[SQLExecutionService] = None,
        query_result_service: Optional[QueryResultService] = None,
        query_answer_service: Optional[QueryAnswerService] = None,
        connection_service: Optional[DatabaseConnectionService] = None,
        sql_retry_count: Optional[int] = None,
        schema_retrieval_top_k: Optional[int] = None,
        max_query_rows: Optional[int] = None,
        query_timeout_seconds: Optional[int] = None,
    ) -> None:
        self.database_context_service = database_context_service
        self.schema_retrieval = schema_retrieval or SchemaRetrievalService()
        self.query_intent_service = query_intent_service or QueryIntentService()
        self.semantic_query_service = semantic_query_service or SemanticQueryService()
        self.sql_generation_service = sql_generation_service or SQLGenerationService()
        self.sql_validation_service = sql_validation_service or SQLValidationService()
        self.sql_execution_service = sql_execution_service or SQLExecutionService()
        self.query_result_service = query_result_service or QueryResultService()
        self.query_answer_service = query_answer_service or QueryAnswerService()
        self.connection_service = connection_service or DatabaseConnectionService()
        self.sql_retry_count = (
            sql_retry_count if sql_retry_count is not None else settings.SQL_RETRY_COUNT
        )
        self.schema_retrieval_top_k = (
            schema_retrieval_top_k
            if schema_retrieval_top_k is not None
            else settings.SCHEMA_RETRIEVAL_TOP_K
        )
        self.max_query_rows = (
            max_query_rows if max_query_rows is not None else settings.MAX_QUERY_ROWS
        )
        self.query_timeout_seconds = (
            query_timeout_seconds
            if query_timeout_seconds is not None
            else settings.QUERY_TIMEOUT_SECONDS
        )

    async def _load_connection_config(
        self, session: AsyncSession, connection_id: UUID
    ) -> DatabaseConfig:
        conn_meta = await self.connection_service.get(
            session, connection_id, decrypt_password=True
        )
        if conn_meta is None:
            raise ContextNotFoundError(
                detail=f"Connection metadata not found for {connection_id}",
                resource_type="connection",
                resource_id=str(connection_id),
            )
        password = (
            conn_meta.encrypted_password.decode("utf-8")
            if isinstance(conn_meta.encrypted_password, (bytes, bytearray))
            else conn_meta.encrypted_password
            if isinstance(conn_meta.encrypted_password, str)
            else ""
        )
        request = DatabaseConnectionRequest(
            database_type=conn_meta.database_type,
            host=conn_meta.host,
            port=conn_meta.port,
            database_name=conn_meta.database_name,
            username=conn_meta.username,
            password=password,
            schema=conn_meta.schema or "public",
            connection_name=conn_meta.connection_name,
        )
        return build_config_from_request(request)

    async def _build_non_query_response(
        self,
        question: str,
        request_id: str,
        intent: QueryIntent,
        semantic_plan: SemanticQueryPlan,
        knowledge_map: DatabaseKnowledgeMap,
    ) -> QueryResponse:
        intent_type = intent.intent_type
        disclaimers = list(_DEFAULT_DISCLAIMERS)
        if intent_type == IntentType.CLARIFICATION:
            bullets = "\n".join(
                f"- {q}" for q in (intent.clarification_questions or ["Por favor, reformula tu pregunta."])
            )
            answer = (
                "Necesito información adicional para responder con precisión:\n"
                f"{bullets}\n\nIncluye más contexto (fechas, nombres, entidades) e inténtalo de nuevo."
            )
            short_answer = "Se requiere aclaración para responder."
        elif intent_type == IntentType.EXPLANATION:
            answer = (
                "Esta pregunta parece solicitar una explicación conceptual. "
                "Aquí tienes un resumen del contexto disponible basado en el esquema actual:\n"
                f"- Dominios detectados: {len(knowledge_map.domains)}\n"
                f"- Entidades de negocio: {len(knowledge_map.entities)}\n"
                f"- Conceptos: {len(knowledge_map.concepts)}\n"
                f"- Métricas: {len(knowledge_map.metrics)}\n"
                f"- Tablas disponibles: {len(knowledge_map.tables)}\n"
                "\nSi buscas datos concretos, reformula la pregunta como una consulta de datos."
            )
            short_answer = "Respuesta explicativa basada en el mapa de conocimiento."
        elif intent_type == IntentType.UNSUPPORTED:
            answer = (
                "Este tipo de consulta no se admite en el agente de base de datos actual. "
                "Prueba con preguntas sobre datos almacenados (ventas, clientes, empleados, inventario, etc.), "
                "indicando métricas, dimensiones y filtros temporales si aplica."
            )
            short_answer = "Tipo de consulta no admitido."
        else:
            answer = (
                "He interpretado tu pregunta como una conversación general. "
                "Si deseas consultar datos de la base de datos, describe qué métricas, "
                "dimensiones o filtros temporales te interesan."
            )
            short_answer = "Respuesta conversacional."
        return QueryResponse(
            question=question,
            answer=answer,
            short_answer=short_answer,
            semantic_plan=semantic_plan,
            sql=None,
            columns=[],
            data=[],
            row_count=0,
            execution_time_ms=0,
            retries=[],
            context_version=knowledge_map.context_version,
            request_id=request_id,
            disclaimers=disclaimers,
        )

    async def run(
        self,
        connection_id: UUID,
        question: str,
        request_id: str,
    ) -> QueryResponse:
        run_started = _now_utc()
        logger.info(
            "QueryPipeline.run started",
            extra={
                "request_id": request_id,
                "connection_id": str(connection_id),
                "question_length": len(question or ""),
            },
        )
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            step_start = _now_utc()
            knowledge_map: Optional[DatabaseKnowledgeMap] = (
                await self.database_context_service.get_latest(connection_id, session)
            )
            if knowledge_map is None:
                raise ContextNotFoundError(
                    detail=f"No knowledge map found for connection {connection_id}",
                    resource_type="knowledge_map",
                    resource_id=str(connection_id),
                )
            if knowledge_map.status != KnowledgeMapStatus.READY:
                raise ContextNotFoundError(
                    detail=(
                        f"Knowledge map for connection {connection_id} is not READY "
                        f"(status={knowledge_map.status.value})"
                    ),
                    resource_type="knowledge_map",
                    resource_id=str(connection_id),
                    extra={"actual_status": knowledge_map.status.value},
                )
            logger.debug(
                "QueryPipeline loaded knowledge map",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(step_start),
                    "context_version": knowledge_map.context_version,
                },
            )

            step_start = _now_utc()
            retrieval_context: RetrievalContext = await self.schema_retrieval.retrieve_context(
                question,
                knowledge_map,
                self.schema_retrieval_top_k,
            )
            logger.debug(
                "QueryPipeline schema retrieval complete",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(step_start),
                    "matches": len(retrieval_context.matches),
                    "tables": len(retrieval_context.relevant_tables),
                },
            )

            step_start = _now_utc()
            intent: QueryIntent = await self.query_intent_service.detect_intent(
                question,
                knowledge_map,
                retrieval_context,
            )
            logger.debug(
                "QueryPipeline intent detected",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(step_start),
                    "intent_type": intent.intent_type.value,
                    "requires_clarification": intent.requires_clarification,
                },
            )

            step_start = _now_utc()
            semantic_plan: SemanticQueryPlan = (
                self.semantic_query_service.build_semantic_plan(
                    question,
                    intent,
                    retrieval_context,
                    knowledge_map,
                )
            )
            logger.debug(
                "QueryPipeline semantic plan built",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(step_start),
                    "entity_refs": len(semantic_plan.entity_refs),
                    "metric_refs": len(semantic_plan.metric_refs),
                },
            )

            if intent.intent_type != IntentType.DATABASE_QUERY or intent.requires_clarification:
                response = await self._build_non_query_response(
                    question, request_id, intent, semantic_plan, knowledge_map
                )
                logger.info(
                    "QueryPipeline returning non-DATABASE_QUERY response",
                    extra={
                        "request_id": request_id,
                        "intent_type": intent.intent_type.value,
                        "total_elapsed_ms": _elapsed_ms(run_started),
                    },
                )
                return response

            retries: list[QueryRetryAttempt] = []
            validated_sql: Optional[ValidatedSQLQuery] = None
            exec_result: Optional[QueryExecutionResult] = None
            last_error: Optional[Exception] = None
            connection_config: Optional[DatabaseConfig] = None
            max_attempts = max(1, self.sql_retry_count + 1)

            for attempt in range(max_attempts):
                attempt_idx = attempt + 1
                logger.info(
                    "QueryPipeline SQL attempt",
                    extra={
                        "request_id": request_id,
                        "attempt": attempt_idx,
                        "max_attempts": max_attempts,
                    },
                )
                try:
                    sql_step_start = _now_utc()
                    previous_sql: Optional[str] = None
                    previous_error: Optional[str] = None
                    if retries:
                        last_retry = retries[-1]
                        previous_sql = last_retry.corrected_sql or None
                        previous_error = last_retry.original_error
                    sql_plan: SQLQueryPlan = (
                        await self.sql_generation_service.generate_sql(
                            semantic_plan=semantic_plan,
                            retrieval_context=retrieval_context,
                            knowledge_map=knowledge_map,
                            dialect="postgresql",
                            previous_sql=previous_sql,
                            previous_error=previous_error,
                            original_question=question,
                        )
                    )
                    logger.debug(
                        "QueryPipeline SQL generated",
                        extra={
                            "request_id": request_id,
                            "elapsed_ms": _elapsed_ms(sql_step_start),
                            "sql_length": len(sql_plan.sql or ""),
                            "tables": sql_plan.tables,
                        },
                    )
                    validate_start = _now_utc()
                    validated_sql = self.sql_validation_service.validate(
                        sql_plan,
                        knowledge_map,
                        retrieval_context,
                    )
                    logger.debug(
                        "QueryPipeline SQL validated",
                        extra={
                            "request_id": request_id,
                            "elapsed_ms": _elapsed_ms(validate_start),
                            "readonly": validated_sql.is_readonly,
                            "max_rows_applied": validated_sql.max_rows_applied,
                        },
                    )
                    if connection_config is None:
                        connection_config = await self._load_connection_config(
                            session, connection_id
                        )
                    exec_start = _now_utc()
                    exec_result = await self.sql_execution_service.execute(
                        connection_config=connection_config,
                        validated=validated_sql,
                        timeout_seconds=self.query_timeout_seconds,
                        max_rows=self.max_query_rows,
                    )
                    logger.info(
                        "QueryPipeline SQL executed successfully",
                        extra={
                            "request_id": request_id,
                            "elapsed_ms": _elapsed_ms(exec_start),
                            "row_count": exec_result.row_count,
                            "truncated": exec_result.truncated,
                        },
                    )
                    break
                except (SQLValidationError, SQLExecutionError, Exception) as exc:
                    last_error = exc
                    error_str = str(exc)
                    attempt_record = QueryRetryAttempt(
                        attempt_index=attempt_idx,
                        original_error=error_str,
                        corrected_sql=None,
                        validation_result=isinstance(exc, SQLValidationError) is False,
                        retry_succeeded=False,
                    )
                    if isinstance(exc, SQLValidationError):
                        attempt_record.corrected_sql = None
                    if attempt >= max_attempts - 1:
                        retries.append(attempt_record)
                        logger.warning(
                            "QueryPipeline SQL final attempt failed",
                            extra={
                                "request_id": request_id,
                                "attempt": attempt_idx,
                                "error": error_str,
                            },
                            exc_info=exc,
                        )
                        break
                    retries.append(attempt_record)
                    logger.info(
                        "QueryPipeline scheduling retry after SQL error",
                        extra={
                            "request_id": request_id,
                            "attempt": attempt_idx,
                            "error_type": type(exc).__name__,
                        },
                    )
                    continue

            if exec_result is None:
                extra_info: dict[str, Any] = {
                    "request_id": request_id,
                    "retries": len(retries),
                }
                if last_error is not None:
                    extra_info["original_error_type"] = type(last_error).__name__
                    extra_info["original_error_message"] = str(last_error)
                raise SQLExecutionError(
                    detail=(
                        "Query execution failed after all retry attempts. "
                        + (str(last_error) if last_error else "Unknown error.")
                    ),
                    extra=extra_info,
                )

            process_start = _now_utc()
            processed_result: QueryExecutionResult = (
                self.query_result_service.process_results(
                    exec_result,
                    max_rows=self.max_query_rows,
                    max_string_len=500,
                    max_tokens=4000,
                )
            )
            logger.debug(
                "QueryPipeline results processed",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(process_start),
                    "final_row_count": processed_result.row_count,
                },
            )

            answer_start = _now_utc()
            raw_sql = validated_sql.validated_sql if validated_sql and hasattr(validated_sql, "validated_sql") else (
                getattr(validated_sql, "sql", None)
                if validated_sql
                else None
            )
            if not raw_sql and validated_sql is not None:
                raw_sql = validated_sql.model_dump().get("validated_sql") or validated_sql.model_dump().get("sql") or ""
            answer, short_answer = await self.query_answer_service.answer_question(
                question=question,
                semantic_plan=semantic_plan,
                sql=raw_sql or "",
                execution_result=processed_result,
                knowledge_map=knowledge_map,
            )
            logger.debug(
                "QueryPipeline answer synthesized",
                extra={
                    "request_id": request_id,
                    "elapsed_ms": _elapsed_ms(answer_start),
                    "answer_length": len(answer or ""),
                },
            )

            disclaimers = list(_DEFAULT_DISCLAIMERS)
            if processed_result.truncated:
                disclaimers.insert(
                    0,
                    "Los resultados fueron truncados para ajustarse al límite máximo de filas configurado.",
                )
            if retries:
                disclaimers.insert(
                    0,
                    f"La consulta requirió {len(retries)} intentos adicionales para generar un SQL válido y ejecutable.",
                )

            response = QueryResponse(
                question=question,
                answer=answer,
                short_answer=short_answer,
                semantic_plan=semantic_plan,
                sql=raw_sql or "",
                columns=processed_result.columns,
                data=processed_result.rows,
                row_count=processed_result.row_count,
                execution_time_ms=processed_result.execution_time_ms,
                retries=retries,
                context_version=knowledge_map.context_version,
                request_id=request_id,
                disclaimers=disclaimers,
            )
            logger.info(
                "QueryPipeline.run finished",
                extra={
                    "request_id": request_id,
                    "connection_id": str(connection_id),
                    "total_elapsed_ms": _elapsed_ms(run_started),
                    "row_count": processed_result.row_count,
                    "retries": len(retries),
                    "intent_type": intent.intent_type.value,
                },
            )
            return response

    async def explain(
        self,
        connection_id: UUID,
        question: str,
        request_id: str,
    ) -> QueryExplainResponse:
        logger.info(
            "QueryPipeline.explain started",
            extra={
                "request_id": request_id,
                "connection_id": str(connection_id),
            },
        )
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            knowledge_map: Optional[DatabaseKnowledgeMap] = (
                await self.database_context_service.get_latest(connection_id, session)
            )
            if knowledge_map is None:
                raise ContextNotFoundError(
                    detail=f"No knowledge map found for connection {connection_id}",
                    resource_type="knowledge_map",
                    resource_id=str(connection_id),
                )
            if knowledge_map.status != KnowledgeMapStatus.READY:
                raise ContextNotFoundError(
                    detail=(
                        f"Knowledge map for connection {connection_id} is not READY "
                        f"(status={knowledge_map.status.value})"
                    ),
                    resource_type="knowledge_map",
                    resource_id=str(connection_id),
                )

            retrieval_context: RetrievalContext = await self.schema_retrieval.retrieve_context(
                question,
                knowledge_map,
                self.schema_retrieval_top_k,
            )
            intent: QueryIntent = await self.query_intent_service.detect_intent(
                question,
                knowledge_map,
                retrieval_context,
            )
            semantic_plan: SemanticQueryPlan = (
                await self.semantic_query_service.build_semantic_plan(
                    question,
                    intent,
                    retrieval_context,
                    knowledge_map,
                )
            )

            intent_summary = (
                f"Intento detectado: {intent.intent_type.value}. "
                f"Entidades mencionadas: {', '.join(intent.entities) if intent.entities else 'ninguna'}. "
                f"Métricas mencionadas: {', '.join(intent.metrics) if intent.metrics else 'ninguna'}."
            )
            if intent.requires_clarification:
                intent_summary += " [PENDIENTE: requiere aclaración del usuario.]"

            plan_parts = []
            if semantic_plan.entity_refs:
                plan_parts.append(f"Entidades a resolver: {', '.join(semantic_plan.entity_refs)}")
            if semantic_plan.metric_refs:
                plan_parts.append(f"Métricas a calcular: {', '.join(semantic_plan.metric_refs)}")
            if semantic_plan.concept_refs:
                plan_parts.append(f"Conceptos relevantes: {', '.join(semantic_plan.concept_refs)}")
            if semantic_plan.dimension_refs:
                plan_parts.append(f"Dimensiones para agrupar: {', '.join(semantic_plan.dimension_refs)}")
            if semantic_plan.time_range:
                tr = semantic_plan.time_range
                if getattr(tr, "relative", None):
                    plan_parts.append(f"Rango temporal relativo: {tr.relative.value}")
                elif getattr(tr, "start", None) or getattr(tr, "end", None):
                    plan_parts.append(
                        "Rango temporal absoluto: "
                        f"{getattr(tr, 'start', None) or '...'} → {getattr(tr, 'end', None) or '...'}"
                    )
            if semantic_plan.filter_refs:
                plan_parts.append(f"Filtros semánticos: {len(semantic_plan.filter_refs)}")
            if semantic_plan.tables_hint:
                plan_parts.append(f"Tablas sugeridas: {', '.join(semantic_plan.tables_hint)}")
            semantic_plan_summary = (
                " | ".join(plan_parts) if plan_parts else "Plan semántico mínimo sin referencias explícitas."
            )

            relevant_concepts: list[dict[str, Any]] = []
            for match in retrieval_context.matches:
                relevant_concepts.append({
                    "type": match.match_type.value,
                    "name": match.name,
                    "code": match.code,
                    "description": match.description,
                    "aliases": list(match.aliases),
                    "tables": list(match.tables),
                    "confidence": float(match.confidence),
                })

            relevant_tables = sorted({
                (f"{getattr(t, 'schema_name', '')}.{getattr(t, 'table_name', '')}"
                 if getattr(t, "schema_name", "")
                 else getattr(t, "table_name", str(t)))
                for t in retrieval_context.relevant_tables
            })

            sql_summary_parts: list[str] = []
            try:
                sql_plan = await self.sql_generation_service.generate_sql(
                    semantic_plan=semantic_plan,
                    retrieval_context=retrieval_context,
                    knowledge_map=knowledge_map,
                    dialect="postgresql",
                )
                validated = await self.sql_validation_service.validate(
                    sql_plan, knowledge_map, retrieval_context
                )
                sql_lines = [line.strip() for line in (sql_plan.sql or "").splitlines() if line.strip()]
                preview = " ".join(sql_lines[:3])
                if len(sql_plan.sql or "") > 240:
                    preview = preview[:232] + " [...]"
                sql_summary_parts.append(
                    f"SQL generado: {preview or '—'}. Tablas: {', '.join(sql_plan.tables) if sql_plan.tables else 'n/d'}."
                )
                sql_summary_parts.append(
                    f"Validación AST: {'OK (solo lectura)' if getattr(validated, 'is_readonly', True) else 'NO PERMITIDA'}."
                )
            except Exception as exc:
                sql_summary_parts.append(
                    f"No se pudo generar SQL validado: {type(exc).__name__}."
                )
            sql_summary = " ".join(sql_summary_parts) if sql_summary_parts else "Generación de SQL omitida."

            execution_notes: list[str] = []
            execution_notes.append(
                f"Contexto: {knowledge_map.context_version} | "
                f"dominios={len(knowledge_map.domains)}, "
                f"entidades={len(knowledge_map.entities)}, "
                f"métricas={len(knowledge_map.metrics)}, "
                f"dimensiones={len(knowledge_map.dimensions)}, "
                f"tablas={len(knowledge_map.tables)}."
            )
            execution_notes.append(
                f"Retrieval: {len(retrieval_context.matches)} coincidencias | "
                f"{len(retrieval_context.relevant_tables)} tablas relevantes | "
                f"{len(retrieval_context.join_paths)} rutas de unión."
            )
            if intent.intent_type != IntentType.DATABASE_QUERY:
                execution_notes.append(
                    f"Nota: el intento detectado es {intent.intent_type.value}, no DATABASE_QUERY. "
                    "Si esperabas datos, reformula la pregunta."
                )
            execution_notes.append(
                "Esta explicación describe el plan de alto nivel; no incluye razonamiento paso a paso ni CoT."
            )

            disclaimer = (
                "Esta explicación es una interpretación de alto nivel del plan de consulta. "
                "Puede contener aproximaciones. El SQL final y los resultados reales pueden "
                "diferir de este resumen según validaciones, límites de filas y reintentos automáticos."
            )

            response = QueryExplainResponse(
                question=question,
                intent_summary=intent_summary,
                semantic_plan_summary=semantic_plan_summary,
                relevant_concepts=relevant_concepts,
                relevant_tables=relevant_tables,
                sql_summary=sql_summary,
                execution_notes=execution_notes,
                disclaimer=disclaimer,
            )
            logger.info(
                "QueryPipeline.explain finished",
                extra={
                    "request_id": request_id,
                    "connection_id": str(connection_id),
                    "intent_type": intent.intent_type.value,
                },
            )
            return response


__all__ = ["QueryPipeline"]
