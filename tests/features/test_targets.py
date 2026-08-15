"""Targets: log-returns, and the two situations where NULL is the honest answer.

The symmetry test asserts the property the decision is made ON, rather than asserting that
`math.log` was called. A test that checked the implementation would pass for any monotone
transform; this one fails for percent change, which is the actual alternative somebody would reach
for.
"""

import math
from datetime import date, timedelta

import pytest

from app.features import targets

THURSDAY = date(2022, 8, 4)


def weeks(start: date, rates) -> list[tuple]:
    """`(week_ending, rate)` pairs on consecutive published weeks."""
    return [(start + timedelta(days=7 * i), r) for i, r in enumerate(rates)]


def by_week(rows):
    return {week: value for week, value in rows}


def test_forward_log_return_is_computed_correctly():
    """Test 16. Hand-computed for a known pair.

    100 -> 200 over one week is ln(2) = 0.693147...; 200 -> 100 is its negative.
    """
    series = weeks(THURSDAY, [100.0, 200.0, 100.0])
    rows = by_week(targets.forward_log_returns(series, 7))

    assert rows[THURSDAY] == pytest.approx(math.log(2)), (
        f"the 7-day return from 100 to 200 is {rows[THURSDAY]}, hand-computed {math.log(2)}"
    )
    assert rows[THURSDAY + timedelta(days=7)] == pytest.approx(-math.log(2))

    # ADDITIVITY, which is the second property log-returns are chosen for: the two 7-day returns
    # spanning a fortnight sum to the 14-day one. Percent changes do not do this either.
    series = weeks(THURSDAY, [100.0, 150.0, 300.0])
    seven = by_week(targets.forward_log_returns(series, 7))
    fourteen = by_week(targets.forward_log_returns(series, 14))
    assert seven[THURSDAY] + seven[THURSDAY + timedelta(days=7)] == pytest.approx(
        fourteen[THURSDAY]
    )


def test_log_return_is_symmetric_for_a_doubling_and_a_halving():
    """Test 19. Decision 6's property, asserted directly.

    THE 2022 EVENT IS THE ARGUMENT. The Cairo-Memphis rate went 388 -> 2,812.5, which is +625% as a
    percent change; the move that undoes it is -86%. Those are the same move in opposite directions
    with magnitudes differing by a factor of seven, so anything fitted on percent changes learns
    that asymmetry as though it were a fact about barge freight rather than a fact about division.
    """
    doubling = targets.forward_log_return(100.0, 200.0)
    halving = targets.forward_log_return(200.0, 100.0)

    assert doubling == pytest.approx(-halving), (
        f"a doubling is {doubling} and a halving is {halving}; they must be equal and opposite. "
        f"Percent change gives +1.0 and -0.5, and every summary statistic inherits the asymmetry."
    )
    assert doubling == pytest.approx(0.6931471805599453)

    # The real event's magnitudes, at the scale the decision was made at.
    rise = targets.forward_log_return(388.0, 2812.5)
    fall = targets.forward_log_return(2812.5, 388.0)
    assert rise == pytest.approx(-fall)
    # Percent change would have given +6.25 and -0.862 - the asymmetry this refuses.
    assert abs(rise) < 2.0, (
        f"a 7.2x move produced {rise}, which is percent-change scale rather than log scale"
    )


def test_a_missing_forward_rate_yields_null_not_a_carried_value():
    """Test 17. A winter closure is not a week the price held steady.

    USDA publishes no rate for a closed river - 774 of 8,260 nearby records, mostly December-March
    (migration 0017). Carrying the previous week's rate through one would produce a return of
    EXACTLY ZERO, and zero is the most ordinary value this column can hold: it means the price did
    not move. So the fabrication is invisible, it lands preferentially in winter, and any seasonal
    comparison built on it finds winter unusually calm.
    """
    # Week 2 has no published rate; week 3 does.
    series = weeks(THURSDAY, [500.0, None, 800.0])
    rows = by_week(targets.forward_log_returns(series, 7))

    assert rows[THURSDAY] is None, (
        f"the 7-day return into an unpublished week is {rows[THURSDAY]}. If it is 0.0 the previous "
        f"rate was carried forward, which manufactures 'the price did not move' out of a closure."
    )
    # And a week whose OWN rate is unpublished has no return either - there is nothing to measure
    # the move from.
    assert rows[THURSDAY + timedelta(days=7)] is None

    # The 14-day horizon skips over the closure entirely and IS computable, which is the check that
    # this test is not passing because everything became NULL.
    fourteen = by_week(targets.forward_log_returns(series, 14))
    assert fourteen[THURSDAY] == pytest.approx(math.log(800.0 / 500.0))


def test_the_final_horizon_weeks_have_no_target():
    """Test 18. The end of the series is correct, not a gap - and the row still exists.

    A NULL row states "no forward observation yet"; a missing row leaves the series simply ending
    early, which nothing can distinguish from a build that stopped short.

    ANY COVERAGE CHECK MUST USE THE RESOLVABLE COUNT, never the row count. `count(*) FILTER (WHERE
    value IS NOT NULL) = count(*)` is the check that looks right and reports the newest three weeks
    as broken on every run, forever.
    """
    series = weeks(THURSDAY, [100.0, 110.0, 120.0, 130.0, 140.0])
    rows = targets.forward_log_returns(series, 21)

    assert len(rows) == 5, "weeks with no target were dropped rather than written as NULL"

    values = by_week(rows)
    # Weeks 1 and 2 have a +21-day partner (weeks 4 and 5); weeks 3, 4 and 5 do not.
    assert values[THURSDAY] is not None
    assert values[THURSDAY + timedelta(days=7)] is not None
    for offset in (14, 21, 28):
        assert values[THURSDAY + timedelta(days=offset)] is None

    assert targets.resolvable_week_count(series, 21) == 2, (
        "resolvable_week_count is the denominator a coverage check needs; it must count only "
        "weeks with BOTH endpoints published"
    )
    assert targets.resolvable_week_count(series, 7) == 4


def test_the_forward_week_is_found_by_exact_date_not_by_position():
    """A gap in the rate series must not let a +14-day observation answer a +7-day question.

    `rates[i + 1]` is the positional version. It reads naturally and it silently reaches across a
    missing week, recording a fortnight's move under horizon 7 - a wrong number in a right-looking
    column, with nothing downstream able to detect it.
    """
    series = [
        (THURSDAY, 100.0),
        # THURSDAY + 7 is absent from the series entirely.
        (THURSDAY + timedelta(days=14), 200.0),
    ]
    rows = by_week(targets.forward_log_returns(series, 7))

    assert rows[THURSDAY] is None, (
        f"the 7-day return is {rows[THURSDAY]}; the +7 week is not in the series at all, so the "
        f"next row (at +14) has been used to answer a question about one week"
    )
    assert by_week(targets.forward_log_returns(series, 14))[THURSDAY] == pytest.approx(math.log(2))


def test_build_targets_covers_every_horizon_and_names_the_series():
    """All three horizons, one row per week each, under the one target name."""
    series = weeks(THURSDAY, [100.0, 110.0, 120.0, 130.0])
    rows = targets.build_targets(series)

    assert {horizon for _w, _n, horizon, _v in rows} == {7, 14, 21}
    assert {name for _w, name, _h, _v in rows} == {"cairo_memphis_nearby_log_return"}
    assert len(rows) == 4 * 3
    assert targets.TARGET_LOCATION == "Cairo-Memphis" and targets.TARGET_HORIZON == "nearby"


def test_a_non_positive_rate_is_refused_rather_than_crashing_the_build():
    """`math.log(0)` raises; a rate of zero is a data problem, not a build failure.

    `pct_of_tariff` carries a CHECK requiring it to be positive (migration 0017), so a zero here
    means something upstream changed - and a ValueError from deep inside the arithmetic would
    surface as a crash rather than as the data problem it is.
    """
    assert targets.forward_log_return(0.0, 100.0) is None
    assert targets.forward_log_return(100.0, 0.0) is None
    assert targets.forward_log_return(-5.0, 100.0) is None
