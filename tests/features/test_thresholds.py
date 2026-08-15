"""The threshold-duration family: strict inequality, NULL across a gap, and no invented level.

The gap tests use the REAL ranges from this project's own `gauge_known_gaps` table - Baton Rouge
2023-01-04 to 2023-08-14 and Memphis 1994-2014 - rather than invented ones, because the distinction
between a zero and a NULL here is load-bearing on those exact rows and nowhere else it could be
demonstrated.
"""

from datetime import date, timedelta

import pytest

from app.features import thresholds

# The two measured gaps, from migration 0012. Inclusive of the first and last missing day.
BATON_ROUGE_GAP_START = date(2023, 1, 4)
BATON_ROUGE_GAP_END = date(2023, 8, 14)


def run(start: date, values):
    """`(date, value)` pairs on consecutive days from `start`."""
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def counts(rows):
    """`{date: run_length}` from the builder's output."""
    return dict(rows)


def test_consecutive_days_below_counts_correctly():
    """Test 11. A hand-built run, counted forwards, resetting on the first day back above.

    Threshold 100. Values 90, 80, 70 are below; 110 is not; then 60, 50 start again.
        1, 2, 3, 0, 1, 2
    """
    rows = counts(thresholds.days_below(run(date(2022, 9, 1), [90, 80, 70, 110, 60, 50]), 100))

    assert [rows[date(2022, 9, 1) + timedelta(days=i)] for i in range(6)] == [1, 2, 3, 0, 1, 2], (
        f"the run lengths are {sorted(rows.items())}"
    )


def test_a_missing_day_yields_null_not_zero():
    """Test 12. The decision this module exists for.

    ZERO ASSERTS THE RIVER CAME BACK UP. It is a measurement - "the run of low days ended here".
    NULL says the run length is unknown. Across a hole, zeroing manufactures a recovery on a day
    nobody observed, and every feature built on run length reads it as one that happened.

    Two low days, a one-day hole, then two more low days. The days after the hole are UNKNOWN: the
    run may have begun before it, so counting from 1 would understate a long event as a short one.
    """
    rows = counts(
        thresholds.days_below(
            [
                (date(2022, 9, 1), 90),
                (date(2022, 9, 2), 90),
                # 2022-09-03 is absent.
                (date(2022, 9, 4), 90),
                (date(2022, 9, 5), 90),
            ],
            100,
        )
    )

    assert rows[date(2022, 9, 2)] == 2
    assert rows[date(2022, 9, 4)] is None, (
        f"the day after the hole counted {rows[date(2022, 9, 4)]}. A 0 asserts the river recovered "
        f"on a day nobody observed; a 1 asserts a new run began, which understates an event that "
        f"may have been running throughout."
    )
    assert rows[date(2022, 9, 5)] is None, "the unknown state ended without the river coming back up"

    # A PRESENT ROW WITH NO VALUE IS THE SAME STATEMENT as an absent one, and must behave the same.
    with_null = counts(
        thresholds.days_below(
            [(date(2022, 9, 1), 90), (date(2022, 9, 2), None), (date(2022, 9, 3), 90)], 100
        )
    )
    assert with_null[date(2022, 9, 2)] is None and with_null[date(2022, 9, 3)] is None


def test_the_counter_resets_after_a_real_gap():
    """Test 13. Baton Rouge 2023-01-04 -> 2023-08-14, from `gauge_known_gaps`.

    Seven months missing. The first day back is unknown while the river is still low - and then
    KNOWLEDGE COMES BACK the moment a day is not below the threshold, because no run of
    below-threshold days can span a day that was not below. That is why the unknown state is
    escaped by an ordinary observation rather than by a rule with its own edge cases.
    """
    before = [(BATON_ROUGE_GAP_START - timedelta(days=n), 90) for n in (3, 2, 1)]
    after_low = [(BATON_ROUGE_GAP_END + timedelta(days=n), 90) for n in (1, 2)]
    recovered = [(BATON_ROUGE_GAP_END + timedelta(days=3), 150)]
    low_again = [(BATON_ROUGE_GAP_END + timedelta(days=4), 80)]

    rows = counts(
        thresholds.days_below(sorted(before + after_low + recovered + low_again), 100)
    )

    assert rows[BATON_ROUGE_GAP_START - timedelta(days=1)] == 3, "the pre-gap run miscounted"

    assert rows[BATON_ROUGE_GAP_END + timedelta(days=1)] is None, (
        "the first day after a seven-month gap carries a number; nothing observed the run in "
        "between, so no number is available"
    )
    assert rows[BATON_ROUGE_GAP_END + timedelta(days=2)] is None

    assert rows[BATON_ROUGE_GAP_END + timedelta(days=3)] == 0, (
        "a day ABOVE the threshold after a gap is a definite zero - no run can span it - so "
        "knowledge is restored here and must not stay NULL forever"
    )
    assert rows[BATON_ROUGE_GAP_END + timedelta(days=4)] == 1, (
        "counting did not resume after knowledge was restored"
    )


def test_a_value_exactly_at_the_threshold_is_not_below():
    """Test 14. STRICTLY below. The boundary is real, not hypothetical.

    A percentile of a record containing repeated values IS one of the observed values, so rows
    land exactly on the threshold in ordinary data. `<=` would count them, quietly lengthening
    every run - and a run length that is systematically one or two too long across a whole series
    is not something any summary statistic reveals.
    """
    rows = counts(
        thresholds.days_below(run(date(2022, 9, 1), [99.0, 100.0, 99.0, 100.0]), 100.0)
    )

    assert rows[date(2022, 9, 1)] == 1
    assert rows[date(2022, 9, 2)] == 0, (
        "a value exactly at the threshold was counted as below it. The comparison is `<`, not "
        "`<=`, and with percentile thresholds the equal case occurs in real rows."
    )
    assert rows[date(2022, 9, 3)] == 1, "the run did not restart after the at-threshold day"
    assert rows[date(2022, 9, 4)] == 0


def test_thresholds_are_percentiles_of_the_sites_own_record():
    """Test 15. Derived from the data, and NO ABSOLUTE LEVEL APPEARS IN THE MODULE.

    CLAUDE.md § 1 puts "threshold values that define an event" on the never-delegate list. A
    constant like `LOW_WATER_CFS = 150000` landing here without a source would read as measured six
    months later, and every conclusion drawn through it would inherit an authority nothing gave it.

    A percentile is a property of the data instead: "the lowest 5% of days this gauge has recorded"
    is a statement anybody can check against the table.
    """
    record = [float(v) for v in range(100, 1100, 100)]  # 100..1000, ten values

    levels = thresholds.thresholds_for(record)
    assert set(levels) == {5, 10, 20}

    # Hand-computed against linear interpolation between order statistics:
    # position = (n-1) * p/100 = 9 * 0.10 = 0.9  ->  100 + 0.9*(200-100) = 190
    assert levels[10] == pytest.approx(190.0), f"the 10th percentile is {levels[10]}, not 190"
    assert levels[5] == pytest.approx(145.0)
    assert levels[20] == pytest.approx(280.0)
    assert levels[5] < levels[10] < levels[20]

    # A PROPERTY OF THE RECORD: a different site's record must give different thresholds. A
    # constant would give the same answer for both, which is the whole failure.
    other_site = [v * 10 for v in record]
    assert thresholds.thresholds_for(other_site)[10] == pytest.approx(1900.0), (
        "two sites with different records produced the same threshold, so it is not being derived "
        "from the data"
    )

    # AND NO ABSOLUTE RIVER LEVEL IS DECLARED ANYWHERE IN THE MODULE. Checked over the module's own
    # constants rather than by grepping the source, so a comment mentioning a number does not fail
    # it and a real constant cannot hide behind formatting.
    numeric_constants = {
        name: value
        for name, value in vars(thresholds).items()
        if not name.startswith("_") and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    oversized = {n: v for n, v in numeric_constants.items() if abs(v) >= 100}
    assert not oversized, (
        f"module-level constant(s) {oversized} look like absolute river levels. Thresholds are "
        f"human-owned (CLAUDE.md § 1) and this module seeds percentiles only - an absolute cfs or "
        f"stage value here would read as measured and nothing measured it."
    )

    # The percentile LEVELS themselves are the only numbers, and they are levels rather than flows.
    assert thresholds.PERCENTILES == (5, 10, 20)


def test_an_empty_record_has_no_threshold_and_says_so():
    """A site with no observations gets an exception, never a default.

    Defaulting would invent exactly the level this module refuses to invent, and it would do it
    for the site that has the least evidence behind it.
    """
    with pytest.raises(ValueError) as excinfo:
        thresholds.thresholds_for([])
    assert "empty record" in str(excinfo.value)


def test_the_builder_names_and_shapes_its_rows_for_the_registry():
    """`build_days_below` returns the four-tuple the build loop writes, with no anomaly.

    A run length is already a departure-from-normal measure. Deseasonalizing it would mean
    subtracting a day-of-year median of a count from a count, which is a number with no meaning
    attached - so both the anomaly and its year count are None, and migration 0020's CHECK permits
    that pairing precisely for this case.
    """
    assert thresholds.feature_name_for(5) == "days_below_p05"
    assert thresholds.feature_name_for(20) == "days_below_p20"

    rows = thresholds.build_days_below(run(date(2022, 9, 1), [10, 20, 30, 40, 50]), level=20)
    assert len(rows) == 5
    for day, value, anomaly, n_years in rows:
        assert isinstance(day, date)
        assert anomaly is None and n_years is None, (
            "a run-length feature produced an anomaly; there is no meaningful climatology of a "
            "count of consecutive days"
        )
    # The 20th percentile of 10..50 is 18, so only the first day is below it.
    assert [value for _day, value, _a, _n in rows] == [1, 0, 0, 0, 0]
