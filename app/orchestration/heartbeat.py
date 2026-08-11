"""The heartbeat: the job that reports on the other jobs.

CLAUDE.md § 12 decisions 13, 16, 17, and 18.

WHAT THIS CHECKS, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
It checks job overdue-ness: for each entry in the cadence table, how long since that job's most
recent SUCCESSFUL run, measured against that entry's own overdue_after.

It does NOT check data liveness, and the omission is deliberate rather than pending. CLAUDE.md § 4
requires liveness measured from the data — MAX(ts) on the ingested table — never from the process,
because a source that accepts your connection and delivers nothing is indistinguishable from a
healthy one at every layer except the data. No ingested table exists yet. Building an empty
freshness registry now would produce a check that iterates over nothing, finds nothing wrong, and
reports healthy — a monitor whose green light means "I looked at zero things." That is the exact
failure this project is trying not to repeat.

So the requirement is written into the contract instead of into empty code: CLAUDE.md § 12 says no
ingest client is complete until it registers its table in the heartbeat's freshness registry. The
registry gets built in Phase 3, by the commit that has something to put in it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import db
from app.orchestration import cadence as cadence_module
from app.orchestration.job import job

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verdict:
    """One job's state, as of one heartbeat run."""

    job_name: str
    last_success: datetime | None
    age: timedelta | None
    overdue_after: timedelta
    overdue: bool

    def describe(self) -> str:
        if self.last_success is None:
            return (
                f"{self.job_name}: NO SUCCESSFUL RUN ON RECORD "
                f"(threshold {self.overdue_after})"
            )
        state = "OVERDUE" if self.overdue else "ok"
        return (
            f"{self.job_name}: {state} - last success {self.last_success.isoformat()} "
            f"({self.age} ago, threshold {self.overdue_after})"
        )


def log_sink(message: str) -> None:
    """The default alert sink: stdout via the logger.

    Slack is a webhook URL, which is a secret (CLAUDE.md § 1) and a later commit. Every sink has
    this signature, so swapping this one for a real transport changes one argument and nothing
    else.
    """
    logger.warning("HEARTBEAT ALERT: %s", message)


def _emit(sink, message: str) -> None:
    """Call the sink, and swallow anything it raises. Decision 18.

    THIS IS THE ONE PLACE IN THIS COMMIT WHERE SWALLOWING AN EXCEPTION IS CORRECT, and it
    contradicts the @job decorator three files away, which never swallows and says so at length.
    The comment is here because the contradiction is real and someone will otherwise "fix" one of
    the two to match the other.

    The difference is what the exception means. In @job, an exception means the work failed, and
    the scheduler and the operator both need to know. Here, an exception means the REPORT ABOUT
    the work could not be delivered — Slack is down, the network is out, the webhook rotated. The
    jobs it was reporting on are unaffected. A monitoring job that fails because it could not
    report is a monitoring job that stops monitoring, and it stops precisely during the kind of
    broad outage where monitoring matters most.

    The failure is logged, so it is not invisible; it just is not fatal.
    """
    try:
        sink(message)
    except Exception:
        logger.exception(
            "alert sink raised; the heartbeat is continuing deliberately (CLAUDE.md § 12). "
            "THE ALERT BELOW WAS NOT DELIVERED: %s",
            message,
        )


def last_success(conn, job_name: str) -> datetime | None:
    """The most recent SUCCESS row's finished_at. Decision 16.

    `status = 'success'` is the entire content of this function. Dropping it — taking
    MAX(finished_at) across all statuses — turns this into "last activity", and a job that has
    failed every night for a week has plenty of recent activity and no recent success. The query
    would report it healthy (CLAUDE.md § 4).

    Backed by the partial index in 0003_job_runs_success_index.sql, which is partial on exactly
    this predicate.
    """
    row = conn.execute(
        "SELECT max(finished_at) FROM job_runs WHERE job_name = %s AND status = 'success'",
        (job_name,),
    ).fetchone()
    return row[0] if row else None


def check(conn, now: datetime | None = None, cadences=None) -> list[Verdict]:
    """One verdict per cadence entry.

    `cadences` defaults to the live cadence table, READ AT CALL TIME rather than captured at
    import. That is what lets the test for decision 13 mutate an entry and observe this function's
    verdict follow it — a module-level `from cadence import CADENCES` binding would freeze the
    value at import and the guard would be untestable, which in practice means unguarded.
    """
    now = now or datetime.now(timezone.utc)
    cadences = cadence_module.CADENCES if cadences is None else cadences

    verdicts = []
    for entry in cadences:
        succeeded_at = last_success(conn, entry.job_name)
        age = None if succeeded_at is None else now - succeeded_at
        verdicts.append(
            Verdict(
                job_name=entry.job_name,
                last_success=succeeded_at,
                age=age,
                # The threshold comes from the cadence entry and from nowhere else. Decision 13.
                overdue_after=entry.overdue_after,
                # No successful run on record counts as overdue. A job that has never succeeded is
                # not a job that is fine; it is the most alarming state in the table, and treating
                # a NULL as "nothing to report" is how a job that never once worked stays quiet.
                overdue=(age is None or age > entry.overdue_after),
            )
        )
    return verdicts


@job("heartbeat")
def heartbeat_job(sink=None, url: str | None = None, now: datetime | None = None):
    """The scheduled unit. Returns None: it writes no rows, so it reports no row count.

    None rather than 0 is the point of decision 9 applied to itself. 0 would claim this job wrote
    zero rows to the database; None says it is not a job that counts rows. The bookkeeping row
    @job writes is not this job's output.
    """
    sink = log_sink if sink is None else sink

    with db.connection(url) as conn:
        verdicts = check(conn, now=now)

    overdue = [v for v in verdicts if v.overdue]

    for verdict in verdicts:
        logger.info("%s", verdict.describe())

    if overdue:
        _emit(
            sink,
            f"{len(overdue)} of {len(verdicts)} job(s) overdue:\n"
            + "\n".join(f"  {v.describe()}" for v in overdue),
        )

    return None
