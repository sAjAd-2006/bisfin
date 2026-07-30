"""SQLSTATE mapping, exception chaining, and credential redaction."""

from __future__ import annotations

import pytest

from bisfin.db.errors import (
    DatabaseError,
    DatabaseUnavailableError,
    EntityNotFoundError,
    IntegrityViolationError,
    InvalidPointInTimeQueryError,
    TemporalOverlapError,
    UnsupportedDatabaseOperationError,
    map_database_error,
    raise_database_error,
    redact_secrets,
)


class _DriverError(Exception):
    def __init__(self, sqlstate: str | None, message: str = "driver failure") -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "expected_type"),
    [
        ("23P01", TemporalOverlapError),
        ("22004", InvalidPointInTimeQueryError),
        ("22023", InvalidPointInTimeQueryError),
        ("P0002", EntityNotFoundError),
        ("0A000", UnsupportedDatabaseOperationError),
        ("23505", IntegrityViolationError),
        ("08006", DatabaseUnavailableError),
        ("XX000", DatabaseError),
    ],
)
def test_sqlstate_mapping(
    sqlstate: str,
    expected_type: type[DatabaseError],
) -> None:
    original = _DriverError(sqlstate)
    mapped = map_database_error(original, operation="read bars")
    assert type(mapped) is expected_type
    assert mapped.sqlstate == sqlstate
    assert mapped.original is original
    assert mapped.operation == "read bars"


def test_raise_helper_preserves_original_exception_chain() -> None:
    original = _DriverError("22023")
    with pytest.raises(InvalidPointInTimeQueryError) as caught:
        raise_database_error(original, operation="market.bars_as_of")
    assert caught.value.__cause__ is original
    assert caught.value.original is original


def test_error_text_and_redaction_never_disclose_credentials() -> None:
    secret = "correct-horse-battery-staple"
    url = f"postgresql+psycopg://worker:{secret}@db.internal:5432/bisfin"
    original = _DriverError("XX000", f"failed at {url} password={secret}")
    mapped = map_database_error(original, operation=f"connect {url} token={secret}")

    rendered = f"{mapped!r} {mapped} {redact_secrets(original)}"
    assert secret not in rendered
    assert "db.internal" not in redact_secrets(url)
    assert redact_secrets(url) == "[REDACTED_DATABASE_URL]"
    assert "password=***" in redact_secrets(original)


def test_redaction_removes_credential_free_urls_and_complete_authorization_values() -> None:
    rendered = redact_secrets(
        "failed postgresql://db.example/bisfin; Authorization: Basic YWxpY2U6c2VjcmV0"
    )

    assert "db.example" not in rendered
    assert "YWxpY2U6c2VjcmV0" not in rendered
    assert "[REDACTED_DATABASE_URL]" in rendered
    assert "Authorization: ***" in rendered


def test_redaction_consumes_url_punctuation_and_multiword_secret_values() -> None:
    rendered = redact_secrets(
        "postgresql://worker:sec)ret@db.example/bisfin password=two words; safe"
    )

    assert "sec)ret" not in rendered
    assert "db.example" not in rendered
    assert "two words" not in rendered
    assert rendered.endswith("; safe")
