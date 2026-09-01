from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.semantic import PIILevel


class ColumnSemanticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    business_name: str
    description: str
    semantic_type: str = "ATTRIBUTE"
    entity_code: Optional[str] = None
    concept_code: Optional[str] = None
    pii_level: PIILevel = PIILevel.NONE
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TableSemanticAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_domain: str
    business_entity: str
    table_description: str
    column_semantics: list[ColumnSemanticOutput] = Field(default_factory=list)


class BusinessDomainOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DomainExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[BusinessDomainOutput] = Field(default_factory=list)


class BusinessEntityOutput(BaseModel):
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


class EntityExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[BusinessEntityOutput] = Field(default_factory=list)


class BusinessConceptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    entity_code: Optional[str] = None
    tables: list[str] = Field(default_factory=list)
    source_columns: list[str] = Field(default_factory=list)
    condition_semantic: Optional[str] = None
    condition_sql: Optional[str] = None
    value_expression: Optional[str] = None
    is_filter: bool = False
    is_computed: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class ConceptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concepts: list[BusinessConceptOutput] = Field(default_factory=list)


class BusinessMetricOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    source_column: Optional[str] = None
    aggregation: str = "NONE"
    filter_condition: Optional[str] = None
    date_column: Optional[str] = None
    default_dimensions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class MetricExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[BusinessMetricOutput] = Field(default_factory=list)


class DimensionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    dimension_type: str = "CATEGORICAL"
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_joins: bool = False


class DimensionExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: list[DimensionOutput] = Field(default_factory=list)


class DateSemanticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: str
    column_name: str
    semantic_role: str = "OTHER"
    description: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DateSemanticExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dates: list[DateSemanticOutput] = Field(default_factory=list)


class TerminologyEntryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    canonical_form: str
    aliases: list[str] = Field(default_factory=list)
    type: str = "OTHER"
    target_code: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TerminologyExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TerminologyEntryOutput] = Field(default_factory=list)


class TimeRangeStruct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Optional[str] = None
    end: Optional[str] = None
    relative: Optional[str] = None


class SemanticFilterStruct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: str = "EQ"
    values: list[Any] = Field(default_factory=list)
    is_negated: bool = False


class QueryIntentStructured(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: str = "DATABASE_QUERY"
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_range: TimeRangeStruct = Field(default_factory=TimeRangeStruct)
    filters: list[SemanticFilterStruct] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class OrderByStruct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    direction: str = "DESC"


class SemanticQueryPlanStructured(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_refs: list[str] = Field(default_factory=list)
    metric_refs: list[str] = Field(default_factory=list)
    concept_refs: list[str] = Field(default_factory=list)
    dimension_refs: list[str] = Field(default_factory=list)
    filters: list[SemanticFilterStruct] = Field(default_factory=list)
    time_range: Optional[TimeRangeStruct] = None
    order_by: list[OrderByStruct] = Field(default_factory=list)
    limit: Optional[int] = None
    description: str = ""
    tables_hint: list[str] = Field(default_factory=list)


class SQLJoinStruct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    join_type: str = "INNER"
    left_table: str
    right_table: str
    on_conditions: list[str] = Field(default_factory=list)


class SQLCteStruct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    sql: str


class SQLQueryPlanStructured(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str
    dialect: str = "postgresql"
    tables: list[str] = Field(default_factory=list)
    joins: list[SQLJoinStruct] = Field(default_factory=list)
    ctes: list[SQLCteStruct] = Field(default_factory=list)
    where: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    having: list[str] = Field(default_factory=list)
    order_by: list[OrderByStruct] = Field(default_factory=list)
    limit: Optional[int] = None


__all__ = [
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
