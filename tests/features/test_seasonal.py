"""Deseasonalization: the median, the eight-year guard, and the leap day.

Every test here is against a hand-computed expectation. That is the point of the builders being
pure functions (CLAUDE.md § 17): a test that read the climatology back out of the database would be
asserting that the code computes what the code computes, and would pass in both directions of every
mutation below.

ONE TEST IN THIS FILE IS DELIBERATELY NOT OF THAT KIND, and it is the last one. Phase 5's live
verification measured `climatology_n_years` at 11 to 37 across every row with NO NULLS ANYWHERE:
THE GUARD HAS NEVER FIRED ON REAL DATA. Its mechanism is unit-tested above and goes red when it is
removed - what has never happened is the refusal surviving a database round-trip, through the
builder's return tuple, through the upsert's parameter list, into a column whose CHECK constraint
has an opinion about it. A NULL a pure function produces correctly can still be written as 0 by a
`coalesce` somebody added to quiet a warning, and CLAUDE.md § 2's theme 2 asks for verification
that crosses the boundary where the bug would live. So that one is integration, on purpose, and it
is the only place in this suite where the database is the thing being asked.
"""

from datetime import date, timedelta

import pytest

from app.features import build, seasonal


def daily_series(start: date, values) -> list[tuple]:
    """`(date, value)` pairs on consecutive days from `start`."""
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def same_day_across_years(month: int, day: int, values_by_year: dict) -> list[tuple]:
    """One calendar day, one observation per year. The shape a climatology is built from."""
    return [(date(year, month, day), value) for year, value in sorted(values_by_year.items())]


def test_climatology_uses_median_not_mean():
    """Test 6. One extreme year must not move the baseline it would be detected against.

    THE 2022 AND 2023 EVENTS ARE IN THE HISTORY THE CLIMATOLOGY IS FITTED ON. With a mean, they
    pull the October baseline down - so the anomaly each produces is measured against a baseline it
    itself depressed, and the events partly erase their own signal. The effect is invisible: the
    resulting series is smooth, plausible, and quietly understated exactly where it matters most.

    Ten ordinary years at 200,000 and one drought year at 20,000:
        median = 200,000        mean = 183,636
    """
    observations = same_day_across_years(
        10,
        11,
        {year: 200000.0 for year in range(2012, 2022)} | {2022: 20000.0},
    )

    climo = seasonal.climatology(observations)
    entry = climo[seasonal.day_of_year(date(2022, 10, 11))]

    assert entry.n_years == 11, f"expected 11 contributing years, got {entry.n_years}"
    assert entry.value == pytest.approx(200000.0), (
        f"the climatology is {entry.value}, not the median 200000. A mean would be 183636 - the "
        f"drought year has moved the baseline it is supposed to be measured against."
    )

    # And the anomaly for the drought year is the full departure, not a shrunken one.
    rows = {day: anomaly for day, _value, anomaly, _n in seasonal.build_anomalies(observations)}
    assert rows[date(2022, 10, 11)] == pytest.approx(-180000.0), (
        f"the drought year's anomaly is {rows[date(2022, 10, 11)]}, not -180000 - the event has "
        f"eaten part of its own signal"
    )


def test_a_day_with_fewer_than_eight_years_yields_a_null_anomaly():
    """Test 7. Below the guard the answer is NULL, and the year count still says why.

    Memphis's daily record starts 2014-10-01, so its early-October days have barely a decade and
    its late-September days have less. A median of three observations is a number with a false air
    of authority - it will be wrong by a wide margin and NOTHING DOWNSTREAM CAN TELL, because an
    anomaly of +40,000 cfs looks identical whether twenty years or three produced it.
    """
    three_years = same_day_across_years(10, 11, {2022: 100.0, 2023: 200.0, 2024: 300.0})

    climo = seasonal.climatology(three_years)
    entry = climo[seasonal.day_of_year(date(2023, 10, 11))]

    assert entry.n_years == 3
    assert entry.value is None, (
        f"a climatology of {entry.value} was computed from 3 years, below the {seasonal.MIN_YEARS}"
        f"-year guard"
    )

    rows = seasonal.build_anomalies(three_years)
    assert all(anomaly is None for _day, _value, anomaly, _n in rows), (
        f"an anomaly survived the guard: {rows}"
    )
    # THE COUNT IS STILL RECORDED. A NULL anomaly with no count beside it is indistinguishable
    # from a bug, and the first response to an unexplained NULL is to delete the check.
    assert all(n == 3 for _day, _value, _anomaly, n in rows)

    # The boundary itself: exactly MIN_YEARS is enough, one fewer is not.
    at_guard = same_day_across_years(
        10, 11, {2015 + i: 100.0 for i in range(seasonal.MIN_YEARS)}
    )
    assert climatology_value(at_guard, date(2020, 10, 11)) == pytest.approx(100.0)

    below_guard = same_day_across_years(
        10, 11, {2015 + i: 100.0 for i in range(seasonal.MIN_YEARS - 1)}
    )
    assert climatology_value(below_guard, date(2020, 10, 11)) is None


def climatology_value(observations, day):
    return seasonal.climatology(observations)[seasonal.day_of_year(day)].value


def test_climatology_n_years_is_stored_and_matches_the_input():
    """Test 8. The count is the evidence, so it has to match what went in.

    Counted over the SMOOTHING WINDOW rather than the single day, because the window is what backs
    the smoothed value. Counting the day alone would understate the evidence by roughly a factor of
    fifteen and refuse almost every day in the database.
    """
    # Twelve years, one observation on each of three consecutive days per year. All three days fall
    # inside each other's 15-day window, so every one of them sees all twelve years.
    observations = []
    for year in range(2010, 2022):
        for offset in range(3):
            observations.append((date(year, 6, 10 + offset), 100.0))

    climo = seasonal.climatology(observations)
    assert climo[seasonal.day_of_year(date(2021, 6, 11))].n_years == 12

    rows = seasonal.build_anomalies(observations)
    assert {n for _day, _value, _anomaly, n in rows} == {12}

    # A day whose window touches nothing gets zero years and no value - not an exception, and not a
    # silently-omitted key.
    january = climo[seasonal.day_of_year(date(2021, 1, 15))]
    assert (january.value, january.n_years) == (None, 0)


def test_feb_29_folds_into_day_59():
    """Test 9. The leap day shares February 28's bucket, and March 1 aligns across year types.

    Its own bucket would hold ONE OBSERVATION EVERY FOUR YEARS - a quarter of what every neighbour
    has - so its climatology would be noisier than the rest of the series by construction, and it
    would fail the eight-year guard for the first thirty-two years of any record while February 28
    and March 1 passed comfortably. A single day behaving differently for a calendar reason is the
    kind of artefact that gets discovered as a modelling result.
    """
    assert seasonal.day_of_year(date(2020, 2, 29)) == 59
    assert seasonal.day_of_year(date(2020, 2, 28)) == 59

    # THE ALIGNMENT THIS BUYS, which is the real reason: the same calendar date must land on the
    # same bucket whether or not its year is a leap year. Without the fold, March 1 is 61 in a leap
    # year and 60 otherwise - a one-day seasonal shift in three years out of four, far too small to
    # notice and perfectly systematic.
    assert seasonal.day_of_year(date(2020, 3, 1)) == seasonal.day_of_year(date(2021, 3, 1)) == 60
    assert seasonal.day_of_year(date(2020, 12, 31)) == seasonal.day_of_year(date(2021, 12, 31))

    # Nothing maps to 366, so there is no bucket a quarter the size of its neighbours.
    every_day = {
        seasonal.day_of_year(date(2020, 1, 1) + timedelta(days=i)) for i in range(366)
    }
    assert every_day == set(range(1, 366)), "the folded year is not exactly 1..365"

    # And the leap day's observations really do join February 28's, rather than forming a bucket of
    # their own that the guard would then refuse.
    observations = [(date(year, 2, 28), 100.0) for year in range(2012, 2022)]
    observations.append((date(2020, 2, 29), 100.0))
    climo = seasonal.climatology(observations)
    assert climo[59].value == pytest.approx(100.0)


def test_anomaly_is_value_minus_climatology():
    """Test 10. Hand-computed, in the direction the sign convention implies.

    A low-water day must produce a NEGATIVE anomaly. Reversing the subtraction is a one-character
    change that leaves every magnitude correct and every conclusion backwards, and a chart of it
    looks entirely reasonable.
    """
    # Ten years at 100 on one day; the eleventh reads 60.
    observations = same_day_across_years(
        7, 15, {year: 100.0 for year in range(2012, 2022)} | {2022: 60.0}
    )

    rows = {day: (value, anomaly) for day, value, anomaly, _n in seasonal.build_anomalies(observations)}

    value, anomaly = rows[date(2022, 7, 15)]
    assert value == 60.0
    assert anomaly == pytest.approx(-40.0), (
        f"anomaly is {anomaly}; hand-computed 60 - 100 = -40. A positive 40 means the subtraction "
        f"runs the other way, which leaves every magnitude right and every direction wrong."
    )

    value, anomaly = rows[date(2015, 7, 15)]
    assert (value, anomaly) == (100.0, pytest.approx(0.0))


def test_a_null_value_contributes_no_year_and_gets_no_anomaly():
    """A missing observation must not satisfy the guard it should be counted out of.

    Counting the year of a NULL would let a run of missing days push a day-of-year over the
    eight-year line with no observations behind it - a climatology of nothing, passing the check
    that exists to prevent exactly that.
    """
    observations = same_day_across_years(
        3, 3, {year: 50.0 for year in range(2012, 2020)} | {2020: None, 2021: None}
    )

    entry = seasonal.climatology(observations)[seasonal.day_of_year(date(2020, 3, 3))]
    assert entry.n_years == 8, f"NULL years were counted: {entry.n_years} instead of 8"

    rows = {day: anomaly for day, _value, anomaly, _n in seasonal.build_anomalies(observations)}
    assert rows[date(2020, 3, 3)] is None
    assert rows[date(2015, 3, 3)] == pytest.approx(0.0)


# ---------------------------------------------------------------------------------------------
# DEBT 1c - the eight-year guard, exercised against a real database for the first time.
#
# Phase 5's FINDING 4, in `CONTEXT.md`: `climatology_n_years` runs 11 to 37 across every row of the
# real `features` table, with no NULLs anywhere. Live verification step 3 expected a substantial
# NULL-anomaly population in Memphis's early years and found none - THE GUARD HOLDS BY LUCK OF
# COVERAGE, NOT BY DEMONSTRATION. The 15-day smoothing window pools distinct calendar years across
# the whole window, and with 35 years at two sites and a decade at the others, every day-of-year
# clears eight comfortably.
#
# It was recorded as a gap rather than as a success, and this is the test that closes it: a
# DELIBERATELY SHALLOW site - five years and no more - so the refusal actually happens, end to end.
# ---------------------------------------------------------------------------------------------

# Five, and the number is the whole fixture. `seasonal.MIN_YEARS` is eight, so a record this short
# must produce a NULL anomaly on every row; at eight it would produce none and the test would pass
# for the wrong reason. Written as an expression of MIN_YEARS rather than as a bare 5 so that
# raising the guard cannot silently turn this into a test of nothing.
SHALLOW_YEARS = seasonal.MIN_YEARS - 3

# A contiguous autumn block in each of those years, and autumn because that is the season the
# project cares about - the 2022 and 2023 events are September-October. The block is wide enough
# that the 15-day smoothing window around the day asserted below falls entirely inside it, so the
# expected year count is exactly SHALLOW_YEARS rather than an accident of the window's edges.
BLOCK_START = (9, 1)
BLOCK_END = (10, 31)

# The day the assertion is made on. Mid-block, so its centred 15-day window (September 13-27) is
# fully seeded in all five years.
ASSERTED_DAY = (9, 20)


def _shallow_autumn_series(first_year: int) -> list[tuple]:
    """`(date, value)` for September 1 - October 31 in each of five consecutive years.

    The values descend within each autumn and differ between years, so the climatology is a real
    median of five distinct numbers rather than a constant - a flat series would produce an anomaly
    of exactly 0.0, which is indistinguishable from the NULL this test is looking for once anything
    downstream coalesces.
    """
    series = []
    for offset in range(SHALLOW_YEARS):
        year = first_year + offset
        day = date(year, *BLOCK_START)
        last = date(year, *BLOCK_END)
        step = 0
        while day <= last:
            series.append((day, 200_000.0 - 500.0 * step + 3_000.0 * offset))
            day += timedelta(days=1)
            step += 1
    return series


@pytest.mark.integration
def test_a_five_year_climatology_yields_null_anomaly_end_to_end(migrated_db, seed_readings):
    """Test 23, DEBT 1c. The guard refuses, and the refusal survives into the table.

    THE MECHANISM IS ALREADY PROVEN ABOVE; THE ROUND TRIP IS NOT. Everything between the builder's
    return tuple and the stored row is untested by the unit tier: the parameter list of
    `FEATURES_UPSERT_SQL`, the ordering of its six placeholders, and migration 0020's
    `features_anomaly_needs_its_year_count` CHECK. A single `coalesce(anomaly, 0)` anywhere in that
    path would leave every test above green and this one red, which is the only reason to pay for
    a database here.

    AND THE YEAR COUNT IS ASSERTED PRESENT, not merely the anomaly absent. That is the half of the
    guard that gets removed first: a NULL with no count beside it is indistinguishable from a bug,
    and the first response to an unexplained NULL is to delete the check that produced it
    (CLAUDE.md § 17). Migration 0020 stores the count on the REFUSED rows precisely so the refusal
    can be told apart from a defect, and this asserts that the refused rows really do carry it.
    """
    from tests.features.conftest import MEMPHIS

    # Memphis, because it is the site whose short daily record (2014-10-01, migration 0011) was the
    # reason eight was chosen - and the site Phase 5 expected to see the guard fire at.
    first_year = 2015
    series = _shallow_autumn_series(first_year)
    seed_readings.daily(MEMPHIS, series)

    start, end = series[0][0], series[-1][0]
    result = build.build(migrated_db, start, end)
    assert result["feature_rows"] > 0, (
        f"the build wrote no features, so nothing below is being asserted about the guard: {result}"
    )

    rows = migrated_db.execute(
        "SELECT date, value, anomaly, climatology_n_years"
        "  FROM features"
        " WHERE site_id = %s AND feature_name = 'discharge_mean'"
        " ORDER BY date",
        (MEMPHIS,),
    ).fetchall()
    assert rows, "no discharge_mean rows at the seeded site"

    # 1. THE REFUSAL. Every row, not merely the asserted day: with five years behind every
    #    day-of-year in this fixture, a single non-NULL anomaly means the guard is being applied
    #    somewhere other than where the year count is computed.
    with_anomaly = [row for row in rows if row[2] is not None]
    assert not with_anomaly, (
        f"{len(with_anomaly)} of {len(rows)} rows carry an anomaly computed from a "
        f"{SHALLOW_YEARS}-year record, and seasonal.MIN_YEARS is {seasonal.MIN_YEARS}. First: "
        f"{with_anomaly[0]}. The guard did not survive the round trip."
    )

    # 2. THE COUNT IS STILL THERE. This is the half that makes the NULL auditable rather than
    #    mysterious, and migration 0020's CHECK deliberately permits it without an anomaly.
    countless = [row for row in rows if row[3] is None]
    assert not countless, (
        f"{len(countless)} refused row(s) carry a NULL anomaly with NO climatology_n_years beside "
        f"it, which is indistinguishable from a bug - see CLAUDE.md § 17. First: {countless[0]}"
    )

    # 3. AND IT IS FIVE. Not "some number below eight": the count is what a human reads to decide
    #    whether a NULL is the guard working or the data missing, so a count that is merely
    #    under-threshold rather than correct would answer that question wrongly while passing a
    #    weaker assertion.
    asserted = date(first_year + SHALLOW_YEARS - 1, *ASSERTED_DAY)
    n_years = {row[0]: row[3] for row in rows}[asserted]
    assert n_years == SHALLOW_YEARS, (
        f"climatology_n_years on {asserted} is {n_years}, expected exactly {SHALLOW_YEARS}. Its "
        f"centred {seasonal.SMOOTHING_DAYS}-day window falls wholly inside a block seeded in "
        f"{SHALLOW_YEARS} consecutive years, so any other value means the window is pooling years "
        f"differently than seasonal.climatology documents."
    )
