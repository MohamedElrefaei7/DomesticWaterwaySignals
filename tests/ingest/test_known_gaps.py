"""The corrected record: measured seeds, and the ranges the source will not serve.

Phase 3's `dv_record_start` values came from ONE-MONTH JANUARY PROBES generalized into a period of
record. That method measures presence in one window, not depth, and it was wrong at three of the
four sites. The correction (migration 0011) and the two gaps the same measurement found (0012) are
what this file guards.

WHY THE SEED ASSERTIONS LIVE HERE RATHER THAN IN test_gauge_seed.py
-------------------------------------------------------------------
test_gauge_seed.py is unit tier by construction - it reads the migration file and never touches a
database, which is what makes it run in the session where someone edits the seed. The corrected
values need BOTH tiers: the offline guard is there, and the assertions below check what the
DATABASE ACTUALLY HOLDS after every migration has been applied in order. A file parsed correctly
and a table updated correctly are two different claims, and 0011 is exactly the kind of migration
that can be written correctly and still not apply - an UPDATE whose WHERE clause matches nothing
succeeds, reports success, and changes no rows.
"""

from datetime import date, timedelta

import pytest

from app.ingest import daily_backfill, usgs_daily_ingest
from app.ingest import gauges as gauges_module
from app.ingest.gauges import EXPECTED, UNEXPLAINED, KnownGap
from tests.ingest.test_daily_backfill import ConnStub, ScriptedDailyClient, gauge

# The two gaps found by the full-range measurement of 2026-08-14: a single request per site over
# 1990-01-01 to 2026-08-01 for 00060/00003, counting values per year.
#
# WRITTEN OUT HERE AS LITERALS, deliberately. Deriving them from the migration would make this
# test agree with whatever the file says, which is the one thing it must not do: these boundaries
# were measured, and a boundary that moves without a new measurement is the failure this catches.
MEASURED_GAPS = {
    # Memphis: 365 values a year 1990-1993, 272 in 1994, then nothing at all until 2014-10-01.
    ("07032000", "dv"): (date(1994, 9, 30), date(2014, 9, 30)),
    # Baton Rouge: dense from 2004-03-17 except 2023 - three days in January, then nothing until
    # the record resumes on 2023-08-15, which is why gap_end is the 14th.
    ("07374000", "dv"): (date(2023, 1, 4), date(2023, 8, 14)),
}

# Corrected 2026-08-14 by the same measurement (migration 0011).
#
# 07010000 is a BOUND, not a discovered start: the request floor was 1990 and the site answered
# from its first day, so its real record begins earlier.
# 07032000 is the start of its CONTINUOUS modern segment. The endpoint also serves 1990-1994 and
# this project deliberately abandons that segment - twenty years of empty windows on every
# backfill to obtain four years disconnected from everything after them, and a series with a
# twenty-year hole teaches a seasonal model the hole rather than the season.
CORRECTED_DV_STARTS = {
    "07010000": date(1990, 1, 1),
    "07032000": date(2014, 10, 1),
    "07289000": date(2008, 1, 1),
    "07374000": date(2004, 3, 17),
}

# NULL means rolling retention: a moving window of recent weeks, which is not a start date. Only
# St. Louis has a real fixed instantaneous start.
ROLLING_RETENTION_SITES = {"07032000", "07289000", "07374000"}
ST_LOUIS_IV_START = date(2007, 10, 1)


def gap(site="07032000", source="dv", start=date(1994, 9, 30), end=date(2014, 9, 30)) -> KnownGap:
    return KnownGap(
        usgs_site_id=site,
        source=source,
        gap_start=start,
        gap_end=end,
        note="test gap",
    )


# ---------------------------------------------------------------------------------------------
# Unit tier — the seeded boundaries and the classification.
# ---------------------------------------------------------------------------------------------


def test_seeded_gaps_match_the_measured_ranges():
    """Exact boundaries, offline, against the migration file. Test 1.

    Goes red if anyone adjusts a boundary without measuring. A gap is not a rounding of "roughly
    the mid-nineties to the mid-twenty-tens": `gap_end` being 2014-09-30 rather than 2014-10-01 is
    what makes the first served day fall outside the gap, and widening a row by a day to silence a
    warning quietly reclassifies a real missing day as accounted for.
    """
    seeded = {(g.usgs_site_id, g.source): (g.gap_start, g.gap_end)
              for g in gauges_module.parse_known_gaps()}

    assert seeded == MEASURED_GAPS, (
        f"the seeded gaps do not match what was measured on 2026-08-14.\n"
        f"  seeded:   {sorted(seeded.items())}\n"
        f"  measured: {sorted(MEASURED_GAPS.items())}\n"
        f"Boundaries are INCLUSIVE of the first and last missing day. If a boundary genuinely "
        f"moved, it moved because someone re-measured - state the new measurement here in the "
        f"same commit."
    )

    # Every seeded row says what it is about, in the row. A note that says nothing sends the next
    # reader back to the source to re-establish what the row already knows.
    for known in gauges_module.parse_known_gaps():
        assert known.note.strip(), f"{known.usgs_site_id} has an empty note"
        assert known.gap_end >= known.gap_start


def test_a_window_inside_a_known_gap_is_classified_expected():
    """A window entirely inside a gap is EXPECTED, and the matching row is what explains it. Test 2."""
    gaps = [gap()]

    assert gauges_module.classify_empty_window(
        "07032000", date(2000, 1, 1), date(2004, 12, 31), gaps
    ) == EXPECTED

    # The boundary days themselves are inside the gap: inclusive on both ends.
    assert gauges_module.classify_empty_window(
        "07032000", date(1994, 9, 30), date(2014, 9, 30), gaps
    ) == EXPECTED

    # And the verdict comes with the row that produced it, so the log line names a real gap rather
    # than a plausible one.
    explanation = gauges_module.explain_empty_window(
        "07032000", date(2000, 1, 1), date(2004, 12, 31), gaps
    )
    assert explanation is not None and explanation.gap_start == date(1994, 9, 30)


def test_a_window_outside_every_known_gap_is_classified_unexplained():
    """No row covers it, so it is UNEXPLAINED - including at the wrong site or the wrong endpoint. Test 3."""
    gaps = [gap()]

    # After the gap ends.
    assert gauges_module.classify_empty_window(
        "07032000", date(2016, 1, 1), date(2016, 12, 31), gaps
    ) == UNEXPLAINED
    # Before it starts.
    assert gauges_module.classify_empty_window(
        "07032000", date(1991, 1, 1), date(1991, 12, 31), gaps
    ) == UNEXPLAINED
    # ANOTHER SITE'S gap does not explain this site's silence. A lookup that matched on dates
    # alone would report Vicksburg's twenty empty years as expected because Memphis has a hole.
    assert gauges_module.classify_empty_window(
        "07289000", date(2000, 1, 1), date(2004, 12, 31), gaps
    ) == UNEXPLAINED
    # And neither does a gap in the OTHER ENDPOINT: a period of record is per entity AND per
    # endpoint (CLAUDE.md § 15), so a hole in the instantaneous service says nothing about the
    # daily one.
    assert gauges_module.classify_empty_window(
        "07032000", date(2000, 1, 1), date(2004, 12, 31), [gap(source="iv")]
    ) == UNEXPLAINED
    # With no rows at all, everything is unexplained rather than everything being fine.
    assert gauges_module.classify_empty_window(
        "07032000", date(2000, 1, 1), date(2004, 12, 31), []
    ) == UNEXPLAINED


def test_a_window_straddling_a_gap_boundary_is_not_classified_expected():
    """Partial overlap is UNEXPLAINED. Test 4.

    THIS IS HOW A REAL GAP EDGE GETS HIDDEN. A window running from inside the gap to a month past
    its end covers days the row does not account for; calling it expected reports those days as
    explained by a measurement that never looked at them. If the gap really extends further, that
    is a new measurement and a new migration - not a classification that rounds outward.
    """
    gaps = [gap()]

    # Overlaps the end.
    assert gauges_module.classify_empty_window(
        "07032000", date(2014, 1, 1), date(2014, 12, 31), gaps
    ) == UNEXPLAINED
    # Overlaps the start.
    assert gauges_module.classify_empty_window(
        "07032000", date(1994, 1, 1), date(1995, 12, 31), gaps
    ) == UNEXPLAINED
    # Strictly contains the gap.
    assert gauges_module.classify_empty_window(
        "07032000", date(1990, 1, 1), date(2020, 12, 31), gaps
    ) == UNEXPLAINED
    # One day past each edge - the smallest straddle there is, and the one a `<=` written as `<`
    # would let through.
    assert gauges_module.classify_empty_window(
        "07032000", date(1994, 9, 29), date(2014, 9, 30), gaps
    ) == UNEXPLAINED
    assert gauges_module.classify_empty_window(
        "07032000", date(1994, 9, 30), date(2014, 10, 1), gaps
    ) == UNEXPLAINED

    # TWO ADJACENT GAPS DO NOT JOINTLY EXPLAIN A WINDOW SPANNING BOTH. The days between them are
    # days the source does serve, and a window covering them came back empty for a reason nothing
    # here has measured.
    two = [
        gap(start=date(2000, 1, 1), end=date(2000, 12, 31)),
        gap(start=date(2002, 1, 1), end=date(2002, 12, 31)),
    ]
    assert gauges_module.classify_empty_window(
        "07032000", date(2000, 1, 1), date(2002, 12, 31), two
    ) == UNEXPLAINED


def test_classification_never_makes_an_empty_window_fatal():
    """Both verdicts return a string. Neither raises, at any input. Test 5, decision 4.

    An empty window has never been fatal (CLAUDE.md § 14) and must not become fatal now: gaps are
    ordinary, and a backfill that stopped at an unexplained one could not be run to completion
    through a sensor outage. The fatal case is a missing SERIES, it is unchanged, and it lives in
    the client where the difference is visible.
    """
    gaps = [gap()]

    for site, start, end in (
        ("07032000", date(2000, 1, 1), date(2004, 12, 31)),   # expected
        ("07032000", date(2020, 1, 1), date(2020, 12, 31)),   # unexplained
        ("07289000", date(2000, 1, 1), date(2000, 12, 31)),   # unknown site
        ("07032000", date(2000, 1, 1), date(2000, 1, 1)),     # single day
    ):
        verdict = gauges_module.classify_empty_window(site, start, end, gaps)
        assert verdict in (EXPECTED, UNEXPLAINED), (
            f"classification returned {verdict!r}, which is neither verdict"
        )

    # The backfill's own empty-window path, driven end to end with a gap that covers nothing it
    # will ask for: every window comes back empty and unexplained, and the run still completes.
    site = gauge(site_id="07032000", dv_record_start=date(2016, 1, 1))
    tiling = daily_backfill.windows(date(2016, 1, 1), date(2020, 12, 31), 365)
    client = ScriptedDailyClient(default=[])

    result = daily_backfill.backfill_site(
        ConnStub(), client, site, end=date(2020, 12, 31), window_days=365, known_gaps=gaps
    )

    assert result.windows_requested == len(tiling)
    assert result.empty_windows == len(tiling)
    assert result.unexplained_empty_windows == len(tiling), (
        "unexplained empty windows were not counted, so the summary cannot distinguish a site "
        "with a known hole from a site that returned nothing at all"
    )


def test_the_backfill_requests_every_window_including_known_gaps(monkeypatch):
    """The walk covers the gap range window by window. Test 6.

    THE GUARD AGAINST THE SKIP-AHEAD OPTIMIZATION. Jumping from the start of a known gap to its
    end looks like an obvious saving - twenty years of Memphis windows that will certainly return
    nothing - and it makes a HUMAN-MAINTAINED TABLE decide what never to ask for. A row whose end
    date is a year too late then skips a year of real data, and leaves no request, no empty
    response, and no evidence that it happened.

    Requesting and receiving nothing is cheap and self-correcting: the day USGS backfills those
    twenty years, the next run picks them up and the warning that stops appearing is the notice.
    """
    monkeypatch.setattr(
        usgs_daily_ingest, "upsert_daily_readings", lambda conn, readings: len(readings)
    )

    gap_start, gap_end = MEASURED_GAPS[("07032000", "dv")]
    gaps = [gap(start=gap_start, end=gap_end)]

    # Walk from before the gap to after it, so a skip-ahead would be visible as missing windows in
    # the middle rather than as a shorter run overall.
    start, end = date(1994, 1, 1), date(2015, 12, 31)
    site = gauge(site_id="07032000", dv_record_start=start)
    expected_windows = daily_backfill.windows(start, end, 1825)

    client = ScriptedDailyClient(default=[])
    result = daily_backfill.backfill_site(
        ConnStub(), client, site, end=end, window_days=1825, known_gaps=gaps
    )

    requested = [(w[1], w[2]) for w in client.windows]
    assert requested == [(w.start, w.end) for w in expected_windows], (
        f"the backfill requested {len(requested)} window(s) where the tiling has "
        f"{len(expected_windows)}. Windows inside a known gap must still be requested."
    )
    assert result.windows_requested == len(expected_windows)

    # Every day of the gap range was inside some requested window - stated as coverage of the
    # RANGE rather than as a window count, because a run that skipped the middle and added a
    # window at the end would have the same count.
    covered = [(s, e) for s, e in requested if not (e < gap_start or s > gap_end)]
    assert covered, "no requested window overlaps the known gap at all"
    assert min(s for s, _ in covered) <= gap_start
    assert max(e for _, e in covered) >= gap_end
    for earlier, later in zip(covered, covered[1:]):
        assert later[0] == earlier[1] + timedelta(days=1), (
            f"windows {earlier} and {later} do not meet - the walk jumped across part of the "
            f"known gap instead of requesting it"
        )


# ---------------------------------------------------------------------------------------------
# Integration tier — what the database actually holds after every migration is applied.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_corrected_dv_record_starts_are_present(migrated_db):
    """All four dv_record_start values, exactly as measured. Test 8.

    Against the DEPLOYED TABLE, not the file: 0011 is a set of UPDATEs, and an UPDATE whose WHERE
    clause matches nothing succeeds and changes no rows. A migration that is correct as text and
    inert in the database is exactly CLAUDE.md § 2's theme 2 - the check that reports the thing
    responsible for the failure as correct.
    """
    seeded = {g.usgs_site_id: g.dv_record_start for g in gauges_module.load(migrated_db)}

    assert seeded == CORRECTED_DV_STARTS, (
        f"the deployed dv_record_start values are not the measured ones.\n"
        f"  in the database: {sorted(seeded.items())}\n"
        f"  measured:        {sorted(CORRECTED_DV_STARTS.items())}\n"
        f"Memphis is 2014-10-01 ON PURPOSE - the endpoint serves 1990-1994 as well, and that "
        f"segment is deliberately abandoned (migration 0011). Do not 'fix' it back to 1990."
    )


@pytest.mark.integration
def test_iv_record_start_is_null_for_rolling_retention_sites(migrated_db):
    """NULL at the three rolling-retention sites; 2007-10-01 at St. Louis. Test 9.

    A rolling window is not a start date. Any value stored for those three is a claim that is
    false within weeks, and nothing about reading a date says it has expired - which is why the
    honest value is NULL and why this test asserts NULL rather than "recent".
    """
    seeded = {g.usgs_site_id: g.iv_record_start for g in gauges_module.load(migrated_db)}

    for site in ROLLING_RETENTION_SITES:
        assert seeded[site] is None, (
            f"{site} has iv_record_start = {seeded[site]}. It serves instantaneous values on a "
            f"rolling window of recent weeks (measured 2026-08-14), so it has no fixed start and "
            f"any date here is wrong by next month. NULL is the answer, not a recent date."
        )

    assert seeded["07010000"] == ST_LOUIS_IV_START, (
        f"St. Louis's iv_record_start is {seeded['07010000']}, not {ST_LOUIS_IV_START}. Its "
        f"instantaneous record is real and fixed - NULLing it too would say this project measured "
        f"something it did not."
    )

    # The instantaneous backfill REFUSES a rolling-retention site by name rather than computing a
    # start for it. It already aborted at their first window on a missing series; this is that
    # abort moved earlier, pointed at the right thing.
    from app.ingest import backfill as iv_backfill

    rolling = next(g for g in gauges_module.load(migrated_db) if g.usgs_site_id == "07032000")
    with pytest.raises(ValueError) as excinfo:
        iv_backfill.resume_point(migrated_db, rolling)
    assert "rolling" in str(excinfo.value).lower()


@pytest.mark.integration
def test_known_gaps_table_holds_exactly_the_two_seeded_rows(migrated_db):
    """Exact set equality against the measurement. Test 10.

    Set equality, not a count and not a subset: a third row is a claim that some other range is
    not served, and it must be a deliberate act with a measurement behind it - the feature layer
    will use these rows to decide where NOT to interpolate, so an invented one silently removes
    real data from a baseline.
    """
    rows = gauges_module.load_known_gaps(migrated_db)
    held = {(g.usgs_site_id, g.source): (g.gap_start, g.gap_end) for g in rows}

    assert held == MEASURED_GAPS, (
        f"gauge_known_gaps does not hold exactly the two measured rows.\n"
        f"  in the database: {sorted(held.items())}\n"
        f"  measured:        {sorted(MEASURED_GAPS.items())}"
    )
    assert len(rows) == len(MEASURED_GAPS), (
        f"{len(rows)} row(s) in the table for {len(MEASURED_GAPS)} distinct (site, source) keys - "
        f"a duplicate would be invisible to the comparison above"
    )

    # The database enforces the boundary ordering, so a reversed row cannot be seeded later: such
    # a row matches no window and would sit in the table looking like an accounted-for gap while
    # explaining nothing.
    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO gauge_known_gaps (usgs_site_id, source, gap_start, gap_end, note)"
            " VALUES ('07010000', 'dv', DATE '2020-01-01', DATE '2019-01-01', 'reversed')"
        )
    migrated_db.rollback()

    # And the endpoint name is constrained: a row written as 'daily' would sit in the table
    # matching no lookup any consumer performs.
    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO gauge_known_gaps (usgs_site_id, source, gap_start, gap_end, note)"
            " VALUES ('07010000', 'daily', DATE '2019-01-01', DATE '2020-01-01', 'wrong source')"
        )
    migrated_db.rollback()


@pytest.mark.integration
def test_the_backfill_reads_known_gaps_from_the_database(migrated_db):
    """A full run classifies against the SEEDED rows, not against a list a test handed it.

    The unit tests above prove the classification; this proves the wiring. A lookup that is never
    connected to the loader is a function that passes its own tests forever while every empty
    window in production logs as unexplained.
    """
    end = date(2005, 12, 31)
    client = ScriptedDailyClient(default=[])

    results = daily_backfill.backfill(
        migrated_db, client=client, site_ids=["07374000"], end=end
    )

    assert client.windows, "no request was made at all"
    assert results[0].empty_windows == results[0].windows_requested
    assert results[0].unexplained_empty_windows == results[0].empty_windows, (
        "windows in 2004-2005 were classified as expected, but the only Baton Rouge gap seeded is "
        "in 2023"
    )

    # And a window that IS inside the seeded 2023 gap classifies as expected, using the rows the
    # loader returned.
    gaps = gauges_module.load_known_gaps(migrated_db, source=gauges_module.SOURCE_DAILY)
    assert gauges_module.classify_empty_window(
        "07374000", date(2023, 2, 1), date(2023, 6, 30), gaps
    ) == EXPECTED
