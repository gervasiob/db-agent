import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.schema import MetaData

from app.core.config import settings
from app.models.orm import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata: MetaData = Base.metadata

SYSTEM_TABLE_PREFIXES = (
    "pg_",
    "sql_",
    "information_schema",
    "pg_catalog",
)


def _exclude_system_tables(name: str, type_: str, parent_names: dict | None = None) -> bool:
    if type_ == "table":
        if any(name.startswith(prefix) for prefix in SYSTEM_TABLE_PREFIXES):
            return False
        schema = parent_names.get("schema_name") if parent_names else None
        if schema in ("information_schema", "pg_catalog"):
            return False
    return True


def run_migrations_offline() -> None:
    url = settings.METADATA_DB_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        render_as_batch=False,
        include_name=_exclude_system_tables,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: AsyncConnection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        render_as_batch=False,
        include_name=_exclude_system_tables,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable: AsyncEngine = create_async_engine(
        settings.METADATA_DB_URL,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
