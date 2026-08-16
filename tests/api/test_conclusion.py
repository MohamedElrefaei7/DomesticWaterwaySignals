"""`/api/conclusion` — the engine's answer, and the cache that must not cross-serve it.

THE STUBS HERE ARE REAL OBJECTS, NOT MOCKS. Every fixture below builds an actual
`engine.AnalogResult` from an actual `gate.GateResult`, `outcomes.Summary` and `render.Condition`,
so `AnalogResult.sentence` runs the REAL renderer. That matters: test 14 asserts the sentence
carries its own denominators, and against a mocked string it would be asserting the test's own
literal.

What IS stubbed is the database read - `engine.query` itself - because the engine's behaviour has
its own suite (tests/analogs/) and this one is about what happens to its answer on the way out.
"""

from datetime import date, timedelta

import pytest

from app.analogs import engine, gate as gate_module, outcomes, parameters, render
from tests.api.conftest import FakeConn, MEMPHIS, make_client

AS_OF = date(2022, 10, 11)

# The sweep's own row, in the column order `dependencies.RUN_SUMMARY_SQL` selects. The two counts
# at the end are the ones that must reach every conclusion body: Phase 6 scanned 6,966 pairs and
# ONE passed, and those two numbers are a different claim from either one alone.
RUN_ROW = (
    1,                       # run_id
    None,                    # started_at
    None,                    # finished_at
    6966,                    # grid_size
    -21,                     # lag_min
    21,                      # lag_max
    [7, 14, 21],             # horizons
    ["onset", "recovery", "all"],
    None,                    # feature_filter
    "f" * 40,                # git_sha
    False,                   # git_dirty
    None,                    # seed
    6966,                    # scanned_pairs
    1,                       # passing_pairs
)


def condition(as_of=AS_OF) -> render.Condition:
    """The 2022 Memphis condition, in the shape `engine._condition` builds from the database."""
    return render.Condition(
        site_label="Memphis",
        river="Mississippi",
        as_of=as_of,
        change=-16_000.0,
        lookback_days=parameters.CONDITION_LOOKBACK_DAYS,
        anomaly=-83_967.0,
        climatology_n_years=12,
    )


def passing_result(as_of=AS_OF) -> engine.AnalogResult:
    """The 2022 result as the instance produced it: 4 analogs, 3 consistent, median +7%.

    The log-returns are the real ones from CONTEXT.md's `analog_matches` breakdown, minus the 2022
    analog, so `median_percent` and the range come out at the recorded values rather than at
    numbers invented here.
    """
    summary = outcomes.Summary(
        n=4,
        median_log_return=0.0710,     # ~ +7%
        low_log_return=-0.6509,       # -48%
        high_log_return=0.16645,      # +18%
    )
    return engine.AnalogResult(
        as_of=as_of,
        site_id=MEMPHIS,
        condition=condition(as_of),
        gate=gate_module.GateResult(
            result=gate_module.PASSED, n_analogs=4, n_consistent=3, n_incomplete=0, direction=1
        ),
        summary=summary,
        matches=(
            engine.MatchSummary(rank=1, event_start=date(2020, 10, 9), distance=14.718),
            engine.MatchSummary(rank=2, event_start=date(2016, 11, 24), distance=15.006),
            engine.MatchSummary(rank=3, event_start=date(2017, 9, 25), distance=15.084),
            engine.MatchSummary(rank=4, event_start=date(2015, 10, 14), distance=15.401),
        ),
        n_raw_detections=77,
        n_collapsed_events=5,
        signal_run_id=1,
        signal_q_value=0.0446,
        parameters_hash="45600c6d05c0aaaa",
    )


def refused_result(reason=gate_module.INSUFFICIENT_ANALOGS, as_of=AS_OF) -> engine.AnalogResult:
    """A refusal, with `summary=None` because `outcomes.summarize` was never called."""
    return engine.AnalogResult(
        as_of=as_of,
        site_id=MEMPHIS,
        condition=condition(as_of),
        gate=gate_module.GateResult(
            result=reason, n_analogs=2, n_consistent=1, n_incomplete=0
        ),
        summary=None,
        matches=(
            engine.MatchSummary(rank=1, event_start=date(2020, 10, 9), distance=14.718),
            engine.MatchSummary(rank=2, event_start=date(2016, 11, 24), distance=15.006),
        ),
        n_raw_detections=12,
        n_collapsed_events=2,
        signal_run_id=1,
        signal_q_value=0.0446,
        parameters_hash="45600c6d05c0aaaa",
    )


def quiet_result(as_of=AS_OF) -> engine.AnalogResult:
    """A river that is not in a low-water condition. Measured on the instance at 2022-09-06."""
    return engine.AnalogResult(
        as_of=as_of,
        site_id=MEMPHIS,
        condition=condition(as_of),
        gate=gate_module.no_current_event(),
        summary=None,
        matches=(),
        n_raw_detections=0,
        n_collapsed_events=0,
        signal_run_id=1,
        signal_q_value=0.0446,
        parameters_hash="45600c6d05c0aaaa",
    )


def stub_engine(monkeypatch, result_for):
    """Replace `engine.query` and RECORD HOW IT WAS CALLED.

    Returns the call log, so a test can assert not only what came back but what was asked - which
    is how `persist=False` is checked. A stub that swallowed its arguments would let the API
    silently go back to writing a row on every request.
    """
    calls = []

    def fake_query(conn, *, as_of, site_id, persist=True):
        calls.append({"as_of": as_of, "site_id": site_id, "persist": persist})
        return result_for(as_of)

    monkeypatch.setattr(engine, "query", fake_query)
    return calls


def get(as_of=AS_OF, site_id=MEMPHIS, conn=None):
    client = make_client(conn=conn if conn is not None else FakeConn(run_summary_row=RUN_ROW))
    return client.get(f"/api/conclusion?site_id={site_id}&as_of={as_of.isoformat()}")


# ---------------------------------------------------------------------------------------------
# Tests 14-17.
# ---------------------------------------------------------------------------------------------


def test_a_passing_gate_renders_sentence_analogs_consistent_and_window(monkeypatch):
    """Test 14. The claim and every denominator behind it, in one body."""
    stub_engine(monkeypatch, lambda as_of: passing_result(as_of))

    body = get().json()

    assert body["gate"] == "passed"
    assert body["analogs"] == 4
    assert body["consistent"] == 3
    assert body["window_days"] == parameters.OUTCOME_WINDOW_DAYS
    assert body["median_pct"] == pytest.approx(7.36, abs=0.5)
    low, high = body["range_pct"]
    assert low == pytest.approx(-48.0, abs=1.0)
    assert high == pytest.approx(18.1, abs=1.0)
    assert len(body["matches"]) == 4

    # The window is READ FROM `parameters`, not written down in the route. A copy in the API layer
    # would be a second definition of a modelling decision fixed before any outcome was inspected.
    assert body["sentence"].endswith("3 of 4 directionally consistent.")


def test_the_sentence_and_its_denominators_are_in_the_same_response(monkeypatch):
    """Test 16. The prose carries K, D and the window; so do the fields, in the SAME body.

    Both halves, because they fail differently. A response that split the sentence into one
    endpoint and the counts into another would let a client render the claim without the evidence -
    and the sentence is the unit that gets quoted (CLAUDE.md § 19).
    """
    stub_engine(monkeypatch, lambda as_of: passing_result(as_of))

    body = get().json()
    sentence = body["sentence"]

    assert "The last 4 times" in sentence
    assert "3 of 4 directionally consistent" in sentence
    assert "within 3 weeks" in sentence

    assert {"analogs", "consistent", "window_days"} <= body.keys()
    assert body["analogs"] == 4 and body["consistent"] == 3


def test_no_current_event_is_distinct_from_refused(monkeypatch):
    """Test 15. A quiet river is not a coverage problem.

    Collapsing this into `refused` would make an ordinary Tuesday read as "we lack the history",
    which sends somebody to go and buy data for a question nobody asked.
    """
    stub_engine(monkeypatch, lambda as_of: quiet_result(as_of))

    body = get().json()

    assert body["gate"] == "no_current_event"
    assert body["gate"] != "refused"
    assert "reason" not in body, (
        "`no_current_event` is its own gate value; giving it a refusal reason re-merges the two"
    )
    assert "Insufficient history" in body["sentence"]
    assert "not in a low-water condition" in body["sentence"]


def test_conclusion_calls_the_engine_and_does_not_reimplement_it(monkeypatch):
    """Test 17, decision 10. The behavioural half of the no-second-implementation guard.

    The structural half greps `app/api/` for the gate's thresholds
    (test_contract.py::test_api_modules_contain_no_gate_logic). Neither alone is enough: a grep
    passes over a route that reimplements the gate with the number spelled differently, and a
    behavioural test passes over a route that calls the engine and then overrides its verdict.

    So this asserts the engine was CALLED, with the caller's own parameters, and that the verdict
    in the body is the one the engine returned - here a refusal on counts that would pass no gate
    anybody could write from these numbers alone.
    """
    calls = stub_engine(monkeypatch, lambda as_of: refused_result(as_of=as_of))

    body = get(as_of=date(2023, 9, 19)).json()

    assert len(calls) == 1
    assert calls[0]["as_of"] == date(2023, 9, 19)
    assert calls[0]["site_id"] == MEMPHIS
    assert body["gate"] == "refused"
    assert body["reason"] == gate_module.INSUFFICIENT_ANALOGS
    assert body["analogs"] == 2
    assert body["required"] == parameters.MIN_ANALOGS


def test_the_conclusion_route_never_persists_a_query_row(monkeypatch):
    """READ-ONLY IS TWO PROPERTIES, AND THIS IS THE ONE THE ROUTE TABLE CANNOT SHOW.

    Not numbered in the brief, and it is the sharpest test in this file. `engine.query`'s default
    is `persist=True`: it INSERTs an `analog_queries` row and COMMITS. Every HTTP request would
    write, on an endpoint declared GET, through a role the live procedure grants SELECT only - so
    the symptom in production would be a 500 on the conclusion endpoint and the symptom in a
    developer's environment would be a silently growing table.

    `test_no_non_get_route_is_declared` cannot see this. Only the call site can.
    """
    calls = stub_engine(monkeypatch, lambda as_of: passing_result(as_of))

    get()

    assert calls[0]["persist"] is False, (
        "the API called the engine with its persisting default; a GET must not write a row"
    )


# ---------------------------------------------------------------------------------------------
# Tests 29-30. The cache.
# ---------------------------------------------------------------------------------------------


def test_the_cache_key_includes_as_of_date(monkeypatch):
    """Test 29, decision 8. Two dates, two answers.

    A cache keyed on the path alone serves one date's conclusion for another's, AND IT LOOKS
    RIGHT - the shape is identical, the sentence is real, the analogs are real, and the only way to
    notice is to already know what the answer should have been.
    """
    def result_for(as_of):
        result = passing_result(as_of)
        # A marker the response cannot fake: the site label carries the date it was built for.
        return engine.AnalogResult(
            **{
                **result.__dict__,
                "condition": render.Condition(
                    site_label=f"Memphis-{as_of.isoformat()}",
                    river="Mississippi",
                    as_of=as_of,
                    change=-16_000.0,
                    lookback_days=parameters.CONDITION_LOOKBACK_DAYS,
                    anomaly=-83_967.0,
                    climatology_n_years=12,
                ),
            }
        )

    stub_engine(monkeypatch, result_for)

    first = get(as_of=date(2022, 10, 11)).json()
    second = get(as_of=date(2023, 9, 19)).json()

    assert first["as_of"] == "2022-10-11"
    assert second["as_of"] == "2023-09-19"
    assert "Memphis-2022-10-11" in first["sentence"]
    assert "Memphis-2023-09-19" in second["sentence"], (
        "the second date was served the first date's conclusion: the cache key does not include "
        "`as_of`, and nothing in the body would tell a reader"
    )


def test_a_repeated_request_is_served_from_the_cache(monkeypatch):
    """The cache actually caches. Without this, test 29 could pass on a cache that never hits.

    Not numbered, and it is the guard that keeps the one above from being vacuous: two identical
    requests must reach the engine once.
    """
    calls = stub_engine(monkeypatch, lambda as_of: passing_result(as_of))

    get()
    get()

    assert len(calls) == 1


def test_cached_responses_carry_computed_at(monkeypatch):
    """Test 30. And `computed_at` is the COMPUTE time, not the serve time.

    A `computed_at` stamped when the response is written would be identical on a hit and a miss,
    which makes it a field that looks like provenance and carries none. Asserting the two calls
    report the SAME timestamp is what pins it to the computation.
    """
    stub_engine(monkeypatch, lambda as_of: passing_result(as_of))

    first = get().json()
    second = get().json()

    assert "computed_at" in first
    assert first["computed_at"] == second["computed_at"], (
        "the cache hit re-stamped `computed_at`; it must report when the value was computed"
    )


def test_a_refusal_is_cached_the_same_way_an_estimate_is(monkeypatch):
    """A refusal carries `computed_at` too, and is cached on the same key.

    Not numbered. Caching only the passing branch would mean the expensive path - the one that runs
    the whole detect/collapse/exclude/measure pipeline and then refuses - is the one recomputed
    every time, which is backwards, and it would make `computed_at` present on some shapes only.
    """
    calls = stub_engine(monkeypatch, lambda as_of: refused_result(as_of=as_of))

    first = get().json()
    second = get().json()

    assert len(calls) == 1
    assert first["computed_at"] == second["computed_at"]
    assert "computed_at" in second


def test_the_cache_expires(monkeypatch):
    """The TTL is a TTL, asserted without sleeping. Not numbered.

    A cache with a broken expiry serves the same conclusion forever, and every test above would
    still pass - they all run inside one TTL window.
    """
    from app.api.cache import TTLCache

    clock = {"t": 0.0}
    cache = TTLCache(ttl_seconds=60, clock=lambda: clock["t"])
    calls = []

    def compute(computed_at):
        calls.append(computed_at)
        return len(calls)

    assert cache.get_or_compute(("k",), compute).value == 1
    clock["t"] = 59.0
    assert cache.get_or_compute(("k",), compute).value == 1
    clock["t"] = 61.0
    assert cache.get_or_compute(("k",), compute).value == 2


@pytest.mark.integration
def test_the_conclusion_endpoint_answers_over_a_real_database(migrated_db, seed, db_client):
    """The route against the real engine, the real migrations and a real (empty-ish) history.

    Not numbered, and it is the only test in this file where `engine.query` is not stubbed. Memphis
    has a feature series with no low-water condition on the query date, so the engine returns
    `no_current_event` - which is exactly the branch the instance produced at `--as-of 2022-09-06`.

    It exists because every other test in this file mocks the read. A route that assembled a body
    correctly from a stub and crashed against a real cursor would pass all of them.
    """
    days = [(date(2022, 1, 1) + timedelta(days=i)) for i in range(400)]
    for name in parameters.SIMILARITY_FEATURES:
        seed.features(MEMPHIS, name, [(day, 0.0, None, None) for day in days])
    seed.features(
        MEMPHIS, "discharge_mean", [(day, 400_000.0, -2_000.0, 12) for day in days]
    )

    response = db_client.get(f"/api/conclusion?site_id={MEMPHIS}&as_of=2022-10-11")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "no_current_event"
    assert body["detections"] == {"raw": 0, "collapsed": 0}
    # The sweep was never run in this fixture, so its verdict is NULL - which means "not measured",
    # not "no relationship", and the block is present saying so rather than absent.
    assert body["sweep"]["run_id"] is None
    assert body["sweep"]["best_q"] is None

    # And nothing was written. `analog_queries` records the CLI's research log; the API does not
    # add to it, which is decision 1 observed on the far side of a real transaction.
    assert migrated_db.execute("SELECT count(*) FROM analog_queries").fetchone()[0] == 0
