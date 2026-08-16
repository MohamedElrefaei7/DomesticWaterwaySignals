"""What the rate did after each analog. ONE WINDOW, FIXED BEFORE ANY OUTCOME IS LOOKED AT.

PURE FUNCTIONS. Nothing here opens a connection.

THE WINDOW IS A PARAMETER AND NOT A SEARCH
-------------------------------------------
Measuring the outcome at 7, 14 and 21 days and reporting whichever moved most IS THE SWEEP'S
MULTIPLE-COMPARISONS PROBLEM RELOCATED SOMEWHERE WITH NO q-VALUES TO CATCH IT. Three windows is
three tests, the strongest of three is not a 21-day result, and unlike `signals` - which records
every combination it scanned precisely so the denominator survives - nothing here would record the
two windows that were discarded. The reader would see one number and no way to know it was a
maximum.

So `measure` takes ONE `window_days`, it is an int, and passing a sequence raises rather than
quietly iterating. `parameters.OUTCOME_WINDOW_DAYS` is the single seed and it was chosen from
CLAUDE.md § 7's example claim ("within 3 weeks") before any outcome in this dataset was computed.

If more than one window is ever wanted, EVERY window is reported for EVERY query, always, with none
selected - which is a different function from this one and would need its own argument for why.

LOG-RETURNS, AND THE IMPLEMENTATION IS BORROWED RATHER THAN REWRITTEN
---------------------------------------------------------------------
`app/features/targets.forward_log_return` already encodes this project's decision that returns are
log and that a non-positive rate is refused rather than passed to `log`. CLAUDE.md § 17 forbids a
second implementation of a rule that has one - a parallel copy here would be the thing that drifts,
and it would drift in a column of plausible small numbers.

A MISSING ENDPOINT IS AN INCOMPLETE OUTCOME, AND IT IS COUNTED RATHER THAN DROPPED
----------------------------------------------------------------------------------
USDA publishes no rate for a closed river - 774 of 8,260 nearby records, concentrated December to
March (migration 0017). An analog whose window starts or ends on such a week has no measurable
outcome, and there are three ways to get that wrong, all of which read as tidying:

    carry the previous week's rate      produces a return of EXACTLY ZERO, the most ordinary value
                                        this column can hold, landing preferentially in winter
    walk back to the last published     silently measures a different window per analog, longer
                                        wherever the river was closed
    drop the analog silently            shortens the evidence with nothing to show it happened

So the outcome is NULL, the analog is excluded from the gate's count, AND IT IS STILL RETURNED with
a stated reason. "The fourth analog had no measurable outcome" and "there was no fourth analog" are
different facts about the history, and only the first one tells you more ingest would help.

THE RATE ON A DATE IS THE LAST ONE PUBLISHED ON OR BEFORE IT, NEVER THE NEAREST
-------------------------------------------------------------------------------
CLAUDE.md § 17, and the reason is that `nearest` admits lookahead of a few days: a rate published
after the window's end is nearer to it than one published five days before, so a price nobody could
have seen sets the endpoint. It appears in no schema, it makes every outcome slightly better than
it was, and it survives review because nobody reads a date match as a modelling error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.analogs import parameters
from app.features.targets import forward_log_return

# Why an outcome could not be measured. Each one is a different fact about the dataset, and
# collapsing them into a single "incomplete" would hide which of them more ingest would fix.
COMPLETE = "complete"
NO_RATE_AT_START = "no_rate_at_start"
NO_RATE_AT_END = "no_rate_at_end"
SERIES_DOES_NOT_REACH = "series_does_not_reach"

REASONS: tuple[str, ...] = (COMPLETE, NO_RATE_AT_START, NO_RATE_AT_END, SERIES_DOES_NOT_REACH)


@dataclass(frozen=True)
class Outcome:
    """One analog's rate move, or the stated reason there is not one.

    `log_return` is None exactly when `reason` is not COMPLETE, and both are always present. An
    Outcome carrying None with no reason would be indistinguishable from a bug, which is the same
    argument `features.climatology_n_years` makes about a NULL anomaly: the first response to an
    unexplained NULL is to delete the check that produced it.
    """

    event_start: date
    log_return: float | None
    reason: str

    @property
    def complete(self) -> bool:
        return self.reason == COMPLETE


def rate_at(rate_series, day: date):
    """The last rate published ON OR BEFORE `day`, and whether such a week exists at all.

    Returns `(week_ending, value)` or None when the series has no week on or before `day`. The
    value may itself be None - a week USDA published with no rate - and THAT IS NOT THE SAME
    CONDITION as having no week at all, so this function does not collapse them. The caller
    distinguishes NO_RATE_AT_START from SERIES_DOES_NOT_REACH on exactly that difference.

    Deliberately does NOT walk backwards past a published-but-null week. Walking back would measure
    a longer window than the one stated, quietly, and by a different amount per analog - which is
    the window-shopping this module refuses, arriving through a helper.
    """
    eligible = [row for row in rate_series if row[0] <= day]
    if not eligible:
        return None
    return max(eligible, key=lambda row: row[0])


def measure(
    event_start: date,
    rate_series,
    window_days: int = parameters.OUTCOME_WINDOW_DAYS,
) -> Outcome:
    """The rate's forward log-return over ONE window from `event_start`.

    `window_days` IS AN INT AND A SEQUENCE RAISES. That is the decision-4 guard in executable form:
    the shape of a multi-window search is a sequence arriving here, and it fails loudly at the
    boundary rather than producing a number whose provenance is a maximum.
    """
    if isinstance(window_days, bool) or not isinstance(window_days, int):
        raise TypeError(
            f"window_days must be a single int, got {type(window_days).__name__}. Measuring at "
            f"several windows and reporting the strongest is the sweep's multiple-comparisons "
            f"problem with no q-value to catch it - and nothing here would record the windows that "
            f"were discarded. The window is fixed in app/analogs/parameters.py before any outcome "
            f"is looked at."
        )
    if window_days <= 0:
        raise ValueError(f"window_days must be positive, got {window_days}")

    end_day = event_start + timedelta(days=window_days)

    latest_published = max((row[0] for row in rate_series), default=None)
    if latest_published is None or latest_published < end_day:
        # The window reaches past the end of the published series. NOT a missing rate: this is an
        # analog whose outcome has not happened yet (or a query whose as_of is too recent), and it
        # is the condition the engine's own eligibility rule is built to exclude before we get
        # here. Recorded distinctly so a run where it fires says so.
        return Outcome(event_start=event_start, log_return=None, reason=SERIES_DOES_NOT_REACH)

    start_row = rate_at(rate_series, event_start)
    if start_row is None or start_row[1] is None:
        return Outcome(event_start=event_start, log_return=None, reason=NO_RATE_AT_START)

    end_row = rate_at(rate_series, end_day)
    if end_row is None or end_row[1] is None:
        return Outcome(event_start=event_start, log_return=None, reason=NO_RATE_AT_END)

    value = forward_log_return(start_row[1], end_row[1])
    if value is None:
        # `forward_log_return` refuses a non-positive endpoint rather than handing it to log().
        return Outcome(event_start=event_start, log_return=None, reason=NO_RATE_AT_END)

    return Outcome(event_start=event_start, log_return=value, reason=COMPLETE)


def measure_all(event_starts, rate_series, window_days=parameters.OUTCOME_WINDOW_DAYS):
    """`measure` over several events, in the order given. Incomplete ones are returned, not dropped."""
    return [measure(start, rate_series, window_days) for start in event_starts]


@dataclass(frozen=True)
class Summary:
    """The aggregate a passing query reports. NEVER BUILT FOR A REFUSED ONE.

    This object existing at all is the estimate. `gate.py` runs first and `engine.py` calls
    `summarize` only on the passing branch, so a refused query has no median to withhold - which is
    a stronger property than withholding one, because a value that exists is one refactor away from
    being displayed.
    """

    n: int
    median_log_return: float
    low_log_return: float
    high_log_return: float

    @staticmethod
    def _as_percent(log_return: float) -> float:
        """A log-return rendered as the percent move it describes. Presentation only.

        The arithmetic is kept as logs everywhere else - see the module docstring and
        `app/features/targets.py` - and converted once, here, at the edge where a human reads it.
        A percent stored or averaged anywhere upstream would reintroduce the asymmetry that made
        this project choose logs.
        """
        import math

        return (math.exp(log_return) - 1.0) * 100.0

    @property
    def median_percent(self) -> float:
        return self._as_percent(self.median_log_return)

    @property
    def low_percent(self) -> float:
        return self._as_percent(self.low_log_return)

    @property
    def high_percent(self) -> float:
        return self._as_percent(self.high_log_return)


def summarize(complete_outcomes) -> Summary:
    """Median and range over analogs THAT ALL HAVE OUTCOMES. Raises on an incomplete one.

    Raising rather than filtering: filtering here would mean the count behind the median could
    differ from the count the gate approved, silently, and the sentence reports the gate's count.
    The caller has already excluded the incomplete ones - if one reaches this function, the two
    counts have diverged and that is a defect rather than a case to handle.
    """
    import statistics

    values = []
    for outcome in complete_outcomes:
        if not outcome.complete or outcome.log_return is None:
            raise ValueError(
                f"summarize received an incomplete outcome for {outcome.event_start} "
                f"({outcome.reason}). The gate's count and the median's count must be the same "
                f"number, and filtering here is how they stop being."
            )
        values.append(outcome.log_return)

    if not values:
        raise ValueError(
            "summarize received no outcomes. A median of nothing is the shape of an estimate "
            "produced for a query that should have refused."
        )

    return Summary(
        n=len(values),
        median_log_return=statistics.median(values),
        low_log_return=min(values),
        high_log_return=max(values),
    )
