"""Writing daily values, and the scheduled poll that keeps them current.

The daily counterpart to usgs_ingest.py, and deliberately the same shape: a reader finding their
way around one of these should find the other where they expect it. The differences are the ones
CLAUDE.md § 15 is about - a calendar-date key that includes the statistic code, and a poll whose
cadence follows publication rather than measurement.

NOTE ON THE FILE LIST: this module was not in the Phase 3.5 brief's Create list. It is here
because the brief's decision 9 requires a `usgs_daily_ingest` cadence entry, and a cadence entry
with no registered function makes `build_scheduler()` refuse to start (by design - a cadence entry
that never fires is reported overdue forever). The alternative was to put the write path and the
scheduled job inside daily_backfill.py, which would have put a scheduled unit in a module whose
name says it is a CLI. Mirroring the Phase 3 split was worth one extra file.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.orchestration import session
from app.ingest import gauges as gauges_module
from app.ingest.usgs_daily_client import STAT_MEAN, UsgsDailyClient
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "usgs_daily_ingest"

TABLE = "gauge_readings_daily"

# How far back the daily poll reaches beyond the newest date it already holds.
#
# SEVEN DAYS, because daily values are REVISED after publication: a value published today as
# provisional is republished within a week or two as approved, sometimes with a different number.
# The upsert makes the overlap free - a re-fetched unchanged value writes nothing and counts
# nothing - so the only cost is one slightly larger request per day.
OVERLAP_DAYS = 7

# What the poll asks for when a site has no daily rows at all. Not dv_record_start: that would
# have a daily job attempt a 35-year backfill every day, and max_instances=1 would leave it
# permanently running rather than either working or broken. The backfill is a separate CLI.
COLD_START_DAYS = 30

BATCH_SIZE = 1000


UPSERT_SQL = """
INSERT INTO gauge_readings_daily
    (usgs_site_id, date, param_code, stat_cd, value, qualifiers)
VALUES {placeholders}
ON CONFLICT (usgs_site_id, date, param_code, stat_cd) DO UPDATE
    SET value = EXCLUDED.value,
        qualifiers = EXCLUDED.qualifiers
    WHERE (gauge_readings_daily.value, gauge_readings_daily.qualifiers)
       IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.qualifiers)
RETURNING 1
"""


def _deduplicate(readings):
    """Collapse repeated natural keys within one batch, keeping the last occurrence.

    Required for correctness, not an optimization: Postgres rejects the whole statement with "ON
    CONFLICT DO UPDATE command cannot affect row a second time" when one INSERT carries the same
    conflict key twice, and that would take down a 35-year backfill hours into a run.
    """
    by_key = {}
    for reading in readings:
        by_key[
            (reading.usgs_site_id, reading.date, reading.param_code, reading.stat_cd)
        ] = reading
    return list(by_key.values())


def upsert_daily_readings(conn, readings) -> int:
    """Write daily values, returning the number that ACTUALLY changed the database.

    Same discipline as the instantaneous path (CLAUDE.md § 14): `DO UPDATE ... WHERE ... IS
    DISTINCT FROM`, counted from RETURNING. A rerun over unchanged data reports 0 rather than
    reporting its whole input, and the seven-day revision overlap reports only genuine revisions.

    `DO NOTHING` would be worse here than on the instantaneous table, not better: daily values
    are revised as a matter of routine, so freezing the first-published value would mean the
    historical backbone permanently disagreed with USGS.
    """
    deduplicated = _deduplicate(readings)
    if not deduplicated:
        return 0

    written = 0
    for start in range(0, len(deduplicated), BATCH_SIZE):
        batch = deduplicated[start : start + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(batch))
        params: list = []
        for reading in batch:
            params.extend(
                [
                    reading.usgs_site_id,
                    reading.date,
                    reading.param_code,
                    reading.stat_cd,
                    reading.value,
                    list(reading.qualifiers),
                ]
            )
        cursor = conn.execute(UPSERT_SQL.format(placeholders=placeholders), params)
        written += len(cursor.fetchall())

    return written


def latest_date(conn, site_id: str) -> date | None:
    """MAX(date) for one site, or None if it has no daily rows.

    THE RESUME POINT, from the data rather than from a checkpoint (CLAUDE.md § 15). Returns a
    plain date - no timezone anywhere in this path.
    """
    row = conn.execute(
        f"SELECT max(date) FROM {TABLE} WHERE usgs_site_id = %s", (site_id,)
    ).fetchone()
    return row[0] if row else None


def poll_site(conn, client: UsgsDailyClient, gauge, today: date) -> int:
    """Fetch and write one site's recent daily values. Returns rows actually written."""
    newest = latest_date(conn, gauge.usgs_site_id)

    if newest is None:
        start = today - timedelta(days=COLD_START_DAYS)
        logger.warning(
            "site %s has NO daily rows at all; polling only the last %d days. This job will not "
            "backfill it - run `python3 -m app.ingest.daily_backfill --site %s` for that.",
            gauge.usgs_site_id,
            COLD_START_DAYS,
            gauge.usgs_site_id,
        )
    else:
        start = newest - timedelta(days=OVERLAP_DAYS)

    readings = client.fetch_window(
        [gauge.usgs_site_id], gauge.available_params, start, today, stat_codes=(STAT_MEAN,)
    )
    written = upsert_daily_readings(conn, readings)
    conn.commit()

    logger.info(
        "%s: %d daily value(s) received for [%s, %s], %d written",
        gauge.usgs_site_id,
        len(readings),
        start.isoformat(),
        today.isoformat(),
        written,
    )
    return written


def ingest(conn, client: UsgsDailyClient | None = None, today: date | None = None) -> int:
    """Poll every registered gauge for recent daily values. Returns total rows written.

    One site per request, for the same reasons as the instantaneous path: each site has its own
    MAX(date), and one site's failure should not take down the other three.
    """
    client = UsgsDailyClient() if client is None else client
    today = datetime.now(timezone.utc).date() if today is None else today

    total = 0
    for gauge in gauges_module.load(conn):
        total += poll_site(conn, client, gauge, today)
    return total


@job(JOB_NAME)
def usgs_daily_ingest_job(url: str | None = None, client=None, today: date | None = None) -> int:
    """The scheduled unit. Returns rows written, which @job records as rows_written.

    An int, never None. A poll that writes 0 is the normal steady state on any day whose value
    has already been fetched and not revised - the freshness registry, not this number, is what
    notices a source that has gone quiet.
    """
    with session.writing(url) as conn:
        return ingest(conn, client=client, today=today)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    parser = argparse.ArgumentParser(
        description=(
            "USGS daily-values ingest. Normally run by the scheduler; this CLI exists for a "
            "one-off poll. For history, use app.ingest.daily_backfill."
        )
    )
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    import os

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    written = usgs_daily_ingest_job()
    print(f"{written} row(s) written")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
