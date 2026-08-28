"""Contract tests for the deliberately small Core metadata surface."""

from sqlalchemy import BigInteger, Boolean, Date, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, VARCHAR

from bisfin.db.tables import (
    MAPPED_TABLES,
    asset_type,
    bar_revision,
    currency,
    data_provider,
    metadata,
    venue,
)


def test_only_required_physical_tables_are_mapped() -> None:
    assert {table.fullname for table in MAPPED_TABLES} == {
        "catalog.currency",
        "catalog.asset_type",
        "catalog.data_provider",
        "catalog.data_feed",
        "catalog.venue",
        "catalog.timeframe",
        "catalog.trading_session",
        "catalog.instrument",
        "catalog.instrument_identifier",
        "catalog.instrument_spec_version",
        "ingest.ingestion_batch",
        "ingest.raw_event",
        "market.bar_series",
        "market.bar_revision",
    }
    assert set(metadata.tables) == {table.fullname for table in MAPPED_TABLES}
    assert data_provider.name == "data_provider"
    assert currency.c.currency_code.primary_key is True
    assert asset_type.c.asset_type_code.primary_key is True
    assert venue.c.venue_code.unique is True


def test_bar_revision_columns_preserve_financial_types_and_primary_key() -> None:
    assert list(bar_revision.c.keys()) == [
        "bar_open_ts",
        "bar_series_id",
        "revision_no",
        "available_at",
        "system_available_at",
        "bar_close_ts",
        "trading_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "official_close_price",
        "settlement_price",
        "volume",
        "quote_volume",
        "trade_count",
        "vwap",
        "open_interest",
        "is_final",
        "quality_flags",
        "ingestion_batch_id",
        "recorded_at",
        "previous_close_price",
    ]
    assert {column.name for column in bar_revision.primary_key.columns} == {
        "bar_open_ts",
        "bar_series_id",
        "revision_no",
        "available_at",
    }
    for name in (
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "official_close_price",
        "settlement_price",
        "volume",
        "quote_volume",
        "vwap",
        "open_interest",
        "previous_close_price",
    ):
        column_type = bar_revision.c[name].type
        assert isinstance(column_type, Numeric)
        assert (column_type.precision, column_type.scale) == (38, 18)


def test_representative_core_types_match_postgresql_ddl() -> None:
    assert isinstance(metadata.tables["catalog.instrument"].c.instrument_id.type, BigInteger)
    assert isinstance(metadata.tables["catalog.data_provider"].c.provider_id.type, SmallInteger)
    assert isinstance(metadata.tables["catalog.instrument"].c.canonical_symbol.type, VARCHAR)
    assert isinstance(metadata.tables["catalog.instrument"].c.display_name.type, Text)
    assert isinstance(metadata.tables["catalog.instrument"].c.metadata.type, JSONB)
    assert isinstance(metadata.tables["catalog.instrument"].c.created_at.type, TIMESTAMP)
    assert metadata.tables["catalog.instrument"].c.created_at.type.timezone is True
    assert isinstance(metadata.tables["ingest.raw_event"].c.raw_payload.type, JSONB)
    assert isinstance(metadata.tables["market.bar_revision"].c.trading_date.type, Date)
    assert isinstance(metadata.tables["market.bar_revision"].c.revision_no.type, Integer)
    assert isinstance(metadata.tables["market.bar_revision"].c.is_final.type, Boolean)
