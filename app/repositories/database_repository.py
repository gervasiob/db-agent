from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import decrypt_secret, encrypt_secret
from app.database.connection import (
    DatabaseConfig,
    close_engine,
    create_engine_from_config,
)
from app.models.database import (
    DatabaseConnectionMetadata as PydanticConnectionMetadata,
    DatabaseConnectionStatus,
)
from app.models.orm import DatabaseConnectionMetadata as ORMConn

logger = get_logger(__name__)


class DatabaseConnectionRepository:
    @staticmethod
    def _orm_to_pydantic(
        orm_obj: ORMConn,
        *,
        decrypt_password: bool = False,
    ) -> PydanticConnectionMetadata:
        password_bytes: Optional[bytes] = None
        if decrypt_password:
            try:
                plaintext = decrypt_secret(orm_obj.encrypted_password)
                password_bytes = plaintext.encode("utf-8")
            except Exception as exc:
                logger.warning(
                    "Failed to decrypt password for connection",
                    extra={"connection_id": str(orm_obj.id)},
                    exc_info=exc,
                )
                password_bytes = None
        return PydanticConnectionMetadata(
            id=orm_obj.id,
            connection_name=orm_obj.connection_name,
            database_type=orm_obj.database_type,
            host=orm_obj.host,
            port=orm_obj.port,
            database_name=orm_obj.database_name,
            username=orm_obj.username,
            schema=orm_obj.schema,
            encrypted_password=password_bytes,
            status=DatabaseConnectionStatus(orm_obj.status)
            if orm_obj.status in {s.value for s in DatabaseConnectionStatus}
            else DatabaseConnectionStatus.DISCOVERED,
            last_connected_at=orm_obj.last_connected_at,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at,
        )

    async def create(
        self,
        session: AsyncSession,
        db_config: DatabaseConfig,
        connection_name: Optional[str] = None,
    ) -> UUID:
        salt = secrets.token_urlsafe(16)
        encrypted = encrypt_secret(
            db_config.password,
            associated_fields=[
                ("connection_id", "pending"),
                ("username", db_config.username),
                ("salt", salt),
            ],
        )
        name = (
            connection_name.strip()
            if connection_name and connection_name.strip()
            else f"{db_config.database_type}-{db_config.host}-{db_config.database_name}"
        )
        orm_conn = ORMConn(
            connection_name=name,
            database_type=db_config.database_type,
            host=db_config.host,
            port=db_config.port,
            database_name=db_config.database_name,
            username=db_config.username,
            schema=db_config.schema,
            encrypted_password=encrypted,
            encryption_salt=salt,
            status=DatabaseConnectionStatus.DISCOVERED.value,
            last_connected_at=None,
        )
        session.add(orm_conn)
        await session.flush()
        logger.info(
            "Created database connection metadata",
            extra={
                "connection_id": str(orm_conn.id),
                "database_type": db_config.database_type,
                "host": db_config.host,
                "database_name": db_config.database_name,
            },
        )
        return orm_conn.id

    async def get_by_id(
        self,
        session: AsyncSession,
        conn_id: UUID,
        *,
        decrypt_password: bool = False,
    ) -> Optional[PydanticConnectionMetadata]:
        stmt = select(ORMConn).where(ORMConn.id == conn_id)
        result = await session.execute(stmt)
        orm_obj = result.scalar_one_or_none()
        if orm_obj is None:
            return None
        return self._orm_to_pydantic(orm_obj, decrypt_password=decrypt_password)

    async def update_status(
        self,
        session: AsyncSession,
        conn_id: UUID,
        status: str | DatabaseConnectionStatus,
        last_connected_at: Optional[datetime] = None,
    ) -> bool:
        status_value = status.value if isinstance(status, DatabaseConnectionStatus) else status
        stmt = select(ORMConn).where(ORMConn.id == conn_id)
        result = await session.execute(stmt)
        orm_obj = result.scalar_one_or_none()
        if orm_obj is None:
            logger.warning(
                "Cannot update status for missing connection",
                extra={"connection_id": str(conn_id)},
            )
            return False
        orm_obj.status = status_value
        if last_connected_at is not None:
            orm_obj.last_connected_at = last_connected_at
        await session.flush()
        logger.debug(
            "Updated connection status",
            extra={
                "connection_id": str(conn_id),
                "status": status_value,
            },
        )
        return True

    async def delete(
        self,
        session: AsyncSession,
        conn_id: UUID,
    ) -> bool:
        stmt = select(ORMConn).where(ORMConn.id == conn_id)
        result = await session.execute(stmt)
        orm_obj = result.scalar_one_or_none()
        if orm_obj is None:
            return False
        await session.delete(orm_obj)
        await session.flush()
        logger.info(
            "Deleted database connection metadata",
            extra={"connection_id": str(conn_id)},
        )
        return True

    async def list(
        self,
        session: AsyncSession,
        skip: int = 0,
        limit: int = 100,
    ) -> list[PydanticConnectionMetadata]:
        stmt = (
            select(ORMConn)
            .order_by(ORMConn.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await session.execute(stmt)
        orm_objects = result.scalars().all()
        return [
            self._orm_to_pydantic(obj, decrypt_password=False)
            for obj in orm_objects
        ]

    async def test_and_save_connection(
        self,
        session: AsyncSession,
        config: DatabaseConfig,
        *,
        connection_name: Optional[str] = None,
    ) -> tuple[Optional[PydanticConnectionMetadata], Optional[dict[str, Any]], Optional[str]]:
        engine = None
        server_info: Optional[dict[str, Any]] = None
        err_message: Optional[str] = None
        connection: Optional[PydanticConnectionMetadata] = None
        try:
            engine = await create_engine_from_config(config, pool_size=2, max_overflow=4, pool_recycle=300)
            server_info = {}
            try:
                async with engine.connect() as conn:
                    if config.database_type == "postgresql":
                        version_result = await conn.execute(
                            func.version()
                        )
                        version_row = version_result.fetchone()
                        if version_row:
                            server_info["version"] = version_row[0]
                        current_db = await conn.execute(
                            func.current_database()
                        )
                        current_db_row = current_db.fetchone()
                        if current_db_row:
                            server_info["current_database"] = current_db_row[0]
                        current_user = await conn.execute(
                            func.current_user()
                        )
                        current_user_row = current_user.fetchone()
                        if current_user_row:
                            server_info["current_user"] = current_user_row[0]
                        now_result = await conn.execute(func.now())
                        now_row = now_result.fetchone()
                        if now_row:
                            server_info["server_time"] = str(now_row[0])
                    elif config.database_type == "mysql":
                        version_result = await conn.execute(
                            func.version()
                        )
                        version_row = version_result.fetchone()
                        if version_row:
                            server_info["version"] = version_row[0]
                    elif config.database_type == "sqlserver":
                        version_result = await conn.execute(
                            func.text("SELECT @@VERSION")
                        )
                        version_row = version_result.fetchone()
                        if version_row:
                            server_info["version"] = version_row[0]
                    server_info["database_type"] = config.database_type
                    server_info["host"] = config.host
                    server_info["port"] = config.port
                    server_info["database_name"] = config.database_name
                    server_info["schema"] = config.schema
                    await conn.commit()
            except SQLAlchemyError as query_exc:
                logger.warning(
                    "Could not query full server info during test",
                    extra={
                        "database_type": config.database_type,
                        "host": config.host,
                    },
                    exc_info=query_exc,
                )
                if not server_info:
                    server_info = {
                        "database_type": config.database_type,
                        "host": config.host,
                        "port": config.port,
                        "database_name": config.database_name,
                    }
            conn_id = await self.create(session, config, connection_name=connection_name)
            now = datetime.now(timezone.utc)
            await self.update_status(
                session,
                conn_id,
                DatabaseConnectionStatus.CONNECTED.value,
                last_connected_at=now,
            )
            fetched = await self.get_by_id(session, conn_id, decrypt_password=False)
            connection = fetched
            logger.info(
                "Connection test succeeded and metadata saved",
                extra={
                    "connection_id": str(conn_id),
                    "database_type": config.database_type,
                },
            )
        except Exception as exc:
            err_message = str(exc)
            logger.error(
                "Connection test failed",
                extra={
                    "database_type": config.database_type,
                    "host": config.host,
                    "database_name": config.database_name,
                },
                exc_info=exc,
            )
            try:
                conn_id = await self.create(session, config, connection_name=connection_name)
                await self.update_status(
                    session,
                    conn_id,
                    DatabaseConnectionStatus.ERROR.value,
                )
                connection = await self.get_by_id(session, conn_id, decrypt_password=False)
            except Exception as inner_exc:
                logger.error(
                    "Could not persist failed connection record",
                    exc_info=inner_exc,
                )
        finally:
            await close_engine(engine)
        return connection, server_info, err_message
