"""The gate refuses, refusal is the default path, and a refused query carries no estimate.

Test 15 is the load-bearing one. It walks the WHOLE returned structure rather than checking named
fields, because the failure it guards is a number appearing somewhere nobody thought to look — a
debug payload, a field added later, a nested object. A test that checks `result.median is None` is
green the day somebody adds `result.detail.median`.
"""

import dataclasses
import math
from datetime import date

import pytest

from app.analogs import gate, outcomes, parameters

START = date(2022, 8, 4)


def complete(*log_returns):
    """Outcomes that all measured cleanly. The gate's ordinary input."""
    return [outcomes.Outcome(START, value, outcomes.COMPLETE) for value in log_returns]


def test_three_analogs_refuses_and_names_the_count():
    """Test 13. CLAUDE.md § 7's ">= 4 analogs", and the count travels with the refusal.

    Three perfectly consistent analogs is the exact case the contract calls "manufacturing
    conviction from three coincidences". Note that consistency here is 3 of 3 — 100% — so the ONLY
    thing standing between this query and a confident sentence is the analog count.
    """
    result = gate.evaluate(complete(0.4, 0.5, 0.6))

    assert not result.passed
    assert result.result == gate.INSUFFICIENT_ANALOGS
    assert result.n_analogs == 3
    assert result.n_consistent == 3
    assert result.consistency == pytest.approx(1.0)

    # And four of the same shape passes, so the refusal above is the count and nothing else.
    assert gate.evaluate(complete(0.4, 0.5, 0.6, 0.7)).passed


def test_inconsistent_direction_refuses_and_names_the_fraction():
    """Test 14. Five analogs, three up and two down — 60%, below the contract's 70%."""
    result = gate.evaluate(complete(0.4, 0.5, 0.6, -0.3, -0.2))

    assert not result.passed
    assert result.result == gate.INCONSISTENT_DIRECTION
    assert result.n_analogs == 5
    assert result.n_consistent == 3
    assert result.consistency == pytest.approx(0.6)

    # 4 of 5 is 80% and passes. The boundary is the contract's number, not a rounding.
    assert gate.evaluate(complete(0.4, 0.5, 0.6, 0.7, -0.2)).passed


def test_a_zero_move_is_not_consistent_with_anything():
    """A rate that did not move agrees with neither direction.

    EXACTLY ZERO IS THE VALUE A CARRIED-FORWARD RATE FABRICATES (`app/features/targets.py`), so a
    rule letting zeros agree with the majority would reward precisely the fabrication this project
    refuses upstream — and it would do so in the direction that makes the gate pass.
    """
    result = gate.evaluate(complete(0.4, 0.5, 0.6, 0.0, 0.0))

    assert result.n_analogs == 5
    assert result.n_consistent == 3
    assert result.result == gate.INCONSISTENT_DIRECTION


def test_a_refused_query_carries_no_numeric_estimate_anywhere():
    """Test 15. WALK THE WHOLE STRUCTURE. No median, no range, no direction.

    Decision 5, asserted structurally so a field added to `GateResult` later is covered without
    anybody remembering to extend this test. The counts are permitted by name — they are the
    evidence for the refusal, not an estimate of anything — and every other numeric leaf is a
    failure.
    """
    permitted = {"n_analogs", "n_consistent", "n_incomplete"}

    for result in (
        gate.evaluate(complete(0.4, 0.5, 0.6)),
        gate.evaluate(complete(0.4, -0.5, 0.6, -0.7, 0.1)),
        gate.no_current_event(),
        gate.evaluate([outcomes.Outcome(START, None, outcomes.NO_RATE_AT_END)]),
    ):
        assert not result.passed

        leaves = []

        def walk(value, path):
            if dataclasses.is_dataclass(value):
                for field in dataclasses.fields(value):
                    walk(getattr(value, field.name), f"{path}.{field.name}")
            elif isinstance(value, dict):
                for key, item in value.items():
                    walk(item, f"{path}[{key}]")
            elif isinstance(value, (list, tuple, set)):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                leaves.append((path, value))

        walk(result, result.result)

        unexpected = [
            (path, value)
            for path, value in leaves
            if path.rsplit(".", 1)[-1] not in permitted
        ]
        assert not unexpected, (
            f"a {result.result!r} verdict carries {unexpected}. A refused query has no median, no "
            f"range and no direction — and a value that exists is one refactor away from being "
            f"displayed."
        )

        assert result.direction is None

    # The constructor refuses it too, so the property cannot be broken by building one by hand.
    with pytest.raises(ValueError, match="carries a direction"):
        gate.GateResult(
            result=gate.INSUFFICIENT_ANALOGS,
            n_analogs=1,
            n_consistent=1,
            n_incomplete=0,
            direction=1,
        )


def test_the_gate_runs_before_outcome_statistics_are_computed(monkeypatch):
    """Test 16. `summarize` is never CALLED on a refusing query, not merely never displayed.

    Watched by replacing the function, because the property is about the order of operations rather
    than about the output. Withholding a computed median is weaker: a value that exists becomes a
    field in a debug payload, then a tooltip, and each step looks like a small convenience.
    """
    from app.analogs import engine

    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        raise AssertionError(
            "outcomes.summarize was called on a query the gate refused. The estimate must not "
            "exist at all — see app/analogs/gate.py."
        )

    monkeypatch.setattr(engine.outcomes, "summarize", spy)

    # The gate itself never reaches for it under any verdict.
    for verdict in (
        gate.evaluate(complete(0.4, 0.5, 0.6)),
        gate.evaluate(complete(0.4, -0.5, 0.6, -0.7, 0.1)),
        gate.no_current_event(),
    ):
        assert not verdict.passed
    assert calls == []

    # And the engine's source computes it only under `if gate_result.passed` — asserted on the
    # source rather than only by the spy, so the ordering is guarded even where no fixture drives
    # the engine end to end.
    import inspect

    source = inspect.getsource(engine.query)
    gate_line = source.index("gate_result = gate_module.evaluate(")
    summarize_line = source.index("outcomes.summarize(")
    assert gate_line < summarize_line, (
        "the summary is computed before the gate has decided. The gate must run first, so a "
        "refused query has nothing to withhold."
    )


def test_a_passing_query_requires_all_three_criteria():
    """Test 17. Enough analogs, consistent direction, AND every outcome measurable.

    Each criterion is removed in turn from a query that otherwise passes, so none of them can be
    the one that is quietly not being checked.
    """
    passing = complete(0.4, 0.5, 0.6, 0.7)
    assert gate.evaluate(passing).passed

    # 1. Too few.
    assert gate.evaluate(passing[:3]).result == gate.INSUFFICIENT_ANALOGS

    # 2. Direction not consistent enough: 2 up, 2 down is 50%.
    assert (
        gate.evaluate(complete(0.4, 0.5, -0.6, -0.7)).result
        == gate.INCONSISTENT_DIRECTION
    )

    # 3. One analog with no measurable outcome refuses the whole query rather than quietly
    #    measuring the subset that happened to be measurable — a subset selected by which weeks
    #    USDA published, which is winter closure deciding the evidence.
    with_gap = passing + [outcomes.Outcome(START, None, outcomes.NO_RATE_AT_END)]
    refused = gate.evaluate(with_gap)
    assert refused.result == gate.INCOMPLETE_OUTCOMES
    assert refused.n_incomplete == 1
    assert refused.n_analogs == 4


def test_no_analogs_at_all_refuses_with_zero_counts():
    """An empty history is a refusal, not an exception. The engine asks this of quiet sites."""
    result = gate.evaluate([])

    assert result.result == gate.INSUFFICIENT_ANALOGS
    assert result.n_analogs == 0
    assert result.consistency is None


def test_the_thresholds_are_the_contract_s_numbers():
    """CLAUDE.md § 7 fixes both. They live in parameters.py with that provenance beside them."""
    assert parameters.MIN_ANALOGS == 4
    assert parameters.MIN_DIRECTIONAL_CONSISTENCY == 0.70

    assert math.isclose(
        gate.evaluate(complete(0.1, 0.2, 0.3, -0.4)).consistency, 0.75
    )
    assert gate.evaluate(complete(0.1, 0.2, 0.3, -0.4)).passed
