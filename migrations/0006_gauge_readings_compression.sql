-- 0006 — compression on gauge_readings.
--
-- ITS OWN MIGRATION, SEPARATE FROM THE HYPERTABLE, because the two answer different questions and
-- fail differently. 0005 has to be right before any data exists; this one can be reasoned about,
-- measured, and — if the measurement disappoints — replaced by a later numbered migration
-- without touching the table's structure.
--
-- ---------------------------------------------------------------------------------------------
-- THE RATIO THIS PRODUCES IS THE DELIVERABLE, AND IT IS NOT WRITTEN DOWN HERE
-- ---------------------------------------------------------------------------------------------
--
-- The compression ratio is the number that justifies running TimescaleDB on an EC2 instance over
-- a managed Postgres, and CLAUDE.md § 7 requires every number in the README, the UI, or the
-- resume to be reproducible from a query. So it is MEASURED ON THIS DATA by a human, after the
-- backfill, and written into CONTEXT.md and the README from that measurement.
--
-- No placeholder ratio appears in this repo, and no vendor figure is cited. A plausible number
-- written down in advance is one nobody re-checks, and "the measurement wins" (CLAUDE.md § 0) is
-- not a rule you can follow after publishing the answer. Use:
--
--     python3 -m app.ingest.usgs_ingest --compression-stats
--
-- which runs exactly the query tests/ingest/test_compression.py exercises, so the measurement
-- step cannot fail at reporting time.

ALTER TABLE gauge_readings SET (
    timescaledb.compress,

    -- SEGMENT BY SITE AND PARAMETER. Segment-by columns are stored once per compressed batch
    -- rather than once per row, and — the part that matters more — a query filtering on them can
    -- skip whole batches without decompressing.
    --
    -- param_code LOOKS REDUNDANT TODAY: this commit ingests one parameter, so it has exactly one
    -- distinct value and segments by it for no benefit. KEEP IT. It is correct the day a second
    -- parameter lands, and segmentby cannot be changed on the fly — altering it means
    -- decompressing every chunk in the table first. Paying nothing now to avoid that later is
    -- the whole trade.
    timescaledb.compress_segmentby = 'usgs_site_id, param_code',

    -- Within a segment, ordered by time descending, matching 0005's index and the direction
    -- every read runs in. Ordering is what makes the delta-of-delta and run-length encodings
    -- work on the timestamp and value columns; an unordered batch compresses like noise.
    timescaledb.compress_orderby = 'ts DESC'
);

-- Compress chunks older than 30 days.
--
-- Thirty rather than seven: the incremental poll's overlap window and USGS's own revisions both
-- rewrite recent readings, and a revision landing in a compressed chunk is a decompress-modify-
-- recompress rather than an ordinary UPDATE. Thirty days puts the whole revision-prone tail of
-- the record in uncompressed chunks and leaves the eighteen-year backfill — which nothing
-- rewrites — compressed.
--
-- A CONSEQUENCE WORTH KNOWING BEFORE IT SURPRISES SOMEONE: a correction issued against a reading
-- more than 30 days old still works on this TimescaleDB version, but it is markedly slower than
-- the same write against a recent chunk. That is a rare path (USGS approvals of old data), not
-- the ingest's normal one. If it ever becomes routine, the fix is to widen this interval, not to
-- stop upserting.
SELECT add_compression_policy('gauge_readings', INTERVAL '30 days');
