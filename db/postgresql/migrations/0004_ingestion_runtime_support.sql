-- Bisfin ingestion runtime partition support
-- Target: PostgreSQL 16+
-- Apply after 0003_point_in_time_hardening.sql.

BEGIN;

-- Creates one UTC calendar-month partition for immutable raw provider events.
-- The transaction-scoped advisory lock serializes same-month callers before
-- the catalog existence check, so a waiter observes the preceding commit at
-- READ COMMITTED isolation and returns without issuing duplicate DDL.
CREATE OR REPLACE FUNCTION ingest.create_raw_event_month_partition(
    p_month DATE
) RETURNS VOID
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog
AS $function$
DECLARE
    v_month          DATE;
    v_next_month     DATE;
    v_partition_name TEXT;
    v_partition_oid  OID;
    v_lock_key       BIGINT;
    v_isolation      TEXT;
BEGIN
    IF p_month IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'p_month must not be null';
    END IF;

    v_isolation := pg_catalog.current_setting('transaction_isolation');
    IF v_isolation <> 'read committed' THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'ingest.create_raw_event_month_partition requires READ COMMITTED isolation',
            DETAIL = pg_catalog.format(
                'transaction_isolation=%s; the post-lock catalog check requires a fresh command snapshot',
                v_isolation
            );
    END IF;

    v_month := pg_catalog.date_trunc('month', p_month::TIMESTAMP)::DATE;
    v_next_month := (v_month + INTERVAL '1 month')::DATE;
    v_partition_name := pg_catalog.format(
        'raw_event_y%sm%s',
        pg_catalog.to_char(v_month, 'YYYY'),
        pg_catalog.to_char(v_month, 'MM')
    );
    v_lock_key := pg_catalog.hashtextextended(
        pg_catalog.format(
            'bisfin:ingest.raw_event:month:%s',
            pg_catalog.to_char(v_month, 'YYYY-MM-DD')
        ),
        0
    );

    PERFORM pg_catalog.pg_advisory_xact_lock(v_lock_key);

    v_partition_oid := pg_catalog.to_regclass(
        pg_catalog.format('%I.%I', 'ingest', v_partition_name)
    );
    IF v_partition_oid IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_inherits AS inheritance
            WHERE inheritance.inhrelid = v_partition_oid
              AND inheritance.inhparent = 'ingest.raw_event'::REGCLASS
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '42P07',
                MESSAGE = pg_catalog.format(
                    'relation ingest.%I already exists but is not a partition of ingest.raw_event',
                    v_partition_name
                );
        END IF;
        RETURN;
    END IF;

    EXECUTE pg_catalog.format(
        'CREATE TABLE %I.%I PARTITION OF %I.%I '
        'FOR VALUES FROM (%L) TO (%L)',
        'ingest',
        v_partition_name,
        'ingest',
        'raw_event',
        v_month::TIMESTAMP AT TIME ZONE 'UTC',
        v_next_month::TIMESTAMP AT TIME ZONE 'UTC'
    );
END
$function$;

COMMENT ON FUNCTION ingest.create_raw_event_month_partition(DATE) IS
'Creates an idempotent UTC monthly ingest.raw_event partition; same-month callers serialize with a transaction-scoped advisory lock and require READ COMMITTED isolation.';

COMMIT;
