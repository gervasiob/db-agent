from __future__ import annotations

import json
from typing import Any, Optional, Type, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import settings
from app.core.exceptions import LLMGenerationError, SemanticAnalysisError
from app.core.logging import get_logger
from app.llm.client import async_generate_structured
from app.llm.prompts.system_prompts import (
    BUSINESS_CONCEPT_SYSTEM_PROMPT,
    BUSINESS_METRIC_SYSTEM_PROMPT,
    DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT,
)
from app.llm.schemas.structured_outputs import (
    BusinessConceptOutput,
    BusinessDomainOutput,
    BusinessEntityOutput,
    BusinessMetricOutput,
    ColumnSemanticOutput,
    ConceptExtraction,
    DateSemanticExtraction,
    DateSemanticOutput,
    DimensionExtraction,
    DimensionOutput,
    DomainExtraction,
    EntityExtraction,
    MetricExtraction,
    TableSemanticAnalysis,
    TerminologyEntryOutput,
    TerminologyExtraction,
)
from app.models.database import ColumnProfile, DatabaseMetadata, TableMetadata
from app.models.semantic import PIILevel

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class DatabaseSemanticAnalyzer:
    def __init__(
        self,
        *,
        tables_per_batch: int = 15,
        include_sample_data: Optional[bool] = None,
        sanitize_samples: Optional[bool] = None,
        max_retries: int = 2,
    ) -> None:
        self.tables_per_batch = max(1, tables_per_batch)
        self.include_sample_data = (
            include_sample_data
            if include_sample_data is not None
            else settings.LLM_INCLUDE_SAMPLE_DATA
        )
        self.sanitize_samples = (
            sanitize_samples
            if sanitize_samples is not None
            else settings.LLM_SANITIZE_SAMPLE_DATA
        )
        self.max_retries = max(0, max_retries)

    def _iter_all_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            tables.extend(schema.tables)
            tables.extend(schema.views)
        return tables

    def _chunk_tables(self, tables: list[TableMetadata]) -> list[list[TableMetadata]]:
        chunks: list[list[TableMetadata]] = []
        for idx in range(0, len(tables), self.tables_per_batch):
            chunks.append(tables[idx : idx + self.tables_per_batch])
        return chunks

    def _serialize_column_profile(self, profile: Optional[ColumnProfile]) -> dict[str, Any]:
        if profile is None:
            return {}
        data = profile.model_dump()
        if data.get("sample_distinct_values"):
            vals = list(data["sample_distinct_values"])
            data["sample_distinct_values"] = [str(v)[:200] for v in vals[:10]]
        for key in ("min_value", "max_value"):
            if data.get(key) is not None:
                data[key] = str(data[key])[:200]
        return data

    def _serialize_table(
        self,
        table: TableMetadata,
        *,
        include_samples: bool,
    ) -> dict[str, Any]:
        columns_info: list[dict[str, Any]] = []
        for col in table.columns:
            col_info: dict[str, Any] = {
                "name": col.name,
                "type": col.type.value if hasattr(col.type, "value") else str(col.type),
                "raw_type": col.raw_type,
                "nullable": col.nullable,
                "ordinal_position": col.ordinal_position,
            }
            if col.default_value:
                col_info["default"] = str(col.default_value)[:200]
            if col.comment:
                col_info["comment"] = col.comment
            if col.is_pk:
                col_info["is_pk"] = True
            if col.is_fk:
                col_info["is_fk"] = True
                if col.fk_target_table:
                    col_info["fk_target_table"] = col.fk_target_table
                if col.fk_target_column:
                    col_info["fk_target_column"] = col.fk_target_column
            if col.enum_values:
                col_info["enum_values"] = list(col.enum_values)[:50]
            if col.heuristics:
                flags = col.heuristics.model_dump()
                true_flags = [k for k, v in flags.items() if v]
                if true_flags:
                    col_info["heuristics"] = true_flags
            if table.column_profiles and col.name in table.column_profiles:
                profile = table.column_profiles[col.name]
                profile_dump = self._serialize_column_profile(profile)
                if profile_dump:
                    col_info["profile"] = profile_dump
            columns_info.append(col_info)

        result: dict[str, Any] = {
            "schema": table.schema_name,
            "table": table.table_name,
            "table_type": (
                table.table_type.value
                if hasattr(table.table_type, "value")
                else str(table.table_type)
            ),
            "row_count_estimate": table.row_count_estimate,
            "columns": columns_info,
        }
        if table.comment:
            result["comment"] = table.comment
        if table.primary_key:
            result["primary_key"] = list(table.primary_key)
        if table.indexes:
            result["indexes"] = [
                {
                    "name": idx.name,
                    "columns": idx.columns,
                    "unique": idx.is_unique,
                    "primary": idx.is_primary,
                }
                for idx in table.indexes[:20]
            ]
        if include_samples and table.sample_rows:
            sanitized_samples = table.sample_rows
            if self.sanitize_samples:
                sanitized_samples = []
                for row in table.sample_rows:
                    clean_row = {}
                    for k, v in row.items():
                        if isinstance(v, str):
                            clean_row[k] = v[:500]
                        else:
                            clean_row[k] = v
                    sanitized_samples.append(clean_row)
            result["sample_rows"] = sanitized_samples[: settings.DATABASE_SAMPLE_ROWS]
        return result

    def _build_system_message(self, template) -> SystemMessage:
        if hasattr(template, "messages") and template.messages:
            first = template.messages[0]
            if isinstance(first, tuple):
                _, content = first
                return SystemMessage(content=content)
            if isinstance(first, SystemMessage):
                return SystemMessage(content=first.content)
            if hasattr(first, "content"):
                return SystemMessage(content=str(first.content))
        return SystemMessage(content="You are a helpful semantic database analyst.")

    async def _run_structured(
        self,
        system_message: SystemMessage,
        user_content: str,
        schema: Type[T],
    ) -> T:
        messages: list[BaseMessage] = [
            system_message,
            HumanMessage(content=user_content),
        ]

        try:
            retryable_generate = retry(
                wait=wait_random_exponential(min=1, max=20),
                stop=stop_after_attempt(self.max_retries + 1),
                retry=retry_if_exception_type((LLMGenerationError,)),
                reraise=True,
            )(async_generate_structured)

            result = await retryable_generate(
                messages=messages,
                pydantic_schema=schema,
                retries=self.max_retries,
            )
            return result
        except RetryError as exc:
            logger.error(
                "LLM retries exhausted for semantic analysis",
                extra={"schema": schema.__name__},
                exc_info=exc,
            )
            raise SemanticAnalysisError(
                detail=f"Semantic analysis ({schema.__name__}) failed after retries",
                extra={"schema": schema.__name__},
            ) from exc
        except SemanticAnalysisError:
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error in structured semantic analysis",
                extra={"schema": schema.__name__},
                exc_info=exc,
            )
            raise SemanticAnalysisError(
                detail=f"Semantic analysis ({schema.__name__}) failed: {exc!s}",
                extra={"schema": schema.__name__},
            ) from exc

    async def analyze_table_semantics(
        self,
        table: TableMetadata,
        samples: Optional[list[dict[str, Any]]] = None,
        profiles: Optional[dict[str, ColumnProfile]] = None,
    ) -> TableSemanticAnalysis:
        if samples is not None and self.include_sample_data:
            previous = table.sample_rows
            table.sample_rows = list(samples)
        if profiles is not None:
            table.column_profiles = dict(profiles)

        payload = {
            "table": self._serialize_table(table, include_samples=self.include_sample_data),
        }

        user_text = (
            "Analyze the following table schema and produce structured semantic analysis:\n\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, default=str)}\n```"
        )

        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, TableSemanticAnalysis)

        existing = {c.name for c in table.columns}
        cleaned_columns: list[ColumnSemanticOutput] = []
        for cs in result.column_semantics:
            if cs.name in existing:
                cleaned_columns.append(cs)
        result.column_semantics = cleaned_columns

        if samples is not None:
            table.sample_rows = samples
        return result

    async def analyze_batch_table_semantics(
        self,
        tables: list[TableMetadata],
    ) -> list[TableSemanticAnalysis]:
        results: list[TableSemanticAnalysis] = []
        for table in tables:
            try:
                analysis = await self.analyze_table_semantics(table)
                results.append(analysis)
            except Exception as exc:
                logger.warning(
                    "Table semantics analysis failed for table, using minimal fallback",
                    extra={
                        "table": f"{table.schema_name}.{table.table_name}",
                        "error": str(exc),
                    },
                )
                fallback_columns: list[ColumnSemanticOutput] = []
                for col in table.columns:
                    fallback_columns.append(
                        ColumnSemanticOutput(
                            name=col.name,
                            business_name=col.name.replace("_", " ").title(),
                            description=(
                                col.comment or f"Column {col.name} of type {col.type}"
                            ),
                            semantic_type=(
                                "KEY"
                                if col.is_pk
                                else ("ID" if col.name.lower().endswith("_id") else "ATTRIBUTE")
                            ),
                            pii_level=(
                                PIILevel.MEDIUM
                                if (
                                    col.heuristics
                                    and (
                                        col.heuristics.is_email
                                        or col.heuristics.is_phone
                                        or col.heuristics.is_address
                                    )
                                )
                                else PIILevel.NONE
                            ),
                            confidence=0.5,
                        )
                    )
                results.append(
                    TableSemanticAnalysis(
                        business_domain="Uncategorized",
                        business_entity=table.table_name.replace("_", " ").title(),
                        table_description=(
                            table.comment
                            or f"Table {table.table_name} containing {len(table.columns)} columns"
                        ),
                        column_semantics=fallback_columns,
                    )
                )
        return results

    async def analyze_domains(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[TableSemanticAnalysis],
    ) -> DomainExtraction:
        tables = self._iter_all_tables(metadata)
        include_samples = self.include_sample_data
        table_payloads = [
            self._serialize_table(t, include_samples=include_samples) for t in tables
        ]
        analyses_payload = [a.model_dump() for a in table_analyses]

        user_text = (
            "Given the database schema metadata below, extract meaningful business domains.\n\n"
            "## Schema tables:\n"
            f"```json\n{json.dumps(table_payloads, ensure_ascii=False, default=str)}\n```\n\n"
            "## Table-level semantic analysis:\n"
            f"```json\n{json.dumps(analyses_payload, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, DomainExtraction)
        return result

    async def analyze_entities(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[TableSemanticAnalysis],
        domain_extraction: Optional[DomainExtraction] = None,
    ) -> EntityExtraction:
        tables = self._iter_all_tables(metadata)
        table_payloads = [
            self._serialize_table(t, include_samples=self.include_sample_data)
            for t in tables
        ]
        analyses_payload = [a.model_dump() for a in table_analyses]
        domains_payload = (
            [d.model_dump() for d in domain_extraction.domains]
            if domain_extraction is not None
            else []
        )

        user_text = (
            "Given the database schema metadata and prior semantic analysis, extract business entities.\n\n"
            "## Schema tables:\n"
            f"```json\n{json.dumps(table_payloads, ensure_ascii=False, default=str)}\n```\n\n"
            "## Table semantic analysis:\n"
            f"```json\n{json.dumps(analyses_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior domains (if any):\n"
            f"```json\n{json.dumps(domains_payload, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, EntityExtraction)
        return result

    async def analyze_concepts(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[TableSemanticAnalysis],
        entity_extraction: Optional[EntityExtraction] = None,
        domain_extraction: Optional[DomainExtraction] = None,
    ) -> ConceptExtraction:
        tables = self._iter_all_tables(metadata)
        table_payloads = [
            self._serialize_table(t, include_samples=self.include_sample_data)
            for t in tables
        ]
        analyses_payload = [a.model_dump() for a in table_analyses]
        entities_payload = (
            [e.model_dump() for e in entity_extraction.entities]
            if entity_extraction is not None
            else []
        )
        domains_payload = (
            [d.model_dump() for d in domain_extraction.domains]
            if domain_extraction is not None
            else []
        )

        user_text = (
            "Given the database schema metadata, extract reusable business concepts (filterable conditions or computed categories).\n\n"
            "## Schema tables:\n"
            f"```json\n{json.dumps(table_payloads, ensure_ascii=False, default=str)}\n```\n\n"
            "## Table semantic analysis:\n"
            f"```json\n{json.dumps(analyses_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior entity extraction:\n"
            f"```json\n{json.dumps(entities_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior domain extraction:\n"
            f"```json\n{json.dumps(domains_payload, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(BUSINESS_CONCEPT_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, ConceptExtraction)
        return result

    async def analyze_metrics(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[TableSemanticAnalysis],
        concept_extraction: Optional[ConceptExtraction] = None,
        entity_extraction: Optional[EntityExtraction] = None,
    ) -> MetricExtraction:
        tables = self._iter_all_tables(metadata)
        table_payloads = [
            self._serialize_table(t, include_samples=self.include_sample_data)
            for t in tables
        ]
        analyses_payload = [a.model_dump() for a in table_analyses]
        concepts_payload = (
            [c.model_dump() for c in concept_extraction.concepts]
            if concept_extraction is not None
            else []
        )
        entities_payload = (
            [e.model_dump() for e in entity_extraction.entities]
            if entity_extraction is not None
            else []
        )

        user_text = (
            "Given the database schema metadata, extract meaningful business metrics/KPIs.\n\n"
            "## Schema tables:\n"
            f"```json\n{json.dumps(table_payloads, ensure_ascii=False, default=str)}\n```\n\n"
            "## Table semantic analysis:\n"
            f"```json\n{json.dumps(analyses_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior business concepts:\n"
            f"```json\n{json.dumps(concepts_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior entities:\n"
            f"```json\n{json.dumps(entities_payload, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(BUSINESS_METRIC_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, MetricExtraction)
        return result

    async def analyze_dimensions(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[TableSemanticAnalysis],
        metric_extraction: Optional[MetricExtraction] = None,
        entity_extraction: Optional[EntityExtraction] = None,
    ) -> DimensionExtraction:
        tables = self._iter_all_tables(metadata)
        table_payloads = [
            self._serialize_table(t, include_samples=False) for t in tables
        ]
        analyses_payload = [a.model_dump() for a in table_analyses]
        metrics_payload = (
            [m.model_dump() for m in metric_extraction.metrics]
            if metric_extraction is not None
            else []
        )
        entities_payload = (
            [e.model_dump() for e in entity_extraction.entities]
            if entity_extraction is not None
            else []
        )

        user_text = (
            "Given the database schema metadata, extract business dimensions (categorical, temporal, geographic, organizational).\n\n"
            "## Schema tables:\n"
            f"```json\n{json.dumps(table_payloads, ensure_ascii=False, default=str)}\n```\n\n"
            "## Table semantic analysis:\n"
            f"```json\n{json.dumps(analyses_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior metrics:\n"
            f"```json\n{json.dumps(metrics_payload, ensure_ascii=False, default=str)}\n```\n\n"
            "## Prior entities:\n"
            f"```json\n{json.dumps(entities_payload, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, DimensionExtraction)
        return result

    async def analyze_date_semantics(
        self,
        metadata: DatabaseMetadata,
        table_profiles: Optional[dict[str, dict[str, ColumnProfile]]] = None,
        samples: Optional[dict[str, list[dict[str, Any]]]] = None,
    ) -> DateSemanticExtraction:
        tables = self._iter_all_tables(metadata)
        include_samples = self.include_sample_data
        date_candidate_payloads: list[dict[str, Any]] = []
        for table in tables:
            tkey = f"{table.schema_name}.{table.table_name}"
            table_dump: dict[str, Any] = {
                "schema": table.schema_name,
                "table": table.table_name,
            }
            date_cols: list[dict[str, Any]] = []
            for col in table.columns:
                heur = col.heuristics
                is_date_candidate = (
                    heur
                    and (heur.is_date or heur.is_timestamp or heur.is_created_at or heur.is_updated_at)
                ) or col.type in {"DATE", "TIMESTAMP", "TIMESTAMPTZ"}
                if not is_date_candidate:
                    continue
                col_dump: dict[str, Any] = {
                    "name": col.name,
                    "type": col.type.value if hasattr(col.type, "value") else str(col.type),
                    "heuristics": [
                        k
                        for k, v in (heur.model_dump() if heur else {}).items()
                        if v
                    ],
                }
                if col.comment:
                    col_dump["comment"] = col.comment
                if table_profiles and tkey in table_profiles and col.name in table_profiles[tkey]:
                    col_dump["profile"] = self._serialize_column_profile(
                        table_profiles[tkey][col.name]
                    )
                if include_samples and samples and tkey in samples:
                    values = []
                    for row in samples[tkey]:
                        if col.name in row and row[col.name] is not None:
                            values.append(str(row[col.name])[:100])
                            if len(values) >= 5:
                                break
                    if values:
                        col_dump["sample_values"] = values
                date_cols.append(col_dump)
            if date_cols:
                table_dump["columns"] = date_cols
                date_candidate_payloads.append(table_dump)

        user_text = (
            "For each date/timestamp column in the schema, assign a semantic role such as CREATED_AT, "
            "UPDATED_AT, DELETED_AT, ORDER_DATE, INVOICE_DATE, PAYMENT_DATE, HIRE_DATE, TERMINATION_DATE, "
            "BIRTH_DATE, EVENT_DATE, or OTHER if none fits.\n\n"
            "## Date columns per table:\n"
            f"```json\n{json.dumps(date_candidate_payloads, ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, DateSemanticExtraction)
        return result

    async def analyze_terminology(
        self,
        domains: Optional[DomainExtraction] = None,
        entities: Optional[EntityExtraction] = None,
        concepts: Optional[ConceptExtraction] = None,
        metrics: Optional[MetricExtraction] = None,
        dimensions: Optional[DimensionExtraction] = None,
        dates: Optional[DateSemanticExtraction] = None,
        table_analyses: Optional[list[TableSemanticAnalysis]] = None,
    ) -> TerminologyExtraction:
        entries: list[dict[str, Any]] = []
        if domains is not None:
            for d in domains.domains:
                entries.append(
                    {
                        "term": d.name,
                        "canonical_form": d.name,
                        "aliases": d.aliases,
                        "type": "DOMAIN",
                        "target_code": d.code,
                        "confidence": d.confidence,
                    }
                )
        if entities is not None:
            for e in entities.entities:
                entries.append(
                    {
                        "term": e.name,
                        "canonical_form": e.name,
                        "aliases": e.aliases,
                        "type": "ENTITY",
                        "target_code": e.code,
                        "confidence": e.confidence,
                    }
                )
        if concepts is not None:
            for c in concepts.concepts:
                entries.append(
                    {
                        "term": c.name,
                        "canonical_form": c.name,
                        "aliases": c.aliases,
                        "type": "CONCEPT",
                        "target_code": c.code,
                        "confidence": c.confidence,
                    }
                )
        if metrics is not None:
            for m in metrics.metrics:
                entries.append(
                    {
                        "term": m.name,
                        "canonical_form": m.name,
                        "aliases": m.aliases,
                        "type": "METRIC",
                        "target_code": m.code,
                        "confidence": m.confidence,
                    }
                )
        if dimensions is not None:
            for d in dimensions.dimensions:
                entries.append(
                    {
                        "term": d.name,
                        "canonical_form": d.name,
                        "aliases": d.aliases,
                        "type": "DIMENSION",
                        "target_code": d.code,
                        "confidence": d.confidence,
                    }
                )
        if dates is not None:
            for dt in dates.dates:
                entries.append(
                    {
                        "term": f"{dt.table_name} {dt.column_name}",
                        "canonical_form": f"{dt.table_name}.{dt.column_name}",
                        "aliases": dt.aliases,
                        "type": "COLUMN",
                        "target_code": None,
                        "confidence": dt.confidence,
                    }
                )
        if table_analyses is not None:
            for analysis in table_analyses:
                entries.append(
                    {
                        "term": analysis.business_entity,
                        "canonical_form": analysis.business_entity,
                        "aliases": [],
                        "type": "TABLE",
                        "target_code": None,
                        "confidence": 0.75,
                    }
                )
                for cs in analysis.column_semantics:
                    entries.append(
                        {
                            "term": cs.business_name,
                            "canonical_form": cs.name,
                            "aliases": [],
                            "type": "COLUMN",
                            "target_code": cs.entity_code,
                            "confidence": cs.confidence,
                        }
                    )

        user_text = (
            "Deduplicate and curate the following terminology candidates into a single consistent glossary. "
            "Remove near-duplicates, merge aliases, and keep only the most confident entries.\n\n"
            "## Candidate entries:\n"
            f"```json\n{json.dumps(entries[:500], ensure_ascii=False, default=str)}\n```"
        )
        system = self._build_system_message(DB_SEMANTIC_ANALYSIS_SYSTEM_PROMPT)
        result = await self._run_structured(system, user_text, TerminologyExtraction)

        baseline: list[TerminologyEntryOutput] = []
        if not result.entries:
            for raw in entries[:500]:
                try:
                    baseline.append(TerminologyEntryOutput(**raw))
                except Exception:
                    continue
            result.entries = baseline

        return result


__all__ = ["DatabaseSemanticAnalyzer"]
