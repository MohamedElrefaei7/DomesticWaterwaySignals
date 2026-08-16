"""The sentence carries its own denominators, and the refusal offers no estimate.

The sentence is the unit that gets quoted — into a Slack message, a README, a résumé — and
CLAUDE.md § 7 requires every number in it to be reproducible from a query. Test 20 asserts the
second half of that literally: every numeral in the rendered string must trace to a value in the
result it was rendered from.
"""

import math
import re
from datetime import date

import pytest

from app.analogs import gate, outcomes, parameters, render

CONDITION = render.Condition(
    site_label="Memphis",
    river="Mississippi",
    as_of=date(2022, 9, 6),
    change=-215_000.0,
    lookback_days=14,
    anomaly=-48_000.0,
    climatology_n_years=12,
)


def _summary(*log_returns):
    return outcomes.summarize(
        [outcomes.Outcome(date(2022, 8, 4), value, outcomes.COMPLETE) for value in log_returns]
    )


def test_the_sentence_carries_k_d_and_the_window():
    """Test 18. K, D and the window are IN THE SENTENCE, not in a caption beside it.

    "The last 5 times", "5 of 5", "within 3 weeks". A rendered claim that does not carry its own
    sample size can be pasted anywhere with the denominator left behind, and this project has an
    entire table built around what happens when a denominator goes missing.
    """
    result = gate.GateResult(
        result=gate.PASSED, n_analogs=5, n_consistent=5, n_incomplete=0, direction=1
    )
    summary = _summary(*[math.log(1.2), math.log(1.3), math.log(1.4), math.log(1.5),
                         math.log(1.6)])

    sentence = render.estimate_sentence(CONDITION, result, summary, 21)

    assert "The last 5 times" in sentence, "K is missing from the sentence"
    assert "5 of 5 directionally consistent" in sentence, "D of K is missing from the sentence"
    assert "within 3 weeks" in sentence, "the outcome window is missing from the sentence"

    # And the condition half is there with its own numbers.
    assert "Mississippi discharge at Memphis" in sentence
    assert "fallen 215,000 cfs in 14 days" in sentence
    assert "48,000 cfs below the 12-year seasonal median" in sentence


def test_the_baseline_depth_is_read_and_not_hardcoded_at_ten_years():
    """CLAUDE.md § 7's example says "the 10-year seasonal median". The real count is stated instead.

    `features.climatology_n_years` exists precisely so a baseline's depth travels with it, and on
    the real table it runs 11 to 37. Hardcoding ten while the median was computed over thirty-seven
    is a number in a quotable sentence that traces to nothing.
    """
    for years in (11, 37):
        condition = render.Condition(**{**CONDITION.__dict__, "climatology_n_years": years})
        assert f"{years}-year seasonal median" in render.condition_clause(condition)


def test_the_sentence_does_not_claim_a_seasonal_match_that_was_never_applied():
    """`parameters.SEASON_MATCH_WINDOW_DAYS` is None, so "during harvest season" is omitted.

    The clause would assert a filter this engine does not apply, in the one artifact a reader
    quotes verbatim. When a human sets that window it can appear and mean something.
    """
    assert parameters.SEASON_MATCH_WINDOW_DAYS is None

    result = gate.GateResult(
        result=gate.PASSED, n_analogs=4, n_consistent=4, n_incomplete=0, direction=1
    )
    sentence = render.estimate_sentence(CONDITION, result, _summary(0.1, 0.2, 0.3, 0.4), 21)

    assert "harvest" not in sentence.lower()
    assert "season" not in sentence.lower().replace("seasonal median", "")


def test_the_sentence_says_discharge_because_discharge_is_what_was_measured():
    """The contract's example says "stage". Stage is not published at two of the four gauges and
    this project refuses to derive it from a rating curve (migration 0004), so rendering "stage"
    would name a variable nothing measured."""
    result = gate.GateResult(
        result=gate.PASSED, n_analogs=4, n_consistent=4, n_incomplete=0, direction=1
    )
    sentence = render.estimate_sentence(CONDITION, result, _summary(0.1, 0.2, 0.3, 0.4), 21)

    assert "discharge" in sentence
    assert "stage" not in sentence


def test_a_range_spanning_zero_is_rendered_with_explicit_signs():
    """4 of 5 consistent means one analog went the other way, and the range spans zero.

    An unsigned "5%-47%" beside the word "rose" is a sentence that says a fall was a rise. Signs
    cost two characters and remove the class of error.
    """
    result = gate.GateResult(
        result=gate.PASSED, n_analogs=5, n_consistent=4, n_incomplete=0, direction=1
    )
    summary = _summary(math.log(0.95), math.log(1.2), math.log(1.3), math.log(1.4),
                       math.log(1.47))

    sentence = render.estimate_sentence(CONDITION, result, summary, 21)

    assert "-5% to +47%" in sentence
    assert "4 of 5 directionally consistent" in sentence


def test_the_refusal_sentence_names_the_counts_and_offers_no_estimate():
    """Test 19. "Insufficient history", the reason, the counts, and "No estimate offered."

    The last clause is in the string on purpose: a refusal that merely omits the number reads, in a
    UI, as a number that failed to load — and the natural response to a number that failed to load
    is to go and find it somewhere else.
    """
    result = gate.evaluate(
        [outcomes.Outcome(date(2022, 8, 4), value, outcomes.COMPLETE) for value in (0.4, 0.5)]
    )
    sentence = render.refusal_sentence(
        CONDITION, result, min_analogs=parameters.MIN_ANALOGS, since=date(1990, 1, 1)
    )

    assert sentence.startswith("Mississippi discharge at Memphis")
    assert "Insufficient history: 2 comparable events since 1990, below the 4 required." in sentence
    assert "No estimate offered." in sentence

    # No estimate anywhere. "seasonal median" is permitted and is not one: it describes the
    # condition being asked about, which was measured, rather than a rate move that was not.
    assert "%" not in sentence
    assert "median +" not in sentence and "median -" not in sentence
    assert "directionally consistent" not in sentence
    assert "barge rate" not in sentence

    with pytest.raises(ValueError, match="passing verdict"):
        render.refusal_sentence(
            render.Condition(**CONDITION.__dict__),
            gate.GateResult(gate.PASSED, 4, 4, 0, 1),
            min_analogs=4,
        )


def test_each_refusal_reason_renders_as_its_own_news():
    """Three refusals, three different facts, and only one of them is fixed by more ingest."""
    quiet = render.refusal_sentence(CONDITION, gate.no_current_event(), min_analogs=4)
    assert "not in a low-water condition" in quiet

    inconsistent = render.refusal_sentence(
        CONDITION,
        gate.evaluate(
            [
                outcomes.Outcome(date(2022, 8, 4), value, outcomes.COMPLETE)
                for value in (0.4, 0.5, -0.6, -0.7)
            ]
        ),
        min_analogs=4,
    )
    assert "moved the rate in the same direction" in inconsistent

    incomplete = render.refusal_sentence(
        CONDITION,
        gate.evaluate(
            [outcomes.Outcome(date(2022, 8, 4), None, outcomes.NO_RATE_AT_END)] * 1
            + [
                outcomes.Outcome(date(2022, 8, 4), value, outcomes.COMPLETE)
                for value in (0.4, 0.5, 0.6, 0.7)
            ]
        ),
        min_analogs=4,
    )
    assert "no measurable rate move" in incomplete


def test_an_estimate_cannot_be_rendered_from_a_refused_verdict():
    """`estimate_sentence` refuses independently of the caller's branching.

    A renderer that can produce a sentence from a refused query is a renderer somebody will call
    with one, and the caution will be lost in the second paste.
    """
    refused = gate.evaluate(
        [outcomes.Outcome(date(2022, 8, 4), v, outcomes.COMPLETE) for v in (0.4, 0.5)]
    )
    with pytest.raises(ValueError, match="refused"):
        render.estimate_sentence(CONDITION, refused, _summary(0.4, 0.5), 21)


def test_a_condition_missing_half_its_measurement_shortens_rather_than_invents():
    """No 14-day change, or no deep-enough climatology, and the clause says so."""
    no_change = render.Condition(**{**CONDITION.__dict__, "change": None})
    assert "no measured 14-day change" in render.condition_clause(no_change)

    no_baseline = render.Condition(
        **{**CONDITION.__dict__, "anomaly": None, "climatology_n_years": None}
    )
    assert "no seasonal baseline deep enough" in render.condition_clause(no_baseline)


def test_every_number_in_the_sentence_traces_to_a_query_result():
    """Test 20. Every numeral in the rendered string is derivable from the result it came from.

    CLAUDE.md § 7: "Every number that appears in the README, the UI, or the résumé must be
    reproducible from a query." This is that rule turned into an assertion — the numbers are
    extracted from the string and each one is matched against the set the result can produce, so a
    hardcoded "10-year" or a rounded constant fails here rather than in a screenshot.
    """
    result = gate.GateResult(
        result=gate.PASSED, n_analogs=5, n_consistent=5, n_incomplete=0, direction=1
    )
    summary = _summary(*[math.log(1.18), math.log(1.25), math.log(1.29), math.log(1.34),
                         math.log(1.47)])
    sentence = render.estimate_sentence(CONDITION, result, summary, 21)

    derivable = {
        abs(CONDITION.change),
        abs(CONDITION.anomaly),
        float(CONDITION.lookback_days),
        float(CONDITION.climatology_n_years),
        float(result.n_analogs),
        float(result.n_consistent),
        3.0,  # 21 days rendered as 3 weeks
        round(summary.low_percent),
        round(summary.high_percent),
        round(summary.median_percent),
    }

    found = [
        float(token.replace(",", ""))
        for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", sentence)
    ]
    assert found, "the sentence carries no numbers at all"

    untraceable = [value for value in found if abs(value) not in derivable]
    assert not untraceable, (
        f"{untraceable} appear in the sentence and trace to nothing in the result. Every number in "
        f"a quotable claim must be reproducible from a query."
    )
