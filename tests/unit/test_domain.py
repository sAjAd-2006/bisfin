"""Unit tests for immutable and persistence-independent domain DTOs."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from bisfin.domain import (
    BarRevision,
    Instrument,
    InstrumentIdentifier,
    InstrumentSpecification,
    PointInTimeBar,
    RawEvent,
    RawEventValidationStatus,
    ReplayMode,
    ResolvedInstrument,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2025, 1, 2, 12, 30, tzinfo=UTC)


def _bar_values() -> dict[str, object]:
    return {
        "bar_open_ts": datetime(2025, 1, 2, 10, 0, tzinfo=UTC),
        "bar_series_id": 11,
        "revision_no": 2,
        "available_at": datetime(2025, 1, 2, 11, 1, tzinfo=UTC),
        "system_available_at": datetime(2025, 1, 2, 11, 2, tzinfo=UTC),
        "bar_close_ts": datetime(2025, 1, 2, 11, 0, tzinfo=UTC),
        "trading_date": date(2025, 1, 2),
        "open_price": Decimal("123.450000000000000001"),
        "high_price": Decimal("129.000000000000000001"),
        "low_price": Decimal("120.000000000000000001"),
        "close_price": Decimal("125.000000000000000001"),
        "official_close_price": Decimal("124.999999999999999999"),
        "settlement_price": None,
        "volume": Decimal("10000000000000000000.000000000000000001"),
        "quote_volume": Decimal("1250000000000000000000.000000000000000001"),
        "trade_count": 410,
        "vwap": Decimal("124.720000000000000001"),
        "open_interest": None,
        "is_final": True,
        "quality_flags": 0,
        "ingestion_batch_id": 19,
        "recorded_at": datetime(2025, 1, 2, 11, 2, tzinfo=UTC),
        "previous_close_price": Decimal("121.000000000000000001"),
    }


def _instrument() -> Instrument:
    return Instrument(
        instrument_id=7,
        asset_type_code="EQUITY",
        venue_id=1,
        quote_currency_code="IRR",
        canonical_symbol="TEST",
        display_name="Test instrument",
        status="ACTIVE",
        active_from=_NOW,
        metadata={"source": "fixture"},
        created_at=_NOW,
    )


def test_bar_prices_and_quantities_preserve_decimal_precision() -> None:
    values = _bar_values()

    bar = BarRevision.model_validate(values)

    assert isinstance(bar.open_price, Decimal)
    assert bar.open_price == values["open_price"]
    assert isinstance(bar.volume, Decimal)
    assert bar.volume == values["volume"]
    assert not any(field.annotation is float for field in BarRevision.model_fields.values())


@pytest.mark.parametrize(
    "field",
    [
        "bar_open_ts",
        "available_at",
        "system_available_at",
        "bar_close_ts",
        "recorded_at",
    ],
)
def test_naive_bar_timestamps_are_rejected(field: str) -> None:
    values = _bar_values()
    values[field] = datetime(2025, 1, 2, 10, 0)

    with pytest.raises(ValidationError, match="timezone-aware"):
        BarRevision.model_validate(values)


def test_optional_catalog_timestamps_also_reject_naive_values() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Instrument(
            instrument_id=7,
            asset_type_code="EQUITY",
            quote_currency_code="IRR",
            canonical_symbol="TEST",
            display_name="Test instrument",
            status="ACTIVE",
            active_from=datetime(2025, 1, 2),
            created_at=_NOW,
        )


def test_replay_mode_exposes_exact_database_values() -> None:
    assert [mode.value for mode in ReplayMode] == [
        "PUBLIC_REPLAY",
        "ACTUAL_SYSTEM_REPLAY",
    ]

    with pytest.raises(ValidationError):
        TypeAdapter(ReplayMode).validate_python("public_replay")


def test_point_in_time_bar_requires_effective_availability() -> None:
    values = _bar_values()
    values["effective_available_at"] = datetime(2025, 1, 2, 11, 1, tzinfo=UTC)

    bar = PointInTimeBar.model_validate(values)

    assert bar.effective_available_at == values["effective_available_at"]
    assert isinstance(bar.close_price, Decimal)


def test_dtos_are_frozen() -> None:
    instrument = _instrument()

    with pytest.raises(ValidationError, match="frozen"):
        setattr(instrument, "canonical_symbol", "CHANGED")


def test_identifier_remains_a_string_and_resolution_retains_exact_interval() -> None:
    identifier = InstrumentIdentifier(
        provider_id=2,
        identifier_type="PROVIDER_CODE",
        identifier_value="00001234",
        valid_from=_NOW,
        instrument_id=7,
        is_primary=True,
    )
    resolved = ResolvedInstrument(instrument=_instrument(), identifier=identifier)

    assert resolved.identifier.identifier_value == "00001234"
    assert isinstance(resolved.identifier.identifier_value, str)
    assert resolved.identifier.valid_from == _NOW


def test_instrument_specification_uses_decimal_and_aware_effective_time() -> None:
    specification = InstrumentSpecification(
        instrument_id=7,
        effective_from=_NOW,
        price_tick=Decimal("0.000000000000000001"),
        quantity_step=Decimal("1.000000000000000000"),
        contract_multiplier=Decimal("1000.000000000000000000"),
    )

    assert specification.price_tick == Decimal("0.000000000000000001")
    assert specification.contract_multiplier == Decimal("1000.000000000000000000")


def test_raw_event_serialization_preserves_external_text_without_implicit_secrets() -> None:
    event = RawEvent(
        ingested_at=_NOW,
        raw_event_id=9,
        ingestion_batch_id=4,
        feed_id=2,
        source_record_key="000001",
        source_event_time_text="1403/10/13 12:30:00",
        source_date_text="1403/10/13",
        payload_sha256="a" * 64,
        raw_payload={"instrumentCode": "000001"},
        validation_status=RawEventValidationStatus.PENDING,
    )

    serialized = event.model_dump_json()
    assert "000001" in serialized
    assert "password" not in serialized.lower()
    assert "authorization" not in serialized.lower()
