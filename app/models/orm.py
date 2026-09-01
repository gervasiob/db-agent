import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    type_annotation_map = {
        Dict[str, Any]: JSONB,
        List[str]: JSONB,
        List[Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


class DatabaseConnectionMetadata(Base):
    __tablename__ = "database_connections_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    connection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    database_type: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(512), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    schema: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_salt: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    __table_args__ = (
        Index(
            "ix_connections_db_type_host_dbname",
            "database_type",
            "host",
            "database_name",
        ),
    )

    context_versions: Mapped[List["DatabaseContextVersion"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )
    discovery_runs: Mapped[List["DatabaseDiscoveryRun"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class DatabaseContextVersion(Base):
    __tablename__ = "database_context_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections_metadata.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    database_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    llm_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "context_version",
            name="uq_context_versions_connection_version",
        ),
        Index("ix_context_versions_connection_id", "connection_id"),
    )

    connection: Mapped["DatabaseConnectionMetadata"] = relationship(
        back_populates="context_versions"
    )
    tables_metadata: Mapped[List["DatabaseTableMetadata"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    relationships: Mapped[List["DatabaseRelationship"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    business_domains: Mapped[List["DatabaseBusinessDomain"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    business_entities: Mapped[List["DatabaseBusinessEntity"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    business_concepts: Mapped[List["DatabaseBusinessConcept"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    metrics: Mapped[List["DatabaseMetric"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    dimensions: Mapped[List["DatabaseDimension"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    terminology: Mapped[List["DatabaseTerminology"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )
    embeddings: Mapped[List["DatabaseEmbedding"]] = relationship(
        back_populates="context_version", cascade="all, delete-orphan"
    )


class DatabaseTableMetadata(Base):
    __tablename__ = "database_tables_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_type: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count_estimate: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "context_version_id",
            "schema_name",
            "table_name",
            name="uq_tables_metadata_context_schema_table",
        ),
        Index("ix_tables_metadata_context_version_id", "context_version_id"),
        Index(
            "ix_tables_metadata_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="tables_metadata"
    )
    columns_metadata: Mapped[List["DatabaseColumnMetadata"]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )


class DatabaseColumnMetadata(Base):
    __tablename__ = "database_columns_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_tables_metadata.id", ondelete="CASCADE"),
        nullable=False,
    )
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_type: Mapped[str] = mapped_column(String(255), nullable=False)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_pk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_fk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fk_target: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    semantic_role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pii_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_columns_metadata_table_id", "table_id"),
        Index(
            "ix_columns_metadata_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    table: Mapped["DatabaseTableMetadata"] = relationship(
        back_populates="columns_metadata"
    )


class DatabaseRelationship(Base):
    __tablename__ = "database_relationships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_table: Mapped[str] = mapped_column(String(512), nullable=False)
    source_column: Mapped[str] = mapped_column(String(255), nullable=False)
    target_table: Mapped[str] = mapped_column(String(512), nullable=False)
    target_column: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_relationships_context_version_id", "context_version_id"),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="relationships"
    )


class DatabaseBusinessDomain(Base):
    __tablename__ = "database_business_domains"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aliases: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_business_domains_context_version_id", "context_version_id"),
        Index(
            "ix_business_domains_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="business_domains"
    )


class DatabaseBusinessEntity(Base):
    __tablename__ = "database_business_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    aliases: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    tables: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_business_entities_context_version_id", "context_version_id"),
        Index(
            "ix_business_entities_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="business_entities"
    )


class DatabaseBusinessConcept(Base):
    __tablename__ = "database_business_concepts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aliases: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    entity_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tables: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    condition_semantic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    condition_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_business_concepts_context_version_id", "context_version_id"),
        Index(
            "ix_business_concepts_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="business_concepts"
    )


class DatabaseMetric(Base):
    __tablename__ = "database_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aliases: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    source_tables: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    source_column: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    aggregation: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    filter_condition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date_column: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_metrics_context_version_id", "context_version_id"),
        Index(
            "ix_metrics_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="metrics"
    )


class DatabaseDimension(Base):
    __tablename__ = "database_dimensions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_table: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_column: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    requires_joins: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_dimensions_context_version_id", "context_version_id"),
        Index(
            "ix_dimensions_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="dimensions"
    )


class DatabaseTerminology(Base):
    __tablename__ = "database_terminology"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_form: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    target_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )

    __table_args__ = (
        Index("ix_terminology_context_version_id", "context_version_id"),
        Index(
            "ix_terminology_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="terminology"
    )


class DatabaseEmbedding(Base):
    __tablename__ = "database_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_context_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )
    embedding_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    __table_args__ = (
        Index("ix_embeddings_context_version_id", "context_version_id"),
        Index(
            "ix_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    context_version: Mapped["DatabaseContextVersion"] = relationship(
        back_populates="embeddings"
    )


class DatabaseDiscoveryRun(Base):
    __tablename__ = "database_discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections_metadata.id", ondelete="CASCADE"),
        nullable=False,
    )
    steps: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_discovery_runs_connection_id", "connection_id"),
    )

    connection: Mapped["DatabaseConnectionMetadata"] = relationship(
        back_populates="discovery_runs"
    )
