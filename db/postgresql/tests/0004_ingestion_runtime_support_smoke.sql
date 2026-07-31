-- Non-destructive smoke test for migration 0004.
-- Exercises raw-event monthly partition creation, routing, and catalog health.
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL TIME ZONE 'UTC';

DO $test$
DECLARE
    v_provider_id            SMALLINT;
    v_feed_id                BIGINT;
    v_batch_id               BIGINT;
    v_first_partition_oid    OID;
    v_second_partition_oid   OID;
    v_routed_partition_oid   OID;
    v_child_index_count      INTEGER;
    v_invalid_index_count    INTEGER;
    v_unvalidated_constraint_count INTEGER;
BEGIN
    BEGIN
        PERFORM ingest.create_raw_event_month_partition(NULL);
        RAISE EXCEPTION 'Expected NULL partition month to be rejected';
    EXCEPTION
        WHEN SQLSTATE '22004' THEN NULL;
    END;

    PERFORM ingest.create_raw_event_month_partition(DATE '2198-02-17');
    v_first_partition_oid := pg_catalog.to_regclass(
        'ingest.raw_event_y2198m02'
    );
    IF v_first_partition_oid IS NULL THEN
        RAISE EXCEPTION 'Expected ingest.raw_event_y2198m02 to be created';
    END IF;

    PERFORM ingest.create_raw_event_month_partition(DATE '2198-02-01');
    v_second_partition_oid := pg_catalog.to_regclass(
        'ingest.raw_event_y2198m02'
    );
    IF v_second_partition_oid IS DISTINCT FROM v_first_partition_oid THEN
        RAISE EXCEPTION
            'Repeated partition call changed relation OID from % to %',
            v_first_partition_oid, v_second_partition_oid;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS child
        JOIN pg_catalog.pg_inherits AS inheritance
          ON inheritance.inhrelid = child.oid
        WHERE child.oid = v_first_partition_oid
          AND child.relispartition
          AND inheritance.inhparent = 'ingest.raw_event'::REGCLASS
    ) THEN
        RAISE EXCEPTION
            'ingest.raw_event_y2198m02 is not attached to ingest.raw_event';
    END IF;

    IF pg_catalog.to_regclass('ingest.raw_event_y2198m03') IS NOT NULL THEN
        RAISE EXCEPTION
            'Reserved uncreated-month partition ingest.raw_event_y2198m03 already exists';
    END IF;

    INSERT INTO catalog.data_provider
        (provider_code, display_name, provider_kind, default_timezone)
    VALUES
        ('RAW04_SMOKE_PROVIDER', 'Raw-event partition smoke provider',
         'INTERNAL', 'UTC')
    ON CONFLICT (provider_code) DO UPDATE
        SET display_name = EXCLUDED.display_name
    RETURNING provider_id INTO v_provider_id;

    INSERT INTO catalog.data_feed
        (provider_id, feed_code, display_name, data_kind,
         native_timezone, parser_version)
    VALUES
        (v_provider_id, 'RAW04_SMOKE_FEED',
         'Raw-event partition smoke feed', 'BAR', 'UTC', 'raw04-smoke-v1')
    ON CONFLICT (provider_id, feed_code) DO UPDATE
        SET parser_version = EXCLUDED.parser_version
    RETURNING feed_id INTO v_feed_id;

    INSERT INTO ingest.ingestion_batch
        (feed_id, request_id, parser_version)
    VALUES
        (v_feed_id, 'RAW04_SMOKE_REQUEST', 'raw04-smoke-v1')
    RETURNING ingestion_batch_id INTO v_batch_id;

    INSERT INTO ingest.raw_event
        (ingested_at, ingestion_batch_id, feed_id, source_record_key,
         source_date_text, payload_sha256, raw_payload)
    VALUES
        (TIMESTAMPTZ '2198-02-01 00:00:00+00',
         v_batch_id, v_feed_id, 'raw04|month-start', '2198-02-01',
         repeat('a', 64), '{"boundary":"month_start"}'::JSONB)
    RETURNING tableoid INTO v_routed_partition_oid;
    IF v_routed_partition_oid <> v_first_partition_oid THEN
        RAISE EXCEPTION 'Month-start row routed to unexpected relation %',
            v_routed_partition_oid::REGCLASS;
    END IF;

    INSERT INTO ingest.raw_event
        (ingested_at, ingestion_batch_id, feed_id, source_record_key,
         source_date_text, payload_sha256, raw_payload)
    VALUES
        (TIMESTAMPTZ '2198-02-28 23:59:59.999999+00',
         v_batch_id, v_feed_id, 'raw04|month-end', '2198-02-28',
         repeat('b', 64), '{"boundary":"month_end"}'::JSONB)
    RETURNING tableoid INTO v_routed_partition_oid;
    IF v_routed_partition_oid <> v_first_partition_oid THEN
        RAISE EXCEPTION 'Month-end row routed to unexpected relation %',
            v_routed_partition_oid::REGCLASS;
    END IF;

    BEGIN
        INSERT INTO ingest.raw_event
            (ingested_at, ingestion_batch_id, feed_id, source_record_key,
             source_date_text, payload_sha256, raw_payload)
        VALUES
            (TIMESTAMPTZ '2198-03-01 00:00:00+00',
             v_batch_id, v_feed_id, 'raw04|outside', '2198-03-01',
             repeat('c', 64), '{"boundary":"outside"}'::JSONB);
        RAISE EXCEPTION 'Expected an uncreated month to reject raw-event insertion';
    EXCEPTION
        WHEN SQLSTATE '23514' THEN NULL;
    END;

    SELECT count(*),
           count(*) FILTER (
               WHERE NOT child_metadata.indisvalid
                  OR NOT child_metadata.indisready
           )
      INTO v_child_index_count,
           v_invalid_index_count
      FROM pg_catalog.pg_inherits AS inheritance
      JOIN pg_catalog.pg_index AS parent_metadata
        ON parent_metadata.indexrelid = inheritance.inhparent
      JOIN pg_catalog.pg_index AS child_metadata
        ON child_metadata.indexrelid = inheritance.inhrelid
     WHERE parent_metadata.indrelid = 'ingest.raw_event'::REGCLASS
       AND child_metadata.indrelid = v_first_partition_oid;

    IF v_child_index_count <> 4 THEN
        RAISE EXCEPTION
            'Expected 4 inherited raw-event indexes, found %',
            v_child_index_count;
    END IF;
    IF v_invalid_index_count <> 0 THEN
        RAISE EXCEPTION
            'Found % invalid or unready raw-event partition indexes',
            v_invalid_index_count;
    END IF;

    SELECT count(*)
      INTO v_unvalidated_constraint_count
      FROM pg_catalog.pg_constraint AS constraint_record
     WHERE constraint_record.conrelid = v_first_partition_oid
       AND NOT constraint_record.convalidated;
    IF v_unvalidated_constraint_count <> 0 THEN
        RAISE EXCEPTION
            'Found % unvalidated raw-event partition constraints',
            v_unvalidated_constraint_count;
    END IF;
END
$test$;

ROLLBACK;
