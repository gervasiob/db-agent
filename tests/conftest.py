from __future__ import annotations

from typing import Any, AsyncGenerator, Generator
from unittest.mock import MagicMock

import pytest
from pydantic_settings import BaseSettings

from app.models.database import (
    ColumnHeuristicFlags,
    ColumnMetadata,
    ColumnType,
    DatabaseConnectionRequest,
    TableMetadata,
    TableType,
)


class FakeSettings(BaseSettings):
    APP_ENV: str = "test"
    APP_NAME: str = "DB-Agent-Test"
    APP_VERSION: str = "0.0.0-test"
    DEBUG: bool = False

    OPENAI_API_KEY: str | None = "sk-test-key"
    OPENAI_MODEL: str = "gpt-5-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_TEMPERATURE: float = 0.0
    OPENAI_MAX_TOKENS: int = 128

    METADATA_DB_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/db_agent_metadata"
    METADATA_DB_SCHEMA: str = "public"

    DATABASE_SAMPLE_ROWS: int = 3
    MAX_QUERY_ROWS: int = 100
    QUERY_TIMEOUT_SECONDS: int = 2
    SQL_RETRY_COUNT: int = 0
    SCHEMA_RETRIEVAL_TOP_K: int = 4
    LLM_INCLUDE_SAMPLE_DATA: bool = False
    LLM_SANITIZE_SAMPLE_DATA: bool = True

    LOG_LEVEL: str = "WARNING"
    LOG_FORMAT: str = "text"

    METADATA_ENCRYPTION_KEY: str = "0123456789012345678901234567890123456789012="


@pytest.fixture(scope="session")
def fake_settings() -> FakeSettings:
    return FakeSettings()


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch, fake_settings: FakeSettings) -> FakeSettings:
    import app.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "settings", fake_settings)
    return fake_settings


@pytest.fixture
def fake_metadata_session() -> MagicMock:
    session = MagicMock(name="fake_metadata_session")
    return session


@pytest.fixture
def fake_connection_request() -> DatabaseConnectionRequest:
    return DatabaseConnectionRequest(
        database_type="postgresql",
        host="localhost",
        port=5432,
        database_name="test",
        username="db_agent_ro",
        password="testpassword123",
        schema="public",
        ssl_mode="disable",
    )


@pytest.fixture
def sample_users_table() -> TableMetadata:
    return TableMetadata(
        schema_name="public",
        table_name="users",
        table_type=TableType.TABLE,
        row_count_estimate=1000,
        comment="Users table",
        columns=[
            ColumnMetadata(
                name="id",
                type=ColumnType.INTEGER,
                raw_type="integer",
                nullable=False,
                ordinal_position=1,
                is_pk=True,
                heuristics=ColumnHeuristicFlags(is_id=True),
            ),
            ColumnMetadata(
                name="name",
                type=ColumnType.VARCHAR,
                raw_type="character varying",
                nullable=False,
                ordinal_position=2,
                heuristics=ColumnHeuristicFlags(is_name=True),
            ),
            ColumnMetadata(
                name="email",
                type=ColumnType.VARCHAR,
                raw_type="character varying",
                nullable=False,
                ordinal_position=3,
                heuristics=ColumnHeuristicFlags(is_email=True),
            ),
            ColumnMetadata(
                name="status",
                type=ColumnType.VARCHAR,
                raw_type="character varying",
                nullable=True,
                ordinal_position=4,
                heuristics=ColumnHeuristicFlags(is_status=True),
            ),
            ColumnMetadata(
                name="created_at",
                type=ColumnType.TIMESTAMPTZ,
                raw_type="timestamp with time zone",
                nullable=False,
                ordinal_position=5,
                heuristics=ColumnHeuristicFlags(is_created_at=True, is_timestamp=True),
            ),
        ],
        primary_key=["id"],
        indexes=[],
        constraints=[],
        sample_rows=None,
        column_profiles=None,
    )


@pytest.fixture
def sample_orders_table() -> TableMetadata:
    return TableMetadata(
        schema_name="public",
        table_name="orders",
        table_type=TableType.TABLE,
        row_count_estimate=50000,
        comment="Customer orders",
        columns=[
            ColumnMetadata(
                name="id",
                type=ColumnType.BIGINT,
                raw_type="bigint",
                nullable=False,
                ordinal_position=1,
                is_pk=True,
                heuristics=ColumnHeuristicFlags(is_id=True),
            ),
            ColumnMetadata(
                name="user_id",
                type=ColumnType.INTEGER,
                raw_type="integer",
                nullable=False,
                ordinal_position=2,
                is_fk=True,
                fk_target_table="public.users",
                fk_target_column="id",
                heuristics=ColumnHeuristicFlags(is_id=True),
            ),
            ColumnMetadata(
                name="total",
                type=ColumnType.NUMERIC,
                raw_type="numeric(10,2)",
                nullable=False,
                ordinal_position=3,
                heuristics=ColumnHeuristicFlags(is_monetary=True),
            ),
            ColumnMetadata(
                name="created_at",
                type=ColumnType.TIMESTAMPTZ,
                raw_type="timestamp with time zone",
                nullable=False,
                ordinal_position=4,
                heuristics=ColumnHeuristicFlags(is_created_at=True, is_timestamp=True),
            ),
        ],
        primary_key=["id"],
        indexes=[],
        constraints=[],
        sample_rows=None,
        column_profiles=None,
    )


@pytest.fixture
def sample_pii_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "name": "Juan Perez",
            "email": "juan.perez@example.com",
            "phone": "+54 9 11 1234-5678",
            "card": "4111 1111 1111 1111",
            "dni": "12.345.678",
            "address": "Av. Siempre Viva 123, CABA",
            "password": "mySuperSecret123",
            "salary": 55000.50,
        },
        {
            "id": 2,
            "name": "Maria Gomez",
            "email": "maria.g@company.co",
            "phone": "11-5555-0001",
            "card": "5500 0000 0000 0004",
            "dni": "33456789",
            "address": "Calle 456, La Plata",
            "password": "abc123xyz",
            "salary": 72000.00,
        },
    ]
