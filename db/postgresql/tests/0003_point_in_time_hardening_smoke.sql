-- Non-destructive integration smoke test for migration 0003.
-- Exercises temporal interval enforcement and point-in-time bar retrieval.
\set ON_ERROR_STOP on

BEGIN;

SELECT market.create_bar_month_partition(DATE '2042-01-01', 0);

CREATE TEMPORARY TABLE pit_smoke_context (
    bar_series_id BIGINT NOT NULL
) ON COMMIT DROP;

CREATE FUNCTION pg_temp.expect_overlap(
    p_statement TEXT,
    p_table_name TEXT,
    p_message_markers TEXT[]
) RETURNS VOID
LANGUAGE plpgsql
AS $function$
DECLARE
    v_message TEXT;
    v_marker TEXT;
BEGIN
    EXECUTE p_statement;
    RAISE EXCEPTION 'Expected temporal overlap rejection for %', p_table_name;
EXCEPTION
    WHEN SQLSTATE '23P01' THEN
        GET STACKED DIAGNOSTICS v_message = MESSAGE_TEXT;
        IF strpos(v_message, p_table_name) = 0 THEN
            RAISE EXCEPTION 'Overlap error did not identify table %: %',
                p_table_name, v_message;
        END IF;
        FOREACH v_marker IN ARRAY p_message_markers LOOP
            IF strpos(v_message, v_marker) = 0 THEN
                RAISE EXCEPTION 'Overlap error for % omitted marker %: %',
                    p_table_name, v_marker, v_message;
            END IF;
        END LOOP;
END
$function$;

-- SECURITY DEFINER guards must refuse attachment to any other relation.
CREATE TEMPORARY TABLE pit_wrong_trigger_target (dummy INTEGER);

CREATE TRIGGER test_wrong_identifier_guard
BEFORE INSERT ON pit_wrong_trigger_target
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_instrument_identifier_no_overlap();
DO $wrong_binding$
BEGIN
    INSERT INTO pit_wrong_trigger_target VALUES (1);
    RAISE EXCEPTION 'Identifier guard accepted a foreign trigger relation';
EXCEPTION WHEN SQLSTATE '0A000' THEN NULL;
END
$wrong_binding$;
DROP TRIGGER test_wrong_identifier_guard ON pit_wrong_trigger_target;

CREATE TRIGGER test_wrong_specification_guard
BEFORE INSERT ON pit_wrong_trigger_target
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_instrument_spec_version_no_overlap();
DO $wrong_binding$
BEGIN
    INSERT INTO pit_wrong_trigger_target VALUES (1);
    RAISE EXCEPTION 'Specification guard accepted a foreign trigger relation';
EXCEPTION WHEN SQLSTATE '0A000' THEN NULL;
END
$wrong_binding$;
DROP TRIGGER test_wrong_specification_guard ON pit_wrong_trigger_target;

CREATE TRIGGER test_wrong_universe_guard
BEFORE INSERT ON pit_wrong_trigger_target
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_universe_member_no_overlap();
DO $wrong_binding$
BEGIN
    INSERT INTO pit_wrong_trigger_target VALUES (1);
    RAISE EXCEPTION 'Universe guard accepted a foreign trigger relation';
EXCEPTION WHEN SQLSTATE '0A000' THEN NULL;
END
$wrong_binding$;
DROP TABLE pit_wrong_trigger_target;

DO $test$
DECLARE
    v_provider_id SMALLINT;
    v_feed_id BIGINT;
    v_timeframe_id SMALLINT;
    v_instrument_a BIGINT;
    v_instrument_b BIGINT;
    v_batch_id BIGINT;
    v_series_id BIGINT;
    v_adjustment_set_id BIGINT;
    v_adjusted_series_id BIGINT;
    v_universe_id BIGINT;
    v_count INTEGER;
    v_revision INTEGER;
    v_current_revision INTEGER;
    v_effective_at TIMESTAMPTZ(6);
    v_times TIMESTAMPTZ(6)[];
    v_invalid_indexes INTEGER;
    v_unvalidated_constraints INTEGER;
    v_table_count INTEGER;
    v_function_oid OID;
BEGIN
    INSERT INTO catalog.data_provider
        (provider_code, display_name, provider_kind, default_timezone)
    VALUES
        ('PIT03_SMOKE_PROVIDER', 'PIT hardening smoke provider',
         'INTERNAL', 'UTC')
    ON CONFLICT (provider_code) DO UPDATE
        SET display_name = EXCLUDED.display_name
    RETURNING provider_id INTO v_provider_id;

    INSERT INTO catalog.data_feed
        (provider_id, feed_code, display_name, data_kind,
         native_timezone, parser_version)
    VALUES
        (v_provider_id, 'PIT03_SMOKE_BAR', 'PIT hardening smoke bars',
         'BAR', 'UTC', 'pit03-smoke-v1')
    ON CONFLICT (provider_id, feed_code) DO UPDATE
        SET parser_version = EXCLUDED.parser_version
    RETURNING feed_id INTO v_feed_id;

    SELECT timeframe_id INTO STRICT v_timeframe_id
    FROM catalog.timeframe
    WHERE timeframe_code = '1m';

    INSERT INTO catalog.instrument
        (asset_type_code, quote_currency_code, canonical_symbol,
         display_name, status, active_from)
    VALUES
        ('EQUITY', 'IRR', 'PIT03_SMOKE_A', 'PIT smoke instrument A',
         'ACTIVE', TIMESTAMPTZ '2039-01-01 00:00:00+00')
    RETURNING instrument_id INTO v_instrument_a;

    INSERT INTO catalog.instrument
        (asset_type_code, quote_currency_code, canonical_symbol,
         display_name, status, active_from)
    VALUES
        ('EQUITY', 'IRR', 'PIT03_SMOKE_B', 'PIT smoke instrument B',
         'ACTIVE', TIMESTAMPTZ '2039-01-01 00:00:00+00')
    RETURNING instrument_id INTO v_instrument_b;

    INSERT INTO catalog.universe (universe_code, display_name)
    VALUES ('PIT03_SMOKE_UNIVERSE', 'PIT hardening smoke universe')
    RETURNING universe_id INTO v_universe_id;

    -- instrument_identifier: disjoint and adjacent intervals succeed.
    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'SUCCESS',
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00', v_instrument_a),
        (v_provider_id, 'PIT03', 'SUCCESS',
         TIMESTAMPTZ '2040-02-01 00:00:00+00',
         TIMESTAMPTZ '2040-03-01 00:00:00+00', v_instrument_a),
        (v_provider_id, 'PIT03', 'SUCCESS',
         TIMESTAMPTZ '2040-04-01 00:00:00+00',
         TIMESTAMPTZ '2040-05-01 00:00:00+00', v_instrument_a);

    SELECT count(*) INTO v_count
    FROM catalog.instrument_identifier
    WHERE provider_id = v_provider_id
      AND identifier_type = 'PIT03'
      AND identifier_value = 'SUCCESS';
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'instrument_identifier accepted interval count %, expected 3',
            v_count;
    END IF;

    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'PARTIAL',
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00', v_instrument_a);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_identifier '
            '(provider_id, identifier_type, identifier_value, valid_from, valid_to, instrument_id) '
            'VALUES (%L, %L, %L, %L::timestamptz, %L::timestamptz, %L)',
            v_provider_id, 'PIT03', 'PARTIAL',
            '2040-01-15 00:00:00+00', '2040-02-15 00:00:00+00',
            v_instrument_a
        ),
        'catalog.instrument_identifier',
        ARRAY['provider_id=' || v_provider_id::text,
              'identifier_type=PIT03', 'identifier_value=PARTIAL',
              '2040-01-15', '2040-02-15']
    );

    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'CONTAINED',
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00', v_instrument_a);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_identifier '
            '(provider_id, identifier_type, identifier_value, valid_from, valid_to, instrument_id) '
            'VALUES (%L, %L, %L, %L::timestamptz, %L::timestamptz, %L)',
            v_provider_id, 'PIT03', 'CONTAINED',
            '2040-02-01 00:00:00+00', '2040-03-01 00:00:00+00',
            v_instrument_a
        ),
        'catalog.instrument_identifier',
        ARRAY['provider_id=' || v_provider_id::text,
              'identifier_type=PIT03', 'identifier_value=CONTAINED',
              '2040-02-01', '2040-03-01']
    );

    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'IDENTICAL',
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00', v_instrument_a);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_identifier '
            '(provider_id, identifier_type, identifier_value, valid_from, valid_to, instrument_id) '
            'VALUES (%L, %L, %L, %L::timestamptz, %L::timestamptz, %L)',
            v_provider_id, 'PIT03', 'IDENTICAL',
            '2040-01-01 00:00:00+00', '2040-02-01 00:00:00+00',
            v_instrument_a
        ),
        'catalog.instrument_identifier',
        ARRAY['provider_id=' || v_provider_id::text,
              'identifier_type=PIT03', 'identifier_value=IDENTICAL',
              '2040-01-01', '2040-02-01']
    );

    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'OPEN',
         TIMESTAMPTZ '2040-01-01 00:00:00+00', NULL, v_instrument_a);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_identifier '
            '(provider_id, identifier_type, identifier_value, valid_from, valid_to, instrument_id) '
            'VALUES (%L, %L, %L, %L::timestamptz, %L::timestamptz, %L)',
            v_provider_id, 'PIT03', 'OPEN',
            '2040-06-01 00:00:00+00', '2040-07-01 00:00:00+00',
            v_instrument_a
        ),
        'catalog.instrument_identifier',
        ARRAY['provider_id=' || v_provider_id::text,
              'identifier_type=PIT03', 'identifier_value=OPEN',
              '2040-06-01', '2040-07-01']
    );

    -- A different logical key may reuse the exact interval.
    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'DIFFERENT_A',
         TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00', v_instrument_a),
        (v_provider_id, 'PIT03', 'DIFFERENT_B',
         TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00', v_instrument_b);

    INSERT INTO catalog.instrument_identifier
        (provider_id, identifier_type, identifier_value,
         valid_from, valid_to, instrument_id)
    VALUES
        (v_provider_id, 'PIT03', 'UPDATE',
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00', v_instrument_a),
        (v_provider_id, 'PIT03', 'UPDATE',
         TIMESTAMPTZ '2040-03-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00', v_instrument_a);
    PERFORM pg_temp.expect_overlap(
        format(
            'UPDATE catalog.instrument_identifier SET valid_from = %L::timestamptz '
            'WHERE provider_id = %L AND identifier_type = %L '
            'AND identifier_value = %L AND valid_from = %L::timestamptz',
            '2040-01-15 00:00:00+00', v_provider_id, 'PIT03', 'UPDATE',
            '2040-03-01 00:00:00+00'
        ),
        'catalog.instrument_identifier',
        ARRAY['provider_id=' || v_provider_id::text,
              'identifier_type=PIT03', 'identifier_value=UPDATE',
              '2040-01-15', '2040-04-01']
    );
    UPDATE catalog.instrument_identifier
    SET valid_from = TIMESTAMPTZ '2040-02-01 00:00:00+00'
    WHERE provider_id = v_provider_id
      AND identifier_type = 'PIT03'
      AND identifier_value = 'UPDATE'
      AND valid_from = TIMESTAMPTZ '2040-03-01 00:00:00+00';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Non-overlapping instrument_identifier update did not run';
    END IF;

    -- instrument_spec_version uses instrument_id as its logical key.
    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00'),
        (v_instrument_a, TIMESTAMPTZ '2040-02-01 00:00:00+00',
         TIMESTAMPTZ '2040-03-01 00:00:00+00'),
        (v_instrument_a, TIMESTAMPTZ '2040-04-01 00:00:00+00',
         TIMESTAMPTZ '2040-05-01 00:00:00+00');
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_spec_version '
            '(instrument_id, effective_from, effective_to) '
            'VALUES (%L, %L::timestamptz, %L::timestamptz)',
            v_instrument_a, '2040-01-15 00:00:00+00',
            '2040-02-15 00:00:00+00'
        ),
        'catalog.instrument_spec_version',
        ARRAY['instrument_id=' || v_instrument_a::text,
              '2040-01-15', '2040-02-15']
    );
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_spec_version '
            '(instrument_id, effective_from, effective_to) '
            'VALUES (%L, %L::timestamptz, %L::timestamptz)',
            v_instrument_a, '2040-02-01 00:00:00+00',
            '2040-03-01 00:00:00+00'
        ),
        'catalog.instrument_spec_version',
        ARRAY['instrument_id=' || v_instrument_a::text,
              '2040-02-01', '2040-03-01']
    );
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_spec_version '
            '(instrument_id, effective_from, effective_to) '
            'VALUES (%L, %L::timestamptz, %L::timestamptz)',
            v_instrument_a, '2040-01-01 00:00:00+00',
            '2040-02-01 00:00:00+00'
        ),
        'catalog.instrument_spec_version',
        ARRAY['instrument_id=' || v_instrument_a::text,
              '2040-01-01', '2040-02-01']
    );
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00', NULL);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.instrument_spec_version '
            '(instrument_id, effective_from, effective_to) '
            'VALUES (%L, %L::timestamptz, %L::timestamptz)',
            v_instrument_a, '2040-06-01 00:00:00+00',
            '2040-07-01 00:00:00+00'
        ),
        'catalog.instrument_spec_version',
        ARRAY['instrument_id=' || v_instrument_a::text,
              '2040-06-01', '2040-07-01']
    );
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00'),
        (v_instrument_b, TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00');
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id IN (v_instrument_a, v_instrument_b);

    INSERT INTO catalog.instrument_spec_version
        (instrument_id, effective_from, effective_to)
    VALUES
        (v_instrument_a, TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00'),
        (v_instrument_a, TIMESTAMPTZ '2040-03-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'UPDATE catalog.instrument_spec_version SET effective_from = %L::timestamptz '
            'WHERE instrument_id = %L AND effective_from = %L::timestamptz',
            '2040-01-15 00:00:00+00', v_instrument_a,
            '2040-03-01 00:00:00+00'
        ),
        'catalog.instrument_spec_version',
        ARRAY['instrument_id=' || v_instrument_a::text,
              '2040-01-15', '2040-04-01']
    );
    UPDATE catalog.instrument_spec_version
    SET effective_from = TIMESTAMPTZ '2040-02-01 00:00:00+00'
    WHERE instrument_id = v_instrument_a
      AND effective_from = TIMESTAMPTZ '2040-03-01 00:00:00+00';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Non-overlapping instrument_spec_version update did not run';
    END IF;
    DELETE FROM catalog.instrument_spec_version
    WHERE instrument_id = v_instrument_a;

    -- universe_member uses (universe_id, instrument_id) as its logical key.
    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00'),
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-02-01 00:00:00+00',
         TIMESTAMPTZ '2040-03-01 00:00:00+00'),
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-04-01 00:00:00+00',
         TIMESTAMPTZ '2040-05-01 00:00:00+00');
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.universe_member '
            '(universe_id, instrument_id, valid_from, valid_to) '
            'VALUES (%L, %L, %L::timestamptz, %L::timestamptz)',
            v_universe_id, v_instrument_a, '2040-01-15 00:00:00+00',
            '2040-02-15 00:00:00+00'
        ),
        'catalog.universe_member',
        ARRAY['universe_id=' || v_universe_id::text,
              'instrument_id=' || v_instrument_a::text,
              '2040-01-15', '2040-02-15']
    );
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.universe_member '
            '(universe_id, instrument_id, valid_from, valid_to) '
            'VALUES (%L, %L, %L::timestamptz, %L::timestamptz)',
            v_universe_id, v_instrument_a, '2040-02-01 00:00:00+00',
            '2040-03-01 00:00:00+00'
        ),
        'catalog.universe_member',
        ARRAY['universe_id=' || v_universe_id::text,
              'instrument_id=' || v_instrument_a::text,
              '2040-02-01', '2040-03-01']
    );
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.universe_member '
            '(universe_id, instrument_id, valid_from, valid_to) '
            'VALUES (%L, %L, %L::timestamptz, %L::timestamptz)',
            v_universe_id, v_instrument_a, '2040-01-01 00:00:00+00',
            '2040-02-01 00:00:00+00'
        ),
        'catalog.universe_member',
        ARRAY['universe_id=' || v_universe_id::text,
              'instrument_id=' || v_instrument_a::text,
              '2040-01-01', '2040-02-01']
    );
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00', NULL);
    PERFORM pg_temp.expect_overlap(
        format(
            'INSERT INTO catalog.universe_member '
            '(universe_id, instrument_id, valid_from, valid_to) '
            'VALUES (%L, %L, %L::timestamptz, %L::timestamptz)',
            v_universe_id, v_instrument_a, '2040-06-01 00:00:00+00',
            '2040-07-01 00:00:00+00'
        ),
        'catalog.universe_member',
        ARRAY['universe_id=' || v_universe_id::text,
              'instrument_id=' || v_instrument_a::text,
              '2040-06-01', '2040-07-01']
    );
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00'),
        (v_universe_id, v_instrument_b,
         TIMESTAMPTZ '2040-08-01 00:00:00+00',
         TIMESTAMPTZ '2040-09-01 00:00:00+00');
    DELETE FROM catalog.universe_member
    WHERE universe_id = v_universe_id;

    INSERT INTO catalog.universe_member
        (universe_id, instrument_id, valid_from, valid_to)
    VALUES
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-01-01 00:00:00+00',
         TIMESTAMPTZ '2040-02-01 00:00:00+00'),
        (v_universe_id, v_instrument_a,
         TIMESTAMPTZ '2040-03-01 00:00:00+00',
         TIMESTAMPTZ '2040-04-01 00:00:00+00');
    PERFORM pg_temp.expect_overlap(
        format(
            'UPDATE catalog.universe_member SET valid_from = %L::timestamptz '
            'WHERE universe_id = %L AND instrument_id = %L '
            'AND valid_from = %L::timestamptz',
            '2040-01-15 00:00:00+00', v_universe_id, v_instrument_a,
            '2040-03-01 00:00:00+00'
        ),
        'catalog.universe_member',
        ARRAY['universe_id=' || v_universe_id::text,
              'instrument_id=' || v_instrument_a::text,
              '2040-01-15', '2040-04-01']
    );
    UPDATE catalog.universe_member
    SET valid_from = TIMESTAMPTZ '2040-02-01 00:00:00+00'
    WHERE universe_id = v_universe_id
      AND instrument_id = v_instrument_a
      AND valid_from = TIMESTAMPTZ '2040-03-01 00:00:00+00';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Non-overlapping universe_member update did not run';
    END IF;

    -- Deterministic point-in-time bar fixtures.
    INSERT INTO ingest.ingestion_batch
        (feed_id, request_id, status, received_row_count, accepted_row_count,
         rejected_row_count, parser_version, finished_at)
    VALUES
        (v_feed_id, 'pit03-smoke-2042-01', 'SUCCEEDED', 10, 10, 0,
         'pit03-smoke-v1', TIMESTAMPTZ '2042-01-10 15:00:00+00')
    RETURNING ingestion_batch_id INTO v_batch_id;

    INSERT INTO market.bar_series
        (feed_id, instrument_id, timeframe_id, price_basis, close_semantics)
    VALUES
        (v_feed_id, v_instrument_a, v_timeframe_id, 'RAW', 'LAST_TRADE')
    RETURNING bar_series_id INTO v_series_id;

    INSERT INTO pit_smoke_context (bar_series_id) VALUES (v_series_id);

    INSERT INTO catalog.adjustment_set
        (instrument_id, method_code, version_no, knowledge_cutoff_ts)
    VALUES
        (v_instrument_b, 'SPLIT_ADJUSTED', 1,
         TIMESTAMPTZ '2042-01-10 12:00:00+00')
    RETURNING adjustment_set_id INTO v_adjustment_set_id;

    INSERT INTO market.bar_series
        (feed_id, instrument_id, timeframe_id, price_basis,
         adjustment_set_id, close_semantics)
    VALUES
        (v_feed_id, v_instrument_b, v_timeframe_id, 'SPLIT_ADJUSTED',
         v_adjustment_set_id, 'LAST_TRADE')
    RETURNING bar_series_id INTO v_adjusted_series_id;

    INSERT INTO market.bar_revision
        (bar_open_ts, bar_series_id, revision_no, available_at,
         system_available_at, bar_close_ts, trading_date,
         open_price, high_price, low_price, close_price,
         volume, trade_count, is_final, ingestion_batch_id)
    VALUES
        -- Original plus a late correction.
        (TIMESTAMPTZ '2042-01-10 10:00:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:01:00+00',
         TIMESTAMPTZ '2042-01-10 10:01:05+00',
         TIMESTAMPTZ '2042-01-10 10:01:00+00', DATE '2042-01-10',
         100, 102, 99, 101, 1000, 10, TRUE, v_batch_id),
        (TIMESTAMPTZ '2042-01-10 10:00:00+00', v_series_id, 2,
         TIMESTAMPTZ '2042-01-10 12:00:00+00',
         TIMESTAMPTZ '2042-01-10 12:05:00+00',
         TIMESTAMPTZ '2042-01-10 10:01:00+00', DATE '2042-01-10',
         100, 103, 99, 102, 1100, 11, TRUE, v_batch_id),
        -- Publicly available before the local system observed it.
        (TIMESTAMPTZ '2042-01-10 10:01:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:02:00+00',
         TIMESTAMPTZ '2042-01-10 11:30:00+00',
         TIMESTAMPTZ '2042-01-10 10:02:00+00', DATE '2042-01-10',
         101, 104, 100, 103, 1200, 12, TRUE, v_batch_id),
        -- Incorrectly early availability cannot expose an incomplete bar.
        (TIMESTAMPTZ '2042-01-10 10:02:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:03:00+00',
         TIMESTAMPTZ '2042-01-10 10:03:00+00',
         TIMESTAMPTZ '2042-01-10 11:30:00+00', DATE '2042-01-10',
         103, 105, 102, 104, 1300, 13, FALSE, v_batch_id),
        -- No eligible revision at the main cutoff.
        (TIMESTAMPTZ '2042-01-10 10:03:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 14:00:00+00',
         TIMESTAMPTZ '2042-01-10 14:00:00+00',
         TIMESTAMPTZ '2042-01-10 10:04:00+00', DATE '2042-01-10',
         104, 106, 103, 105, 1400, 14, TRUE, v_batch_id),
        -- Same eligibility timestamp: revision_no is the deterministic winner.
        (TIMESTAMPTZ '2042-01-10 10:04:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:05:00+00', DATE '2042-01-10',
         200, 202, 199, 200, 1500, 15, TRUE, v_batch_id),
        (TIMESTAMPTZ '2042-01-10 10:04:00+00', v_series_id, 2,
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:05:00+00', DATE '2042-01-10',
         200, 203, 199, 201, 1600, 16, TRUE, v_batch_id),
        (TIMESTAMPTZ '2042-01-10 10:05:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:06:00+00',
         TIMESTAMPTZ '2042-01-10 10:06:00+00', DATE '2042-01-10',
         201, 204, 200, 202, 1700, 17, TRUE, v_batch_id),
        -- A completed but explicitly non-final revision is not replay-safe.
        (TIMESTAMPTZ '2042-01-10 10:06:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:08:00+00',
         TIMESTAMPTZ '2042-01-10 10:08:00+00',
         TIMESTAMPTZ '2042-01-10 10:07:00+00', DATE '2042-01-10',
         202, 205, 201, 203, 1750, 18, FALSE, v_batch_id),
        -- Exactly at the exclusive range end.
        (TIMESTAMPTZ '2042-01-10 10:10:00+00', v_series_id, 1,
         TIMESTAMPTZ '2042-01-10 10:11:00+00',
         TIMESTAMPTZ '2042-01-10 10:11:00+00',
         TIMESTAMPTZ '2042-01-10 10:11:00+00', DATE '2042-01-10',
         203, 206, 202, 204, 1800, 19, TRUE, v_batch_id);

    -- Original revision is visible before the correction cutoff.
    SELECT revision_no, effective_available_at
    INTO STRICT v_revision, v_effective_at
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:00:00+00',
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_revision <> 1
       OR v_effective_at <> TIMESTAMPTZ '2042-01-10 10:01:00+00' THEN
        RAISE EXCEPTION 'Expected public original revision 1 at 11:00, got % at %',
            v_revision, v_effective_at;
    END IF;

    -- The late correction becomes visible only after its own cutoff.
    SELECT revision_no INTO STRICT v_revision
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:00:00+00',
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 13:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_revision <> 2 THEN
        RAISE EXCEPTION 'Expected late public correction revision 2, got %', v_revision;
    END IF;

    -- PUBLIC_REPLAY and ACTUAL_SYSTEM_REPLAY intentionally differ.
    SELECT count(*), min(effective_available_at)
    INTO v_count, v_effective_at
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 10:02:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_count <> 1
       OR v_effective_at <> TIMESTAMPTZ '2042-01-10 10:02:00+00' THEN
        RAISE EXCEPTION 'PUBLIC_REPLAY did not expose the publicly available bar';
    END IF;

    SELECT count(*) INTO v_count
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 10:02:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'ACTUAL_SYSTEM_REPLAY'
    );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'ACTUAL_SYSTEM_REPLAY leaked a row before system availability';
    END IF;

    SELECT count(*), min(effective_available_at)
    INTO v_count, v_effective_at
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 10:02:00+00',
        TIMESTAMPTZ '2042-01-10 12:00:00+00',
        'ACTUAL_SYSTEM_REPLAY'
    );
    IF v_count <> 1
       OR v_effective_at <> TIMESTAMPTZ '2042-01-10 11:30:00+00' THEN
        RAISE EXCEPTION 'ACTUAL_SYSTEM_REPLAY did not expose the row at system cutoff';
    END IF;

    -- Future/incomplete bars are absent even with an early availability timestamp.
    SELECT count(*) INTO v_count
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:02:00+00',
        TIMESTAMPTZ '2042-01-10 10:03:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'bars_as_of returned a bar before bar_close_ts';
    END IF;

    -- A logical bar with no eligible revision is absent.
    SELECT count(*) INTO v_count
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:03:00+00',
        TIMESTAMPTZ '2042-01-10 10:04:00+00',
        TIMESTAMPTZ '2042-01-10 13:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'bars_as_of returned a revision unavailable at cutoff';
    END IF;

    -- Equal availability timestamps select the highest revision deterministically.
    SELECT count(*), min(revision_no)
    INTO v_count, v_revision
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:04:00+00',
        TIMESTAMPTZ '2042-01-10 10:05:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_count <> 1 OR v_revision <> 2 THEN
        RAISE EXCEPTION 'Expected one deterministic winner revision 2, got count %, rev %',
            v_count, v_revision;
    END IF;

    -- Half-open boundaries, one row per logical bar, and chronological order.
    SELECT array_agg(bar_open_ts), count(*)
    INTO v_times, v_count
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:00:00+00',
        TIMESTAMPTZ '2042-01-10 10:10:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_count <> 4 OR v_times <> ARRAY[
        TIMESTAMPTZ '2042-01-10 10:00:00+00',
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 10:04:00+00',
        TIMESTAMPTZ '2042-01-10 10:05:00+00'
    ]::TIMESTAMPTZ[] THEN
        RAISE EXCEPTION 'Unexpected PIT range/order result: count %, times %',
            v_count, v_times;
    END IF;

    SELECT count(*) INTO v_count
    FROM (
        SELECT bar_open_ts
        FROM market.bars_as_of(
            v_series_id,
            TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:10:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00',
            'PUBLIC_REPLAY'
        )
        GROUP BY bar_open_ts
        HAVING count(*) > 1
    ) duplicate_bars;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'bars_as_of returned duplicate logical bars';
    END IF;

    -- current_bar exposes the latest correction, while historical PIT does not.
    SELECT revision_no INTO STRICT v_current_revision
    FROM market.current_bar
    WHERE bar_series_id = v_series_id
      AND bar_open_ts = TIMESTAMPTZ '2042-01-10 10:00:00+00';
    SELECT revision_no INTO STRICT v_revision
    FROM market.bars_as_of(
        v_series_id,
        TIMESTAMPTZ '2042-01-10 10:00:00+00',
        TIMESTAMPTZ '2042-01-10 10:01:00+00',
        TIMESTAMPTZ '2042-01-10 11:00:00+00',
        'PUBLIC_REPLAY'
    );
    IF v_current_revision <> 2 OR v_revision <> 1 THEN
        RAISE EXCEPTION 'Expected current_bar rev 2 and historical bars_as_of rev 1';
    END IF;

    -- Required parameter and semantic validation uses deterministic SQLSTATEs.
    BEGIN
        PERFORM * FROM market.bars_as_of(
            NULL, TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected NULL series rejection';
    EXCEPTION WHEN SQLSTATE '22004' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, NULL,
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected NULL from timestamp rejection';
    EXCEPTION WHEN SQLSTATE '22004' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:00:00+00', NULL,
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected NULL to timestamp rejection';
    EXCEPTION WHEN SQLSTATE '22004' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00', NULL, 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected NULL cutoff rejection';
    EXCEPTION WHEN SQLSTATE '22004' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', NULL);
        RAISE EXCEPTION 'Expected NULL replay mode rejection';
    EXCEPTION WHEN SQLSTATE '22004' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected empty range rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:02:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected reversed range rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 12:00:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected range-after-cutoff rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '-infinity',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected non-finite timestamp rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_series_id, TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'UNKNOWN_REPLAY');
        RAISE EXCEPTION 'Expected unknown replay mode rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            9223372036854775807,
            TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected unknown bar series rejection';
    EXCEPTION WHEN SQLSTATE 'P0002' THEN NULL;
    END;
    BEGIN
        PERFORM * FROM market.bars_as_of(
            v_adjusted_series_id,
            TIMESTAMPTZ '2042-01-10 10:00:00+00',
            TIMESTAMPTZ '2042-01-10 10:01:00+00',
            TIMESTAMPTZ '2042-01-10 11:00:00+00', 'PUBLIC_REPLAY');
        RAISE EXCEPTION 'Expected future adjustment provenance rejection';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;

    -- Catalog/regression assertions for the hardened schema.
    IF to_regprocedure(
        'catalog.enforce_instrument_identifier_no_overlap()'
    ) IS NULL OR to_regprocedure(
        'catalog.enforce_instrument_spec_version_no_overlap()'
    ) IS NULL OR to_regprocedure(
        'catalog.enforce_universe_member_no_overlap()'
    ) IS NULL THEN
        RAISE EXCEPTION 'One or more temporal overlap trigger functions are missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) privilege
        WHERE p.oid IN (
            to_regprocedure(
                'catalog.enforce_instrument_identifier_no_overlap()'
            ),
            to_regprocedure(
                'catalog.enforce_instrument_spec_version_no_overlap()'
            ),
            to_regprocedure(
                'catalog.enforce_universe_member_no_overlap()'
            )
        )
          AND privilege.grantee = 0
          AND privilege.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'Temporal SECURITY DEFINER guards are executable by PUBLIC';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE (n.nspname, c.relname, t.tgname) IN (
        ('catalog', 'instrument_identifier',
         'trg_instrument_identifier_no_overlap'),
        ('catalog', 'instrument_spec_version',
         'trg_instrument_spec_version_no_overlap'),
        ('catalog', 'universe_member',
         'trg_universe_member_no_overlap')
    )
      AND NOT t.tgisinternal
      AND t.tgenabled <> 'D';
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'Expected 3 enabled overlap triggers, found %', v_count;
    END IF;

    v_function_oid := to_regprocedure(
        'market.bars_as_of(bigint,timestamp with time zone,'
        'timestamp with time zone,timestamp with time zone,character varying)'
    );
    IF v_function_oid IS NULL THEN
        RAISE EXCEPTION 'market.bars_as_of function is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc WHERE oid = v_function_oid AND provolatile = 's'
    ) THEN
        RAISE EXCEPTION 'market.bars_as_of must be STABLE';
    END IF;
    IF COALESCE(obj_description(v_function_oid, 'pg_proc'), '')
       NOT ILIKE '%look-ahead%' THEN
        RAISE EXCEPTION 'market.bars_as_of anti-look-ahead comment is missing';
    END IF;
    IF COALESCE(
        obj_description('market.current_bar'::regclass, 'pg_class'), ''
    ) NOT ILIKE '%historical%' THEN
        RAISE EXCEPTION 'market.current_bar historical replay warning is missing';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'market'
      AND (
          (
              c.relname = 'ix_bar_revision_range_pit'
              AND pg_get_indexdef(i.indexrelid) LIKE
                  '%(bar_series_id, bar_open_ts, available_at DESC, revision_no DESC)%'
          )
          OR (
              c.relname = 'ix_bar_revision_system_pit'
              AND pg_get_indexdef(i.indexrelid) LIKE
                  '%(bar_series_id, bar_open_ts, system_available_at DESC, revision_no DESC)%'
          )
      )
      AND i.indisvalid;
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'Required public/system PIT indexes are missing or invalid';
    END IF;

    SELECT count(*) INTO v_invalid_indexes
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog', 'ingest', 'market',
                        'external', 'backtest', 'ml')
      AND NOT i.indisvalid;
    IF v_invalid_indexes <> 0 THEN
        RAISE EXCEPTION 'Found % invalid indexes', v_invalid_indexes;
    END IF;

    SELECT count(*) INTO v_unvalidated_constraints
    FROM pg_constraint c
    JOIN pg_namespace n ON n.oid = c.connamespace
    WHERE n.nspname IN ('catalog', 'ingest', 'market',
                        'external', 'backtest', 'ml')
      AND NOT c.convalidated;
    IF v_unvalidated_constraints <> 0 THEN
        RAISE EXCEPTION 'Found % unvalidated constraints',
            v_unvalidated_constraints;
    END IF;

    SELECT count(*) INTO v_table_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog', 'ingest', 'market',
                        'external', 'backtest', 'ml')
      AND c.relkind IN ('r', 'p');
    IF v_table_count < 72 THEN
        RAISE EXCEPTION 'Expected at least 72 tables, found %', v_table_count;
    END IF;
END
$test$;

-- Exercise the audited function with runtime/buffer reporting. The explicit
-- index-friendly equivalent exposes the underlying path because PL/pgSQL
-- functions otherwise appear as a single Function Scan node.
SET LOCAL enable_seqscan = off;
SET LOCAL jit = off;

-- Machine-check that each replay branch actually selects the child of its
-- corresponding partitioned PIT index. The printed plans below remain useful
-- diagnostic evidence in CI logs.
DO $plan_assertion$
DECLARE
    v_series_id BIGINT;
    v_public_index TEXT;
    v_system_index TEXT;
    v_public_plan JSON;
    v_system_plan JSON;
BEGIN
    SELECT bar_series_id INTO STRICT v_series_id
    FROM pit_smoke_context;

    SELECT child.relname INTO STRICT v_public_index
    FROM pg_inherits inheritance
    JOIN pg_class child ON child.oid = inheritance.inhrelid
    JOIN pg_index child_metadata
      ON child_metadata.indexrelid = child.oid
    WHERE inheritance.inhparent =
          'market.ix_bar_revision_range_pit'::regclass
      AND child_metadata.indrelid =
          'market.bar_revision_y2042m01'::regclass;

    SELECT child.relname INTO STRICT v_system_index
    FROM pg_inherits inheritance
    JOIN pg_class child ON child.oid = inheritance.inhrelid
    JOIN pg_index child_metadata
      ON child_metadata.indexrelid = child.oid
    WHERE inheritance.inhparent =
          'market.ix_bar_revision_system_pit'::regclass
      AND child_metadata.indrelid =
          'market.bar_revision_y2042m01'::regclass;

    EXECUTE format(
        $query$
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT DISTINCT ON (b.bar_open_ts)
               b.bar_open_ts, b.revision_no, b.available_at
        FROM market.bar_revision b
        WHERE b.bar_series_id = %s
          AND b.bar_open_ts >= TIMESTAMPTZ '2042-01-10 10:00:00+00'
          AND b.bar_open_ts < TIMESTAMPTZ '2042-01-10 10:10:00+00'
          AND b.bar_close_ts <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
          AND b.available_at <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
          AND b.is_final
        ORDER BY b.bar_open_ts, b.available_at DESC, b.revision_no DESC
        $query$,
        v_series_id
    ) INTO v_public_plan;

    EXECUTE format(
        $query$
        EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT DISTINCT ON (b.bar_open_ts)
               b.bar_open_ts, b.revision_no, b.system_available_at
        FROM market.bar_revision b
        WHERE b.bar_series_id = %s
          AND b.bar_open_ts >= TIMESTAMPTZ '2042-01-10 10:00:00+00'
          AND b.bar_open_ts < TIMESTAMPTZ '2042-01-10 10:10:00+00'
          AND b.bar_close_ts <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
          AND b.system_available_at <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
          AND b.is_final
        ORDER BY b.bar_open_ts, b.system_available_at DESC, b.revision_no DESC
        $query$,
        v_series_id
    ) INTO v_system_plan;

    IF strpos(v_public_plan::TEXT, format('"Index Name": "%s"', v_public_index)) = 0
       OR strpos(v_public_plan::TEXT, '"Node Type": "Index Scan"') = 0 THEN
        RAISE EXCEPTION 'PUBLIC_REPLAY plan did not use expected index %: %',
            v_public_index, v_public_plan;
    END IF;

    IF strpos(v_system_plan::TEXT, format('"Index Name": "%s"', v_system_index)) = 0
       OR strpos(v_system_plan::TEXT, '"Node Type": "Index Scan"') = 0 THEN
        RAISE EXCEPTION 'ACTUAL_SYSTEM_REPLAY plan did not use expected index %: %',
            v_system_index, v_system_plan;
    END IF;
END
$plan_assertion$;

EXPLAIN (ANALYZE, BUFFERS)
SELECT *
FROM market.bars_as_of(
    (SELECT bar_series_id FROM pit_smoke_context),
    TIMESTAMPTZ '2042-01-10 10:00:00+00',
    TIMESTAMPTZ '2042-01-10 10:10:00+00',
    TIMESTAMPTZ '2042-01-10 11:00:00+00',
    'PUBLIC_REPLAY'
);

EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT ON (b.bar_open_ts)
       b.bar_open_ts, b.revision_no, b.available_at
FROM market.bar_revision b
WHERE b.bar_series_id = (SELECT bar_series_id FROM pit_smoke_context)
  AND b.bar_open_ts >= TIMESTAMPTZ '2042-01-10 10:00:00+00'
  AND b.bar_open_ts < TIMESTAMPTZ '2042-01-10 10:10:00+00'
  AND b.bar_close_ts <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
  AND b.available_at <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
  AND b.is_final
ORDER BY b.bar_open_ts, b.available_at DESC, b.revision_no DESC;

EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT ON (b.bar_open_ts)
       b.bar_open_ts, b.revision_no, b.system_available_at
FROM market.bar_revision b
WHERE b.bar_series_id = (SELECT bar_series_id FROM pit_smoke_context)
  AND b.bar_open_ts >= TIMESTAMPTZ '2042-01-10 10:00:00+00'
  AND b.bar_open_ts < TIMESTAMPTZ '2042-01-10 10:10:00+00'
  AND b.bar_close_ts <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
  AND b.system_available_at <= TIMESTAMPTZ '2042-01-10 11:00:00+00'
  AND b.is_final
ORDER BY b.bar_open_ts, b.system_available_at DESC, b.revision_no DESC;

RESET enable_seqscan;

ROLLBACK;
