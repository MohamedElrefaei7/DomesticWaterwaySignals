"""Writing readings, and the scheduled poll that produces them.

CLAUDE.md § 14. Two decisions here are load-bearing and both have a shorter wrong version.

1. WRITES ARE UPSERTS ON THE NATURAL KEY, AND `DO NOTHING` IS THE TEMPTING WRONG ONE.

   USGS data is provisional and revised: a reading published today as 'P' is republished weeks
   later as 'A', sometimes with a different value. The natural key is (site, ts, param), so:

     * insert-only      -> duplicate-key errors, or duplicate rows if the key is dropped
     * DO NOTHING       -> reruns are safe and the provisional value is kept FOREVER, silently,
                           over the corrected one. This is the one that looks right.
     * DO UPDATE        -> the revision lands. Correct.

   `DO NOTHING` deserves the extra sentence because it passes every test that checks for
   duplicates, makes the backfill idempotent, and reads like the careful choice. What it actually
   does is freeze bad data permanently while reporting success.

2. `rows_written` MEANS ROWS THAT ACTUALLY CHANGED THE DATABASE.

   CLAUDE.md § 4: rows written to the database, never rows examined or processed. With a plain
   `DO UPDATE`, re-running the backfill over data it already ingested would report millions of
   rows written, every time, because every row "updates". That number would be a lie of exactly
   the shape § 2's theme 1 warns about - large, reassuring, and unrelated to what happened.

   So the upsert carries `WHERE (value, qualifiers) IS DISTINCT FROM (EXCLUDED...)` and counts
   what RETURNING hands back. A rerun over unchanged data reports 0. The overlap window reports
   only genuine revisions. 0 and NULL stay distinct, and both stay meaningful.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.ingest import gauges as gauges_module
from app.ingest.usgs_client import UsgsClient
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "usgs_ingest"

# How far back the hourly poll reaches beyond the newest reading it already has.
#
# The upsert makes overlap free - a re-fetched unchanged reading writes nothing and counts
# nothing - so this is bought cheaply. It buys two things: a reading that arrived late (USGS
# transmits hourly, but not punctually) and a reading that was revised after we first stored it.
# Without it, anything that landed in the gap between two polls is missed permanently, because
# nothing ever looks at that window again.
OVERLAP = timedelta(hours=2)

# What the poll asks for when a site has NO rows at all.
#
# Not record_start: that would make an hourly scheduled job attempt an eighteen-year backfill,
# every hour, and `max_instances=1` would then have it perpetually still running. The backfill is
# a CLI a human runs (see backfill.py); this window just keeps the site current until they do,
# and logs loudly so the empty site is not mistaken for a working one.
COLD_START_WINDOW = timedelta(hours=6)

# Rows per INSERT statement. Large enough that an eighteen-year backfill is not a round trip per
# reading, small enough that one statement's parameter list stays well inside Postgres' limit.
BATCH_SIZE = 1000


UPSERT_SQL = """
INSERT INTO gauge_readings (usgs_site_id, ts, param_code, value, qualifiers)
VALUES {placeholders}
ON CONFLICT (usgs_site_id, ts, param_code) DO UPDATE
    SET value = EXCLUDED.value,
        qualifiers = EXCLUDED.qualifiers
    WHERE (gauge_readings.value, gauge_readings.qualifiers)
       IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.qualifiers)
RETURNING 1
"""


def _deduplicate(readings):
    """Collapse repeated natural keys within one batch, keeping the last occurrence.

    NOT AN OPTIMIZATION - it is required for correctness, and the failure without it is abrupt:
    Postgres rejects the whole statement with "ON CONFLICT DO UPDATE command cannot affect row a
    second time" when one INSERT carries the same conflict key twice. A series with two
    measurement methods reporting the same instant does exactly that, and it would take down a
    backfill hours into a run.

    Last occurrence wins, matching what the upsert itself would do if the rows arrived in
    separate statements.
    """
    by_key = {}
    for reading in readings:
        by_key[(reading.usgs_site_id, reading.ts, reading.param_code)] = reading
    return list(by_key.values())


def upsert_readings(conn, readings) -> int:
    """Write readings, returning the number that ACTUALLY changed the database.

    Not the number parsed, not the number sent, not the number that matched. Rows inserted plus
    rows whose value or qualifiers genuinely differed from what was already stored. The count
    comes from RETURNING, which under `DO UPDATE ... WHERE` yields a row only for the writes that
    happened - so this is measured rather than inferred.
    """
    deduplicated = _deduplicate(readings)
    if not deduplicated:
        return 0

    written = 0
    for start in range(0, len(deduplicated), BATCH_SIZE):
        batch = deduplicated[start : start + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s, %s, %s)"] * len(batch))
        params: list = []
        for reading in batch:
            params.extend(
                [
                    reading.usgs_site_id,
                    reading.ts,
                    reading.param_code,
                    reading.value,
                    list(reading.qualifiers),
                ]
            )
        cursor = conn.execute(UPSERT_SQL.format(placeholders=placeholders), params)
        written += len(cursor.fetchall())

    return written


def latest_ts(conn, site_id: str) -> datetime | None:
    """MAX(ts) for one site, or None if it has no rows.

    THE RESUME POINT, and it comes from the data rather than from a checkpoint (CLAUDE.md § 14).
    A checkpoint file or a progress table is a second record of the same fact, and when the two
    disagree it is the checkpoint that gets believed - so a backfill that crashed after writing
    rows but before updating its checkpoint re-fetches work it already did, and one that updated
    its checkpoint first skips work it never did. The second failure is silent.
    """
    row = conn.execute(
        "SELECT max(ts) FROM gauge_readings WHERE usgs_site_id = %s", (site_id,)
    ).fetchone()
    return row[0] if row else None


def poll_site(conn, client: UsgsClient, gauge, now: datetime) -> int:
    """Fetch and write one site's recent readings. Returns rows actually written."""
    newest = latest_ts(conn, gauge.usgs_site_id)

    if newest is None:
        start = now - COLD_START_WINDOW
        logger.warning(
            "site %s has NO readings at all; polling only the last %s. This job will not "
            "backfill it - run `python3 -m app.ingest.backfill --site %s` for that.",
            gauge.usgs_site_id,
            COLD_START_WINDOW,
            gauge.usgs_site_id,
        )
    else:
        start = newest - OVERLAP

    readings = client.fetch_window(
        [gauge.usgs_site_id], gauge.available_params, start, now
    )
    written = upsert_readings(conn, readings)
    conn.commit()

    logger.info(
        "%s: %d reading(s) received for [%s, %s], %d written",
        gauge.usgs_site_id,
        len(readings),
        start.isoformat(),
        now.isoformat(),
        written,
    )
    return written


def ingest(conn, client: UsgsClient | None = None, now: datetime | None = None) -> int:
    """Poll every registered gauge. Returns total rows written.

    ONE SITE PER REQUEST, deliberately. Batching all four into a single call would be one round
    trip instead of four, and it would break the resume logic: each site has its own MAX(ts), so
    a shared window would either re-fetch far more than needed for the freshest site or under-
    fetch for the stalest. It would also make a single site's failure take down the other three.
    """
    client = UsgsClient() if client is None else client
    now = datetime.now(timezone.utc) if now is None else now

    total = 0
    for gauge in gauges_module.load(conn):
        total += poll_site(conn, client, gauge, now)
    return total


@job(JOB_NAME)
def usgs_ingest_job(url: str | None = None, client=None, now: datetime | None = None) -> int:
    """The scheduled unit. Returns rows written, which @job records as rows_written.

    An int, never None: this job writes rows, so 0 is a meaningful statement about a run that
    found nothing new and NULL would mean "does not count rows" (CLAUDE.md § 4). A poll that
    genuinely writes nothing - because nothing was revised and no new reading arrived - reports
    0, and the heartbeat's freshness check, not this number, is what notices a source that has
    gone quiet.
    """
    with db.connection(url) as conn:
        return ingest(conn, client=client, now=now)


# ---------------------------------------------------------------------------------------------
# The compression measurement.
# ---------------------------------------------------------------------------------------------
#
# Lives here rather than in verify/ because it is not a check - nothing passes or fails. It
# reports two byte counts and their ratio, and the ratio is the number that justifies running
# TimescaleDB over a managed Postgres (CLAUDE.md § 7: every published number is reproducible from
# a query). It is the query live verification step 6 runs.

# TimescaleDB renamed this function's family in the 2.18 "columnstore" rework and kept the old
# name working. Which one exists is a property of the SERVER, so it is discovered from the
# server's own catalog rather than assumed from the image tag - the same reasoning as reading the
# apt codename from /etc/os-release instead of hardcoding it (CLAUDE.md § 10).
_STATS_FUNCTIONS = ("hypertable_compression_stats", "hypertable_columnstore_stats")


def _stats_function(conn) -> str:
    available = {
        row[0]
        for row in conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = ANY(%s)",
            (list(_STATS_FUNCTIONS),),
        ).fetchall()
    }
    for name in _STATS_FUNCTIONS:
        if name in available:
            return name
    raise RuntimeError(
        f"this TimescaleDB server exposes neither {' nor '.join(_STATS_FUNCTIONS)}. "
        f"The compression measurement cannot be taken, and a ratio must NOT be quoted from any "
        f"other source (CLAUDE.md § 7)."
    )


# The same rename touched the settings view. Same reasoning: ask the server what it has.
_SETTINGS_VIEWS = (
    "timescaledb_information.compression_settings",
    "timescaledb_information.hypertable_compression_settings",
)


def _settings_view(conn) -> str:
    for candidate in _SETTINGS_VIEWS:
        schema, _, name = candidate.partition(".")
        exists = conn.execute(
            "SELECT 1 FROM pg_views WHERE schemaname = %s AND viewname = %s",
            (schema, name),
        ).fetchone()
        if exists:
            return candidate
    raise RuntimeError(
        f"this TimescaleDB server exposes none of {list(_SETTINGS_VIEWS)}; the compression "
        f"settings cannot be read back and therefore cannot be verified."
    )


def compression_settings(conn, table: str = "gauge_readings") -> dict:
    """The segmentby and orderby columns actually in effect, read back from the server.

    Read back rather than trusted from the migration text. `ALTER TABLE ... SET
    (timescaledb.compress_segmentby = ...)` accepts a column list without complaint; whether it
    took effect the way the file intended is a property of the server, and CLAUDE.md § 13 is
    about checking the thing rather than the statement that was supposed to configure it.
    """
    view = _settings_view(conn)
    rows = conn.execute(
        f"SELECT attname, segmentby_column_index, orderby_column_index, orderby_asc"
        f" FROM {view} WHERE hypertable_name = %s",
        (table,),
    ).fetchall()

    segmentby = [r[0] for r in sorted((r for r in rows if r[1] is not None), key=lambda r: r[1])]
    orderby = [
        (r[0], "ASC" if r[3] else "DESC")
        for r in sorted((r for r in rows if r[2] is not None), key=lambda r: r[2])
    ]
    return {"view": view, "segmentby": segmentby, "orderby": orderby}


def compression_stats(conn, table: str = "gauge_readings") -> dict:
    """Uncompressed and compressed total bytes for a hypertable, plus the ratio.

    Returns None for the sizes when nothing has been compressed yet, rather than 0 - a table
    whose chunks are all still uncompressed has no compressed size, and reporting that as zero
    bytes would make the ratio look infinite.
    """
    function = _stats_function(conn)
    row = conn.execute(
        f"SELECT before_compression_total_bytes, after_compression_total_bytes,"
        f" number_compressed_chunks FROM {function}(%s)",
        (table,),
    ).fetchone()

    before, after, chunks = (None, None, 0) if row is None else row
    ratio = None
    if before and after:
        ratio = before / after

    return {
        "function": function,
        "before_bytes": before,
        "after_bytes": after,
        "compressed_chunks": chunks,
        "ratio": ratio,
    }


def _print_compression_stats(url: str | None = None) -> int:  # pragma: no cover - the CLI path
    with db.connection(url) as conn:
        stats = compression_stats(conn)

    print(f"source function:     {stats['function']}()")
    print(f"compressed chunks:   {stats['compressed_chunks']}")
    if stats["before_bytes"] is None or stats["after_bytes"] is None:
        print(
            "\nNo chunks are compressed yet, so there is no ratio to report.\n"
            "Compress the eligible chunks first:\n"
            "    SELECT compress_chunk(c) FROM show_chunks('gauge_readings',\n"
            "        older_than => INTERVAL '30 days') c;\n"
            "Do NOT quote a ratio from any other source (CLAUDE.md § 7)."
        )
        return 1

    print(f"before compression:  {stats['before_bytes']:>15,} bytes")
    print(f"after compression:   {stats['after_bytes']:>15,} bytes")
    print(f"ratio:               {stats['ratio']:.2f}x")
    print(
        "\nThis is the number that justifies TimescaleDB over a managed Postgres. Record it in "
        "CONTEXT.md and the README exactly as measured - including if it disappoints. The "
        "measurement wins (CLAUDE.md § 0)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    parser = argparse.ArgumentParser(
        description=(
            "USGS instantaneous-values ingest. Normally run by the scheduler; this CLI exists "
            "for a one-off poll and for the compression measurement."
        )
    )
    parser.add_argument(
        "--compression-stats",
        action="store_true",
        help="report gauge_readings' before/after compression sizes and change nothing",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    import os

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    if args.compression_stats:
        return _print_compression_stats()

    written = usgs_ingest_job()
    print(f"{written} row(s) written")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
