"""The sentence. EVERY NUMBER IN IT COMES FROM A QUERY, AND IT CARRIES ITS OWN DENOMINATORS.

PURE FUNCTIONS. Nothing here opens a connection, and nothing here computes an estimate - it is
handed one or it is handed a refusal.

CLAUDE.md § 7 fixes the shape:

    Mississippi stage at Memphis has fallen 4.2 ft in 14 days and is now 1.8 ft below the 10-year
    seasonal median. The last 5 times stage fell this far this fast during harvest season, the
    Cairo-Memphis barge rate rose 18-47% within 3 weeks - median +29%, 5 of 5 directionally
    correct.

K, D AND THE WINDOW APPEAR IN THE SENTENCE ITSELF
--------------------------------------------------
"The last 5 times", "5 of 5", "within 3 weeks". Not in a caption, not in a tooltip, not in a
sibling field - IN THE SENTENCE, because the sentence is the unit that gets quoted. A rendered
claim that does not carry its own sample size can be pasted into a Slack message, a README or a
résumé with the denominator left behind, and this project has an entire table (`signals`) built
around what happens when a denominator goes missing.

THREE DEVIATIONS FROM THE EXAMPLE, EACH BECAUSE THE EXAMPLE WOULD BE A FALSE CLAIM HERE
----------------------------------------------------------------------------------------
  "stage"                 -> DISCHARGE. Stage is not published at two of the four gauges and this
                             project refuses to derive it from a rating curve (migration 0004).
                             Rendering "stage" while measuring discharge names the wrong variable.

  "during harvest season" -> OMITTED. `parameters.SEASON_MATCH_WINDOW_DAYS` is None: THIS ENGINE
                             APPLIES NO SEASONAL RESTRICTION. The clause would assert a filter
                             that was never applied, in the one artifact a reader quotes verbatim.
                             When a human sets that window, the clause appears and means something.

  "the 10-year median"    -> THE ACTUAL YEAR COUNT, read from `features.climatology_n_years`. That
                             column exists precisely so a baseline's depth travels with it, and
                             hardcoding ten while the median was computed over thirty-seven is a
                             number in a quotable sentence that traces to nothing.

"directionally consistent" rather than "directionally correct": the analogs agreed with each other,
which is what was measured. Whether they were correct is a claim about a prediction nobody made.

THE REFUSAL IS A SENTENCE TOO, AND IT NAMES THE COUNTS
-------------------------------------------------------
    Insufficient history: 2 comparable events since 1990, below the 4 required. No estimate offered.

"No estimate offered" is in the string on purpose. A refusal that merely omits the number reads, in
a UI, as a number that failed to load - and the natural response to a number that failed to load is
to go and find it somewhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.analogs import gate as gate_module

# The segment CLAUDE.md § 7's output contract names, and the one `app/features/targets.py` builds.
RATE_LABEL = "Cairo-Memphis"

# What is actually measured. See the module docstring - the contract's example says "stage" and
# this project does not have stage at two of its four gauges.
SERIES_LABEL = "discharge"


@dataclass(frozen=True)
class Condition:
    """The query condition, as described in the first half of the sentence.

    Built by `engine.py` from the database and handed here. It carries `climatology_n_years`
    because the sentence states the baseline's depth, and a baseline whose depth is not stated is
    the "10-year seasonal median" that turns out to be three years deep.

    `change` and `anomaly` are in the series' own published units (cfs for discharge) and are NOT
    converted here. CLAUDE.md § 16: published units are stored exactly as published, and a
    conversion in a renderer is a conversion nobody can find later.
    """

    site_label: str
    river: str
    as_of: date
    change: float | None
    lookback_days: int
    anomaly: float | None
    climatology_n_years: int | None


def site_label(name: str) -> str:
    """'Mississippi River at Memphis, TN' -> 'Memphis'. Display only.

    Falls back to the full name whenever the shape does not match, rather than guessing: a gauge
    named something else entirely should read awkwardly rather than read as a place it is not.
    """
    label = name.split(" at ")[-1] if " at " in name else name
    return label.split(",")[0].strip() or name


def _signed_percent(value: float) -> str:
    """'+29%' / '-14%'. The sign is always explicit.

    An unsigned '29%' beside the word "rose" is fine until the day the range spans zero, and then
    it is a sentence that says a fall was a rise. Signs cost two characters and remove the class.
    """
    return f"{value:+.0f}%"


def _window_phrase(window_days: int) -> str:
    """'3 weeks' where the window divides evenly, '10 days' where it does not.

    Weeks because the rate series is weekly and the contract's example is phrased in weeks; days
    whenever rounding would misdescribe the window that was actually measured.
    """
    if window_days % 7 == 0:
        weeks = window_days // 7
        return f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
    return f"{window_days} day" if window_days == 1 else f"{window_days} days"


def _magnitude(value: float) -> str:
    """A discharge figure with thousands separators and no false precision."""
    return f"{value:,.0f}"


def condition_clause(condition: Condition) -> str:
    """The first half: what the river is doing, and how unusual that is.

    Each half degrades independently. A condition with no measurable 14-day change still has an
    anomaly worth stating, and an anomaly refused by the eight-year climatology guard
    (`features.anomaly` NULL, `climatology_n_years` present) still leaves the change. NEITHER HALF
    IS INVENTED WHEN THE OTHER IS MISSING - the sentence gets shorter, which is what a shorter
    measurement should look like.
    """
    parts = [f"{condition.river} {SERIES_LABEL} at {condition.site_label}"]

    if condition.change is None:
        parts.append(f"has no measured {condition.lookback_days}-day change")
    elif condition.change < 0:
        parts.append(
            f"has fallen {_magnitude(abs(condition.change))} cfs "
            f"in {condition.lookback_days} days"
        )
    else:
        parts.append(
            f"has risen {_magnitude(condition.change)} cfs in {condition.lookback_days} days"
        )

    if condition.anomaly is not None and condition.climatology_n_years is not None:
        side = "below" if condition.anomaly < 0 else "above"
        parts.append(
            f"and is now {_magnitude(abs(condition.anomaly))} cfs {side} the "
            f"{condition.climatology_n_years}-year seasonal median"
        )
    else:
        # NULL anomaly with no year count is the climatology guard refusing a shallow baseline
        # (CLAUDE.md § 17). Saying so is more useful than an absent clause, because an absent
        # clause reads as a sentence somebody forgot to finish.
        parts.append("and has no seasonal baseline deep enough to compare against")

    return " ".join(parts) + "."


def estimate_sentence(condition: Condition, result, summary, window_days: int) -> str:
    """The full claim, for a query the gate PASSED. Raises if handed a refusal.

    Raising rather than rendering something cautious: a renderer that can produce a sentence from a
    refused query is a renderer somebody will call with one, and the caution will be lost in the
    second paste. `engine.py` chooses the branch; this function refuses to be the fallback.
    """
    if not result.passed:
        raise ValueError(
            f"estimate_sentence called on a {result.result!r} verdict. A refused query has no "
            f"estimate to render - call refusal_sentence. See app/analogs/gate.py."
        )

    verb = "rose" if result.direction == 1 else "fell"
    return (
        f"{condition_clause(condition)} "
        f"The last {result.n_analogs} times {SERIES_LABEL} moved like this, "
        f"the {RATE_LABEL} barge rate {verb}, "
        f"{_signed_percent(summary.low_percent)} to {_signed_percent(summary.high_percent)} "
        f"within {_window_phrase(window_days)} - "
        f"median {_signed_percent(summary.median_percent)}, "
        f"{result.n_consistent} of {result.n_analogs} directionally consistent."
    )


def refusal_sentence(condition: Condition, result, *, min_analogs: int, since=None) -> str:
    """"Insufficient history", the reason, and the counts that produced it.

    The reason is rendered rather than summarised because the three refusals are not the same news:
    too few events is a coverage problem that more history fixes, an unmeasurable outcome is a
    published-rate problem, and an inconsistent direction is a statement about the relationship
    that no amount of ingest will improve.
    """
    if result.passed:
        raise ValueError("refusal_sentence called on a passing verdict")

    horizon = f" since {since:%Y}" if since is not None else ""

    if result.result == gate_module.NO_CURRENT_EVENT:
        detail = (
            f"{condition.site_label} is not in a low-water condition on "
            f"{condition.as_of:%Y-%m-%d}, so there is no condition to find analogs for"
        )
    elif result.result == gate_module.INCOMPLETE_OUTCOMES:
        detail = (
            f"{result.n_incomplete} of {result.n_analogs + result.n_incomplete} comparable events"
            f"{horizon} have no measurable rate move over the outcome window"
        )
    elif result.result == gate_module.INSUFFICIENT_ANALOGS:
        detail = (
            f"{result.n_analogs} comparable event"
            f"{'' if result.n_analogs == 1 else 's'}{horizon}, "
            f"below the {min_analogs} required"
        )
    else:
        detail = (
            f"{result.n_consistent} of {result.n_analogs} comparable events{horizon} moved the "
            f"rate in the same direction, below the threshold required"
        )

    return f"{condition_clause(condition)} Insufficient history: {detail}. No estimate offered."


def sentence(condition: Condition, result, summary, *, window_days: int, min_analogs: int,
             since=None) -> str:
    """The one entry point. Chooses the branch from the verdict, never from the summary's presence.

    Branching on `summary is not None` would make a rendering bug into an estimate: hand this a
    refusal that somehow arrived with a summary attached and the caution disappears silently. The
    verdict decides, and `estimate_sentence` refuses a refusal independently.
    """
    if result.passed:
        return estimate_sentence(condition, result, summary, window_days)
    return refusal_sentence(condition, result, min_analogs=min_analogs, since=since)
