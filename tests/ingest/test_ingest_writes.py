"""Integration tier — writing readings. Requires DATABASE_URL and a real TimescaleDB.

Covers CLAUDE.md § 14's upsert bullet and CLAUDE.md § 4's definition of `rows_written`.

These run against the real migrations: the real hypertable, the real primary key, the real
foreign key to `gauges`, and the real @job decorator. Asserting upsert semantics against a
fixture table would test the fixture.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import usgs_ingest
from app.ingest.usgs_client import PARAM_DISCHARGE, Reading

pytestmark = pytest.mark.integration

SITE = "07010000"
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def reading(offset_minutes: int, value: float, qualifiers=("P",), site=SITE) -> Reading:
    return Reading(
        usgs_site_id=site,
        ts=BASE + timedelta(minutes=offset_minutes),
        param_code=PARAM_DISCHARGE,
        value=value,
        qualifiers=tuple(qualifiers),
    )


class StubClient:
    """A UsgsClient stand-in that returns prepared readings and records its windows.

    Deliberately not a mock of the client's internals: it stands in at the boundary the ingest
    actually calls, so everything below that boundary - the upsert, the counting, the resume
    query, the @job integration - is the real code.
    """

    def __init__(self, batches):
        self.batches = list(batches)
        self.windows = []

    def fetch_window(self, site_ids, param_codes, start, end):
        self.windows.append((tuple(site_ids), tuple(param_codes), start, end))
        if not self.batches:
            return []
        return self.batches.pop(0)


def test_readings_are_upserted_not_duplicated(migrated_db, readings_table):
    """The same (site, ts, param) written twice produces one row.

    The natural key is enforced by the database, so this is a statement about the migration as
    much as about the writer.
    """
    batch = [reading(0, 148000), reading(15, 147500)]

    usgs_ingest.upsert_readings(migrated_db, batch)
    migrated_db.commit()
    usgs_ingest.upsert_readings(migrated_db, batch)
    migrated_db.commit()

    rows = readings_table.rows(SITE)
    assert len(rows) == 2, (
        f"writing two readings twice produced {len(rows)} rows. The natural key "
        f"(usgs_site_id, ts, param_code) is not doing its job."
    )
    assert {r["value"] for r in rows} == {148000, 147500}


def test_a_revised_value_overwrites_the_original(migrated_db, readings_table):
    """USGS republishes revised readings, and the revision must win.

    THE FAILURE THIS EXCLUDES IS `ON CONFLICT DO NOTHING`, which makes reruns safe, passes the
    no-duplicates test above, and keeps the provisional value forever - silently. A gauge whose
    published discharge was corrected upward by 8% weeks later would keep the wrong number, and
    nothing anywhere would report a problem.
    """
    usgs_ingest.upsert_readings(migrated_db, [reading(0, 148000, qualifiers=("P",))])
    migrated_db.commit()

    # Same instant, revised value, promoted from provisional to approved.
    written = usgs_ingest.upsert_readings(
        migrated_db, [reading(0, 151200, qualifiers=("A",))]
    )
    migrated_db.commit()

    rows = readings_table.rows(SITE)
    assert len(rows) == 1, "the revision inserted a second row instead of replacing the value"
    assert rows[0]["value"] == 151200, (
        f"the stored value is {rows[0]['value']}, not the revised 151200. The upsert used "
        f"DO NOTHING: the provisional value is now frozen in place permanently."
    )
    assert rows[0]["qualifiers"] == ["A"], (
        "the qualifier was not updated with the value - a reading promoted from provisional to "
        "approved still reads as provisional"
    )
    assert written == 1, f"a genuine revision reported {written} rows written"


def test_rows_written_counts_written_rows_not_parsed_rows(migrated_db, readings_table):
    """CLAUDE.md § 4: rows WRITTEN TO THE DATABASE, never rows examined or processed.

    Half the batch already exists, unchanged. A plain `DO UPDATE` would report the whole batch as
    written every time, which would make a re-run of the backfill claim millions of rows written
    and mean nothing at all - large, reassuring, and unrelated to what happened.
    """
    already_present = [reading(i * 15, 148000 + i) for i in range(5)]
    usgs_ingest.upsert_readings(migrated_db, already_present)
    migrated_db.commit()

    # Ten readings: the same five, byte-identical, plus five genuinely new ones.
    new = [reading((5 + i) * 15, 149000 + i) for i in range(5)]
    written = usgs_ingest.upsert_readings(migrated_db, already_present + new)
    migrated_db.commit()

    assert written == 5, (
        f"reported {written} rows written for a batch of 10 in which 5 already existed unchanged. "
        f"rows_written is counting rows PARSED, not rows written (CLAUDE.md § 4)."
    )
    assert readings_table.count() == 10

    # And a batch that changes nothing at all reports 0 - which is a meaningful statement, not an
    # error, and is distinct from NULL.
    unchanged = usgs_ingest.upsert_readings(migrated_db, already_present + new)
    migrated_db.commit()
    assert unchanged == 0, (
        f"re-writing identical data reported {unchanged} rows written. Every rerun of the "
        f"backfill would claim to have written its entire input."
    )


def test_duplicate_keys_within_one_batch_do_not_abort_the_write(migrated_db, readings_table):
    """Two readings for the same instant in one batch collapse instead of raising.

    Not in the commit brief, and here because the failure is abrupt and remote: Postgres rejects
    the entire statement with "ON CONFLICT DO UPDATE command cannot affect row a second time"
    when one INSERT carries a conflict key twice. A site publishing two measurement methods for
    the same instant produces exactly that, and it would take down a backfill hours into a run
    with an error that points at the writer rather than at the data.
    """
    batch = [reading(0, 148000), reading(0, 148500)]

    written = usgs_ingest.upsert_readings(migrated_db, batch)
    migrated_db.commit()

    rows = readings_table.rows(SITE)
    assert len(rows) == 1
    assert rows[0]["value"] == 148500, "the last occurrence should win, matching upsert order"
    assert written == 1


def test_ingest_registers_a_job_run_row(migrated_db, database_url, readings_table, job_runs):
    """The @job integration holds end to end, with a real row count.

    Drives the decorated job rather than the inner function: the bookkeeping row, the row count
    it records, and the write all have to line up, and only the decorated path exercises that.
    """
    client = StubClient([[reading(0, 148000), reading(15, 147500)]])

    written = usgs_ingest.usgs_ingest_job(
        url=database_url, client=client, now=BASE + timedelta(hours=1)
    )

    rows = job_runs.rows(usgs_ingest.JOB_NAME)
    assert len(rows) == 1, "the ingest job left no job_runs row"
    assert rows[0]["status"] == "success"
    assert rows[0]["error_message"] is None

    assert rows[0]["rows_written"] == written == 2, (
        f"job_runs.rows_written is {rows[0]['rows_written']} but {written} rows were written"
    )
    # An int, not NULL: this job writes rows, so 0 would be a meaningful claim and NULL would
    # mean "does not count rows" (CLAUDE.md § 4).
    assert isinstance(rows[0]["rows_written"], int)

    # Only one site had readings prepared; the other three returned nothing, which is not an
    # error. Four windows were still requested - one per site.
    assert len(client.windows) == 4, (
        f"expected one request per registered gauge, got {len(client.windows)}"
    )
    assert readings_table.count() == 2


def test_a_site_with_no_rows_polls_a_short_window_not_its_whole_record(
    migrated_db, database_url
):
    """A cold start does not turn the hourly job into an eighteen-year backfill.

    Reaching back to record_start here would have the scheduled job attempt the full record every
    hour; with max_instances=1 it would simply never finish, and the heartbeat would report a job
    that is permanently "running" rather than one that is broken. The backfill is a separate CLI
    a human runs, and this window just keeps the site current until they do.
    """
    client = StubClient([])
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    usgs_ingest.usgs_ingest_job(url=database_url, client=client, now=now)

    assert client.windows, "no request was made at all"
    for _sites, _params, start, end in client.windows:
        assert end == now
        assert start == now - usgs_ingest.COLD_START_WINDOW, (
            f"a site with no rows was polled from {start}, not from "
            f"{now - usgs_ingest.COLD_START_WINDOW}. Reaching back to record_start would make "
            f"every hourly run attempt the full backfill."
        )


def test_the_poll_overlaps_the_newest_stored_reading(migrated_db, database_url):
    """The incremental window starts before MAX(ts), not at it.

    USGS transmits hourly but not punctually, and revises what it has already published. Without
    the overlap, anything that landed in the gap between two polls is missed permanently - nothing
    ever looks at that window again. The upsert makes the overlap free: a re-fetched unchanged
    reading writes nothing and counts nothing.
    """
    usgs_ingest.upsert_readings(migrated_db, [reading(0, 148000)])
    migrated_db.commit()

    client = StubClient([])
    now = BASE + timedelta(hours=5)
    usgs_ingest.usgs_ingest_job(url=database_url, client=client, now=now)

    by_site = {sites[0]: (start, end) for sites, _params, start, end in client.windows}
    start, _end = by_site[SITE]

    assert start == BASE - usgs_ingest.OVERLAP, (
        f"the poll for a site whose newest reading is {BASE} started at {start}; expected "
        f"{BASE - usgs_ingest.OVERLAP}, an overlap of {usgs_ingest.OVERLAP}."
    )
