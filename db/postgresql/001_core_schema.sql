-- Bisfin trading/backtesting/ML schema
-- Target: PostgreSQL 16+ (validated against PostgreSQL 18)
-- All instants are UTC TIMESTAMPTZ. All time ranges use [from, to).
-- This migration intentionally requires no PostgreSQL extension.

BEGIN;

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS external;
CREATE SCHEMA IF NOT EXISTS backtest;
CREATE SCHEMA IF NOT EXISTS ml;

-- ---------------------------------------------------------------------------
-- Catalog and instrument master
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog.currency (
    currency_code      VARCHAR(12) PRIMARY KEY,
    display_name       TEXT NOT NULL,
    minor_unit         SMALLINT NOT NULL DEFAULT 2 CHECK (minor_unit BETWEEN 0 AND 18),
    is_fiat            BOOLEAN NOT NULL DEFAULT TRUE,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS catalog.asset_type (
    asset_type_code    VARCHAR(32) PRIMARY KEY,
    display_name       TEXT NOT NULL,
    description        TEXT
);

CREATE TABLE IF NOT EXISTS catalog.data_provider (
    provider_id        SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_code      VARCHAR(64) NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    provider_kind      VARCHAR(24) NOT NULL DEFAULT 'VENDOR'
                       CHECK (provider_kind IN ('EXCHANGE','BROKER','VENDOR','INTERNAL','PUBLIC')),
    base_url           TEXT,
    default_timezone   VARCHAR(64),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog.data_feed (
    feed_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id        SMALLINT NOT NULL REFERENCES catalog.data_provider(provider_id),
    feed_code          VARCHAR(96) NOT NULL,
    display_name       TEXT NOT NULL,
    data_kind          VARCHAR(32) NOT NULL
                       CHECK (data_kind IN ('INSTRUMENT','BAR','TICK','ORDER_BOOK','CORPORATE_ACTION',
                                            'FUNDAMENTAL','DOCUMENT','NAV','SENTIMENT','ONCHAIN','OTHER')),
    native_timezone    VARCHAR(64),
    parser_version     TEXT,
    active_from        TIMESTAMPTZ(6),
    active_to          TIMESTAMPTZ(6),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider_id, feed_code),
    CHECK (active_to IS NULL OR active_from IS NULL OR active_to > active_from)
);

CREATE TABLE IF NOT EXISTS catalog.venue (
    venue_id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    venue_code         VARCHAR(32) NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    mic_code           VARCHAR(8),
    country_code       CHAR(2),
    timezone_name      VARCHAR(64) NOT NULL,
    base_currency_code VARCHAR(12) REFERENCES catalog.currency(currency_code),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS catalog.timeframe (
    timeframe_id       SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timeframe_code     VARCHAR(16) NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    duration_seconds   INTEGER CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    calendar_unit      VARCHAR(16) NOT NULL DEFAULT 'FIXED'
                       CHECK (calendar_unit IN ('FIXED','SESSION','DAY','WEEK','MONTH')),
    session_aligned    BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK ((calendar_unit = 'FIXED' AND duration_seconds IS NOT NULL)
        OR (calendar_unit <> 'FIXED'))
);

CREATE TABLE IF NOT EXISTS catalog.trading_session (
    venue_id           SMALLINT NOT NULL REFERENCES catalog.venue(venue_id),
    trading_date       DATE NOT NULL,
    session_code       VARCHAR(24) NOT NULL DEFAULT 'REGULAR',
    is_trading_day     BOOLEAN NOT NULL DEFAULT TRUE,
    session_open_ts    TIMESTAMPTZ(6),
    session_close_ts   TIMESTAMPTZ(6),
    settlement_date    DATE,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (venue_id, trading_date, session_code),
    CHECK ((is_trading_day AND session_open_ts IS NOT NULL AND session_close_ts IS NOT NULL
            AND session_close_ts > session_open_ts)
        OR (NOT is_trading_day))
);

CREATE TABLE IF NOT EXISTS catalog.instrument (
    instrument_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_type_code    VARCHAR(32) NOT NULL REFERENCES catalog.asset_type(asset_type_code),
    venue_id           SMALLINT REFERENCES catalog.venue(venue_id),
    quote_currency_code VARCHAR(12) NOT NULL REFERENCES catalog.currency(currency_code),
    canonical_symbol   VARCHAR(128) NOT NULL,
    display_name       TEXT NOT NULL,
    status             VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('PENDING','ACTIVE','HALTED','DELISTED','EXPIRED','INACTIVE')),
    active_from        TIMESTAMPTZ(6),
    active_to          TIMESTAMPTZ(6),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE NULLS NOT DISTINCT (venue_id, canonical_symbol),
    CHECK (active_to IS NULL OR active_from IS NULL OR active_to > active_from)
);

CREATE TABLE IF NOT EXISTS catalog.instrument_identifier (
    provider_id        SMALLINT NOT NULL REFERENCES catalog.data_provider(provider_id),
    identifier_type    VARCHAR(32) NOT NULL,
    identifier_value   TEXT NOT NULL,
    valid_from         TIMESTAMPTZ(6) NOT NULL DEFAULT '-infinity',
    valid_to           TIMESTAMPTZ(6),
    instrument_id      BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    is_primary         BOOLEAN NOT NULL DEFAULT FALSE,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (provider_id, identifier_type, identifier_value, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX IF NOT EXISTS ix_instrument_identifier_resolve
    ON catalog.instrument_identifier (provider_id, identifier_type, identifier_value, valid_from DESC, valid_to);

CREATE TABLE IF NOT EXISTS catalog.instrument_spec_version (
    instrument_id      BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    effective_from     TIMESTAMPTZ(6) NOT NULL,
    effective_to       TIMESTAMPTZ(6),
    price_tick         NUMERIC(38,18),
    quantity_step      NUMERIC(38,18),
    lot_size           NUMERIC(38,18),
    contract_multiplier NUMERIC(38,18) NOT NULL DEFAULT 1,
    price_scale        SMALLINT,
    quantity_scale     SMALLINT,
    lower_price_limit  NUMERIC(38,18),
    upper_price_limit  NUMERIC(38,18),
    shares_outstanding NUMERIC(38,6),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (instrument_id, effective_from),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (price_tick IS NULL OR price_tick > 0),
    CHECK (quantity_step IS NULL OR quantity_step > 0),
    CHECK (lot_size IS NULL OR lot_size > 0),
    CHECK (contract_multiplier > 0),
    CHECK (upper_price_limit IS NULL OR lower_price_limit IS NULL OR upper_price_limit >= lower_price_limit)
);

CREATE TABLE IF NOT EXISTS catalog.derivative_contract (
    instrument_id      BIGINT PRIMARY KEY REFERENCES catalog.instrument(instrument_id),
    underlying_instrument_id BIGINT REFERENCES catalog.instrument(instrument_id),
    contract_type      VARCHAR(16) NOT NULL CHECK (contract_type IN ('FUTURE','OPTION','SWAP','FORWARD','CFD')),
    option_side        VARCHAR(4) CHECK (option_side IN ('CALL','PUT')),
    strike_price       NUMERIC(38,18),
    contract_size      NUMERIC(38,18) NOT NULL DEFAULT 1 CHECK (contract_size > 0),
    expiry_ts          TIMESTAMPTZ(6),
    settlement_type    VARCHAR(16) CHECK (settlement_type IN ('CASH','PHYSICAL')),
    initial_margin     NUMERIC(38,18),
    maintenance_margin NUMERIC(38,18),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((contract_type = 'OPTION' AND option_side IS NOT NULL AND strike_price IS NOT NULL)
        OR contract_type <> 'OPTION')
);

CREATE TABLE IF NOT EXISTS catalog.corporate_action (
    corporate_action_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id      BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    feed_id            BIGINT REFERENCES catalog.data_feed(feed_id),
    external_id        TEXT,
    action_type        VARCHAR(24) NOT NULL
                       CHECK (action_type IN ('SPLIT','REVERSE_SPLIT','CASH_DIVIDEND','STOCK_DIVIDEND',
                                              'RIGHTS','MERGER','SPINOFF','SYMBOL_CHANGE','DELISTING','OTHER')),
    announced_at       TIMESTAMPTZ(6),
    ex_ts              TIMESTAMPTZ(6) NOT NULL,
    record_date        DATE,
    payable_date       DATE,
    ratio_numerator    NUMERIC(38,18),
    ratio_denominator  NUMERIC(38,18),
    cash_amount        NUMERIC(38,18),
    currency_code      VARCHAR(12) REFERENCES catalog.currency(currency_code),
    available_at       TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    revision_no        INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (feed_id, external_id, revision_no),
    CHECK (system_available_at >= available_at),
    CHECK (ratio_denominator IS NULL OR ratio_denominator <> 0)
);

CREATE INDEX IF NOT EXISTS ix_corporate_action_instrument_time
    ON catalog.corporate_action (instrument_id, ex_ts, available_at DESC, revision_no DESC);

CREATE TABLE IF NOT EXISTS catalog.adjustment_set (
    adjustment_set_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id      BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    method_code        VARCHAR(32) NOT NULL
                       CHECK (method_code IN ('RAW','SPLIT_ADJUSTED','TOTAL_RETURN','PROVIDER_ADJUSTED','CUSTOM')),
    version_no         INTEGER NOT NULL CHECK (version_no > 0),
    knowledge_cutoff_ts TIMESTAMPTZ(6) NOT NULL,
    code_sha256        CHAR(64),
    created_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (instrument_id, method_code, version_no),
    UNIQUE (adjustment_set_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS catalog.adjustment_factor (
    adjustment_set_id  BIGINT NOT NULL REFERENCES catalog.adjustment_set(adjustment_set_id),
    effective_ts       TIMESTAMPTZ(6) NOT NULL,
    price_multiplier   NUMERIC(38,18) NOT NULL DEFAULT 1,
    price_addend       NUMERIC(38,18) NOT NULL DEFAULT 0,
    volume_multiplier  NUMERIC(38,18) NOT NULL DEFAULT 1,
    source_action_id   BIGINT REFERENCES catalog.corporate_action(corporate_action_id),
    PRIMARY KEY (adjustment_set_id, effective_ts),
    CHECK (price_multiplier > 0),
    CHECK (volume_multiplier > 0)
);

CREATE TABLE IF NOT EXISTS catalog.universe (
    universe_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    universe_code      VARCHAR(96) NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    selection_rule     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog.universe_member (
    universe_id        BIGINT NOT NULL REFERENCES catalog.universe(universe_id),
    instrument_id      BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    valid_from         TIMESTAMPTZ(6) NOT NULL,
    valid_to           TIMESTAMPTZ(6),
    weight             DOUBLE PRECISION,
    source_reason      TEXT,
    PRIMARY KEY (universe_id, instrument_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX IF NOT EXISTS ix_universe_member_asof
    ON catalog.universe_member (universe_id, valid_from, valid_to, instrument_id);

CREATE TABLE IF NOT EXISTS catalog.data_snapshot (
    data_snapshot_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_code      VARCHAR(128) NOT NULL UNIQUE,
    knowledge_cutoff_ts TIMESTAMPTZ(6) NOT NULL,
    availability_mode  VARCHAR(24) NOT NULL
                       CHECK (availability_mode IN ('PUBLIC_REPLAY','ACTUAL_SYSTEM_REPLAY')),
    manifest_sha256    CHAR(64),
    status             VARCHAR(16) NOT NULL DEFAULT 'BUILDING'
                       CHECK (status IN ('BUILDING','FROZEN','FAILED','DEPRECATED')),
    created_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    frozen_at          TIMESTAMPTZ(6),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((status = 'FROZEN' AND frozen_at IS NOT NULL AND manifest_sha256 IS NOT NULL)
        OR status <> 'FROZEN')
);

CREATE TABLE IF NOT EXISTS catalog.data_snapshot_component (
    data_snapshot_id   BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    component_key      VARCHAR(160) NOT NULL,
    feed_id            BIGINT REFERENCES catalog.data_feed(feed_id),
    event_from         TIMESTAMPTZ(6),
    event_to           TIMESTAMPTZ(6),
    max_available_at   TIMESTAMPTZ(6),
    max_system_available_at TIMESTAMPTZ(6),
    row_count          BIGINT CHECK (row_count IS NULL OR row_count >= 0),
    component_sha256   CHAR(64) NOT NULL,
    storage_uri        TEXT,
    PRIMARY KEY (data_snapshot_id, component_key),
    CHECK (event_to IS NULL OR event_from IS NULL OR event_to > event_from)
);

COMMENT ON TABLE catalog.instrument IS 'Canonical financial instrument; provider symbols live in instrument_identifier.';
COMMENT ON COLUMN catalog.instrument.instrument_id IS 'Stable internal surrogate key used by all fact tables.';
COMMENT ON COLUMN catalog.instrument.canonical_symbol IS 'Current canonical symbol; historical/provider codes are versioned separately.';
COMMENT ON TABLE catalog.trading_session IS 'Venue session calendar used to distinguish missing bars from market closures.';
COMMENT ON TABLE catalog.corporate_action IS 'Revision-aware corporate actions with public and system availability times.';
COMMENT ON TABLE catalog.data_snapshot IS 'Frozen reproducibility boundary; per-decision point-in-time checks still remain mandatory.';

-- Bootstrap lookups. Production changes should use new numbered migrations.
INSERT INTO catalog.currency (currency_code, display_name, minor_unit, is_fiat) VALUES
    ('IRR','Iranian rial',0,TRUE), ('USD','US dollar',2,TRUE), ('EUR','Euro',2,TRUE),
    ('BTC','Bitcoin',8,FALSE), ('USDT','Tether',6,FALSE)
ON CONFLICT (currency_code) DO NOTHING;

INSERT INTO catalog.asset_type (asset_type_code, display_name) VALUES
    ('EQUITY','Equity'), ('FOREX','Foreign exchange'), ('CRYPTO','Crypto asset'),
    ('ETF','Exchange-traded fund'), ('INDEX','Index'), ('OPTION','Option'),
    ('FUTURE','Future'), ('COMMODITY','Commodity'), ('BOND','Bond'), ('OTHER','Other')
ON CONFLICT (asset_type_code) DO NOTHING;

INSERT INTO catalog.timeframe (timeframe_code, display_name, duration_seconds, calendar_unit, session_aligned) VALUES
    ('1m','1 minute',60,'FIXED',TRUE), ('2m','2 minutes',120,'FIXED',TRUE),
    ('5m','5 minutes',300,'FIXED',TRUE), ('15m','15 minutes',900,'FIXED',TRUE),
    ('1h','1 hour',3600,'FIXED',TRUE), ('1d','1 trading session',NULL,'SESSION',TRUE)
ON CONFLICT (timeframe_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Immutable ingestion/audit zone
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest.ingestion_batch (
    ingestion_batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    request_id          TEXT,
    requested_event_from TIMESTAMPTZ(6),
    requested_event_to  TIMESTAMPTZ(6),
    started_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at         TIMESTAMPTZ(6),
    status              VARCHAR(16) NOT NULL DEFAULT 'RUNNING'
                        CHECK (status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED','QUARANTINED')),
    received_row_count  BIGINT NOT NULL DEFAULT 0 CHECK (received_row_count >= 0),
    accepted_row_count  BIGINT NOT NULL DEFAULT 0 CHECK (accepted_row_count >= 0),
    rejected_row_count  BIGINT NOT NULL DEFAULT 0 CHECK (rejected_row_count >= 0),
    payload_sha256      CHAR(64),
    parser_version      TEXT NOT NULL,
    source_watermark    TEXT,
    error_summary       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (feed_id, request_id),
    CHECK (requested_event_to IS NULL OR requested_event_from IS NULL
           OR requested_event_to > requested_event_from),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (accepted_row_count + rejected_row_count <= received_row_count)
);

CREATE TABLE IF NOT EXISTS ingest.raw_event (
    ingested_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_event_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    source_record_key   TEXT,
    source_event_time_text TEXT,
    source_date_text    TEXT,
    source_sequence     BIGINT,
    observed_at         TIMESTAMPTZ(6),
    payload_sha256      CHAR(64) NOT NULL,
    raw_payload         JSONB NOT NULL,
    validation_status   VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                        CHECK (validation_status IN ('PENDING','ACCEPTED','REJECTED','QUARANTINED')),
    validation_errors   JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (ingested_at, raw_event_id)
) PARTITION BY RANGE (ingested_at);

CREATE INDEX IF NOT EXISTS ix_raw_event_batch
    ON ingest.raw_event (ingestion_batch_id, raw_event_id);
CREATE INDEX IF NOT EXISTS ix_raw_event_source_key
    ON ingest.raw_event (feed_id, source_record_key, ingested_at DESC);
CREATE INDEX IF NOT EXISTS ix_raw_event_ingested_brin
    ON ingest.raw_event USING BRIN (ingested_at) WITH (pages_per_range = 128, autosummarize = on);

COMMENT ON TABLE ingest.ingestion_batch IS 'Auditable unit of source acquisition and canonicalization.';
COMMENT ON COLUMN ingest.ingestion_batch.requested_event_to IS 'Exclusive end of requested source range.';
COMMENT ON TABLE ingest.raw_event IS 'Immutable original provider payload; preserve Jalali/text forms for reprocessing.';
COMMENT ON COLUMN ingest.raw_event.source_event_time_text IS 'Unmodified provider time representation, possibly without timezone.';
COMMENT ON COLUMN ingest.raw_event.validation_errors IS 'Structured parser/schema/data-quality errors; core query fields remain typed elsewhere.';

-- ---------------------------------------------------------------------------
-- Canonical market data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS market.bar_series (
    bar_series_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    price_basis         VARCHAR(24) NOT NULL DEFAULT 'RAW'
                        CHECK (price_basis IN ('RAW','SPLIT_ADJUSTED','TOTAL_RETURN','PROVIDER_ADJUSTED','CUSTOM')),
    adjustment_set_id   BIGINT REFERENCES catalog.adjustment_set(adjustment_set_id),
    close_semantics     VARCHAR(24) NOT NULL DEFAULT 'LAST_TRADE'
                        CHECK (close_semantics IN ('LAST_TRADE','OFFICIAL_CLOSE','SETTLEMENT','MID','NAV')),
    session_code        VARCHAR(24) NOT NULL DEFAULT 'REGULAR',
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bar_series_id, instrument_id, timeframe_id),
    FOREIGN KEY (adjustment_set_id, instrument_id)
        REFERENCES catalog.adjustment_set(adjustment_set_id, instrument_id),
    CHECK ((price_basis = 'RAW' AND adjustment_set_id IS NULL)
        OR (price_basis <> 'RAW' AND adjustment_set_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bar_series_identity
    ON market.bar_series (feed_id, instrument_id, timeframe_id, price_basis,
                          COALESCE(adjustment_set_id, 0), close_semantics, session_code);

CREATE TABLE IF NOT EXISTS market.bar_revision (
    bar_open_ts         TIMESTAMPTZ(6) NOT NULL,
    bar_series_id       BIGINT NOT NULL REFERENCES market.bar_series(bar_series_id),
    revision_no         INTEGER NOT NULL CHECK (revision_no > 0),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    bar_close_ts        TIMESTAMPTZ(6) NOT NULL,
    trading_date        DATE NOT NULL,
    open_price          NUMERIC(38,18) NOT NULL,
    high_price          NUMERIC(38,18) NOT NULL,
    low_price           NUMERIC(38,18) NOT NULL,
    close_price         NUMERIC(38,18) NOT NULL,
    official_close_price NUMERIC(38,18),
    settlement_price    NUMERIC(38,18),
    volume              NUMERIC(38,18),
    quote_volume        NUMERIC(38,18),
    trade_count         BIGINT,
    vwap                NUMERIC(38,18),
    open_interest       NUMERIC(38,18),
    is_final            BOOLEAN NOT NULL DEFAULT TRUE,
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    recorded_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bar_open_ts, bar_series_id, revision_no, available_at),
    UNIQUE (bar_open_ts, bar_series_id, revision_no),
    CHECK (bar_close_ts > bar_open_ts),
    CHECK (system_available_at >= available_at),
    CHECK (high_price >= low_price),
    CHECK (open_price BETWEEN low_price AND high_price),
    CHECK (close_price BETWEEN low_price AND high_price),
    CHECK (volume IS NULL OR volume >= 0),
    CHECK (quote_volume IS NULL OR quote_volume >= 0),
    CHECK (trade_count IS NULL OR trade_count >= 0),
    CHECK (open_interest IS NULL OR open_interest >= 0),
    CHECK (NOT is_final OR available_at >= bar_close_ts)
) PARTITION BY RANGE (bar_open_ts);

CREATE INDEX IF NOT EXISTS ix_bar_revision_range_pit
    ON market.bar_revision (bar_series_id, bar_open_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_bar_revision_system_pit
    ON market.bar_revision (bar_series_id, bar_open_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_bar_revision_time_brin
    ON market.bar_revision USING BRIN (bar_open_ts) WITH (pages_per_range = 64, autosummarize = on);

CREATE TABLE IF NOT EXISTS market.trade_tick (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    source_sequence     BIGINT NOT NULL,
    event_no            SMALLINT NOT NULL DEFAULT 0,
    revision_no         INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    event_ts_ns         BIGINT,
    source_trade_id     TEXT,
    trade_state         VARCHAR(12) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (trade_state IN ('ACTIVE','CANCELED','CORRECTED')),
    price               NUMERIC(38,18),
    quantity            NUMERIC(38,18),
    aggressor_side      CHAR(1) CHECK (aggressor_side IN ('B','S','U')),
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    recorded_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_ts, feed_id, instrument_id, source_sequence, event_no, revision_no),
    CHECK (system_available_at >= available_at),
    CHECK (quantity IS NULL OR quantity >= 0),
    CHECK ((trade_state = 'CANCELED') OR (price IS NOT NULL AND quantity IS NOT NULL))
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_trade_tick_replay
    ON market.trade_tick (feed_id, instrument_id, event_ts, source_sequence, event_no, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_trade_tick_time_brin
    ON market.trade_tick USING BRIN (event_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE TABLE IF NOT EXISTS market.order_book_snapshot (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    snapshot_id         BIGINT GENERATED ALWAYS AS IDENTITY,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    source_sequence     BIGINT NOT NULL,
    revision_no         INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    is_complete         BOOLEAN NOT NULL DEFAULT TRUE,
    depth               SMALLINT CHECK (depth IS NULL OR depth > 0),
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_ts, snapshot_id),
    UNIQUE (event_ts, feed_id, instrument_id, source_sequence, revision_no),
    CHECK (system_available_at >= available_at)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_order_book_snapshot_lookup
    ON market.order_book_snapshot (feed_id, instrument_id, event_ts DESC, source_sequence DESC);

CREATE TABLE IF NOT EXISTS market.order_book_level (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    snapshot_id         BIGINT NOT NULL,
    side                CHAR(1) NOT NULL CHECK (side IN ('B','A')),
    level_no            SMALLINT NOT NULL CHECK (level_no > 0),
    price               NUMERIC(38,18) NOT NULL,
    quantity            NUMERIC(38,18) NOT NULL CHECK (quantity >= 0),
    order_count         INTEGER CHECK (order_count IS NULL OR order_count >= 0),
    PRIMARY KEY (event_ts, snapshot_id, side, level_no),
    FOREIGN KEY (event_ts, snapshot_id)
        REFERENCES market.order_book_snapshot(event_ts, snapshot_id) ON DELETE CASCADE
) PARTITION BY RANGE (event_ts);

CREATE TABLE IF NOT EXISTS market.order_book_delta (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    source_sequence     BIGINT NOT NULL,
    event_no            SMALLINT NOT NULL DEFAULT 0,
    revision_no         INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    side                CHAR(1) CHECK (side IN ('B','A')),
    action_code         VARCHAR(8) NOT NULL CHECK (action_code IN ('UPSERT','DELETE','CLEAR')),
    price               NUMERIC(38,18),
    quantity            NUMERIC(38,18),
    order_count         INTEGER,
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_ts, feed_id, instrument_id, source_sequence, event_no, revision_no),
    CHECK (system_available_at >= available_at),
    CHECK (quantity IS NULL OR quantity >= 0),
    CHECK (order_count IS NULL OR order_count >= 0),
    CHECK ((action_code = 'CLEAR') OR (side IS NOT NULL AND price IS NOT NULL))
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_order_book_delta_replay
    ON market.order_book_delta (feed_id, instrument_id, event_ts, source_sequence, event_no, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_order_book_delta_time_brin
    ON market.order_book_delta USING BRIN (event_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE OR REPLACE VIEW market.current_bar AS
SELECT DISTINCT ON (bar_series_id, bar_open_ts)
       bar_open_ts, bar_series_id, revision_no, available_at, system_available_at,
       bar_close_ts, trading_date, open_price, high_price, low_price, close_price,
       official_close_price, settlement_price, volume, quote_volume, trade_count,
       vwap, open_interest, is_final, quality_flags, ingestion_batch_id, recorded_at
FROM market.bar_revision
ORDER BY bar_series_id, bar_open_ts, available_at DESC, revision_no DESC;

COMMENT ON TABLE market.bar_series IS 'Defines one provider/instrument/timeframe/price-basis candle series.';
COMMENT ON COLUMN market.bar_series.close_semantics IS 'Prevents conflating last trade, official close, settlement and NAV.';
COMMENT ON TABLE market.bar_revision IS 'Append-only OHLCV revisions; use availability-aware DISTINCT ON for replay.';
COMMENT ON COLUMN market.bar_revision.available_at IS 'Earliest public/source availability, not ingestion time.';
COMMENT ON COLUMN market.bar_revision.system_available_at IS 'Earliest instant this system could have served the row.';
COMMENT ON COLUMN market.bar_revision.official_close_price IS 'Provider official/weighted close when distinct from last trade close_price.';
COMMENT ON TABLE market.trade_tick IS 'Revision/cancellation-aware tape keyed by feed sequence.';
COMMENT ON COLUMN market.trade_tick.event_ts_ns IS 'Optional Unix nanoseconds for feeds more precise than TIMESTAMPTZ microseconds.';
COMMENT ON TABLE market.order_book_delta IS 'Sequence-ordered book mutations; reconstruct from a complete snapshot plus subsequent deltas.';

-- ---------------------------------------------------------------------------
-- Extensible external/alternative data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS external.data_series (
    series_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    series_code         VARCHAR(160) NOT NULL,
    display_name        TEXT NOT NULL,
    entity_type         VARCHAR(24) NOT NULL
                        CHECK (entity_type IN ('INSTRUMENT','ISSUER','DOCUMENT','BLOCKCHAIN','MACRO','GLOBAL','OTHER')),
    value_type          VARCHAR(16) NOT NULL
                        CHECK (value_type IN ('NUMERIC','FLOAT','INTEGER','BOOLEAN','TEXT','JSON')),
    unit_code           VARCHAR(32),
    currency_code       VARCHAR(12) REFERENCES catalog.currency(currency_code),
    event_time_semantics TEXT NOT NULL,
    availability_rule  JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (feed_id, series_code)
);

CREATE TABLE IF NOT EXISTS external.observation (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    series_id           BIGINT NOT NULL REFERENCES external.data_series(series_id),
    entity_key          TEXT NOT NULL,
    instrument_id       BIGINT REFERENCES catalog.instrument(instrument_id),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    revision_no         INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    value_numeric       NUMERIC(38,18),
    value_float         DOUBLE PRECISION,
    value_integer       BIGINT,
    value_boolean       BOOLEAN,
    value_text          TEXT,
    value_json          JSONB,
    is_missing          BOOLEAN NOT NULL DEFAULT FALSE,
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    recorded_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_ts, series_id, entity_key, available_at, revision_no),
    UNIQUE (event_ts, series_id, entity_key, revision_no),
    CHECK (system_available_at >= available_at),
    CHECK ((is_missing AND num_nonnulls(value_numeric, value_float, value_integer,
                                        value_boolean, value_text, value_json) = 0)
        OR (NOT is_missing AND num_nonnulls(value_numeric, value_float, value_integer,
                                            value_boolean, value_text, value_json) = 1))
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_external_observation_pit
    ON external.observation (series_id, entity_key, event_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_external_observation_system_pit
    ON external.observation (series_id, entity_key, event_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_external_observation_time_brin
    ON external.observation USING BRIN (event_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE TABLE IF NOT EXISTS external.document (
    document_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id             BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    external_id         TEXT NOT NULL,
    instrument_id       BIGINT REFERENCES catalog.instrument(instrument_id),
    document_type       VARCHAR(32) NOT NULL,
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    published_at        TIMESTAMPTZ(6),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    revision_no         INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    title               TEXT,
    source_uri          TEXT,
    content_uri         TEXT,
    content_sha256      CHAR(64),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_batch_id  BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    UNIQUE (feed_id, external_id, revision_no),
    CHECK (system_available_at >= available_at)
);

CREATE INDEX IF NOT EXISTS ix_external_document_instrument_pit
    ON external.document (instrument_id, event_ts, available_at DESC);

COMMENT ON TABLE external.data_series IS 'Typed registry for fundamentals, NAV, sentiment, macro and on-chain observations.';
COMMENT ON TABLE external.observation IS 'Vintage-aware external fact; period/event time is distinct from publication availability.';
COMMENT ON TABLE external.document IS 'Codal/news/disclosure metadata; large content should live in object storage by URI and hash.';

-- ---------------------------------------------------------------------------
-- Backtesting and simulated execution ledger
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS backtest.strategy (
    strategy_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_code       VARCHAR(128) NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    description         TEXT,
    owner_name          TEXT,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest.strategy_version (
    strategy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id         BIGINT NOT NULL REFERENCES backtest.strategy(strategy_id),
    version_no          INTEGER NOT NULL CHECK (version_no > 0),
    class_path          TEXT NOT NULL,
    code_sha256         CHAR(64) NOT NULL,
    code_uri            TEXT,
    parameter_schema    JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_parameters  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deprecated_at       TIMESTAMPTZ(6),
    UNIQUE (strategy_id, version_no),
    UNIQUE (strategy_id, code_sha256)
);

CREATE TABLE IF NOT EXISTS backtest.run (
    run_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_code            VARCHAR(128) NOT NULL UNIQUE,
    strategy_version_id BIGINT NOT NULL REFERENCES backtest.strategy_version(strategy_version_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    universe_id         BIGINT NOT NULL REFERENCES catalog.universe(universe_id),
    timeframe_id        SMALLINT REFERENCES catalog.timeframe(timeframe_id),
    base_currency_code  VARCHAR(12) NOT NULL REFERENCES catalog.currency(currency_code),
    event_from          TIMESTAMPTZ(6) NOT NULL,
    event_to            TIMESTAMPTZ(6) NOT NULL,
    knowledge_cutoff_ts TIMESTAMPTZ(6) NOT NULL,
    availability_mode   VARCHAR(24) NOT NULL
                        CHECK (availability_mode IN ('PUBLIC_REPLAY','ACTUAL_SYSTEM_REPLAY')),
    initial_capital     NUMERIC(38,18) NOT NULL CHECK (initial_capital >= 0),
    parameters          JSONB NOT NULL,
    parameter_sha256    CHAR(64) NOT NULL,
    execution_model     JSONB NOT NULL DEFAULT '{}'::jsonb,
    transaction_cost_model JSONB NOT NULL DEFAULT '{}'::jsonb,
    engine_version      TEXT NOT NULL,
    random_seed         BIGINT NOT NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMPTZ(6),
    finished_at         TIMESTAMPTZ(6),
    error_summary       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (event_to > event_from),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX IF NOT EXISTS ix_backtest_run_strategy
    ON backtest.run (strategy_version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_backtest_run_parameter
    ON backtest.run (strategy_version_id, parameter_sha256, data_snapshot_id);

CREATE TABLE IF NOT EXISTS backtest.run_instrument (
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    membership_valid_from TIMESTAMPTZ(6),
    membership_valid_to TIMESTAMPTZ(6),
    initial_weight      DOUBLE PRECISION,
    PRIMARY KEY (run_id, instrument_id),
    CHECK (membership_valid_to IS NULL OR membership_valid_from IS NULL
           OR membership_valid_to > membership_valid_from)
);

CREATE TABLE IF NOT EXISTS backtest.signal (
    signal_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    signal_ts           TIMESTAMPTZ(6) NOT NULL,
    signal_type         VARCHAR(16) NOT NULL DEFAULT 'ENTRY'
                        CHECK (signal_type IN ('ENTRY','EXIT','ADJUST','CANCEL')),
    direction           VARCHAR(8) NOT NULL CHECK (direction IN ('LONG','SHORT','FLAT')),
    target_quantity     NUMERIC(38,18),
    intended_entry_price NUMERIC(38,18),
    stop_loss_price     NUMERIC(38,18),
    take_profit_price   NUMERIC(38,18),
    score               DOUBLE PRECISION,
    reason_code         TEXT,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_signal_run_instrument_time
    ON backtest.signal (run_id, instrument_id, signal_ts);

CREATE TABLE IF NOT EXISTS backtest.bt_order (
    order_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    signal_id           BIGINT REFERENCES backtest.signal(signal_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    client_order_key    TEXT NOT NULL,
    submitted_at        TIMESTAMPTZ(6) NOT NULL,
    valid_until         TIMESTAMPTZ(6),
    side                VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type          VARCHAR(16) NOT NULL CHECK (order_type IN ('MARKET','LIMIT','STOP','STOP_LIMIT','MOC','LOC')),
    time_in_force       VARCHAR(8) NOT NULL DEFAULT 'DAY'
                        CHECK (time_in_force IN ('DAY','GTC','IOC','FOK','GTD')),
    quantity            NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    limit_price         NUMERIC(38,18),
    stop_price          NUMERIC(38,18),
    status              VARCHAR(16) NOT NULL
                        CHECK (status IN ('NEW','ACCEPTED','PARTIAL','FILLED','CANCELLED','REJECTED','EXPIRED')),
    reject_reason       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, client_order_key),
    CHECK (valid_until IS NULL OR valid_until >= submitted_at),
    CHECK ((order_type IN ('LIMIT','STOP_LIMIT','LOC') AND limit_price IS NOT NULL)
        OR order_type NOT IN ('LIMIT','STOP_LIMIT','LOC')),
    CHECK ((order_type IN ('STOP','STOP_LIMIT') AND stop_price IS NOT NULL)
        OR order_type NOT IN ('STOP','STOP_LIMIT'))
);

CREATE INDEX IF NOT EXISTS ix_bt_order_run_instrument_time
    ON backtest.bt_order (run_id, instrument_id, submitted_at);

CREATE TABLE IF NOT EXISTS backtest.fill (
    fill_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    order_id            BIGINT NOT NULL REFERENCES backtest.bt_order(order_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    fill_ts             TIMESTAMPTZ(6) NOT NULL,
    price               NUMERIC(38,18) NOT NULL,
    quantity            NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    commission_amount   NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    slippage_amount     NUMERIC(38,18) NOT NULL DEFAULT 0,
    tax_amount          NUMERIC(38,18) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    fee_currency_code   VARCHAR(12) NOT NULL REFERENCES catalog.currency(currency_code),
    liquidity_flag      CHAR(1) CHECK (liquidity_flag IN ('M','T','U')),
    execution_reference JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_fill_run_instrument_time
    ON backtest.fill (run_id, instrument_id, fill_ts);
CREATE INDEX IF NOT EXISTS ix_fill_order ON backtest.fill (order_id, fill_ts);

CREATE TABLE IF NOT EXISTS backtest.round_trip_trade (
    trade_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    entry_signal_id     BIGINT REFERENCES backtest.signal(signal_id),
    exit_signal_id      BIGINT REFERENCES backtest.signal(signal_id),
    direction           VARCHAR(8) NOT NULL CHECK (direction IN ('LONG','SHORT')),
    entry_ts            TIMESTAMPTZ(6) NOT NULL,
    exit_ts             TIMESTAMPTZ(6),
    quantity            NUMERIC(38,18) NOT NULL CHECK (quantity > 0),
    average_entry_price NUMERIC(38,18) NOT NULL,
    average_exit_price  NUMERIC(38,18),
    gross_pnl           NUMERIC(38,18),
    commission_amount   NUMERIC(38,18) NOT NULL DEFAULT 0,
    slippage_amount     NUMERIC(38,18) NOT NULL DEFAULT 0,
    tax_amount          NUMERIC(38,18) NOT NULL DEFAULT 0,
    other_cost_amount   NUMERIC(38,18) NOT NULL DEFAULT 0,
    net_pnl             NUMERIC(38,18),
    return_fraction     DOUBLE PRECISION,
    maximum_favorable_excursion DOUBLE PRECISION,
    maximum_adverse_excursion DOUBLE PRECISION,
    status              VARCHAR(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    CHECK (exit_ts IS NULL OR exit_ts >= entry_ts),
    CHECK ((status = 'CLOSED' AND exit_ts IS NOT NULL AND average_exit_price IS NOT NULL AND net_pnl IS NOT NULL)
        OR status = 'OPEN')
);

CREATE INDEX IF NOT EXISTS ix_round_trip_trade_run_time
    ON backtest.round_trip_trade (run_id, instrument_id, entry_ts, exit_ts);

CREATE TABLE IF NOT EXISTS backtest.trade_fill_allocation (
    trade_id            BIGINT NOT NULL REFERENCES backtest.round_trip_trade(trade_id),
    fill_id             BIGINT NOT NULL REFERENCES backtest.fill(fill_id),
    leg_type            VARCHAR(8) NOT NULL CHECK (leg_type IN ('ENTRY','EXIT')),
    allocated_quantity  NUMERIC(38,18) NOT NULL CHECK (allocated_quantity > 0),
    PRIMARY KEY (trade_id, fill_id, leg_type)
);

CREATE INDEX IF NOT EXISTS ix_trade_fill_allocation_fill
    ON backtest.trade_fill_allocation (fill_id, trade_id);

CREATE TABLE IF NOT EXISTS backtest.cash_ledger (
    cash_entry_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    entry_ts            TIMESTAMPTZ(6) NOT NULL,
    currency_code       VARCHAR(12) NOT NULL REFERENCES catalog.currency(currency_code),
    entry_type          VARCHAR(24) NOT NULL
                        CHECK (entry_type IN ('INITIAL_CAPITAL','TRADE_NOTIONAL','COMMISSION','SLIPPAGE','TAX',
                                              'DIVIDEND','BORROW_FEE','FUNDING','MARGIN_INTEREST','FX','ADJUSTMENT','OTHER')),
    amount              NUMERIC(38,18) NOT NULL,
    fill_id             BIGINT REFERENCES backtest.fill(fill_id),
    trade_id            BIGINT REFERENCES backtest.round_trip_trade(trade_id),
    corporate_action_id BIGINT REFERENCES catalog.corporate_action(corporate_action_id),
    fx_rate_to_base     NUMERIC(38,18),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_cash_ledger_run_time
    ON backtest.cash_ledger (run_id, entry_ts, currency_code);

CREATE TABLE IF NOT EXISTS backtest.position_snapshot (
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    snapshot_ts         TIMESTAMPTZ(6) NOT NULL,
    quantity            NUMERIC(38,18) NOT NULL,
    average_cost        NUMERIC(38,18),
    market_price        NUMERIC(38,18),
    market_value_base   NUMERIC(38,18),
    realized_pnl_base   NUMERIC(38,18) NOT NULL DEFAULT 0,
    unrealized_pnl_base NUMERIC(38,18) NOT NULL DEFAULT 0,
    margin_used_base    NUMERIC(38,18) NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, instrument_id, snapshot_ts)
);

CREATE TABLE IF NOT EXISTS backtest.equity_point (
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    cash_base           NUMERIC(38,18) NOT NULL,
    equity_base         NUMERIC(38,18) NOT NULL,
    gross_exposure_base NUMERIC(38,18) NOT NULL DEFAULT 0,
    net_exposure_base   NUMERIC(38,18) NOT NULL DEFAULT 0,
    drawdown_fraction   DOUBLE PRECISION,
    PRIMARY KEY (run_id, event_ts)
);

CREATE TABLE IF NOT EXISTS backtest.run_summary (
    run_id              BIGINT PRIMARY KEY REFERENCES backtest.run(run_id),
    total_return        DOUBLE PRECISION,
    annualized_return   DOUBLE PRECISION,
    annualized_volatility DOUBLE PRECISION,
    sharpe_ratio        DOUBLE PRECISION,
    sortino_ratio       DOUBLE PRECISION,
    max_drawdown        DOUBLE PRECISION,
    win_rate            DOUBLE PRECISION,
    profit_factor       DOUBLE PRECISION,
    exposure_fraction   DOUBLE PRECISION,
    turnover_fraction   DOUBLE PRECISION,
    trade_count         BIGINT NOT NULL DEFAULT 0 CHECK (trade_count >= 0),
    winning_trade_count BIGINT NOT NULL DEFAULT 0 CHECK (winning_trade_count >= 0),
    losing_trade_count  BIGINT NOT NULL DEFAULT 0 CHECK (losing_trade_count >= 0),
    gross_pnl_base      NUMERIC(38,18),
    net_pnl_base        NUMERIC(38,18),
    total_cost_base     NUMERIC(38,18),
    calculation_version TEXT NOT NULL,
    annualization_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    calculated_at       TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (win_rate IS NULL OR win_rate BETWEEN 0 AND 1),
    CHECK (max_drawdown IS NULL OR max_drawdown BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS backtest.run_metric (
    run_metric_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    metric_name         VARCHAR(96) NOT NULL,
    metric_version      VARCHAR(32) NOT NULL DEFAULT '1',
    scope_key           VARCHAR(160) NOT NULL DEFAULT 'ALL',
    metric_value        DOUBLE PRECISION NOT NULL,
    unit_code           VARCHAR(32),
    annualization_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    dimensions          JSONB NOT NULL DEFAULT '{}'::jsonb,
    measured_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, metric_name, metric_version, scope_key)
);

CREATE INDEX IF NOT EXISTS ix_run_metric_lookup
    ON backtest.run_metric (run_id, metric_name, scope_key);

COMMENT ON TABLE backtest.run IS 'Reproducible backtest execution bound to strategy, snapshot, universe, parameters and seed.';
COMMENT ON COLUMN backtest.run.knowledge_cutoff_ts IS 'Global snapshot upper bound; each event must additionally pass decision-time availability.';
COMMENT ON TABLE backtest.signal IS 'Strategy intent, separate from orders, partial fills and derived round-trip trades.';
COMMENT ON TABLE backtest.fill IS 'Execution source of truth including commission, slippage and tax.';
COMMENT ON TABLE backtest.cash_ledger IS 'Multi-currency cash movements used to reconcile equity and PnL.';
COMMENT ON TABLE backtest.position_snapshot IS 'Time-aware position valuation; derived from fills and corporate-action processing.';
COMMENT ON COLUMN backtest.run_summary.annualization_basis IS 'Sessions/year, return interval and risk-free assumptions used by Sharpe/Sortino.';

-- ---------------------------------------------------------------------------
-- Feature store, labels, frozen datasets and experiment tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ml.feature_definition (
    feature_definition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_key         VARCHAR(160) NOT NULL,
    version_no          INTEGER NOT NULL CHECK (version_no > 0),
    display_name        TEXT NOT NULL,
    description         TEXT,
    value_type          VARCHAR(12) NOT NULL
                        CHECK (value_type IN ('FLOAT64','INT64','BOOLEAN','CATEGORY')),
    entity_type         VARCHAR(16) NOT NULL DEFAULT 'INSTRUMENT'
                        CHECK (entity_type IN ('INSTRUMENT','MARKET','PORTFOLIO','GLOBAL')),
    event_time_semantics TEXT NOT NULL,
    availability_rule   JSONB NOT NULL DEFAULT '{}'::jsonb,
    lookback_bars       INTEGER CHECK (lookback_bars IS NULL OR lookback_bars > 0),
    lookback_interval   INTERVAL,
    parameters          JSONB NOT NULL DEFAULT '{}'::jsonb,
    adjustment_policy   VARCHAR(32) NOT NULL DEFAULT 'RAW',
    code_uri            TEXT,
    code_sha256         CHAR(64) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','FROZEN','DEPRECATED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    frozen_at           TIMESTAMPTZ(6),
    UNIQUE (feature_key, version_no),
    CHECK ((status = 'FROZEN' AND frozen_at IS NOT NULL) OR status <> 'FROZEN')
);

CREATE TABLE IF NOT EXISTS ml.feature_set (
    feature_set_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_set_key     VARCHAR(160) NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    description         TEXT,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml.feature_set_version (
    feature_set_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_set_id      BIGINT NOT NULL REFERENCES ml.feature_set(feature_set_id),
    version_no          INTEGER NOT NULL CHECK (version_no > 0),
    timeframe_id        SMALLINT REFERENCES catalog.timeframe(timeframe_id),
    schema_sha256       CHAR(64) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','FROZEN','DEPRECATED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    frozen_at           TIMESTAMPTZ(6),
    UNIQUE (feature_set_id, version_no),
    CHECK ((status = 'FROZEN' AND frozen_at IS NOT NULL) OR status <> 'FROZEN')
);

CREATE TABLE IF NOT EXISTS ml.feature_set_member (
    feature_set_version_id BIGINT NOT NULL REFERENCES ml.feature_set_version(feature_set_version_id),
    feature_definition_id BIGINT NOT NULL REFERENCES ml.feature_definition(feature_definition_id),
    ordinal             INTEGER NOT NULL CHECK (ordinal > 0),
    output_name         VARCHAR(160) NOT NULL,
    is_required         BOOLEAN NOT NULL DEFAULT TRUE,
    deterministic_transform JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (feature_set_version_id, feature_definition_id),
    UNIQUE (feature_set_version_id, ordinal),
    UNIQUE (feature_set_version_id, output_name)
);

CREATE TABLE IF NOT EXISTS ml.feature_materialization_run (
    materialization_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_set_version_id BIGINT NOT NULL REFERENCES ml.feature_set_version(feature_set_version_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    availability_mode   VARCHAR(24) NOT NULL
                        CHECK (availability_mode IN ('PUBLIC_REPLAY','ACTUAL_SYSTEM_REPLAY')),
    event_from          TIMESTAMPTZ(6) NOT NULL,
    event_to            TIMESTAMPTZ(6) NOT NULL,
    code_sha256         CHAR(64) NOT NULL,
    parameter_sha256    CHAR(64) NOT NULL,
    parameters          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    started_at          TIMESTAMPTZ(6),
    finished_at         TIMESTAMPTZ(6),
    error_summary       TEXT,
    UNIQUE (materialization_run_id, feature_set_version_id, data_snapshot_id),
    CHECK (event_to > event_from),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS ml.feature_value (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    feature_definition_id BIGINT NOT NULL REFERENCES ml.feature_definition(feature_definition_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    revision_no         INTEGER NOT NULL CHECK (revision_no > 0),
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    window_start_ts     TIMESTAMPTZ(6),
    window_end_ts       TIMESTAMPTZ(6) NOT NULL,
    source_max_available_at TIMESTAMPTZ(6) NOT NULL,
    value_float         DOUBLE PRECISION,
    value_integer       BIGINT,
    value_boolean       BOOLEAN,
    value_text          TEXT,
    is_missing          BOOLEAN NOT NULL DEFAULT FALSE,
    missing_reason      TEXT,
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    materialization_run_id BIGINT NOT NULL REFERENCES ml.feature_materialization_run(materialization_run_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    computed_at         TIMESTAMPTZ(6) NOT NULL,
    row_sha256          CHAR(64) NOT NULL,
    PRIMARY KEY (event_ts, feature_definition_id, instrument_id, timeframe_id, available_at, revision_no),
    UNIQUE (event_ts, feature_definition_id, instrument_id, timeframe_id, revision_no),
    CHECK (window_start_ts IS NULL OR window_end_ts >= window_start_ts),
    CHECK (event_ts >= window_end_ts),
    CHECK (available_at >= window_end_ts),
    CHECK (available_at >= source_max_available_at),
    CHECK (system_available_at >= available_at),
    CHECK (system_available_at >= computed_at),
    CHECK ((is_missing AND num_nonnulls(value_float, value_integer, value_boolean, value_text) = 0)
        OR (NOT is_missing AND num_nonnulls(value_float, value_integer, value_boolean, value_text) = 1))
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_feature_value_public_pit
    ON ml.feature_value (feature_definition_id, instrument_id, timeframe_id,
                         event_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_feature_value_system_pit
    ON ml.feature_value (feature_definition_id, instrument_id, timeframe_id,
                         event_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_feature_value_time_brin
    ON ml.feature_value USING BRIN (event_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE TABLE IF NOT EXISTS ml.feature_vector (
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    feature_set_version_id BIGINT NOT NULL REFERENCES ml.feature_set_version(feature_set_version_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    revision_no         INTEGER NOT NULL CHECK (revision_no > 0),
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    window_start_ts     TIMESTAMPTZ(6) NOT NULL,
    window_end_ts       TIMESTAMPTZ(6) NOT NULL,
    source_max_available_at TIMESTAMPTZ(6) NOT NULL,
    feature_count       SMALLINT NOT NULL CHECK (feature_count > 0),
    missing_count       SMALLINT NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    values              DOUBLE PRECISION[] NOT NULL,
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    materialization_run_id BIGINT NOT NULL REFERENCES ml.feature_materialization_run(materialization_run_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    computed_at         TIMESTAMPTZ(6) NOT NULL,
    row_sha256          CHAR(64) NOT NULL,
    PRIMARY KEY (event_ts, feature_set_version_id, instrument_id, timeframe_id, available_at, revision_no),
    UNIQUE (event_ts, feature_set_version_id, instrument_id, timeframe_id, revision_no),
    FOREIGN KEY (materialization_run_id, feature_set_version_id, data_snapshot_id)
        REFERENCES ml.feature_materialization_run(materialization_run_id, feature_set_version_id, data_snapshot_id),
    CHECK (window_end_ts >= window_start_ts),
    CHECK (event_ts >= window_end_ts),
    CHECK (available_at >= window_end_ts),
    CHECK (available_at >= source_max_available_at),
    CHECK (system_available_at >= available_at),
    CHECK (system_available_at >= computed_at),
    CHECK (cardinality(values) = feature_count),
    CHECK (missing_count <= feature_count)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_feature_vector_public_window
    ON ml.feature_vector (feature_set_version_id, instrument_id, timeframe_id,
                          event_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_feature_vector_system_window
    ON ml.feature_vector (feature_set_version_id, instrument_id, timeframe_id,
                          event_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_feature_vector_complete_window
    ON ml.feature_vector (feature_set_version_id, instrument_id, timeframe_id, event_ts)
    WHERE missing_count = 0;
CREATE INDEX IF NOT EXISTS ix_feature_vector_time_brin
    ON ml.feature_vector USING BRIN (event_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE TABLE IF NOT EXISTS ml.label_definition (
    label_definition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_key           VARCHAR(160) NOT NULL,
    version_no          INTEGER NOT NULL CHECK (version_no > 0),
    display_name        TEXT NOT NULL,
    description         TEXT,
    task_type           VARCHAR(24) NOT NULL
                        CHECK (task_type IN ('REGRESSION','BINARY_CLASSIFICATION','MULTICLASS_CLASSIFICATION','MULTILABEL')),
    value_type          VARCHAR(12) NOT NULL CHECK (value_type IN ('FLOAT64','INT64','TEXT','JSON')),
    horizon_bars        INTEGER,
    horizon_interval    INTERVAL,
    formula_parameters  JSONB NOT NULL DEFAULT '{}'::jsonb,
    adjustment_policy   VARCHAR(32) NOT NULL DEFAULT 'RAW',
    code_uri            TEXT,
    code_sha256         CHAR(64) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','FROZEN','DEPRECATED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    frozen_at           TIMESTAMPTZ(6),
    UNIQUE (label_key, version_no),
    CHECK (num_nonnulls(horizon_bars, horizon_interval) = 1),
    CHECK (horizon_bars IS NULL OR horizon_bars > 0),
    CHECK ((status = 'FROZEN' AND frozen_at IS NOT NULL) OR status <> 'FROZEN')
);

CREATE TABLE IF NOT EXISTS ml.label_materialization_run (
    label_run_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_definition_id BIGINT NOT NULL REFERENCES ml.label_definition(label_definition_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    event_from          TIMESTAMPTZ(6) NOT NULL,
    event_to            TIMESTAMPTZ(6) NOT NULL,
    code_sha256         CHAR(64) NOT NULL,
    parameter_sha256    CHAR(64) NOT NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    started_at          TIMESTAMPTZ(6),
    finished_at         TIMESTAMPTZ(6),
    error_summary       TEXT,
    UNIQUE (label_run_id, label_definition_id, data_snapshot_id),
    CHECK (event_to > event_from),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS ml.label_value (
    anchor_ts           TIMESTAMPTZ(6) NOT NULL,
    label_definition_id BIGINT NOT NULL REFERENCES ml.label_definition(label_definition_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    available_at        TIMESTAMPTZ(6) NOT NULL,
    revision_no         INTEGER NOT NULL CHECK (revision_no > 0),
    system_available_at TIMESTAMPTZ(6) NOT NULL,
    outcome_start_ts    TIMESTAMPTZ(6) NOT NULL,
    outcome_end_ts      TIMESTAMPTZ(6) NOT NULL,
    value_float         DOUBLE PRECISION,
    value_integer       BIGINT,
    value_text          TEXT,
    value_json          JSONB,
    is_censored         BOOLEAN NOT NULL DEFAULT FALSE,
    censor_reason       TEXT,
    quality_flags       INTEGER NOT NULL DEFAULT 0,
    label_run_id        BIGINT NOT NULL REFERENCES ml.label_materialization_run(label_run_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    computed_at         TIMESTAMPTZ(6) NOT NULL,
    row_sha256          CHAR(64) NOT NULL,
    PRIMARY KEY (anchor_ts, label_definition_id, instrument_id, timeframe_id, available_at, revision_no),
    UNIQUE (anchor_ts, label_definition_id, instrument_id, timeframe_id, revision_no),
    FOREIGN KEY (label_run_id, label_definition_id, data_snapshot_id)
        REFERENCES ml.label_materialization_run(label_run_id, label_definition_id, data_snapshot_id),
    CHECK (outcome_start_ts >= anchor_ts),
    CHECK (outcome_end_ts >= outcome_start_ts),
    CHECK (available_at >= outcome_end_ts),
    CHECK (system_available_at >= available_at),
    CHECK (system_available_at >= computed_at),
    CHECK ((is_censored AND num_nonnulls(value_float, value_integer, value_text, value_json) = 0)
        OR (NOT is_censored AND num_nonnulls(value_float, value_integer, value_text, value_json) = 1))
) PARTITION BY RANGE (anchor_ts);

CREATE INDEX IF NOT EXISTS ix_label_value_lookup
    ON ml.label_value (label_definition_id, instrument_id, timeframe_id,
                       anchor_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_label_value_time_brin
    ON ml.label_value USING BRIN (anchor_ts) WITH (pages_per_range = 128, autosummarize = on);

CREATE TABLE IF NOT EXISTS ml.dataset (
    dataset_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_key         VARCHAR(160) NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    description         TEXT,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml.dataset_version (
    dataset_version_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id          BIGINT NOT NULL REFERENCES ml.dataset(dataset_id),
    version_no          INTEGER NOT NULL CHECK (version_no > 0),
    task_mode           VARCHAR(16) NOT NULL CHECK (task_mode IN ('SUPERVISED','UNSUPERVISED','RL')),
    feature_set_version_id BIGINT NOT NULL REFERENCES ml.feature_set_version(feature_set_version_id),
    label_definition_id BIGINT REFERENCES ml.label_definition(label_definition_id),
    label_run_id        BIGINT REFERENCES ml.label_materialization_run(label_run_id),
    universe_id         BIGINT NOT NULL REFERENCES catalog.universe(universe_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    data_snapshot_id    BIGINT NOT NULL REFERENCES catalog.data_snapshot(data_snapshot_id),
    event_from          TIMESTAMPTZ(6) NOT NULL,
    event_to            TIMESTAMPTZ(6) NOT NULL,
    knowledge_cutoff_ts TIMESTAMPTZ(6) NOT NULL,
    availability_mode   VARCHAR(24) NOT NULL
                        CHECK (availability_mode IN ('PUBLIC_REPLAY','ACTUAL_SYSTEM_REPLAY')),
    sequence_length     INTEGER NOT NULL DEFAULT 1 CHECK (sequence_length > 0),
    stride_bars         INTEGER NOT NULL DEFAULT 1 CHECK (stride_bars > 0),
    decision_lag        INTERVAL NOT NULL DEFAULT INTERVAL '0',
    sampling_policy     JSONB NOT NULL DEFAULT '{}'::jsonb,
    missing_value_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    split_policy        JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest_uri        TEXT,
    manifest_sha256     CHAR(64),
    data_fingerprint    CHAR(64) NOT NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','BUILDING','FROZEN','FAILED','DEPRECATED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    frozen_at           TIMESTAMPTZ(6),
    UNIQUE (dataset_id, version_no),
    FOREIGN KEY (label_run_id, label_definition_id, data_snapshot_id)
        REFERENCES ml.label_materialization_run(label_run_id, label_definition_id, data_snapshot_id),
    CHECK (event_to > event_from),
    CHECK ((task_mode = 'SUPERVISED' AND label_definition_id IS NOT NULL AND label_run_id IS NOT NULL)
        OR task_mode <> 'SUPERVISED'),
    CHECK ((status = 'FROZEN' AND frozen_at IS NOT NULL AND manifest_sha256 IS NOT NULL)
        OR status <> 'FROZEN')
);

CREATE TABLE IF NOT EXISTS ml.dataset_sample (
    dataset_version_id  BIGINT NOT NULL REFERENCES ml.dataset_version(dataset_version_id),
    sample_id           BIGINT GENERATED ALWAYS AS IDENTITY,
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    timeframe_id        SMALLINT NOT NULL REFERENCES catalog.timeframe(timeframe_id),
    bar_series_id       BIGINT NOT NULL REFERENCES market.bar_series(bar_series_id),
    anchor_ts           TIMESTAMPTZ(6) NOT NULL,
    prediction_ts       TIMESTAMPTZ(6) NOT NULL,
    window_start_ts     TIMESTAMPTZ(6) NOT NULL,
    window_end_ts       TIMESTAMPTZ(6) NOT NULL,
    expected_steps      INTEGER NOT NULL CHECK (expected_steps > 0),
    label_definition_id BIGINT,
    label_anchor_ts     TIMESTAMPTZ(6),
    label_available_at  TIMESTAMPTZ(6),
    label_revision_no   INTEGER,
    sample_weight       DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (sample_weight >= 0),
    sample_sha256       CHAR(64) NOT NULL,
    PRIMARY KEY (dataset_version_id, sample_id),
    UNIQUE (dataset_version_id, instrument_id, timeframe_id, bar_series_id, anchor_ts, prediction_ts),
    UNIQUE (dataset_version_id, sample_id, instrument_id, timeframe_id, bar_series_id),
    FOREIGN KEY (bar_series_id, instrument_id, timeframe_id)
        REFERENCES market.bar_series(bar_series_id, instrument_id, timeframe_id),
    FOREIGN KEY (label_anchor_ts, label_definition_id, instrument_id, timeframe_id,
                 label_available_at, label_revision_no)
        REFERENCES ml.label_value(anchor_ts, label_definition_id, instrument_id, timeframe_id,
                                  available_at, revision_no),
    CHECK (window_end_ts >= window_start_ts),
    CHECK (anchor_ts >= window_end_ts),
    CHECK (prediction_ts >= anchor_ts),
    CHECK (num_nonnulls(label_definition_id, label_anchor_ts, label_available_at, label_revision_no) IN (0,4))
);

CREATE INDEX IF NOT EXISTS ix_dataset_sample_batch
    ON ml.dataset_sample (dataset_version_id, instrument_id, anchor_ts, sample_id);

CREATE TABLE IF NOT EXISTS ml.dataset_sample_step (
    dataset_version_id  BIGINT NOT NULL,
    sample_id           BIGINT NOT NULL,
    step_no             INTEGER NOT NULL CHECK (step_no >= 0),
    feature_event_ts    TIMESTAMPTZ(6) NOT NULL,
    feature_set_version_id BIGINT NOT NULL,
    instrument_id       BIGINT NOT NULL,
    timeframe_id        SMALLINT NOT NULL,
    bar_series_id       BIGINT NOT NULL,
    bar_open_ts         TIMESTAMPTZ(6) NOT NULL,
    bar_available_at    TIMESTAMPTZ(6) NOT NULL,
    bar_revision_no     INTEGER NOT NULL,
    feature_available_at TIMESTAMPTZ(6) NOT NULL,
    feature_revision_no INTEGER NOT NULL,
    PRIMARY KEY (dataset_version_id, sample_id, step_no),
    FOREIGN KEY (dataset_version_id, sample_id, instrument_id, timeframe_id, bar_series_id)
        REFERENCES ml.dataset_sample(dataset_version_id, sample_id, instrument_id, timeframe_id, bar_series_id)
        ON DELETE CASCADE,
    FOREIGN KEY (bar_open_ts, bar_series_id, bar_revision_no, bar_available_at)
        REFERENCES market.bar_revision(bar_open_ts, bar_series_id, revision_no, available_at),
    FOREIGN KEY (feature_event_ts, feature_set_version_id, instrument_id, timeframe_id,
                 feature_available_at, feature_revision_no)
        REFERENCES ml.feature_vector(event_ts, feature_set_version_id, instrument_id, timeframe_id,
                                     available_at, revision_no)
);

CREATE TABLE IF NOT EXISTS ml.dataset_split (
    split_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_version_id  BIGINT NOT NULL REFERENCES ml.dataset_version(dataset_version_id),
    fold_no             INTEGER NOT NULL DEFAULT 0 CHECK (fold_no >= 0),
    segment_no          INTEGER NOT NULL DEFAULT 0 CHECK (segment_no >= 0),
    split_role          VARCHAR(12) NOT NULL CHECK (split_role IN ('TRAIN','VALIDATION','TEST')),
    event_from          TIMESTAMPTZ(6) NOT NULL,
    event_to            TIMESTAMPTZ(6) NOT NULL,
    purge_bars          INTEGER NOT NULL DEFAULT 0 CHECK (purge_bars >= 0),
    embargo_bars        INTEGER NOT NULL DEFAULT 0 CHECK (embargo_bars >= 0),
    UNIQUE (dataset_version_id, fold_no, segment_no, split_role),
    UNIQUE (dataset_version_id, split_id),
    CHECK (event_to > event_from)
);

CREATE TABLE IF NOT EXISTS ml.dataset_sample_assignment (
    dataset_version_id  BIGINT NOT NULL,
    sample_id           BIGINT NOT NULL,
    split_id            BIGINT NOT NULL,
    PRIMARY KEY (dataset_version_id, sample_id, split_id),
    FOREIGN KEY (dataset_version_id, sample_id)
        REFERENCES ml.dataset_sample(dataset_version_id, sample_id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_version_id, split_id)
        REFERENCES ml.dataset_split(dataset_version_id, split_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_dataset_assignment_split
    ON ml.dataset_sample_assignment (dataset_version_id, split_id, sample_id);

CREATE TABLE IF NOT EXISTS ml.experiment (
    experiment_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_key      VARCHAR(160) NOT NULL UNIQUE,
    objective           TEXT NOT NULL,
    owner_name          TEXT,
    tags                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml.training_run (
    training_run_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id       BIGINT NOT NULL REFERENCES ml.experiment(experiment_id),
    dataset_version_id  BIGINT NOT NULL REFERENCES ml.dataset_version(dataset_version_id),
    fold_no             INTEGER,
    model_family        TEXT NOT NULL,
    hyperparameters     JSONB NOT NULL,
    random_seed         BIGINT NOT NULL,
    code_sha256         CHAR(64) NOT NULL,
    container_digest    TEXT,
    dependency_lock_sha256 CHAR(64),
    hardware_info       JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_training BOOLEAN NOT NULL DEFAULT FALSE,
    status              VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMPTZ(6),
    finished_at         TIMESTAMPTZ(6),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS ml.training_metric (
    training_metric_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    training_run_id     BIGINT NOT NULL REFERENCES ml.training_run(training_run_id),
    split_role          VARCHAR(12) NOT NULL CHECK (split_role IN ('TRAIN','VALIDATION','TEST')),
    metric_name         VARCHAR(96) NOT NULL,
    metric_value        DOUBLE PRECISION NOT NULL,
    epoch_no            INTEGER,
    step_no             INTEGER,
    dimensions          JSONB NOT NULL DEFAULT '{}'::jsonb,
    measured_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_training_metric_lookup
    ON ml.training_metric (training_run_id, split_role, metric_name, epoch_no);

CREATE TABLE IF NOT EXISTS ml.model_artifact (
    artifact_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    training_run_id     BIGINT NOT NULL REFERENCES ml.training_run(training_run_id),
    artifact_type       VARCHAR(24) NOT NULL
                        CHECK (artifact_type IN ('MODEL','PREPROCESSOR','SCALER','CHECKPOINT','PREDICTION',
                                                 'EXPLANATION','REPORT','LOG','MANIFEST')),
    artifact_uri        TEXT NOT NULL,
    artifact_sha256     CHAR(64) NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (training_run_id, artifact_type, artifact_sha256)
);

COMMENT ON TABLE ml.feature_definition IS 'Immutable versioned semantic definition; any formula/lookback/source/lag change creates a new version.';
COMMENT ON COLUMN ml.feature_definition.availability_rule IS 'Rule for deriving earliest public eligibility from all source observations.';
COMMENT ON TABLE ml.feature_value IS 'Canonical long feature store for lineage and point-in-time selection.';
COMMENT ON COLUMN ml.feature_value.source_max_available_at IS 'Maximum public availability across every input used by this feature value.';
COMMENT ON TABLE ml.feature_vector IS 'Dense numeric cache ordered by feature_set_member.ordinal for fast sliding-window reads.';
COMMENT ON COLUMN ml.feature_vector.values IS 'Numeric vector; NULL array elements represent missing values, never silently backward-filled.';
COMMENT ON TABLE ml.label_value IS 'Future outcome target; available_at is at or after outcome_end_ts and never joins into predictors.';
COMMENT ON TABLE ml.dataset_sample_step IS 'Exact feature-vector revision for each sequence step; protects frozen datasets from late revisions.';
COMMENT ON TABLE ml.dataset_sample_assignment IS 'Exact fold/split membership; supports walk-forward folds without random time-series splits.';
COMMENT ON TABLE ml.model_artifact IS 'Content-addressed models and fitted preprocessors such as scalers/PCA/encoders.';

-- Creates one monthly bar partition. Set p_hash_buckets > 1 to subpartition by
-- bar_series_id (the canonical symbol/series dimension) without LIST explosion.
CREATE OR REPLACE FUNCTION market.create_bar_month_partition(
    p_month DATE,
    p_hash_buckets INTEGER DEFAULT 0
) RETURNS VOID
LANGUAGE plpgsql
AS $function$
DECLARE
    v_month       DATE := date_trunc('month', p_month)::date;
    v_next_month  DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::date;
    v_parent_name TEXT := format('bar_revision_y%sm%s',
                                 to_char(p_month, 'YYYY'), to_char(p_month, 'MM'));
    v_leaf_name   TEXT;
    v_bucket      INTEGER;
BEGIN
    IF p_hash_buckets < 0 OR p_hash_buckets > 64 OR p_hash_buckets = 1 THEN
        RAISE EXCEPTION 'p_hash_buckets must be 0 or between 2 and 64';
    END IF;

    IF to_regclass(format('market.%I', v_parent_name)) IS NOT NULL THEN
        RETURN;
    END IF;

    IF p_hash_buckets = 0 THEN
        EXECUTE format(
            'CREATE TABLE market.%I PARTITION OF market.bar_revision '
            'FOR VALUES FROM (%L) TO (%L)',
            v_parent_name,
            (v_month::timestamp AT TIME ZONE 'UTC'),
            (v_next_month::timestamp AT TIME ZONE 'UTC')
        );
    ELSE
        EXECUTE format(
            'CREATE TABLE market.%I PARTITION OF market.bar_revision '
            'FOR VALUES FROM (%L) TO (%L) PARTITION BY HASH (bar_series_id)',
            v_parent_name,
            (v_month::timestamp AT TIME ZONE 'UTC'),
            (v_next_month::timestamp AT TIME ZONE 'UTC')
        );

        FOR v_bucket IN 0..p_hash_buckets - 1 LOOP
            v_leaf_name := format('%s_h%s', v_parent_name, lpad(v_bucket::text, 2, '0'));
            EXECUTE format(
                'CREATE TABLE market.%I PARTITION OF market.%I '
                'FOR VALUES WITH (MODULUS %s, REMAINDER %s)',
                v_leaf_name, v_parent_name, p_hash_buckets, v_bucket
            );
        END LOOP;
    END IF;
END
$function$;

COMMENT ON FUNCTION market.create_bar_month_partition(DATE, INTEGER) IS
'Creates a UTC monthly bar partition and optional HASH subpartitions by bar_series_id.';

COMMIT;
