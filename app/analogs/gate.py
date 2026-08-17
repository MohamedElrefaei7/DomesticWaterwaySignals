"""The confidence gate. REFUSAL IS THE DEFAULT PATH, AND ON THIS DATASET IT IS THE EXPECTED ONE.

PURE FUNCTIONS. Nothing here opens a connection.

WHAT THIS GATE IS FOR
----------------------
CLAUDE.md § 7: ">= 4 analogs and >= 70% directional consistency, else the system says 'insufficient
history.' Manufacturing conviction from three coincidences is the failure this gate exists to
prevent."

Both numbers are the contract's, not this module's. They live in `parameters.py` with that
provenance written next to them, and lowering either is a human decision in its own commit - which
is worth saying plainly because the pressure to lower them arrives at exactly one moment: after the
gate has refused something somebody wanted.

THE REFUSAL IS NOT AN ERROR, AND IT IS NOT LOGGED AS ONE
---------------------------------------------------------
Phase 6 scanned 6,966 pairs and one passed, at LAG 0 - a contemporaneous association, with zero
passing rows at any non-zero lag in either direction (docs/phase-log.md, Phase 6). This engine
is built on top of that. Most queries against this dataset are expected to refuse, and:

    AN ANALOG ENGINE THAT FINDS CONFIDENT ANALOGS WHERE THE SWEEP FOUND NO RELATIONSHIP
    HAS A BUG, NOT A DISCOVERY.

So a refusal is an ordinary return value with a stated reason and its counts. It is not an
exception, it does not warn, and nothing downstream treats it as degraded output. `analog_queries`
records it as a row for the same reason `signals` records its nulls: a table holding only the
queries that produced an estimate makes an engine that refuses look like an engine that answers.

THE GATE RUNS BEFORE THE ESTIMATE EXISTS
-----------------------------------------
This module counts and compares. It never computes a median, a range or a direction of a refused
query, and `engine.py` calls `outcomes.summarize` only on the passing branch.

That ordering is the decision, and it is stronger than withholding: A VALUE THAT EXISTS IS ONE
REFACTOR AWAY FROM BEING DISPLAYED. Compute the median first and "don't show it on refusal" becomes
a rule in a renderer, then a field in a debug payload, then a tooltip. Never computing it means
there is nothing to leak, and `test_the_gate_runs_before_outcome_statistics_are_computed` asserts
it by watching whether `summarize` was called at all.

A ZERO MOVE IS NOT DIRECTIONALLY CONSISTENT WITH ANYTHING
----------------------------------------------------------
An analog whose rate did not move agrees with neither direction and is counted in `n_analogs`
without being counted in `n_consistent`. It dilutes consistency, which is the conservative
direction, and there is a specific reason to prefer that here: EXACTLY ZERO IS THE VALUE A
CARRIED-FORWARD RATE FABRICATES (`app/features/targets.py`), so a rule that let zeros agree with
whatever the majority was would reward precisely the fabrication this project refuses upstream.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analogs import parameters

# The gate's verdicts. These strings are the `analog_queries.gate_result` vocabulary and migration
# 0024 carries a CHECK over the same set - a closed set by definition, like `signals.status`, so a
# misspelling opens a silent sixth category that every `group by gate_result` reports as real.
PASSED = "passed"
NO_CURRENT_EVENT = "no_current_event"
INSUFFICIENT_ANALOGS = "insufficient_analogs"
INCONSISTENT_DIRECTION = "inconsistent_direction"
INCOMPLETE_OUTCOMES = "incomplete_outcomes"

RESULTS: tuple[str, ...] = (
    PASSED,
    NO_CURRENT_EVENT,
    INSUFFICIENT_ANALOGS,
    INCONSISTENT_DIRECTION,
    INCOMPLETE_OUTCOMES,
)


@dataclass(frozen=True)
class GateResult:
    """The verdict, its counts, and NOTHING THAT COULD BE READ AS AN ESTIMATE.

    There is no median here, no range, and `direction` is None unless the gate passed. The counts
    are present on every verdict because a refusal without them cannot be acted on: "too few
    events" is a fact about the dataset that more ingest fixes, and "inconsistent direction" is a
    fact about the relationship that it does not.

    `tests/analogs/test_gate.py::test_a_refused_query_carries_no_numeric_estimate_anywhere` walks
    the whole returned structure of a refused query, so a field added here later is covered without
    anybody remembering to extend the test.
    """

    result: str
    n_analogs: int
    n_consistent: int
    n_incomplete: int
    direction: int | None = None

    def __post_init__(self):
        if self.result not in RESULTS:
            raise ValueError(f"unknown gate result {self.result!r}. Known: {list(RESULTS)}")
        if self.result != PASSED and self.direction is not None:
            raise ValueError(
                f"a {self.result!r} verdict carries a direction. A refused query has no median, no "
                f"range and no direction - see the module docstring."
            )

    @property
    def passed(self) -> bool:
        return self.result == PASSED

    @property
    def consistency(self) -> float | None:
        """`n_consistent / n_analogs`, or None when there are no analogs.

        DERIVED, NEVER STORED. Migration 0024 keeps the two counts and not the fraction, for
        CLAUDE.md § 18's reason: 4 of 5 and 40 of 50 are both 80% and are not equally informative,
        and a stored fraction is a number that can drift from its own evidence.
        """
        if self.n_analogs == 0:
            return None
        return self.n_consistent / self.n_analogs


def no_current_event() -> GateResult:
    """The verdict when the query date is not in a low-water condition at all.

    A distinct result rather than `insufficient_analogs`, because it says something different: the
    engine was asked about a river that is not doing the thing. Collapsing it into "too few
    analogs" would make an ordinary Tuesday look like a data-coverage problem, and step 6 of the
    live procedure is a human checking exactly this case.
    """
    return GateResult(
        result=NO_CURRENT_EVENT, n_analogs=0, n_consistent=0, n_incomplete=0
    )


def direction_of(log_return: float) -> int:
    """+1, -1, or 0. Zero is its own answer - see the module docstring."""
    if log_return > 0:
        return 1
    if log_return < 0:
        return -1
    return 0


def evaluate(
    selected_outcomes,
    *,
    min_analogs: int = parameters.MIN_ANALOGS,
    min_consistency: float = parameters.MIN_DIRECTIONAL_CONSISTENCY,
) -> GateResult:
    """The verdict over the analogs that were selected. ALL THREE CRITERIA, IN A STATED ORDER.

    `selected_outcomes` is every `outcomes.Outcome` for the k-nearest eligible events - including
    the incomplete ones, which is why they can be counted rather than merely absent.

    The order matters and is not arbitrary:

      1. NOTHING SELECTED            -> insufficient_analogs. There is no history to speak from.
      2. ANY SELECTED ANALOG HAS NO  -> incomplete_outcomes. The evidence set is not clean, and
         MEASURABLE OUTCOME             this is the criterion CLAUDE.md § 7 states as "every
                                        analog has a complete outcome". It refuses rather than
                                        quietly measuring the subset that happened to be
                                        measurable - a subset selected by which weeks USDA
                                        published, which is winter closure deciding the evidence.
      3. TOO FEW COMPLETE ANALOGS    -> insufficient_analogs.
      4. DIRECTION NOT CONSISTENT    -> inconsistent_direction.
      5. otherwise                   -> passed.

    Steps 2 and 3 are both refusals and are deliberately distinguishable: the first says the
    history is there and could not be measured, the second says it is not there. More ingest fixes
    one of them.
    """
    selected = list(selected_outcomes)
    complete = [outcome for outcome in selected if outcome.complete]
    n_incomplete = len(selected) - len(complete)

    if not selected:
        return GateResult(
            result=INSUFFICIENT_ANALOGS, n_analogs=0, n_consistent=0, n_incomplete=0
        )

    if n_incomplete:
        return GateResult(
            result=INCOMPLETE_OUTCOMES,
            n_analogs=len(complete),
            n_consistent=0,
            n_incomplete=n_incomplete,
        )

    directions = [direction_of(outcome.log_return) for outcome in complete]
    n_up = directions.count(1)
    n_down = directions.count(-1)
    majority = 1 if n_up >= n_down else -1
    n_consistent = n_up if majority == 1 else n_down

    if len(complete) < min_analogs:
        return GateResult(
            result=INSUFFICIENT_ANALOGS,
            n_analogs=len(complete),
            n_consistent=n_consistent,
            n_incomplete=0,
        )

    if n_consistent / len(complete) < min_consistency:
        return GateResult(
            result=INCONSISTENT_DIRECTION,
            n_analogs=len(complete),
            n_consistent=n_consistent,
            n_incomplete=0,
        )

    return GateResult(
        result=PASSED,
        n_analogs=len(complete),
        n_consistent=n_consistent,
        n_incomplete=0,
        direction=majority,
    )
