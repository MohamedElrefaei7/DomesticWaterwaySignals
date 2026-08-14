-- 0010 — gauge_series: one daily series per site, and which source each row came from.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS IS A VIEW AND NOT A QUERY PATTERN EVERYONE REPEATS
-- ---------------------------------------------------------------------------------------------
--
-- Three later phases need "the daily series for this site": Phase 5's features, Phase 7's analog
-- engine, and Phase 8's API. If the precedence rule lives in a paragraph rather than in the
-- database, it gets re-derived three times - and the three copies diverge SILENTLY, because each
-- one returns a plausible series and nothing compares them. That is CLAUDE.md § 4's "two tables
-- of the same fact" applied to logic instead of data.
--
-- One definition, in the one place every consumer already has to go through.
--
-- ---------------------------------------------------------------------------------------------
-- THE RULE, AND WHY INSTANTANEOUS WINS WHERE BOTH EXIST
-- ---------------------------------------------------------------------------------------------
--
-- For each (usgs_site_id, date, param_code): use the instantaneous data aggregated to a daily
-- mean where it exists, otherwise the published daily mean.
--
-- Instantaneous is preferred because it is the finer measurement: where it covers a day, the
-- daily mean computed from it comes from the actual sub-daily record this project holds, and can
-- be recomputed, inspected, and explained. Where it is absent - which is MOST OF HISTORY at
-- three of the four sites, since instantaneous retention is a rolling window - the published
-- daily value is the only answer there is.
--
-- ---------------------------------------------------------------------------------------------
-- THE HONEST COST, STATED HERE RATHER THAN DISCOVERED LATER
-- ---------------------------------------------------------------------------------------------
--
-- THESE ARE NOT THE SAME MEASUREMENT, AND A SERIES THAT SWITCHES SOURCE MID-HISTORY HAS A SEAM
-- AT THE SWITCH. Two known differences, neither of which this view hides:
--
--   1. DAY BOUNDARIES DIFFER. USGS computes its daily mean over a calendar day in the SITE'S
--      LOCAL TIME. The aggregation below buckets by UTC date, because this project stores
--      instantaneous timestamps in UTC and does not record a timezone per gauge. On the lower
--      Mississippi that is a five-to-six-hour offset, so an iv-sourced daily mean and a
--      dv-sourced one for the same date are computed over windows that differ at both edges.
--      For a river stage that moves in feet per day this is small; it is not zero, and it is not
--      a rounding error.
--
--   2. SAMPLING DIFFERS. The instantaneous mean below is an unweighted average of whatever
--      samples arrived that day - 96 of them at Baton Rouge, 24 at Memphis, fewer across a gap.
--      USGS's own daily mean is computed from the full unit-value record with its own handling
--      of partial days.
--
-- THIS IS EXACTLY WHY `source` IS EXPOSED. A consumer that cares about the seam can see it, test
-- for it, and exclude one side; a consumer that does not gets a complete series. What nobody
-- gets is a series that silently changes provenance.
-- tests/ingest/test_gauge_series_view.py asserts the column exists and is never NULL, so the
-- seam stays visible.

CREATE VIEW gauge_series AS
WITH iv_daily AS (
    -- (ts AT TIME ZONE 'UTC')::date, not ts::date. A bare cast on a timestamptz resolves in the
    -- SESSION's TimeZone setting, which means this view would return different dates depending on
    -- who connected and from where. Naming the zone makes the bucketing a property of the
    -- definition rather than of the caller.
    SELECT
        usgs_site_id,
        (ts AT TIME ZONE 'UTC')::date AS date,
        param_code,
        avg(value)                    AS value
      FROM gauge_readings_iv
     GROUP BY usgs_site_id, (ts AT TIME ZONE 'UTC')::date, param_code
),
dv_daily AS (
    -- The MEAN only. Daily minimum and maximum are a different series answering a different
    -- question, and unioning them in here would silently triple the row count per date. They
    -- are addressable directly on gauge_readings_daily, which is why stat_cd is in its key.
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
 -- The precedence rule, and the reason this is UNION ALL with a NOT EXISTS rather than a plain
 -- UNION ALL of both sides. Without this clause the view emits ONE ROW PER SOURCE for every date
 -- both cover - which is not an error anything would raise, just a series where St. Louis has
 -- twice as many rows as it should and every average over it is quietly reweighted.
 WHERE NOT EXISTS (
           SELECT 1
             FROM iv_daily iv
            WHERE iv.usgs_site_id = dv.usgs_site_id
              AND iv.date         = dv.date
              AND iv.param_code   = dv.param_code
       );

COMMENT ON VIEW gauge_series IS
    'One daily value per (site, date, parameter), preferring instantaneous-derived means over '
    'published daily means. The `source` column says which was used: the two are not identical '
    'measurements (different day boundaries and sampling), so a series that switches source '
    'mid-history has a seam - see the migration for the detail. Consumers must never re-derive '
    'this precedence rule themselves (CLAUDE.md section 15).';
