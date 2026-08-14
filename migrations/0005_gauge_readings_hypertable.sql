-- 0005 — gauge_readings, and the hypertable conversion.
--
-- THE ORDER HERE IS THE DECISION: create_hypertable runs on an EMPTY table, in the same
-- migration that creates it, before the backfill puts eighteen years in it. Converting a
-- populated table works, but it rewrites every row into chunks while holding locks, and the
-- window in which someone discovers the conversion was forgotten is the window in which the
-- table is largest. An empty conversion is instant and cannot half-finish.
--
-- Chunk interval is 7 DAYS, which is far below the default. The default assumes a table where a
-- chunk is a meaningful working set; four sites at 15-to-60-minute cadence produce on the order
-- of a few thousand rows a week. Small chunks are the right trade here anyway: the queries this
-- table exists for are windowed ("the last 14 days", "this window in 2012"), so chunk exclusion
-- is doing the real work, and compression operates per chunk.

CREATE TABLE gauge_readings (
    -- FK to gauges. A reading for a site that is not in the registry is a reading nobody
    -- decided to collect, and it would be invisible to every per-site query that starts from
    -- the registry. Rejecting it at write time is much cheaper than finding it later.
    usgs_site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- UTC, always. The API returns local offsets ('2026-08-01T00:00:00.000-05:00') and these
    -- sites span Central and Eastern observance. Storing the wall clock and dropping the offset
    -- would shift an hour of readings twice a year, silently, in a way that looks like the river
    -- did something interesting. app/ingest/usgs_client.py converts on parse; timestamptz is
    -- what makes a mistake there impossible to store.
    ts timestamptz NOT NULL,

    -- '00060' discharge (ft3/s), '00065' gage height (ft). Text, not an enum: USGS parameter
    -- codes are a published five-digit vocabulary, and pinning a subset of it into a Postgres
    -- type means a migration every time a new one is ingested.
    param_code text NOT NULL,

    value double precision NOT NULL,

    -- The USGS qualifier codes for this reading, as published: 'P' provisional, 'A' approved,
    -- 'e' estimated, and others.
    --
    -- PRESERVED, NEVER FILTERED. Dropping provisional readings on the way in would discard most
    -- of the recent record — which is the part the signal actually runs on — and would do it
    -- silently, as a smaller row count. The provisional/approved distinction is a fact about the
    -- reading that downstream layers are entitled to see and gate on themselves.
    qualifiers text[],

    -- The natural key, and the conflict target every write upserts on. TimescaleDB requires the
    -- partitioning column (ts) to be part of any unique constraint, which it is.
    PRIMARY KEY (usgs_site_id, ts, param_code)
);

-- by_range() rather than the legacy positional form. Both work on the pinned image
-- (timescale/timescaledb:2.26.2-pg16), but the positional signature is deprecated and emits a
-- notice; building against the deprecated spelling of a pinned dependency is how a routine image
-- bump turns into a migration that no longer applies.
SELECT create_hypertable('gauge_readings', by_range('ts', INTERVAL '7 days'));

-- The per-site time-ordered lookup: "this site, this window", which is every query the analog
-- search makes. The hypertable's own chunk exclusion narrows to the right chunks; this narrows
-- within them.
--
-- ts DESC because the incremental poll's resume query is MAX(ts) per site and the UI reads the
-- most recent end of the record. It is the same order compression sorts by (see 0006), so the
-- two agree about what "recent" means.
CREATE INDEX gauge_readings_site_param_ts_idx
    ON gauge_readings (usgs_site_id, param_code, ts DESC);
