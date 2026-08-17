"""Integration tier — every ingest write path is asserted from a connection it did not write on.

WHY THIS FILE EXISTS, AND WHY IT IS NOT REDUNDANT WITH THE SUITE BESIDE IT.

Phase 11 shipped a nightly backup whose `backups` INSERT was silently rolled back: `db.connection`
deliberately commits nothing implicitly (app/db.py), the job never called `commit()`, and every
layer above agreed with itself — the function returned, `job_runs` recorded success, and S3 held a
verified archive. The only thing missing was the row.

Stage B's audit asked the second question, which is the one that matters: COULD THE EXISTING TESTS
HAVE TOLD? Measured on 2026-08-17 by deleting each write path's `conn.commit()` and running the
tests that cover it:

    CAUGHT      app/ingest/usgs_ingest.py:171          test_ingest_registers_a_job_run_row
    CAUGHT      app/orchestration/backup.py:555        test_backup_integration_end_to_end
    NOT CAUGHT  app/ingest/usgs_daily_ingest.py:151    114 passed
    NOT CAUGHT  app/ingest/usda_rates.py:484           114 passed
    NOT CAUGHT  app/ingest/usda_movements.py:314       114 passed
    NOT CAUGHT  app/ingest/backfill.py:262             114 passed
    NOT CAUGHT  app/ingest/daily_backfill.py:375       114 passed
    NOT CAUGHT  app/ingest/usda_backfill.py:180        114 passed

Eight of ten commits were deletable with the suite green. Two causes, both structural rather than
careless: five of the eight job entrypoints were never invoked by any test at all, and where a path
was tested it was called as `ingest(migrated_db, ...)` and asserted with `migrated_db.execute(...)`
— the writing session, which cannot distinguish committed from uncommitted. Several of those tests
additionally call `migrated_db.commit()` themselves, so they would have committed the data even
with the production commit deleted.

THE PROPERTY EVERY TEST BELOW HOLDS, AND THE CLAIM IT RESTS ON.

Each test drives the real entrypoint, lets the writer's connection CLOSE, and only then opens a new
one to look. The claim relied upon is the strong one: THE WRITER'S TRANSACTION HAS ENDED before the
reader opens, because the writer's connection is closed — either by the job's own `with
db.connection(url)` block, or by this file's `_write_then_close` helper. That is a statement about
transactions, not about object identity.

The weaker claim — "it is a different session object" — is deliberately NOT what these tests lean
on, because it is not the same claim and it is the one that quietly stops being true. It does also
happen to hold here: `db.connect()` calls `psycopg.connect()` directly with no pool anywhere in
this project, so every `db.connection()` is a distinct DBAPI connection. If a pool is ever
introduced, these tests keep working, because closing the writer first is what they depend on.

Asserting through the writing session is the tempting version of every test here: the session is
already open and the rows are visibly in it. It is exactly the test that passed on the broken
backup job.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app import db
from app.ingest import (
    backfill as iv_backfill,
    daily_backfill,
    usda_backfill,
    usda_movements,
    usda_rates,
    usgs_daily_ingest,
    usgs_ingest,
)
from app.ingest.socrata_client import SocrataClient
from app.ingest.usgs_client import PARAM_DISCHARGE, Reading
from app.ingest.usgs_daily_client import STAT_MEAN, DailyReading

pytestmark = pytest.mark.integration

SITE = "07010000"
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)
TODAY = date(2026, 8, 12)

NEARBY = "barge_rates_nearby"
ONE_MONTH = "barge_rates_1month"
THREE_MONTH = "barge_rates_3month"


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _write_then_close(database_url, work):
    """Run `work(conn)` on a connection of its own and CLOSE it, committing nothing here.

    This is the CLI shape from `main()` in each backfill module — `with db.connection() as conn:
    backfill(conn, ...)` with no commit at the call site — so the only thing that can make the rows
    survive is a commit inside the code under test. Closing without committing is what makes the
    subsequent read-back meaningful: psycopg discards an open transaction on close.
    """
    with db.connection(database_url) as conn:
        return work(conn)


def _count(database_url, table, where="TRUE", params=()):
    """Count rows on a NEW connection. Opened after the writer above has closed."""
    with db.connection(database_url) as conn:
        return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def reading(offset_minutes, value, site=SITE):
    return Reading(
        usgs_site_id=site,
        ts=BASE + timedelta(minutes=offset_minutes),
        param_code=PARAM_DISCHARGE,
        value=value,
        qualifiers=("P",),
    )


def daily_reading(day_offset, value, site=SITE):
    return DailyReading(
        usgs_site_id=site,
        date=TODAY - timedelta(days=day_offset),
        param_code=PARAM_DISCHARGE,
        stat_cd=STAT_MEAN,
        value=value,
        qualifiers=("A",),
    )


class StubIvClient:
    def __init__(self, batches):
        self.batches = list(batches)

    def fetch_window(self, site_ids, param_codes, start, end):
        return self.batches.pop(0) if self.batches else []


class StubDailyClient:
    def __init__(self, batches):
        self.batches = list(batches)

    def fetch_window(self, site_ids, param_codes, start, end, stat_codes=None):
        return self.batches.pop(0) if self.batches else []


def rate_record(published_date="2026-08-11T00:00:00.000", rate="112.5", rate_month=None):
    built = {
        usda_rates.FIELDS["week_ending"]: published_date,
        "week": "32",
        "month": "8",
        "year": "2026",
        usda_rates.FIELDS["location"]: "Cairo-Memphis",
        usda_rates.FIELDS["pct_of_tariff"]: rate,
    }
    if rate_month is not None:
        built[usda_rates.FIELDS["rate_month"]] = rate_month
    return built


def socrata_returning(records):
    """A real SocrataClient over a stubbed transport: one page of records, then an empty page.

    The real client, not a fake — the pager, the error-document check and the empty-page
    termination are all code under test here rather than assumptions baked into a stand-in.

    The page sequence is keyed on `$offset` rather than popped from a list, because the rates job
    walks THREE sibling datasets in one scheduled unit and a shared list runs dry on the second.
    Serving by offset makes the stub indifferent to how many datasets ask.
    """

    def fetch(url, timeout=None):
        return json.dumps(records) if "%24offset=0" in url or "$offset=0" in url else "[]"

    return SocrataClient(fetch)


# ---------------------------------------------------------------------------------------------
# The scheduled ingest jobs. Each opens and closes its own connection internally.
# ---------------------------------------------------------------------------------------------


def test_usgs_ingest_rows_visible_from_new_connection(migrated_db, database_url):
    """`usgs_ingest_job` opens its own connection; the rows must outlive it."""
    client = StubIvClient([[reading(0, 148000), reading(15, 147500)]])

    written = usgs_ingest.usgs_ingest_job(
        url=database_url, client=client, now=BASE + timedelta(hours=1)
    )

    assert written == 2, f"the job reported {written} rows written, not 2"
    surviving = _count(database_url, "gauge_readings_iv", "usgs_site_id = %s", (SITE,))
    assert surviving == 2, (
        f"the job reported {written} rows written and a new connection sees {surviving}. The "
        f"write was rolled back on close: db.connection commits nothing implicitly."
    )


def test_usgs_daily_ingest_rows_visible_from_new_connection(migrated_db, database_url):
    """`usgs_daily_ingest_job` — the commit lives in poll_site, one level below the job."""
    client = StubDailyClient([[daily_reading(1, 141000), daily_reading(2, 139500)]])

    written = usgs_daily_ingest.usgs_daily_ingest_job(
        url=database_url, client=client, today=TODAY
    )

    assert written == 2, f"the job reported {written} rows written, not 2"
    surviving = _count(database_url, "gauge_readings_daily", "usgs_site_id = %s", (SITE,))
    assert surviving == 2, (
        f"the job reported {written} rows written and a new connection sees {surviving}. "
        f"poll_site's commit is not reaching the database."
    )


def test_usda_rates_ingest_rows_visible_from_new_connection(migrated_db, database_url):
    """`usda_rates_ingest_job` — one commit covering all three horizon datasets."""
    # `rate_month` is present because the two FORWARD datasets key on it and reject a record
    # without it. The nearby dataset's field map does not read it, so one record shape serves all
    # three — and all three are one scheduled unit behind a single commit.
    client = socrata_returning([rate_record(rate_month="9")])

    written = usda_rates.usda_rates_ingest_job(url=database_url, client=client, today=TODAY)

    assert written > 0, "the rates job wrote nothing, so this test proves nothing about commits"
    surviving = _count(database_url, "barge_rates")
    assert surviving == written, (
        f"the job reported {written} rows written and a new connection sees {surviving}. "
        f"usda_rates.ingest's commit is not reaching the database."
    )


def test_usda_movements_ingest_rows_visible_from_new_connection(migrated_db, database_url):
    """`usda_movements_ingest_job` — a NULL-tonnage row is still a row, and still must survive."""
    # `tons: "0"` deliberately: a REPORTED ZERO is the routine way USDA says nothing moved (8,218
    # of 26,144 records), and it is a row that must survive the round trip like any other.
    record = {
        usda_movements.FIELDS["week_ending"]: "2026-08-11T00:00:00.000",
        usda_movements.FIELDS["lock"]: "MS Lock 15",
        usda_movements.FIELDS["commodity"]: "Corn",
        usda_movements.FIELDS["tons"]: "0",
    }
    client = socrata_returning([record])

    written = usda_movements.usda_movements_ingest_job(
        url=database_url, client=client, today=TODAY
    )

    assert written == 1, f"the movements job reported {written} rows written, not 1"
    surviving = _count(database_url, "lock_movements")
    assert surviving == 1, (
        f"the job reported {written} rows written and a new connection sees {surviving}. "
        f"usda_movements.ingest's commit is not reaching the database."
    )


# ---------------------------------------------------------------------------------------------
# The backfill CLIs. Their `main()` opens a connection and never commits at that level, so the
# commit inside backfill_site / backfill is the only thing standing between hours of fetching and
# an empty table.
# ---------------------------------------------------------------------------------------------


def test_usgs_backfill_rows_visible_from_new_connection(migrated_db, database_url):
    """`app.ingest.backfill` — the CLI's own connection commits nothing.

    A backfill runs for hours. Discovering on the next run that it wrote nothing is the most
    expensive form of this defect in the repo.
    """
    client = StubIvClient([[reading(0, 148000)]])

    results = _write_then_close(
        database_url,
        lambda conn: iv_backfill.backfill(
            conn,
            client=client,
            site_ids=[SITE],
            start_override=BASE - timedelta(days=1),
            end=BASE + timedelta(hours=1),
            window_days=30,
        ),
    )

    written = sum(r.rows_written for r in results)
    assert written == 1, f"the backfill reported {written} rows written, not 1"
    surviving = _count(database_url, "gauge_readings_iv", "usgs_site_id = %s", (SITE,))
    assert surviving == 1, (
        f"the backfill reported {written} rows written and a new connection sees {surviving}. "
        f"Hours of fetching would have been discarded on close."
    )


def test_usgs_daily_backfill_rows_visible_from_new_connection(migrated_db, database_url):
    """`app.ingest.daily_backfill` — same shape, the daily endpoint."""
    client = StubDailyClient([[daily_reading(1, 141000)]])

    results = _write_then_close(
        database_url,
        lambda conn: daily_backfill.backfill(
            conn,
            client=client,
            site_ids=[SITE],
            start_override=TODAY - timedelta(days=2),
            end=TODAY,
            window_days=30,
        ),
    )

    written = sum(r.rows_written for r in results)
    assert written == 1, f"the daily backfill reported {written} rows written, not 1"
    surviving = _count(database_url, "gauge_readings_daily", "usgs_site_id = %s", (SITE,))
    assert surviving == 1, (
        f"the daily backfill reported {written} rows written and a new connection sees "
        f"{surviving}."
    )


def test_usda_backfill_rows_visible_from_new_connection(migrated_db, database_url):
    """`app.ingest.usda_backfill` — one dataset is enough to exercise the commit."""
    result = _write_then_close(
        database_url,
        lambda conn: usda_backfill.backfill(
            conn, NEARBY, client=socrata_returning([rate_record(rate="500")])
        ),
    )

    written = result["rows_written"]
    assert written == 1, f"the USDA backfill reported {written} rows written, not 1"
    surviving = _count(database_url, "barge_rates")
    assert surviving == 1, (
        f"the USDA backfill reported {written} rows written and a new connection sees "
        f"{surviving}."
    )
