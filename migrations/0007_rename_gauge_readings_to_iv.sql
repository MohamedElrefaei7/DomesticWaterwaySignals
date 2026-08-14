-- 0007 — say which endpoint each of these names is about.
--
-- Pure renames. No data moves, nothing is dropped, and every statement here is reversible by
-- another rename. The 223,706 St. Louis rows loaded in Phase 3 stay exactly where they are.
--
-- ---------------------------------------------------------------------------------------------
-- WHY RENAME AT ALL, RATHER THAN JUST ADDING THE DAILY TABLE NEXT TO IT
-- ---------------------------------------------------------------------------------------------
--
-- A table called `gauge_readings` sitting beside `gauge_readings_daily` reads as "the main one"
-- forever. Someone will write `SELECT ... FROM gauge_readings WHERE usgs_site_id = '07032000'`
-- expecting the site's history and get the last two months of it, because Memphis's
-- instantaneous retention is a rolling window (measured 2026-08-14). No error, no empty result -
-- just a shorter answer than the question assumed, which is CLAUDE.md § 2's theme 1.
--
-- The name has to state what the table holds. After this migration there is no table whose name
-- lets you avoid deciding which measurement you meant; `gauge_series` (0010) is the one place
-- that answers "just give me the series" and it says which source each row came from.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS IS NOT THE `..._archived_YYYYMMDD` TREATMENT FROM CLAUDE.md § 3
-- ---------------------------------------------------------------------------------------------
--
-- § 3 requires destructive operations to be archived rather than dropped. This is not a
-- destructive operation: no rows are removed, no column is discarded, and the inverse of every
-- statement below is another ALTER ... RENAME. It gets its own numbered migration anyway,
-- separate from the schema additions in 0008, because it is close enough to that family that
-- reviewing it mixed in with new-table DDL would be reviewing it less carefully.
--
-- ---------------------------------------------------------------------------------------------
-- WHAT TO VERIFY AFTER APPLYING THIS, RATHER THAN ASSUME
-- ---------------------------------------------------------------------------------------------
--
-- TimescaleDB is expected to carry the hypertable registration, the compression settings, and
-- the compression policy through a table rename - it tracks them by relation OID, not by name.
-- EXPECTED IS NOT VERIFIED. A silently dropped compression policy would be invisible until the
-- storage bill, so live verification step 2 reads the catalog back before anything else happens,
-- and tests/ingest/test_compression.py asserts the same thing against a real server.

ALTER TABLE gauge_readings RENAME TO gauge_readings_iv;

-- Postgres does NOT rename a table's indexes or constraints along with the table. Left alone,
-- this hypertable would carry an index and a primary key still named `gauge_readings_*`, which
-- is the same "reads as the main one" problem one level down - and it is the level an operator
-- meets when a query plan or a constraint violation names the object.
ALTER INDEX gauge_readings_site_param_ts_idx RENAME TO gauge_readings_iv_site_param_ts_idx;

-- The implicit time index create_hypertable() builds, named <table>_<column>_idx from the names
-- as they were in 0005. Renamed too: an index this project did not write by hand is still an
-- index an operator reads in an EXPLAIN, and leaving one object called `gauge_readings_*` behind
-- defeats the point of the rename for exactly the reader who is furthest from this file.
ALTER INDEX gauge_readings_ts_idx RENAME TO gauge_readings_iv_ts_idx;
ALTER TABLE gauge_readings_iv RENAME CONSTRAINT gauge_readings_pkey TO gauge_readings_iv_pkey;
ALTER TABLE gauge_readings_iv
    RENAME CONSTRAINT gauge_readings_usgs_site_id_fkey TO gauge_readings_iv_usgs_site_id_fkey;


-- ---------------------------------------------------------------------------------------------
-- The same disambiguation, applied to the seed.
-- ---------------------------------------------------------------------------------------------
--
-- `gauges.record_start` was written when there was one endpoint and it could only have meant one
-- thing. There are now two, and MEASUREMENT SAYS THEY DIFFER PER SITE AND PER ENDPOINT: Vicksburg
-- publishes daily values from somewhere in 2008-2010 while its instantaneous record is a rolling
-- window of recent weeks. One column cannot hold both, and a column named `record_start` that
-- silently means the instantaneous one is worse than either.
--
-- Renamed rather than dropped-and-recreated so the seeded values survive. `dv_record_start`
-- arrives in 0008, alongside the table it describes.
ALTER TABLE gauges RENAME COLUMN record_start TO iv_record_start;

COMMENT ON COLUMN gauges.iv_record_start IS
    'Start of this site''s INSTANTANEOUS-values record, as seeded by a human. NOTE: measured '
    '2026-08-14, instantaneous retention is a ROLLING WINDOW at 07032000, 07289000 and 07374000 '
    '- these sites have no fixed start, and the seeded value here is the Phase 3 assumption that '
    'the measurement contradicted. See CONTEXT.md; the daily record is the historical backbone.';
