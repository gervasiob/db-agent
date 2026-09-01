from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.database import RelationshipMetadata, TableMetadata
from app.models.semantic import (
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
    ColumnSemantic,
    DateSemantic,
    Dimension,
    EnumValue,
    RelationshipGraph,
    TerminologyEntry,
)


class KnowledgeMapStatus(str, Enum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


class ContextStatus(str, Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    DISCOVERING = "DISCOVERING"
    SAMPLING = "SAMPLING"
    PROFILING = "PROFILING"
    SEMANTIC_ANALYZING = "SEMANTIC_ANALYZING"
    BUILDING_KNOWLEDGE_MAP = "BUILDING_KNOWLEDGE_MAP"
    GENERATING_EMBEDDINGS = "GENERATING_EMBEDDINGS"
    READY = "READY"
    ERROR = "ERROR"


class MatchType(str, Enum):
    CONCEPT = "CONCEPT"
    METRIC = "METRIC"
    ENTITY = "ENTITY"
    DOMAIN = "DOMAIN"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    DIMENSION = "DIMENSION"
    TERMINOLOGY = "TERMINOLOGY"
    OTHER = "OTHER"


class DatabaseKnowledgeMap(BaseModel):
    id: Optional[UUID] = None
    database_connection_id: UUID
    context_version: str
    schema_hash: str
    database_summary: str
    domains: list[BusinessDomain] = Field(default_factory=list)
    entities: list[BusinessEntity] = Field(default_factory=list)
    concepts: list[BusinessConcept] = Field(default_factory=list)
    metrics: list[BusinessMetric] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    tables: list[TableMetadata] = Field(default_factory=list)
    column_semantics: list[ColumnSemantic] = Field(default_factory=list)
    relationships: list[RelationshipMetadata] = Field(default_factory=list)
    relationship_graph: Optional[RelationshipGraph] = Field(default_factory=RelationshipGraph)
    date_semantics: list[DateSemantic] = Field(default_factory=list)
    enum_values: list[EnumValue] = Field(default_factory=list)
    join_paths: list[dict[str, Any]] = Field(default_factory=list)
    terminology: list[TerminologyEntry] = Field(default_factory=list)
    llm_model: str
    embedding_model: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: KnowledgeMapStatus = KnowledgeMapStatus.BUILDING


class DatabaseContextStatus(BaseModel):
    status: ContextStatus = ContextStatus.IDLE
    current_step: Optional[str] = None
    completed_steps: int = 0
    total_steps: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    context_version: Optional[str] = None


class DiscoveryRun(BaseModel):
    id: UUID
    connection_id: UUID
    steps: list[dict[str, Any]] = Field(default_factory=list)
    status: ContextStatus = ContextStatus.IDLE
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class RetrievalMatch(BaseModel):
    match_type: MatchType
    code: Optional[str] = None
    name: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RetrievalContext(BaseModel):
    question: str
    matches: list[RetrievalMatch] = Field(default_factory=list)
    relevant_tables: list[TableMetadata] = Field(default_factory=list)
    relevant_relationships: list[RelationshipMetadata] = Field(default_factory=list)
    relevant_concepts: list[BusinessConcept] = Field(default_factory=list)
    relevant_metrics: list[BusinessMetric] = Field(default_factory=list)
    relevant_entities: list[BusinessEntity] = Field(default_factory=list)
    relevant_dimensions: list[Dimension] = Field(default_factory=list)
    join_paths: list[dict[str, Any]] = Field(default_factory=list)
    sample_rows_enabled: bool = True
