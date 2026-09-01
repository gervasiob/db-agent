from __future__ import annotations

import pytest

from app.core.exceptions import SQLValidationError
from app.models.database import TableMetadata
from app.security.sql_validator import SqlValidator


@pytest.fixture
def validator() -> SqlValidator:
    return SqlValidator()


_ALLOWED_SQL = [
    "SELECT id, name FROM users WHERE status = 'ACTIVE' LIMIT 100",
    "WITH active AS (SELECT id FROM users WHERE status='ACTIVE') SELECT * FROM active",
    "SELECT count(*) FROM orders WHERE created_at >= NOW() - INTERVAL '30 days'",
    "SELECT u.name, sum(o.total) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
    "SELECT DISTINCT country FROM customers",
    "SELECT * FROM (SELECT id FROM t) x",
    "SELECT 1,2,3 UNION SELECT 4,5,6",
]


_BLOCKED_SQL = [
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
    "EXEC sp_help 'users'",
    "COPY users FROM '/tmp/x'",
    "VACUUM users",
    "SET statement_timeout = '1h'",
    "DO $$ BEGIN DELETE FROM users; END $$;",
    "WITH bad AS (DELETE FROM users RETURNING *) SELECT * FROM bad",
    "SELECT pg_sleep(60)",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM unknown_table_xyz",
    "-- DROP TABLE\nSELECT 1",
]


@pytest.mark.parametrize("sql", _ALLOWED_SQL)
def test_sql_validator_allowed(validator: SqlValidator, sql: str) -> None:
    result = validator.validate_read_only(sql, allowed_tables=["users", "orders", "customers", "t"])
    assert result is not None
    assert result.is_readonly is True
    assert result.validated_sql


@pytest.mark.parametrize("sql", _BLOCKED_SQL)
def test_sql_validator_blocked(validator: SqlValidator, sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validator.validate_read_only(sql, allowed_tables=["users", "orders"])


def test_sql_validator_empty(validator: SqlValidator) -> None:
    with pytest.raises(SQLValidationError):
        validator.validate_read_only("   ")


def test_sql_validator_multiple_statements(validator: SqlValidator) -> None:
    with pytest.raises(SQLValidationError):
        validator.validate_read_only("SELECT 1; SELECT 2")


def test_sql_validator_columns_exist(validator: SqlValidator, sample_users_table: TableMetadata) -> None:
    validated = validator.validate_read_only(
        "SELECT id, name FROM users",
        allowed_tables=["users"],
    )
    validator.validate_columns_exist(validated, [sample_users_table])


def test_sql_validator_unknown_column(validator: SqlValidator, sample_users_table: TableMetadata) -> None:
    validated = validator.validate_read_only(
        "SELECT non_existent_col FROM users",
        allowed_tables=["users"],
    )
    with pytest.raises(SQLValidationError):
        validator.validate_columns_exist(validated, [sample_users_table])
