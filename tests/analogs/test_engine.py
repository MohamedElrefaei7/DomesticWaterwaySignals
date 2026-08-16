"""The engine end to end, against a real database.

THE MOST SEDUCTIVE BUG IN THIS PHASE IS AN EVENT MATCHING ITSELF. It reports a distance of exactly
zero, so it lands at rank 1 with the strongest possible similarity, and it has a real measured
outcome — so the sentence says "the last 5 times conditions looked like this" about a set that
contains this time. The output looks BETTER, not broken, which is why tests 21 and 22 are
integration tests reading real rows rather than assertions about a helper.
"""

from datetime import date, timedelta

import pytest

from app.analogs import engine, events, parameters
from tests.analogs.conftest import MEMPHIS

# Five low-water events, one per named year, far enough apart that the separation rule keeps them
# distinct. 2022 and 2023 are the labelled events CONTEXT.md records; the earlier three exist so
# there is a history to be an analog OF.
EVENT_YEARS = (2016, 2018, 2020, 2022, 2023)
EVENT_MONTH_DAY = (8, 15)
EVENT_LENGTH_DAYS = 45

HISTORY_START = date(2016, 1, 1)
HISTORY_END = date(2023, 12, 31)


def _counter_on(day: date) -> float:
    """`days_below_p10` on one day: 0 unless the day falls inside one of the seeded events."""
    for year in EVENT_YEARS:
        start = date(year, *EVENT_MONTH_DAY)
        if start <= day < start + timedelta(days=EVENT_LENGTH_DAYS):
            return float((day - start).days + 1)
    return 0.0


def seed_memphis(seeder):
    """Eight years of five features and a weekly rate series. Deterministic, no randomness.

    The rate rises through every event by the same shape, so the analogs AGREE — which is what
    makes a passing gate reachable in this fixture. That is deliberate: a fixture where the gate
    can never pass would let a mutation that breaks the passing branch go unnoticed, and it would
    make "it refused" indistinguishable from "it is broken".
    """
    days = [
        HISTORY_START + timedelta(days=i)
        for i in range((HISTORY_END - HISTORY_START).days + 1)
    ]

    seeder.features(
        MEMPHIS,
        "days_below_p10",
        [(day, _counter_on(day), None, None) for day in days],
    )
    seeder.features(
        MEMPHIS,
        "days_below_p05",
        [(day, max(0.0, _counter_on(day) - 15.0), None, None) for day in days],
    )
    seeder.features(
        MEMPHIS,
        "days_below_p20",
        [(day, _counter_on(day) + (5.0 if _counter_on(day) else 0.0), None, None)
         for day in days],
    )

    # `discharge_min` IS `discharge_mean` at Memphis (Phase 5 finding 3). Seeded identically on
    # purpose: the fixture reproduces the duplication the real site has, so the unweighted metric
    # is exercised against the case where it counts one variable twice.
    for name in ("discharge_mean", "discharge_min"):
        seeder.features(
            MEMPHIS,
            name,
            [
                (
                    day,
                    400_000.0 - 3_000.0 * _counter_on(day),
                    -2_000.0 * _counter_on(day),
                    12,
                )
                for day in days
            ],
        )

    # Weekly rates, running past the end of the feature history so an outcome window at the last
    # event still closes. Rising with the counter, so every event's forward move is positive.
    weeks = []
    week = date(2016, 1, 7)
    while week <= HISTORY_END + timedelta(days=120):
        weeks.append((week, 300.0 + 25.0 * _counter_on(week)))
        week += timedelta(days=7)
    seeder.rates(weeks)


@pytest.fixture
def memphis(seed_analogs):
    seed_memphis(seed_analogs)
    return seed_analogs


def test_eligible_events_excludes_the_query_event_and_anything_overlapping_it():
    """The exclusion rule as arithmetic, before it is asserted end to end.

    A unit assertion beside the two integration ones because this is where the rule is legible: an
    analog qualifies only when its whole outcome window closed before the query's own event began.
    """
    query_start = date(2022, 8, 15)
    as_of = date(2022, 9, 20)

    candidates = [
        events.Event(start=date(2020, 8, 15), n_detections=45),  # long past: eligible
        events.Event(start=date(2022, 8, 1), n_detections=3),  # window runs into the query event
        events.Event(start=query_start, n_detections=37),  # the query event itself
        events.Event(start=date(2022, 9, 10), n_detections=1),  # window reaches past as_of
    ]

    kept = engine.eligible_events(
        candidates, query_start=query_start, as_of=as_of, window_days=21
    )

    assert [event.start for event in kept] == [date(2020, 8, 15)]


@pytest.mark.integration
def test_the_query_event_is_excluded_from_its_own_analogs(memphis, migrated_db):
    """Test 21. Asking during the 2022 event must not return the 2022 event as an analog.

    Asserted on the rows the engine actually wrote, not on a helper's return value: the failure is
    a self-match reaching `analog_matches` at rank 1 with distance 0.0, and the only place that is
    observable is the far side of the write.
    """
    as_of = date(2022, 9, 20)

    result = engine.query(migrated_db, as_of=as_of, site_id=MEMPHIS)

    assert result.matches, "the fixture should provide analogs to exclude from"
    starts = [match.event_start for match in result.matches]

    assert date(2022, 8, 15) not in starts, (
        "the query's own event came back as its own analog — a distance of zero, at rank 1, with a "
        "real outcome. The sentence would then say 'the last N times' about a set containing this "
        "time."
    )
    assert all(start < as_of for start in starts)
    assert min(match.distance for match in result.matches) > 0.0

    rows = migrated_db.execute(
        "SELECT event_start FROM analog_matches WHERE query_id = %s ORDER BY rank",
        (result.query_id,),
    ).fetchall()
    assert [row[0] for row in rows] == starts


@pytest.mark.integration
def test_overlapping_windows_are_excluded_from_analogs(memphis, migrated_db):
    """Test 22. Every analog's outcome window closed BEFORE the query event began.

    The subtler half of test 21: an analog whose window merely overlaps the query condition is
    scored partly on the same weeks the query is asking about, which is the lookahead that does not
    announce itself as a self-match.
    """
    as_of = date(2022, 9, 20)

    result = engine.query(migrated_db, as_of=as_of, site_id=MEMPHIS)
    query_start = date(2022, 8, 15)
    window = timedelta(days=parameters.OUTCOME_WINDOW_DAYS)

    for match in result.matches:
        assert match.event_start + window < query_start, (
            f"analog {match.event_start} has an outcome window reaching {match.event_start + window}"
            f", which is on or after the query event's start {query_start}."
        )
        assert match.event_start + window <= as_of


@pytest.mark.integration
def test_the_strongest_relevant_signal_row_is_recorded_with_its_q_value(memphis, migrated_db):
    """Test 23. The sweep's verdict rides on every query, so an output cannot be read without it.

    An analog engine reporting confident analogs where the sweep found no relationship has a bug,
    and this column pair is what makes that contradiction visible in the data rather than in an
    argument.
    """
    memphis.signal(MEMPHIS, parameters.ENTRY_FEATURE, 0.0446)

    result = engine.query(migrated_db, as_of=date(2022, 9, 20), site_id=MEMPHIS)

    assert result.signal_q_value == pytest.approx(0.0446)
    assert result.signal_run_id is not None

    row = migrated_db.execute(
        "SELECT signal_run_id, signal_q_value FROM analog_queries WHERE query_id = %s",
        (result.query_id,),
    ).fetchone()
    assert row[0] == result.signal_run_id
    assert float(row[1]) == pytest.approx(0.0446)


@pytest.mark.integration
def test_an_unscanned_pair_records_null_rather_than_a_default(memphis, migrated_db):
    """No sweep row is a THIRD state, and it is not "no relationship".

    It means the pair was never scanned — because it was skipped as degenerate, or because no sweep
    has run. Defaulting it to 1.0 would look like a measured null result forever.
    """
    result = engine.query(migrated_db, as_of=date(2022, 9, 20), site_id=MEMPHIS)

    assert result.signal_q_value is None
    assert result.signal_run_id is None


def test_parameters_hash_changes_when_a_parameter_changes():
    """Test 24. Two outputs under different settings are never mistaken for two observations.

    Every hashed parameter is varied in turn, so none of them can be the one silently left out of
    the hash — which is the failure mode of a hand-maintained list, and the reason the list is
    hand-maintained is that a module scan would change the hash for reasons nobody could trace.
    """
    baseline = parameters.parameters_hash()

    changes = {
        "ENTRY_FEATURE": "days_below_p20",
        "ENTRY_RUN_LENGTH_DAYS": 20,
        "MIN_EVENT_SEPARATION_DAYS": 45,
        "SIMILARITY_FEATURES": ("discharge_mean",),
        "SIMILARITY_WEIGHTS": (1.0, 1.0, 1.0, 1.0, 1.0),
        "SIMILARITY_CUTOFF": 2.0,
        "K_NEAREST": 4,
        "SEASON_MATCH_WINDOW_DAYS": 30,
        "OUTCOME_WINDOW_DAYS": 14,
        "CONDITION_LOOKBACK_DAYS": 7,
        "MIN_ANALOGS": 3,
        "MIN_DIRECTIONAL_CONSISTENCY": 0.5,
    }
    assert set(changes) == set(parameters.HASHED_PARAMETERS), (
        "a hashed parameter is not exercised here. Every one of them must be shown to move the "
        "hash, or it is in the list without being in the hash."
    )

    for name, value in changes.items():
        assert parameters.parameters_hash({name: value}) != baseline, (
            f"changing {name} did not change the parameters hash. Two results produced under "
            f"different settings would then carry the same fingerprint."
        )

    # Stable across processes: a salted `hash()` would differ between runs of identical parameters.
    assert parameters.parameters_hash() == baseline

    with pytest.raises(ValueError, match="not hashed parameters"):
        parameters.parameters_hash({"NOT_A_PARAMETER": 1})


@pytest.mark.integration
def test_every_match_row_references_a_query(memphis, migrated_db):
    """Test 25. The foreign key, asserted against the real one.

    A match with no query is an analog with no question, no as-of date, no parameters hash and no
    sweep verdict — a row that reads as evidence and cannot be placed.
    """
    result = engine.query(migrated_db, as_of=date(2022, 9, 20), site_id=MEMPHIS)

    orphans = migrated_db.execute(
        "SELECT count(*) FROM analog_matches m"
        " LEFT JOIN analog_queries q ON q.query_id = m.query_id"
        " WHERE q.query_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0

    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO analog_matches (query_id, rank, event_start, distance)"
            " VALUES (%s, 1, %s, 0.0)",
            (result.query_id + 10_000, date(2020, 8, 15)),
        )
    migrated_db.rollback()


@pytest.mark.integration
def test_a_query_outside_any_low_water_condition_refuses_cleanly(memphis, migrated_db):
    """Step 6 of the live procedure. An ordinary Tuesday is not a coverage problem.

    `no_current_event` rather than `insufficient_analogs`, and no exception: the engine was asked
    about a river that is not doing the thing, and it must not return distant analogs for a
    condition that is not happening.
    """
    result = engine.query(migrated_db, as_of=date(2021, 5, 12), site_id=MEMPHIS)

    assert result.gate.result == "no_current_event"
    assert result.summary is None
    assert result.matches == ()
    assert "not in a low-water condition" in result.sentence

    row = migrated_db.execute(
        "SELECT gate_result FROM analog_queries WHERE query_id = %s", (result.query_id,)
    ).fetchone()
    assert row[0] == "no_current_event", "a refusal must be recorded, not merely returned"


@pytest.mark.integration
def test_a_refused_query_is_written_with_its_counts(memphis, migrated_db):
    """The denominator, one layer up from `signals`.

    A table holding only the queries that produced an estimate would make an engine that refuses
    ninety-nine times in a hundred look like an engine that answers.
    """
    engine.query(migrated_db, as_of=date(2016, 9, 1), site_id=MEMPHIS)

    row = migrated_db.execute(
        "SELECT gate_result, n_raw_detections, n_collapsed_events, n_analogs"
        "  FROM analog_queries ORDER BY query_id DESC LIMIT 1"
    ).fetchone()

    # The first seeded event has nothing before it, so there is nothing to be an analog of.
    assert row[0] == "insufficient_analogs"
    assert row[1] > 0, "detections happened and must be recorded even though the gate refused"
    assert row[2] == 1
    assert row[3] == 0


@pytest.mark.integration
def test_the_database_refuses_a_passing_row_on_three_analogs(memphis, migrated_db):
    """Migration 0024's CHECK, asserted from the database side.

    Not "the gate never builds such a row" — that tests the gate. The point is that a script, a
    manual INSERT, or a future module cannot write one either, which is the same argument
    tests/signals/ makes about p-without-q.
    """
    with pytest.raises(Exception, match="analog_queries_passing_needs_enough_analogs"):
        migrated_db.execute(
            "INSERT INTO analog_queries"
            " (as_of_date, site_id, feature_vector, k, outcome_window_days, gate_result,"
            "  n_raw_detections, n_collapsed_events, n_analogs, n_consistent, git_sha, git_dirty,"
            "  parameters_hash)"
            " VALUES (%s, %s, ARRAY['days_below_p10'], 10, 21, 'passed', 9, 3, 3, 3, %s, false,"
            "         'abc')",
            (date(2022, 9, 20), MEMPHIS, "f" * 40),
        )
    migrated_db.rollback()

    with pytest.raises(
        Exception, match="analog_queries_passing_needs_directional_consistency"
    ):
        migrated_db.execute(
            "INSERT INTO analog_queries"
            " (as_of_date, site_id, feature_vector, k, outcome_window_days, gate_result,"
            "  n_raw_detections, n_collapsed_events, n_analogs, n_consistent, git_sha, git_dirty,"
            "  parameters_hash)"
            " VALUES (%s, %s, ARRAY['days_below_p10'], 10, 21, 'passed', 30, 6, 5, 2, %s, false,"
            "         'abc')",
            (date(2022, 9, 20), MEMPHIS, "f" * 40),
        )
    migrated_db.rollback()
