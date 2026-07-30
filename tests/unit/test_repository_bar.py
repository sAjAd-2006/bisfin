"""Unit coverage for the single database-authoritative PIT access path."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

from bisfin.domain.market_data import ReplayMode
from bisfin.repositories.bar_repository import SqlAlchemyBarRepository


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def _pit_row(open_minute: int, price: str) -> dict[str, object]:
    open_ts = datetime(2026, 1, 1, 10, open_minute, tzinfo=UTC)
    close_ts = datetime(2026, 1, 1, 10, open_minute + 1, tzinfo=UTC)
    decimal_price = Decimal(price)
    return {
        "bar_open_ts": open_ts,
        "bar_series_id": 7,
        "revision_no": 1,
        "available_at": close_ts,
        "system_available_at": close_ts,
        "bar_close_ts": close_ts,
        "trading_date": date(2026, 1, 1),
        "open_price": decimal_price,
        "high_price": decimal_price,
        "low_price": decimal_price,
        "close_price": decimal_price,
        "official_close_price": None,
        "settlement_price": None,
        "volume": Decimal("10.000000000000000000"),
        "quote_volume": None,
        "trade_count": 1,
        "vwap": decimal_price,
        "open_interest": None,
        "is_final": True,
        "quality_flags": 0,
        "ingestion_batch_id": 5,
        "recorded_at": close_ts,
        "previous_close_price": None,
        "effective_available_at": close_ts,
    }


def test_pit_query_calls_only_audited_function_and_preserves_values() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.all.return_value = [
        _pit_row(0, "101.123456789012345678"),
        _pit_row(1, "102.123456789012345678"),
    ]
    repository = SqlAlchemyBarRepository(connection)

    bars = repository.get_bars_as_of(
        7,
        datetime(2026, 1, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 10, 2, tzinfo=UTC),
        datetime(2026, 1, 1, 11, tzinfo=UTC),
        ReplayMode.PUBLIC_REPLAY,
    )

    assert [bar.bar_open_ts.minute for bar in bars] == [0, 1]
    assert bars[0].close_price == Decimal("101.123456789012345678")
    statement, parameters = mock.execute.call_args.args
    rendered = str(statement).lower()
    assert "from market.bars_as_of(" in rendered
    assert "market.current_bar" not in rendered
    assert "market.bar_revision" not in rendered
    assert parameters["replay_mode"] == "PUBLIC_REPLAY"
    mock.commit.assert_not_called()


@pytest.mark.parametrize("field", ("from_ts", "to_ts", "knowledge_cutoff_ts"))
def test_pit_query_rejects_each_naive_timestamp(field: str) -> None:
    connection, mock = _connection()
    values = {
        "from_ts": datetime(2026, 1, 1, 10, tzinfo=UTC),
        "to_ts": datetime(2026, 1, 1, 11, tzinfo=UTC),
        "knowledge_cutoff_ts": datetime(2026, 1, 1, 12, tzinfo=UTC),
    }
    values[field] = datetime(2026, 1, 1, 10)

    with pytest.raises(ValueError, match="timezone-aware"):
        SqlAlchemyBarRepository(connection).get_bars_as_of(
            7,
            values["from_ts"],
            values["to_ts"],
            values["knowledge_cutoff_ts"],
            ReplayMode.ACTUAL_SYSTEM_REPLAY,
        )

    mock.execute.assert_not_called()
