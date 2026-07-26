-- Bisfin technical-backtesting completion migration
-- Target: PostgreSQL 16+
-- Apply after 001_core_schema.sql. ML/DL tables are intentionally untouched.

BEGIN;

-- ---------------------------------------------------------------------------
-- Canonical technical-market snapshots exposed by BrsApi/TSETMC/IME
-- ---------------------------------------------------------------------------

ALTER TABLE market.bar_revision
    ADD COLUMN IF NOT EXISTS previous_close_price NUMERIC(38,18);

COMMENT ON COLUMN market.bar_revision.previous_close_price IS
'Previous session official close (BrsApi py); distinct from the current bar close.';

CREATE TABLE IF NOT EXISTS market.quote_snapshot (
    event_ts             TIMESTAMPTZ(6) NOT NULL,
    feed_id              BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id        BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    revision_no          INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    available_at         TIMESTAMPTZ(6) NOT NULL,
    system_available_at  TIMESTAMPTZ(6) NOT NULL,
    trading_date         DATE NOT NULL,
    source_state         TEXT,
    normalized_state     VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
                         CHECK (normalized_state IN
                                ('PREOPEN','OPEN','AUCTION','HALTED','SUSPENDED','CLOSED','UNKNOWN')),
    lower_price_limit    NUMERIC(38,18),
    upper_price_limit    NUMERIC(38,18),
    previous_close_price NUMERIC(38,18),
    open_price           NUMERIC(38,18),
    session_low_price    NUMERIC(38,18),
    session_high_price   NUMERIC(38,18),
    last_price           NUMERIC(38,18),
    official_close_price NUMERIC(38,18),
    settlement_price     NUMERIC(38,18),
    base_volume          NUMERIC(38,18),
    trade_count          BIGINT,
    volume               NUMERIC(38,18),
    turnover_value       NUMERIC(38,18),
    is_final             BOOLEAN NOT NULL DEFAULT FALSE,
    ingestion_batch_id   BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    quality_flags        INTEGER NOT NULL DEFAULT 0,
    recorded_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_ts, feed_id, instrument_id, revision_no, available_at),
    UNIQUE (event_ts, feed_id, instrument_id, revision_no),
    CHECK (system_available_at >= available_at),
    CHECK (upper_price_limit IS NULL OR lower_price_limit IS NULL
           OR upper_price_limit >= lower_price_limit),
    CHECK (session_high_price IS NULL OR session_low_price IS NULL
           OR session_high_price >= session_low_price),
    CHECK (open_price IS NULL OR session_low_price IS NULL OR session_high_price IS NULL
           OR open_price BETWEEN session_low_price AND session_high_price),
    CHECK (last_price IS NULL OR session_low_price IS NULL OR session_high_price IS NULL
           OR last_price BETWEEN session_low_price AND session_high_price),
    CHECK (base_volume IS NULL OR base_volume >= 0),
    CHECK (trade_count IS NULL OR trade_count >= 0),
    CHECK (volume IS NULL OR volume >= 0),
    CHECK (turnover_value IS NULL OR turnover_value >= 0)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_quote_snapshot_public_pit
    ON market.quote_snapshot
       (feed_id, instrument_id, event_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_quote_snapshot_system_pit
    ON market.quote_snapshot
       (feed_id, instrument_id, event_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_quote_snapshot_time_brin
    ON market.quote_snapshot USING BRIN (event_ts)
       WITH (pages_per_range = 128, autosummarize = on);

COMMENT ON TABLE market.quote_snapshot IS
'Append-only intraday/session quote snapshots. BrsApi mappings: pf=open, pmin/pmax=low/high, pl=last, pc=official close, py=previous close, tno/tvol/tval=trades/volume/value.';
COMMENT ON COLUMN market.quote_snapshot.normalized_state IS
'Replayable trading state; catalog.instrument.status remains lifecycle state only.';

CREATE TABLE IF NOT EXISTS market.participant_flow_snapshot (
    event_ts                TIMESTAMPTZ(6) NOT NULL,
    feed_id                 BIGINT NOT NULL REFERENCES catalog.data_feed(feed_id),
    instrument_id           BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    revision_no             INTEGER NOT NULL DEFAULT 1 CHECK (revision_no > 0),
    available_at            TIMESTAMPTZ(6) NOT NULL,
    system_available_at     TIMESTAMPTZ(6) NOT NULL,
    trading_date            DATE NOT NULL,
    window_start_ts         TIMESTAMPTZ(6) NOT NULL,
    aggregation_kind        VARCHAR(20) NOT NULL
                            CHECK (aggregation_kind IN ('BAR','SESSION_TO_DATE','SESSION_FINAL')),
    individual_buy_count    BIGINT,
    individual_sell_count   BIGINT,
    legal_buy_count         BIGINT,
    legal_sell_count        BIGINT,
    individual_buy_volume   NUMERIC(38,18),
    individual_sell_volume  NUMERIC(38,18),
    legal_buy_volume        NUMERIC(38,18),
    legal_sell_volume       NUMERIC(38,18),
    individual_buy_value    NUMERIC(38,18),
    individual_sell_value   NUMERIC(38,18),
    legal_buy_value         NUMERIC(38,18),
    legal_sell_value        NUMERIC(38,18),
    is_final                BOOLEAN NOT NULL DEFAULT FALSE,
    ingestion_batch_id      BIGINT NOT NULL REFERENCES ingest.ingestion_batch(ingestion_batch_id),
    quality_flags           INTEGER NOT NULL DEFAULT 0,
    recorded_at             TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_ts, feed_id, instrument_id, revision_no, available_at),
    UNIQUE (event_ts, feed_id, instrument_id, revision_no),
    CHECK (event_ts >= window_start_ts),
    CHECK (system_available_at >= available_at),
    CHECK (individual_buy_count IS NULL OR individual_buy_count >= 0),
    CHECK (individual_sell_count IS NULL OR individual_sell_count >= 0),
    CHECK (legal_buy_count IS NULL OR legal_buy_count >= 0),
    CHECK (legal_sell_count IS NULL OR legal_sell_count >= 0),
    CHECK (individual_buy_volume IS NULL OR individual_buy_volume >= 0),
    CHECK (individual_sell_volume IS NULL OR individual_sell_volume >= 0),
    CHECK (legal_buy_volume IS NULL OR legal_buy_volume >= 0),
    CHECK (legal_sell_volume IS NULL OR legal_sell_volume >= 0),
    CHECK (individual_buy_value IS NULL OR individual_buy_value >= 0),
    CHECK (individual_sell_value IS NULL OR individual_sell_value >= 0),
    CHECK (legal_buy_value IS NULL OR legal_buy_value >= 0),
    CHECK (legal_sell_value IS NULL OR legal_sell_value >= 0)
) PARTITION BY RANGE (event_ts);

CREATE INDEX IF NOT EXISTS ix_participant_flow_public_pit
    ON market.participant_flow_snapshot
       (feed_id, instrument_id, event_ts, available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_participant_flow_system_pit
    ON market.participant_flow_snapshot
       (feed_id, instrument_id, event_ts, system_available_at DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_participant_flow_time_brin
    ON market.participant_flow_snapshot USING BRIN (event_ts)
       WITH (pages_per_range = 128, autosummarize = on);

COMMENT ON TABLE market.participant_flow_snapshot IS
'Wide, append-only BrsApi حقیقی/حقوقی snapshots for fast liquidity/order-flow features in technical backtests.';

-- The previous replay indexes did not put availability in the searchable key.
CREATE INDEX IF NOT EXISTS ix_trade_tick_public_pit
    ON market.trade_tick
       (feed_id, instrument_id, event_ts, available_at DESC,
        source_sequence, event_no, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_trade_tick_system_pit
    ON market.trade_tick
       (feed_id, instrument_id, event_ts, system_available_at DESC,
        source_sequence, event_no, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_order_book_snapshot_public_pit
    ON market.order_book_snapshot
       (feed_id, instrument_id, event_ts DESC, available_at DESC,
        source_sequence DESC, revision_no DESC);
CREATE INDEX IF NOT EXISTS ix_order_book_snapshot_system_pit
    ON market.order_book_snapshot
       (feed_id, instrument_id, event_ts DESC, system_available_at DESC,
        source_sequence DESC, revision_no DESC);

-- ---------------------------------------------------------------------------
-- Reproducible technical-backtest input binding and point-in-time decisions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS backtest.run_market_series (
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    bar_series_id       BIGINT NOT NULL REFERENCES market.bar_series(bar_series_id),
    series_role         VARCHAR(12) NOT NULL
                        CHECK (series_role IN ('SIGNAL','EXECUTION','VALUATION','BENCHMARK')),
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    execution_lag       INTERVAL NOT NULL DEFAULT INTERVAL '0',
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, bar_series_id, series_role),
    CHECK (execution_lag >= INTERVAL '0')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_run_market_series_primary_role
    ON backtest.run_market_series (run_id, series_role)
    WHERE is_primary;
CREATE INDEX IF NOT EXISTS ix_run_market_series_role
    ON backtest.run_market_series (run_id, series_role, bar_series_id);

COMMENT ON TABLE backtest.run_market_series IS
'Exact candle series used by a run; supports separate signal, execution, valuation and benchmark timeframes.';

CREATE TABLE IF NOT EXISTS backtest.decision_context (
    decision_context_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    decision_seq        BIGINT NOT NULL CHECK (decision_seq > 0),
    decision_ts         TIMESTAMPTZ(6) NOT NULL,
    strategy_state_sha256 CHAR(64),
    input_manifest_uri  TEXT,
    input_manifest_sha256 CHAR(64),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, decision_seq),
    UNIQUE (decision_context_id, run_id)
);

CREATE INDEX IF NOT EXISTS ix_decision_context_run_time
    ON backtest.decision_context (run_id, decision_ts, decision_seq);

ALTER TABLE backtest.signal
    ADD COLUMN IF NOT EXISTS decision_context_id BIGINT;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.signal'::regclass
          AND conname = 'fk_signal_decision_same_run'
    ) THEN
        ALTER TABLE backtest.signal
            ADD CONSTRAINT fk_signal_decision_same_run
            FOREIGN KEY (decision_context_id, run_id)
            REFERENCES backtest.decision_context(decision_context_id, run_id);
    END IF;
END
$constraints$;

CREATE INDEX IF NOT EXISTS ix_signal_decision_context
    ON backtest.signal (decision_context_id, signal_id)
    WHERE decision_context_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS backtest.decision_bar_input (
    decision_context_id BIGINT NOT NULL REFERENCES backtest.decision_context(decision_context_id)
                              ON DELETE CASCADE,
    input_no            INTEGER NOT NULL CHECK (input_no >= 0),
    input_role          VARCHAR(12) NOT NULL DEFAULT 'SIGNAL'
                        CHECK (input_role IN ('SIGNAL','FILTER','WARMUP','BENCHMARK')),
    bar_open_ts         TIMESTAMPTZ(6) NOT NULL,
    bar_series_id       BIGINT NOT NULL,
    bar_revision_no     INTEGER NOT NULL,
    bar_available_at    TIMESTAMPTZ(6) NOT NULL,
    effective_available_at TIMESTAMPTZ(6) NOT NULL,
    PRIMARY KEY (decision_context_id, input_no),
    UNIQUE (decision_context_id, bar_series_id, bar_open_ts, bar_revision_no),
    FOREIGN KEY (bar_open_ts, bar_series_id, bar_revision_no, bar_available_at)
        REFERENCES market.bar_revision(bar_open_ts, bar_series_id, revision_no, available_at)
);

CREATE INDEX IF NOT EXISTS ix_decision_bar_input_series_time
    ON backtest.decision_bar_input (decision_context_id, bar_series_id, bar_open_ts);

CREATE OR REPLACE FUNCTION backtest.enforce_decision_bar_input_pit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_run_id             BIGINT;
    v_decision_ts        TIMESTAMPTZ(6);
    v_cutoff_ts          TIMESTAMPTZ(6);
    v_mode               VARCHAR(24);
    v_public_available   TIMESTAMPTZ(6);
    v_system_available   TIMESTAMPTZ(6);
    v_effective          TIMESTAMPTZ(6);
BEGIN
    SELECT dc.run_id, dc.decision_ts, r.knowledge_cutoff_ts, r.availability_mode,
           b.available_at, b.system_available_at
      INTO v_run_id, v_decision_ts, v_cutoff_ts, v_mode,
           v_public_available, v_system_available
    FROM backtest.decision_context dc
    JOIN backtest.run r ON r.run_id = dc.run_id
    JOIN market.bar_revision b
      ON b.bar_open_ts = NEW.bar_open_ts
     AND b.bar_series_id = NEW.bar_series_id
     AND b.revision_no = NEW.bar_revision_no
     AND b.available_at = NEW.bar_available_at
    WHERE dc.decision_context_id = NEW.decision_context_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Decision context or exact bar revision does not exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM backtest.run_market_series rms
        WHERE rms.run_id = v_run_id AND rms.bar_series_id = NEW.bar_series_id
    ) THEN
        RAISE EXCEPTION 'Bar series % is not bound to run %', NEW.bar_series_id, v_run_id;
    END IF;

    v_effective := CASE WHEN v_mode = 'PUBLIC_REPLAY'
                        THEN v_public_available ELSE v_system_available END;

    IF v_effective > v_decision_ts OR v_effective > v_cutoff_ts THEN
        RAISE EXCEPTION
            'Point-in-time violation: input available %, decision %, snapshot cutoff %',
            v_effective, v_decision_ts, v_cutoff_ts;
    END IF;

    NEW.effective_available_at := v_effective;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_decision_bar_input_pit ON backtest.decision_bar_input;
CREATE TRIGGER trg_decision_bar_input_pit
BEFORE INSERT OR UPDATE ON backtest.decision_bar_input
FOR EACH ROW EXECUTE FUNCTION backtest.enforce_decision_bar_input_pit();

COMMENT ON TABLE backtest.decision_context IS
'One deterministic strategy evaluation instant; signals may share a decision context.';
COMMENT ON TABLE backtest.decision_bar_input IS
'Optional exact input audit. Its trigger rejects any bar revision unavailable at decision time or beyond the frozen snapshot cutoff.';

-- A run must point to a frozen snapshot, and its duplicated replay fields must match it.
CREATE OR REPLACE FUNCTION backtest.enforce_run_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_status catalog.data_snapshot.status%TYPE;
    v_cutoff TIMESTAMPTZ(6);
    v_mode   VARCHAR(24);
BEGIN
    SELECT status, knowledge_cutoff_ts, availability_mode
      INTO v_status, v_cutoff, v_mode
    FROM catalog.data_snapshot
    WHERE data_snapshot_id = NEW.data_snapshot_id;

    IF NOT FOUND OR v_status <> 'FROZEN' THEN
        RAISE EXCEPTION 'Backtest run requires a FROZEN data snapshot';
    END IF;

    IF NEW.knowledge_cutoff_ts <> v_cutoff OR NEW.availability_mode <> v_mode THEN
        RAISE EXCEPTION 'Run cutoff/availability mode must equal its data snapshot';
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.status <> 'QUEUED'
       AND (NEW.data_snapshot_id, NEW.knowledge_cutoff_ts, NEW.availability_mode)
           IS DISTINCT FROM
           (OLD.data_snapshot_id, OLD.knowledge_cutoff_ts, OLD.availability_mode) THEN
        RAISE EXCEPTION 'Replay boundary cannot change after a run leaves QUEUED state';
    END IF;

    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_run_frozen_snapshot ON backtest.run;
CREATE TRIGGER trg_run_frozen_snapshot
BEFORE INSERT OR UPDATE OF data_snapshot_id, knowledge_cutoff_ts, availability_mode
ON backtest.run
FOR EACH ROW EXECUTE FUNCTION backtest.enforce_run_snapshot();

-- ---------------------------------------------------------------------------
-- Auditable order/fill lifecycle and typed execution lineage
-- ---------------------------------------------------------------------------

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.signal'::regclass
          AND conname = 'uq_signal_run_signal_instrument'
    ) THEN
        ALTER TABLE backtest.signal
            ADD CONSTRAINT uq_signal_run_signal_instrument
            UNIQUE (run_id, signal_id, instrument_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.bt_order'::regclass
          AND conname = 'uq_order_run_order_instrument'
    ) THEN
        ALTER TABLE backtest.bt_order
            ADD CONSTRAINT uq_order_run_order_instrument
            UNIQUE (run_id, order_id, instrument_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.bt_order'::regclass
          AND conname = 'fk_order_signal_same_run_instrument'
    ) THEN
        ALTER TABLE backtest.bt_order
            ADD CONSTRAINT fk_order_signal_same_run_instrument
            FOREIGN KEY (run_id, signal_id, instrument_id)
            REFERENCES backtest.signal(run_id, signal_id, instrument_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.fill'::regclass
          AND conname = 'uq_fill_run_fill_order_instrument'
    ) THEN
        ALTER TABLE backtest.fill
            ADD CONSTRAINT uq_fill_run_fill_order_instrument
            UNIQUE (run_id, fill_id, order_id, instrument_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.fill'::regclass
          AND conname = 'uq_fill_run_fill_instrument'
    ) THEN
        ALTER TABLE backtest.fill
            ADD CONSTRAINT uq_fill_run_fill_instrument
            UNIQUE (run_id, fill_id, instrument_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.fill'::regclass
          AND conname = 'fk_fill_order_same_run_instrument'
    ) THEN
        ALTER TABLE backtest.fill
            ADD CONSTRAINT fk_fill_order_same_run_instrument
            FOREIGN KEY (run_id, order_id, instrument_id)
            REFERENCES backtest.bt_order(run_id, order_id, instrument_id);
    END IF;
END
$constraints$;

ALTER TABLE backtest.fill
    ADD COLUMN IF NOT EXISTS execution_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_fill_execution_key
    ON backtest.fill (run_id, execution_key)
    WHERE execution_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fill_run_time_id
    ON backtest.fill (run_id, fill_ts, fill_id);
CREATE INDEX IF NOT EXISTS ix_bt_order_open
    ON backtest.bt_order (run_id, instrument_id, submitted_at, order_id)
    WHERE status IN ('NEW','ACCEPTED','PARTIAL');

COMMENT ON COLUMN backtest.fill.execution_key IS
'Deterministic idempotency key generated by the execution engine; required by production writers.';

CREATE TABLE IF NOT EXISTS backtest.order_event (
    order_event_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL,
    order_id            BIGINT NOT NULL,
    instrument_id       BIGINT NOT NULL,
    event_seq           INTEGER NOT NULL CHECK (event_seq > 0),
    event_key           TEXT NOT NULL,
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    event_type          VARCHAR(20) NOT NULL
                        CHECK (event_type IN
                               ('SUBMITTED','ACCEPTED','TRIGGERED','PARTIAL_FILL','FILLED',
                                'CANCEL_REQUESTED','CANCELLED','REJECTED','EXPIRED','REPLACED')),
    status_after        VARCHAR(16) NOT NULL
                        CHECK (status_after IN
                               ('NEW','ACCEPTED','PARTIAL','FILLED','CANCELLED','REJECTED','EXPIRED')),
    source_fill_id      BIGINT,
    filled_quantity_delta NUMERIC(38,18),
    remaining_quantity NUMERIC(38,18),
    event_price         NUMERIC(38,18),
    reason_code         TEXT,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (order_id, event_seq),
    UNIQUE (run_id, event_key),
    FOREIGN KEY (run_id, order_id, instrument_id)
        REFERENCES backtest.bt_order(run_id, order_id, instrument_id),
    FOREIGN KEY (run_id, source_fill_id, order_id, instrument_id)
        REFERENCES backtest.fill(run_id, fill_id, order_id, instrument_id),
    CHECK (filled_quantity_delta IS NULL OR filled_quantity_delta >= 0),
    CHECK (remaining_quantity IS NULL OR remaining_quantity >= 0),
    CHECK ((event_type IN ('PARTIAL_FILL','FILLED') AND source_fill_id IS NOT NULL)
        OR event_type NOT IN ('PARTIAL_FILL','FILLED'))
);

CREATE INDEX IF NOT EXISTS ix_order_event_run_time
    ON backtest.order_event (run_id, event_ts, event_seq, order_event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_order_event_source_fill
    ON backtest.order_event (source_fill_id)
    WHERE source_fill_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS backtest.fill_market_reference (
    fill_id              BIGINT NOT NULL,
    run_id               BIGINT NOT NULL,
    instrument_id        BIGINT NOT NULL,
    reference_no         SMALLINT NOT NULL CHECK (reference_no >= 0),
    reference_role       VARCHAR(12) NOT NULL
                         CHECK (reference_role IN ('TRIGGER','PRICE','LIQUIDITY')),
    reference_type       VARCHAR(8) NOT NULL
                         CHECK (reference_type IN ('BAR','TICK','BOOK','QUOTE','MODEL')),
    bar_open_ts          TIMESTAMPTZ(6),
    bar_series_id        BIGINT,
    bar_revision_no      INTEGER,
    bar_available_at     TIMESTAMPTZ(6),
    tick_event_ts        TIMESTAMPTZ(6),
    tick_feed_id         BIGINT,
    tick_source_sequence BIGINT,
    tick_event_no        SMALLINT,
    tick_revision_no     INTEGER,
    book_event_ts        TIMESTAMPTZ(6),
    book_feed_id         BIGINT,
    book_source_sequence BIGINT,
    book_revision_no     INTEGER,
    quote_event_ts       TIMESTAMPTZ(6),
    quote_feed_id        BIGINT,
    quote_revision_no    INTEGER,
    quote_available_at   TIMESTAMPTZ(6),
    effective_available_at TIMESTAMPTZ(6),
    model_price          NUMERIC(38,18),
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (fill_id, reference_no),
    FOREIGN KEY (run_id, fill_id, instrument_id)
        REFERENCES backtest.fill(run_id, fill_id, instrument_id) ON DELETE CASCADE,
    FOREIGN KEY (bar_open_ts, bar_series_id, bar_revision_no, bar_available_at)
        REFERENCES market.bar_revision(bar_open_ts, bar_series_id, revision_no, available_at),
    FOREIGN KEY (tick_event_ts, tick_feed_id, instrument_id, tick_source_sequence,
                 tick_event_no, tick_revision_no)
        REFERENCES market.trade_tick(event_ts, feed_id, instrument_id, source_sequence,
                                     event_no, revision_no),
    FOREIGN KEY (book_event_ts, book_feed_id, instrument_id, book_source_sequence,
                 book_revision_no)
        REFERENCES market.order_book_snapshot(event_ts, feed_id, instrument_id,
                                              source_sequence, revision_no),
    FOREIGN KEY (quote_event_ts, quote_feed_id, instrument_id, quote_revision_no,
                 quote_available_at)
        REFERENCES market.quote_snapshot(event_ts, feed_id, instrument_id, revision_no,
                                         available_at),
    CHECK (
        (reference_type = 'BAR'
         AND num_nonnulls(bar_open_ts, bar_series_id, bar_revision_no, bar_available_at) = 4
         AND num_nonnulls(tick_event_ts, tick_feed_id, tick_source_sequence, tick_event_no,
                          tick_revision_no, book_event_ts, book_feed_id, book_source_sequence,
                          book_revision_no, quote_event_ts, quote_feed_id, quote_revision_no,
                          quote_available_at) = 0)
     OR (reference_type = 'TICK'
         AND num_nonnulls(tick_event_ts, tick_feed_id, tick_source_sequence, tick_event_no,
                          tick_revision_no) = 5
         AND num_nonnulls(bar_open_ts, bar_series_id, bar_revision_no, bar_available_at,
                          book_event_ts, book_feed_id, book_source_sequence, book_revision_no,
                          quote_event_ts, quote_feed_id, quote_revision_no, quote_available_at) = 0)
     OR (reference_type = 'BOOK'
         AND num_nonnulls(book_event_ts, book_feed_id, book_source_sequence, book_revision_no) = 4
         AND num_nonnulls(bar_open_ts, bar_series_id, bar_revision_no, bar_available_at,
                          tick_event_ts, tick_feed_id, tick_source_sequence, tick_event_no,
                          tick_revision_no, quote_event_ts, quote_feed_id, quote_revision_no,
                          quote_available_at) = 0)
     OR (reference_type = 'QUOTE'
         AND num_nonnulls(quote_event_ts, quote_feed_id, quote_revision_no, quote_available_at) = 4
         AND num_nonnulls(bar_open_ts, bar_series_id, bar_revision_no, bar_available_at,
                          tick_event_ts, tick_feed_id, tick_source_sequence, tick_event_no,
                          tick_revision_no, book_event_ts, book_feed_id, book_source_sequence,
                          book_revision_no) = 0)
     OR (reference_type = 'MODEL'
         AND num_nonnulls(bar_open_ts, bar_series_id, bar_revision_no, bar_available_at,
                          tick_event_ts, tick_feed_id, tick_source_sequence, tick_event_no,
                          tick_revision_no, book_event_ts, book_feed_id, book_source_sequence,
                          book_revision_no, quote_event_ts, quote_feed_id, quote_revision_no,
                          quote_available_at) = 0)
    )
);

CREATE INDEX IF NOT EXISTS ix_fill_market_reference_run_fill
    ON backtest.fill_market_reference (run_id, fill_id, reference_role);

CREATE OR REPLACE FUNCTION backtest.enforce_fill_market_reference_pit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_fill_ts           TIMESTAMPTZ(6);
    v_cutoff_ts         TIMESTAMPTZ(6);
    v_mode              VARCHAR(24);
    v_public_available  TIMESTAMPTZ(6);
    v_system_available  TIMESTAMPTZ(6);
    v_source_instrument BIGINT;
    v_effective         TIMESTAMPTZ(6);
BEGIN
    SELECT f.fill_ts, r.knowledge_cutoff_ts, r.availability_mode
      INTO v_fill_ts, v_cutoff_ts, v_mode
    FROM backtest.fill f
    JOIN backtest.run r ON r.run_id = f.run_id
    WHERE f.run_id = NEW.run_id
      AND f.fill_id = NEW.fill_id
      AND f.instrument_id = NEW.instrument_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Fill does not belong to the supplied run/instrument';
    END IF;

    IF NEW.reference_type = 'MODEL' THEN
        NEW.effective_available_at := NULL;
        RETURN NEW;
    ELSIF NEW.reference_type = 'BAR' THEN
        SELECT b.available_at, b.system_available_at, bs.instrument_id
          INTO v_public_available, v_system_available, v_source_instrument
        FROM market.bar_revision b
        JOIN market.bar_series bs ON bs.bar_series_id = b.bar_series_id
        WHERE b.bar_open_ts = NEW.bar_open_ts
          AND b.bar_series_id = NEW.bar_series_id
          AND b.revision_no = NEW.bar_revision_no
          AND b.available_at = NEW.bar_available_at;

        IF NOT EXISTS (
            SELECT 1 FROM backtest.run_market_series rms
            WHERE rms.run_id = NEW.run_id
              AND rms.bar_series_id = NEW.bar_series_id
              AND rms.series_role = 'EXECUTION'
        ) THEN
            RAISE EXCEPTION 'Execution bar series % is not bound to run %',
                            NEW.bar_series_id, NEW.run_id;
        END IF;
    ELSIF NEW.reference_type = 'TICK' THEN
        SELECT available_at, system_available_at, instrument_id
          INTO v_public_available, v_system_available, v_source_instrument
        FROM market.trade_tick
        WHERE event_ts = NEW.tick_event_ts
          AND feed_id = NEW.tick_feed_id
          AND instrument_id = NEW.instrument_id
          AND source_sequence = NEW.tick_source_sequence
          AND event_no = NEW.tick_event_no
          AND revision_no = NEW.tick_revision_no;
    ELSIF NEW.reference_type = 'BOOK' THEN
        SELECT available_at, system_available_at, instrument_id
          INTO v_public_available, v_system_available, v_source_instrument
        FROM market.order_book_snapshot
        WHERE event_ts = NEW.book_event_ts
          AND feed_id = NEW.book_feed_id
          AND instrument_id = NEW.instrument_id
          AND source_sequence = NEW.book_source_sequence
          AND revision_no = NEW.book_revision_no;
    ELSIF NEW.reference_type = 'QUOTE' THEN
        SELECT available_at, system_available_at, instrument_id
          INTO v_public_available, v_system_available, v_source_instrument
        FROM market.quote_snapshot
        WHERE event_ts = NEW.quote_event_ts
          AND feed_id = NEW.quote_feed_id
          AND instrument_id = NEW.instrument_id
          AND revision_no = NEW.quote_revision_no
          AND available_at = NEW.quote_available_at;
    END IF;

    IF NOT FOUND OR v_source_instrument <> NEW.instrument_id THEN
        RAISE EXCEPTION 'Exact market source does not exist or belongs to another instrument';
    END IF;

    v_effective := CASE WHEN v_mode = 'PUBLIC_REPLAY'
                        THEN v_public_available ELSE v_system_available END;

    IF v_effective > v_fill_ts OR v_effective > v_cutoff_ts THEN
        RAISE EXCEPTION
            'Execution look-ahead: source available %, fill %, snapshot cutoff %',
            v_effective, v_fill_ts, v_cutoff_ts;
    END IF;

    NEW.effective_available_at := v_effective;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_fill_market_reference_pit ON backtest.fill_market_reference;
CREATE TRIGGER trg_fill_market_reference_pit
BEFORE INSERT OR UPDATE ON backtest.fill_market_reference
FOR EACH ROW EXECUTE FUNCTION backtest.enforce_fill_market_reference_pit();

COMMENT ON TABLE backtest.order_event IS
'Immutable order lifecycle needed to audit trigger, partial-fill, cancel and rejection behavior.';
COMMENT ON TABLE backtest.fill_market_reference IS
'Typed exact source used to generate a simulated fill; replaces unauditable JSON-only execution lineage.';

-- Deferred accounting invariant: no overfill and terminal/partial statuses agree with fills.
CREATE OR REPLACE FUNCTION backtest.validate_order_fill_totals()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_order_id      BIGINT;
    v_order_qty     NUMERIC(38,18);
    v_filled_qty    NUMERIC(38,18);
    v_submitted_at  TIMESTAMPTZ(6);
    v_first_fill_ts TIMESTAMPTZ(6);
    v_status        VARCHAR(16);
BEGIN
    FOR v_order_id IN
        SELECT DISTINCT id
        FROM unnest(ARRAY[
            CASE WHEN TG_OP <> 'INSERT' THEN OLD.order_id ELSE NULL END,
            CASE WHEN TG_OP <> 'DELETE' THEN NEW.order_id ELSE NULL END
        ]) AS ids(id)
        WHERE id IS NOT NULL
    LOOP
        SELECT quantity, submitted_at, status
          INTO v_order_qty, v_submitted_at, v_status
        FROM backtest.bt_order
        WHERE order_id = v_order_id;

        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        SELECT COALESCE(sum(quantity), 0), min(fill_ts)
          INTO v_filled_qty, v_first_fill_ts
        FROM backtest.fill
        WHERE order_id = v_order_id;

        IF v_first_fill_ts IS NOT NULL AND v_first_fill_ts < v_submitted_at THEN
            RAISE EXCEPTION 'Order % has a fill before submission', v_order_id;
        END IF;
        IF v_filled_qty > v_order_qty THEN
            RAISE EXCEPTION 'Order % is overfilled: % > %',
                            v_order_id, v_filled_qty, v_order_qty;
        END IF;
        IF v_status = 'FILLED' AND v_filled_qty <> v_order_qty THEN
            RAISE EXCEPTION 'FILLED order % has filled quantity %, expected %',
                            v_order_id, v_filled_qty, v_order_qty;
        END IF;
        IF v_status = 'PARTIAL' AND NOT (v_filled_qty > 0 AND v_filled_qty < v_order_qty) THEN
            RAISE EXCEPTION 'PARTIAL order % has invalid filled quantity %',
                            v_order_id, v_filled_qty;
        END IF;
        IF v_status IN ('NEW','ACCEPTED','REJECTED') AND v_filled_qty <> 0 THEN
            RAISE EXCEPTION 'Order % status % is inconsistent with filled quantity %',
                            v_order_id, v_status, v_filled_qty;
        END IF;
    END LOOP;

    RETURN NULL;
END
$function$;

DROP TRIGGER IF EXISTS trg_fill_totals_from_fill ON backtest.fill;
CREATE CONSTRAINT TRIGGER trg_fill_totals_from_fill
AFTER INSERT OR UPDATE OR DELETE ON backtest.fill
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION backtest.validate_order_fill_totals();

DROP TRIGGER IF EXISTS trg_fill_totals_from_order ON backtest.bt_order;
CREATE CONSTRAINT TRIGGER trg_fill_totals_from_order
AFTER INSERT OR UPDATE OF quantity, submitted_at, status ON backtest.bt_order
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION backtest.validate_order_fill_totals();

-- ---------------------------------------------------------------------------
-- Position accounting and exact valuation lineage
-- ---------------------------------------------------------------------------

ALTER TABLE backtest.cash_ledger
    ADD COLUMN IF NOT EXISTS source_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_ledger_source_key
    ON backtest.cash_ledger (run_id, source_key)
    WHERE source_key IS NOT NULL;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'catalog.corporate_action'::regclass
          AND conname = 'uq_corporate_action_id_instrument'
    ) THEN
        ALTER TABLE catalog.corporate_action
            ADD CONSTRAINT uq_corporate_action_id_instrument
            UNIQUE (corporate_action_id, instrument_id);
    END IF;
END
$constraints$;

CREATE TABLE IF NOT EXISTS backtest.position_ledger (
    position_entry_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES backtest.run(run_id),
    instrument_id       BIGINT NOT NULL REFERENCES catalog.instrument(instrument_id),
    event_ts            TIMESTAMPTZ(6) NOT NULL,
    entry_type          VARCHAR(20) NOT NULL
                        CHECK (entry_type IN
                               ('INITIAL','FILL','SPLIT','STOCK_DIVIDEND','RIGHTS',
                                'EXPIRY','ASSIGNMENT','ADJUSTMENT')),
    quantity_delta      NUMERIC(38,18) NOT NULL CHECK (quantity_delta <> 0),
    unit_cost_base      NUMERIC(38,18),
    fill_id             BIGINT,
    corporate_action_id BIGINT,
    source_key          TEXT NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, source_key),
    FOREIGN KEY (run_id, fill_id, instrument_id)
        REFERENCES backtest.fill(run_id, fill_id, instrument_id),
    FOREIGN KEY (corporate_action_id, instrument_id)
        REFERENCES catalog.corporate_action(corporate_action_id, instrument_id),
    CHECK ((entry_type = 'FILL' AND fill_id IS NOT NULL)
        OR entry_type <> 'FILL'),
    CHECK ((entry_type IN ('SPLIT','STOCK_DIVIDEND','RIGHTS')
            AND corporate_action_id IS NOT NULL)
        OR entry_type NOT IN ('SPLIT','STOCK_DIVIDEND','RIGHTS'))
);

CREATE INDEX IF NOT EXISTS ix_position_ledger_run_time
    ON backtest.position_ledger (run_id, instrument_id, event_ts, position_entry_id);
CREATE INDEX IF NOT EXISTS ix_position_snapshot_run_time
    ON backtest.position_snapshot (run_id, snapshot_ts, instrument_id);

CREATE TABLE IF NOT EXISTS backtest.position_valuation_bar_reference (
    run_id              BIGINT NOT NULL,
    instrument_id       BIGINT NOT NULL,
    snapshot_ts         TIMESTAMPTZ(6) NOT NULL,
    bar_open_ts         TIMESTAMPTZ(6) NOT NULL,
    bar_series_id       BIGINT NOT NULL,
    bar_revision_no     INTEGER NOT NULL,
    bar_available_at    TIMESTAMPTZ(6) NOT NULL,
    effective_available_at TIMESTAMPTZ(6) NOT NULL,
    price_field         VARCHAR(16) NOT NULL
                        CHECK (price_field IN
                               ('OPEN','HIGH','LOW','CLOSE','OFFICIAL_CLOSE','SETTLEMENT','VWAP')),
    PRIMARY KEY (run_id, instrument_id, snapshot_ts),
    FOREIGN KEY (run_id, instrument_id, snapshot_ts)
        REFERENCES backtest.position_snapshot(run_id, instrument_id, snapshot_ts)
        ON DELETE CASCADE,
    FOREIGN KEY (bar_open_ts, bar_series_id, bar_revision_no, bar_available_at)
        REFERENCES market.bar_revision(bar_open_ts, bar_series_id, revision_no, available_at)
);

CREATE OR REPLACE FUNCTION backtest.enforce_position_valuation_pit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
DECLARE
    v_cutoff_ts        TIMESTAMPTZ(6);
    v_mode             VARCHAR(24);
    v_public_available TIMESTAMPTZ(6);
    v_system_available TIMESTAMPTZ(6);
    v_bar_instrument   BIGINT;
    v_effective        TIMESTAMPTZ(6);
BEGIN
    SELECT r.knowledge_cutoff_ts, r.availability_mode,
           b.available_at, b.system_available_at, bs.instrument_id
      INTO v_cutoff_ts, v_mode, v_public_available, v_system_available,
           v_bar_instrument
    FROM backtest.run r
    JOIN market.bar_revision b
      ON b.bar_open_ts = NEW.bar_open_ts
     AND b.bar_series_id = NEW.bar_series_id
     AND b.revision_no = NEW.bar_revision_no
     AND b.available_at = NEW.bar_available_at
    JOIN market.bar_series bs ON bs.bar_series_id = b.bar_series_id
    WHERE r.run_id = NEW.run_id;

    IF NOT FOUND OR v_bar_instrument <> NEW.instrument_id THEN
        RAISE EXCEPTION 'Valuation bar does not exist or belongs to another instrument';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM backtest.run_market_series rms
        WHERE rms.run_id = NEW.run_id
          AND rms.bar_series_id = NEW.bar_series_id
          AND rms.series_role = 'VALUATION'
    ) THEN
        RAISE EXCEPTION 'Valuation bar series % is not bound to run %',
                        NEW.bar_series_id, NEW.run_id;
    END IF;

    v_effective := CASE WHEN v_mode = 'PUBLIC_REPLAY'
                        THEN v_public_available ELSE v_system_available END;
    IF v_effective > NEW.snapshot_ts OR v_effective > v_cutoff_ts THEN
        RAISE EXCEPTION 'Position valuation uses future market data';
    END IF;

    NEW.effective_available_at := v_effective;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_position_valuation_pit
ON backtest.position_valuation_bar_reference;
CREATE TRIGGER trg_position_valuation_pit
BEFORE INSERT OR UPDATE ON backtest.position_valuation_bar_reference
FOR EACH ROW EXECUTE FUNCTION backtest.enforce_position_valuation_pit();

COMMENT ON TABLE backtest.position_ledger IS
'Signed quantity ledger. Fills and non-cash corporate actions can reconstruct every position independently of snapshots.';
COMMENT ON TABLE backtest.position_valuation_bar_reference IS
'Exact bar revision used to value each position snapshot, with point-in-time enforcement.';

-- Leveraged portfolios can lose more than initial equity, so max drawdown may exceed 1.
ALTER TABLE backtest.run_summary
    DROP CONSTRAINT IF EXISTS run_summary_max_drawdown_check;
ALTER TABLE backtest.run_summary
    ADD CONSTRAINT run_summary_max_drawdown_check
    CHECK (max_drawdown IS NULL OR max_drawdown >= 0);

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'backtest.run_summary'::regclass
          AND conname = 'ck_run_summary_trade_counts'
    ) THEN
        ALTER TABLE backtest.run_summary
            ADD CONSTRAINT ck_run_summary_trade_counts
            CHECK (winning_trade_count + losing_trade_count <= trade_count);
    END IF;
END
$constraints$;

-- ---------------------------------------------------------------------------
-- Monthly partition automation for the technical-data parents
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION market.create_technical_month_partitions(
    p_month DATE,
    p_bar_hash_buckets INTEGER DEFAULT 0
) RETURNS VOID
LANGUAGE plpgsql
AS $function$
DECLARE
    v_month          DATE := date_trunc('month', p_month)::date;
    v_next_month     DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::date;
    v_parent         TEXT;
    v_partition_name TEXT;
BEGIN
    PERFORM market.create_bar_month_partition(v_month, p_bar_hash_buckets);

    FOREACH v_parent IN ARRAY ARRAY[
        'trade_tick',
        'order_book_snapshot',
        'order_book_level',
        'order_book_delta',
        'quote_snapshot',
        'participant_flow_snapshot'
    ]
    LOOP
        v_partition_name := format('%s_y%sm%s', v_parent,
                                   to_char(v_month, 'YYYY'), to_char(v_month, 'MM'));
        IF to_regclass(format('market.%I', v_partition_name)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE market.%I PARTITION OF market.%I '
                'FOR VALUES FROM (%L) TO (%L)',
                v_partition_name, v_parent,
                (v_month::timestamp AT TIME ZONE 'UTC'),
                (v_next_month::timestamp AT TIME ZONE 'UTC')
            );
        END IF;
    END LOOP;
END
$function$;

COMMENT ON FUNCTION market.create_technical_month_partitions(DATE, INTEGER) IS
'Creates all monthly market partitions needed by technical backtests; bar HASH subpartitioning is optional.';

COMMIT;
