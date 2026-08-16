"""`/api/signals` and `/api/signals/runs`. THE DENOMINATOR IS THE DEFAULT VIEW.

`passing_only` DEFAULTS TO FALSE, AND THAT IS THE DECISION IN THIS FILE
------------------------------------------------------------------------
CLAUDE.md § 18: a grid of ~7,000 tests at α = 0.05 produces ~350 significant results on pure noise.
Not through a bug - by construction, on random data, every time. `signals` records EVERY scanned
combination, including the null results and the refusals, because the table of scanned pairs IS the
multiple-comparisons record.

An endpoint defaulting to `passing_only=true` would undo that at the last possible moment. The
filter would happen at read time, leave no trace of itself, and hand a frontend twelve rows in a
table of twelve - which reads as twelve findings. The same twelve in a table of 6,966 read as the
top of a distribution, and the reader can see that chance alone would have produced three hundred
and fifty. Nobody has to delete anything for this to go wrong.

Measured, on this project's own data: **1 of 6,966 pairs passes.** A default that hid the 6,965
would turn that into "we found a signal."

`grid_size` RIDES ON EVERY ROW
-------------------------------
A q-value is meaningless without knowing how many tests it was adjusted against. A later run over a
narrower grid produces smaller q-values in the same column, in the same units, from a different
experiment - so `grid_size` is denormalized onto each row in the database and it is emitted with
each row here. The envelope's `run` block carries the run's scanned and passing counts beside it.

`passes_gate` IS READ, NEVER RECOMPUTED. The sweep computes and stores it precisely so consumers
filter rather than the writer selecting (migration 0023). A WHERE clause on a stored boolean is not
a second implementation of the gate; an `AND q_value < 0.05` in this file would be.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.api import models
from app.api.cache import SIGNALS_CACHE, key_from_request
from app.api.dependencies import (
    Page,
    get_connection,
    latest_run_id,
    page,
    run_summary,
)
from app.api.errors import NOT_FOUND, ApiError

router = APIRouter(prefix="/api", tags=["signals"])


SIGNALS_SQL = """
SELECT run_id, feature_name, site_id, series_column, target_name, horizon_days, lag_days, regime,
       status, statistic, p_value, q_value, grid_size, n_tests_adjusted, n_observations,
       n_effective, folds, directional_consistency, passes_gate
  FROM signals
 WHERE run_id = %(run_id)s
   AND (NOT %(passing_only)s::boolean OR passes_gate)
 ORDER BY q_value NULLS LAST, feature_name, site_id, horizon_days, lag_days, regime
 LIMIT %(limit)s OFFSET %(offset)s
"""

SIGNALS_COUNT_SQL = """
SELECT count(*)
  FROM signals
 WHERE run_id = %(run_id)s
   AND (NOT %(passing_only)s::boolean OR passes_gate)
"""

RUNS_SQL = """
SELECT r.run_id, r.started_at, r.finished_at, r.grid_size, r.lag_min, r.lag_max, r.horizons,
       r.regimes, r.feature_filter, r.git_sha, r.git_dirty, r.seed,
       count(s.run_id)                        AS scanned_pairs,
       count(*) FILTER (WHERE s.passes_gate)  AS passing_pairs
  FROM signal_runs r
  LEFT JOIN signals s ON s.run_id = r.run_id
 GROUP BY r.run_id
 ORDER BY r.run_id DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""

RUNS_COUNT_SQL = "SELECT count(*) FROM signal_runs"


def _run(row) -> models.SignalRun:
    """One `signal_runs` row plus its two counts, in the column order both queries above share."""
    return models.SignalRun(
        run_id=row[0],
        started_at=row[1],
        finished_at=row[2],
        grid_size=row[3],
        lag_min=row[4],
        lag_max=row[5],
        horizons=list(row[6]),
        regimes=list(row[7]),
        feature_filter=row[8],
        git_sha=row[9],
        git_dirty=row[10],
        seed=row[11],
        scanned_pairs=row[12],
        passing_pairs=row[13],
    )


def _signals(conn, *, run_id, passing_only: bool, bound: Page, computed_at: datetime):
    """One page of scanned combinations, with the run they came from on the envelope."""
    resolved = run_id if run_id is not None else latest_run_id(conn)
    if resolved is None:
        # No sweep has ever run. An empty page with a null run, NOT a 404: the question "what did
        # the sweep find" has an honest answer here, and it is "nothing has been scanned".
        return models.SignalList(
            limit=bound.limit,
            offset=bound.offset,
            total=0,
            run=None,
            passing_only=passing_only,
            computed_at=computed_at,
            rows=[],
        )

    summary = run_summary(conn, resolved)
    if summary is None:
        raise ApiError(
            NOT_FOUND,
            f"No sweep run {resolved}. See /api/signals/runs for the runs on record.",
            status_code=404,
        )

    params = {
        "run_id": resolved,
        "passing_only": passing_only,
        "limit": bound.limit,
        "offset": bound.offset,
    }
    total = conn.execute(SIGNALS_COUNT_SQL, params).fetchone()[0]
    rows = conn.execute(SIGNALS_SQL, params).fetchall()

    return models.SignalList(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        run=_run(summary),
        passing_only=passing_only,
        computed_at=computed_at,
        rows=[
            models.Signal(
                run_id=row[0],
                feature_name=row[1],
                site_id=row[2],
                series_column=row[3],
                target_name=row[4],
                horizon_days=row[5],
                lag_days=row[6],
                regime=row[7],
                status=row[8],
                statistic=row[9],
                p_value=row[10],
                q_value=row[11],
                grid_size=row[12],
                n_tests_adjusted=row[13],
                n_observations=row[14],
                n_effective=row[15],
                folds=row[16],
                directional_consistency=row[17],
                passes_gate=row[18],
            )
            for row in rows
        ],
    )


@router.get(
    "/signals",
    response_model=models.SignalList,
    summary="Scanned combinations from one sweep run. ALL of them, unless you ask otherwise.",
)
def get_signals(
    request: Request,
    conn=Depends(get_connection),
    bound: Page = Depends(page),
    run_id: int | None = Query(
        None,
        description=(
            "The sweep run to read. Defaults to the MOST RECENT run - most recent rather than "
            "best, because taking the run with the friendliest q-values would be model selection "
            "performed by the consumer."
        ),
    ),
    passing_only: bool = Query(
        False,
        description=(
            "Restrict to rows that passed the sweep's gate. DEFAULTS TO FALSE: the scanned rows "
            "are the multiple-comparisons denominator, and 1 passing row reads very differently "
            "from 1 of 6,966."
        ),
    ),
):
    """Cached for 60 seconds on the full query string, so `passing_only` and `run_id` key it."""
    cached = SIGNALS_CACHE.get_or_compute(
        key_from_request(request),
        lambda computed_at: _signals(
            conn,
            run_id=run_id,
            passing_only=passing_only,
            bound=bound,
            computed_at=computed_at,
        ),
    )
    return cached.value


@router.get(
    "/signals/runs",
    response_model=models.SignalRunList,
    summary="The sweep runs on record, newest first, each with its scanned and passing counts.",
)
def get_signal_runs(
    conn=Depends(get_connection), bound: Page = Depends(page)
) -> models.SignalRunList:
    """Not cached: it is a small listing, and a run appearing is exactly what somebody polls for."""
    total = conn.execute(RUNS_COUNT_SQL).fetchone()[0]
    rows = conn.execute(
        RUNS_SQL, {"limit": bound.limit, "offset": bound.offset}
    ).fetchall()

    return models.SignalRunList(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        rows=[_run(row) for row in rows],
    )
