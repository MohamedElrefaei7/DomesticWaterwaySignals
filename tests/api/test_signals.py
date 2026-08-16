"""`/api/signals` — the denominator is the default view, and `grid_size` never travels alone.

THE ARITHMETIC THIS FILE ANSWERS TO (CLAUDE.md § 18): a grid of ~7,000 tests at α = 0.05 produces
~350 significant results on pure noise. Not through a bug - by construction, on random data, every
time. `signals` records EVERY scanned combination precisely so the denominator survives, and the
last place that can be undone is a read endpoint that filters by default.

Measured on this project's own data: 1 of 6,966 pairs passes. `passing_only=true` as a default
would turn that into "we found a signal", at the layer where somebody screenshots it.

These are integration tests. The claims are about what a real `signals` table returns through a
real query, and a fake connection would be asserting the fake's own WHERE clause.
"""

from datetime import date

import pytest

from tests.api.conftest import BATON_ROUGE, MEMPHIS, ST_LOUIS, VICKSBURG

# A grid with one survivor in it, which is the shape of this project's own sweep result: the
# passing row is real and the reason it means anything is the 5 beside it.
GRID_SIZE = 5


@pytest.fixture
def swept(seed):
    """One run over five pairs, of which exactly one passes the sweep's gate."""
    run_id = seed.signal_run(grid_size=GRID_SIZE)

    seed.signal(run_id, feature_name="days_below_p10", site_id=MEMPHIS,
                q_value=0.0446, passes_gate=True, grid_size=GRID_SIZE)
    for index, (feature, site) in enumerate(
        [
            ("days_below_p10", ST_LOUIS),
            ("days_below_p05", MEMPHIS),
            ("discharge_mean", VICKSBURG),
            ("discharge_mean", BATON_ROUGE),
        ]
    ):
        seed.signal(run_id, feature_name=feature, site_id=site, q_value=0.4 + index * 0.1,
                    passes_gate=False, grid_size=GRID_SIZE, lag_days=index - 2)
    return run_id


@pytest.mark.integration
def test_signals_default_to_all_rows_not_passing_only(swept, db_client):
    """Test 27. The scanned rows ARE the multiple-comparisons record, so they are the default.

    A default of `passing_only=true` would filter at read time and leave no trace of itself: the
    client receives one row in a table of one, which reads as a finding. The same row in a table of
    five reads as the top of a distribution.
    """
    body = db_client.get("/api/signals").json()

    assert body["passing_only"] is False, "the default view must not be pre-filtered"
    assert body["total"] == GRID_SIZE, (
        f"the default view returned {body['total']} of {GRID_SIZE} scanned rows; the ones it left "
        f"out are the denominator"
    )
    assert len(body["rows"]) == GRID_SIZE
    assert sum(1 for row in body["rows"] if row["passes_gate"]) == 1

    # And the filter still works when it is asked for explicitly - the point is the default, not
    # the capability.
    passing = db_client.get("/api/signals?passing_only=true").json()
    assert passing["passing_only"] is True
    assert passing["total"] == 1


@pytest.mark.integration
def test_a_signal_row_carries_its_q_value_and_grid_size_together(swept, db_client):
    """Test 28. A q-value is meaningless without the grid it was adjusted against.

    A later run over a NARROWER grid produces smaller q-values in the same column, in the same
    units, from a different experiment. `grid_size` is denormalized onto the row in the database
    for exactly this reason and it must not be dropped on the way out.
    """
    body = db_client.get("/api/signals").json()

    for row in body["rows"]:
        assert "q_value" in row
        assert "grid_size" in row, (
            "a row carrying a q-value with no grid size re-creates the multiple-comparisons "
            "problem at the API layer"
        )
        assert row["grid_size"] == GRID_SIZE
        assert row["n_tests_adjusted"] == GRID_SIZE

    survivor = next(row for row in body["rows"] if row["passes_gate"])
    assert survivor["q_value"] == pytest.approx(0.0446)
    assert survivor["grid_size"] == GRID_SIZE
    # `directional_consistency` never appears without `folds`: 4 of 5 and 40 of 50 are both 80%.
    assert survivor["folds"] is not None
    assert survivor["directional_consistency"] is not None


@pytest.mark.integration
def test_signals_responses_carry_grid_size_and_scanned_count(swept, db_client):
    """Test 5. The envelope's run block carries both counts; every row carries its grid size.

    `passing_pairs` without `scanned_pairs` is the dishonest form of this result. Phase 6's own
    headline is "1 of 6,966", and the two numbers have to arrive together or the first one is a
    claim about nothing.
    """
    body = db_client.get("/api/signals").json()

    run = body["run"]
    assert run is not None
    assert run["grid_size"] == GRID_SIZE
    assert run["scanned_pairs"] == GRID_SIZE
    assert run["passing_pairs"] == 1
    assert {"scanned_pairs", "passing_pairs"} <= run.keys(), (
        "a passing count without its denominator is the dishonest form (CLAUDE.md § 18)"
    )

    assert all("grid_size" in row for row in body["rows"])

    # AND THE FILTERED VIEW STILL CARRIES THE FULL DENOMINATOR. This is the case that matters:
    # a client asking only for survivors must still be told how many were scanned.
    filtered = db_client.get("/api/signals?passing_only=true").json()
    assert filtered["total"] == 1
    assert filtered["run"]["scanned_pairs"] == GRID_SIZE


@pytest.mark.integration
def test_the_conclusion_sweep_block_reports_the_same_two_counts(swept, seed, db_client):
    """One definition of "how many passed", read by both endpoints. Not numbered.

    `run_summary` is shared between the conclusion route and the signals route precisely so the
    two cannot disagree about a run's denominator - two copies of that query would be two
    definitions of the same number, with nothing comparing them.
    """
    from app.analogs import parameters

    days = [date(2022, 1, 1)]
    for name in parameters.SIMILARITY_FEATURES:
        seed.features(MEMPHIS, name, [(day, 0.0, None, None) for day in days])
    seed.features(MEMPHIS, "discharge_mean", [(day, 400_000.0, -2_000.0, 12) for day in days])

    conclusion = db_client.get(
        f"/api/conclusion?site_id={MEMPHIS}&as_of=2022-01-01"
    ).json()
    signals = db_client.get("/api/signals").json()

    assert conclusion["sweep"]["scanned_pairs"] == signals["run"]["scanned_pairs"]
    assert conclusion["sweep"]["passing_pairs"] == signals["run"]["passing_pairs"]
    assert conclusion["sweep"]["grid_size"] == signals["run"]["grid_size"]
    assert conclusion["sweep"]["run_id"] == swept


@pytest.mark.integration
def test_signal_runs_are_listed_newest_first_with_both_counts(swept, seed, db_client):
    """`/api/signals/runs`. Not numbered.

    Newest first because the most recent run is the current answer; a run that enumerated a grid
    and wrote nothing yet still appears, with `scanned_pairs: 0`, rather than vanishing from the
    listing - a run that disappears because it produced nothing is a denominator going missing one
    level up.
    """
    empty_run = seed.signal_run(grid_size=99)

    body = db_client.get("/api/signals/runs").json()

    assert body["total"] == 2
    assert [row["run_id"] for row in body["rows"]] == [empty_run, swept]

    newest = body["rows"][0]
    assert newest["grid_size"] == 99
    assert newest["scanned_pairs"] == 0, (
        "a run that enumerated a grid and wrote no rows must still be listed, saying so"
    )
    assert newest["passing_pairs"] == 0


@pytest.mark.integration
def test_signals_defaults_to_the_most_recent_run_not_the_friendliest(swept, seed, db_client):
    """Most recent, never best. Not numbered.

    Taking the run with the smallest q-values anywhere would be selecting the friendliest
    experiment - model selection performed by the consumer instead of the writer, which is the
    failure `app/signals/` is arranged to prevent. `app/analogs/engine.py` makes the same choice
    for the same reason.
    """
    newer = seed.signal_run(grid_size=1)
    seed.signal(newer, feature_name="days_below_p10", site_id=MEMPHIS, q_value=0.9,
                passes_gate=False, grid_size=1)

    body = db_client.get("/api/signals").json()

    assert body["run"]["run_id"] == newer
    assert body["total"] == 1
    assert body["rows"][0]["q_value"] == pytest.approx(0.9), (
        "the default picked a run with a friendlier q-value than the latest one"
    )


@pytest.mark.integration
def test_no_sweep_on_record_is_an_empty_page_rather_than_an_error(db_client):
    """"Nothing has been scanned" is an honest answer to "what did the sweep find". Not numbered."""
    body = db_client.get("/api/signals").json()

    assert body["total"] == 0
    assert body["rows"] == []
    assert body["run"] is None


@pytest.mark.integration
def test_an_unknown_run_id_is_a_404(swept, db_client):
    """Asking about a run that does not exist is a mistake, not an empty result. Not numbered."""
    response = db_client.get(f"/api/signals?run_id={swept + 999}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.integration
def test_the_signals_cache_key_includes_passing_only(swept, db_client):
    """Decision 8, on the other cached endpoint. Not numbered.

    The conclusion cache's cross-serving failure has an exact twin here: two requests that differ
    only in `passing_only` have identically shaped bodies, and serving one for the other would hand
    a client the filtered view while it believed it had the whole grid.
    """
    everything = db_client.get("/api/signals").json()
    survivors = db_client.get("/api/signals?passing_only=true").json()

    assert everything["total"] == GRID_SIZE
    assert survivors["total"] == 1, (
        "the filtered request was served the unfiltered answer: the cache key does not include "
        "`passing_only`"
    )
    assert everything["computed_at"] is not None
    assert survivors["computed_at"] is not None
