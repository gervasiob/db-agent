from __future__ import annotations

import pytest

from app.models.database import ColumnHeuristicFlags, ColumnMetadata, ColumnType
from app.models.semantic import PIILevel
from app.services.data_sanitization_service import DataSanitizationService


@pytest.fixture
def sanitizer() -> DataSanitizationService:
    return DataSanitizationService(default_pii_level=PIILevel.MEDIUM)


def test_sanitize_email(sanitizer: DataSanitizationService) -> None:
    assert sanitizer.sanitize_value("user@example.com") == "[EMAIL]"
    embedded = sanitizer.sanitize_value("Contact me at foo.bar+baz@sub.domain.co please")
    assert "[EMAIL]" in embedded
    assert "foo.bar+baz@sub.domain.co" not in embedded


def test_sanitize_phone(sanitizer: DataSanitizationService) -> None:
    result = sanitizer.sanitize_value("+54 9 11 1234-5678")
    assert "[PHONE]" in result


def test_sanitize_credit_card(sanitizer: DataSanitizationService) -> None:
    assert sanitizer.sanitize_value("4111 1111 1111 1111") == "[CARD]"
    assert sanitizer.sanitize_value("5500-0000-0000-0004") == "[CARD]"


def test_sanitize_documents(sanitizer: DataSanitizationService) -> None:
    r1 = sanitizer.sanitize_value("DNI 12.345.678")
    assert "[DOCUMENT]" in r1
    r2 = sanitizer.sanitize_value("CUIL 20-12345678-9")
    assert "[DOCUMENT]" in r2


def test_sanitize_password_secret(sanitizer: DataSanitizationService) -> None:
    result = sanitizer.sanitize_value("password=mySuperSecret123")
    assert "[SECRET]" in result
    token_result = sanitizer.sanitize_value("Authorization: Bearer eyJxxx.yyy.zzz")
    assert "[SECRET]" in token_result


def test_sanitize_rows_pii_level(
    sanitizer: DataSanitizationService,
    sample_pii_rows,
) -> None:
    sanitized = sanitizer.sanitize_rows(sample_pii_rows, default_pii=PIILevel.HIGH)
    assert len(sanitized) == len(sample_pii_rows)
    emails_present = any(
        ("@" in str(v)) and ("[EMAIL]" not in str(v))
        for row in sanitized
        for v in row.values()
    )
    assert not emails_present


def test_classify_column_pii_email(sanitizer: DataSanitizationService) -> None:
    col = ColumnMetadata(
        name="email",
        type=ColumnType.VARCHAR,
        raw_type="varchar",
        ordinal_position=1,
        heuristics=ColumnHeuristicFlags(is_email=True),
    )
    assert sanitizer.classify_column_pii(col) == PIILevel.HIGH


def test_classify_column_pii_password(sanitizer: DataSanitizationService) -> None:
    col = ColumnMetadata(
        name="user_password",
        type=ColumnType.VARCHAR,
        raw_type="varchar",
        ordinal_position=1,
    )
    samples = ["abc123", "superSecret", "topSecret!"]
    assert sanitizer.classify_column_pii(col, samples) == PIILevel.REDACTED


def test_classify_column_pii_id(sanitizer: DataSanitizationService) -> None:
    col = ColumnMetadata(
        name="id",
        type=ColumnType.INTEGER,
        raw_type="integer",
        ordinal_position=1,
        is_pk=True,
    )
    assert sanitizer.classify_column_pii(col) == PIILevel.NONE


def test_sanitize_numeric_values_untouched(sanitizer: DataSanitizationService) -> None:
    assert sanitizer.sanitize_value(42) == 42
    assert sanitizer.sanitize_value(3.14) == 3.14
    assert sanitizer.sanitize_value(None) is None


def test_sanitize_low_level_leaves_emails(sanitizer: DataSanitizationService) -> None:
    service = DataSanitizationService(default_pii_level=PIILevel.LOW)
    email = "juan.perez@example.com"
    assert service.sanitize_value(email, PIILevel.LOW) == email


def test_sanitize_redacted_level_blocks_everything(sanitizer: DataSanitizationService) -> None:
    value = "Contact me at user@example.com or 4111 1111 1111 1111"
    result = sanitizer.sanitize_value(value, PIILevel.REDACTED)
    assert "[EMAIL]" in result
    assert "[CARD]" in result
