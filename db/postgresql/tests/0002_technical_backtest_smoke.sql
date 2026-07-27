-- Non-destructive smoke test for 001_core_schema.sql +
-- 003_technical_backtest_completion.sql.
\set ON_ERROR_STOP on

BEGIN;

SELECT market.create_technical_month_partitions(DATE '2035-01-01', 4);

DO $test$
DECLARE
    v_provider_id       SMALLINT;
    v_feed_id           BIGINT;
    v_venue_id          SMALLINT;
    v_timeframe_id      SMALLINT;
    v_instrument_id     BIGINT;
    v_batch_id          BIGINT;
    v_series_id         BIGINT;
    v_universe_id       BIGINT;
    v_snapshot_id       BIGINT;
    v_strategy_id       BIGINT;
    v_strategy_version  BIGINT;
    v_run_id            BIGINT;
    v_decision_id       BIGINT;
    v_signal_id         BIGINT;
    v_order_id          BIGINT;
    v_fill_id           BIGINT;
    v_effective_at      TIMESTAMPTZ(6);
    v_table_count       INTEGER;
    v_invalid_indexes   INTEGER;
    v_unvalidated       INTEGER;
BEGIN
    INSERT INTO catalog.data_provider
        (provider_code, display_name, provider_kind, default_timezone)
    VALUES ('SMOKE_BRSAPI', 'Smoke BrsApi', 'VENDOR', 'Asia/Tehran')
    ON CONFLICT (provider_code) DO UPDATE SET display_name = EXCLUDED.display_name
    RETURNING provider_id INTO v_provider_id;

    INSERT INTO catalog.data_feed
        (provider_id, feed_code, display_name, data_kind, native_timezone, parser_version)
    VALUES (v_provider_id, 'TECHNICAL_SMOKE', 'Technical smoke feed',
            'OTHER', 'Asia/Tehran', 'smoke-v1')
    ON CONFLICT (provider_id, feed_code) DO UPDATE
        SET parser_version = EXCLUDED.parser_version
    RETURNING feed_id INTO v_feed_id;

    INSERT INTO catalog.venue
        (venue_code, display_name, country_code, timezone_name, base_currency_code)
    VALUES ('SMOKE_TSE', 'Smoke venue', 'IR', 'Asia/Tehran', 'IRR')
    ON CONFLICT (venue_code) DO UPDATE SET display_name = EXCLUDED.display_name
    RETURNING venue_id INTO v_venue_id;

    SELECT timeframe_id INTO v_timeframe_id
    FROM catalog.timeframe WHERE timeframe_code = '1m';

    INSERT INTO catalog.instrument
        (asset_type_code, venue_id, quote_currency_code, canonical_symbol,
         display_name, status, active_from)
    VALUES ('EQUITY', v_venue_id, 'IRR', 'SMOKE', 'Smoke instrument',
            'ACTIVE', TIMESTAMPTZ '2030-01-01 00:00:00+00')
    RETURNING instrument_id INTO v_instrument_id;

    INSERT INTO ingest.ingestion_batch
        (feed_id, request_id, status, received_row_count, accepted_row_count,
         rejected_row_count, parser_version, finished_at)
    VALUES (v_feed_id, 'smoke-2035-01', 'SUCCEEDED', 10, 10, 0,
            'smoke-v1', CURRENT_TIMESTAMP)
    RETURNING ingestion_batch_id INTO v_batch_id;

    INSERT INTO market.bar_series
        (feed_id, instrument_id, timeframe_id, price_basis, close_semantics)
    VALUES (v_feed_id, v_instrument_id, v_timeframe_id, 'RAW', 'LAST_TRADE')
    RETURNING bar_series_id INTO v_series_id;

    INSERT INTO catalog.universe (universe_code, display_name)
    VALUES ('SMOKE_UNIVERSE', 'Smoke universe')
    RETURNING universe_id INTO v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from)
    VALUES (v_universe_id, v_instrument_id,
            TIMESTAMPTZ '2030-01-01 00:00:00+00');

    INSERT INTO catalog.data_snapshot
        (snapshot_code, knowledge_cutoff_ts, availability_mode,
         manifest_sha256, status, frozen_at)
    VALUES ('SMOKE_SNAPSHOT', TIMESTAMPTZ '2035-01-31 23:59:59+00',
            'PUBLIC_REPLAY', repeat('a', 64), 'FROZEN', CURRENT_TIMESTAMP)
    RETURNING data_snapshot_id INTO v_snapshot_id;

    INSERT INTO market.bar_revision
        (bar_open_ts, bar_series_id, revision_no, available_at,
         system_available_at, bar_close_ts, trading_date,
         open_price, high_price, low_price, close_price,
         official_close_price, previous_close_price, volume, trade_count,
         is_final, ingestion_batch_id)
    VALUES
        (TIMESTAMPTZ '2035-01-15 06:00:00+00', v_series_id, 1,
         TIMESTAMPTZ '2035-01-15 06:01:00+00',
         TIMESTAMPTZ '2035-01-15 06:01:01+00',
         TIMESTAMPTZ '2035-01-15 06:01:00+00', DATE '2035-01-15',
         100, 103, 99, 102, 101.5, 98, 10000, 25, TRUE, v_batch_id),
        (TIMESTAMPTZ '2035-01-15 06:01:00+00', v_series_id, 1,
         TIMESTAMPTZ '2035-01-15 06:03:00+00',
         TIMESTAMPTZ '2035-01-15 06:03:01+00',
         TIMESTAMPTZ '2035-01-15 06:02:00+00', DATE '2035-01-15',
         102, 104, 101, 103, 102.5, 101.5, 8000, 20, TRUE, v_batch_id);

    INSERT INTO market.quote_snapshot
        (event_ts, feed_id, instrument_id, revision_no, available_at,
         system_available_at, trading_date, source_state, normalized_state,
         lower_price_limit, upper_price_limit, previous_close_price,
         open_price, session_low_price, session_high_price, last_price,
         official_close_price, base_volume, trade_count, volume, turnover_value,
         is_final, ingestion_batch_id)
    VALUES
        (TIMESTAMPTZ '2035-01-15 06:01:30+00', v_feed_id, v_instrument_id, 1,
         TIMESTAMPTZ '2035-01-15 06:01:30+00',
         TIMESTAMPTZ '2035-01-15 06:01:31+00', DATE '2035-01-15',
         'مجاز', 'OPEN', 90, 110, 98, 100, 99, 103, 102, 101.5,
         1000, 25, 10000, 1015000, FALSE, v_batch_id);

    INSERT INTO market.participant_flow_snapshot
        (event_ts, feed_id, instrument_id, revision_no, available_at,
         system_available_at, trading_date, window_start_ts, aggregation_kind,
         individual_buy_count, individual_sell_count, legal_buy_count,
         legal_sell_count, individual_buy_volume, individual_sell_volume,
         legal_buy_volume, legal_sell_volume, is_final, ingestion_batch_id)
    VALUES
        (TIMESTAMPTZ '2035-01-15 06:01:30+00', v_feed_id, v_instrument_id, 1,
         TIMESTAMPTZ '2035-01-15 06:01:30+00',
         TIMESTAMPTZ '2035-01-15 06:01:31+00', DATE '2035-01-15',
         TIMESTAMPTZ '2035-01-15 05:30:00+00', 'SESSION_TO_DATE',
         10, 8, 2, 3, 7000, 6000, 3000, 4000, FALSE, v_batch_id);

    INSERT INTO backtest.strategy (strategy_code, display_name)
    VALUES ('SMOKE_TECHNICAL', 'Smoke technical strategy')
    RETURNING strategy_id INTO v_strategy_id;

    INSERT INTO backtest.strategy_version
        (strategy_id, version_no, class_path, code_sha256)
    VALUES (v_strategy_id, 1, 'smoke.Strategy', repeat('b', 64))
    RETURNING strategy_version_id INTO v_strategy_version;

    INSERT INTO backtest.run
        (run_code, strategy_version_id, data_snapshot_id, universe_id,
         timeframe_id, base_currency_code, event_from, event_to,
         knowledge_cutoff_ts, availability_mode, initial_capital,
         parameters, parameter_sha256, engine_version, random_seed, status)
    VALUES
        ('SMOKE_RUN', v_strategy_version, v_snapshot_id, v_universe_id,
         v_timeframe_id, 'IRR', TIMESTAMPTZ '2035-01-15 00:00:00+00',
         TIMESTAMPTZ '2035-01-16 00:00:00+00',
         TIMESTAMPTZ '2035-01-31 23:59:59+00', 'PUBLIC_REPLAY', 1000000,
         '{}'::jsonb, repeat('c', 64), 'smoke-v1', 42, 'QUEUED')
    RETURNING run_id INTO v_run_id;

    INSERT INTO backtest.run_instrument (run_id, instrument_id)
    VALUES (v_run_id, v_instrument_id);

    INSERT INTO backtest.run_market_series
        (run_id, bar_series_id, series_role, is_primary)
    VALUES
        (v_run_id, v_series_id, 'SIGNAL', TRUE),
        (v_run_id, v_series_id, 'EXECUTION', TRUE),
        (v_run_id, v_series_id, 'VALUATION', TRUE);

    INSERT INTO backtest.decision_context
        (run_id, decision_seq, decision_ts, strategy_state_sha256)
    VALUES (v_run_id, 1, TIMESTAMPTZ '2035-01-15 06:02:00+00', repeat('d', 64))
    RETURNING decision_context_id INTO v_decision_id;

    INSERT INTO backtest.decision_bar_input
        (decision_context_id, input_no, input_role, bar_open_ts,
         bar_series_id, bar_revision_no, bar_available_at)
    VALUES
        (v_decision_id, 0, 'SIGNAL', TIMESTAMPTZ '2035-01-15 06:00:00+00',
         v_series_id, 1, TIMESTAMPTZ '2035-01-15 06:01:00+00');

    SELECT effective_available_at INTO v_effective_at
    FROM backtest.decision_bar_input
    WHERE decision_context_id = v_decision_id AND input_no = 0;

    IF v_effective_at <> TIMESTAMPTZ '2035-01-15 06:01:00+00' THEN
        RAISE EXCEPTION 'Decision PIT trigger stored an unexpected eligibility time';
    END IF;

    BEGIN
        INSERT INTO backtest.decision_bar_input
            (decision_context_id, input_no, input_role, bar_open_ts,
             bar_series_id, bar_revision_no, bar_available_at)
        VALUES
            (v_decision_id, 1, 'SIGNAL', TIMESTAMPTZ '2035-01-15 06:01:00+00',
             v_series_id, 1, TIMESTAMPTZ '2035-01-15 06:03:00+00');
        RAISE EXCEPTION 'Future bar input unexpectedly succeeded';
    EXCEPTION WHEN OTHERS THEN
        IF position('Point-in-time violation' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
    END;

    INSERT INTO backtest.signal
        (run_id, instrument_id, signal_ts, signal_type, direction,
         target_quantity, decision_context_id)
    VALUES (v_run_id, v_instrument_id,
            TIMESTAMPTZ '2035-01-15 06:02:00+00', 'ENTRY', 'LONG', 10,
            v_decision_id)
    RETURNING signal_id INTO v_signal_id;

    INSERT INTO backtest.bt_order
        (run_id, signal_id, instrument_id, client_order_key, submitted_at,
         side, order_type, time_in_force, quantity, status)
    VALUES
        (v_run_id, v_signal_id, v_instrument_id, 'smoke-order-1',
         TIMESTAMPTZ '2035-01-15 06:02:01+00', 'BUY', 'MARKET', 'DAY', 10,
         'FILLED')
    RETURNING order_id INTO v_order_id;

    INSERT INTO backtest.fill
        (run_id, order_id, instrument_id, fill_ts, price, quantity,
         fee_currency_code, execution_key)
    VALUES
        (v_run_id, v_order_id, v_instrument_id,
         TIMESTAMPTZ '2035-01-15 06:02:02+00', 102, 10, 'IRR',
         'smoke-fill-1')
    RETURNING fill_id INTO v_fill_id;

    INSERT INTO backtest.order_event
        (run_id, order_id, instrument_id, event_seq, event_key, event_ts,
         event_type, status_after, remaining_quantity)
    VALUES
        (v_run_id, v_order_id, v_instrument_id, 1, 'smoke-order-1:submitted',
         TIMESTAMPTZ '2035-01-15 06:02:01+00', 'SUBMITTED', 'NEW', 10);

    INSERT INTO backtest.order_event
        (run_id, order_id, instrument_id, event_seq, event_key, event_ts,
         event_type, status_after, source_fill_id, filled_quantity_delta,
         remaining_quantity, event_price)
    VALUES
        (v_run_id, v_order_id, v_instrument_id, 2, 'smoke-order-1:filled',
         TIMESTAMPTZ '2035-01-15 06:02:02+00', 'FILLED', 'FILLED',
         v_fill_id, 10, 0, 102);

    INSERT INTO backtest.fill_market_reference
        (fill_id, run_id, instrument_id, reference_no, reference_role,
         reference_type, bar_open_ts, bar_series_id, bar_revision_no,
         bar_available_at, model_price)
    VALUES
        (v_fill_id, v_run_id, v_instrument_id, 0, 'PRICE', 'BAR',
         TIMESTAMPTZ '2035-01-15 06:00:00+00', v_series_id, 1,
         TIMESTAMPTZ '2035-01-15 06:01:00+00', 102);

    INSERT INTO backtest.position_ledger
        (run_id, instrument_id, event_ts, entry_type, quantity_delta,
         unit_cost_base, fill_id, source_key)
    VALUES
        (v_run_id, v_instrument_id, TIMESTAMPTZ '2035-01-15 06:02:02+00',
         'FILL', 10, 102, v_fill_id, 'position:smoke-fill-1');

    INSERT INTO backtest.position_snapshot
        (run_id, instrument_id, snapshot_ts, quantity, average_cost,
         market_price, market_value_base)
    VALUES
        (v_run_id, v_instrument_id, TIMESTAMPTZ '2035-01-15 06:02:03+00',
         10, 102, 102, 1020);

    INSERT INTO backtest.position_valuation_bar_reference
        (run_id, instrument_id, snapshot_ts, bar_open_ts, bar_series_id,
         bar_revision_no, bar_available_at, price_field)
    VALUES
        (v_run_id, v_instrument_id, TIMESTAMPTZ '2035-01-15 06:02:03+00',
         TIMESTAMPTZ '2035-01-15 06:00:00+00', v_series_id, 1,
         TIMESTAMPTZ '2035-01-15 06:01:00+00', 'CLOSE');

    INSERT INTO backtest.cash_ledger
        (run_id, entry_ts, currency_code, entry_type, amount, fill_id, source_key)
    VALUES
        (v_run_id, TIMESTAMPTZ '2035-01-15 06:02:02+00', 'IRR',
         'TRADE_NOTIONAL', -1020, v_fill_id, 'cash:smoke-fill-1');

    INSERT INTO backtest.run_summary
        (run_id, max_drawdown, trade_count, winning_trade_count,
         losing_trade_count, calculation_version)
    VALUES (v_run_id, 1.25, 1, 1, 0, 'smoke-v1');

    SELECT count(*) INTO v_table_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog','ingest','market','external','backtest','ml')
      AND c.relkind IN ('r','p');

    IF v_table_count < 72 THEN
        RAISE EXCEPTION 'Expected at least 72 tables, found %', v_table_count;
    END IF;

    SELECT count(*) INTO v_invalid_indexes
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog','ingest','market','external','backtest','ml')
      AND NOT i.indisvalid;

    IF v_invalid_indexes <> 0 THEN
        RAISE EXCEPTION 'Found % invalid indexes', v_invalid_indexes;
    END IF;

    SELECT count(*) INTO v_unvalidated
    FROM pg_constraint c
    JOIN pg_namespace n ON n.oid = c.connamespace
    WHERE n.nspname IN ('catalog','ingest','market','external','backtest','ml')
      AND NOT c.convalidated;

    IF v_unvalidated <> 0 THEN
        RAISE EXCEPTION 'Found % unvalidated constraints', v_unvalidated;
    END IF;
END
$test$;

-- Force deferred fill/order invariants before the final rollback.
SET CONSTRAINTS ALL IMMEDIATE;

PREPARE smoke_quote_asof (
    BIGINT, BIGINT, TIMESTAMPTZ, TIMESTAMPTZ, VARCHAR
) AS
WITH eligible AS (
    SELECT q.*,
           row_number() OVER (
               PARTITION BY q.feed_id, q.instrument_id, q.event_ts
               ORDER BY CASE WHEN $5 = 'PUBLIC_REPLAY'
                             THEN q.available_at ELSE q.system_available_at END DESC,
                        q.revision_no DESC
           ) AS revision_rank
    FROM market.quote_snapshot q
    WHERE q.feed_id = $1
      AND q.instrument_id = $2
      AND q.event_ts < $3
      AND CASE WHEN $5 = 'PUBLIC_REPLAY'
               THEN q.available_at ELSE q.system_available_at END <= $4
)
SELECT event_ts, normalized_state, lower_price_limit, upper_price_limit,
       previous_close_price, last_price, official_close_price,
       trade_count, volume, turnover_value
FROM eligible
WHERE revision_rank = 1
ORDER BY event_ts;

EXPLAIN EXECUTE smoke_quote_asof(
    1, 1,
    TIMESTAMPTZ '2035-02-01 00:00:00+00',
    TIMESTAMPTZ '2035-01-31 23:59:59+00',
    'PUBLIC_REPLAY'
);

DEALLOCATE ALL;
ROLLBACK;
