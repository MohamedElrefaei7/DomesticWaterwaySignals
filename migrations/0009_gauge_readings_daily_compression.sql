-- 0009 — compression on gauge_readings_daily.
--
-- Separate from 0006 (the instantaneous table's compression) and DIFFERENTLY TUNED, because the
-- two tables are different sizes doing different jobs. Copying 0006's settings across would look
-- consistent and be wrong in both directions.
--
-- Like 0006, THIS FILE RECORDS NO RATIO. The compression measurement is taken on real data by a
-- human and written into CONTEXT.md and the README from that measurement (CLAUDE.md § 7). Both
-- hypertables get measured at live verification step 8, and both numbers get reported - including
-- if one of them disappoints.

ALTER TABLE gauge_readings_daily SET (
    timescaledb.compress,

    -- Site, parameter AND STATISTIC. stat_cd is in the segmentby list for the same reason it is
    -- in the primary key: only '00003' arrives today, so it segments a single value and buys
    -- nothing right now, and the day daily minimum lands it is already correct. Changing
    -- segmentby later requires decompressing every chunk in the table first, so the free version
    -- is the one written now.
    timescaledb.compress_segmentby = 'usgs_site_id, param_code, stat_cd',

    -- Matching the index in 0008 and the direction every read runs in.
    timescaledb.compress_orderby = 'date DESC'
);

-- ONE YEAR, against the instantaneous table's thirty days.
--
-- The arithmetic that makes these different: the daily table holds roughly 50,000 rows for the
-- entire 35-year record, against ~1.3M for the instantaneous one. Compressing recent daily data
-- buys a few kilobytes and makes those chunks expensive to correct - and daily values are
-- REVISED after publication, which is precisely why the scheduled poll re-requests the last
-- seven days on every run.
--
-- A year keeps everything the poll and its revisions can reach in uncompressed chunks, and
-- compresses only the settled historical record that nothing rewrites. The instantaneous table's
-- thirty days is the same reasoning applied to a table where the revision window is the same but
-- the volume is twenty-five times larger.
SELECT add_compression_policy('gauge_readings_daily', INTERVAL '1 year');
