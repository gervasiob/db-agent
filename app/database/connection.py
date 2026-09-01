from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, MutableMapping, Optional
from urllib.parse import quote_plus, urlparse, urlunparse

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.exceptions import DatabaseConnectionError, InvalidInputError
from app.core.logging import get_logger
from app.core.security import mask_connection_string, mask_password


logger = get_logger(__name__)

_DATABASE_TYPE = Literal["postgresql", "sqlserver", "mysql"]

_DEFAULT_PORTS: dict[str, int] = {
    "postgresql": 5432,
    "sqlserver": 1433,
    "mysql": 3306,
}

_ASYNC_DRIVERS: dict[str, str] = {
    "postgresql": "postgresql+asyncpg",
    "sqlserver": "mssql+aioodbc",
    "mysql": "mysql+asyncmy",
}


class DatabaseConfig(BaseModel):
    database_type: _DATABASE_TYPE
    host: str
    port: int = Field(ge=1, le=65535)
    database_name: str
    username: str
    password: str = Field(default="", repr=False)
    schema: str = "public"
    ssl_mode: Optional[str] = None
    connect_timeout: int = Field(default=10, ge=1, le=600)

    model_config = {
        "frozen": False,
        "validate_assignment": True,
        "str_strip_whitespace": True,
    }

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("host cannot be empty")
        if len(stripped) > 253:
            raise ValueError("host exceeds maximum length of 253 characters")
        if re.search(r"\s", stripped):
            raise ValueError("host must not contain whitespace")
        return stripped

    @field_validator("database_name")
    @classmethod
    def validate_database_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("database_name cannot be empty")
        if len(stripped) > 128:
            raise ValueError("database_name exceeds maximum length of 128 characters")
        return stripped

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("username cannot be empty")
        if len(stripped) > 128:
            raise ValueError("username exceeds maximum length of 128 characters")
        return stripped

    @field_validator("schema")
    @classmethod
    def validate_schema(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("schema cannot be empty")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", stripped):
            raise ValueError(f"schema '{stripped}' is not a valid identifier")
        return stripped

    @field_validator("ssl_mode")
    @classmethod
    def validate_ssl_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            return None
        valid_values = {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }
        if stripped not in valid_values:
            raise ValueError(f"ssl_mode must be one of {sorted(valid_values)}")
        return stripped

    @model_validator(mode="before")
    @classmethod
    def set_default_port(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        db_type = data.get("database_type")
        port = data.get("port")
        if (port is None or port == 0) and db_type in _DEFAULT_PORTS:
            data = dict(data)
            data["port"] = _DEFAULT_PORTS[db_type]
        return data

    def to_redacted_dict(self) -> MutableMapping[str, Any]:
        data = self.model_dump()
        data["password"] = mask_password(self.password)
        return data


@dataclass(frozen=True)
class ConnectionStringResult:
    raw: str
    redacted: str

    def __repr__(self) -> str:
        return f"ConnectionStringResult(raw=<REDACTED>, redacted={self.redacted!r})"


def build_connection_string(config: DatabaseConfig, *, include_query: bool = True) -> ConnectionStringResult:
    db_type = config.database_type
    driver = _ASYNC_DRIVERS.get(db_type)
    if not driver:
        raise InvalidInputError(
            detail=f"Unsupported database type: {db_type}",
            extra={"database_type": db_type},
        )
    user = quote_plus(config.username)
    password = quote_plus(config.password)
    host = config.host
    port = config.port
    db = quote_plus(config.database_name)
    authority = f"{user}:{password}@{host}:{port}"
    redacted_authority = f"{user}:***@{host}:{port}"
    raw = f"{driver}://{authority}/{db}"
    redacted = f"{driver}://{redacted_authority}/{db}"
    if include_query:
        query_params: list[str] = []
        if config.database_type == "mysql":
            query_params.append(f"connect_timeout={config.connect_timeout}")
        if config.ssl_mode and db_type == "postgresql":
            query_params.append(f"sslmode={config.ssl_mode}")
        if query_params:
            joined = "&".join(query_params)
            raw = f"{raw}?{joined}"
            redacted = f"{redacted}?{joined}"
    return ConnectionStringResult(raw=raw, redacted=redacted)


def build_connect_args(config: DatabaseConfig) -> MutableMapping[str, Any]:
    connect_args: MutableMapping[str, Any] = {"timeout": config.connect_timeout}
    if config.database_type == "postgresql":
        connect_args["statement_cache_size"] = 0
        connect_args["server_settings"] = {
            "search_path": config.schema,
            "application_name": "db-agent-tp-austral",
            "client_encoding": "utf8",
        }
        if config.ssl_mode:
            connect_args["ssl"] = config.ssl_mode != "disable"
    elif config.database_type == "mysql":
        connect_args.setdefault("charset", "utf8mb4")
    return connect_args


async def create_engine_from_config(
    config: DatabaseConfig,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_recycle: int = 1800,
    pool_pre_ping: bool = True,
    echo: bool = False,
    future: bool = True,
) -> AsyncEngine:
    cs = build_connection_string(config)
    connect_args = build_connect_args(config)
    logger.info(
        "Creating async engine for database",
        extra={
            "database_type": config.database_type,
            "host": config.host,
            "port": config.port,
            "database_name": config.database_name,
            "schema": config.schema,
            "connection_string": cs.redacted,
            "pool_size": pool_size,
            "max_overflow": max_overflow,
        },
    )
    try:
        engine = create_async_engine(
            cs.raw,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle,
            pool_pre_ping=pool_pre_ping,
            echo=echo,
            future=future,
            connect_args=connect_args,
            execution_options={
                "schema_translate_map": {None: config.schema},
                "isolation_level": "READ COMMITTED",
            },
        )
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.commit()
    except Exception as exc:
        logger.error(
            "Failed to create async engine for database",
            extra={
                "database_type": config.database_type,
                "host": config.host,
                "database_name": config.database_name,
                "connection_string": cs.redacted,
            },
            exc_info=exc,
        )
        raise DatabaseConnectionError(
            detail=f"Could not establish connection to {config.database_type} database: {exc}",
            extra={
                "database_type": config.database_type,
                "host": config.host,
                "database_name": config.database_name,
                "connection_string": cs.redacted,
            },
        ) from exc
    logger.info(
        "Database engine created and connectivity verified",
        extra={
            "database_type": config.database_type,
            "host": config.host,
            "database_name": config.database_name,
            "schema": config.schema,
        },
    )
    return engine


async def close_engine(engine: Optional[AsyncEngine]) -> None:
    if engine is None:
        return
    try:
        logger.debug("Disposing async database engine")
        await engine.dispose()
        logger.debug("Async database engine disposed successfully")
    except Exception as exc:
        logger.warning(
            "Error while disposing database engine",
            exc_info=exc,
        )


def build_config_from_request(request: Any) -> DatabaseConfig:
    from app.models.database import DatabaseConnectionRequest

    if not isinstance(request, DatabaseConnectionRequest):
        raise InvalidInputError(
            detail="Expected DatabaseConnectionRequest",
            field="request",
        )
    return DatabaseConfig(
        database_type=request.database_type,
        host=request.host,
        port=request.port,
        database_name=request.database_name,
        username=request.username,
        password=request.password,
        schema=request.schema or "public",
        ssl_mode=request.ssl_mode,
    )


def parse_connection_string_to_config(
    connection_string: str,
    *,
    schema: Optional[str] = None,
    ssl_mode: Optional[str] = None,
    connect_timeout: int = 10,
) -> DatabaseConfig:
    if not isinstance(connection_string, str) or not connection_string.strip():
        raise InvalidInputError(detail="Connection string cannot be empty")
    try:
        parsed = urlparse(connection_string)
    except Exception as exc:
        raise InvalidInputError(detail="Malformed connection string") from exc
    scheme = parsed.scheme.lower().split("+")[0]
    type_map: dict[str, _DATABASE_TYPE] = {
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "pgsql": "postgresql",
        "mysql": "mysql",
        "mssql": "sqlserver",
        "sqlserver": "sqlserver",
    }
    db_type = type_map.get(scheme)
    if not db_type:
        raise InvalidInputError(
            detail=f"Unsupported database scheme '{scheme}' in connection string",
            extra={"scheme": scheme},
        )
    host = parsed.hostname
    if not host:
        raise InvalidInputError(detail="Connection string is missing a hostname")
    port = parsed.port or _DEFAULT_PORTS[db_type]
    path = parsed.path.lstrip("/")
    database_name = path.split("?", 1)[0].split("#", 1)[0]
    if not database_name:
        raise InvalidInputError(detail="Connection string is missing a database name")
    username = parsed.username or ""
    password = parsed.password or ""
    if not username:
        raise InvalidInputError(detail="Connection string is missing a username")
    return DatabaseConfig(
        database_type=db_type,
        host=host,
        port=port,
        database_name=database_name,
        username=username,
        password=password,
        schema=schema or "public",
        ssl_mode=ssl_mode,
        connect_timeout=connect_timeout,
    )
