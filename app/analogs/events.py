"""Event detection, using ONLY observations available on the detection date.

PURE FUNCTIONS. Nothing here opens a connection.

THE LOOKAHEAD THIS MODULE IS SHAPED TO PREVENT
-----------------------------------------------
The tempting definition of an event is:

    "a period during which `days_below_p10` reached at least 20 days"

and it is wrong in a way that produces beautiful results. That condition CANNOT BE EVALUATED UNTIL
THE PERIOD IS OVER, so every historical event defined that way is defined using its own future.
Detect analogs on it, then measure what the rate did afterwards, and the analogs were selected
partly by knowing how the river turned out - which correlates with how the market turned out. The
numbers come out strong, every step is defensible in isolation, and nothing in the output shows it.

So the primitive here is `is_entry(history)`. It is handed the series UP TO AND INCLUDING the
candidate date and nothing else, and it decides about that date. THE FUTURE IS NOT A PARAMETER, so
the lookahead version cannot be written by accident - it would have to ask for an argument that
does not exist, the same guard `app/signals/regimes.py` uses to keep the target away from the
regime split.

`tests/analogs/test_events.py` asserts this twice, and both are needed:

    by behaviour   detection on a date against a TRUNCATED series equals detection against the
                   full one. This is the only form of the test that cannot pass vacuously.
    by signature   `is_entry` takes exactly one positional parameter. A behavioural test alone
                   would go green again the moment somebody added an `as_of` argument and passed
                   the whole series.

AN EVENT'S DEPTH AND DURATION ARE OUTCOMES, NOT PART OF ITS DEFINITION. They are not returned here
and they are not columns in `analog_matches` (migration 0025). Whatever the event became is exactly
the thing that must not have selected it.

WHY A DETECTION IS EVERY DAY THE CONDITION HOLDS, AND NOT JUST THE CROSSING
---------------------------------------------------------------------------
A crossing-only detector would collapse a sustained event to one date for free, and the collapse
rule below would then be untestable - it would be a no-op that looked correct. Worse, it would be a
no-op whose absence nobody could observe, so the day somebody changed the entry condition to
something that re-triggers (a threshold on a noisy series, say), the inflation would arrive with no
guard in place.

So detection is deliberately naive - the condition holds or it does not - and `collapse` carries
the whole anti-inflation argument, visibly, with a test that goes red when it is removed.

THE COLLAPSE IS THE LARGEST INFLATION RISK IN THIS PHASE
---------------------------------------------------------
The 2022 low-water period ran from August into November. Counted raw, IT ALONE PRODUCES DOZENS OF
DETECTIONS, and CLAUDE.md § 7's ">= 4 analogs" would be satisfied four times over by ONE EVENT -
manufactured conviction from a single coincidence, which is the exact failure the gate exists to
prevent, arriving in a form the gate cannot see because four analogs is four analogs.

So detections within `MIN_EVENT_SEPARATION_DAYS` of the previous detection extend the current event
instead of opening a new one, and BOTH COUNTS ARE KEPT. A history whose raw count is in the hundreds
and whose collapsed count is 2 is the honest description of this dataset, and it is only readable
if the two numbers travel together (migration 0024 stores both, for the same reason).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.analogs import parameters


@dataclass(frozen=True)
class Event:
    """One collapsed event: when it was first detected, and how many raw detections it absorbed.

    `start` IS A DETECTION DATE, never a period boundary derived after the fact. `n_detections` is
    carried so a single sustained event cannot be mistaken for several - it is the number that says
    "this one absorbed 61 days".

    There is deliberately no `end`, no `peak` and no `duration`. Those are outcomes; an event
    described by them is an event defined using its own future (see the module docstring), and a
    field that exists is a field somebody will filter on.
    """

    start: date
    n_detections: int


@dataclass(frozen=True)
class EventHistory:
    """Every detection and every collapsed event over one series.

    Both counts, always together. `n_raw_detections` alone would overstate the evidence by the
    length of each event; `events` alone would hide that the collapse did anything at all, and a
    collapse whose effect is invisible is a collapse nobody notices the removal of.
    """

    raw_detections: tuple[date, ...]
    events: tuple[Event, ...]

    @property
    def n_raw_detections(self) -> int:
        return len(self.raw_detections)

    @property
    def n_collapsed_events(self) -> int:
        return len(self.events)


def is_entry(history, *, run_length_days: int = parameters.ENTRY_RUN_LENGTH_DAYS) -> bool:
    """Does the entry condition hold on the LAST date of `history`?

    ONE POSITIONAL PARAMETER, AND IT IS THE SERIES UP TO AND INCLUDING THE CANDIDATE DATE. There is
    no way to hand this function anything dated later, which is the whole design - see the module
    docstring. `test_the_detector_is_given_no_access_to_future_observations` asserts the positional
    signature, so adding a second series would go red before it could be used.

    `run_length_days` is keyword-only and is a SCALAR PARAMETER, not a series: it is the human-owned
    threshold from `parameters.py`, defaulted here so the value has exactly one home and passed
    explicitly by tests that need to vary it. A scalar cannot carry a future observation.

    A NULL value is not an entry. `app/features/thresholds.py` writes NULL rather than 0 across a
    data gap - Memphis has a twenty-year hole in its daily record - and "we do not know whether the
    river was low" must not open an event. Reading NULL as 0 would be a recovery that never
    happened; reading it as "still low" would be an event nobody observed.
    """
    if not history:
        return False

    _, value = history[-1]
    if value is None:
        return False
    return value >= run_length_days


def detections(dated_values, **entry_kwargs) -> list[date]:
    """Every date on which the entry condition held.

    Walks prefixes and hands each one to `is_entry`, which is the mechanical expression of decision
    1: THE FUNCTION DECIDING ABOUT A DATE NEVER SEES A LATER ONE. Slicing here rather than passing
    an index is what makes that true rather than merely intended - an index would let a future
    implementation reach past it.

    Raw and uncollapsed. `collapse` is what turns these into analogs, and the two steps are kept
    apart so the collapse is a thing that can be removed and observed to break something.
    """
    ordered = sorted(dated_values, key=lambda row: row[0])
    return [
        ordered[index][0]
        for index in range(len(ordered))
        if is_entry(ordered[: index + 1], **entry_kwargs)
    ]


def collapse(
    detection_dates,
    *,
    separation_days: int = parameters.MIN_EVENT_SEPARATION_DAYS,
) -> list[Event]:
    """Detections -> events. A detection within `separation_days` of the previous one extends.

    MEASURED FROM THE PREVIOUS DETECTION, NOT FROM THE EVENT'S START, and the difference is not
    cosmetic: anchored to the start, a continuously low winter of 200 days would break into three
    "events" 90 days apart, which is the inflation this rule exists to prevent wearing the rule's
    own clothes. Anchored to the previous detection, an unbroken run is one event however long it
    runs, and two runs are two events only when the river actually came back up in between.
    """
    ordered = sorted(detection_dates)
    if not ordered:
        return []

    window = timedelta(days=separation_days)
    starts: list[date] = [ordered[0]]
    counts: list[int] = [1]
    previous = ordered[0]

    for day in ordered[1:]:
        if day - previous > window:
            starts.append(day)
            counts.append(1)
        else:
            counts[-1] += 1
        previous = day

    return [Event(start=start, n_detections=count) for start, count in zip(starts, counts)]


def history(dated_values, *, separation_days=None, **entry_kwargs) -> EventHistory:
    """`(date, value)` pairs -> every detection and every collapsed event.

    The one call the engine makes. Returns both counts because the gate consumes the collapsed one
    and a reader needs the raw one to see what the collapse did - migration 0024 stores both for
    exactly that reason.
    """
    found = detections(dated_values, **entry_kwargs)
    if separation_days is None:
        events = collapse(found)
    else:
        events = collapse(found, separation_days=separation_days)
    return EventHistory(raw_detections=tuple(found), events=tuple(events))


def observations_through(dated_values, as_of: date) -> list[tuple]:
    """The series up to AND INCLUDING `as_of`, sorted.

    The engine's single point of truncation. Every series the engine reads passes through here
    before anything looks at it - the feature series, and the z-score population `similarity.py`
    standardizes against. A z-score fitted on the full record would leak the future into the
    distances quietly, in a number nobody reads as a prediction.
    """
    return sorted(
        (row for row in dated_values if row[0] <= as_of),
        key=lambda row: row[0],
    )
