-- 0012 — gauge_known_gaps: the ranges the source will not serve, recorded where code can read them.
--
-- The full-range measurement of 2026-08-14 (see 0011) found two ranges inside the corridor's
-- record where the daily endpoint returns nothing at all:
--
--   07032000 Memphis       1994-09-30 to 2014-09-30. Twenty years. The endpoint serves nothing in
--                          this range however the request is framed, and the 1990-1994 segment
--                          before it is deliberately not ingested (0011).
--   07374000 Baton Rouge   2023-01-04 to 2023-08-14. Three days of January 2023 are served, then
--                          nothing until the record resumes 2023-08-15.
--
-- ---------------------------------------------------------------------------------------------
-- WHY A TABLE RATHER THAN A COMMENT IN A MIGRATION OR A PARAGRAPH IN CONTEXT.md
-- ---------------------------------------------------------------------------------------------
--
-- A COMMENT CANNOT BE QUERIED BY THE THING THAT NEEDS IT. Two consumers need these ranges:
--
--   * the daily backfill, which has to decide whether a window that returned nothing is expected
--     or is a surprise worth a warning (CLAUDE.md § 2 theme 1: an empty result that nobody looks
--     at is how a silently-vanished series survives for months);
--   * Phase 5's features, which must not interpolate a rolling mean or a seasonal baseline ACROSS
--     A TWENTY-YEAR HOLE. A gap that is invisible to the feature layer becomes a smooth line
--     between 1994 and 2014 that no gauge ever read.
--
-- Neither of them can read markdown. So the gaps are rows.
--
-- ---------------------------------------------------------------------------------------------
-- THIS TABLE IS NOT CONSULTED TO DECIDE WHAT TO REQUEST
-- ---------------------------------------------------------------------------------------------
--
-- The tempting optimization - skip ahead to the end of a known gap instead of walking twenty years
-- of empty windows - IS DELIBERATELY NOT BUILT, and tests/ingest/test_known_gaps.py asserts the
-- backfill still requests every window inside these ranges.
--
-- It would make a human-maintained table decide what NEVER TO ASK FOR. A row with a wrong end date
-- would silently skip real data, and the skip would be indistinguishable from the source not
-- having it - there would be no request, no empty response, and no evidence. Requesting a window
-- and receiving nothing is cheap and self-correcting: the day USGS backfills Memphis's twenty
-- years, the next run picks them up and the WARNING that stops appearing is the notification.

CREATE TABLE gauge_known_gaps (
    usgs_site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- WHICH ENDPOINT this gap is about. A period of record is per entity AND per endpoint
    -- (CLAUDE.md § 15), so a gap is too: Memphis's daily hole from 1994 to 2014 says nothing
    -- about its instantaneous service, which is a rolling window with no history at all.
    source text NOT NULL,

    -- INCLUSIVE OF THE FIRST MISSING DAY AND OF THE LAST MISSING DAY.
    --
    -- Stated here because an off-by-one is silent and directional: read as exclusive, the real
    -- boundary days (the last day that HAS data and the first day that has it again) get treated
    -- as missing, and a genuine one-day edge of a gap disappears into the gap. Baton Rouge's
    -- record resumes 2023-08-15, so gap_end is 2023-08-14 - the last day with nothing.
    gap_start date NOT NULL,
    gap_end date NOT NULL,

    -- What was measured and what was decided, in the row itself. A note that says only "no data"
    -- sends the next reader back to the source to re-establish what this row already knows.
    note text NOT NULL,

    -- One gap per site, endpoint and start. Extending a gap is an UPDATE of its end; discovering a
    -- second, later gap is a new row.
    PRIMARY KEY (usgs_site_id, source, gap_start),

    -- 'dv' and 'iv' are the two endpoints this project speaks to, and they are the same names the
    -- gauges columns use. Constrained rather than free text so a row written as 'daily' does not
    -- sit in the table matching no lookup anything performs.
    CONSTRAINT gauge_known_gaps_source_known
        CHECK (source IN ('dv', 'iv')),

    -- A single missing day is a legitimate gap, so this is >=, not >. Reversed bounds are not: a
    -- gap whose end precedes its start matches no window and would be permanently inert - present
    -- in the table, visible to a reader, and doing nothing.
    CONSTRAINT gauge_known_gaps_ordered
        CHECK (gap_end >= gap_start)
);

COMMENT ON TABLE gauge_known_gaps IS
    'Ranges a source is known not to serve, per site and per endpoint, established by measurement. '
    'Read by the daily backfill to classify an empty window as expected rather than unexplained, '
    'and by the feature layer so nothing interpolates across a gap. NEVER used to decide what not '
    'to request - see the note in migration 0012.';

COMMENT ON COLUMN gauge_known_gaps.gap_start IS
    'FIRST missing day, INCLUSIVE.';

COMMENT ON COLUMN gauge_known_gaps.gap_end IS
    'LAST missing day, INCLUSIVE. The record resumes on gap_end + 1 day.';

COMMENT ON COLUMN gauge_known_gaps.source IS
    'Which endpoint the gap is about: ''dv'' (daily values) or ''iv'' (instantaneous values). A '
    'gap in one says nothing about the other.';


-- ---------------------------------------------------------------------------------------------
-- The seed. Two rows, from the full-range measurement of 2026-08-14 described in 0011.
-- ---------------------------------------------------------------------------------------------
--
-- ONE ROW PER LINE, and the layout is load-bearing for the same reason 0004's is: app/ingest/
-- gauges.py parses this statement so the unit tier - which has no database - can assert the exact
-- boundaries offline. A third row appearing here is a deliberate act and turns
-- tests/ingest/test_known_gaps.py red until it is stated there too.

INSERT INTO gauge_known_gaps (usgs_site_id, source, gap_start, gap_end, note) VALUES
    ('07032000', 'dv', DATE '1994-09-30', DATE '2014-09-30', 'endpoint serves nothing in this range; segment before it deliberately not ingested'),
    ('07374000', 'dv', DATE '2023-01-04', DATE '2023-08-14', 'endpoint serves nothing; record resumes 2023-08-15');
