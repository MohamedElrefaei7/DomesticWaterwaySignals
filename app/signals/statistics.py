"""Correlation, an honest sample size for overlapping windows, and Benjamini-Hochberg.

PURE FUNCTIONS, and STANDARD LIBRARY ONLY - see the last block of this docstring for why scipy was
not added.

THE TWO NUMBERS THIS MODULE EXISTS TO REFUSE
---------------------------------------------
    a p-value computed from the raw count of overlapping observations
    a p-value reported without a q-value adjusted across the grid it was found in

Both are what the obvious implementation produces, both look exactly like the correct version, and
both are wrong in the flattering direction.

OVERLAPPING FORWARD WINDOWS, AND THE EFFECTIVE SAMPLE SIZE
------------------------------------------------------------
The rate series is weekly and the targets are forward log-returns at 7, 14 and 21 days. At horizon
14, the target for week t spans weeks t..t+2 and the target for week t+1 spans t+1..t+3 - THEY
SHARE A WEEK. Consecutive observations are not independent draws; they are a moving window over the
same series, and about half of each one is the previous one.

A t-test on n such observations is answering "how surprising is this correlation among n
independent draws", and there are not n independent draws. The naive n is roughly twice the
independent n at horizon 14 and three times at horizon 21, and since the p-value falls with n, USING
THE RAW COUNT ROUGHLY HALVES EVERY p-VALUE AT HORIZON 14. Uniformly, invisibly, and in the direction
that makes the project's thesis look better.

    n_effective = n_observations / (horizon_days / 7)

The formula is deliberately crude, and stating that is part of using it: it is the standard
"non-overlapping equivalent" correction - how many disjoint windows would fit in the same span -
and it is not the exact variance inflation of an overlapping-window estimator, which depends on the
autocorrelation of the underlying series. IT ERRS TOWARD FEWER OBSERVATIONS AND THEREFORE TOWARD
LARGER p-VALUES, which is the direction to err in when the alternative is a table of seven thousand
tests. A refinement that pushed the other way would need a much better argument than this one.

BENJAMINI-HOCHBERG, NOT BONFERRONI
-----------------------------------
The grid is ~7,000 tests, so ~350 clear α = 0.05 on pure noise. Something has to account for that.

Bonferroni divides α by the number of tests, and ASSUMES THEY ARE INDEPENDENT. These are anything
but: lag +7 and lag +8 of one feature at one site are very nearly the same test, and there are 41
lags of each. Bonferroni on this grid demands p < 0.0000071, nothing whatever survives, and the
sweep becomes theatre - a procedure that runs, reports nothing, and is quietly stopped being run.

BH controls the FALSE DISCOVERY RATE: of the rows I am calling signals, what fraction are noise? On
a correlated grid that is both the answerable question and the one actually being asked.

WHY scipy WAS NOT ADDED, THOUGH THE BRIEF ALLOWED IT
------------------------------------------------------
Only one thing here is not arithmetic: the tail of a t distribution, which is a regularized
incomplete beta function. That is ~40 lines of a continued fraction (below), it is exercised
against known critical values by the test suite, and `math.lgamma` in the standard library does the
hard part.

Against that, `requirements.txt` opens by saying every version is pinned because a version resolved
at install time is a version nobody chose - and scipy is a large binary wheel, on a project whose
runtime dependency list is three packages, to obtain one function. THE TRADE IS ~40 LINES OF CODE A
TEST CAN CHECK AGAINST PUBLISHED CRITICAL VALUES, VERSUS A DEPENDENCY. It was not added, and the
commit report says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# The publication interval of the target series, in days. The rate is weekly, so a horizon of 7
# days is one published step and produces NO overlap; 14 produces one week of it, 21 two.
TARGET_PERIOD_DAYS = 7

# The floor on the effective sample size, and it is the MATHEMATICAL one rather than a judgement.
#
# A t distribution needs df = n_effective - 2 > 0. Below that there is no p-value to compute, so
# the pair is refused as `insufficient_observations` rather than assigned a number.
#
# It is deliberately NOT a statistical-taste threshold like "at least 30 observations". CLAUDE.md
# § 1 puts threshold values that define an event on the never-invent list, and while this is not
# quite one of those, a floor picked for feel would carry exactly the same false authority into
# every result that cleared it. What actually protects the output is downstream and stated: a pair
# needs five walk-forward folds and 70% directional consistency to pass the gate, and a handful of
# observations cannot produce five folds.
MIN_EFFECTIVE_OBSERVATIONS = 2.0

# Continued-fraction limits for the incomplete beta. 200 iterations is far past convergence for
# every (a, b, x) this module produces; the epsilon is a few ulp of a double.
_MAX_ITERATIONS = 200
_EPSILON = 3.0e-16
_TINY = 1.0e-300


@dataclass(frozen=True)
class Measurement:
    """A correlation and everything needed to read it honestly.

    ONE OBJECT RATHER THAN FOUR RETURN VALUES, so a caller cannot obtain the p-value without also
    obtaining the effective n it was computed from and the raw n it was corrected down from. The
    same argument seasonal.DayClimatology makes about a baseline and its year count: the number and
    the evidence for it travel together, because the code path that separates them is the one that
    reports the number alone.
    """

    statistic: float
    n_observations: int
    n_effective: float
    p_value: float


def pearson(xs, ys) -> float | None:
    """Pearson correlation, or None when it is undefined.

    None rather than 0.0 for a constant series - a zero correlation is a MEASUREMENT ("these move
    independently") and a constant series supports no measurement at all. A run-length feature that
    sat at 0 for an entire regime window is exactly this case and it is common, not exotic.
    """
    if len(xs) != len(ys):
        raise ValueError(f"paired series differ in length: {len(xs)} and {len(ys)}")
    n = len(xs)
    if n < 2:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]

    covariance = sum(a * b for a, b in zip(dx, dy))
    variance_x = sum(a * a for a in dx)
    variance_y = sum(b * b for b in dy)
    if variance_x <= 0.0 or variance_y <= 0.0:
        return None

    r = covariance / math.sqrt(variance_x * variance_y)
    # Clamp: the schema has a CHECK requiring |statistic| <= 1, and floating point can land a
    # hair outside on a perfectly correlated pair.
    return max(-1.0, min(1.0, r))


def overlap_factor(horizon_days: int, period_days: int = TARGET_PERIOD_DAYS) -> float:
    """How many published periods each forward window spans. 1.0 at horizon 7, 2.0 at 14, 3.0 at 21.

    FLOORED AT 1.0. A horizon shorter than the publication interval produces no overlap, and a
    factor below 1 would INFLATE the sample size - inventing observations, which is the failure
    this correction exists to prevent, running backwards. Migration 0023's
    `signals_effective_n_never_exceeds_raw_n` is the same guard at the schema level.
    """
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be positive, got {horizon_days}")
    if period_days <= 0:
        raise ValueError(f"period_days must be positive, got {period_days}")
    return max(1.0, horizon_days / period_days)


def effective_n(n_observations: int, horizon_days: int) -> float:
    """The non-overlapping equivalent sample size. THE p-VALUE IS COMPUTED FROM THIS."""
    if n_observations < 0:
        raise ValueError(f"n_observations must be non-negative, got {n_observations}")
    return n_observations / overlap_factor(horizon_days)


# ---------------------------------------------------------------------------------------------
# The t distribution's tail, via the regularized incomplete beta. Standard library only.
# ---------------------------------------------------------------------------------------------


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction for the incomplete beta, by the modified Lentz method.

    Converges rapidly for x < (a+1)/(a+b+2); `regularized_incomplete_beta` uses the symmetry
    relation to put every call on that side. The `_TINY` guards are Lentz's: a zero denominator
    mid-fraction is a division by zero rather than a wrong answer, so it is nudged instead.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0

    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    h = d

    for m in range(1, _MAX_ITERATIONS + 1):
        m2 = 2 * m

        # The even step.
        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + numerator / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        h *= d * c

        # The odd step.
        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + numerator / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        step = d * c
        h *= step

        if abs(step - 1.0) < _EPSILON:
            return h

    # Non-convergence is returned rather than raised: this is reached only for arguments far
    # outside anything a correlation produces, and 200 iterations is already past double precision.
    return h  # pragma: no cover - unreachable for the (a, b, x) a t distribution generates


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """`I_x(a, b)`. The only non-elementary function in this project, and it is here in full."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    front = math.exp(log_front)

    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def t_distribution_two_tailed_p(t: float, df: float) -> float:
    """`P(|T| >= |t|)` for a t distribution with `df` degrees of freedom.

    `df` IS A FLOAT, not an integer, and that is required rather than permissive: the effective
    sample size at horizon 21 is n/3 and is very rarely a whole number. Rounding it would be a
    second, smaller version of the optimism the correction exists to remove.
    """
    if df <= 0.0:
        raise ValueError(f"degrees of freedom must be positive, got {df}")
    x = df / (df + t * t)
    return regularized_incomplete_beta(df / 2.0, 0.5, x)


def p_value(statistic: float, n_effective: float) -> float | None:
    """Two-tailed p for a correlation, FROM THE EFFECTIVE SAMPLE SIZE.

    `t = r * sqrt(df / (1 - r^2))` with `df = n_effective - 2`. The whole point of the module is
    which number is passed in here - `n_effective`, never `n_observations` - so this function takes
    only the effective one and there is no parameter the raw count could arrive through.
    """
    if n_effective <= MIN_EFFECTIVE_OBSERVATIONS:
        return None

    df = n_effective - 2.0
    r = max(-1.0, min(1.0, statistic))
    if abs(r) >= 1.0:
        # A perfect correlation puts the t statistic at infinity. The limit is the honest answer
        # and it is reachable on tiny windows, where 1.0 means "two points" rather than "certain".
        return 0.0

    t = r * math.sqrt(df / (1.0 - r * r))
    return max(0.0, min(1.0, t_distribution_two_tailed_p(t, df)))


def measure(xs, ys, horizon_days: int) -> Measurement | None:
    """Correlate a paired series and return the statistic with its sample sizes and p-value.

    None when the pair cannot be measured at all - too few observations, or a constant series. The
    caller records that as a REFUSAL WITH A STATED STATUS rather than dropping the row, because an
    omitted pair is indistinguishable from a pair nobody enumerated and the count of enumerated
    pairs is the denominator (migration 0023).
    """
    statistic = pearson(xs, ys)
    if statistic is None:
        return None

    n = len(xs)
    n_eff = effective_n(n, horizon_days)
    p = p_value(statistic, n_eff)
    if p is None:
        return None

    return Measurement(
        statistic=statistic, n_observations=n, n_effective=n_eff, p_value=p
    )


# ---------------------------------------------------------------------------------------------
# Benjamini-Hochberg.
# ---------------------------------------------------------------------------------------------


def benjamini_hochberg(p_values) -> list[float]:
    """Raw p-values -> FDR-adjusted q-values, in the input's order.

    The procedure: sort ascending, scale each by `m / rank`, then sweep from the largest down
    keeping a running minimum.

    THE RUNNING MINIMUM IS THE HALF THAT GETS LEFT OUT, and leaving it out is not obviously wrong -
    `p * m / rank` alone is what the formula looks like, and it produces plausible numbers. But it
    is not monotone: a p-value can be scaled above the one ranked after it, so a row would carry a
    LARGER q than a row with a larger p. The sweep-down enforces q_i <= q_(i+1), which is what
    makes the column sortable and what the hand-computed fixture in the test suite pins - its third
    and fourth entries are equal precisely because the third was pulled down.

    Clamped to 1.0: `p * m / rank` exceeds 1 for most of a grid this size, and a "probability" of
    12.4 in a results table is the kind of number that gets quietly dropped from a write-up rather
    than explained.
    """
    m = len(p_values)
    if m == 0:
        return []
    if any(p is None for p in p_values):
        raise ValueError(
            "benjamini_hochberg received a None among its p-values. Unscannable pairs are excluded "
            "from the adjustment by the caller and recorded with a refusal status - passing them "
            "through would inflate m with tests that were never performed, which weakens every "
            "q-value on the grid for pairs that produced no evidence at all."
        )

    ascending = sorted(range(m), key=lambda i: p_values[i])
    q_values = [0.0] * m

    running_minimum = 1.0
    for rank in range(m, 0, -1):
        index = ascending[rank - 1]
        candidate = p_values[index] * m / rank
        running_minimum = min(running_minimum, candidate)
        q_values[index] = min(1.0, running_minimum)

    return q_values
