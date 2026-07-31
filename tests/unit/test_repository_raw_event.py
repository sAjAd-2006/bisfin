"""Unit coverage for Decimal-safe raw-event persistence and composite identity."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

from bisfin.domain.ingestion import RawEventIdentity, RawEventValidationStatus
from bisfin.repositories.raw_event_repository import (
    SqlAlchemyRawEventRepository,
    canonical_json_text,
)


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def _raw_row() -> dict[str, object]:
    timestamp = datetime(2026, 7, 1, 10, tzinfo=UTC)
    return {
        "ingested_at": timestamp,
        "raw_event_id": 19,
        "ingestion_batch_id": 11,
        "feed_id": 5,
        "source_record_key": "brsapi|candlestick|type=2|فملی|1405-04-10",
        "source_event_time_text": "12:30:00",
        "source_date_text": "۱۴۰۵/۰۴/۱۰",
        "source_sequence": 0,
        "observed_at": timestamp,
        "payload_sha256": "a" * 64,
        "validation_status": "PENDING",
        "raw_payload_text": '{"close":100.10,"l18":"فملی"}',
        "validation_errors_text": "[]",
    }


def test_canonical_json_is_sorted_unicode_and_decimal_safe() -> None:
    rendered = canonical_json_text({"symbol": "فملی", "close": Decimal("100.10"), "count": 2})

    assert rendered == '{"close":100.10,"count":2,"symbol":"فملی"}'
    assert canonical_json_text({"b": 2, "a": 1}) == canonical_json_text({"a": 1, "b": 2})
    with pytest.raises(TypeError, match="Decimal"):
        canonical_json_text({"close": 100.1})
    with pytest.raises(ValueError, match="finite"):
        canonical_json_text({"close": Decimal("NaN")})


def test_insert_preserves_decimal_json_and_does_not_commit() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.one.return_value = _raw_row()
    timestamp = datetime(2026, 7, 1, 10, tzinfo=UTC)

    record = SqlAlchemyRawEventRepository(connection).insert_response_record(
        ingested_at=timestamp,
        ingestion_batch_id=11,
        feed_id=5,
        payload_sha256="a" * 64,
        raw_payload={"l18": "فملی", "close": Decimal("100.10")},
        source_record_key="brsapi|candlestick|type=2|فملی|1405-04-10",
        source_date_text="۱۴۰۵/۰۴/۱۰",
        source_sequence=0,
        observed_at=timestamp,
    )

    assert record.raw_payload["close"] == Decimal("100.10")
    assert record.identity == RawEventIdentity(ingested_at=timestamp, raw_event_id=19)
    assert record.validation_status is RawEventValidationStatus.PENDING
    statement = mock.execute.call_args.args[0]
    assert "CAST" in str(statement)
    mock.commit.assert_not_called()


def test_validation_update_targets_both_partition_key_columns() -> None:
    connection, mock = _connection()
    row = _raw_row()
    row["validation_status"] = "REJECTED"
    row["validation_errors_text"] = '[{"code":"INVALID_OHLC"}]'
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = row
    identity = RawEventIdentity(
        ingested_at=datetime(2026, 7, 1, 10, tzinfo=UTC),
        raw_event_id=19,
    )

    result = SqlAlchemyRawEventRepository(connection).update_validation_result(
        identity,
        validation_status=RawEventValidationStatus.REJECTED,
        validation_errors=({"code": "INVALID_OHLC"},),
    )

    rendered = str(mock.execute.call_args.args[0])
    assert "raw_event.ingested_at" in rendered
    assert "raw_event.raw_event_id" in rendered
    assert result.validation_errors == [{"code": "INVALID_OHLC"}]


def test_partition_month_uses_utc_instant() -> None:
    connection, mock = _connection()
    instant = datetime.fromisoformat("2026-08-01T00:30:00+03:30")

    SqlAlchemyRawEventRepository(connection).ensure_month_partition(instant)

    assert mock.execute.call_args.args[1]["month"].isoformat() == "2026-07-01"
