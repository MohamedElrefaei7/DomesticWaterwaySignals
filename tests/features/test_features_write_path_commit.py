"""Integration tier — the feature build's writes are asserted from a connection it did not use.

See tests/ingest/test_ingest_write_paths_commit.py for the full reasoning and the audit that produced
this file. In short: `app/features/build.py:222`'s commit was deletable on 2026-08-17 with all 40
tests in tests/features/ still green, because every one of them calls `build.build(migrated_db,
...)` and asserts with `migrated_db.execute(...)` — the writing session, which cannot tell a
committed row from an uncommitted one.

A DERIVED TABLE IS THE WORST PLACE FOR THIS DEFECT TO HIDE. An ingest table that silently fails to
commit goes stale, and the heartbeat's freshness registry notices. A feature build that silently
fails to commit leaves every table it READS perfectly fresh while writing nothing — CLAUDE.md § 17
already says a stopped build is invisible from the data, and an uncommitted build is a stopped
build that also reports success.
"""

from datetime import date, timedelta

import pytest

from app import db
from app.features import build

pytestmark = pytest.mark.integration

START = date(2022, 6, 1)
END = date(2022, 10, 31)


def _seed(seed_readings):
    """Enough daily discharge and weekly rates for the build to produce all three tables."""
    from tests.features.conftest import ST_LOUIS

    seed_readings.daily(
        ST_LOUIS,
        [(START + timedelta(days=i), 200000.0 - 300.0 * i) for i in range((END - START).days + 1)],
    )
    seed_readings.rates(
        [(date(2022, 8, 4) + timedelta(days=7 * i), 400.0 + 50.0 * i) for i in range(12)]
    )


def _counts(database_url):
    """The three tables' row counts, read on a connection opened after the writer closed."""
    with db.connection(database_url) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("gauge_daily", "features", "targets")
        }


def test_features_build_rows_visible_from_new_connection(
    migrated_db, database_url, seed_readings
):
    """`features_build_job` opens its own connection; all three tables must outlive it.

    Drives the DECORATED job rather than `build()`, because the job is the path the scheduler
    takes and it is the path that was never invoked by any test before this one.
    """
    _seed(seed_readings)

    written = build.features_build_job(url=database_url, today=END)

    assert written > 0, (
        f"the build job reported {written} rows written, so this test would pass on a job that "
        f"did nothing and proves nothing about commits"
    )

    counts = _counts(database_url)
    assert counts["gauge_daily"] > 0 and counts["features"] > 0 and counts["targets"] > 0, (
        f"the build job reported {written} rows written and a new connection sees {counts}. The "
        f"build was rolled back on close: db.connection commits nothing implicitly, and every "
        f"table the build READS stays fresh while it writes nothing."
    )
    assert sum(counts.values()) == written, (
        f"the job reported {written} rows written but a new connection sees {sum(counts.values())} "
        f"across the three tables: {counts}. Part of the build did not survive its connection."
    )


def test_features_build_cli_path_rows_visible_from_new_connection(
    migrated_db, database_url, seed_readings
):
    """The CLI shape: `with db.connection() as conn: build(conn, start, end)`, no commit outside.

    `app/features/build.py:330`'s `main()` never commits at the call site, so `build()`'s own
    commit is the only thing between a rebuild and an empty table. A separate test from the job
    above because the two enter through different connections and only one of them is scheduled.
    """
    _seed(seed_readings)

    with db.connection(database_url) as conn:
        result = build.build(conn, START, END)

    assert result["feature_rows"] > 0, f"the build wrote no features: {result}"

    counts = _counts(database_url)
    assert counts["features"] == result["feature_rows"], (
        f"build() reported {result['feature_rows']} feature rows and a new connection sees "
        f"{counts['features']}. The CLI path commits nothing of its own, so this is build()'s "
        f"commit failing to reach the database."
    )
