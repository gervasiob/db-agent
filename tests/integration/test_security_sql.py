from __future__ import annotations

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    import sqlalchemy
    _TC_AVAILABLE = True
except Exception:  # pragma: no cover - optional dep
    _TC_AVAILABLE = False


from app.security.sql_validator import SqlValidator
from app.core.exceptions import SQLValidationError


pytestmark = pytest.mark.skipif(
    not _TC_AVAILABLE,
    reason="testcontainers[postgresql] is not installed",
)


_ALLOWED_PATTERNS = [
    "SELECT 1",
    "SELECT id, name FROM users WHERE status = 'ACTIVE' LIMIT 100",
    "SELECT count(*) FROM orders WHERE created_at >= NOW() - INTERVAL '30 days'",
    "WITH active AS (SELECT id FROM users WHERE status='ACTIVE') SELECT * FROM active",
]


_BLOCKED_PATTERNS = [
    "SELECT * FROM users; DROP TABLE users",
    "INSERT INTO users (name) VALUES ('x')",
    "UPDATE users SET status='ACTIVE' WHERE id=1",
    "DELETE FROM users WHERE id=1",
    "DROP TABLE users",
    "ALTER TABLE users ADD COLUMN x INT",
    "CREATE TABLE x (id int)",
    "TRUNCATE TABLE users",
    "GRANT SELECT ON users TO public",
    "CALL my_proc()",
    "COPY users FROM '/tmp/x'",
    "VACUUM users",
    "SET statement_timeout = '1h'",
    "SELECT pg_sleep(60)",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.tables",
]


@pytest.fixture(scope="module")
def postgres_container():
    with PostgresContainer("postgres:16") as container:
        yield container


@pytest.fixture
def validator() -> SqlValidator:
    return SqlValidator()


@pytest.mark.parametrize("sql", _ALLOWED_PATTERNS)
def test_allowed_sql_validator(validator: SqlValidator, sql: str) -> None:
    result = validator.validate_read_only(
        sql,
        allowed_tables=["users", "orders"],
        allowed_schemas=["public"],
    )
    assert result.is_readonly is True


@pytest.mark.parametrize("sql", _BLOCKED_PATTERNS)
def test_blocked_sql_validator(validator: SqlValidator, sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validator.validate_read_only(sql)


@pytest.mark.parametrize("sql", _ALLOWED_PATTERNS)
def test_allowed_sql_executes_in_container(
    postgres_container: PostgresContainer,
    sql: str,
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = postgres_container.get_connection_url()
    async_url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    engine = create_async_engine(async_url)
    try:
        import asyncio

        async def _run():
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT, status TEXT)"))
                await conn.execute(text("CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, user_id INT, created_at TIMESTAMPTZ DEFAULT NOW())"))
                await conn.commit()
            async with engine.connect() as conn:
                await conn.execute(text(sql))

        asyncio.get_event_loop().run_until_complete(_run())
    finally:
        import asyncio
        asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users; DROP TABLE users",
        "INSERT INTO users (name) VALUES ('hijack')",
        "UPDATE users SET status='ACTIVE' WHERE id=1",
        "DELETE FROM users WHERE id=1",
        "DROP TABLE IF EXISTS users_critical",
    ],
)
def test_blocked_sql_on_container_readonly_role(
    postgres_container: PostgresContainer,
    sql: str,
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import create_async_engine

    url = postgres_container.get_connection_url()
    async_url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    engine = create_async_engine(async_url)
    import asyncio

    async def _setup_and_test():
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT, status TEXT)"))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS users_critical (id SERIAL PRIMARY KEY)"))
            await conn.execute(text("DROP ROLE IF EXISTS db_agent_ro_test"))
            await conn.execute(text("CREATE ROLE db_agent_ro_test NOINHERIT LOGIN PASSWORD 'readonly_123'"))
            await conn.execute(text("GRANT CONNECT ON DATABASE test TO db_agent_ro_test"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO db_agent_ro_test"))
            await conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_agent_ro_test"))
            await conn.commit()
        from sqlalchemy.engine import make_url
        ro_url = make_url(async_url).set(username="db_agent_ro_test", password="readonly_123")
        ro_engine = create_async_engine(ro_url)
        raised = False
        try:
            async with ro_engine.connect() as conn:
                try:
                    await conn.execute(text(sql))
                    await conn.commit()
                except SQLAlchemyError:
                    raised = True
        except SQLAlchemyError:
            raised = True
        finally:
            await ro_engine.dispose()
        assert raised is True, f"Expected read-only role to block: {sql}"

    try:
        asyncio.get_event_loop().run_until_complete(_setup_and_test())
    finally:
        asyncio.get_event_loop().run_until_complete(engine.dispose())
