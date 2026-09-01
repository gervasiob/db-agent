from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ContextNotFoundError, DatabaseConnectionError
from app.core.logging import get_logger
from app.core.security import mask_connection_string
from app.database.adapters.base import DatabaseAdapter
from app.database.connection import (
    DatabaseConfig,
    build_connection_string,
    close_engine,
    create_engine_from_config,
)
from app.models.database import (
    DatabaseConnectionMetadata,
    DatabaseConnectionRequest,
    DatabaseConnectionResponse,
    DatabaseConnectionStatus,
)
from app.repositories.database_repository import DatabaseConnectionRepository

logger = get_logger(__name__)


class DatabaseConnectionService:
    def __init__(
        self,
        repository: Optional[DatabaseConnectionRepository] = None,
    ) -> None:
        self.repository = repository or DatabaseConnectionRepository()

    @staticmethod
    def _request_to_config(
        request: DatabaseConnectionRequest,
    ) -> DatabaseConfig:
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

    async def test_connection(
        self,
        request: DatabaseConnectionRequest,
    ) -> DatabaseConnectionResponse:
        logger.info(
            "Testing database connection",
            extra={
                "database_type": request.database_type,
                "host": request.host,
                "database_name": request.database_name,
            },
        )
        config = self._request_to_config(request)
        cs_result = build_connection_string(config)
        engine = None
        try:
            adapter_cls = DatabaseAdapter.by_database_type(config.database_type)
            adapter = adapter_cls(config)
            ok, server_info, version = await adapter.test_connection()
            engine = getattr(adapter, "_engine", None)
            if ok:
                return DatabaseConnectionResponse(
                    ok=True,
                    message="Connection successful",
                    masked_url=cs_result.redacted,
                    version=version or server_info.get("version"),
                    server_info=server_info,
                )
            error_msg = version or "Unknown connection error"
            return DatabaseConnectionResponse(
                ok=False,
                message=f"Connection failed: {error_msg}",
                masked_url=cs_result.redacted,
                version=server_info.get("version") if server_info else None,
                server_info=server_info or None,
            )
        except Exception as exc:
            logger.error(
                "Database connection test failed unexpectedly",
                extra={
                    "database_type": request.database_type,
                    "host": request.host,
                    "database_name": request.database_name,
                    "connection_string": cs_result.redacted,
                },
                exc_info=exc,
            )
            return DatabaseConnectionResponse(
                ok=False,
                message=f"Connection error: {exc!s}",
                masked_url=cs_result.redacted,
            )
        finally:
            if engine is not None:
                await close_engine(engine)

    async def connect(
        self,
        session: AsyncSession,
        request: DatabaseConnectionRequest,
        connection_name: Optional[str] = None,
    ) -> UUID:
        logger.info(
            "Persisting database connection",
            extra={
                "database_type": request.database_type,
                "host": request.host,
                "database_name": request.database_name,
            },
        )
        config = self._request_to_config(request)
        connection, server_info, err_msg = await self.repository.test_and_save_connection(
            session,
            config,
            connection_name=connection_name,
        )
        if connection is None:
            raise DatabaseConnectionError(
                detail=f"Failed to persist connection: {err_msg or 'unknown error'}",
                extra={
                    "database_type": request.database_type,
                    "host": request.host,
                    "database_name": request.database_name,
                },
            )
        return connection.id

    async def get(
        self,
        session: AsyncSession,
        connection_id: UUID,
        *,
        decrypt_password: bool = False,
    ) -> DatabaseConnectionMetadata:
        logger.debug(
            "Retrieving database connection metadata",
            extra={"connection_id": str(connection_id)},
        )
        result = await self.repository.get_by_id(
            session,
            connection_id,
            decrypt_password=decrypt_password,
        )
        if result is None:
            raise ContextNotFoundError(
                detail=f"Database connection {connection_id} was not found",
                resource_type="database_connection",
                resource_id=str(connection_id),
            )
        return result

    async def list(
        self,
        session: AsyncSession,
        skip: int = 0,
        limit: int = 100,
    ) -> list[DatabaseConnectionMetadata]:
        logger.debug(
            "Listing database connections",
            extra={"skip": skip, "limit": limit},
        )
        return await self.repository.list(session, skip=skip, limit=limit)

    async def delete(
        self,
        session: AsyncSession,
        connection_id: UUID,
    ) -> bool:
        logger.info(
            "Deleting database connection metadata",
            extra={"connection_id": str(connection_id)},
        )
        existing = await self.repository.get_by_id(session, connection_id)
        if existing is None:
            raise ContextNotFoundError(
                detail=f"Database connection {connection_id} was not found",
                resource_type="database_connection",
                resource_id=str(connection_id),
            )
        return await self.repository.delete(session, connection_id)

    async def build_engine_for(
        self,
        metadata: DatabaseConnectionMetadata,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle: int = 1800,
    ):
        password: str = ""
        if metadata.encrypted_password:
            try:
                password = bytes(metadata.encrypted_password).decode("utf-8")
            except Exception:
                password = ""
        if not password:
            raise DatabaseConnectionError(
                detail="Stored connection metadata does not include a usable password",
                extra={"connection_id": str(metadata.id)},
            )
        host = metadata.host
        if isinstance(host, str) and host.strip().lower() == "localhost":
            host = "127.0.0.1"
        config = DatabaseConfig(
            database_type=metadata.database_type,
            host=host,
            port=metadata.port,
            database_name=metadata.database_name,
            username=metadata.username,
            password=password,
            schema=metadata.schema or "public",
        )
        engine = await create_engine_from_config(
            config,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle,
        )
        return engine, config


__all__ = ["DatabaseConnectionService"]
