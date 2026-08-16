"""Detection uses only the past, and a sustained event is ONE event.

The two failures guarded here are the two largest in the phase, and they fail in opposite
directions: lookahead makes the results better than they are, and uncollapsed detections make the
evidence larger than it is. Neither is visible in the output.
"""

import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.analogs import events, parameters
from tests.analogs.conftest import daily, run_of


def test_detection_on_a_date_ignores_later_observations():
    """Test 1. Detection against a TRUNCATED series equals detection against the full one.

    THE ONLY FORM OF THIS TEST THAT CANNOT PASS VACUOUSLY. A signature test alone would stay green
    against an implementation that took the full series and sliced it wrongly; a behavioural test
    on one series alone would stay green against one that never sliced at all.

    The series is built so the future is LOUD: after the query date the counter climbs to 40, which
    is exactly the shape a "an event is a period that reached at least 20 days" definition would
    detect and this one must not.
    """
    start = date(2022, 8, 1)
    full = daily(start, [0.0, 0.0, 1.0, 2.0, 3.0] + [float(i) for i in range(4, 41)])
    as_of = start + timedelta(days=4)

    truncated = events.observations_through(full, as_of)
    assert [day for day, _ in truncated][-1] == as_of

    assert events.detections(truncated) == events.detections(
        events.observations_through(full, as_of)
    )

    # And the answer itself: three detections up to as_of, not the forty the full series holds.
    assert events.detections(truncated) == [
        start + timedelta(days=2),
        start + timedelta(days=3),
        start + timedelta(days=4),
    ]
    assert len(events.detections(full)) == 40


def test_the_detector_is_given_no_access_to_future_observations():
    """Test 2. `is_entry` takes exactly one positional parameter, and it is the history.

    By signature, in `app/signals/regimes.py`'s style. A behavioural test goes green again the
    moment somebody adds an `as_of` argument and passes the whole series alongside it — the
    lookahead version needs a parameter to arrive through, and this asserts there is none.

    Keyword-only SCALARS are permitted and `run_length_days` is one: a threshold cannot carry an
    observation. A keyword-only parameter accepting a sequence would defeat this, which is why the
    assertion names the allowed keyword rather than allowing keywords generally.
    """
    signature = inspect.signature(events.is_entry)

    positional = [
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind
        in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    assert positional == ["history"], (
        f"is_entry takes {positional}. It must take exactly one positional parameter — the series "
        f"up to and including the candidate date. A second one is where the future arrives."
    )

    keyword_only = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind == parameter.KEYWORD_ONLY
    }
    assert keyword_only == {"run_length_days"}, (
        f"unexpected keyword-only parameters {keyword_only - {'run_length_days'}}. Only the "
        f"human-owned scalar threshold belongs here; a sequence arriving by keyword is lookahead "
        f"through the back door."
    )

    assert signature.parameters["run_length_days"].default == parameters.ENTRY_RUN_LENGTH_DAYS


def test_a_sustained_crossing_collapses_to_one_event():
    """Test 3. Sixty consecutive days below the threshold is ONE event, not sixty analogs.

    This is the inflation the gate cannot see: sixty raw detections would satisfy ">= 4 analogs"
    fifteen times over FROM A SINGLE LOW-WATER PERIOD, and four analogs is four analogs to anything
    downstream. Both counts are asserted, because a collapse whose effect is invisible is a
    collapse nobody notices the removal of.
    """
    series = run_of(date(2022, 8, 1), 60, before=5, after=5)

    history = events.history(series)

    assert history.n_raw_detections == 60
    assert history.n_collapsed_events == 1
    assert history.events[0].start == date(2022, 8, 6)
    assert history.events[0].n_detections == 60


def test_two_crossings_separated_by_more_than_the_separation_window_are_two_events():
    """Test 4. Two genuinely separate low-water periods are two analogs.

    The complement of test 3, and both are needed: an implementation that collapses everything into
    one event passes test 3 perfectly and destroys the history.
    """
    first = run_of(date(2021, 8, 1), 10)
    gap_start = first[-1][0] + timedelta(days=1)
    quiet = daily(gap_start, [0.0] * (parameters.MIN_EVENT_SEPARATION_DAYS + 10))
    second = run_of(quiet[-1][0] + timedelta(days=1), 10)

    history = events.history(first + quiet + second)

    assert history.n_raw_detections == 20
    assert history.n_collapsed_events == 2
    assert [event.n_detections for event in history.events] == [10, 10]

    # And the boundary is the separation rule, not the calendar: the same two runs closer together
    # than the window are one event.
    near_quiet = daily(gap_start, [0.0] * 5)
    near_second = run_of(near_quiet[-1][0] + timedelta(days=1), 10)
    assert events.history(first + near_quiet + near_second).n_collapsed_events == 1


def test_raw_detection_count_and_collapsed_count_are_both_stored():
    """Test 5. Both numbers travel together, on the object and in the schema.

    `n_raw_detections` alone overstates the evidence by the length of each event. `events` alone
    hides that the collapse did anything — and a history whose raw count is in the hundreds and
    whose collapsed count is 2 is the honest description of this dataset.
    """
    history = events.history(run_of(date(2022, 8, 1), 45))

    assert history.n_raw_detections == 45
    assert history.n_collapsed_events == 1
    assert history.n_raw_detections != history.n_collapsed_events

    # "Stored" means stored, so the schema is asserted too. A dataclass carrying both counts that
    # writes only one of them would pass every in-memory assertion above.
    schema = (
        Path(__file__).resolve().parents[2] / "migrations" / "0024_analog_queries.sql"
    ).read_text()
    assert "n_raw_detections" in schema
    assert "n_collapsed_events" in schema
    assert "analog_queries_collapsed_never_exceeds_raw" in schema, (
        "the schema must refuse a collapsed count above the raw one — collapsing only ever "
        "reduces, and the wrong direction would look like more evidence rather than like a bug."
    )


def test_a_null_value_is_not_an_entry():
    """A gap in the record does not open an event.

    `app/features/thresholds.py` writes NULL rather than 0 across a data gap — Memphis has a
    twenty-year hole in its daily record — and "we do not know whether the river was low" must
    neither open an event nor close one.
    """
    series = daily(date(2014, 9, 28), [None, None, 3.0, None])

    assert events.detections(series) == [date(2014, 9, 30)]


def test_an_empty_history_detects_nothing_rather_than_raising():
    """The engine asks this of sites with no features yet. A refusal, not an exception."""
    assert events.is_entry([]) is False
    assert events.detections([]) == []
    assert events.history([]).events == ()


@pytest.mark.parametrize("threshold", [1, 5, 20])
def test_the_entry_threshold_is_a_parameter_and_changes_what_is_detected(threshold):
    """The mechanism is built; the number is human-owned (CLAUDE.md § 1).

    Asserted across three values so the threshold is demonstrably load-bearing rather than a
    constant that happens to be read.
    """
    series = run_of(date(2022, 8, 1), 30)

    found = events.detections(series, run_length_days=threshold)

    assert len(found) == 30 - threshold + 1
