-- Bisfin point-in-time replay and temporal-integrity hardening
-- Target: PostgreSQL 16+
-- Apply after 0002_technical_backtest_completion.sql.

BEGIN;

-- Keep validation and trigger installation atomic with respect to concurrent
-- writers. SHARE ROW EXCLUSIVE conflicts with ordinary INSERT/UPDATE/DELETE
-- locks while still permitting readers.
LOCK TABLE
    catalog.instrument_identifier,
    catalog.instrument_spec_version,
    catalog.universe_member
IN SHARE ROW EXCLUSIVE MODE;

-- Refuse to install the guards over already-invalid temporal data. Intervals
-- are half-open: [from, to), with NULL to meaning positive infinity.
DO $preflight$
DECLARE
    v_provider_id       SMALLINT;
    v_identifier_type   VARCHAR(32);
    v_identifier_value  TEXT;
    v_universe_id       BIGINT;
    v_instrument_id     BIGINT;
    v_first_from        TIMESTAMPTZ;
    v_first_to          TIMESTAMPTZ;
    v_second_from       TIMESTAMPTZ;
    v_second_to         TIMESTAMPTZ;
BEGIN
    SELECT first_row.provider_id,
           first_row.identifier_type,
           first_row.identifier_value,
           first_row.valid_from,
           first_row.valid_to,
           second_row.valid_from,
           second_row.valid_to
      INTO v_provider_id,
           v_identifier_type,
           v_identifier_value,
           v_first_from,
           v_first_to,
           v_second_from,
           v_second_to
      FROM catalog.instrument_identifier AS first_row
      JOIN catalog.instrument_identifier AS second_row
        ON second_row.provider_id = first_row.provider_id
       AND second_row.identifier_type = first_row.identifier_type
       AND second_row.identifier_value = first_row.identifier_value
       AND first_row.valid_from < second_row.valid_from
       AND first_row.valid_from
           < COALESCE(second_row.valid_to, 'infinity'::TIMESTAMPTZ)
       AND second_row.valid_from
           < COALESCE(first_row.valid_to, 'infinity'::TIMESTAMPTZ)
     ORDER BY first_row.provider_id,
              first_row.identifier_type,
              first_row.identifier_value,
              first_row.valid_from,
              second_row.valid_from
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.instrument_identifier temporal overlap for key (provider_id=%s, identifier_type=%s, identifier_value=%s) proposed interval [%s,%s)',
                v_provider_id,
                v_identifier_type,
                v_identifier_value,
                v_second_from,
                COALESCE(v_second_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_first_from,
                COALESCE(v_first_to::TEXT, 'infinity')
            );
    END IF;

    SELECT first_row.instrument_id,
           first_row.effective_from,
           first_row.effective_to,
           second_row.effective_from,
           second_row.effective_to
      INTO v_instrument_id,
           v_first_from,
           v_first_to,
           v_second_from,
           v_second_to
      FROM catalog.instrument_spec_version AS first_row
      JOIN catalog.instrument_spec_version AS second_row
        ON second_row.instrument_id = first_row.instrument_id
       AND first_row.effective_from < second_row.effective_from
       AND first_row.effective_from
           < COALESCE(second_row.effective_to, 'infinity'::TIMESTAMPTZ)
       AND second_row.effective_from
           < COALESCE(first_row.effective_to, 'infinity'::TIMESTAMPTZ)
     ORDER BY first_row.instrument_id,
              first_row.effective_from,
              second_row.effective_from
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.instrument_spec_version temporal overlap for key (instrument_id=%s) proposed interval [%s,%s)',
                v_instrument_id,
                v_second_from,
                COALESCE(v_second_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_first_from,
                COALESCE(v_first_to::TEXT, 'infinity')
            );
    END IF;

    SELECT first_row.universe_id,
           first_row.instrument_id,
           first_row.valid_from,
           first_row.valid_to,
           second_row.valid_from,
           second_row.valid_to
      INTO v_universe_id,
           v_instrument_id,
           v_first_from,
           v_first_to,
           v_second_from,
           v_second_to
      FROM catalog.universe_member AS first_row
      JOIN catalog.universe_member AS second_row
        ON second_row.universe_id = first_row.universe_id
       AND second_row.instrument_id = first_row.instrument_id
       AND first_row.valid_from < second_row.valid_from
       AND first_row.valid_from
           < COALESCE(second_row.valid_to, 'infinity'::TIMESTAMPTZ)
       AND second_row.valid_from
           < COALESCE(first_row.valid_to, 'infinity'::TIMESTAMPTZ)
     ORDER BY first_row.universe_id,
              first_row.instrument_id,
              first_row.valid_from,
              second_row.valid_from
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.universe_member temporal overlap for key (universe_id=%s, instrument_id=%s) proposed interval [%s,%s)',
                v_universe_id,
                v_instrument_id,
                v_second_from,
                COALESCE(v_second_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_first_from,
                COALESCE(v_first_to::TEXT, 'infinity')
            );
    END IF;
END
$preflight$;

-- Advisory locks serialize writers for one logical identifier key. The
-- overlap SELECT is deliberately a separate PL/pgSQL statement after the
-- blocking lock call so READ COMMITTED obtains a fresh command snapshot.
CREATE OR REPLACE FUNCTION catalog.enforce_instrument_identifier_no_overlap()
RETURNS TRIGGER
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_new_lock_key       BIGINT;
    v_old_lock_key       BIGINT;
    v_existing_from      TIMESTAMPTZ;
    v_existing_to        TIMESTAMPTZ;
    v_transaction_level  TEXT;
BEGIN
    IF TG_RELID <> 'catalog.instrument_identifier'::REGCLASS THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.enforce_instrument_identifier_no_overlap may only guard catalog.instrument_identifier';
    END IF;

    v_transaction_level := pg_catalog.current_setting('transaction_isolation');
    IF v_transaction_level <> 'read committed' THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.instrument_identifier overlap enforcement requires READ COMMITTED isolation',
            DETAIL = pg_catalog.format(
                'transaction_isolation=%s; the post-lock overlap check requires a fresh command snapshot',
                v_transaction_level
            );
    END IF;

    v_new_lock_key := pg_catalog.hashtextextended(
        pg_catalog.jsonb_build_array(
            'catalog.instrument_identifier',
            NEW.provider_id,
            NEW.identifier_type,
            NEW.identifier_value
        )::TEXT,
        0
    );

    IF TG_OP = 'UPDATE' THEN
        v_old_lock_key := pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'catalog.instrument_identifier',
                OLD.provider_id,
                OLD.identifier_type,
                OLD.identifier_value
            )::TEXT,
            0
        );

        PERFORM pg_catalog.pg_advisory_xact_lock(
            LEAST(v_old_lock_key, v_new_lock_key)
        );
        IF v_old_lock_key <> v_new_lock_key THEN
            PERFORM pg_catalog.pg_advisory_xact_lock(
                GREATEST(v_old_lock_key, v_new_lock_key)
            );
        END IF;

        SELECT candidate.valid_from,
               candidate.valid_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.instrument_identifier AS candidate
         WHERE candidate.provider_id = NEW.provider_id
           AND candidate.identifier_type = NEW.identifier_type
           AND candidate.identifier_value = NEW.identifier_value
           AND candidate.valid_from
               < COALESCE(NEW.valid_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.valid_from
               < COALESCE(candidate.valid_to, 'infinity'::TIMESTAMPTZ)
           AND ROW(
                   candidate.provider_id,
                   candidate.identifier_type,
                   candidate.identifier_value,
                   candidate.valid_from
               ) IS DISTINCT FROM ROW(
                   OLD.provider_id,
                   OLD.identifier_type,
                   OLD.identifier_value,
                   OLD.valid_from
               )
         ORDER BY candidate.valid_from
         LIMIT 1;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(v_new_lock_key);

        SELECT candidate.valid_from,
               candidate.valid_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.instrument_identifier AS candidate
         WHERE candidate.provider_id = NEW.provider_id
           AND candidate.identifier_type = NEW.identifier_type
           AND candidate.identifier_value = NEW.identifier_value
           AND candidate.valid_from
               < COALESCE(NEW.valid_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.valid_from
               < COALESCE(candidate.valid_to, 'infinity'::TIMESTAMPTZ)
         ORDER BY candidate.valid_from
         LIMIT 1;
    END IF;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.instrument_identifier temporal overlap for key (provider_id=%s, identifier_type=%s, identifier_value=%s) proposed interval [%s,%s)',
                NEW.provider_id,
                NEW.identifier_type,
                NEW.identifier_value,
                NEW.valid_from,
                COALESCE(NEW.valid_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_existing_from,
                COALESCE(v_existing_to::TEXT, 'infinity')
            );
    END IF;

    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION catalog.enforce_instrument_spec_version_no_overlap()
RETURNS TRIGGER
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_new_lock_key       BIGINT;
    v_old_lock_key       BIGINT;
    v_existing_from      TIMESTAMPTZ;
    v_existing_to        TIMESTAMPTZ;
    v_transaction_level  TEXT;
BEGIN
    IF TG_RELID <> 'catalog.instrument_spec_version'::REGCLASS THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.enforce_instrument_spec_version_no_overlap may only guard catalog.instrument_spec_version';
    END IF;

    v_transaction_level := pg_catalog.current_setting('transaction_isolation');
    IF v_transaction_level <> 'read committed' THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.instrument_spec_version overlap enforcement requires READ COMMITTED isolation',
            DETAIL = pg_catalog.format(
                'transaction_isolation=%s; the post-lock overlap check requires a fresh command snapshot',
                v_transaction_level
            );
    END IF;

    v_new_lock_key := pg_catalog.hashtextextended(
        pg_catalog.jsonb_build_array(
            'catalog.instrument_spec_version',
            NEW.instrument_id
        )::TEXT,
        0
    );

    IF TG_OP = 'UPDATE' THEN
        v_old_lock_key := pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'catalog.instrument_spec_version',
                OLD.instrument_id
            )::TEXT,
            0
        );

        PERFORM pg_catalog.pg_advisory_xact_lock(
            LEAST(v_old_lock_key, v_new_lock_key)
        );
        IF v_old_lock_key <> v_new_lock_key THEN
            PERFORM pg_catalog.pg_advisory_xact_lock(
                GREATEST(v_old_lock_key, v_new_lock_key)
            );
        END IF;

        SELECT candidate.effective_from,
               candidate.effective_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.instrument_spec_version AS candidate
         WHERE candidate.instrument_id = NEW.instrument_id
           AND candidate.effective_from
               < COALESCE(NEW.effective_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.effective_from
               < COALESCE(candidate.effective_to, 'infinity'::TIMESTAMPTZ)
           AND ROW(
                   candidate.instrument_id,
                   candidate.effective_from
               ) IS DISTINCT FROM ROW(
                   OLD.instrument_id,
                   OLD.effective_from
               )
         ORDER BY candidate.effective_from
         LIMIT 1;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(v_new_lock_key);

        SELECT candidate.effective_from,
               candidate.effective_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.instrument_spec_version AS candidate
         WHERE candidate.instrument_id = NEW.instrument_id
           AND candidate.effective_from
               < COALESCE(NEW.effective_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.effective_from
               < COALESCE(candidate.effective_to, 'infinity'::TIMESTAMPTZ)
         ORDER BY candidate.effective_from
         LIMIT 1;
    END IF;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.instrument_spec_version temporal overlap for key (instrument_id=%s) proposed interval [%s,%s)',
                NEW.instrument_id,
                NEW.effective_from,
                COALESCE(NEW.effective_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_existing_from,
                COALESCE(v_existing_to::TEXT, 'infinity')
            );
    END IF;

    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION catalog.enforce_universe_member_no_overlap()
RETURNS TRIGGER
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_new_lock_key       BIGINT;
    v_old_lock_key       BIGINT;
    v_existing_from      TIMESTAMPTZ;
    v_existing_to        TIMESTAMPTZ;
    v_transaction_level  TEXT;
BEGIN
    IF TG_RELID <> 'catalog.universe_member'::REGCLASS THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.enforce_universe_member_no_overlap may only guard catalog.universe_member';
    END IF;

    v_transaction_level := pg_catalog.current_setting('transaction_isolation');
    IF v_transaction_level <> 'read committed' THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'catalog.universe_member overlap enforcement requires READ COMMITTED isolation',
            DETAIL = pg_catalog.format(
                'transaction_isolation=%s; the post-lock overlap check requires a fresh command snapshot',
                v_transaction_level
            );
    END IF;

    v_new_lock_key := pg_catalog.hashtextextended(
        pg_catalog.jsonb_build_array(
            'catalog.universe_member',
            NEW.universe_id,
            NEW.instrument_id
        )::TEXT,
        0
    );

    IF TG_OP = 'UPDATE' THEN
        v_old_lock_key := pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'catalog.universe_member',
                OLD.universe_id,
                OLD.instrument_id
            )::TEXT,
            0
        );

        PERFORM pg_catalog.pg_advisory_xact_lock(
            LEAST(v_old_lock_key, v_new_lock_key)
        );
        IF v_old_lock_key <> v_new_lock_key THEN
            PERFORM pg_catalog.pg_advisory_xact_lock(
                GREATEST(v_old_lock_key, v_new_lock_key)
            );
        END IF;

        SELECT candidate.valid_from,
               candidate.valid_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.universe_member AS candidate
         WHERE candidate.universe_id = NEW.universe_id
           AND candidate.instrument_id = NEW.instrument_id
           AND candidate.valid_from
               < COALESCE(NEW.valid_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.valid_from
               < COALESCE(candidate.valid_to, 'infinity'::TIMESTAMPTZ)
           AND ROW(
                   candidate.universe_id,
                   candidate.instrument_id,
                   candidate.valid_from
               ) IS DISTINCT FROM ROW(
                   OLD.universe_id,
                   OLD.instrument_id,
                   OLD.valid_from
               )
         ORDER BY candidate.valid_from
         LIMIT 1;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(v_new_lock_key);

        SELECT candidate.valid_from,
               candidate.valid_to
          INTO v_existing_from,
               v_existing_to
          FROM catalog.universe_member AS candidate
         WHERE candidate.universe_id = NEW.universe_id
           AND candidate.instrument_id = NEW.instrument_id
           AND candidate.valid_from
               < COALESCE(NEW.valid_to, 'infinity'::TIMESTAMPTZ)
           AND NEW.valid_from
               < COALESCE(candidate.valid_to, 'infinity'::TIMESTAMPTZ)
         ORDER BY candidate.valid_from
         LIMIT 1;
    END IF;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23P01',
            MESSAGE = pg_catalog.format(
                'catalog.universe_member temporal overlap for key (universe_id=%s, instrument_id=%s) proposed interval [%s,%s)',
                NEW.universe_id,
                NEW.instrument_id,
                NEW.valid_from,
                COALESCE(NEW.valid_to::TEXT, 'infinity')
            ),
            DETAIL = pg_catalog.format(
                'conflicting_interval=[%s,%s)',
                v_existing_from,
                COALESCE(v_existing_to::TEXT, 'infinity')
            );
    END IF;

    RETURN NEW;
END
$function$;

-- Trigger functions run with the migration owner's privileges so their
-- overlap queries remain complete. They are not a public callable API and
-- must not be attachable to attacker-controlled tables.
REVOKE EXECUTE ON FUNCTION
    catalog.enforce_instrument_identifier_no_overlap()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
    catalog.enforce_instrument_spec_version_no_overlap()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
    catalog.enforce_universe_member_no_overlap()
FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_instrument_identifier_no_overlap
    ON catalog.instrument_identifier;
CREATE TRIGGER trg_instrument_identifier_no_overlap
BEFORE INSERT OR UPDATE OF
    provider_id,
    identifier_type,
    identifier_value,
    valid_from,
    valid_to
ON catalog.instrument_identifier
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_instrument_identifier_no_overlap();

DROP TRIGGER IF EXISTS trg_instrument_spec_version_no_overlap
    ON catalog.instrument_spec_version;
CREATE TRIGGER trg_instrument_spec_version_no_overlap
BEFORE INSERT OR UPDATE OF
    instrument_id,
    effective_from,
    effective_to
ON catalog.instrument_spec_version
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_instrument_spec_version_no_overlap();

DROP TRIGGER IF EXISTS trg_universe_member_no_overlap
    ON catalog.universe_member;
CREATE TRIGGER trg_universe_member_no_overlap
BEFORE INSERT OR UPDATE OF
    universe_id,
    instrument_id,
    valid_from,
    valid_to
ON catalog.universe_member
FOR EACH ROW
EXECUTE FUNCTION catalog.enforce_universe_member_no_overlap();

COMMENT ON FUNCTION catalog.enforce_instrument_identifier_no_overlap() IS
'Enforces half-open [from,to) intervals with a NULL end treated as positive infinity. A per-logical-key transaction advisory lock serializes writers; READ COMMITTED lets the post-lock query see the preceding writer.';

COMMENT ON FUNCTION catalog.enforce_instrument_spec_version_no_overlap() IS
'Enforces half-open [from,to) intervals with a NULL end treated as positive infinity. A per-logical-key transaction advisory lock serializes writers; READ COMMITTED lets the post-lock query see the preceding writer.';

COMMENT ON FUNCTION catalog.enforce_universe_member_no_overlap() IS
'Enforces half-open [from,to) intervals with a NULL end treated as positive infinity. A per-logical-key transaction advisory lock serializes writers; READ COMMITTED lets the post-lock query see the preceding writer.';

COMMENT ON TRIGGER trg_instrument_identifier_no_overlap
    ON catalog.instrument_identifier IS
'Rejects overlapping validity intervals for one provider/type/value identifier key.';

COMMENT ON TRIGGER trg_instrument_spec_version_no_overlap
    ON catalog.instrument_spec_version IS
'Rejects overlapping effective intervals for one instrument.';

COMMENT ON TRIGGER trg_universe_member_no_overlap
    ON catalog.universe_member IS
'Rejects overlapping membership intervals for one universe/instrument key.';

-- Point-in-time bar replay. There are two literal query branches so each mode
-- can use its matching availability index without a CASE expression obscuring
-- the indexed column.
CREATE OR REPLACE FUNCTION market.bars_as_of(
    p_bar_series_id        BIGINT,
    p_from_ts              TIMESTAMPTZ,
    p_to_ts                TIMESTAMPTZ,
    p_knowledge_cutoff_ts  TIMESTAMPTZ,
    p_replay_mode          VARCHAR
)
RETURNS TABLE (
    bar_open_ts             TIMESTAMPTZ(6),
    bar_series_id           BIGINT,
    revision_no             INTEGER,
    available_at            TIMESTAMPTZ(6),
    system_available_at     TIMESTAMPTZ(6),
    bar_close_ts            TIMESTAMPTZ(6),
    trading_date            DATE,
    open_price              NUMERIC(38,18),
    high_price              NUMERIC(38,18),
    low_price               NUMERIC(38,18),
    close_price             NUMERIC(38,18),
    official_close_price    NUMERIC(38,18),
    settlement_price        NUMERIC(38,18),
    volume                  NUMERIC(38,18),
    quote_volume            NUMERIC(38,18),
    trade_count             BIGINT,
    vwap                    NUMERIC(38,18),
    open_interest           NUMERIC(38,18),
    is_final                BOOLEAN,
    quality_flags           INTEGER,
    ingestion_batch_id      BIGINT,
    recorded_at             TIMESTAMPTZ(6),
    previous_close_price    NUMERIC(38,18),
    effective_available_at  TIMESTAMPTZ(6)
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_adjustment_set_id      BIGINT;
    v_adjustment_cutoff_ts   TIMESTAMPTZ;
BEGIN
    IF p_bar_series_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'market.bars_as_of: p_bar_series_id must not be NULL';
    END IF;
    IF p_from_ts IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'market.bars_as_of: p_from_ts must not be NULL';
    END IF;
    IF p_to_ts IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'market.bars_as_of: p_to_ts must not be NULL';
    END IF;
    IF p_knowledge_cutoff_ts IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'market.bars_as_of: p_knowledge_cutoff_ts must not be NULL';
    END IF;
    IF p_replay_mode IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22004',
            MESSAGE = 'market.bars_as_of: p_replay_mode must not be NULL';
    END IF;

    IF NOT pg_catalog.isfinite(p_from_ts) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: p_from_ts must be finite',
            DETAIL = pg_catalog.format('p_from_ts=%s', p_from_ts);
    END IF;
    IF NOT pg_catalog.isfinite(p_to_ts) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: p_to_ts must be finite',
            DETAIL = pg_catalog.format('p_to_ts=%s', p_to_ts);
    END IF;
    IF NOT pg_catalog.isfinite(p_knowledge_cutoff_ts) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: p_knowledge_cutoff_ts must be finite',
            DETAIL = pg_catalog.format(
                'p_knowledge_cutoff_ts=%s',
                p_knowledge_cutoff_ts
            );
    END IF;

    IF p_from_ts >= p_to_ts THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: p_from_ts must be earlier than p_to_ts',
            DETAIL = pg_catalog.format(
                'requested_range=[%s,%s)',
                p_from_ts,
                p_to_ts
            );
    END IF;

    IF p_to_ts > p_knowledge_cutoff_ts THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: requested range must not end after the knowledge cutoff',
            DETAIL = pg_catalog.format(
                'p_to_ts=%s, p_knowledge_cutoff_ts=%s',
                p_to_ts,
                p_knowledge_cutoff_ts
            );
    END IF;

    IF p_replay_mode NOT IN ('PUBLIC_REPLAY', 'ACTUAL_SYSTEM_REPLAY') THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: unsupported replay mode',
            DETAIL = pg_catalog.format(
                'p_replay_mode=%L; expected PUBLIC_REPLAY or ACTUAL_SYSTEM_REPLAY',
                p_replay_mode
            );
    END IF;

    SELECT series.adjustment_set_id,
           adjustment.knowledge_cutoff_ts
      INTO v_adjustment_set_id,
           v_adjustment_cutoff_ts
      FROM market.bar_series AS series
      LEFT JOIN catalog.adjustment_set AS adjustment
        ON adjustment.adjustment_set_id = series.adjustment_set_id
     WHERE series.bar_series_id = p_bar_series_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'market.bars_as_of: unknown bar series',
            DETAIL = pg_catalog.format('bar_series_id=%s', p_bar_series_id);
    END IF;

    IF v_adjustment_set_id IS NOT NULL
       AND (
           v_adjustment_cutoff_ts IS NULL
           OR v_adjustment_cutoff_ts > p_knowledge_cutoff_ts
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'market.bars_as_of: adjustment data was not fixed by the requested knowledge cutoff',
            DETAIL = pg_catalog.format(
                'bar_series_id=%s, adjustment_set_id=%s, adjustment_knowledge_cutoff_ts=%s, requested_knowledge_cutoff_ts=%s',
                p_bar_series_id,
                v_adjustment_set_id,
                COALESCE(v_adjustment_cutoff_ts::TEXT, 'NULL'),
                p_knowledge_cutoff_ts
            );
    END IF;

    IF p_replay_mode = 'PUBLIC_REPLAY' THEN
        RETURN QUERY
        WITH ranked AS (
            SELECT revision.bar_open_ts,
                   revision.bar_series_id,
                   revision.revision_no,
                   revision.available_at,
                   revision.system_available_at,
                   revision.bar_close_ts,
                   revision.trading_date,
                   revision.open_price,
                   revision.high_price,
                   revision.low_price,
                   revision.close_price,
                   revision.official_close_price,
                   revision.settlement_price,
                   revision.volume,
                   revision.quote_volume,
                   revision.trade_count,
                   revision.vwap,
                   revision.open_interest,
                   revision.is_final,
                   revision.quality_flags,
                   revision.ingestion_batch_id,
                   revision.recorded_at,
                   revision.previous_close_price,
                   revision.available_at AS effective_available_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY revision.bar_series_id,
                                    revision.bar_open_ts
                       ORDER BY revision.available_at DESC,
                                revision.revision_no DESC,
                                revision.system_available_at DESC,
                                revision.recorded_at DESC,
                                revision.ingestion_batch_id DESC
                   ) AS revision_rank
              FROM market.bar_revision AS revision
             WHERE revision.bar_series_id = p_bar_series_id
               AND revision.bar_open_ts >= p_from_ts
               AND revision.bar_open_ts < p_to_ts
               AND revision.available_at <= p_knowledge_cutoff_ts
               AND revision.bar_close_ts <= p_knowledge_cutoff_ts
               AND revision.is_final
        )
        SELECT ranked.bar_open_ts,
               ranked.bar_series_id,
               ranked.revision_no,
               ranked.available_at,
               ranked.system_available_at,
               ranked.bar_close_ts,
               ranked.trading_date,
               ranked.open_price,
               ranked.high_price,
               ranked.low_price,
               ranked.close_price,
               ranked.official_close_price,
               ranked.settlement_price,
               ranked.volume,
               ranked.quote_volume,
               ranked.trade_count,
               ranked.vwap,
               ranked.open_interest,
               ranked.is_final,
               ranked.quality_flags,
               ranked.ingestion_batch_id,
               ranked.recorded_at,
               ranked.previous_close_price,
               ranked.effective_available_at
          FROM ranked
         WHERE ranked.revision_rank = 1
         ORDER BY ranked.bar_open_ts,
                  ranked.bar_series_id;
    ELSE
        RETURN QUERY
        WITH ranked AS (
            SELECT revision.bar_open_ts,
                   revision.bar_series_id,
                   revision.revision_no,
                   revision.available_at,
                   revision.system_available_at,
                   revision.bar_close_ts,
                   revision.trading_date,
                   revision.open_price,
                   revision.high_price,
                   revision.low_price,
                   revision.close_price,
                   revision.official_close_price,
                   revision.settlement_price,
                   revision.volume,
                   revision.quote_volume,
                   revision.trade_count,
                   revision.vwap,
                   revision.open_interest,
                   revision.is_final,
                   revision.quality_flags,
                   revision.ingestion_batch_id,
                   revision.recorded_at,
                   revision.previous_close_price,
                   revision.system_available_at AS effective_available_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY revision.bar_series_id,
                                    revision.bar_open_ts
                       ORDER BY revision.system_available_at DESC,
                                revision.revision_no DESC,
                                revision.available_at DESC,
                                revision.recorded_at DESC,
                                revision.ingestion_batch_id DESC
                   ) AS revision_rank
              FROM market.bar_revision AS revision
             WHERE revision.bar_series_id = p_bar_series_id
               AND revision.bar_open_ts >= p_from_ts
               AND revision.bar_open_ts < p_to_ts
               AND revision.system_available_at <= p_knowledge_cutoff_ts
               AND revision.bar_close_ts <= p_knowledge_cutoff_ts
               AND revision.is_final
        )
        SELECT ranked.bar_open_ts,
               ranked.bar_series_id,
               ranked.revision_no,
               ranked.available_at,
               ranked.system_available_at,
               ranked.bar_close_ts,
               ranked.trading_date,
               ranked.open_price,
               ranked.high_price,
               ranked.low_price,
               ranked.close_price,
               ranked.official_close_price,
               ranked.settlement_price,
               ranked.volume,
               ranked.quote_volume,
               ranked.trade_count,
               ranked.vwap,
               ranked.open_interest,
               ranked.is_final,
               ranked.quality_flags,
               ranked.ingestion_batch_id,
               ranked.recorded_at,
               ranked.previous_close_price,
               ranked.effective_available_at
          FROM ranked
         WHERE ranked.revision_rank = 1
         ORDER BY ranked.bar_open_ts,
                  ranked.bar_series_id;
    END IF;
END
$function$;

COMMENT ON FUNCTION market.bars_as_of(
    BIGINT,
    TIMESTAMPTZ,
    TIMESTAMPTZ,
    TIMESTAMPTZ,
    VARCHAR
) IS
'Anti-look-ahead final-bar replay as of one public or actual-system knowledge cutoff. It requires bar completion by the cutoff, validates adjusted-series provenance, and returns the latest eligible revision per logical bar.';

COMMENT ON VIEW market.current_bar IS
'Operational latest-known view only. Unsafe for historical backtests, point-in-time features, or historical ML/DL datasets because it ignores a knowledge cutoff; use market.bars_as_of(...) instead.';

COMMIT;
