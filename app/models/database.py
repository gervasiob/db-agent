from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ColumnType(str, Enum):
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    SMALLINT = "SMALLINT"
    TEXT = "TEXT"
    VARCHAR = "VARCHAR"
    CHAR = "CHAR"
    BOOLEAN = "BOOLEAN"
    FLOAT = "FLOAT"
    NUMERIC = "NUMERIC"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMPTZ = "TIMESTAMPTZ"
    TIME = "TIME"
    JSON = "JSON"
    JSONB = "JSONB"
    UUID = "UUID"
    BYTEA = "BYTEA"
    ENUM = "ENUM"
    UNKNOWN = "UNKNOWN"


class ConstraintType(str, Enum):
    PRIMARY_KEY = "PRIMARY_KEY"
    FOREIGN_KEY = "FOREIGN_KEY"
    UNIQUE = "UNIQUE"
    CHECK = "CHECK"
    EXCLUDE = "EXCLUDE"


class DatabaseConnectionStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    CONNECTED = "CONNECTED"
    READY = "READY"
    ERROR = "ERROR"


class TableType(str, Enum):
    TABLE = "TABLE"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"


class RelationshipType(str, Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"


class DatabaseConnectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_type: str
    host: str
    port: int
    database_name: str
    username: str
    password: str
    schema: Optional[str] = None
    ssl_mode: Optional[str] = None


class DatabaseConnectionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    message: str
    masked_url: str
    version: Optional[str] = None
    server_info: Optional[dict[str, Any]] = None


class DatabaseConnectionMetadata(BaseModel):
    id: UUID
    connection_name: Optional[str] = None
    database_type: str
    host: str
    port: int
    database_name: str
    username: str
    schema: Optional[str] = None
    encrypted_password: Optional[bytes] = None
    status: DatabaseConnectionStatus = DatabaseConnectionStatus.DISCOVERED
    last_connected_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class IndexMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    columns: list[str]
    is_unique: bool = False
    is_primary: bool = False


class ConstraintMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: ConstraintType
    columns: list[str]
    definition: Optional[str] = None


class ColumnHeuristicFlags(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_id: bool = False
    is_name: bool = False
    is_description: bool = False
    is_status: bool = False
    is_category: bool = False
    is_date: bool = False
    is_timestamp: bool = False
    is_monetary: bool = False
    is_created_at: bool = False
    is_updated_at: bool = False
    is_audit: bool = False
    is_soft_delete: bool = False
    is_email: bool = False
    is_phone: bool = False
    is_address: bool = False


class ColumnProfile(BaseModel):
    distinct_count_approx: Optional[int] = None
    null_percentage: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    sample_distinct_values: Optional[list[Any]] = None


class ColumnMetadata(BaseModel):
    name: str
    type: ColumnType
    raw_type: str
    nullable: bool = True
    default_value: Optional[str] = None
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    ordinal_position: int
    comment: Optional[str] = None
    is_pk: bool = False
    is_fk: bool = False
    fk_target_table: Optional[str] = None
    fk_target_column: Optional[str] = None
    enum_values: Optional[list[str]] = None
    heuristics: ColumnHeuristicFlags = Field(default_factory=ColumnHeuristicFlags)


class TableMetadata(BaseModel):
    schema_name: str
    table_name: str
    table_type: TableType = TableType.TABLE
    row_count_estimate: int = 0
    comment: Optional[str] = None
    columns: list[ColumnMetadata] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[IndexMetadata] = Field(default_factory=list)
    constraints: list[ConstraintMetadata] = Field(default_factory=list)
    sample_rows: Optional[list[dict[str, Any]]] = None
    column_profiles: Optional[dict[str, ColumnProfile]] = None


class RelationshipMetadata(BaseModel):
    source_schema: str
    source_table: str
    source_columns: list[str]
    target_schema: str
    target_table: str
    target_columns: list[str]
    relationship_type: RelationshipType
    inferred: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Optional[str] = None


class SchemaMetadata(BaseModel):
    schema_name: str
    tables: list[TableMetadata] = Field(default_factory=list)
    views: list[TableMetadata] = Field(default_factory=list)


class DatabaseMetadata(BaseModel):
    database_type: str
    database_name: str
    schemas: list[SchemaMetadata] = Field(default_factory=list)
    relationships: list[RelationshipMetadata] = Field(default_factory=list)
    estimated_total_rows: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)
