from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.exceptions import DatabaseConnectionError
from app.core.logging import get_logger
from app.core.security import mask_connection_string


logger = get_logger(__name__)

_metadata_engine: Optional[AsyncEngine] = None
_metadata_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_metadata_scoped_session: Optional[async_scoped_session[AsyncSession]] = None


def _build_metadata_connect_args() -> dict:
    return {
        "timeout": settings.QUERY_TIMEOUT_SECONDS,
        "server_settings": {
            "search_path": settings.METADATA_DB_SCHEMA,
            "application_name": "db-agent-tp-austral-metadata",
            "client_encoding": "utf8",
        },
    }


def _build_metadata_engine() -> AsyncEngine:
    redacted_url = mask_connection_string(settings.METADATA_DB_URL)
    logger.info(
        "Constructing metadata database engine",
        extra={
            "database_url": redacted_url,
            "schema": settings.METADATA_DB_SCHEMA,
            "pool_pre_ping": True,
        },
    )
    return create_async_engine(
        settings.METADATA_DB_URL,
        echo=settings.DEBUG and settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_timeout=30,
        future=True,
        connect_args=_build_metadata_connect_args(),
        execution_options={
            "schema_translate_map": {None: settings.METADATA_DB_SCHEMA},
            "isolation_level": "READ COMMITTED",
        },
    )


def _build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
        twophase=False,
    )


def _build_scoped_session(factory: async_sessionmaker[AsyncSession]) -> async_scoped_session[AsyncSession]:
    return async_scoped_session(factory, scopefunc=asyncio.current_task)


def get_metadata_engine() -> AsyncEngine:
    global _metadata_engine
    if _metadata_engine is None:
        raise DatabaseConnectionError(
            detail="Metadata database engine has not been initialized. Call init_metadata_db() first.",
            extra={"schema": settings.METADATA_DB_SCHEMA},
        )
    return _metadata_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _metadata_session_factory
    if _metadata_session_factory is None:
        raise DatabaseConnectionError(
            detail="Metadata session factory has not been initialized. Call init_metadata_db() first.",
            extra={"schema": settings.METADATA_DB_SCHEMA},
        )
    return _metadata_session_factory


async def init_metadata_db(*, verify_connection: bool = True) -> AsyncEngine:
    global _metadata_engine, _metadata_session_factory, _metadata_scoped_session
    if _metadata_engine is not None:
        logger.debug("Metadata database engine already initialized, skipping creation")
        return _metadata_engine
    try:
        engine = _build_metadata_engine()
        if verify_connection:
            redacted_url = mask_connection_string(settings.METADATA_DB_URL)
            logger.debug(
                "Verifying metadata database connectivity",
                extra={"database_url": redacted_url, "schema": settings.METADATA_DB_SCHEMA},
            )
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.commit()
                try:
                    await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {settings.METADATA_DB_SCHEMA}"))
                    await conn.commit()
                except SQLAlchemyError as schema_exc:
                    logger.warning(
                        "Could not ensure metadata schema existence; continuing anyway",
                        extra={"schema": settings.METADATA_DB_SCHEMA},
                        exc_info=schema_exc,
                    )
        session_factory = _build_session_factory(engine)
        scoped_session = _build_scoped_session(session_factory)
        _metadata_engine = engine
        _metadata_session_factory = session_factory
        _metadata_scoped_session = scoped_session
        redacted_url = mask_connection_string(settings.METADATA_DB_URL)
        logger.info(
            "Metadata database initialized successfully",
            extra={
                "database_url": redacted_url,
                "schema": settings.METADATA_DB_SCHEMA,
            },
        )
        return engine
    except SQLAlchemyError as exc:
        redacted_url = mask_connection_string(settings.METADATA_DB_URL)
        logger.error(
            "Metadata database initialization failed",
            extra={
                "database_url": redacted_url,
                "schema": settings.METADATA_DB_SCHEMA,
            },
            exc_info=exc,
        )
        raise DatabaseConnectionError(
            detail=f"Failed to initialize metadata database: {exc}",
            extra={
                "schema": settings.METADATA_DB_SCHEMA,
                "database_url": redacted_url,
            },
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected error during metadata database initialization",
            exc_info=exc,
        )
        raise


async def dispose_metadata_db() -> None:
    global _metadata_engine, _metadata_session_factory, _metadata_scoped_session
    if _metadata_scoped_session is not None:
        try:
            await _metadata_scoped_session.remove()
        except Exception as exc:
            logger.warning("Error removing scoped metadata sessions", exc_info=exc)
        _metadata_scoped_session = None
    if _metadata_engine is not None:
        try:
            logger.info("Disposing metadata database engine")
            await _metadata_engine.dispose()
            logger.info("Metadata database engine disposed")
        except Exception as exc:
            logger.warning("Error while disposing metadata database engine", exc_info=exc)
    _metadata_engine = None
    _metadata_session_factory = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_async_session_factory()
    session: Optional[AsyncSession] = None
    try:
        async with factory() as session:
            yield session
    except SQLAlchemyError as exc:
        logger.error(
            "Metadata database session error",
            extra={"schema": settings.METADATA_DB_SCHEMA},
            exc_info=exc,
        )
        if session is not None:
            try:
                await session.rollback()
            except Exception:
                pass
        raise DatabaseConnectionError(
            detail=f"Metadata database session error: {exc}",
            extra={"schema": settings.METADATA_DB_SCHEMA},
        ) from exc
    finally:
        if session is not None:
            try:
                if session.is_active:
                    await session.close()
            except Exception as exc:
                logger.warning("Error closing metadata session", exc_info=exc)


DbSessionDep = Depends(get_db)
