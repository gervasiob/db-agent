from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AggregationType(str, Enum):
    SUM = "SUM"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    MEDIAN = "MEDIAN"
    NONE = "NONE"


class DimensionType(str, Enum):
    CATEGORICAL = "CATEGORICAL"
    TEMPORAL = "TEMPORAL"
    GEOGRAPHIC = "GEOGRAPHIC"
    ORGANIZATIONAL = "ORGANIZATIONAL"


class DateSemanticRole(str, Enum):
    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    DELETED_AT = "DELETED_AT"
    ORDER_DATE = "ORDER_DATE"
    INVOICE_DATE = "INVOICE_DATE"
    PAYMENT_DATE = "PAYMENT_DATE"
    HIRE_DATE = "HIRE_DATE"
    TERMINATION_DATE = "TERMINATION_DATE"
    BIRTH_DATE = "BIRTH_DATE"
    EVENT_DATE = "EVENT_DATE"
    OTHER = "OTHER"


class TerminologyEntryType(str, Enum):
    DOMAIN = "DOMAIN"
    ENTITY = "ENTITY"
    CONCEPT = "CONCEPT"
    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    COLUMN = "COLUMN"
    TABLE = "TABLE"
    OTHER = "OTHER"


class RelationshipEdgeType(str, Enum):
    FK = "FK"
    INFERRED = "INFERRED"
    JOIN_PATH = "JOIN_PATH"


class SemanticType(str, Enum):
    ATTRIBUTE = "ATTRIBUTE"
    KEY = "KEY"
    STATUS = "STATUS"
    FLAG = "FLAG"
    METRIC = "METRIC"
    DATE = "DATE"
    ID = "ID"
    CATEGORY = "CATEGORY"
    TEXT = "TEXT"


class PIILevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    REDACTED = "REDACTED"


class BusinessDomain(BaseModel):
    id: Optional[UUID] = None
    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Optional[list[str]] = None


class BusinessEntity(BaseModel):
    id: Optional[UUID] = None
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
    evidence: Optional[list[str]] = None


class BusinessConcept(BaseModel):
    id: Optional[UUID] = None
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
    value_expression: Optional[str] = None
    is_filter: bool = False
    is_computed: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Optional[list[str]] = None


class BusinessMetric(BaseModel):
    id: Optional[UUID] = None
    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    domain_code: Optional[str] = None
    entity_code: Optional[str] = None
    source_tables: list[str] = Field(default_factory=list)
    source_column: Optional[str] = None
    aggregation: AggregationType = AggregationType.NONE
    filter_condition: Optional[str] = None
    date_column: Optional[str] = None
    default_dimensions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Optional[list[str]] = None


class Dimension(BaseModel):
    id: Optional[UUID] = None
    name: str
    code: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    dimension_type: DimensionType = DimensionType.CATEGORICAL
    source_table: Optional[str] = None
    source_column: Optional[str] = None
    values: Optional[list[Any]] = None
    requires_joins: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DateSemantic(BaseModel):
    id: Optional[UUID] = None
    table_name: str
    column_name: str
    semantic_role: DateSemanticRole = DateSemanticRole.OTHER
    description: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EnumValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    table_name: str
    column_name: str
    values: list[str]
    description: Optional[str] = None


class TerminologyEntry(BaseModel):
    id: Optional[UUID] = None
    term: str
    canonical_form: str
    aliases: list[str] = Field(default_factory=list)
    type: TerminologyEntryType = TerminologyEntryType.OTHER
    target_code: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RelationshipEdge(BaseModel):
    id: Optional[str] = None
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    edge_type: RelationshipEdgeType = RelationshipEdgeType.FK
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RelationshipGraph(BaseModel):
    nodes: list[str] = Field(default_factory=list)
    edges: list[RelationshipEdge] = Field(default_factory=list)


class ColumnSemantic(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    business_name: str
    description: str
    semantic_type: SemanticType = SemanticType.ATTRIBUTE
    entity_code: Optional[str] = None
    domain_code: Optional[str] = None
    concept_code: Optional[str] = None
    pii_level: PIILevel = PIILevel.NONE
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
