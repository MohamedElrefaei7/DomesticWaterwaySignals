"""The effective sample size, the p-value that must come from it, and Benjamini-Hochberg.

THE HAND-COMPUTED EXPECTATIONS IN THIS FILE ARE THE POINT. Every mutation this module guards
against produces a number that is plausible, smooth and wrong - `n` where `n_effective` belongs
halves a p-value; a missing running minimum in BH leaves q-values that are individually reasonable
and not monotone. Neither is visible in an output table. So the assertions are against arithmetic a
reader can redo on paper, never against what the code produced last time.

The t distribution is the one thing here that cannot be hand-computed, and it is checked against
PUBLISHED CRITICAL VALUES instead - the same numbers a statistics table gives, which is an
independent source in exactly the way this project asks for.
"""

import math

import pytest

from app.signals import statistics

INTEGRATION_ROW = pytest.mark.integration


def test_effective_n_divides_by_the_overlap_factor():
    """Test 4. Hand-computed for the three horizons this project publishes targets at.

    The rate series is weekly. At horizon 7 a forward window is one published step and there is no
    overlap; at 14 consecutive windows share a week; at 21 they share two. The effective count is
    how many DISJOINT windows fit in the same span:

        horizon  7:  100 / (7/7)  = 100
        horizon 14:  100 / (14/7) =  50
        horizon 21:  100 / (21/7) = 33.333...
    """
    assert statistics.effective_n(100, 7) == pytest.approx(100.0)
    assert statistics.effective_n(100, 14) == pytest.approx(50.0)
    assert statistics.effective_n(100, 21) == pytest.approx(100.0 / 3.0)

    # NOT ROUNDED. n/3 is not an integer, and rounding it up would be a second, smaller version of
    # the optimism the correction exists to remove.
    assert statistics.effective_n(100, 21) != 33.0
    assert isinstance(statistics.effective_n(100, 21), float)

    assert statistics.overlap_factor(7) == 1.0
    assert statistics.overlap_factor(14) == 2.0
    assert statistics.overlap_factor(21) == 3.0

    # FLOORED AT 1.0. A horizon shorter than the publication interval does not overlap, and a
    # factor below 1 would INVENT observations - the correction running backwards. Migration 0023's
    # signals_effective_n_never_exceeds_raw_n is the same guard at the schema level.
    assert statistics.overlap_factor(1) == 1.0
    assert statistics.effective_n(100, 1) == pytest.approx(100.0)


def _series_with_known_correlation(n: int, noise: float):
    """A paired series long enough to split, with a real but imperfect correlation."""
    xs = [float(i) for i in range(n)]
    ys = [float(i) + noise * ((i * 7919) % 11 - 5) for i in range(n)]
    return xs, ys


def test_p_value_uses_effective_n_not_raw_n():
    """Test 5. THE SINGLE MOST IMPORTANT ASSERTION IN THIS SUITE.

    Every naive implementation feeds the raw count to the t distribution. It is one identifier, the
    result is a perfectly ordinary-looking p-value, and at horizon 14 it is ROUGHLY HALF what it
    should be - uniformly, across the entire grid, in the direction that makes this project's
    thesis look better.

    Two halves, and both are needed:
      1. the two computations genuinely DIFFER at horizon 14, so the assertion below is not vacuous
      2. what `measure` stores is the effective-n one
    """
    n = 40
    xs, ys = _series_with_known_correlation(n, noise=3.0)
    r = statistics.pearson(xs, ys)
    assert r is not None and 0.2 < r < 0.99, f"fixture correlation {r} is not a useful test case"

    from_raw = statistics.p_value(r, float(n))
    from_effective = statistics.p_value(r, statistics.effective_n(n, 14))

    # 1. THE GUARD IS NOT VACUOUS. If these were equal, everything below would pass with the
    #    correction removed.
    assert from_effective > from_raw, (
        f"the raw-n and effective-n p-values are {from_raw} and {from_effective}: halving the "
        f"sample size did not raise the p-value, so this test cannot detect the mutation it exists "
        f"for"
    )
    assert from_effective > from_raw * 1.5, (
        f"halving n moved the p-value from {from_raw} to {from_effective}, which is too small a "
        f"difference to distinguish the two implementations on this fixture"
    )

    # 2. AND THE STORED VALUE IS THE HONEST ONE.
    measurement = statistics.measure(xs, ys, 14)
    assert measurement is not None
    assert measurement.n_observations == n
    assert measurement.n_effective == pytest.approx(n / 2.0)
    assert measurement.p_value == pytest.approx(from_effective), (
        f"measure() stored p={measurement.p_value}; the effective-n computation gives "
        f"{from_effective} and the raw-n one gives {from_raw}. The stored value is the raw-n one."
    )

    # At horizon 7 there is no overlap, so the two agree - which is why a test written only against
    # horizon 7 would pass in both directions of the mutation.
    at_seven = statistics.measure(xs, ys, 7)
    assert at_seven.n_effective == pytest.approx(float(n))
    assert at_seven.p_value == pytest.approx(from_raw)


def test_bh_adjustment_matches_a_hand_computed_example():
    """Test 6. Five p-values, five q-values, and the third one is the whole test.

        m = 5, sorted ascending, q_i = min over j >= i of (p_j * m / j)

        rank  p        p * m / rank      running min from the top     q
        1     0.001    0.001*5/1 = 0.005          0.005               0.005
        2     0.008    0.008*5/2 = 0.020          0.020               0.020
        3     0.039    0.039*5/3 = 0.065          0.05125             0.05125   <- PULLED DOWN
        4     0.041    0.041*5/4 = 0.05125        0.05125             0.05125
        5     0.900    0.900*5/5 = 0.900          0.900               0.900

    RANK 3 IS WHY THIS FIXTURE LOOKS LIKE THIS. `p * m / rank` alone gives it 0.065, which is
    LARGER than rank 4's 0.05125 - so the column would not be monotone in p, and a row with a
    smaller p-value would carry a larger q than the row beneath it. The sweep's whole output is
    read in q order. A naive implementation passes every other assertion here and fails this one.
    """
    p_values = [0.001, 0.008, 0.039, 0.041, 0.9]
    q_values = statistics.benjamini_hochberg(p_values)

    assert q_values == pytest.approx([0.005, 0.02, 0.05125, 0.05125, 0.9]), (
        f"BH gave {q_values}; hand-computed is [0.005, 0.02, 0.05125, 0.05125, 0.9]"
    )

    # MONOTONE IN p, stated as its own assertion because it is the property the running minimum
    # buys and the property a reader relies on when sorting the results table.
    ordered = [q for _p, q in sorted(zip(p_values, q_values))]
    assert ordered == sorted(ordered), f"q-values are not monotone in p: {list(zip(p_values, q_values))}"

    # ORDER-INDEPENDENT. The adjustment is over a set of tests, so shuffling the input must permute
    # the output identically - a q-value that depended on the grid's enumeration order would differ
    # between two runs that scanned exactly the same thing.
    shuffled = [0.9, 0.001, 0.041, 0.008, 0.039]
    assert statistics.benjamini_hochberg(shuffled) == pytest.approx(
        [0.9, 0.005, 0.05125, 0.02, 0.05125]
    )

    # CLAMPED AT 1. On a grid this size p * m / rank exceeds 1 for most rows, and a "probability"
    # of 12.4 in a results table gets quietly dropped rather than explained.
    assert all(0.0 <= q <= 1.0 for q in statistics.benjamini_hochberg([0.4, 0.6, 0.8, 0.99]))

    assert statistics.benjamini_hochberg([]) == []
    # A single test needs no adjustment, and BH must not invent one.
    assert statistics.benjamini_hochberg([0.03]) == pytest.approx([0.03])

    # A None among the p-values is refused rather than treated as a test. Counting unscannable
    # pairs in m would weaken every real q-value on behalf of pairs that produced no evidence.
    with pytest.raises(ValueError, match="None"):
        statistics.benjamini_hochberg([0.01, None])


def test_the_t_distribution_matches_published_critical_values():
    """Not in the brief's list. It is the check that the hand-rolled incomplete beta is right.

    scipy was NOT added (see the module docstring), so the t tail is ~40 lines of continued
    fraction in this repo. The argument for writing it rather than depending on scipy is only good
    if it is checked against an independent source - so these are the standard two-tailed 5% and 1%
    critical values from any statistics table.
    """
    for t, df in [(12.706, 1), (4.303, 2), (2.228, 10), (2.042, 30), (1.984, 100)]:
        assert statistics.t_distribution_two_tailed_p(t, df) == pytest.approx(0.05, abs=5e-4), (
            f"t={t}, df={df} should be the two-tailed 5% point"
        )
    for t, df in [(63.657, 1), (3.169, 10), (2.750, 30)]:
        assert statistics.t_distribution_two_tailed_p(t, df) == pytest.approx(0.01, abs=5e-4)

    # The limits: no evidence at all, and the normal limit at large df.
    assert statistics.t_distribution_two_tailed_p(0.0, 10) == pytest.approx(1.0)
    assert statistics.t_distribution_two_tailed_p(1.96, 1e7) == pytest.approx(0.05, abs=1e-3)

    # FRACTIONAL DEGREES OF FREEDOM, which is what an effective sample size produces at horizon 21
    # and is the case a table lookup could not serve. Bracketed by its integer neighbours.
    between = statistics.t_distribution_two_tailed_p(2.2, 10.5)
    assert (
        statistics.t_distribution_two_tailed_p(2.2, 11)
        < between
        < statistics.t_distribution_two_tailed_p(2.2, 10)
    )


def test_a_constant_series_has_no_correlation_rather_than_a_zero_one():
    """Also not in the brief's list, and it is a real case rather than a defensive one.

    A run-length feature sits at exactly 0 for months - eleven consecutive weeks in the Phase 5
    measurement. Inside an `onset` window a feature can be constant across every observation that
    survived the filter, and `covariance / 0` is not a correlation of zero. Returning 0.0 would
    record "these move independently", which is a MEASUREMENT, where the truth is that no
    measurement was possible.
    """
    assert statistics.pearson([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None
    assert statistics.pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) is None
    assert statistics.measure([5.0, 5.0, 5.0], [1.0, 2.0, 3.0], 7) is None

    assert statistics.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert statistics.pearson([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]) == pytest.approx(-1.0)

    with pytest.raises(ValueError, match="differ in length"):
        statistics.pearson([1.0, 2.0], [1.0])

    # Below the mathematical floor there is no p-value, so the pair is refused rather than assigned
    # a number: df = n_effective - 2 must be positive.
    assert statistics.p_value(0.9, 2.0) is None
    assert statistics.measure([1.0, 2.0], [1.0, 3.0], 7) is None


# ---------------------------------------------------------------------------------------------
# The integration tier: two facts that live in the schema and cannot be asserted in Python.
# ---------------------------------------------------------------------------------------------


def _signal_row(run_id, **overrides):
    """A complete, legal `signals` row. Tests override the one column they are about."""
    row = {
        "run_id": run_id,
        "feature_name": "days_below_p10",
        "site_id": None,  # filled by the caller
        "series_column": "value",
        "target_name": "cairo_memphis_nearby_log_return",
        "horizon_days": 14,
        "lag_days": 7,
        "regime": "onset",
        "status": "scanned",
        "statistic": 0.42,
        "p_value": 0.001,
        "q_value": 0.01,
        "grid_size": 100,
        "n_tests_adjusted": 90,
        "n_observations": 60,
        "n_effective": 30.0,
        "folds": 5,
        "directional_consistency": 0.8,
        "passes_gate": True,
    }
    row.update(overrides)
    return row


INSERT = """
INSERT INTO signals
    (run_id, feature_name, site_id, series_column, target_name, horizon_days, lag_days, regime,
     status, statistic, p_value, q_value, grid_size, n_tests_adjusted, n_observations,
     n_effective, folds, directional_consistency, passes_gate)
VALUES (%(run_id)s, %(feature_name)s, %(site_id)s, %(series_column)s, %(target_name)s,
        %(horizon_days)s, %(lag_days)s, %(regime)s, %(status)s, %(statistic)s, %(p_value)s,
        %(q_value)s, %(grid_size)s, %(n_tests_adjusted)s, %(n_observations)s, %(n_effective)s,
        %(folds)s, %(directional_consistency)s, %(passes_gate)s)
"""


@INTEGRATION_ROW
def test_no_row_can_carry_a_p_value_without_a_q_value(migrated_db, seed_signals, sweepable):
    """Test 7. THE DATABASE refuses it, AND the writer never builds one. Both halves.

    The first half is the constraint. A test asserting only that `sweep.adjust_and_gate` produces a
    q-value would test the writer, and the writer is not the only thing that can insert here - a
    script, a manual INSERT during an investigation, or a future module all bypass it. The CHECK is
    where the rule holds for all of them.

    THE SECOND HALF IS AT THE END OF THIS TEST AND IT IS WHY THE SWEEP RUNS HERE. The mutation this
    guards - "drop the Benjamini-Hochberg adjustment, store only the raw p" - leaves the constraint
    perfectly intact. Asserting the CHECK exists would stay green while every row in the table
    carried an unadjusted p-value in a q-value's column, or no q at all. So the sweep is run and
    the table is read back.

    A raw p-value in a table of thousands is the number a reader's eye goes to, and it means
    something entirely different from what it means alone.
    """
    import psycopg

    from tests.signals.conftest import MEMPHIS

    run_id = seed_signals.open_run()

    # A legal row, so the fixture is known to be insertable and the failures below are about the
    # column under test rather than about something else being wrong.
    migrated_db.execute(INSERT, _signal_row(run_id, site_id=MEMPHIS))
    migrated_db.commit()

    with pytest.raises(psycopg.errors.CheckViolation, match="p_value_needs_its_q_value"):
        migrated_db.execute(
            INSERT, _signal_row(run_id, site_id=MEMPHIS, lag_days=1, q_value=None)
        )
    migrated_db.rollback()

    # AND THE REVERSE. A q-value with nothing to adjust is equally broken, and a one-directional
    # CHECK would permit it - which is what a "drop the BH step and keep the raw p" mutation would
    # produce if it were written the other way round.
    with pytest.raises(psycopg.errors.CheckViolation, match="p_value_needs_its_q_value"):
        migrated_db.execute(
            INSERT, _signal_row(run_id, site_id=MEMPHIS, lag_days=2, p_value=None)
        )
    migrated_db.rollback()

    # Nothing may pass the gate on an unadjusted p-value either. Without this, dropping BH would
    # leave a table full of passing rows carrying no q at all.
    with pytest.raises(psycopg.errors.CheckViolation, match="passing_rows_carry_a_q_value"):
        migrated_db.execute(
            INSERT,
            _signal_row(
                run_id,
                site_id=MEMPHIS,
                lag_days=3,
                p_value=None,
                q_value=None,
                statistic=None,
                passes_gate=True,
            ),
        )
    migrated_db.rollback()

    # A refusal row - no statistic, no p, no q - is legal, because it must be: writing only the
    # measurable pairs is what destroys the denominator.
    migrated_db.execute(
        INSERT,
        _signal_row(
            run_id,
            site_id=MEMPHIS,
            lag_days=4,
            status="insufficient_observations",
            statistic=None,
            p_value=None,
            q_value=None,
            n_effective=None,
            folds=None,
            directional_consistency=None,
            passes_gate=False,
        ),
    )
    migrated_db.commit()

    # ------------------------------------------------------------------------------------------
    # THE SECOND HALF: a real sweep, and the table read back. See the docstring.
    # ------------------------------------------------------------------------------------------
    from app.signals import regimes, sweep

    from tests.signals.conftest import FIXED_GIT, SWEEP_HORIZONS, SWEEP_LAG_MAX, SWEEP_LAG_MIN

    result = sweep.run(
        migrated_db,
        lag_min=SWEEP_LAG_MIN,
        lag_max=SWEEP_LAG_MAX,
        horizons=SWEEP_HORIZONS,
        regimes=regimes.REGIMES,
        git=FIXED_GIT,
    )

    unadjusted = migrated_db.execute(
        "SELECT count(*) FROM signals"
        " WHERE run_id = %s AND p_value IS NOT NULL AND q_value IS NULL",
        (result["run_id"],),
    ).fetchone()[0]
    assert unadjusted == 0, (
        f"{unadjusted} row(s) carry a raw p-value with no q-value. On a grid of "
        f"{result['grid_size']} an unadjusted p means something entirely different from what it "
        f"means alone, and it is the number a reader's eye goes to."
    )

    # AND THE ADJUSTMENT ACTUALLY MOVED THE NUMBERS. Storing p in the q column would satisfy every
    # assertion above - the constraint only requires both to be present - so the two must differ.
    adjusted, moved, m = migrated_db.execute(
        "SELECT count(*),"
        "       count(*) FILTER (WHERE q_value > p_value),"
        "       max(n_tests_adjusted)"
        "  FROM signals WHERE run_id = %s AND p_value IS NOT NULL",
        (result["run_id"],),
    ).fetchone()
    assert adjusted > 1
    assert moved > 0, (
        f"no q-value on this run exceeds its p-value across {adjusted} tests. Benjamini-Hochberg "
        f"scales by m/rank and m is {m}, so the raw p is being copied into the q column rather "
        f"than adjusted."
    )

    # Monotone in p across the whole run, which is what the running minimum buys and what makes the
    # table sortable by q.
    ordered = migrated_db.execute(
        "SELECT p_value, q_value FROM signals"
        " WHERE run_id = %s AND p_value IS NOT NULL ORDER BY p_value",
        (result["run_id"],),
    ).fetchall()
    assert all(a[1] <= b[1] + 1e-12 for a, b in zip(ordered, ordered[1:])), (
        "q-values are not monotone in p across the run"
    )


@INTEGRATION_ROW
def test_grid_size_is_stored_on_every_row(migrated_db, seed_signals):
    """Test 8. A q-value with no denominator beside it is not interpretable.

    The same q-value means different things on a grid of 7,000 and a grid of 200, and the two are
    indistinguishable once they are in the same column. NOT NULL is what stops a row from existing
    without the number that makes its q readable.
    """
    import psycopg

    from tests.signals.conftest import MEMPHIS

    run_id = seed_signals.open_run()

    with pytest.raises(psycopg.errors.NotNullViolation, match="grid_size"):
        migrated_db.execute(INSERT, _signal_row(run_id, site_id=MEMPHIS, grid_size=None))
    migrated_db.rollback()

    # And the m BH actually adjusted against, which is a different number whenever any pair was
    # unscannable. Storing only one of the two would misdescribe the adjustment.
    with pytest.raises(psycopg.errors.NotNullViolation, match="n_tests_adjusted"):
        migrated_db.execute(INSERT, _signal_row(run_id, site_id=MEMPHIS, n_tests_adjusted=None))
    migrated_db.rollback()

    # A grid of zero is refused too: it would make every q-value on the run meaningless while
    # looking like an ordinary integer.
    with pytest.raises(psycopg.errors.CheckViolation, match="grid_size_positive"):
        migrated_db.execute(INSERT, _signal_row(run_id, site_id=MEMPHIS, grid_size=0))
    migrated_db.rollback()

    # The effective sample size can never exceed the raw count - the schema's own copy of the
    # overlap correction's direction. n_effective ABOVE n is the correction applied backwards,
    # which looks like a stronger result rather than like a bug.
    with pytest.raises(psycopg.errors.CheckViolation, match="effective_n_never_exceeds_raw_n"):
        migrated_db.execute(
            INSERT, _signal_row(run_id, site_id=MEMPHIS, n_observations=30, n_effective=60.0)
        )
    migrated_db.rollback()

    migrated_db.execute(INSERT, _signal_row(run_id, site_id=MEMPHIS))
    migrated_db.commit()

    stored = migrated_db.execute(
        "SELECT grid_size, n_tests_adjusted FROM signals WHERE run_id = %s", (run_id,)
    ).fetchall()
    assert stored and all(row[0] is not None and row[1] is not None for row in stored)
    assert math.isclose(stored[0][0], 100)
