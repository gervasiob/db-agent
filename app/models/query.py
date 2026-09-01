from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class IntentType(str, Enum):
    DATABASE_QUERY = "DATABASE_QUERY"
    CLARIFICATION = "CLARIFICATION"
    EXPLANATION = "EXPLANATION"
    UNSUPPORTED = "UNSUPPORTED"
    CHITCHAT = "CHITCHAT"


class RelativeTimeRange(str, Enum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_QUARTER = "THIS_QUARTER"
    THIS_YEAR = "THIS_YEAR"
    LAST_YEAR = "LAST_YEAR"
    LAST_7_DAYS = "LAST_7_DAYS"
    LAST_30_DAYS = "LAST_30_DAYS"
    LAST_90_DAYS = "LAST_90_DAYS"
    CURRENT_MONTH_TO_DATE = "CURRENT_MONTH_TO_DATE"
    CURRENT_YEAR_TO_DATE = "CURRENT_YEAR_TO_DATE"


class FilterOperator(str, Enum):
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    BETWEEN = "BETWEEN"
    LIKE = "LIKE"
    CONTAINS = "CONTAINS"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class TimeRange(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    relative: Optional[RelativeTimeRange] = None


class SemanticFilter(BaseModel):
    field: str
    operator: FilterOperator
    values: list[Any] = Field(default_factory=list)
    is_negated: bool = False


class QueryIntent(BaseModel):
    intent_type: IntentType = IntentType.DATABASE_QUERY
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    filters: list[SemanticFilter] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    raw_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SemanticQueryPlan(BaseModel):
    id: Optional[str] = None
    intent: QueryIntent
    entity_refs: list[str] = Field(default_factory=list)
    metric_refs: list[str] = Field(default_factory=list)
    concept_refs: list[str] = Field(default_factory=list)
    dimension_refs: list[str] = Field(default_factory=list)
    filter_refs: list[SemanticFilter] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    order_by: Optional[list[dict[str, Any]]] = None
    limit: Optional[int] = None
    semantic_description: str
    tables_hint: list[str] = Field(default_factory=list)


class SQLQueryPlan(BaseModel):
    sql: str
    dialect: Literal["postgresql", "sqlserver", "mysql"]
    tables: list[str] = Field(default_factory=list)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    ctes: list[dict[str, Any]] = Field(default_factory=list)
    where: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    having: list[str] = Field(default_factory=list)
    order_by: list[dict[str, Any]] = Field(default_factory=list)
    limit: Optional[int] = None
    parameters: list[Any] = Field(default_factory=list)


class ValidatedSQLQuery(BaseModel):
    validated_sql: str
    dialect: Literal["postgresql", "sqlserver", "mysql"]
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    ast_signature: str
    is_readonly: bool = True
    max_rows_applied: bool = False


class QueryExecutionResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    truncated: bool = False
    applied_limit: Optional[int] = None
    timeout_seconds: Optional[int] = None


class QueryRetryAttempt(BaseModel):
    attempt_index: int
    original_error: str
    corrected_sql: Optional[str] = None
    validation_result: Optional[bool] = None
    retry_succeeded: Optional[bool] = None


class QueryResponse(BaseModel):
    question: str
    answer: str
    short_answer: Optional[str] = None
    semantic_plan: Optional[SemanticQueryPlan] = None
    sql: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    retries: list[QueryRetryAttempt] = Field(default_factory=list)
    context_version: Optional[str] = None
    request_id: str
    disclaimers: list[str] = Field(default_factory=list)


class QueryExplainResponse(BaseModel):
    question: str
    intent_summary: str
    semantic_plan_summary: str
    relevant_concepts: list[dict[str, Any]] = Field(default_factory=list)
    relevant_tables: list[str] = Field(default_factory=list)
    sql_summary: str
    execution_notes: list[str] = Field(default_factory=list)
    disclaimer: str
