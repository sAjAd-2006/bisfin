-- Non-destructive structural/query smoke test. Run after 001_core_schema.sql.
-- The transaction is rolled back, including the temporary sample partition.
\set ON_ERROR_STOP on

BEGIN;

DO $test$
DECLARE
    v_table_count INTEGER;
    v_invalid_index_count INTEGER;
BEGIN
    SELECT count(*) INTO v_table_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog','ingest','market','external','backtest','ml')
      AND c.relkind IN ('r','p');

    IF v_table_count < 60 THEN
        RAISE EXCEPTION 'Expected at least 60 base/partitioned tables, found %', v_table_count;
    END IF;

    SELECT count(*) INTO v_invalid_index_count
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog','ingest','market','external','backtest','ml')
      AND NOT i.indisvalid;

    IF v_invalid_index_count <> 0 THEN
        RAISE EXCEPTION 'Found % invalid indexes', v_invalid_index_count;
    END IF;
END
$test$;

SELECT market.create_bar_month_partition(DATE '2035-01-01', 4);

PREPARE smoke_backtest_bars (
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, TIMESTAMPTZ, TIMESTAMPTZ, INTERVAL
) AS
WITH selected_series AS (
    SELECT bs.bar_series_id
    FROM market.bar_series bs
    JOIN catalog.instrument i ON i.instrument_id = bs.instrument_id
    JOIN catalog.venue v ON v.venue_id = i.venue_id
    JOIN catalog.timeframe tf ON tf.timeframe_id = bs.timeframe_id
    JOIN catalog.data_feed f ON f.feed_id = bs.feed_id
    JOIN catalog.data_provider p ON p.provider_id = f.provider_id
    WHERE v.venue_code = $1
      AND i.canonical_symbol = $2
      AND tf.timeframe_code = $3
      AND p.provider_code = $4
      AND f.feed_code = $5
      AND bs.price_basis = $6
), eligible AS (
    SELECT b.*,
           CASE WHEN ds.availability_mode = 'PUBLIC_REPLAY'
                THEN b.available_at ELSE b.system_available_at END AS eligible_at,
           row_number() OVER (
               PARTITION BY b.bar_series_id, b.bar_open_ts
               ORDER BY CASE WHEN ds.availability_mode = 'PUBLIC_REPLAY'
                             THEN b.available_at ELSE b.system_available_at END DESC,
                        b.revision_no DESC
           ) AS revision_rank
    FROM market.bar_revision b
    JOIN selected_series ss USING (bar_series_id)
    JOIN catalog.data_snapshot ds
      ON ds.data_snapshot_id = $7 AND ds.status = 'FROZEN'
    WHERE b.bar_open_ts >= $8
      AND b.bar_open_ts < $9
      AND b.is_final
      AND CASE WHEN ds.availability_mode = 'PUBLIC_REPLAY'
               THEN b.available_at ELSE b.system_available_at END
          <= LEAST(b.bar_close_ts + $10, ds.knowledge_cutoff_ts)
)
SELECT bar_open_ts, bar_close_ts, open_price, high_price, low_price, close_price,
       volume, revision_no, eligible_at
FROM eligible
WHERE revision_rank = 1
ORDER BY bar_open_ts;

PREPARE smoke_lstm_window (BIGINT, BIGINT, INTEGER, TEXT) AS
SELECT s.sample_id,
       jsonb_agg(
           jsonb_build_object(
               'step', st.step_no,
               'ohlcv', jsonb_build_array(b.open_price, b.high_price, b.low_price,
                                          b.close_price, b.volume),
               'features', to_jsonb(fv.values)
           ) ORDER BY st.step_no
       ) AS x,
       COALESCE(to_jsonb(lv.value_float), to_jsonb(lv.value_integer),
                to_jsonb(lv.value_text), lv.value_json) AS y
FROM ml.dataset_sample s
JOIN ml.dataset_version dv USING (dataset_version_id)
JOIN ml.dataset_sample_step st
  ON st.dataset_version_id = s.dataset_version_id AND st.sample_id = s.sample_id
JOIN market.bar_revision b
  ON b.bar_open_ts = st.bar_open_ts AND b.bar_series_id = st.bar_series_id
 AND b.revision_no = st.bar_revision_no AND b.available_at = st.bar_available_at
JOIN ml.feature_vector fv
  ON fv.event_ts = st.feature_event_ts
 AND fv.feature_set_version_id = st.feature_set_version_id
 AND fv.instrument_id = st.instrument_id AND fv.timeframe_id = st.timeframe_id
 AND fv.available_at = st.feature_available_at AND fv.revision_no = st.feature_revision_no
JOIN ml.label_value lv
  ON lv.anchor_ts = s.label_anchor_ts AND lv.label_definition_id = s.label_definition_id
 AND lv.instrument_id = s.instrument_id AND lv.timeframe_id = s.timeframe_id
 AND lv.available_at = s.label_available_at AND lv.revision_no = s.label_revision_no
JOIN ml.dataset_sample_assignment a
  ON a.dataset_version_id = s.dataset_version_id AND a.sample_id = s.sample_id
JOIN ml.dataset_split sp
  ON sp.dataset_version_id = a.dataset_version_id AND sp.split_id = a.split_id
WHERE s.dataset_version_id = $1 AND s.sample_id = $2
  AND sp.fold_no = $3 AND sp.split_role = $4
  AND s.expected_steps = 20
  AND CASE WHEN dv.availability_mode = 'PUBLIC_REPLAY'
           THEN fv.available_at ELSE fv.system_available_at END <= s.prediction_ts
GROUP BY s.sample_id, lv.value_float, lv.value_integer, lv.value_text, lv.value_json,
         s.expected_steps
HAVING count(*) = 20 AND min(st.step_no) = 0 AND max(st.step_no) = 19;

PREPARE smoke_strategy_report (TEXT, INTEGER) AS
SELECT s.strategy_code, sv.version_no, r.run_id,
       rs.sharpe_ratio, rs.sortino_ratio, rs.max_drawdown, rs.win_rate,
       custom.metrics
FROM backtest.strategy s
JOIN backtest.strategy_version sv USING (strategy_id)
JOIN backtest.run r USING (strategy_version_id)
LEFT JOIN backtest.run_summary rs USING (run_id)
LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(rm.metric_name || ':' || rm.scope_key, rm.metric_value) AS metrics
    FROM backtest.run_metric rm WHERE rm.run_id = r.run_id
) custom ON TRUE
WHERE s.strategy_code = $1
  AND r.status = 'SUCCEEDED'
  AND ($2 IS NULL OR sv.version_no = $2)
ORDER BY rs.sharpe_ratio DESC NULLS LAST;

EXPLAIN EXECUTE smoke_backtest_bars(
    'TSE','TEST','1m','provider','bars','RAW',1,
    TIMESTAMPTZ '2026-01-01 00:00:00+00',
    TIMESTAMPTZ '2026-02-01 00:00:00+00', INTERVAL '0 seconds'
);
EXPLAIN EXECUTE smoke_lstm_window(1, 1, 0, 'TRAIN');
EXPLAIN EXECUTE smoke_strategy_report('strategy', NULL);

DEALLOCATE ALL;
ROLLBACK;
