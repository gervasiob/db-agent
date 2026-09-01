from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = Field(default="development")
    APP_NAME: str = Field(default="DB-Agent-TP-Austral")
    APP_VERSION: str = Field(default="0.1.0")
    DEBUG: bool = Field(default=False)

    OPENAI_API_KEY: Optional[str] = Field(default=None)
    OPENAI_MODEL: str = Field(default="gpt-5-mini")
    OPENAI_EMBEDDING_MODEL: str = Field(default="text-embedding-3-small")
    OPENAI_TEMPERATURE: float = Field(default=0.1)
    OPENAI_MAX_TOKENS: int = Field(default=4000)

    METADATA_DB_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/db_agent_metadata"
    )
    METADATA_DB_SCHEMA: str = Field(default="public")

    DATABASE_SAMPLE_ROWS: int = Field(default=5)
    MAX_QUERY_ROWS: int = Field(default=500)
    QUERY_TIMEOUT_SECONDS: int = Field(default=15)
    SQL_RETRY_COUNT: int = Field(default=2)
    SCHEMA_RETRIEVAL_TOP_K: int = Field(default=8)
    LLM_INCLUDE_SAMPLE_DATA: bool = Field(default=True)
    LLM_SANITIZE_SAMPLE_DATA: bool = Field(default=True)

    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(default="json")

    METADATA_ENCRYPTION_KEY: Optional[str] = Field(default=None)


settings = Settings()
