from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    OrderByStruct,
    QueryIntentStructured,
    SQLCteStruct,
    SQLJoinStruct,
    SQLQueryPlanStructured,
    SemanticFilterStruct,
    SemanticQueryPlanStructured,
    TableSemanticAnalysis,
    TerminologyEntryOutput,
    TerminologyExtraction,
    TimeRangeStruct,
)


class LLMColumnSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    business_name: str
    description: str
    semantic_type: str
    pii_level: str
    entity_code: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMTableSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: str
    table: str
    business_entity: str
    business_domain: str
    description: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMSemanticBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[LLMTableSemantic] = Field(default_factory=list)
    columns: list[LLMColumnSemantic] = Field(default_factory=list)


class LLMBusinessDomain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class LLMBusinessEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    domain_code: Optional[str] = None
    tables: list[str] = Field(default_factory=list)
    primary_table: Optional[str] = None
    key_columns: list[str] = Field(default_factory=list)
    status_column: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class LLMBusinessConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    entity_code: Optional[str] = None
    domain_code: Optional[str] = None
    tables: list[str] = Field(default_factory=list)
    source_columns: list[str] = Field(default_factory=list)
    condition_semantic: Optional[str] = None
    condition_sql: Optional[str] = None
    is_filter: bool = False
    is_computed: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class LLMBusinessMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    domain_code: Optional[str] = None
    entity_code: Optional[str] = None
    source_tables: list[str] = Field(default_factory=list)
    source_column: Optional[str] = None
    aggregation: str
    filter_condition: Optional[str] = None
    date_column: Optional[str] = None
    default_dimensions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class LLMDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    dimension_type: str
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    requires_joins: list[SQLJoinStruct] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMDateSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    semantic_role: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMTerminology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TerminologyEntryOutput] = Field(default_factory=list)


class LLMIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: str
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_range: Optional[TimeRangeStruct] = Field(default_factory=TimeRangeStruct)
    filters: list[SemanticFilterStruct] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMSQLPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str
    tables: list[str] = Field(default_factory=list)
    joins: list[SQLJoinStruct] = Field(default_factory=list)
    ctes: list[SQLCteStruct] = Field(default_factory=list)
    where: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    having: list[str] = Field(default_factory=list)
    order_by: list[OrderByStruct] = Field(default_factory=list)
    limit: int = 0
    notes: list[str] = Field(default_factory=list)


class LLMAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    short_answer: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)
    requires_clarification: bool = False
    follow_up: list[str] = Field(default_factory=list)


class LLMExplain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_summary: str
    semantic_plan_summary: str
    relevant_concepts: list[dict[str, Any]] = Field(default_factory=list)
    relevant_tables: list[str] = Field(default_factory=list)
    sql_summary: str
    execution_notes: list[str] = Field(default_factory=list)
    disclaimer: str


__all__ = [
    "LLMColumnSemantic",
    "LLMTableSemantic",
    "LLMSemanticBatch",
    "LLMBusinessDomain",
    "LLMBusinessEntity",
    "LLMBusinessConcept",
    "LLMBusinessMetric",
    "LLMDimension",
    "LLMDateSemantic",
    "LLMTerminology",
    "LLMIntent",
    "LLMSQLPlan",
    "LLMAnswer",
    "LLMExplain",
    "ColumnSemanticOutput",
    "TableSemanticAnalysis",
    "BusinessDomainOutput",
    "DomainExtraction",
    "BusinessEntityOutput",
    "EntityExtraction",
    "BusinessConceptOutput",
    "ConceptExtraction",
    "BusinessMetricOutput",
    "MetricExtraction",
    "DimensionOutput",
    "DimensionExtraction",
    "DateSemanticOutput",
    "DateSemanticExtraction",
    "TerminologyEntryOutput",
    "TerminologyExtraction",
    "TimeRangeStruct",
    "SemanticFilterStruct",
    "QueryIntentStructured",
    "OrderByStruct",
    "SemanticQueryPlanStructured",
    "SQLJoinStruct",
    "SQLCteStruct",
    "SQLQueryPlanStructured",
]
