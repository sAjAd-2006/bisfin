"""Unit contracts for partition, series, and append-only bar writes."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

from sqlalchemy.engine import Connection

from bisfin.domain.market_data import (
    BarRevisionCandidate,
    BarRevisionWriteStatus,
)
from bisfin.repositories.bar_writer_repository import SqlAlchemyBarWriterRepository


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def _series_row() -> dict[str, object]:
    return {
        "bar_series_id": 22,
        "feed_id": 8,
        "instrument_id": 9,
        "timeframe_id": 6,
        "price_basis": "RAW",
        "adjustment_set_id": None,
        "close_semantics": "LAST_TRADE",
        "session_code": "REGULAR",
        "metadata": {},
        "created_at": datetime(2026, 7, 1, tzinfo=UTC),
    }


def _candidate(*, close_price: str = "101") -> BarRevisionCandidate:
    return BarRevisionCandidate(
        bar_open_ts=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
        bar_series_id=22,
        available_at=datetime(2026, 7, 2, 10, tzinfo=UTC),
        system_available_at=datetime(2026, 7, 2, 10, 0, 1, tzinfo=UTC),
        bar_close_ts=datetime(2026, 7, 1, 9, tzinfo=UTC),
        trading_date=date(2026, 7, 1),
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal(close_price),
        volume=Decimal("1000"),
        ingestion_batch_id=31,
    )


def _revision_row(*, revision_no: int = 1, close_price: str = "101") -> dict[str, object]:
    candidate = _candidate(close_price=close_price)
    return {
        **candidate.model_dump(),
        "revision_no": revision_no,
        "recorded_at": datetime(2026, 7, 2, 10, 0, 2, tzinfo=UTC),
    }


def test_partition_months_are_unique_and_sorted_before_locking() -> None:
    connection, mock = _connection()

    SqlAlchemyBarWriterRepository(connection).ensure_month_partitions(
        (date(2026, 8, 31), date(2026, 7, 2), date(2026, 8, 1))
    )

    months = [call.args[1]["month"] for call in mock.execute.call_args_list]
    assert months == [
        date(2026, 7, 1),
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 8, 1),
    ]
    assert "pg_advisory_xact_lock" in str(mock.execute.call_args_list[0].args[0])
    assert "create_bar_month_partition" in str(mock.execute.call_args_list[1].args[0])


def test_daily_series_creation_uses_exact_raw_identity_without_commit() -> None:
    connection, mock = _connection()
    timeframe_result = MagicMock()
    timeframe_result.scalar_one_or_none.return_value = 6
    insert_result = MagicMock()
    insert_result.mappings.return_value.one_or_none.return_value = _series_row()
    mock.execute.side_effect = (timeframe_result, insert_result)

    series = SqlAlchemyBarWriterRepository(connection).get_or_create_daily_raw_series(
        feed_id=8,
        instrument_id=9,
        timeframe_id=6,
    )

    assert series.price_basis == "RAW"
    assert series.adjustment_set_id is None
    assert series.close_semantics == "LAST_TRADE"
    assert series.session_code == "REGULAR"
    insert_statement = mock.execute.call_args_list[1].args[0]
    assert "ON CONFLICT DO NOTHING" in str(insert_statement)
    mock.commit.assert_not_called()


def test_audit_timestamp_changes_do_not_create_a_revision() -> None:
    connection, mock = _connection()
    lock_result = MagicMock()
    latest_result = MagicMock()
    existing = _revision_row()
    existing["available_at"] = datetime(2026, 7, 1, 10, tzinfo=UTC)
    existing["system_available_at"] = datetime(2026, 7, 1, 10, 0, 1, tzinfo=UTC)
    existing["ingestion_batch_id"] = 1
    latest_result.mappings.return_value.one_or_none.return_value = existing
    mock.execute.side_effect = (lock_result, latest_result)

    result = SqlAlchemyBarWriterRepository(connection).append_revision_if_changed(_candidate())

    assert result.status is BarRevisionWriteStatus.UNCHANGED
    assert result.revision.revision_no == 1
    assert mock.execute.call_count == 2


def test_financial_change_appends_next_revision() -> None:
    connection, mock = _connection()
    lock_result = MagicMock()
    latest_result = MagicMock()
    latest_result.mappings.return_value.one_or_none.return_value = _revision_row()
    insert_result = MagicMock()
    insert_result.mappings.return_value.one.return_value = _revision_row(
        revision_no=2,
        close_price="101.5",
    )
    mock.execute.side_effect = (lock_result, latest_result, insert_result)

    result = SqlAlchemyBarWriterRepository(connection).append_revision_if_changed(
        _candidate(close_price="101.5")
    )

    assert result.status is BarRevisionWriteStatus.CORRECTED
    assert result.revision.revision_no == 2
    assert result.revision.close_price == Decimal("101.5")
    assert "INSERT INTO market.bar_revision" in str(mock.execute.call_args_list[2].args[0])
