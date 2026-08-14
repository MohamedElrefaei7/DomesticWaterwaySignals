-- 0008 — gauge_readings_daily: the historical backbone.
--
-- Measured against the live USGS API on 2026-08-14, and it overturns what Phase 3 assumed:
-- INSTANTANEOUS RETENTION IS A ROLLING WINDOW AT THREE OF THE FOUR SITES. Memphis returned
-- nothing at 2025-01, 2025-06 or 2026-01 and data at 2026-06. The Phase 3 backfill aborting at
-- Memphis's first window was the § 14 guard working exactly as designed - a 200 with the
-- requested series absent, refused rather than written as zero rows.
--
-- The daily-values endpoint carries the depth the instantaneous one does not. Memphis and
-- Vicksburg both return a complete 122-value series across 2022-09-01 to 2022-12-31 - the low
-- water event this project labels as a natural experiment, fully covered at every gauge.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS IS ITS OWN TABLE AND NOT A `source` COLUMN ON gauge_readings_iv
-- ---------------------------------------------------------------------------------------------
--
-- THE SHARED-TABLE VERSION WAS CONSIDERED AND TURNED DOWN. It is the tidier-looking option, it
-- is the one a future session will propose as a simplification, and this comment exists so that
-- proposal meets the reasoning rather than a blank file.
--
-- A daily mean stamped at midnight and an instantaneous reading taken at 14:45 are different
-- KINDS of measurement. One is USGS's aggregation over a calendar day in the site's local time;
-- the other is a single sample at an instant. With a discriminator column, the obvious query -
-- `SELECT * FROM gauge_readings WHERE usgs_site_id = X` - returns a silent mixture of the two,
-- and every aggregate computed over that mixture double-counts the days where both exist. The
-- filter that would prevent it is one a caller has to remember, every time, forever.
--
-- Separate tables make that mistake IMPOSSIBLE rather than merely discouraged, and the cost is a
-- single view (0010) that resolves precedence once and says which source each row came from.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THE COLUMN IS `date` AND NOT `ts`
-- ---------------------------------------------------------------------------------------------
--
-- A daily mean is not an instant. Naming it `ts` would invite joining it to instantaneous
-- timestamps as though the two were commensurable, and would invite storing it as timestamptz -
-- at which point the server's TimeZone setting decides what midnight meant, silently, with a
-- different answer in the container than on a laptop.
--
-- The API returns `2022-10-01T00:00:00.000` with NO OFFSET (measured; the instantaneous endpoint
-- returns `-05:00`). So the calendar date the source stated is stored verbatim, with no timezone
-- arithmetic applied at any point. A daily mean for 2022-10-01 belongs to 2022-10-01 wherever it
-- is read from.

CREATE TABLE gauge_readings_daily (
    usgs_site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- The calendar date as published. Never converted, never localized. See above.
    date date NOT NULL,

    -- '00060' discharge. Same five-digit USGS vocabulary as the instantaneous table.
    param_code text NOT NULL,

    -- THE STATISTIC CODE, AND IT IS PART OF THE KEY EVEN THOUGH ONLY '00003' ARRIVES TODAY.
    --
    -- USGS publishes daily minimum ('00001'), maximum ('00002') and mean ('00003') for many
    -- sites. This project has a specific future interest in the daily MINIMUM: the constraint
    -- that binds a barge tow is the low point of the day, not the average of it - a channel that
    -- was passable for twenty hours and not for four is a channel with a draft restriction.
    --
    -- A table keyed without stat_cd cannot hold mean and minimum for the same site and date, and
    -- adding a column to a primary key after the fact means rebuilding the table and its
    -- compressed chunks. It costs nothing now.
    --
    -- Parsed from the response's variable.options.option where name = 'Statistic', never
    -- hardcoded, and asserted against what was requested (CLAUDE.md § 15).
    stat_cd text NOT NULL,

    value double precision NOT NULL,

    -- As published: 'P' provisional, 'A' approved. Preserved, never filtered - same reasoning as
    -- the instantaneous table.
    qualifiers text[],

    -- TimescaleDB requires the partitioning column in any unique constraint, which `date` is.
    PRIMARY KEY (usgs_site_id, date, param_code, stat_cd)
);

-- One year per chunk. The instantaneous table uses seven days because it holds ~1.3M rows at
-- sub-hourly cadence; this one holds one row per site per day - roughly 50k rows for the whole
-- 35-year record. Seven-day chunks here would produce ~1,800 chunks averaging 28 rows each,
-- where the per-chunk metadata costs more than the data and chunk exclusion has nothing to
-- exclude.
--
-- WRITTEN AS `365 days` RATHER THAN `1 year` DELIBERATELY. Measured on the pinned image: an
-- interval of '1 year' is stored by TimescaleDB as 360 days, because a Postgres interval year is
-- twelve 30-day months. The difference is harmless to the data and poisonous to a check - a test
-- asserting the file's stated intent would read 360 and fail, and the natural fix is to weaken
-- the test rather than to notice the file said something it did not mean.
SELECT create_hypertable('gauge_readings_daily', by_range('date', INTERVAL '365 days'));

CREATE INDEX gauge_readings_daily_site_param_stat_date_idx
    ON gauge_readings_daily (usgs_site_id, param_code, stat_cd, date DESC);


-- ---------------------------------------------------------------------------------------------
-- Where the daily record starts, per site.
-- ---------------------------------------------------------------------------------------------
--
-- 0007 renamed `record_start` to `iv_record_start`. This is its daily counterpart, added here
-- rather than there because it describes the table this migration creates.
--
-- THE SEEDED VALUES BELOW ARE BRACKETS, NOT MEASURED START DATES, and they are floors the
-- backfill walks forward from. They come from one-month January probes taken 2026-08-14:
--
--     07010000 St. Louis    daily present at 1990, 2000, 2007, 2010+   -> floor 1990-01-01
--     07032000 Memphis      daily present at 1990, 2000, 2007, 2010+   -> floor 1990-01-01
--     07289000 Vicksburg    absent 1990/2000/2007, present <=2010      -> floor 2008-01-01
--     07374000 Baton Rouge  absent 1990/2000, present 2007             -> floor 2005-01-01
--
-- St. Louis and Memphis very likely publish daily values from well before 1990 - USGS daily
-- records at these gauges run to the nineteenth century. 1990 is a DELIBERATE FLOOR, not a
-- discovered boundary: 35 years is more history than the ten-year seasonal medians and the
-- analog search need, and reaching further is a human's decision to make and seed, not an
-- agent's to assume.
--
-- THE BACKFILL DOES NOT UPDATE THESE VALUES. It reports the first date that actually returned
-- data per site and stops there (CLAUDE.md § 15). A backfill that silently rewrote its own
-- starting assumption could never be caught being wrong, and these are a human's claim about the
-- data (CLAUDE.md § 1). Live verification step 6 is where they get reconciled, in a NEW numbered
-- migration.
--
-- Empty windows before a site's real start are ordinary and simply advance; a window entirely
-- outside the period of record returns a non-JSON body, which is a hard failure naming the seed
-- as the thing to fix.

ALTER TABLE gauges ADD COLUMN dv_record_start date;

COMMENT ON COLUMN gauges.dv_record_start IS
    'Earliest-plausible start of this site''s DAILY-values record, seeded by a human as a FLOOR '
    'for the backfill to walk forward from - not a measured boundary. Reconciled against '
    'min(date) in gauge_readings_daily by a new numbered migration; never written by the '
    'backfill itself (CLAUDE.md section 15).';

UPDATE gauges SET dv_record_start = DATE '1990-01-01' WHERE usgs_site_id = '07010000';
UPDATE gauges SET dv_record_start = DATE '1990-01-01' WHERE usgs_site_id = '07032000';
UPDATE gauges SET dv_record_start = DATE '2008-01-01' WHERE usgs_site_id = '07289000';
UPDATE gauges SET dv_record_start = DATE '2005-01-01' WHERE usgs_site_id = '07374000';

-- Applied AFTER the updates above, so the constraint is proved against the seeded rows rather
-- than trusted. A site with no daily floor would have the backfill either skip it silently or
-- walk from an arbitrary default - both of which produce a site that looks ingested and is not.
ALTER TABLE gauges ALTER COLUMN dv_record_start SET NOT NULL;
