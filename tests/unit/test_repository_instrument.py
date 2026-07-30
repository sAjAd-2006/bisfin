"""Unit coverage for deterministic, time-aware instrument repository queries."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

from bisfin.db.errors import IntegrityViolationError
from bisfin.repositories.instrument_repository import SqlAlchemyInstrumentRepository


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def _instrument_row() -> dict[str, object]:
    return {
        "instrument_id": 41,
        "asset_type_code": "EQUITY",
        "venue_id": None,
        "quote_currency_code": "USD",
        "canonical_symbol": "0007",
        "display_name": "Example",
        "status": "ACTIVE",
        "active_from": None,
        "active_to": None,
        "metadata": {},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def _resolved_row() -> dict[str, object]:
    row = _instrument_row()
    row.update(
        {
            "identifier_provider_id": 3,
            "identifier_identifier_type": "TSETMC_ID",
            "identifier_identifier_value": "0000007",
            "identifier_valid_from": datetime(2025, 1, 1, tzinfo=UTC),
            "identifier_valid_to": None,
            "identifier_instrument_id": 41,
            "identifier_is_primary": True,
            "identifier_metadata": {},
        }
    )
    return row


def _spec_row() -> dict[str, object]:
    return {
        "instrument_id": 41,
        "effective_from": datetime(2025, 1, 1, tzinfo=UTC),
        "effective_to": None,
        "price_tick": Decimal("0.010000000000000000"),
        "quantity_step": Decimal("1.000000000000000000"),
        "lot_size": Decimal("1.000000000000000000"),
        "contract_multiplier": Decimal("1.000000000000000000"),
        "price_scale": 2,
        "quantity_scale": 0,
        "lower_price_limit": None,
        "upper_price_limit": None,
        "shares_outstanding": None,
        "metadata": {},
    }


def test_identifier_lookup_preserves_text_and_half_open_query() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.all.return_value = [_resolved_row()]
    repository = SqlAlchemyInstrumentRepository(connection)

    resolved = repository.find_by_identifier(
        3,
        "TSETMC_ID",
        "0000007",
        datetime(2025, 6, 1, tzinfo=UTC),
    )

    assert resolved is not None
    assert resolved.identifier.identifier_value == "0000007"
    statement = mock.execute.call_args.args[0]
    rendered = str(statement)
    assert "instrument_identifier.valid_from <=" in rendered
    assert "instrument_identifier.valid_to IS NULL" in rendered
    assert "instrument_identifier.valid_to >" in rendered
    assert "LIMIT" in rendered
    mock.commit.assert_not_called()


def test_identifier_lookup_rejects_naive_timestamp_before_query() -> None:
    connection, mock = _connection()
    repository = SqlAlchemyInstrumentRepository(connection)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.find_by_identifier(3, "ISIN", "IR0007", datetime(2025, 1, 1))

    mock.execute.assert_not_called()


def test_identifier_lookup_detects_impossible_multiple_active_rows() -> None:
    connection, mock = _connection()
    row = _resolved_row()
    mock.execute.return_value.mappings.return_value.all.return_value = [row, row]

    with pytest.raises(IntegrityViolationError):
        SqlAlchemyInstrumentRepository(connection).find_by_identifier(
            3,
            "ISIN",
            "IR0007",
            datetime(2025, 1, 1, tzinfo=UTC),
        )


def test_active_spec_preserves_decimal_and_detects_multiple_rows() -> None:
    connection, mock = _connection()
    result = mock.execute.return_value.mappings.return_value.all
    result.return_value = [_spec_row()]
    repository = SqlAlchemyInstrumentRepository(connection)

    specification = repository.get_active_spec(41, datetime(2025, 6, 1, tzinfo=UTC))

    assert specification is not None
    assert specification.price_tick == Decimal("0.010000000000000000")

    result.return_value = [_spec_row(), _spec_row()]
    with pytest.raises(IntegrityViolationError):
        repository.get_active_spec(41, datetime(2025, 6, 1, tzinfo=UTC))
