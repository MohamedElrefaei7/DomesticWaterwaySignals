"""The leakage gap, the fold minimum, and directional consistency with its fold count.

THE CENTRAL TEST IN THIS FILE IS 12, AND ITS FIXTURE IS DAILY ON PURPOSE. The gap's failure mode is
an off-by-one - a training observation whose forward window lands EXACTLY on the first test date -
and on the weekly rate grid the boundary falls between observations, so a one-day error is
invisible. A daily date series is where the boundary is reachable, which is CLAUDE.md § 17's point
about the nearest-date guard restated: against a dense series the two implementations disagree, and
against a sparse one the test is vacuous.
"""

from datetime import date, timedelta

import pytest

from app.signals import walkforward

HORIZONS = (7, 14, 21)


def daily_dates(start: date, count: int) -> list:
    return [start + timedelta(days=i) for i in range(count)]


def weekly_dates(start: date, count: int) -> list:
    return [start + timedelta(days=7 * i) for i in range(count)]


def test_no_training_target_window_intersects_any_test_date():
    """Test 12. DECISION 4, AT THE DATA LEVEL.

    A target is a FORWARD log-return: the observation at `d` is computed from the rate at
    `d + horizon`. A training row therefore reaches `horizon_days` past its own date. If the test
    window opens inside that reach, a training target is a function of a test observation and the
    evaluation is contaminated - in the direction that flatters, because the model is scored partly
    on what it was fitted on.

    THIS DOES NOT CHECK THE SPLITTER'S CONFIGURED GAP. It takes the folds the splitter actually
    returned, reconstructs the training and test sets from their boundaries, and looks for any
    training date whose forward window contains a test date. `assert gap == horizon` would pass
    while the splitter was off by one, which is CLAUDE.md § 2's theme 2 exactly.
    """
    dates = daily_dates(date(2020, 1, 1), 400)

    for horizon in HORIZONS:
        folds = walkforward.splits(dates, horizon_days=horizon)
        assert len(folds) == walkforward.MIN_FOLDS, (
            f"horizon {horizon} produced {len(folds)} folds over 400 daily observations"
        )

        for fold in folds:
            leaks = walkforward.leaking_training_dates(fold, dates, horizon)
            assert not leaks, (
                f"horizon {horizon}, fold {fold.index}: {len(leaks)} training observation(s) have "
                f"a {horizon}-day forward window reaching into the test window "
                f"[{fold.test_start} .. {fold.test_end}]. First offender {leaks[0]}, whose target "
                f"reads {leaks[0] + timedelta(days=horizon)}. Training ends {fold.train_end}."
            )

        # THE BOUNDARY IS TIGHT, not merely safe. A splitter that gapped by a year would pass the
        # loop above and would be throwing away most of the series - so the last admitted training
        # observation must be the LAST one that could have been admitted.
        for fold in folds:
            reach = walkforward.forward_window_end(fold.train_end, horizon)
            assert reach < fold.test_start
            next_day = fold.train_end + timedelta(days=1)
            if next_day in dates and next_day < fold.test_start:
                assert walkforward.forward_window_end(next_day, horizon) >= fold.test_start, (
                    f"horizon {horizon}, fold {fold.index}: {next_day} could have been trained on "
                    f"without leaking, so the gap is wider than the horizon requires"
                )

    # AND THE GUARD CATCHES A HAND-BUILT LEAK. Without this, `leaking_training_dates` returning []
    # unconditionally would make every assertion above pass.
    leaky = walkforward.Fold(
        index=0,
        train_start=date(2020, 1, 1),
        train_end=date(2020, 1, 20),
        test_start=date(2020, 1, 21),
        test_end=date(2020, 1, 31),
        gap_days=7,
        n_train=20,
        n_test=11,
    )
    found = walkforward.leaking_training_dates(leaky, dates, 7)
    assert found, "a fold whose training window ends the day before its test window reports no leak"
    assert found[-1] == date(2020, 1, 20)

    # The O(1) runtime form the sweep uses agrees with the exhaustive one on the same fold.
    with pytest.raises(ValueError, match="leaks"):
        walkforward.assert_gap_clean([leaky], 7)
    walkforward.assert_gap_clean(walkforward.splits(dates, horizon_days=7), 7)


def test_the_gap_equals_horizon_days_for_each_horizon():
    """Test 13. The applied gap is the target horizon, for every horizon, on both date grids.

    Read together with test 12, which is what makes this meaningful: on its own it is a
    configuration assertion of exactly the kind this project has already been burned by. Here it
    pins the SECOND half - that the gap tracks the horizon rather than being a constant that
    happens to be safe at 21 days and is over-wide at 7.
    """
    for dates in (daily_dates(date(2020, 1, 1), 400), weekly_dates(date(2020, 1, 2), 200)):
        for horizon in HORIZONS:
            folds = walkforward.splits(dates, horizon_days=horizon)
            assert folds, f"no folds for horizon {horizon}"
            assert {fold.gap_days for fold in folds} == {horizon}, (
                f"horizon {horizon} produced gaps {sorted({f.gap_days for f in folds})}"
            )

    # AND THE GAP IS ACTUALLY APPLIED, not merely stored. A `gap_days` field that no splitter
    # consulted would satisfy every assertion above, so the observable consequence is asserted too.
    #
    # WHAT MOVES IS THE TEST BOUNDARY, NOT THE TRAINING ONE, and that is worth stating because the
    # obvious expectation is the opposite. Training cannot begin ending earlier: the first fold's
    # training window is pinned by MIN_TRAIN_OBSERVATIONS, so `train_end` is the same observation
    # for every horizon. A longer gap therefore pushes the first TESTABLE observation later - the
    # separation between the two grows by exactly the horizon.
    dates = daily_dates(date(2020, 1, 1), 400)
    first = {h: walkforward.splits(dates, horizon_days=h)[0] for h in HORIZONS}

    assert len({fold.train_end for fold in first.values()}) == 1, (
        f"the first fold's training boundary moved with the horizon: "
        f"{ {h: f.train_end for h, f in first.items()} }. It is pinned by "
        f"MIN_TRAIN_OBSERVATIONS and should not."
    )

    starts = {h: fold.test_start for h, fold in first.items()}
    assert starts[7] < starts[14] < starts[21], (
        f"the test boundary does not recede as the horizon grows: {starts}. gap_days is being "
        f"stored but not applied."
    )
    assert (starts[14] - starts[7]).days == 7
    assert (starts[21] - starts[14]).days == 7

    # Stated directly: the separation between the last training observation and the first test one
    # is the horizon, plus the one day that makes the inequality strict.
    for horizon, fold in first.items():
        assert (fold.test_start - fold.train_end).days == horizon + 1, (
            f"horizon {horizon}: training ends {fold.train_end} and testing starts "
            f"{fold.test_start}, a separation of {(fold.test_start - fold.train_end).days} days. "
            f"It must be {horizon + 1} - the horizon, so the last training target's forward window "
            f"ends the day before testing begins."
        )


def test_fewer_than_five_usable_folds_reports_insufficient_folds():
    """Test 14. A short series yields NO folds, and the caller records a stated refusal.

    Silently evaluating on three folds is the failure this returns empty to prevent: three folds
    all agreeing is 100% directional consistency, and it would clear a gate written for five. The
    number would be correct, the label would be correct, and the row would be worthless.
    """
    short = weekly_dates(date(2022, 1, 6), 20)
    assert walkforward.splits(short, horizon_days=14) == [], (
        "a 20-observation series produced folds despite MIN_TRAIN_OBSERVATIONS being "
        f"{walkforward.MIN_TRAIN_OBSERVATIONS}"
    )

    # Just under the line: enough for a training window but not for five test blocks after it.
    barely = weekly_dates(date(2022, 1, 6), walkforward.MIN_TRAIN_OBSERVATIONS + 3)
    assert walkforward.splits(barely, horizon_days=7) == []

    # And over it, exactly MIN_FOLDS - never four, never six.
    ample = weekly_dates(date(2018, 1, 4), 200)
    folds = walkforward.splits(ample, horizon_days=7)
    assert len(folds) == walkforward.MIN_FOLDS == 5

    # EVERY FOLD MEETS THE TRAINING MINIMUM, including the first. A splitter that met it on average
    # would put the weakest evidence in the earliest fold, where nobody looks.
    assert all(fold.n_train >= walkforward.MIN_TRAIN_OBSERVATIONS for fold in folds), (
        f"training sizes {[f.n_train for f in folds]} against a minimum of "
        f"{walkforward.MIN_TRAIN_OBSERVATIONS}"
    )

    # The windows walk forward and the training set expands. A fold that trained on data after its
    # own test window would be the leak test 12 catches, arriving by a different route.
    for earlier, later in zip(folds, folds[1:]):
        assert later.test_start > earlier.test_end
        assert later.n_train >= earlier.n_train
        assert later.train_end >= earlier.train_end

    # No observation is dropped off the end: the last block absorbs the remainder, so the most
    # recent fold is not quietly the shortest.
    assert folds[-1].test_end == ample[-1]

    with pytest.raises(ValueError, match="horizon_days must be positive"):
        walkforward.splits(ample, horizon_days=0)


def test_directional_consistency_is_the_fraction_of_folds_matching_the_full_sample_sign():
    """Test 15. Hand-computed, including the cases that are easy to get wrong by exclusion.

        full sample +0.40
        folds       +0.10  +0.50  -0.20  +0.30  +0.60
        matching    4 of 5 = 0.80
    """
    result = walkforward.directional_consistency(0.40, [0.10, 0.50, -0.20, 0.30, 0.60])
    assert result.fraction == pytest.approx(0.8)
    assert result.folds == 5

    # A NEGATIVE full-sample sign is matched by negative folds. The measure is agreement in
    # DIRECTION, not positivity - and Phase 5's recovery-side observation is a negative
    # relationship that this gate must be able to confirm rather than reject.
    negative = walkforward.directional_consistency(-0.40, [-0.1, -0.5, 0.2, -0.3, -0.6])
    assert negative.fraction == pytest.approx(0.8)
    assert negative.folds == 5

    assert walkforward.directional_consistency(0.4, [0.1] * 5).fraction == pytest.approx(1.0)
    assert walkforward.directional_consistency(0.4, [-0.1] * 5).fraction == pytest.approx(0.0)

    # A ZERO-SIGN FOLD COUNTS AS NOT MATCHING RATHER THAN BEING DROPPED. Excluding it would raise
    # the fraction by shrinking the denominator, which is this whole phase's failure mode at the
    # scale of one pair: 4 matching of 5 folds is 0.80, not 4 of 4.
    with_a_zero = walkforward.directional_consistency(0.4, [0.1, 0.2, 0.3, 0.4, 0.0])
    assert with_a_zero.folds == 5
    assert with_a_zero.fraction == pytest.approx(0.8)

    # An UNMEASURABLE fold is a different thing from a fold with no direction, and it is excluded -
    # `None` means the correlation could not be computed there at all, so there is no result to
    # agree or disagree with. The fold count then says 4, which is the honest denominator and is
    # below the gate's minimum.
    with_a_none = walkforward.directional_consistency(0.4, [0.1, 0.2, 0.3, 0.4, None])
    assert with_a_none.folds == 4
    assert with_a_none.fraction == pytest.approx(1.0)

    # Nothing to measure is None, not zero. Zero would say "no fold agreed", which is a
    # measurement, and this is its absence.
    assert walkforward.directional_consistency(0.4, []) is None
    assert walkforward.directional_consistency(0.4, [None, None]) is None
    assert walkforward.directional_consistency(None, [0.1, 0.2]) is None
    assert walkforward.directional_consistency(0.0, [0.1, 0.2]) is None, (
        "a full-sample statistic of exactly zero has no sign for a fold to match"
    )


def test_fold_count_is_stored_alongside_consistency():
    """Test 16. 4 of 5 and 40 of 50 are both 80% and are not equally informative.

    Asserted against the TYPE rather than against a call's return value, because the guard is that
    there is no way to obtain the fraction without the count. A function returning a bare float
    would satisfy every assertion in test 15 and would be exactly the thing this forbids.
    """
    fields = walkforward.Consistency.__dataclass_fields__
    assert set(fields) == {"fraction", "folds"}, (
        f"Consistency carries {sorted(fields)}. The fold count must travel with the fraction - a "
        f"consistency of 0.80 from 5 folds and one from 50 are read very differently, and a "
        f"consumer that receives only the fraction cannot tell them apart."
    )

    few = walkforward.directional_consistency(0.4, [0.1, 0.2, 0.3, -0.4, 0.5])
    many = walkforward.directional_consistency(0.4, [0.1, 0.2, 0.3, -0.4, 0.5] * 10)
    assert few.fraction == many.fraction == pytest.approx(0.8)
    assert (few.folds, many.folds) == (5, 50), (
        "two consistencies that are numerically identical must still be distinguishable by their "
        "fold counts - that is the entire reason the count is stored"
    )

    # The gate reads both, and the same fraction passes or fails on the count alone.
    from app.signals import sweep

    assert sweep.passes_gate(0.01, few.fraction, few.folds) is True
    assert sweep.passes_gate(0.01, many.fraction, 4) is False, (
        "a pair with four folds cleared a gate that requires five"
    )
