-- 0027 — gauge_readings_iv: 986 chunks become tens, and the old hypertable is archived.
--
-- ---------------------------------------------------------------------------------------------
-- WHAT WAS ACTUALLY BROKEN, BECAUSE "TOO MANY CHUNKS" SOUNDS LIKE A TIDINESS PROBLEM
-- ---------------------------------------------------------------------------------------------
--
-- 986 chunks for 258,739 rows - 262 rows per chunk, 18.9 years at the 7-day interval 0005 chose.
-- A query over the whole table takes a lock per chunk PLUS one per index per chunk, roughly 2,000
-- lock slots. The cluster had 3,200 in total, cluster-wide, so:
--
--     SELECT min(ts), max(ts), count(*) FROM gauge_readings_iv;
--     ERROR:  out of shared memory
--     HINT:  You might need to increase max_locks_per_transaction.
--
-- The project's largest table was not fully queryable, and the heartbeat's freshness check
-- reported CANNOT BE CHECKED for it. Migration 0026's predecessor commit raised the ceiling to
-- 12,800 slots (infra/postgres/settings.py); THIS migration removes the demand.
--
-- 7 days was not a silly choice - it was the right one for a table expected to hold recent
-- instantaneous readings. It became wrong when Phase 3.5 measured that the daily endpoint carries
-- 35 years and the backfill loaded them (CLAUDE.md § 15).
--
-- ---------------------------------------------------------------------------------------------
-- set_chunk_time_interval() ALONE IS NOT THE FIX, AND HALF-APPLYING IT IS THE TRAP
-- ---------------------------------------------------------------------------------------------
--
-- `set_chunk_time_interval` affects ONLY CHUNKS CREATED AFTER IT RUNS. The existing 986 are
-- untouched by it. A migration containing just that call applies cleanly, reports success,
-- changes the catalog in a way that reads correct in every subsequent inspection - and leaves
-- every historical query exactly as broken as it was. It is CLAUDE.md § 2's theme 1 available in
-- a single statement.
--
-- So the existing chunks are consolidated, which means a REWRITE.
--
-- ---------------------------------------------------------------------------------------------
-- ARCHIVED, NOT DROPPED - AND WHAT THAT COSTS
-- ---------------------------------------------------------------------------------------------
--
-- CLAUDE.md § 3: destructive operations are archived as `..._archived_YYYYMMDD`, and only a human
-- runs an actual DROP. There is no DROP TABLE anywhere in this file and there is not meant to be.
--
-- THE COST IS REAL AND IS STATED RATHER THAN DISCOVERED: the archive roughly DOUBLES this table's
-- footprint until a human drops it, and it will appear in every nightly backup and in the restore
-- test's per-table `row_counts` until then. `backups.byte_size` will step up on the next run. That
-- is the intended trade - the archive is the only copy of the pre-consolidation data, and the
-- consolidation is a rewrite of the project's largest table.
--
-- ---------------------------------------------------------------------------------------------
-- gauge_series IS A VIEW OVER THIS TABLE, AND A RENAME TAKES IT WITH US
-- ---------------------------------------------------------------------------------------------
--
-- THIS IS THE PART THAT WOULD HAVE BEEN SILENT. Postgres binds a view's dependencies BY OID, not
-- by name, so `ALTER TABLE gauge_readings_iv RENAME TO ..._archived_...` does not break
-- `gauge_series` (0010) - it quietly repoints it at the archive. Every query through the view
-- keeps working, keeps returning plausible rows, and reads a table that stops receiving writes the
-- moment this migration commits. A stale series is harder to notice than a missing one
-- (CLAUDE.md § 17), and this one would have gone stale behind the precedence view that every
-- feature and every analog lookup reads.
--
-- The view is therefore recreated against the new table below, AND a catalog check afterwards
-- asserts that NOTHING is left depending on the archive. The check enumerates rather than trusting
-- this file's author to have listed every dependent (CLAUDE.md § 22): a second view added later,
-- or one this header failed to name, fails the migration instead of going quietly stale.
--
-- Everything else that references this table does so BY NAME and re-resolves correctly:
-- app/ingest/usgs_ingest.py, app/ingest/backfill.py, app/features/rollup.py,
-- app/orchestration/heartbeat.py.
--
-- ---------------------------------------------------------------------------------------------
-- THE CONCURRENT WRITER, AND WHAT THE GUARDS BELOW DO AND DO NOT COVER
-- ---------------------------------------------------------------------------------------------
--
-- `usgs_ingest` runs hourly and writes here. A write landing mid-copy would go into the archived
-- table and be lost from the live one - silently, as a few missing readings.
--
-- AN ADVISORY LOCK IS THE WRONG MECHANISM AND IS DELIBERATELY NOT USED. An advisory lock only
-- detects a party that also takes it, and `usgs_ingest` takes none; a `pg_try_advisory_lock` here
-- would succeed against a running ingest and report the coast clear. That is a guard that reports
-- correct while the thing it guards against is happening.
--
-- What is used instead, in order of how much each is worth:
--
--   1. STOP THE SCHEDULER. Procedural, required, and the only complete answer. See
--      docs/runbooks/cluster-settings.md.
--   2. A job_runs check that REFUSES, with a message naming the job. It catches an ingest that is
--      ALREADY running - the case where somebody forgot step 1 - and it fails with a sentence
--      instead of a lock timeout.
--   3. LOCK TABLE ... IN ACCESS EXCLUSIVE MODE under a lock_timeout. Held for the whole
--      transaction, so no writer can touch either table while the copy runs, and a writer already
--      holding a conflicting lock makes this fail in 30 seconds rather than wait indefinitely.
--
-- WHAT IS NOT CLAIMED: whether a writer that BLOCKS on that lock and resumes after commit lands in
-- the new table or the archive depends on Postgres re-resolving the relation name after acquiring
-- the lock. That behaviour was not measured here, so it is not relied on. Step 1 is required.

SET LOCAL lock_timeout = '30s';


-- ---------------------------------------------------------------------------------------------
-- Guard 2: refuse rather than race.
-- ---------------------------------------------------------------------------------------------

DO $$
DECLARE
    running_since timestamptz;
BEGIN
    SELECT started_at INTO running_since
      FROM job_runs
     WHERE job_name = 'usgs_ingest'
       AND status   = 'running'
     ORDER BY started_at DESC
     LIMIT 1;

    IF running_since IS NOT NULL THEN
        RAISE EXCEPTION
            'usgs_ingest has been running since % and writes to gauge_readings_iv. This migration '
            'rewrites that table; a write landing mid-copy goes into the archive and is lost from '
            'the live table. Stop the scheduler first: docker compose stop scheduler',
            running_since;
    END IF;
END
$$;


-- ---------------------------------------------------------------------------------------------
-- Guard 3, and the count that everything below is checked against.
-- ---------------------------------------------------------------------------------------------
--
-- The lock is taken BEFORE the counts, so the numbers captured here describe a table nothing can
-- change for the rest of the transaction. Counting first and locking afterwards would leave a
-- window in which the source moved between the measurement and the copy, which is the failure the
-- count exists to detect - measured against a source that was still moving.

LOCK TABLE gauge_readings_iv IN ACCESS EXCLUSIVE MODE;

CREATE TEMP TABLE _0027_source_state ON COMMIT DROP AS
SELECT count(*)  AS n_rows,
       min(ts)   AS min_ts,
       max(ts)   AS max_ts,
       count(*) FILTER (WHERE qualifiers IS NOT NULL) AS n_qualified,
       sum(value) AS value_sum
  FROM gauge_readings_iv;


-- ---------------------------------------------------------------------------------------------
-- The archive. Indexes and constraints are renamed too, as 0007 did.
-- ---------------------------------------------------------------------------------------------
--
-- Postgres does not rename a table's indexes or constraints along with the table. Left alone, the
-- archive would hold objects named `gauge_readings_iv_*` and the NEW table could not create its
-- own under those names - the migration would fail on a duplicate index name, which is the good
-- outcome, but it fails halfway through a rewrite rather than reading correctly here.

ALTER TABLE gauge_readings_iv RENAME TO gauge_readings_iv_archived_20260818;

ALTER INDEX gauge_readings_iv_site_param_ts_idx
    RENAME TO gauge_readings_iv_archived_20260818_site_param_ts_idx;
ALTER INDEX gauge_readings_iv_ts_idx
    RENAME TO gauge_readings_iv_archived_20260818_ts_idx;
ALTER TABLE gauge_readings_iv_archived_20260818
    RENAME CONSTRAINT gauge_readings_iv_pkey TO gauge_readings_iv_archived_20260818_pkey;
ALTER TABLE gauge_readings_iv_archived_20260818
    RENAME CONSTRAINT gauge_readings_iv_usgs_site_id_fkey
                   TO gauge_readings_iv_archived_20260818_usgs_site_id_fkey;

-- The archive keeps its compressed chunks - they are the data - but not its compression POLICY.
-- A background job compressing a table that is waiting for a human to drop it is work nobody
-- asked for, on a schedule nobody is watching.
SELECT remove_compression_policy('gauge_readings_iv_archived_20260818', if_exists => true);


-- ---------------------------------------------------------------------------------------------
-- The new hypertable. Column-for-column 0005, at 365 days.
-- ---------------------------------------------------------------------------------------------
--
-- Written out rather than `CREATE TABLE ... (LIKE ... INCLUDING ALL)`, because LIKE does not copy
-- the FOREIGN KEY to gauges - it copies CHECK constraints and calls that "constraints". A table
-- that looks right and accepts a reading for a site nobody decided to collect is exactly the
-- silent structural drift a rewrite is most likely to introduce. tests/db/test_chunk_interval.py
-- asserts the two tables' columns and constraints against each other, against a real server,
-- rather than trusting that this file and 0005 agree.
--
-- 365 days, NOT `1 year`. TimescaleDB stores a Postgres interval year as 360 DAYS, so a file
-- saying "1 year" and a test asserting 365 disagree forever - 0008 already hit this and its
-- header says so.

CREATE TABLE gauge_readings_iv (
    usgs_site_id text NOT NULL REFERENCES gauges (usgs_site_id),
    ts timestamptz NOT NULL,
    param_code text NOT NULL,
    value double precision NOT NULL,
    qualifiers text[],
    PRIMARY KEY (usgs_site_id, ts, param_code)
);

SELECT create_hypertable('gauge_readings_iv', by_range('ts', INTERVAL '365 days'));

CREATE INDEX gauge_readings_iv_site_param_ts_idx
    ON gauge_readings_iv (usgs_site_id, param_code, ts DESC);


-- ---------------------------------------------------------------------------------------------
-- The copy.
-- ---------------------------------------------------------------------------------------------

INSERT INTO gauge_readings_iv (usgs_site_id, ts, param_code, value, qualifiers)
SELECT usgs_site_id, ts, param_code, value, qualifiers
  FROM gauge_readings_iv_archived_20260818;


-- ---------------------------------------------------------------------------------------------
-- EXACT equality, in five dimensions, and no tolerance of any size.
-- ---------------------------------------------------------------------------------------------
--
-- A tolerance is a tolerance for exactly the loss this exists to detect (CLAUDE.md § 3). The row
-- count alone would pass a copy that moved the right NUMBER of rows and the wrong ones, so the
-- endpoints, the null-count of the one nullable column, and the sum of the measure are compared
-- too. This is the last moment any of it is checkable: after the transaction commits the source is
-- an archive nobody diffs, and a short copy is invisible.

DO $$
DECLARE
    src _0027_source_state%ROWTYPE;
    dst _0027_source_state%ROWTYPE;
BEGIN
    SELECT * INTO src FROM _0027_source_state;

    SELECT count(*), min(ts), max(ts),
           count(*) FILTER (WHERE qualifiers IS NOT NULL),
           sum(value)
      INTO dst.n_rows, dst.min_ts, dst.max_ts, dst.n_qualified, dst.value_sum
      FROM gauge_readings_iv;

    IF dst.n_rows <> src.n_rows THEN
        RAISE EXCEPTION 'row count: archive has %, the new table has % (difference %)',
            src.n_rows, dst.n_rows, dst.n_rows - src.n_rows;
    END IF;
    IF dst.min_ts IS DISTINCT FROM src.min_ts THEN
        RAISE EXCEPTION 'min(ts): archive has %, the new table has %', src.min_ts, dst.min_ts;
    END IF;
    IF dst.max_ts IS DISTINCT FROM src.max_ts THEN
        RAISE EXCEPTION 'max(ts): archive has %, the new table has %', src.max_ts, dst.max_ts;
    END IF;
    IF dst.n_qualified IS DISTINCT FROM src.n_qualified THEN
        RAISE EXCEPTION 'rows with qualifiers: archive has %, the new table has %',
            src.n_qualified, dst.n_qualified;
    END IF;
    IF dst.value_sum IS DISTINCT FROM src.value_sum THEN
        RAISE EXCEPTION 'sum(value): archive has %, the new table has %',
            src.value_sum, dst.value_sum;
    END IF;

    RAISE NOTICE '0027: copied % rows, % to %, sum(value) %',
        dst.n_rows, dst.min_ts, dst.max_ts, dst.value_sum;
END
$$;


-- ---------------------------------------------------------------------------------------------
-- Repoint gauge_series, then prove nothing is left on the archive.
-- ---------------------------------------------------------------------------------------------
--
-- Byte-for-byte 0010's body. The view is not being changed here; it is being made to refer to the
-- same table it always meant, after a rename moved that name to a different relation.

CREATE OR REPLACE VIEW gauge_series AS
WITH iv_daily AS (
    SELECT
        usgs_site_id,
        (ts AT TIME ZONE 'UTC')::date AS date,
        param_code,
        avg(value)                    AS value
      FROM gauge_readings_iv
     GROUP BY usgs_site_id, (ts AT TIME ZONE 'UTC')::date, param_code
),
dv_daily AS (
    SELECT usgs_site_id, date, param_code, value
      FROM gauge_readings_daily
     WHERE stat_cd = '00003'
)
SELECT
    usgs_site_id,
    date,
    param_code,
    value,
    'iv'::text AS source
  FROM iv_daily
UNION ALL
SELECT
    dv.usgs_site_id,
    dv.date,
    dv.param_code,
    dv.value,
    'dv'::text AS source
  FROM dv_daily dv
 WHERE NOT EXISTS (
           SELECT 1
             FROM iv_daily iv
            WHERE iv.usgs_site_id = dv.usgs_site_id
              AND iv.date         = dv.date
              AND iv.param_code   = dv.param_code
       );

DO $$
DECLARE
    dependents text;
BEGIN
    SELECT string_agg(DISTINCT c.relname, ', ')
      INTO dependents
      FROM pg_depend  d
      JOIN pg_rewrite r ON r.oid = d.objid
      JOIN pg_class   c ON c.oid = r.ev_class
     WHERE d.refobjid  = 'gauge_readings_iv_archived_20260818'::regclass
       AND d.refclassid = 'pg_class'::regclass
       AND c.relkind IN ('v', 'm')
       AND c.relname <> 'gauge_readings_iv_archived_20260818';

    IF dependents IS NOT NULL THEN
        RAISE EXCEPTION
            'these views still read the archived table and would go silently stale: %. A view '
            'binds by OID, so a rename repoints it instead of breaking it. Recreate each one '
            'against gauge_readings_iv in this migration.', dependents;
    END IF;
END
$$;


-- ---------------------------------------------------------------------------------------------
-- Compression, re-enabled AND re-applied.
-- ---------------------------------------------------------------------------------------------
--
-- Settings identical to 0006. Re-enabling alone would leave a table configured for compression
-- with no compressed chunks, which reads as a compression REGRESSION in the next measurement -
-- the same ratio query that produced the 3.36:1 baseline would report roughly 1:1 and the obvious
-- conclusion would be that consolidation cost us the win.
--
-- The chunks are compressed HERE rather than left to the policy, so the after-measurement is
-- available immediately and describes this migration rather than whatever the policy had got
-- around to. The policy is added too, for everything ingested from now on.

ALTER TABLE gauge_readings_iv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'usgs_site_id, param_code',
    timescaledb.compress_orderby = 'ts DESC'
);

-- Everything older than the policy's own threshold, compressed now. The policy below would reach
-- the same state eventually; "eventually" is not a state a measurement can be taken in.
SELECT compress_chunk(chunk, if_not_compressed => true)
  FROM show_chunks('gauge_readings_iv', older_than => INTERVAL '30 days') AS chunk;

SELECT add_compression_policy('gauge_readings_iv', INTERVAL '30 days');


COMMENT ON TABLE gauge_readings_iv_archived_20260818 IS
    'The pre-consolidation gauge_readings_iv, at 986 chunks of 7 days, archived by migration 0027 '
    'on 2026-08-18. NOT WRITTEN TO AND NOT READ BY ANYTHING - gauge_series was repointed at the '
    'new table in the same migration. Kept because CLAUDE.md § 3 archives rather than drops and '
    'only a human runs a DROP. It roughly doubles this table''s footprint and appears in every '
    'backup and in the restore test''s row_counts until somebody drops it.';
