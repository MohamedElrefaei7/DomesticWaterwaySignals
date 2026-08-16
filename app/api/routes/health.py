"""`/api/health`. PER-JOB, CADENCE-AWARE, DATA-MEASURED, 200 WHILE DEGRADED, NEVER CACHED.

WHAT THIS ENDPOINT IS NOT
--------------------------
It is not `{"status": "ok"}`. A one-word health check is what let the prior project record
"Completed" while the whole stack had been down for two and a half months (CLAUDE.md § 2). The
check ran, the check passed, and the check was measuring whether the check could run.

So this reports, per job: the last SUCCESSFUL run's `finished_at`, whether that is overdue against
THAT JOB'S OWN `overdue_after`, and separately, per registered table, how old the newest row is
against that table's own `max_staleness`.

WHY 200 AND NOT 503
--------------------
An uptime monitor that goes red on a stale ingest job is indistinguishable from one that goes red
because the API is down, AND THE TWO NEED DIFFERENT RESPONSES. "USDA has not published in eleven
days" is a Monday-morning email; "the API is not answering" is a page. Collapsing them into one
signal means the page fires for the email's cause, and after the third time it fires for that
cause it is muted - so the next real outage is silent. `degraded` is a field precisely so a monitor
can alert on the field and keep the status code meaning what it says.

WHY IT IS NEVER CACHED
-----------------------
Health is read when somebody suspects something is wrong. A 60-second cache would answer that
suspicion with the state of the world before it, and the answer would look identical to a live one.
`test_health_is_never_cached` changes the database between two calls and asserts the second moved.

NOTHING HERE COMPUTES A THRESHOLD
----------------------------------
`heartbeat.check` and `heartbeat.check_freshness` do all of it, reading the cadence table and the
freshness registry AT CALL TIME. This module maps their verdicts onto JSON and computes exactly one
thing: `degraded`, which is an OR over what they said.

That matters more here than anywhere else in this package. The cadence table is the single source
of truth for overdue thresholds (CLAUDE.md § 4), and the natural way to write a health endpoint is
`if age > timedelta(hours=2)` right where the comparison happens. It reads fine, it is local, and
it is a second table of the same fact - which diverges silently and produces a health endpoint
reporting healthy about a job the heartbeat is alerting on.

THIS ENDPOINT NEVER WRITES AND NEVER FAILS BECAUSE ONE JOB FAILED. A job that has never succeeded
is reported overdue; a registered table that cannot be queried is reported as a failed check with
its error class named. Neither raises out of this function.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from app.api import models
from app.api.dependencies import get_connection, now as now_dependency
from app.orchestration import heartbeat

router = APIRouter(prefix="/api", tags=["health"])


def _seconds(delta) -> float | None:
    """A timedelta as seconds, or None. NONE IS NOT ZERO HERE.

    A job with no successful run on record has no age. Emitting 0 would say "it succeeded just
    now", which is the exact inversion of what a NULL last_success means - and it is the most
    alarming state in the table (CLAUDE.md § 12), so inverting it is the worst available default.
    """
    return None if delta is None else delta.total_seconds()


def _error_class(error: str | None) -> str | None:
    """The exception's CLASS NAME only, never its message.

    The heartbeat records `f"{type(exc).__name__}: {exc}"` for its own log, where the full detail
    belongs. Across an HTTP boundary the message is where a table name, a column, a role name or a
    connection string leaks (errors.py), so only the part before the colon crosses - which still
    distinguishes `UndefinedTable` from `InsufficientPrivilege`, which is the actionable half.
    """
    if error is None:
        return None
    return error.split(":", 1)[0].strip() or None


@router.get(
    "/health",
    response_model=models.HealthResponse,
    summary="Per-job and per-table health. 200 even when degraded.",
)
def get_health(
    conn=Depends(get_connection),
    checked_at: datetime = Depends(now_dependency),
) -> models.HealthResponse:
    """Read the cadence table, `job_runs`, and the freshness registry. Report all three."""
    job_verdicts = heartbeat.check(conn, now=checked_at)
    freshness_verdicts = heartbeat.check_freshness(conn, now=checked_at)

    jobs = [
        models.JobHealth(
            job_name=verdict.job_name,
            last_success=verdict.last_success,
            age_seconds=_seconds(verdict.age),
            overdue_after_seconds=verdict.overdue_after.total_seconds(),
            overdue=verdict.overdue,
        )
        for verdict in job_verdicts
    ]

    data = [
        models.TableFreshness(
            table=verdict.table,
            job_name=verdict.job_name,
            newest=verdict.newest,
            age_seconds=_seconds(verdict.age),
            max_staleness_seconds=verdict.max_staleness.total_seconds(),
            stale=verdict.stale,
            error=_error_class(verdict.error),
        )
        for verdict in freshness_verdicts
    ]

    # The ONLY computation in this module, and it is an OR over verdicts somebody else reached.
    # Both halves are in it: a job can be running perfectly while the table it writes goes quiet,
    # which is the failure the freshness registry exists to catch and which no job-status field can
    # see (CLAUDE.md § 4).
    degraded = any(job.overdue for job in jobs) or any(table.stale for table in data)

    return models.HealthResponse(
        degraded=degraded,
        checked_at=checked_at,
        jobs=jobs,
        data=data,
    )
