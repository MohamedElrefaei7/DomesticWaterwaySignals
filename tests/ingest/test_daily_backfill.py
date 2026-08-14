"""The daily backfill's window arithmetic, resume logic, and refusals.

Covers CLAUDE.md § 15's backfill bullets. The two that carry the most weight:

  * the resume point comes from MAX(date) IN THE DATA, per site;
  * the backfill NEVER writes to the seed table it reads from.

The second is the one a future session is most likely to "improve". Auto-correcting
`dv_record_start` from what the backfill discovered looks helpful and destroys the only evidence
that the seed was ever wrong - the run that would have shown the discrepancy is the run that
overwrites it.
"""

import logging
from datetime import date, timedelta

import pytest

from app.ingest import daily_backfill, usgs_daily_ingest
from app.ingest.gauges import Gauge, KnownGap
from app.ingest.usgs_client import PARAM_DISCHARGE, MissingSeriesError
from app.ingest.usgs_daily_client import (
    STAT_MEAN,
    DailyReading,
    OutsidePeriodOfRecordError,
)


def gauge(site_id="07032000", dv_record_start=date(1990, 1, 1)) -> Gauge:
    return Gauge(
        usgs_site_id=site_id,
        name="test site",
        river="Mississippi",
        river_mile=None,
        lat=None,
        lon=None,
        tier=1,
        available_params=(PARAM_DISCHARGE,),
        native_cadence_minutes=60,
        iv_record_start=date(2007, 10, 1),
        dv_record_start=dv_record_start,
    )


def daily(day: date, value=121000.0, site="07032000") -> DailyReading:
    return DailyReading(
        usgs_site_id=site,
        date=day,
        param_code=PARAM_DISCHARGE,
        stat_cd=STAT_MEAN,
        value=value,
        qualifiers=("A",),
    )


class ScriptedDailyClient:
    """Returns a prepared response per window and records the windows requested.

    A response may be a list of readings or an exception instance to raise, so the ordinary-gap,
    missing-series and out-of-record paths are all driven with the same shape of setup.
    """

    def __init__(self, responses=None, default=()):
        self.responses = list(responses or [])
        self.default = list(default)
        self.windows = []

    def fetch_window(self, site_ids, param_codes, start, end, stat_codes=(STAT_MEAN,)):
        self.windows.append((tuple(site_ids), start, end, tuple(stat_codes)))
        response = self.responses.pop(0) if self.responses else self.default
        if isinstance(response, Exception):
            raise response
        return list(response)


class ConnStub:
    """A connection standing in for an EMPTY daily table.

    `execute` answers the resume query with NULL - which is the honest model of a site that has
    no daily rows yet, and the state in which `resume_point` must fall back to that site's own
    seeded floor. The real resume_point() therefore runs in these unit tests rather than being
    bypassed; only the write is replaced.

    It refuses any statement other than the resume query, so a test that started depending on
    some other database access fails loudly here instead of silently receiving NULL.
    """

    def __init__(self):
        self.commits = 0
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "max(date)" not in sql:
            raise AssertionError(
                f"ConnStub received an unexpected statement, so this test is exercising a "
                f"database path it does not model: {sql!r}"
            )

        class _Result:
            @staticmethod
            def fetchone():
                return (None,)

        return _Result()

    def commit(self):
        self.commits += 1


# ---------------------------------------------------------------------------------------------
# Unit tier — window arithmetic and refusals.
# ---------------------------------------------------------------------------------------------


def test_windows_tile_without_gaps_or_overlap():
    """Consecutive INCLUSIVE date windows meet at end + 1 day. No day is dropped or repeated.

    Inclusive-on-both-ends is what the daily service takes, and it is the arithmetic most likely
    to go quietly wrong: an off-by-one drops exactly one day per window - six days across a
    35-year backfill, which no row count anyone would eyeball would reveal.
    """
    start = date(1990, 1, 1)
    end = date(2000, 1, 1)

    tiles = daily_backfill.windows(start, end, window_days=1825)

    assert tiles, "no windows produced for a decade-long range"
    assert tiles[0].start == start
    assert tiles[-1].end == end, "the last window does not reach the end of the range"

    for earlier, later in zip(tiles, tiles[1:]):
        assert later.start == earlier.end + timedelta(days=1), (
            f"windows {earlier} and {later} do not meet: a gap loses every day between them, "
            f"an overlap is absorbed silently by the upsert"
        )

    # Every window but the last spans exactly window_days; the last is clipped.
    for tile in tiles[:-1]:
        assert (tile.end - tile.start).days + 1 == 1825
    assert (tiles[-1].end - tiles[-1].start).days + 1 <= 1825

    # The tiling covers every single day in the range exactly once, counted rather than eyeballed.
    covered = sum((t.end - t.start).days + 1 for t in tiles)
    assert covered == (end - start).days + 1, (
        f"the windows cover {covered} days but the range is {(end - start).days + 1} days"
    )

    # Degenerate ranges produce nothing rather than one absurd window.
    assert daily_backfill.windows(end, start) == []
    # A single-day range is one window, not zero: end == start is a real request.
    assert len(daily_backfill.windows(start, start)) == 1


def test_first_data_date_is_logged_per_site(monkeypatch):
    """The summary reports the first date that actually returned data, per site.

    That output IS the deliverable of a backfill run: live verification step 5 reconciles the
    seeded `dv_record_start` floors against it, and the seeded floors are brackets from one-month
    January probes rather than measured boundaries. A run that did not report it would leave the
    human comparing the seed against nothing.
    """
    monkeypatch.setattr(
        usgs_daily_ingest, "upsert_daily_readings", lambda conn, readings: len(readings)
    )

    site = gauge(dv_record_start=date(1990, 1, 1))
    end = date(2004, 12, 31)

    # The window count is DERIVED from the tiling rather than written as a literal. A hardcoded
    # count here goes red whenever the default window size changes, and the natural fix is to
    # update the number without re-reading what the test is about.
    expected_windows = daily_backfill.windows(site.dv_record_start, end, 1825)
    assert len(expected_windows) >= 3, "the range is too short to exercise empty-then-data"

    # Every window empty except the last, which carries the site's first real data.
    responses = [[] for _ in expected_windows]
    responses[-1] = [daily(date(2000, 3, 15)), daily(date(2000, 3, 16))]
    client = ScriptedDailyClient(responses=responses)

    result = daily_backfill.backfill_site(
        ConnStub(), client, site, end=end, window_days=1825
    )

    assert result.first_data_date == date(2000, 3, 15), (
        f"first data reported as {result.first_data_date}, expected the earliest date actually "
        f"returned"
    )
    assert result.seeded_floor == date(1990, 1, 1)

    described = result.describe()
    assert "FIRST DATA 2000-03-15" in described, (
        f"the summary omits the first-data date: {described}"
    )
    assert "1990-01-01" in described, (
        f"the summary omits the seeded floor, so there is nothing to compare against: {described}"
    )
    # Empty windows before the record began are ordinary and were walked, not treated as fatal.
    assert result.windows_requested == len(expected_windows), (
        f"only {result.windows_requested} of {len(expected_windows)} windows were requested - an "
        f"empty window was treated as a stopping condition"
    )
    assert result.empty_windows == len(expected_windows) - 1


def test_an_out_of_record_window_aborts_rather_than_advancing(monkeypatch):
    """A non-JSON body at the first window is fatal, and the error names the seed.

    MEASURED: the daily service answers a window entirely outside a site's period of record with
    a plain-text error page. Advancing past it would walk the whole configured range collecting
    nothing, logging steady progress, and finish reporting success over an empty table -
    CLAUDE.md § 2's theme 1 with a progress bar.

    Held apart from the missing-series case in the same test, because both are fatal and it would
    be easy to implement one catch-all that reported the wrong fix.
    """
    monkeypatch.setattr(
        usgs_daily_ingest, "upsert_daily_readings", lambda conn, readings: len(readings)
    )

    site = gauge(site_id="07289000", dv_record_start=date(1990, 1, 1))

    client = ScriptedDailyClient(
        responses=[
            OutsidePeriodOfRecordError(
                "not JSON for ['07289000'] over 1990-01-01 to 1994-12-31; check dv_record_start"
            ),
            [daily(date(1995, 1, 1), site="07289000")],
        ]
    )

    with pytest.raises(OutsidePeriodOfRecordError) as excinfo:
        daily_backfill.backfill_site(
            ConnStub(), client, site, end=date(1999, 12, 31), window_days=1825
        )

    assert "dv_record_start" in str(excinfo.value), (
        "the error does not point at the seed, which is the thing to fix"
    )
    assert len(client.windows) == 1, (
        f"the backfill made {len(client.windows)} request(s) after an out-of-record window; it "
        f"should have stopped at the first"
    )

    # A missing series is ALSO fatal - and it is a different exception with a different fix.
    client = ScriptedDailyClient(
        responses=[MissingSeriesError("MISSING: [('07289000', '00060', '00003')]"), []]
    )
    with pytest.raises(MissingSeriesError):
        daily_backfill.backfill_site(
            ConnStub(), client, site, end=date(1999, 12, 31), window_days=1825
        )
    assert len(client.windows) == 1

    # And an EMPTY window is not fatal - the control that stops this test passing against an
    # implementation where everything raises.
    tiling = daily_backfill.windows(site.dv_record_start, date(1999, 12, 31), 1825)
    responses = [[] for _ in tiling]
    responses[-1] = [daily(date(1995, 6, 1), site="07289000")]
    client = ScriptedDailyClient(responses=responses)
    result = daily_backfill.backfill_site(
        ConnStub(), client, site, end=date(1999, 12, 31), window_days=1825
    )
    assert result.windows_requested == len(tiling), (
        "an empty window stopped the run; empty windows are ordinary and must advance"
    )
    assert result.empty_windows == len(tiling) - 1


def test_expected_and_unexplained_empty_windows_log_at_different_levels(caplog, monkeypatch):
    """INFO for a window inside a known gap, WARNING for one nothing accounts for. Test 7.

    THE POINT IS THAT THE TWO ARE DISTINGUISHABLE IN THE LOG. Memphis's twenty-year hole produces
    dozens of empty windows on every full backfill, all of them expected. Logged identically to
    everything else, they are forty lines an operator learns to scroll past - and the one empty
    window that means a series has quietly stopped arriving scrolls past with them (CLAUDE.md § 2,
    theme 1).

    Asserted on the LEVEL, not on the wording, because the level is what a log filter and a human
    scanning for warnings actually act on.
    """
    monkeypatch.setattr(
        usgs_daily_ingest, "upsert_daily_readings", lambda conn, readings: len(readings)
    )

    site = gauge(site_id="07032000", dv_record_start=date(2000, 1, 1))
    known = KnownGap(
        usgs_site_id="07032000",
        source="dv",
        gap_start=date(2000, 1, 1),
        gap_end=date(2000, 12, 31),
        note="measured: endpoint serves nothing here",
    )

    # Two windows of one year each: the first falls entirely inside the gap, the second entirely
    # outside it. Same site, same emptiness, same run - only the classification differs.
    with caplog.at_level(logging.INFO, logger=daily_backfill.logger.name):
        result = daily_backfill.backfill_site(
            ConnStub(),
            client=ScriptedDailyClient(default=[]),
            gauge=site,
            end=date(2001, 12, 31),
            window_days=366,
            known_gaps=[known],
        )

    assert result.empty_windows == 2, f"expected two empty windows, got {result.empty_windows}"
    assert result.unexplained_empty_windows == 1, (
        f"{result.unexplained_empty_windows} window(s) counted as unexplained; exactly one of the "
        f"two falls outside the known gap"
    )

    empties = [
        record
        for record in caplog.records
        if "returned no daily values" in record.getMessage()
    ]
    assert len(empties) == 2, f"expected one log line per empty window, got {len(empties)}"

    levels = [record.levelno for record in empties]
    assert levels == [logging.INFO, logging.WARNING], (
        f"empty-window log levels were {[logging.getLevelName(lvl) for lvl in levels]}. The "
        f"window inside the known gap must log at INFO and the one outside it at WARNING; logged "
        f"at the same level, the expected ones bury the one that matters."
    )

    expected_line, unexplained_line = (record.getMessage() for record in empties)
    assert "EXPECTED" in expected_line and known.note in expected_line, (
        f"the expected line does not name the gap that explains it: {expected_line}"
    )
    assert "UNEXPLAINED" in unexplained_line, (
        f"the unexplained line does not say so: {unexplained_line}"
    )

    # NEITHER IS FATAL, and the run walked both. Decision 4: an empty window has never been an
    # error here and a classification is not a place to make one.
    assert result.windows_requested == 2


# ---------------------------------------------------------------------------------------------
# Integration tier — the resume point and the seed table.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_resume_point_comes_from_max_date_in_the_daily_table(migrated_db):
    """Seed daily rows and the first requested window starts from THEM, not from the floor.

    From the data, per site. A checkpoint is a second record of the same fact, and the failure
    that matters is silent: a run that recorded its progress before writing its rows skips work
    it never did, and the gap is indistinguishable from a complete backfill forever after.
    """
    site = "07032000"
    newest = date(2015, 6, 1)

    usgs_daily_ingest.upsert_daily_readings(migrated_db, [daily(newest, site=site)])
    migrated_db.commit()

    client = ScriptedDailyClient(default=[])
    daily_backfill.backfill(
        migrated_db, client=client, site_ids=[site], end=date(2015, 12, 31)
    )

    assert client.windows, "no request was made at all"
    first_start = client.windows[0][1]
    assert first_start == newest, (
        f"the daily backfill resumed from {first_start} instead of the newest stored date "
        f"({newest}). It is not reading its resume point from the data."
    )

    # And a site with no rows starts from its OWN seeded floor, which differs per site.
    from app.ingest import gauges as gauges_module

    seeded = {g.usgs_site_id: g for g in gauges_module.load(migrated_db)}
    vicksburg = seeded["07289000"]
    assert vicksburg.dv_record_start != seeded["07010000"].dv_record_start, (
        "the seeded dv floors are identical across sites, so this cannot distinguish a per-site "
        "start from a global one"
    )

    client = ScriptedDailyClient(default=[])
    daily_backfill.backfill(
        migrated_db, client=client, site_ids=["07289000"], end=date(2010, 1, 1)
    )
    assert client.windows[0][1] == vicksburg.dv_record_start, (
        f"Vicksburg started at {client.windows[0][1]} rather than its own dv_record_start floor "
        f"of {vicksburg.dv_record_start}"
    )


@pytest.mark.integration
def test_the_backfill_never_writes_to_the_gauges_table(migrated_db):
    """A full run leaves `gauges` byte-for-byte unchanged. Decision 6.

    THE GUARD AGAINST A FUTURE "HELPFUL" AUTO-CORRECTION. Updating `dv_record_start` from what the
    backfill discovered is a two-line change that looks like an improvement and destroys the only
    evidence the seed was ever wrong: the run that would have shown the discrepancy is the run
    that overwrites it. The floors are a human's claim about the data (CLAUDE.md § 1).

    Asserted by snapshotting the whole table rather than one column, so a well-meaning write to
    any other field is caught by the same test.
    """
    columns = (
        "usgs_site_id, name, river, river_mile, lat, lon, tier, available_params, "
        "native_cadence_minutes, iv_record_start, dv_record_start"
    )

    def snapshot():
        return migrated_db.execute(
            f"SELECT {columns} FROM gauges ORDER BY usgs_site_id"
        ).fetchall()

    before = snapshot()
    assert before, "the gauges table is empty; this test would be vacuous"

    # A run that DOES find data, at dates well after every seeded floor - the exact situation in
    # which an auto-correcting backfill would decide the seed needed fixing.
    client = ScriptedDailyClient(default=[daily(date(2012, 5, 1), site="07010000")])
    results = daily_backfill.backfill(
        migrated_db, client=client, site_ids=["07010000"], end=date(2012, 12, 31)
    )
    migrated_db.commit()

    assert results[0].first_data_date == date(2012, 5, 1), (
        "the run did not discover a first-data date, so there was nothing for a misbehaving "
        "implementation to write back and this test proves nothing"
    )
    assert results[0].first_data_date != results[0].seeded_floor, (
        "the discovered date equals the seeded floor, so a write-back would be undetectable"
    )

    assert snapshot() == before, (
        "the gauges table changed during a backfill run. The backfill must never write to the "
        "seed it reads from - reconciling dv_record_start is a human's job, in a new numbered "
        "migration (CLAUDE.md § 15)."
    )
