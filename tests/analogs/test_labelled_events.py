"""The 2022 and 2023 events, as validation rather than as a demo.

THESE TESTS DO NOT ASSERT THAT THE GATE PASSES, and that is the point of them. Phase 6 scanned
6,966 pairs and one passed, at lag 0. If a test here demanded a confident answer, the only way to
keep it green would be to loosen the gate — and the gate is CLAUDE.md § 7's, not this phase's.

So each test asserts a BEHAVIOUR: that the event is detected in time, that the analogs are honest,
and that whatever the gate returned is what the data supports. A pass and a refusal are both
allowed outcomes; an unexplained one is not.
"""

from datetime import date, timedelta

import pytest

from app.analogs import engine, gate, parameters
from tests.analogs.conftest import MEMPHIS
from tests.analogs.test_engine import seed_memphis

# The dates CONTEXT.md's Phase 5 tables record the rate beginning to climb hard. Detection must
# happen ON OR BEFORE these — an engine that notices a low-water event after the market already
# moved has nothing to offer, whatever its analogs say.
RATE_CLIMB_2022 = date(2022, 8, 30)
RATE_CLIMB_2023 = date(2023, 8, 29)


@pytest.fixture
def memphis(seed_analogs):
    seed_memphis(seed_analogs)
    return seed_analogs


@pytest.mark.integration
@pytest.mark.parametrize(
    "climb_date", [RATE_CLIMB_2022, RATE_CLIMB_2023], ids=["2022", "2023"]
)
def test_the_event_is_detected_on_or_before_the_rate_climb(memphis, migrated_db, climb_date):
    """Tests 26 and 27. The condition is visible before the market moved, not after.

    Asserted through the engine's own detection path — `events.is_entry` over the series truncated
    at the query date — rather than by reading the fixture back, so a detector that only works when
    handed the full history fails here.
    """
    result = engine.query(migrated_db, as_of=climb_date, site_id=MEMPHIS)

    assert result.gate.result != gate.NO_CURRENT_EVENT, (
        f"no low-water condition was detected at {climb_date}, which is the week CONTEXT.md "
        f"records the rate beginning to climb. An engine that notices the event after the market "
        f"has nothing to offer."
    )
    assert result.n_collapsed_events >= 1
    assert result.n_raw_detections >= result.n_collapsed_events


@pytest.mark.integration
@pytest.mark.parametrize(
    "as_of", [date(2022, 9, 20), date(2023, 9, 19)], ids=["2022", "2023"]
)
def test_the_gate_result_matches_what_the_data_supports_in_both_years(
    memphis, migrated_db, as_of
):
    """Test 28. WHATEVER THE GATE RETURNED, IT IS CONSISTENT WITH THE ROWS BEHIND IT.

    Every branch is checked against the evidence rather than against a desired answer:

        passed                 -> >= MIN_ANALOGS analogs, >= 70% consistent, and a summary exists
        insufficient_analogs   -> fewer analogs than the minimum, and NO summary
        inconsistent_direction -> enough analogs, consistency genuinely below the threshold
        incomplete_outcomes    -> at least one analog with no measurable rate move

    A test demanding `passed` would force somebody to loosen the gate to keep it green, which is
    the pressure CLAUDE.md § 18's last bullet describes and this phase inherits.
    """
    result = engine.query(migrated_db, as_of=as_of, site_id=MEMPHIS)
    verdict = result.gate

    assert verdict.result in gate.RESULTS

    if verdict.passed:
        assert verdict.n_analogs >= parameters.MIN_ANALOGS
        assert verdict.consistency >= parameters.MIN_DIRECTIONAL_CONSISTENCY
        assert result.summary is not None
        assert result.summary.n == verdict.n_analogs, (
            "the median was computed over a different number of analogs than the gate approved"
        )
        assert "directionally consistent" in result.sentence
    else:
        assert result.summary is None, (
            "a refused query carries a summary. The estimate must not exist at all — see "
            "app/analogs/gate.py."
        )
        assert "No estimate offered." in result.sentence
        assert "%" not in result.sentence

        if verdict.result == gate.INSUFFICIENT_ANALOGS:
            assert verdict.n_analogs < parameters.MIN_ANALOGS
        elif verdict.result == gate.INCONSISTENT_DIRECTION:
            assert verdict.n_analogs >= parameters.MIN_ANALOGS
            assert verdict.consistency < parameters.MIN_DIRECTIONAL_CONSISTENCY
        elif verdict.result == gate.INCOMPLETE_OUTCOMES:
            assert verdict.n_incomplete >= 1

    # Whatever it decided, it was recorded — with the counts that produced it.
    row = migrated_db.execute(
        "SELECT gate_result, n_analogs, n_consistent FROM analog_queries WHERE query_id = %s",
        (result.query_id,),
    ).fetchone()
    assert row == (verdict.result, verdict.n_analogs, verdict.n_consistent)


@pytest.mark.integration
def test_the_2022_query_draws_its_analogs_only_from_earlier_events(memphis, migrated_db):
    """The 2023 event may never be an analog for a 2022 question, and neither may 2022 itself.

    The ordering constraint stated plainly, because it is the one a reader of the output would
    assume without checking: every analog is an event whose outcome had already happened when the
    question was asked.
    """
    as_of = date(2022, 9, 20)
    result = engine.query(migrated_db, as_of=as_of, site_id=MEMPHIS)

    for match in result.matches:
        assert match.event_start.year < 2022, (
            f"analog {match.event_start} is not earlier than the 2022 event being asked about"
        )
        assert (
            match.event_start + timedelta(days=parameters.OUTCOME_WINDOW_DAYS) <= as_of
        )


@pytest.mark.integration
def test_a_2023_query_may_use_2022_as_an_analog(memphis, migrated_db):
    """The complement: an implementation that excluded everything would pass every test above.

    2022's outcome window closed long before the 2023 event began, so it is exactly the kind of
    analog this engine exists to find — and if the exclusion rule were written as "same year" or as
    a blanket cutoff, this is the test that would notice.
    """
    result = engine.query(migrated_db, as_of=date(2023, 9, 19), site_id=MEMPHIS)

    starts = [match.event_start for match in result.matches]
    assert date(2022, 8, 15) in starts
