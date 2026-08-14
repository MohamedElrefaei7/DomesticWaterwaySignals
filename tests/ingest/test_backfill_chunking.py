"""The backfill's window arithmetic and resume logic.

Covers CLAUDE.md § 14's backfill bullets: chunk by window, resume from MAX(ts) in the data rather
than a checkpoint, per-entity period of record, and — the one that matters most — never collapse
"this window returned nothing" into "this series is missing".

The window-tiling tests are unit tier. The resume tests need a real database, because the fact
under test is that the resume point comes from a query against the data.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import backfill, usgs_ingest
from app.ingest.gauges import Gauge
from app.ingest.usgs_client import PARAM_DISCHARGE, MissingSeriesError, Reading

UTC = timezone.utc


def gauge(site_id="07010000", iv_record_start=None, params=(PARAM_DISCHARGE,)) -> Gauge:
    return Gauge(
        usgs_site_id=site_id,
        name="test site",
        river="Mississippi",
        river_mile=None,
        lat=None,
        lon=None,
        tier=1,
        available_params=tuple(params),
        native_cadence_minutes=30,
        iv_record_start=iv_record_start or datetime(2008, 1, 1, tzinfo=UTC).date(),
        dv_record_start=datetime(2008, 1, 1, tzinfo=UTC).date(),
    )


class ScriptedClient:
    """Returns a prepared response per window, and records what was asked for.

    A response may be a list of readings or an exception instance to raise - which is how the
    empty-window and missing-pair paths are driven through the same code with the same shape of
    setup, so the test that holds them apart is not quietly testing two different things.
    """

    def __init__(self, responses=None, default=()):
        self.responses = list(responses or [])
        self.default = list(default)
        self.windows = []

    def fetch_window(self, site_ids, param_codes, start, end):
        self.windows.append((tuple(site_ids), tuple(param_codes), start, end))
        response = self.responses.pop(0) if self.responses else self.default
        if isinstance(response, Exception):
            raise response
        return list(response)


# ---------------------------------------------------------------------------------------------
# Unit tier — window arithmetic.
# ---------------------------------------------------------------------------------------------


def test_windows_tile_the_range_without_gaps_or_overlap():
    """Consecutive windows share a boundary and neither claims it twice.

    A gap loses every reading inside it, permanently and silently - nothing looks at that range
    again once the backfill has walked past it. An overlap is harmless because the upsert absorbs
    it, which is exactly why an overlap bug would never be noticed: it costs nothing and teaches
    nothing, right up until someone reads the row counts and believes them.
    """
    start = datetime(2008, 1, 1, tzinfo=UTC)
    end = datetime(2008, 12, 31, tzinfo=UTC)

    tiles = backfill.windows(start, end, window_days=90)

    assert tiles, "no windows produced for a year-long range"
    assert tiles[0].start == start
    assert tiles[-1].end == end, "the last window does not reach the end of the range"

    for earlier, later in zip(tiles, tiles[1:]):
        assert earlier.end == later.start, (
            f"windows {earlier} and {later} do not meet: a gap loses everything between them, "
            f"an overlap is absorbed silently by the upsert"
        )

    # Every window but the last is a full span; the last is clipped rather than overshooting.
    for tile in tiles[:-1]:
        assert tile.end - tile.start == timedelta(days=90)
    assert tiles[-1].end - tiles[-1].start <= timedelta(days=90)

    # The service's endDT is inclusive, so the requested end sits one second below the next
    # window's start. Without this every boundary reading is fetched twice.
    assert tiles[0].request_end() == tiles[1].start - timedelta(seconds=1)

    # Degenerate ranges produce nothing rather than one absurd window.
    assert backfill.windows(end, start) == []
    assert backfill.windows(start, start) == []


class ConnStub:
    """Stands in for the connection so the window loop runs without a database.

    Only `commit` is reachable: `start_override` bypasses the resume query, and the write itself
    is replaced. Everything between the client and the write is the real code, which is the part
    this test is about.
    """

    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_an_empty_window_advances_and_a_missing_pair_aborts(monkeypatch):
    """BOTH BEHAVIOURS IN ONE TEST, so the distinction cannot be collapsed without a failure.

    They arrive over the wire looking identical - HTTP 200, well-formed JSON - and they mean
    opposite things:

      * empty window  -> ordinary. A sensor outage, or a window before the record really began.
                         Advance. Treating it as fatal makes the backfill unrunnable.
      * missing pair  -> the site has stopped serving a parameter it is recorded as serving.
                         Abort. Continuing walks the remaining years collecting nothing while
                         logging progress, which is CLAUDE.md § 2's theme 1 exactly.

    Written as one test because two SEPARATE tests can each be satisfied by one wrong
    implementation: make everything fatal and the missing-pair test passes; make nothing fatal
    and the empty-window test passes. Only holding both at once pins the behaviour, which is why
    the mutation table points both "treat an empty window as fatal" and "treat a missing pair as
    an empty window" at this single test.
    """
    site = gauge()
    start = datetime(2008, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=270)
    reading = Reading(site.usgs_site_id, datetime(2008, 4, 1, tzinfo=UTC), PARAM_DISCHARGE, 1.0, ())

    written: list[Reading] = []

    def fake_upsert(conn, readings):
        written.extend(readings)
        return len(readings)

    monkeypatch.setattr(usgs_ingest, "upsert_readings", fake_upsert)

    # THE EMPTY-WINDOW HALF: three windows, the first two returning nothing. All three are
    # requested and the run completes.
    client = ScriptedClient(responses=[[], [], [reading]])
    result = backfill.backfill_site(
        ConnStub(), client, site, end=end, window_days=90, start_override=start
    )

    assert result.windows_requested == 3, (
        f"only {result.windows_requested} of 3 windows were requested - an empty window was "
        f"treated as a stopping condition, which makes the backfill unrunnable across any gap"
    )
    assert result.empty_windows == 2
    assert result.rows_written == 1
    assert len(written) == 1

    # The first window that actually returned data is recorded, which is how a wrong seeded
    # iv_record_start becomes visible instead of becoming a silent sweep of empty windows.
    assert result.first_window_with_data is not None
    assert result.first_window_with_data.start == start + timedelta(days=180)

    # THE MISSING-PAIR HALF: same site, same window arithmetic, same shape of response - and the
    # opposite outcome. The third window is never requested.
    client = ScriptedClient(
        responses=[[], MissingSeriesError("MISSING: [('07010000', '00060')]"), [reading]]
    )
    with pytest.raises(MissingSeriesError):
        backfill.backfill_site(
            ConnStub(), client, site, end=end, window_days=90, start_override=start
        )

    assert len(client.windows) == 2, (
        f"the backfill made {len(client.windows)} request(s) after a missing series; it should "
        f"have stopped at the second. Continuing walks the remaining record collecting nothing "
        f"while logging progress."
    )


# ---------------------------------------------------------------------------------------------
# Integration tier — the resume point comes from the data.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_resume_point_comes_from_max_ts_in_the_database(migrated_db):
    """Seed rows, and the first requested window starts from THEM, not from iv_record_start.

    A checkpoint file or a progress table is a second record of the same fact, and when the two
    disagree it is the checkpoint that gets believed. The failure that matters is silent: a run
    that wrote its checkpoint before its rows skips work it never did, and the gap is
    indistinguishable from a complete backfill forever after.
    """
    site = "07374000"
    newest = datetime(2015, 6, 1, 12, tzinfo=UTC)

    usgs_ingest.upsert_readings(
        migrated_db,
        [Reading(site, newest, PARAM_DISCHARGE, 402000.0, ("A",))],
    )
    migrated_db.commit()

    client = ScriptedClient(default=[])
    backfill.backfill(
        migrated_db,
        client=client,
        site_ids=[site],
        end=newest + timedelta(days=30),
    )

    assert client.windows, "no request was made at all"
    first_start = client.windows[0][2]
    assert first_start == newest, (
        f"the backfill resumed from {first_start} instead of the newest stored reading "
        f"({newest}). It is not reading its resume point from the data."
    )


@pytest.mark.integration
def test_a_site_with_no_rows_starts_at_its_own_iv_record_start(migrated_db):
    """Per site, not global. Vicksburg's record begins later than the other three.

    The plan assumed 2007-10-01 for everything; measurement says Vicksburg's IV record appears to
    begin 2008-01-01. A single global start is therefore wrong for at least one site, and the
    wrongness is invisible - it just produces a quarter of empty windows that look like an outage.
    """
    from app.ingest import gauges as gauges_module

    seeded = {g.usgs_site_id: g for g in gauges_module.load(migrated_db)}
    vicksburg = seeded["07289000"]
    st_louis = seeded["07010000"]

    assert vicksburg.iv_record_start != st_louis.iv_record_start, (
        "the seeded iv_record_start values are now identical across sites, so this test can no "
        "longer tell a per-site start from a global one"
    )

    client = ScriptedClient(default=[])
    backfill.backfill(
        migrated_db,
        client=client,
        site_ids=[vicksburg.usgs_site_id, st_louis.usgs_site_id],
        end=datetime(2008, 6, 1, tzinfo=UTC),
        window_days=90,
    )

    first_by_site = {}
    for sites, _params, start, _end in client.windows:
        first_by_site.setdefault(sites[0], start)

    assert first_by_site[vicksburg.usgs_site_id] == datetime(
        vicksburg.iv_record_start.year,
        vicksburg.iv_record_start.month,
        vicksburg.iv_record_start.day,
        tzinfo=UTC,
    ), (
        f"Vicksburg started at {first_by_site[vicksburg.usgs_site_id]} rather than its own "
        f"iv_record_start of {vicksburg.iv_record_start}"
    )
    assert first_by_site[st_louis.usgs_site_id] == datetime(
        st_louis.iv_record_start.year,
        st_louis.iv_record_start.month,
        st_louis.iv_record_start.day,
        tzinfo=UTC,
    )
    assert first_by_site[vicksburg.usgs_site_id] != first_by_site[st_louis.usgs_site_id], (
        "both sites started from the same instant - the backfill is using one global start"
    )
