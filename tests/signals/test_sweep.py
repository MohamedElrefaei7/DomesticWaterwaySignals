"""The sweep: every pair written, negative lags kept, the gate computed, and no winner exposed.

FOUR OF THESE ARE INTEGRATION TESTS AND THE BRIEF EXPECTED UNIT ONES (17, 18, 19). The deviation is
deliberate and it is the same argument tests/features/test_rollup.py makes about the rollup.

"Every scanned pair is written, including the nulls" is a claim about WHAT LANDED IN THE TABLE. An
in-memory version - build a list, count it, assert the count - asserts that the code counted what
the code counted, and passes in both directions of the mutation it exists for. The failure mode
being guarded against is a filter at WRITE time, so the assertion has to be on the far side of the
write. `select count(*) from signals` is the check; anything earlier is a rehearsal of it.

Test 20 is a unit test, because the thing it asserts is the module's public surface.
"""

import inspect
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.features import build as features_build
from app.features import targets as targets_module
from app.signals import pairs, regimes, sweep

from tests.signals.conftest import (
    FIXED_GIT,
    MEMPHIS,
    SWEEP_HORIZONS as HORIZONS,
    SWEEP_LAG_MAX as LAG_MAX,
    SWEEP_LAG_MIN as LAG_MIN,
)


def small_grid_kwargs():
    """The parameters every sweep in this file runs with. The `sweepable` fixture is in conftest,
    shared with test_statistics.py so the p-without-q constraint can be exercised from both ends."""
    return {
        "lag_min": LAG_MIN,
        "lag_max": LAG_MAX,
        "horizons": HORIZONS,
        "regimes": regimes.REGIMES,
        "git": FIXED_GIT,
    }


# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_every_scanned_pair_is_written_including_nulls(migrated_db, sweepable):
    """Test 17. DECISION 6. The row count in the table equals the grid size.

    WRITING ONLY THE WINNERS IS WHAT MAKES A SWEEP DISHONEST, and it does not feel like fraud from
    the inside - the filter happens at write time and leaves no trace of itself. Twelve surviving
    rows in a table of twelve read as twelve findings; the same twelve in a table of seven thousand
    read as the top of a distribution, and the reader can see that a fair coin would have produced
    three hundred and fifty.

    So: the count of rows in `signals` must equal the count of pairs enumerated, and refusals and
    null results must be present in it rather than merely permitted by the schema.
    """
    result = sweep.run(migrated_db, **small_grid_kwargs())

    written = migrated_db.execute(
        "SELECT count(*) FROM signals WHERE run_id = %s", (result["run_id"],)
    ).fetchone()[0]

    assert written == result["grid_size"], (
        f"the grid enumerated {result['grid_size']} pairs and {written} rows were written. The "
        f"count of enumerated pairs IS the multiple-comparisons denominator; a table that lost "
        f"some of them reports a better passing fraction than the sweep earned."
    )
    assert result["rows_written"] == written

    # THE NULLS ARE ACTUALLY THERE. A sweep on a fixture where every pair happened to be measurable
    # would satisfy the count above while filtering refusals, so their presence is asserted
    # directly - two of the four seeded gauges carry no features at all and must appear as refusals.
    statuses = dict(
        migrated_db.execute(
            "SELECT status, count(*) FROM signals WHERE run_id = %s GROUP BY 1",
            (result["run_id"],),
        ).fetchall()
    )
    assert statuses.get(sweep.STATUS_INSUFFICIENT_OBSERVATIONS, 0) > 0, (
        f"no refusal rows were written, so this test cannot tell a complete table from a filtered "
        f"one: {statuses}"
    )
    assert statuses.get(sweep.STATUS_SCANNED, 0) > 0, f"nothing was measured at all: {statuses}"
    assert sum(statuses.values()) == result["grid_size"]

    # And rows that failed the gate are present - the population that disappears first.
    failing = migrated_db.execute(
        "SELECT count(*) FROM signals WHERE run_id = %s AND NOT passes_gate",
        (result["run_id"],),
    ).fetchone()[0]
    assert failing > 0, "every written row passed the gate, which means the failures were filtered"

    # THE DENOMINATOR AND THE NUMERATOR TOGETHER, which is the query the live procedure runs first.
    scanned, passing = migrated_db.execute(
        "SELECT count(*), count(*) FILTER (WHERE passes_gate) FROM signals WHERE run_id = %s",
        (result["run_id"],),
    ).fetchone()
    assert (scanned, passing) == (result["grid_size"], result["passing"])


@pytest.mark.integration
def test_negative_lags_are_scanned_and_retained(migrated_db, sweepable):
    """Test 18. DECISION 7. A negative lag is a finding about the world, not an artefact.

    A negative lag means THE TARGET MOVED BEFORE THE FEATURE. `CONTEXT.md` records the rate peaking
    two to three weeks BEFORE discharge bottomed in both 2022 and 2023 - the "operators price the
    published river forecast" case the handoff named. If the strongest relationships sit at
    negative lags, the project's claim changes from "the physical signal leads" to "the market
    prices the forecast", and that is the story rather than a nuisance.

    The failure this guards is not a crash. It is a scan quietly restricted to lag >= 0, which
    would report the absence of the negative-lag case as evidence for the positive one.
    """
    result = sweep.run(migrated_db, **small_grid_kwargs())

    lags = [
        row[0]
        for row in migrated_db.execute(
            "SELECT DISTINCT lag_days FROM signals WHERE run_id = %s ORDER BY 1",
            (result["run_id"],),
        ).fetchall()
    ]
    assert lags == list(range(LAG_MIN, LAG_MAX + 1)), (
        f"the table holds lags {lags}; the run was configured for {LAG_MIN}..{LAG_MAX}"
    )

    negative, positive = migrated_db.execute(
        "SELECT count(*) FILTER (WHERE lag_days < 0), count(*) FILTER (WHERE lag_days > 0)"
        "  FROM signals WHERE run_id = %s",
        (result["run_id"],),
    ).fetchone()
    assert negative == positive > 0, (
        f"{negative} negative-lag rows against {positive} positive-lag rows - the scan is not "
        f"symmetric about zero"
    )

    # NEGATIVE-LAG ROWS ARE MEASURED, not merely enumerated and refused. A scan that wrote them as
    # `insufficient_observations` would satisfy the counts above and would have measured nothing.
    measured = migrated_db.execute(
        "SELECT count(*) FROM signals"
        " WHERE run_id = %s AND lag_days < 0 AND status = %s AND p_value IS NOT NULL",
        (result["run_id"], sweep.STATUS_SCANNED),
    ).fetchone()[0]
    assert measured > 0, "every negative-lag pair was refused rather than measured"

    # And the sign convention is the stated one: a positive lag reads the feature BEFORE the target
    # week, a negative lag reads it after.
    week = date(2022, 6, 2)
    assert sweep.anchor_date(week, 7) == week - timedelta(days=7)
    assert sweep.anchor_date(week, -7) == week + timedelta(days=7)
    assert sweep.anchor_date(week, 0) == week


@pytest.mark.integration
def test_passes_gate_is_computed_not_filtered_on_write(migrated_db, sweepable):
    """Test 19. The gate is a stored column consumers filter on, not a decision the writer made.

    Two halves. The boolean must be RECOMPUTABLE from the other columns on the row - if it is not,
    it is an opinion rather than a computation, and nobody can check it. And rows on both sides of
    it must be present, because a table where every row passes is indistinguishable from a table
    that was filtered.
    """
    result = sweep.run(migrated_db, **small_grid_kwargs())

    rows = migrated_db.execute(
        "SELECT q_value, directional_consistency, folds, passes_gate"
        "  FROM signals WHERE run_id = %s",
        (result["run_id"],),
    ).fetchall()
    assert rows

    for q_value, consistency, folds, stored in rows:
        recomputed = sweep.passes_gate(q_value, consistency, folds)
        assert recomputed == stored, (
            f"stored passes_gate={stored} but the stated criteria recompute to {recomputed} for "
            f"q={q_value}, consistency={consistency}, folds={folds}. The column is not a "
            f"computation of the criteria it claims to apply."
        )

    assert any(not row[3] for row in rows), (
        "every row passes the gate - the failures were filtered out at write time, which is the "
        "shape this test exists to detect"
    )

    # THE THREE CRITERIA ARE THE STATED ONES and none of them was invented here: alpha from this
    # phase's own multiple-comparisons arithmetic, consistency from CLAUDE.md § 7 verbatim, folds
    # from decision 4's minimum.
    assert sweep.GATE_MAX_Q_VALUE == 0.05
    assert sweep.GATE_MIN_DIRECTIONAL_CONSISTENCY == 0.70
    assert sweep.GATE_MIN_FOLDS == 5

    # A row cannot pass on an unadjusted p-value, on too few folds, or on a missing consistency.
    assert sweep.passes_gate(None, 0.9, 10) is False
    assert sweep.passes_gate(0.001, None, 10) is False
    assert sweep.passes_gate(0.001, 0.9, None) is False
    assert sweep.passes_gate(0.001, 0.9, 4) is False
    assert sweep.passes_gate(0.06, 0.9, 10) is False
    assert sweep.passes_gate(0.001, 0.69, 10) is False
    assert sweep.passes_gate(0.05, 0.70, 5) is True, "the boundary is inclusive on all three"


def test_the_sweep_exposes_no_best_pair_accessor():
    """Test 20. DECISION 8, BY API SURFACE.

    The sweep measures and records. The moment another module can ask it which pair looked best,
    it has become a model-selection procedure with no held-out data - and every later evaluation of
    the chosen pair is an evaluation of a pair chosen because it evaluated well.

    Asserted against the module's public names rather than against behaviour, because the failure
    is somebody in a hurry ADDING one. A behavioural test cannot detect an accessor that does not
    exist yet, and this is the guard that goes red when it appears.

    `--top` is fine and exists: it PRINTS for a human and returns None. The private
    `_print_top_rows` is the function that would be the violation if it were public.
    """
    selection = re.compile(
        r"best|winner|strongest|rank|leaderboard|argmax|top|choose|pick|select", re.IGNORECASE
    )

    public = [
        name
        for name, value in vars(sweep).items()
        if not name.startswith("_") and callable(value) and getattr(value, "__module__", "") == sweep.__name__
    ]
    assert public, "introspection found no public callables at all, so this test asserts nothing"

    offenders = [name for name in public if selection.search(name)]
    assert not offenders, (
        f"app/signals/sweep.py exposes {offenders}. The sweep must not offer its own best result "
        f"to another module: selection happens in Phase 7, under CLAUDE.md § 7's confidence gate, "
        f"in a separate step from the procedure that generated the candidates. A human-readable "
        f"listing is fine and is what `_print_top_rows` is - private, and returning None."
    )

    # The human-reading path returns nothing, so there is no value to take a `[0]` from. Called
    # rather than read off the annotation, which `from __future__ import annotations` turns into
    # the string "None" - and an annotation is a claim about a return value where this is the
    # return value.
    assert sweep._print_top_rows([], 5) is None
    assert sweep._print_top_rows.__name__.startswith("_"), (
        "the function that prints the strongest rows is public, so another module can import it"
    )

    # `run` hands back a summary dict, and the numbers in it are reported together. It carries the
    # denominator alongside the passing count - a summary that offered only `passing` would make
    # the dishonest report the easy one to write.
    summary_keys = set(inspect.getsource(sweep.run).split("return {")[1].split("}")[0].split())
    for required in ('"grid_size":', '"passing":', '"scanned":'):
        assert required in " ".join(summary_keys), (
            f"sweep.run's summary omits {required} - the passing count and its denominator must "
            f"travel together"
        )


@pytest.mark.integration
def test_run_metadata_records_git_sha_and_dirty_state(migrated_db, sweepable, tmp_path):
    """Test 21. The commit a measurement was taken against, read from the repo and stored.

    A signal measured in March under different feature definitions is not comparable to one
    measured in June, and without the sha there is no way to know they differ - they sit in the
    same table looking like two observations of one thing. THIS PROJECT HAS ALREADY DONE THIS ONCE:
    Phase 5 contradicted Phase 4's headline on the same data, by changing what a feature meant.
    """
    sha, dirty = sweep.git_state()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"git_state returned {sha!r}, not a sha"

    actual_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(sweep.__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert sha == actual_sha

    result = sweep.run(
        migrated_db,
        lag_min=0,
        lag_max=0,
        horizons=(7,),
        regimes=(regimes.ALL,),
        feature_filter="days_below_p10",
    )

    stored = migrated_db.execute(
        "SELECT git_sha, git_dirty, grid_size, lag_min, lag_max, horizons, regimes,"
        "       feature_filter, seed, started_at, finished_at"
        "  FROM signal_runs WHERE run_id = %s",
        (result["run_id"],),
    ).fetchone()

    assert stored[0] == sha
    assert stored[1] == dirty, (
        f"signal_runs.git_dirty is {stored[1]} while the working tree reports {dirty}. A dirty "
        f"run's sha names a commit whose code is NOT what ran, and the two facts must not diverge."
    )
    assert (stored[2], stored[3], stored[4]) == (result["grid_size"], 0, 0)
    assert stored[5] == [7] and stored[6] == ["all"]
    assert stored[7] == "days_below_p10", "the feature filter was not recorded on the run"
    assert stored[8] is None, "a run that used no randomness must record no seed"
    assert stored[10] is not None and stored[10] >= stored[9], (
        "finished_at was not set, so a completed run is indistinguishable from a crashed one"
    )

    # THE SHA IS NOT DEFAULTED WHEN IT CANNOT BE READ. A run recorded as 'unknown' would look like
    # a sha-shaped value in every listing afterwards, and the ambiguity is permanent while
    # re-running the sweep is cheap.
    with pytest.raises(RuntimeError, match="could not read the commit"):
        sweep.git_state(tmp_path)


@pytest.mark.integration
def test_every_signal_row_references_a_run(migrated_db, sweepable, seed_signals):
    """Test 22. A row with no run is a measurement whose parameters are unrecoverable.

    Enforced by a foreign key rather than by the writer, for the reason test 7 gives: the writer is
    not the only thing that can insert here, and a row inserted by a script during an investigation
    would be just as unreadable six months later.
    """
    import psycopg

    result = sweep.run(migrated_db, **small_grid_kwargs())

    orphans = migrated_db.execute(
        "SELECT count(*) FROM signals s"
        " WHERE NOT EXISTS (SELECT 1 FROM signal_runs r WHERE r.run_id = s.run_id)"
    ).fetchone()[0]
    assert orphans == 0

    runs = migrated_db.execute(
        "SELECT DISTINCT run_id FROM signals ORDER BY 1"
    ).fetchall()
    assert runs == [(result["run_id"],)], (
        f"signals references runs {runs}; this test's sweep created {result['run_id']}"
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="run_id"):
        migrated_db.execute(
            "INSERT INTO signals"
            " (run_id, feature_name, site_id, series_column, target_name, horizon_days,"
            "  lag_days, regime, status, grid_size, n_tests_adjusted, n_observations, passes_gate)"
            " VALUES (%s, 'days_below_p10', %s, 'value', 'x', 7, 0, 'all', 'scanned', 1, 1, 0,"
            "         false)",
            (result["run_id"] + 10_000, MEMPHIS),
        )
    migrated_db.rollback()

    # A run row exists BEFORE the scan writes anything, so a sweep that dies leaves evidence it was
    # attempted rather than being indistinguishable from a sweep nobody started.
    open_run_id = seed_signals.open_run()
    unfinished = migrated_db.execute(
        "SELECT finished_at,"
        "       (SELECT count(*) FROM signals WHERE run_id = %s)"
        "  FROM signal_runs WHERE run_id = %s",
        (open_run_id, open_run_id),
    ).fetchone()
    assert unfinished == (None, 0)


# ---------------------------------------------------------------------------------------------
# Not in the brief's list. The lag alignment is where a leak would enter and not be noticed.
# ---------------------------------------------------------------------------------------------


def test_the_lagged_alignment_agrees_with_the_canonical_last_on_or_before():
    """`sweep.align_lagged` is a fast evaluation of `build.last_on_or_before`, not a second rule.

    CLAUDE.md § 17 is explicit that a precedence rule gets one implementation, because two diverge
    SILENTLY - each returns a plausible series and nothing compares them. The sweep needs bisection
    (it asks this question about a million times) and the canonical definition lives in build.py
    with the leakage argument written beside it. So the two are pinned against each other here,
    over a date set built so that the NEAREST-date version - the wrong one - disagrees with both.
    """
    feature_dates = sorted(
        [date(2022, 6, 1), date(2022, 6, 3), date(2022, 6, 10), date(2022, 7, 1)]
    )
    week = date(2022, 6, 5)

    for lag in range(-21, 22):
        anchor = sweep.anchor_date(week, lag)
        assert sweep.align_lagged(feature_dates, week, lag) == features_build.last_on_or_before(
            feature_dates, anchor
        ), f"the two implementations disagree at lag {lag} (anchor {anchor})"

    # THE GUARD IS NOT VACUOUS: nearest-date really does give a different answer here. The Saturday
    # after the anchor is nearer to it than the Friday before, so nearest-date would read river
    # conditions that had not happened yet.
    anchor = sweep.anchor_date(week, 0)
    nearest = min(feature_dates, key=lambda day: abs(day - anchor))
    assert nearest == date(2022, 6, 3)
    assert sweep.align_lagged(feature_dates, week, -3) == date(2022, 6, 3)
    nearest_at_minus_three = min(
        feature_dates, key=lambda day: abs(day - sweep.anchor_date(week, -3))
    )
    assert nearest_at_minus_three == date(2022, 6, 10), (
        "the fixture no longer distinguishes last-on-or-before from nearest-date, so this test "
        "would pass with the leaky implementation"
    )

    # No feature on or before the anchor is None, never the earliest available date - reaching
    # forward would reintroduce the leak at the one place it would be largest.
    assert sweep.align_lagged(feature_dates, date(2021, 1, 1), 0) is None
    assert sweep.align_lagged([], date(2022, 6, 5), 0) is None


def test_the_sweep_refuses_an_empty_grid(migrated_db, sweepable):
    """A sweep over nothing must not be recorded as a completed run that found no signals.

    Not in the brief's list. It is here because "no rows passed" and "no rows were scanned" print
    almost identically and mean completely different things - and a feature filter matching nothing
    is one typo away at every invocation.
    """
    with pytest.raises(ValueError, match="grid is empty"):
        sweep.run(migrated_db, feature_filter="no_such_feature_*", git=FIXED_GIT)

    assert (
        migrated_db.execute("SELECT count(*) FROM signal_runs").fetchone()[0] == 0
    ), "an empty-grid sweep opened a run row before refusing"


def test_adjustment_excludes_unscannable_pairs_from_m_but_not_from_the_grid():
    """The two denominators are different numbers and both are stored. Unit.

    Not in the brief's list, and it is the arithmetic behind `n_tests_adjusted`: a pair that
    produced no p-value contributed no test, so counting it in m would weaken every real q-value on
    the grid on behalf of pairs that produced no evidence at all. But it WAS enumerated, so it
    stays in `grid_size` and it gets a row.
    """
    pair = pairs.Pair(
        feature_name="days_below_p10",
        site_id=MEMPHIS,
        series_column="value",
        target_name=targets_module.TARGET_NAME,
        horizon_days=7,
        lag_days=0,
        regime=regimes.ALL,
    )
    scanned = [
        sweep.Scanned(pair, sweep.STATUS_SCANNED, 0.5, 0.001, 60, 60.0, 5, 1.0),
        sweep.Scanned(pair, sweep.STATUS_SCANNED, 0.2, 0.400, 60, 60.0, 5, 0.4),
        sweep.Scanned(
            pair, sweep.STATUS_INSUFFICIENT_OBSERVATIONS, None, None, 3, None, None, None
        ),
    ]

    rows = sweep.adjust_and_gate(scanned, grid_size=len(scanned))

    assert len(rows) == 3, "a refusal was dropped rather than written"
    assert all(row.grid_size == 3 for row in rows)
    assert all(row.n_tests_adjusted == 2 for row in rows), (
        f"m should be 2 - the two pairs that produced a p-value - not "
        f"{[r.n_tests_adjusted for r in rows]}"
    )

    assert rows[2].q_value is None and rows[2].p_value is None
    assert rows[0].q_value == pytest.approx(0.002)
    assert rows[1].q_value == pytest.approx(0.4)

    assert rows[0].passes_gate is True
    assert rows[1].passes_gate is False
    assert rows[2].passes_gate is False
