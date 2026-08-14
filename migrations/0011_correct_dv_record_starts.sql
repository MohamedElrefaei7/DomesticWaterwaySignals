-- 0011 — the record starts, corrected to what was measured.
--
-- 0008 seeded `dv_record_start` from ONE-MONTH JANUARY PROBES: a site was asked for January 1990,
-- January 2000, January 2007 and January 2010, and the earliest month that answered became the
-- floor. That method measures PRESENCE IN ONE WINDOW. It does not measure depth, and it was wrong
-- for three of the four sites.
--
-- The correct instrument is a SINGLE FULL-RANGE REQUEST PER SITE, counting values per year. Run
-- 2026-08-14 against the daily-values endpoint, `00060`, `statCd=00003`, requested 1990-01-01 to
-- 2026-08-01. Every figure below is from that run:
--
--   07010000 St. Louis     365/366 values every year, 1990 through 2026. Unbroken.
--   07032000 Memphis       365 in 1990-1993, 272 in 1994, then NOTHING until 2014-10-01,
--                          dense from there to 2026.
--   07289000 Vicksburg     first value 2008-01-01, dense and unbroken to 2026.
--   07374000 Baton Rouge   first value 2004-03-17, dense except 2023: three days in January,
--                          then nothing until 2023-08-15.
--
-- ---------------------------------------------------------------------------------------------
-- ST. LOUIS'S 1990-01-01 IS A BOUND, NOT A DISCOVERED START
-- ---------------------------------------------------------------------------------------------
--
-- The request floor was 1990-01-01 and St. Louis answered from the first day of it, so its real
-- record begins somewhere before 1990 - USGS daily records at this gauge run to the nineteenth
-- century. The value is unchanged from 0008 and its MEANING is different: it is the earliest date
-- this project has asked for, not the earliest date the site serves. Reaching further back is a
-- human's decision to make and seed (CLAUDE.md § 1); 35 years is already more history than the
-- ten-year seasonal medians and the analog search need.
--
-- ---------------------------------------------------------------------------------------------
-- MEMPHIS IS SEEDED AT 2014-10-01 AND THE 1990-1994 SEGMENT IS DELIBERATELY ABANDONED
-- ---------------------------------------------------------------------------------------------
--
-- THE ENDPOINT WILL SERVE MEMPHIS 1990-1994. This seed deliberately does not collect it, and the
-- question "Memphis has data back to 1990, why does the seed say 2014" is exactly what a future
-- session will ask and answer by "fixing" this file. The reasoning, so that it meets an argument
-- rather than a blank line:
--
--   * Collecting those four years means walking TWENTY YEARS OF EMPTY WINDOWS on every backfill
--     to reach them.
--   * What they buy is a DISCONTINUOUS series. For seasonal adjustment a discontinuous series is
--     worse than a shorter continuous one: a model fitted across a twenty-year hole learns the
--     discontinuity, not the season.
--
-- So Memphis's floor is the first date of its continuous modern segment. The abandoned segment is
-- recorded as a known gap in 0012, not merely forgotten.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THE CATALOG IS NOT THE SEED SOURCE EITHER
-- ---------------------------------------------------------------------------------------------
--
-- `seriesCatalogOutput` reports Memphis `00060/00003` as 1933-01-01 to 2026-08-12 with 26,886
-- values. The DV endpoint WILL NOT SERVE anything between 1994-09 and 2014-10 regardless of how
-- the request is framed - `statCd=00003` stated explicitly, different window sizes, and
-- `format=rdb` were all tried on 2026-08-14. The catalog reports an ENVELOPE and a COUNT; it does
-- not tell you what the endpoint returns. Seed from what the endpoint serves.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS IS A NEW MIGRATION RATHER THAN AN EDIT TO 0008
-- ---------------------------------------------------------------------------------------------
--
-- The runner's checksum guard would refuse an edit to an applied file (CLAUDE.md § 3), but that is
-- not the reason. A migration file records WHAT WAS BELIEVED WHEN IT WAS WRITTEN. 0008 believed a
-- January probe established a period of record; that belief is why three of these four values were
-- wrong, and the correction is itself a fact worth having in the sequence. Editing 0008 would
-- leave a repository in which the mistake never happened.

UPDATE gauges SET dv_record_start = DATE '1990-01-01' WHERE usgs_site_id = '07010000';
UPDATE gauges SET dv_record_start = DATE '2014-10-01' WHERE usgs_site_id = '07032000';
UPDATE gauges SET dv_record_start = DATE '2008-01-01' WHERE usgs_site_id = '07289000';
UPDATE gauges SET dv_record_start = DATE '2004-03-17' WHERE usgs_site_id = '07374000';

COMMENT ON COLUMN gauges.dv_record_start IS
    'First date of this site''s CONTINUOUS daily-values record as MEASURED on 2026-08-14 by a '
    'single full-range request counting values per year - not a probe of sample windows, and not '
    'the catalog''s envelope (CLAUDE.md section 15). 07010000 is a BOUND rather than a discovered '
    'start: its record predates the 1990 request floor. 07032000 is the start of its modern '
    'segment; the endpoint also serves 1990-1994 there and this project deliberately does not '
    'ingest it - see migration 0011 and gauge_known_gaps. Never written by the backfill itself.';


-- ---------------------------------------------------------------------------------------------
-- iv_record_start is NULL at the three rolling-retention sites, because a rolling window is not
-- a start date.
-- ---------------------------------------------------------------------------------------------
--
-- Measured 2026-08-14 (recorded in 0007 and CONTEXT.md): Memphis, Vicksburg and Baton Rouge serve
-- INSTANTANEOUS values on a rolling window of roughly two months. The values 0004 seeded for them
-- were the Phase 3 assumption that the measurement contradicted, and they have been carried since
-- as known-wrong dates.
--
-- ANY DATE IN THIS COLUMN FOR THOSE SITES IS A CLAIM THAT IS FALSE WITHIN WEEKS. Today's honest
-- answer is 2026-06-something; next month it is 2026-07-something. A column holding a value that
-- decays is worse than an empty one, because nothing about reading it says it has expired.
--
-- St. Louis keeps 2007-10-01, which is a real fixed start: it is the site whose instantaneous
-- record actually carries depth, and the 223,706 rows Phase 3 loaded came from it.
--
-- CONSEQUENCE, STATED RATHER THAN DISCOVERED: the instantaneous backfill cannot run for the three
-- NULL sites. It already could not - it aborts at their first window on a missing series, which is
-- § 14's guard working - and this commit does not change that. The likely resolution is that the
-- IV backfill does not apply to rolling-retention sites at all and the incremental poll is the
-- only path to their instantaneous data. That is a human's decision and the first candidate for
-- the next ingest commit; see CONTEXT.md.

ALTER TABLE gauges ALTER COLUMN iv_record_start DROP NOT NULL;

UPDATE gauges SET iv_record_start = NULL WHERE usgs_site_id = '07032000';
UPDATE gauges SET iv_record_start = NULL WHERE usgs_site_id = '07289000';
UPDATE gauges SET iv_record_start = NULL WHERE usgs_site_id = '07374000';

-- NULL IS THE PERMITTED WAY TO SAY "NO FIXED START". A SENTINEL DATE IS NOT.
--
-- The constraint exists because the alternative a future session reaches for is not a wrong date -
-- it is a placeholder: 1970-01-01, or 0001-01-01, or 1900-01-01, meaning "unknown" while typing as
-- a date. Every one of those reads as a real record start to the backfill, which would then walk
-- from the epoch through fifty years of empty windows for a site whose service holds two months.
-- This catches the ones far enough out to be unambiguous; it cannot catch a plausible-looking
-- wrong date, and nothing at this layer can. That is what the column comment is for.
ALTER TABLE gauges ADD CONSTRAINT gauges_iv_record_start_null_not_sentinel
    CHECK (iv_record_start IS NULL OR iv_record_start > DATE '1900-01-01');

COMMENT ON COLUMN gauges.iv_record_start IS
    'Start of this site''s INSTANTANEOUS-values record. NULL MEANS ROLLING RETENTION: the site '
    'serves a moving window of recent weeks and has no fixed start, so there is no date that '
    'would stay true. NULL at 07032000, 07289000 and 07374000 (measured 2026-08-14); 07010000 '
    'keeps 2007-10-01, which is real. NULL is not "unknown" and must never be filled with a '
    'sentinel date - see the CHECK on this column and CLAUDE.md section 15.';
