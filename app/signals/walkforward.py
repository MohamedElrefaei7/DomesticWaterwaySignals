"""Walk-forward splits with a leakage gap, and directional consistency that carries its fold count.

PURE FUNCTIONS. Nothing here opens a connection and nothing here computes a correlation - it hands
back windows, and the caller correlates inside them. That separation is what lets the gap be
asserted against constructed dates rather than against a sweep's output.

THE GAP, AND WHY IT IS ASSERTED AT THE DATA LEVEL RATHER THAN CHECKED IN CONFIGURATION
---------------------------------------------------------------------------------------
A target is a FORWARD log-return: the value at week t is computed from the rate at t + horizon. So
a training observation does not stop at its own date - IT REACHES `horizon_days` INTO THE FUTURE.
If the test window begins within that reach, a training row's target is computed from a rate that
is also a test observation, and the evaluation is contaminated. It is contaminated in the direction
that flatters: the model is being scored partly on data it was fitted on.

The obvious guard is `assert splitter.gap == horizon_days`. IT PASSES WHILE THE SPLITTER IS OFF BY
ONE, because it checks the number the splitter was configured with rather than the dates it
produced - which is CLAUDE.md § 2's theme 2 exactly, and this project has already shipped ten
scheduler tests that asserted configuration while the behaviour they described did not hold.

So the real guard is `leaking_training_dates`, which takes the FOLDS THE SPLITTER ACTUALLY
RETURNED and looks for any training date whose forward window contains a test date. It must be
empty. `tests/signals/test_walkforward.py` runs it over a DAILY date grid, where an off-by-one is
reachable; on the weekly rate grid alone the boundary falls between observations and a one-day
error would be invisible.

THE ARITHMETIC OF THE GAP, WRITTEN OUT, BECAUSE THE OFF-BY-ONE LIVES HERE
--------------------------------------------------------------------------
`gap_days` is the number of days EXCLUDED between the last training observation and the first test
one - not the difference between their dates. A training observation at `d` is admitted only when

    d < test_start - gap_days          (STRICTLY less than)

so with `gap_days = horizon_days` the last admitted `d` satisfies `d + horizon_days < test_start`:
its forward window ends before the test window opens, and touches nothing inside it.

Set the gap to `horizon_days - 1` and the last admitted training observation's forward window lands
EXACTLY ON `test_start`. One day, one observation, and the training target is then a function of the
first test week's rate. That is the mutation the test suite watches.

DIRECTIONAL CONSISTENCY NEVER TRAVELS WITHOUT ITS FOLD COUNT
--------------------------------------------------------------
4 of 5 folds and 40 of 50 are both 80%, and they are not equally informative. `Consistency` carries
both fields and there is no constructor that produces one without the other - the same discipline
`seasonal.DayClimatology` applies to a baseline and its year count, and for the same reason: the
code path that separates a number from its evidence is the path that reports the number alone.

This is the quantity CLAUDE.md § 7's confidence gate consumes (≥70%), computed here rather than in
Phase 7's analog engine so that the sweep's gate and the output contract's gate read one number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# The minimum number of walk-forward folds. A pair that cannot produce this many is reported as
# `insufficient_folds` and NOT SCANNED - recorded as a row with a stated status, never dropped.
#
# Five because directional consistency is a fraction of folds and the gate wants ≥70%: with four
# folds the only achievable values are 0, 25, 50, 75 and 100%, so the gate would be testing "at
# least 3 of 4" while claiming to test 70%. Five is the smallest count at which the threshold means
# roughly what it says.
MIN_FOLDS = 5

# The minimum training observations behind any fold.
#
# 26 - half a year of a weekly series. IT IS A STATED MINIMUM AND IT IS NOT A KNOB. Lowering it
# makes more pairs scannable, and the pairs it makes scannable are exactly the short, sparse ones
# most likely to produce a large correlation by chance; raising it after seeing the results would
# be selecting a training length that suited the answer. If it changes, it changes in its own
# commit with a reason, and every run before and after is a different experiment - which is what
# `signal_runs.git_sha` exists to make visible.
MIN_TRAIN_OBSERVATIONS = 26


@dataclass(frozen=True)
class Fold:
    """One walk-forward split: an expanding training window, a gap, and a test block.

    `gap_days` is stored rather than recomputed so a caller can read what the splitter applied.
    It is NOT the evidence that the gap worked - `leaking_training_dates` is. A fold carrying
    `gap_days = 21` whose windows overlap is precisely the failure this pair of facts separates.
    """

    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    gap_days: int
    n_train: int
    n_test: int


@dataclass(frozen=True)
class Consistency:
    """A directional-consistency fraction and the number of folds behind it. Never one alone."""

    fraction: float
    folds: int


def forward_window_end(observation: date, horizon_days: int) -> date:
    """The last date a target at `observation` reads. THE REACH OF A TRAINING ROW."""
    return observation + timedelta(days=horizon_days)


def splits(
    dates,
    *,
    horizon_days: int,
    n_folds: int = MIN_FOLDS,
    min_train: int = MIN_TRAIN_OBSERVATIONS,
) -> list[Fold]:
    """Expanding-window walk-forward folds over `dates`, gapped by `horizon_days`.

    Returns an EMPTY LIST when the series cannot support `n_folds` - the caller records
    `insufficient_folds` rather than quietly evaluating on three. A silently-shortened fold count
    is the failure mode this returns empty to prevent: three folds all agreeing is 100% directional
    consistency, and it would clear a gate written for five.

    The first test block starts at the earliest observation whose GAPPED training window already
    holds `min_train` observations. Computed by walking forward rather than by subtracting an
    assumed number of observations per week, because the feature series is daily, the rate series is
    weekly, and both have real gaps in them - any arithmetic shortcut here silently assumes a
    regular grid that this project's data does not have.
    """
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be positive, got {horizon_days}")
    if n_folds < 1:
        raise ValueError(f"n_folds must be at least 1, got {n_folds}")

    ordered = sorted(set(dates))
    gap_days = horizon_days

    first_test = None
    for index, candidate in enumerate(ordered):
        cutoff = candidate - timedelta(days=gap_days)
        if sum(1 for day in ordered if day < cutoff) >= min_train:
            first_test = index
            break

    if first_test is None:
        return []

    testable = len(ordered) - first_test
    if testable < n_folds:
        return []

    block = testable // n_folds

    folds: list[Fold] = []
    for k in range(n_folds):
        low = first_test + k * block
        # The last block absorbs the remainder, so no observation is silently discarded off the end
        # of the series. Dropping them would shorten the most recent fold - the one most likely to
        # be read as "and it still works today".
        high = len(ordered) if k == n_folds - 1 else first_test + (k + 1) * block

        test_dates = ordered[low:high]
        test_start = test_dates[0]

        # THE GAP, and the strict `<` is the whole thing. See the module docstring.
        cutoff = test_start - timedelta(days=gap_days)
        train_dates = [day for day in ordered if day < cutoff]
        if not train_dates:
            return []

        folds.append(
            Fold(
                index=k,
                train_start=train_dates[0],
                train_end=train_dates[-1],
                test_start=test_start,
                test_end=test_dates[-1],
                gap_days=gap_days,
                n_train=len(train_dates),
                n_test=len(test_dates),
            )
        )

    return folds


def leaking_training_dates(fold: Fold, dates, horizon_days: int) -> list[date]:
    """Training dates in `fold` whose forward window contains a test date. MUST BE EMPTY.

    THE DATA-LEVEL ASSERTION decision 4 requires, as a function rather than as a line inside a
    test, so the sweep can run it too. It reconstructs the training and test sets from the fold's
    own boundaries and the real date series - it does not trust `fold.gap_days`, which is the number
    under suspicion.
    """
    training = [day for day in dates if fold.train_start <= day <= fold.train_end]
    testing = {day for day in dates if fold.test_start <= day <= fold.test_end}

    return [
        day
        for day in training
        if any(day < test <= forward_window_end(day, horizon_days) for test in testing)
    ]


def assert_gap_clean(folds, horizon_days: int) -> None:
    """Raise unless every fold's training window ends clear of its test window. RUNTIME GUARD.

    `fold.train_end + horizon_days < fold.test_start` is EXACTLY equivalent to
    `leaking_training_dates` returning empty - `train_end` is the largest training date and
    `test_start` the smallest test date, so if the largest reach clears the earliest test
    observation, every smaller one does too. It is the O(1) form of the same statement, which is
    what makes it affordable on every pair of a seven-thousand-cell grid.

    The exhaustive version stays the one the test suite uses. A guard that is cheap enough to run
    in production and a guard that is thorough enough to trust are allowed to be two functions, as
    long as the second is what decides whether the first is right - and the equivalence above is
    the argument, stated so it can be checked rather than assumed.
    """
    for fold in folds:
        if forward_window_end(fold.train_end, horizon_days) >= fold.test_start:
            raise ValueError(
                f"fold {fold.index} leaks: its last training observation ({fold.train_end}) has a "
                f"{horizon_days}-day forward window reaching {forward_window_end(fold.train_end, horizon_days)}, "
                f"which is on or after the first test observation ({fold.test_start}). The "
                f"training target is then computed from a rate inside the test window and the "
                f"evaluation is contaminated in the flattering direction."
            )


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def directional_consistency(full_sample_statistic, fold_statistics) -> Consistency | None:
    """The fraction of folds whose sign matches the full-sample sign, WITH the fold count.

    None - not zero - when there is nothing to measure: no usable folds, or a full-sample statistic
    of exactly zero, which has no sign for a fold to agree with. Zero would say "no fold agreed",
    which is a measurement, and this is its absence.

    A fold whose own statistic is exactly zero counts as NOT MATCHING rather than being dropped. It
    is a fold that produced no direction, and excluding it would raise the fraction by shrinking the
    denominator - which is the same denominator problem this whole phase is arranged around, at the
    scale of one pair.
    """
    if full_sample_statistic is None:
        return None

    usable = [s for s in fold_statistics if s is not None]
    if not usable:
        return None

    reference = _sign(full_sample_statistic)
    if reference == 0:
        return None

    matching = sum(1 for s in usable if _sign(s) == reference)
    return Consistency(fraction=matching / len(usable), folds=len(usable))
