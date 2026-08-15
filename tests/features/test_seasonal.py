"""Deseasonalization: the median, the eight-year guard, and the leap day.

Every test here is against a hand-computed expectation. That is the point of the builders being
pure functions (CLAUDE.md § 17): a test that read the climatology back out of the database would be
asserting that the code computes what the code computes, and would pass in both directions of every
mutation below.
"""

from datetime import date, timedelta

import pytest

from app.features import seasonal


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
