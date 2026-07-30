"""Minimal SQLAlchemy Core mappings for catalog and market-data access.

These objects are query metadata only.  Schema creation remains exclusively
owned by the checked, raw SQL migrations; this module intentionally exposes no
``create_all`` helper.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, VARCHAR

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


data_provider = Table(
    "data_provider",
    metadata,
    Column(
        "provider_id",
        SmallInteger,
        Identity(always=True),
        primary_key=True,
        nullable=False,
    ),
    Column("provider_code", VARCHAR(64), nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("provider_kind", VARCHAR(24), nullable=False, server_default=text("'VENDOR'")),
    Column("base_url", Text, nullable=True),
    Column("default_timezone", VARCHAR(64), nullable=True),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    schema="catalog",
)

# Domain language normally calls this a provider.  The database's authoritative
# physical name is catalog.data_provider, so the alias does not add metadata.
provider = data_provider


instrument = Table(
    "instrument",
    metadata,
    Column(
        "instrument_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "asset_type_code",
        VARCHAR(32),
        ForeignKey("catalog.asset_type.asset_type_code"),
        nullable=False,
    ),
    Column("venue_id", SmallInteger, ForeignKey("catalog.venue.venue_id"), nullable=True),
    Column(
        "quote_currency_code",
        VARCHAR(12),
        ForeignKey("catalog.currency.currency_code"),
        nullable=False,
    ),
    Column("canonical_symbol", VARCHAR(128), nullable=False),
    Column("display_name", Text, nullable=False),
    Column("status", VARCHAR(16), nullable=False, server_default=text("'ACTIVE'")),
    Column("active_from", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("active_to", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    schema="catalog",
)


instrument_identifier = Table(
    "instrument_identifier",
    metadata,
    Column(
        "provider_id",
        SmallInteger,
        ForeignKey("catalog.data_provider.provider_id"),
        primary_key=True,
        nullable=False,
    ),
    Column("identifier_type", VARCHAR(32), primary_key=True, nullable=False),
    Column("identifier_value", Text, primary_key=True, nullable=False),
    Column(
        "valid_from",
        TIMESTAMP(timezone=True, precision=6),
        primary_key=True,
        nullable=False,
        server_default=text("'-infinity'"),
    ),
    Column("valid_to", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column(
        "instrument_id",
        BigInteger,
        ForeignKey("catalog.instrument.instrument_id"),
        nullable=False,
    ),
    Column("is_primary", Boolean, nullable=False, server_default=text("false")),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    schema="catalog",
)


instrument_spec_version = Table(
    "instrument_spec_version",
    metadata,
    Column(
        "instrument_id",
        BigInteger,
        ForeignKey("catalog.instrument.instrument_id"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "effective_from",
        TIMESTAMP(timezone=True, precision=6),
        primary_key=True,
        nullable=False,
    ),
    Column("effective_to", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("price_tick", Numeric(38, 18), nullable=True),
    Column("quantity_step", Numeric(38, 18), nullable=True),
    Column("lot_size", Numeric(38, 18), nullable=True),
    Column("contract_multiplier", Numeric(38, 18), nullable=False, server_default=text("1")),
    Column("price_scale", SmallInteger, nullable=True),
    Column("quantity_scale", SmallInteger, nullable=True),
    Column("lower_price_limit", Numeric(38, 18), nullable=True),
    Column("upper_price_limit", Numeric(38, 18), nullable=True),
    Column("shares_outstanding", Numeric(38, 6), nullable=True),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    schema="catalog",
)


ingestion_batch = Table(
    "ingestion_batch",
    metadata,
    Column(
        "ingestion_batch_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "feed_id",
        BigInteger,
        ForeignKey("catalog.data_feed.feed_id"),
        nullable=False,
    ),
    Column("request_id", Text, nullable=True),
    Column("requested_event_from", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("requested_event_to", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column(
        "started_at",
        TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("status", VARCHAR(16), nullable=False, server_default=text("'RUNNING'")),
    Column("received_row_count", BigInteger, nullable=False, server_default=text("0")),
    Column("accepted_row_count", BigInteger, nullable=False, server_default=text("0")),
    Column("rejected_row_count", BigInteger, nullable=False, server_default=text("0")),
    Column("payload_sha256", CHAR(64), nullable=True),
    Column("parser_version", Text, nullable=False),
    Column("source_watermark", Text, nullable=True),
    Column("error_summary", Text, nullable=True),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    UniqueConstraint("feed_id", "request_id"),
    schema="ingest",
)


raw_event = Table(
    "raw_event",
    metadata,
    Column(
        "ingested_at",
        TIMESTAMP(timezone=True, precision=6),
        primary_key=True,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "raw_event_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "ingestion_batch_id",
        BigInteger,
        ForeignKey("ingest.ingestion_batch.ingestion_batch_id"),
        nullable=False,
    ),
    Column(
        "feed_id",
        BigInteger,
        ForeignKey("catalog.data_feed.feed_id"),
        nullable=False,
    ),
    Column("source_record_key", Text, nullable=True),
    Column("source_event_time_text", Text, nullable=True),
    Column("source_date_text", Text, nullable=True),
    Column("source_sequence", BigInteger, nullable=True),
    Column("observed_at", TIMESTAMP(timezone=True, precision=6), nullable=True),
    Column("payload_sha256", CHAR(64), nullable=False),
    Column("raw_payload", JSONB, nullable=False),
    Column("validation_status", VARCHAR(16), nullable=False, server_default=text("'PENDING'")),
    Column("validation_errors", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    schema="ingest",
)


bar_series = Table(
    "bar_series",
    metadata,
    Column(
        "bar_series_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "feed_id",
        BigInteger,
        ForeignKey("catalog.data_feed.feed_id"),
        nullable=False,
    ),
    Column(
        "instrument_id",
        BigInteger,
        ForeignKey("catalog.instrument.instrument_id"),
        nullable=False,
    ),
    Column(
        "timeframe_id",
        SmallInteger,
        ForeignKey("catalog.timeframe.timeframe_id"),
        nullable=False,
    ),
    Column("price_basis", VARCHAR(24), nullable=False, server_default=text("'RAW'")),
    Column(
        "adjustment_set_id",
        BigInteger,
        ForeignKey("catalog.adjustment_set.adjustment_set_id"),
        nullable=True,
    ),
    Column(
        "close_semantics",
        VARCHAR(24),
        nullable=False,
        server_default=text("'LAST_TRADE'"),
    ),
    Column("session_code", VARCHAR(24), nullable=False, server_default=text("'REGULAR'")),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("bar_series_id", "instrument_id", "timeframe_id"),
    ForeignKeyConstraint(
        ["adjustment_set_id", "instrument_id"],
        [
            "catalog.adjustment_set.adjustment_set_id",
            "catalog.adjustment_set.instrument_id",
        ],
    ),
    schema="market",
)


bar_revision = Table(
    "bar_revision",
    metadata,
    Column(
        "bar_open_ts",
        TIMESTAMP(timezone=True, precision=6),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "bar_series_id",
        BigInteger,
        ForeignKey("market.bar_series.bar_series_id"),
        primary_key=True,
        nullable=False,
    ),
    Column("revision_no", Integer, primary_key=True, nullable=False),
    Column(
        "available_at",
        TIMESTAMP(timezone=True, precision=6),
        primary_key=True,
        nullable=False,
    ),
    Column("system_available_at", TIMESTAMP(timezone=True, precision=6), nullable=False),
    Column("bar_close_ts", TIMESTAMP(timezone=True, precision=6), nullable=False),
    Column("trading_date", Date, nullable=False),
    Column("open_price", Numeric(38, 18), nullable=False),
    Column("high_price", Numeric(38, 18), nullable=False),
    Column("low_price", Numeric(38, 18), nullable=False),
    Column("close_price", Numeric(38, 18), nullable=False),
    Column("official_close_price", Numeric(38, 18), nullable=True),
    Column("settlement_price", Numeric(38, 18), nullable=True),
    Column("volume", Numeric(38, 18), nullable=True),
    Column("quote_volume", Numeric(38, 18), nullable=True),
    Column("trade_count", BigInteger, nullable=True),
    Column("vwap", Numeric(38, 18), nullable=True),
    Column("open_interest", Numeric(38, 18), nullable=True),
    Column("is_final", Boolean, nullable=False, server_default=text("true")),
    Column("quality_flags", Integer, nullable=False, server_default=text("0")),
    Column(
        "ingestion_batch_id",
        BigInteger,
        ForeignKey("ingest.ingestion_batch.ingestion_batch_id"),
        nullable=False,
    ),
    Column(
        "recorded_at",
        TIMESTAMP(timezone=True, precision=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("previous_close_price", Numeric(38, 18), nullable=True),
    UniqueConstraint("bar_open_ts", "bar_series_id", "revision_no"),
    schema="market",
)


MAPPED_TABLES = (
    data_provider,
    instrument,
    instrument_identifier,
    instrument_spec_version,
    ingestion_batch,
    raw_event,
    bar_series,
    bar_revision,
)

__all__ = [
    "MAPPED_TABLES",
    "NAMING_CONVENTION",
    "bar_revision",
    "bar_series",
    "data_provider",
    "ingestion_batch",
    "instrument",
    "instrument_identifier",
    "instrument_spec_version",
    "metadata",
    "provider",
    "raw_event",
]
