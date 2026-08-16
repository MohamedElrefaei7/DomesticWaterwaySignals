"""One window, fixed in advance, and a missing endpoint is counted rather than filled in.

Test 12 is the decision-4 guard. The failure it prevents — measure at 7, 14 and 21 days and report
whichever moved most — is the sweep's multiple-comparisons problem relocated somewhere with no
q-values to catch it, and unlike the sweep, nothing here would record the windows that were
discarded.
"""

import ast
import inspect
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.analogs import outcomes, parameters
from tests.analogs.conftest import weekly


def test_outcome_is_a_forward_log_return_over_the_stated_window():
    """Test 10. Hand-computed: ln(2) over exactly 21 days.

    Rates chosen so the expected value is a number that can be checked without running the code —
    a doubling is +0.6931, and it is the same magnitude as the halving that undoes it, which is the
    whole reason `app/features/targets.py` chose logs over percent change.
    """
    start = date(2022, 8, 4)
    series = weekly(start, [100.0, 120.0, 150.0, 200.0, 260.0])

    outcome = outcomes.measure(start, series, 21)

    assert outcome.complete
    assert outcome.reason == outcomes.COMPLETE
    assert outcome.log_return == pytest.approx(math.log(2.0))

    # And the endpoint really is the last rate published ON OR BEFORE start+21, not the nearest.
    # Nudging the query two days later must pick the SAME published week, never the one after it.
    nudged = outcomes.measure(start + timedelta(days=2), series, 21)
    assert nudged.log_return == pytest.approx(math.log(200.0 / 100.0))


def test_the_rate_on_a_date_is_the_last_published_on_or_before_it():
    """CLAUDE.md § 17: last-on-or-before, never nearest.

    `nearest` admits lookahead of a day or two — a rate published after the window's end is nearer
    to it than one five days before, so a price nobody could have seen sets the endpoint. It
    appears in no schema and it survives review, because nobody reads a date match as a modelling
    error.
    """
    series = weekly(date(2022, 8, 4), [100.0, 900.0])

    # Six days after the first week: the second week is one day away and must NOT be chosen.
    assert outcomes.rate_at(series, date(2022, 8, 10))[1] == 100.0
    assert outcomes.rate_at(series, date(2022, 8, 11))[1] == 900.0
    assert outcomes.rate_at(series, date(2022, 8, 3)) is None


def test_an_incomplete_outcome_window_is_excluded_and_counted():
    """Test 11. A window with no published endpoint is NULL with a stated reason, not a zero.

    Three wrong versions all read as tidying: carrying the previous rate forward (produces a return
    of EXACTLY ZERO — the most ordinary value this column can hold, landing preferentially in
    winter closure), walking back to the last published week (silently measures a longer window,
    by a different amount per analog), and dropping the analog (shortens the evidence with nothing
    to show it happened).
    """
    start = date(2022, 12, 1)

    # A week USDA published with no rate: the river was closed. 774 of 8,260 nearby records.
    closed_end = weekly(start, [100.0, 110.0, 120.0, None, 500.0])
    outcome = outcomes.measure(start, closed_end, 21)
    assert not outcome.complete
    assert outcome.reason == outcomes.NO_RATE_AT_END
    assert outcome.log_return is None, "a missing endpoint must not become a number"

    closed_start = weekly(start, [None, 110.0, 120.0, 130.0, 500.0])
    assert outcomes.measure(start, closed_start, 21).reason == outcomes.NO_RATE_AT_START

    # And the series simply not reaching that far is a THIRD condition — an outcome that has not
    # happened yet, which is different news from one the source declined to publish.
    short = weekly(start, [100.0, 110.0])
    assert outcomes.measure(start, short, 21).reason == outcomes.SERIES_DOES_NOT_REACH

    # Counted, not dropped: measure_all returns one row per event whatever happened.
    starts = [start, start + timedelta(days=7)]
    measured = outcomes.measure_all(starts, closed_end, 21)
    assert len(measured) == len(starts)
    assert sum(1 for o in measured if not o.complete) >= 1


def test_outcomes_are_not_computed_at_multiple_windows_and_selected():
    """Test 12. The window is ONE int. A sequence raises, and no plural parameter exists.

    Both halves are needed. The signature check alone would go green against a module that grew a
    `measure_several_windows` helper beside `measure`; the runtime check alone would go green
    against one that took a list somewhere else and called `measure` in a loop, keeping the max.
    """
    series = weekly(date(2022, 8, 4), [100.0, 150.0, 200.0, 300.0, 400.0])

    with pytest.raises(TypeError, match="single int"):
        outcomes.measure(date(2022, 8, 4), series, [7, 14, 21])

    signature = inspect.signature(outcomes.measure)
    assert "window_days" in signature.parameters
    assert "windows" not in signature.parameters
    assert signature.parameters["window_days"].default == parameters.OUTCOME_WINDOW_DAYS

    # No identifier anywhere in the module is plural-window-shaped or maximum-shaped: the failure
    # is somebody adding a selector, and no behavioural test can be written against a function
    # nobody has written yet. Same structural guard as similarity.py's.
    tree = ast.parse(Path(outcomes.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    offenders = {
        name
        for name in names
        if name.lower() in {"windows", "window_list", "best", "strongest", "argmax"}
    }
    assert not offenders, (
        f"outcomes.py contains {sorted(offenders)}. Reporting the strongest of several windows is "
        f"a maximum presented as a measurement, and nothing here would record the windows that "
        f"were discarded."
    )


def test_summarize_reports_median_and_range_over_complete_outcomes():
    """The aggregate a PASSING query reports. Hand-computed."""
    start = date(2022, 8, 4)
    made = [
        outcomes.Outcome(start, math.log(1.10), outcomes.COMPLETE),
        outcomes.Outcome(start, math.log(1.30), outcomes.COMPLETE),
        outcomes.Outcome(start, math.log(1.50), outcomes.COMPLETE),
    ]

    summary = outcomes.summarize(made)

    assert summary.n == 3
    assert summary.median_log_return == pytest.approx(math.log(1.30))
    assert summary.median_percent == pytest.approx(30.0)
    assert summary.low_percent == pytest.approx(10.0)
    assert summary.high_percent == pytest.approx(50.0)


def test_summarize_refuses_an_incomplete_outcome_rather_than_filtering_it():
    """The gate's count and the median's count must be the same number.

    Filtering here is how they stop being: the sentence reports the gate's K while the median was
    computed over fewer, and nothing in the output would show the two had diverged.
    """
    start = date(2022, 8, 4)
    with pytest.raises(ValueError, match="incomplete outcome"):
        outcomes.summarize(
            [
                outcomes.Outcome(start, math.log(1.1), outcomes.COMPLETE),
                outcomes.Outcome(start, None, outcomes.NO_RATE_AT_END),
            ]
        )

    with pytest.raises(ValueError, match="no outcomes"):
        outcomes.summarize([])
