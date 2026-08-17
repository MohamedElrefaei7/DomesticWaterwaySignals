"""The sweep: measure every pair in the grid, record all of them, and select none of them.

A CLI, NOT A SCHEDULED JOB, AND THAT IS A DECISION RATHER THAN AN OMISSION
---------------------------------------------------------------------------
There is no cadence entry for this module and no freshness registration for `signals`. It is
long-running, it is a research operation, and its output is read and argued with by a human before
anything consumes it.

A scheduled sweep would accumulate runs nobody reads - and the whole risk of this phase is that a
grid of seven thousand tests produces significant-looking rows by construction. Left running
nightly, it would eventually be the thing that "found" a signal at 3am that nothing validated, sat
in a table that looks authoritative, and got quoted. `signals` going stale is not a system-health
question; nobody should be alerted that nobody has run a sweep lately.

WHAT THIS MODULE WILL NOT DO
-----------------------------
It will not tell you its best pair. There is no `best_pair()`, no ranked return value, and no
public callable a downstream module could ask for a winner - `tests/signals/test_sweep.py` asserts
that by reading the module's public surface, because the guard has to survive somebody who is in a
hurry and would find one convenient.

`--top` prints the strongest rows FOR A HUMAN TO READ, prints them under a heading that says they
are the top of a distribution, and returns None. The distinction is not pedantry: the moment
another module can ask this one which pair looked best, the sweep has become a model-selection
procedure with no held-out data, and every subsequent evaluation of the chosen pair is an
evaluation of a pair chosen because it evaluated well. Selection is Phase 7's job, under CLAUDE.md
§ 7's stated confidence gate, in a separate step.

THE GATE'S NUMBERS ARE ASSEMBLED FROM STATED ONES, NOT INVENTED
----------------------------------------------------------------
CLAUDE.md § 1 forbids this agent from inventing confidence-gating logic. So none of the three
thresholds below is chosen here:

    q ≤ 0.05                    the α this phase's own multiple-comparisons arithmetic is stated in
    consistency ≥ 0.70          CLAUDE.md § 7's confidence gate, verbatim
    folds ≥ 5                   decision 4's stated minimum (walkforward.MIN_FOLDS)

§ 7's OTHER half - ≥4 analogs - HAS NO COUNTERPART HERE and is deliberately not approximated by
something fold-shaped. A sweep has no analogs; it has folds. Phase 7 builds the analog engine and
applies that half. Writing "folds ≥ 4" here and calling the gate satisfied would be the more
dangerous kind of wrong, because it would look like the contract had been honoured.

`passes_gate` IS COMPUTED AND STORED, NEVER FILTERED ON WRITE
--------------------------------------------------------------
Every enumerated pair gets a row, including the refusals and the null results. The table is the
multiple-comparisons record (migration 0023); writing only the survivors would destroy the
denominator and turn the top of a distribution into a list of findings.
"""

from __future__ import annotations

import argparse
import bisect
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.orchestration import session
from app.features import targets as targets_module
from app.signals import pairs as pairs_module
from app.signals import regimes as regimes_module
from app.signals import statistics, walkforward

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------
# The gate. Every number here comes from somewhere else; see the module docstring.
# ---------------------------------------------------------------------------------------------

# The α this phase's arithmetic is stated in: at 0.05 on a ~7,000-cell grid, roughly 350 rows clear
# the threshold on pure noise, which is the fact the whole design answers to. Applied to the
# ADJUSTED q-value, so it means "false discovery rate at most 5%" rather than "one in twenty".
GATE_MAX_Q_VALUE = 0.05

# CLAUDE.md § 7, verbatim. Phase 7's analog engine consumes the same number.
GATE_MIN_DIRECTIONAL_CONSISTENCY = 0.70

# Decision 4's stated minimum, read from the module that implements it rather than repeated.
GATE_MIN_FOLDS = walkforward.MIN_FOLDS

STATUS_SCANNED = "scanned"
STATUS_INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
STATUS_INSUFFICIENT_FOLDS = "insufficient_folds"


# ---------------------------------------------------------------------------------------------
# Run provenance.
# ---------------------------------------------------------------------------------------------


def git_state(repo_root: Path = REPO_ROOT) -> tuple[str, bool]:
    """`(sha, dirty)` for the working tree the sweep is running from.

    RAISES RATHER THAN DEFAULTING. A sweep whose commit cannot be determined produces a table of
    q-values nobody can place against a set of feature definitions - and Phase 5 contradicting
    Phase 4 on the same data, by changing what a feature meant, is this project's own evidence that
    the provenance is the difference between a result and a number. The sweep is cheap to re-run
    and the ambiguity is permanent, so this stops.

    `git status --porcelain` rather than `git diff --quiet`: the latter is blind to untracked
    files, and a new module sitting untracked in `app/signals/` is exactly the state in which
    somebody runs an exploratory sweep.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"could not read the commit this sweep is running against, from {repo_root}: {exc}. "
            f"signal_runs.git_sha is NOT NULL on purpose - a measurement whose feature definitions "
            f"cannot be identified is not comparable to any other, and there is no way to discover "
            f"later which one it was. Run the sweep from a git checkout."
        ) from exc

    if not sha:
        raise RuntimeError(
            f"`git rev-parse HEAD` returned nothing in {repo_root} - a repository with no commits. "
            f"Commit before sweeping: the run records what it ran against."
        )
    return sha, bool(porcelain)


# ---------------------------------------------------------------------------------------------
# Reading the two series.
# ---------------------------------------------------------------------------------------------

# The column name is interpolated into the SQL text, so this is an allowlist rather than a bound
# parameter - the same boundary rollup.observations draws, for the same reason.
READABLE_SERIES_COLUMNS = frozenset({"value", "anomaly"})

FEATURE_SERIES_SQL = """
SELECT date, {column}
  FROM features
 WHERE feature_name = %(feature_name)s
   AND site_id      = %(site_id)s
 ORDER BY date
"""

# NULL TARGETS ARE EXCLUDED HERE AND NOT IMPUTED ANYWHERE. A NULL is an unpublished week - a real
# river closure (migration 0017) or the end of the series - and CLAUDE.md § 17 forbids carrying one
# forward. Excluding them shrinks n, which the p-value then reflects; filling them would produce a
# return of exactly zero, which is the most ordinary value this column can hold.
TARGET_SERIES_SQL = """
SELECT week_ending, value
  FROM targets
 WHERE target_name  = %(target_name)s
   AND horizon_days = %(horizon_days)s
   AND value IS NOT NULL
 ORDER BY week_ending
"""


def feature_series(conn, feature_name: str, site_id: str, column: str) -> list[tuple]:
    """One feature's dated series at one site, from the stated column."""
    if column not in READABLE_SERIES_COLUMNS:
        raise ValueError(
            f"{column!r} is not a readable features column. Known: "
            f"{sorted(READABLE_SERIES_COLUMNS)}. This name is interpolated into SQL, so it is an "
            f"allowlist rather than a parameter."
        )
    rows = conn.execute(
        FEATURE_SERIES_SQL.format(column=column),
        {"feature_name": feature_name, "site_id": site_id},
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def target_series(conn, target_name: str, horizon_days: int) -> list[tuple]:
    """One target series, NULLs already excluded."""
    rows = conn.execute(
        TARGET_SERIES_SQL, {"target_name": target_name, "horizon_days": horizon_days}
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


# ---------------------------------------------------------------------------------------------
# The lag alignment. The one place a leak could enter and not be noticed.
# ---------------------------------------------------------------------------------------------


def anchor_date(week_ending: date, lag_days: int) -> date:
    """The feature date a target week at `lag_days` should be matched against.

    POSITIVE LAG SUBTRACTS: at +7 the feature is read a week BEFORE the target week, which is the
    physical-signal-leads case. At -7 it is read a week AFTER - the target moved first, which is
    the "operators price the published forecast" case and is a finding rather than an artefact.
    """
    return week_ending - timedelta(days=lag_days)


def align_lagged(sorted_feature_dates, week_ending: date, lag_days: int) -> date | None:
    """The last feature date on or before the lag-shifted anchor. NEVER THE NEAREST.

    This is `app/features/build.py`'s `last_on_or_before` over a pre-sorted list, by bisection
    rather than by a linear scan - the sweep asks this question about a million times and the
    canonical implementation is O(n) per call.

    A SECOND IMPLEMENTATION OF A RULE IS EXACTLY WHAT CLAUDE.md § 17 WARNS ABOUT, so the test suite
    pins these two against each other over a date set constructed so that the nearest-date version
    disagrees with both. The canonical definition stays in build.py, where the leakage argument is
    written down; this is a faster way to evaluate it, and the equivalence is asserted rather than
    asserted-in-a-comment.

    Nearest-date matching would admit lookahead at every positive lag: a feature dated two days
    after the anchor is nearer to it than one dated three days before, so conditions that had not
    happened would inform the row. It appears in no schema and it survives review.
    """
    anchor = anchor_date(week_ending, lag_days)
    position = bisect.bisect_right(sorted_feature_dates, anchor)
    return sorted_feature_dates[position - 1] if position else None


# ---------------------------------------------------------------------------------------------
# One pair's measurement.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scanned:
    """One pair, measured or refused. No q-value yet - that needs the whole grid."""

    pair: pairs_module.Pair
    status: str
    statistic: float | None
    p_value: float | None
    n_observations: int
    n_effective: float | None
    folds: int | None
    directional_consistency: float | None


@dataclass(frozen=True)
class SignalRow:
    """A `signals` row: a Scanned with its q-value, its denominators, and its gate verdict."""

    pair: pairs_module.Pair
    status: str
    statistic: float | None
    p_value: float | None
    q_value: float | None
    grid_size: int
    n_tests_adjusted: int
    n_observations: int
    n_effective: float | None
    folds: int | None
    directional_consistency: float | None
    passes_gate: bool


def scan_one(
    pair: pairs_module.Pair,
    aligned,
    regime_dates,
    *,
    n_folds: int = GATE_MIN_FOLDS,
) -> Scanned:
    """Measure one (feature, site, horizon, lag, regime) combination.

    `aligned` is `[(week_ending, feature_date, feature_value, target_value)]` for this
    (feature, site, horizon, lag), already lag-shifted. `regime_dates` is the set of FEATURE dates
    belonging to this pair's regime - computed from the feature series alone by regimes.py, which
    has no parameter a target could be passed through.

    Every exit is a Scanned with a stated status. There is no path that returns nothing.
    """
    rows = [row for row in aligned if row[1] in regime_dates]
    xs = [row[2] for row in rows]
    ys = [row[3] for row in rows]

    measurement = statistics.measure(xs, ys, pair.horizon_days)
    if measurement is None:
        return Scanned(
            pair=pair,
            status=STATUS_INSUFFICIENT_OBSERVATIONS,
            statistic=None,
            p_value=None,
            n_observations=len(rows),
            n_effective=None,
            folds=None,
            directional_consistency=None,
        )

    weeks = [row[0] for row in rows]
    folds = walkforward.splits(weeks, horizon_days=pair.horizon_days, n_folds=n_folds)
    if not folds:
        # THE STATISTIC IS STILL RECORDED. A pair with a full-sample correlation and no folds is a
        # measured thing that failed a stated evaluation requirement, which is different from a
        # pair with nothing to measure - and `status` is what tells them apart. What it does NOT
        # get is a passing gate: passes_gate requires folds, so this row cannot become a finding.
        return Scanned(
            pair=pair,
            status=STATUS_INSUFFICIENT_FOLDS,
            statistic=measurement.statistic,
            p_value=measurement.p_value,
            n_observations=measurement.n_observations,
            n_effective=measurement.n_effective,
            folds=len(folds),
            directional_consistency=None,
        )

    # THE RUNTIME LEAKAGE GUARD. Cheap enough to run on every pair; the exhaustive per-date form is
    # what the test suite uses to decide whether this one is right (walkforward.assert_gap_clean).
    walkforward.assert_gap_clean(folds, pair.horizon_days)

    by_week = {row[0]: (row[2], row[3]) for row in rows}
    fold_statistics = []
    for fold in folds:
        window = [
            by_week[week] for week in weeks if fold.test_start <= week <= fold.test_end
        ]
        fold_statistics.append(
            statistics.pearson([w[0] for w in window], [w[1] for w in window])
        )

    consistency = walkforward.directional_consistency(measurement.statistic, fold_statistics)

    return Scanned(
        pair=pair,
        status=STATUS_SCANNED,
        statistic=measurement.statistic,
        p_value=measurement.p_value,
        n_observations=measurement.n_observations,
        n_effective=measurement.n_effective,
        folds=len(folds) if consistency is None else consistency.folds,
        directional_consistency=None if consistency is None else consistency.fraction,
    )


# ---------------------------------------------------------------------------------------------
# The grid scan.
# ---------------------------------------------------------------------------------------------


def _alignment_key(pair: pairs_module.Pair) -> tuple:
    """Everything that determines a pair's aligned series EXCEPT its regime.

    `pairs.build_grid` varies regime fastest, so grouping on this key walks the grid in blocks of
    three and each block's alignment is computed once instead of three times. The grouping is an
    optimisation and nothing else: `scan` returns one Scanned per pair, in grid order, and the
    count is asserted against `grid.size` by the caller.
    """
    return (
        pair.feature_name,
        pair.site_id,
        pair.series_column,
        pair.target_name,
        pair.horizon_days,
        pair.lag_days,
    )


def scan(conn, grid: pairs_module.Grid, *, n_folds: int = GATE_MIN_FOLDS) -> list[Scanned]:
    """Measure every pair in the grid. Returns one Scanned per pair, in grid order."""
    feature_cache: dict[tuple, tuple] = {}
    target_cache: dict[tuple, list] = {}

    results: list[Scanned] = []
    for key, block in groupby(grid.pairs, key=_alignment_key):
        feature_name, site_id, column, target_name, horizon_days, lag_days = key

        cache_key = (feature_name, site_id, column)
        if cache_key not in feature_cache:
            series = feature_series(conn, feature_name, site_id, column)
            usable = [(day, value) for day, value in series if value is not None]
            feature_cache[cache_key] = (
                [day for day, _ in usable],
                {day: value for day, value in usable},
                {
                    regime: regimes_module.dates_in_regime(series, regime)
                    for regime in regimes_module.REGIMES
                },
            )
        feature_dates, feature_values, regime_dates = feature_cache[cache_key]

        target_key = (target_name, horizon_days)
        if target_key not in target_cache:
            target_cache[target_key] = target_series(conn, target_name, horizon_days)
        targets = target_cache[target_key]

        aligned = []
        for week, target_value in targets:
            matched = align_lagged(feature_dates, week, lag_days)
            if matched is None:
                continue
            aligned.append((week, matched, feature_values[matched], target_value))

        for pair in block:
            results.append(
                scan_one(pair, aligned, regime_dates[pair.regime], n_folds=n_folds)
            )

    return results


# ---------------------------------------------------------------------------------------------
# Adjustment and the gate. The step that turns raw p-values into something reportable.
# ---------------------------------------------------------------------------------------------


def passes_gate(q_value, directional_consistency, folds) -> bool:
    """The three stated criteria, in one place, applied to STORED column values.

    Written against the values that end up on the row rather than against the objects that produced
    them, so a consumer can recompute the boolean from the table and get the same answer. The test
    suite does exactly that, which is what makes `passes_gate` a computed column rather than an
    assertion about the writer's intentions.
    """
    if q_value is None or directional_consistency is None or folds is None:
        return False
    return (
        q_value <= GATE_MAX_Q_VALUE
        and folds >= GATE_MIN_FOLDS
        and directional_consistency >= GATE_MIN_DIRECTIONAL_CONSISTENCY
    )


def adjust_and_gate(scanned, grid_size: int) -> list[SignalRow]:
    """Benjamini-Hochberg across the whole run, then the gate. One row out per row in.

    THE ADJUSTMENT IS OVER EVERY p-VALUE THIS RUN PRODUCED, which is the entire point: a q-value
    adjusted within one feature's 41 lags would be adjusted against 41 tests while the reader was
    looking at a table of seven thousand.

    Pairs with no p-value are excluded from `m` and keep a NULL q. They contributed no test, so
    counting them would weaken every real q-value on the grid on behalf of pairs that produced no
    evidence at all. `n_tests_adjusted` records the m that was actually used, beside the
    `grid_size` that was enumerated, because the two differ and a q-value means nothing without the
    first.
    """
    tested = [index for index, row in enumerate(scanned) if row.p_value is not None]
    q_values = statistics.benjamini_hochberg([scanned[index].p_value for index in tested])
    q_by_index = dict(zip(tested, q_values))
    n_tests = len(tested)

    rows = []
    for index, row in enumerate(scanned):
        q_value = q_by_index.get(index)
        rows.append(
            SignalRow(
                pair=row.pair,
                status=row.status,
                statistic=row.statistic,
                p_value=row.p_value,
                q_value=q_value,
                grid_size=grid_size,
                n_tests_adjusted=n_tests,
                n_observations=row.n_observations,
                n_effective=row.n_effective,
                folds=row.folds,
                directional_consistency=row.directional_consistency,
                passes_gate=passes_gate(q_value, row.directional_consistency, row.folds),
            )
        )
    return rows


# ---------------------------------------------------------------------------------------------
# Writing.
# ---------------------------------------------------------------------------------------------

OPEN_RUN_SQL = """
INSERT INTO signal_runs
    (grid_size, lag_min, lag_max, horizons, regimes, feature_filter, git_sha, git_dirty, seed)
VALUES (%(grid_size)s, %(lag_min)s, %(lag_max)s, %(horizons)s, %(regimes)s, %(feature_filter)s,
        %(git_sha)s, %(git_dirty)s, %(seed)s)
RETURNING run_id
"""

CLOSE_RUN_SQL = "UPDATE signal_runs SET finished_at = now() WHERE run_id = %s"

INSERT_SIGNAL_SQL = """
INSERT INTO signals
    (run_id, feature_name, site_id, series_column, target_name, horizon_days, lag_days, regime,
     status, statistic, p_value, q_value, grid_size, n_tests_adjusted, n_observations,
     n_effective, folds, directional_consistency, passes_gate)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def open_run(conn, *, grid_size, lag_min, lag_max, horizons, regimes, feature_filter,
             git_sha, git_dirty, seed=None) -> int:
    """Record the run and COMMIT IT, before the scan starts.

    Committed early on purpose. The scan is the long part; a sweep that dies in it must leave
    evidence that it was attempted, or a crash is indistinguishable from a sweep nobody ran. A run
    row with `finished_at IS NULL` and no `signals` rows is that evidence, and migration 0022 says
    so in a comment beside the column.
    """
    run_id = conn.execute(
        OPEN_RUN_SQL,
        {
            "grid_size": grid_size,
            "lag_min": lag_min,
            "lag_max": lag_max,
            "horizons": list(horizons),
            "regimes": list(regimes),
            "feature_filter": feature_filter,
            "git_sha": git_sha,
            "git_dirty": git_dirty,
            "seed": seed,
        },
    ).fetchone()[0]
    conn.commit()
    return run_id


def write_rows(conn, run_id: int, rows) -> int:
    """Insert every row, including the null results. Returns the number written.

    A PLAIN INSERT RATHER THAN AN UPSERT, and no filtering. Each run has its own `run_id`, so
    nothing can conflict - and an `ON CONFLICT DO UPDATE` here would quietly permit a re-run to
    overwrite a recorded measurement, which is the one thing a research log must not allow.
    """
    parameters = [
        (
            run_id,
            row.pair.feature_name,
            row.pair.site_id,
            row.pair.series_column,
            row.pair.target_name,
            row.pair.horizon_days,
            row.pair.lag_days,
            row.pair.regime,
            row.status,
            row.statistic,
            row.p_value,
            row.q_value,
            row.grid_size,
            row.n_tests_adjusted,
            row.n_observations,
            row.n_effective,
            row.folds,
            row.directional_consistency,
            row.passes_gate,
        )
        for row in rows
    ]
    with conn.cursor() as cursor:
        cursor.executemany(INSERT_SIGNAL_SQL, parameters)
    return len(parameters)


# ---------------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------------


def run(
    conn,
    *,
    lag_min: int = pairs_module.DEFAULT_LAG_MIN,
    lag_max: int = pairs_module.DEFAULT_LAG_MAX,
    horizons=targets_module.HORIZON_DAYS,
    regimes=regimes_module.REGIMES,
    target_name: str = targets_module.TARGET_NAME,
    feature_filter: str | None = None,
    n_folds: int = GATE_MIN_FOLDS,
    git: tuple[str, bool] | None = None,
    seed: int | None = None,
) -> dict:
    """One sweep. Returns a summary; the numbers in it are the ones the report must state together.

    `scanned` and `passing` are both in the returned dict and both are printed together by `main`.
    A passing count without its denominator is the dishonest form of this result, and the shape of
    the summary is what makes reporting it correctly the path of least resistance.
    """
    git_sha, git_dirty = git_state() if git is None else git

    grid = pairs_module.grid(
        conn,
        lag_min=lag_min,
        lag_max=lag_max,
        horizons=horizons,
        regimes=regimes,
        target_name=target_name,
        feature_filter=feature_filter,
    )
    if grid.size == 0:
        raise ValueError(
            "the grid is empty - no pairs to scan. With a feature filter set, it matched nothing; "
            "without one, the gauges table is empty. Either way a sweep over nothing would be "
            "recorded as a completed run that found no signals, which is not the same statement."
        )

    run_id = open_run(
        conn,
        grid_size=grid.size,
        lag_min=lag_min,
        lag_max=lag_max,
        horizons=horizons,
        regimes=regimes,
        feature_filter=feature_filter,
        git_sha=git_sha,
        git_dirty=git_dirty,
        seed=seed,
    )

    scanned = scan(conn, grid, n_folds=n_folds)
    rows = adjust_and_gate(scanned, grid.size)

    # THE DENOMINATOR CHECK, IN CODE. If the writer ever drops a row, this raises rather than
    # producing a smaller and better-looking table. It is decision 6 as an assertion rather than as
    # an intention.
    if len(rows) != grid.size:
        raise RuntimeError(
            f"the scan produced {len(rows)} rows for a grid of {grid.size}. Every enumerated pair "
            f"must be written, including the null results: the count of enumerated pairs IS the "
            f"multiple-comparisons denominator, and a table that lost some of them reports a "
            f"better passing fraction than the sweep earned."
        )

    written = write_rows(conn, run_id, rows)
    conn.execute(CLOSE_RUN_SQL, (run_id,))
    conn.commit()

    return {
        "run_id": run_id,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "grid_size": grid.size,
        "rows_written": written,
        "scanned": sum(1 for row in rows if row.status == STATUS_SCANNED),
        "passing": sum(1 for row in rows if row.passes_gate),
        "n_tests_adjusted": rows[0].n_tests_adjusted,
        "insufficient_observations": sum(
            1 for row in rows if row.status == STATUS_INSUFFICIENT_OBSERVATIONS
        ),
        "insufficient_folds": sum(
            1 for row in rows if row.status == STATUS_INSUFFICIENT_FOLDS
        ),
        "negative_lag_passing": sum(
            1 for row in rows if row.passes_gate and row.pair.lag_days < 0
        ),
        "positive_lag_passing": sum(
            1 for row in rows if row.passes_gate and row.pair.lag_days > 0
        ),
        "skipped": grid.skipped,
        "rows": rows,
    }


# ---------------------------------------------------------------------------------------------
# The CLI. Everything below is for a human reading a finished run.
# ---------------------------------------------------------------------------------------------


def _print_top_rows(rows, limit: int) -> None:
    """Print the strongest rows FOR READING. Private, and returns None.

    Both properties are the guard. Private, so no module can import it; returning None, so there is
    no value for a caller to take a `[0]` from. `tests/signals/test_sweep.py` asserts the module's
    public surface exposes no selection accessor, and this is the function that would be one if it
    were public.
    """
    passing = [row for row in rows if row.passes_gate]
    population = passing if passing else [row for row in rows if row.q_value is not None]
    population = sorted(population, key=lambda row: row.q_value)[:limit]

    if not population:
        print("\n  No row in this run produced an adjusted q-value.\n")
        return

    heading = (
        f"THE {len(population)} STRONGEST OF {len(passing)} PASSING ROWS"
        if passing
        else f"NO ROW PASSED THE GATE. The {len(population)} smallest q-values, for reading only"
    )
    print(f"\n  {heading} - THE TOP OF A DISTRIBUTION, NOT A LIST OF FINDINGS:\n")
    print(
        f"    {'feature':<16} {'site':<10} {'hz':>3} {'lag':>4} {'regime':<9} "
        f"{'stat':>7} {'q':>9} {'n_eff':>7} {'folds':>5} {'consist':>7}"
    )
    for row in population:
        consistency = (
            "-" if row.directional_consistency is None else f"{row.directional_consistency:.2f}"
        )
        print(
            f"    {row.pair.feature_name:<16} {row.pair.site_id:<10} "
            f"{row.pair.horizon_days:>3} {row.pair.lag_days:>4} {row.pair.regime:<9} "
            f"{row.statistic:>7.3f} {row.q_value:>9.5f} {row.n_effective:>7.1f} "
            f"{row.folds if row.folds is not None else '-':>5} {consistency:>7}"
        )
    print(
        "\n    Nothing downstream reads this ordering. Selection is Phase 7's job, under "
        "CLAUDE.md § 7's confidence gate.\n"
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan every (feature, site, target, horizon, lag, regime) combination and record all "
            "of them, including the null results. Measures; never selects."
        )
    )
    parser.add_argument("--lag-min", type=int, default=pairs_module.DEFAULT_LAG_MIN)
    parser.add_argument("--lag-max", type=int, default=pairs_module.DEFAULT_LAG_MAX)
    parser.add_argument(
        "--feature",
        default=None,
        help=(
            "glob restricting which registry features are scanned, e.g. 'days_below_*'. Recorded "
            "on the run: a filtered sweep's q-values are adjusted across a smaller grid and are "
            "NOT comparable to a full sweep's."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="how many of the strongest rows to print for reading (default 20)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:  # pragma: no cover - the live-verification path
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    import os

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    started = datetime.now(timezone.utc)
    with session.writing() as conn:
        result = run(
            conn,
            lag_min=args.lag_min,
            lag_max=args.lag_max,
            feature_filter=args.feature,
        )
    elapsed = datetime.now(timezone.utc) - started

    print(f"\n  run_id {result['run_id']}  -  {elapsed}")
    print(f"  commit {result['git_sha'][:12]}{'  *** WORKING TREE DIRTY ***' if result['git_dirty'] else ''}")

    if result["git_dirty"]:
        print(
            "\n  The working tree carried uncommitted changes, so this run's commit does NOT\n"
            "  identify the code that produced it. The results are still recorded and they are\n"
            "  NOT reproducible. signal_runs.git_dirty is true for this row."
        )

    # THE DENOMINATOR, STATED FIRST AND ON THE SAME LINE. A passing count printed alone is the
    # dishonest form of this result, so the format makes them inseparable.
    print(
        f"\n  {result['passing']} of {result['grid_size']} scanned pairs pass the gate "
        f"(q <= {GATE_MAX_Q_VALUE}, consistency >= {GATE_MIN_DIRECTIONAL_CONSISTENCY}, "
        f"folds >= {GATE_MIN_FOLDS})"
    )

    chance = result["n_tests_adjusted"] * GATE_MAX_Q_VALUE
    print(
        f"  {result['n_tests_adjusted']} pair(s) produced a p-value; at alpha "
        f"{GATE_MAX_Q_VALUE} roughly {chance:.0f} of those would clear an UNADJUSTED threshold on "
        f"pure noise."
    )
    print(
        f"  refused: {result['insufficient_observations']} insufficient_observations, "
        f"{result['insufficient_folds']} insufficient_folds"
    )
    print(
        f"  passing by lag sign: {result['negative_lag_passing']} negative, "
        f"{result['positive_lag_passing']} positive"
    )
    if result["negative_lag_passing"] > result["positive_lag_passing"]:
        print(
            "\n  NEGATIVE LAGS DOMINATE. The target moved before the feature more often than\n"
            "  after it, which changes the claim from 'the physical signal leads' to 'the market\n"
            "  prices the forecast'. That is a finding about the world; report it either way."
        )

    for skip in result["skipped"]:
        logger.info("skipped %s at %s: %s", skip.feature_name, skip.site_id, skip.reason)
    if result["skipped"]:
        skipped_names = sorted({s.feature_name for s in result["skipped"]})
        print(
            f"\n  {len(result['skipped'])} (feature, site) pair(s) skipped as duplicates "
            f"[{', '.join(skipped_names)}] - see the log lines above for the measured reason."
        )

    _print_top_rows(result["rows"], args.top)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
