"""The heartbeat: the job that reports on the other jobs, and on the data they produce.

CLAUDE.md § 12 decisions 13, 16, 17, and 18.

TWO CHECKS, AND THE SECOND IS THE ONE THAT CATCHES THE HARD FAILURE
-------------------------------------------------------------------
JOB OVERDUE-NESS. For each entry in the cadence table, how long since that job's most recent
SUCCESSFUL run, measured against that entry's own overdue_after.

DATA FRESHNESS. For each entry in the freshness registry below, how long since the newest row in
the ingested table, measured against that entry's own max_staleness.

The second exists because the first cannot see the failure that matters most. CLAUDE.md § 4:
liveness is measured from the data, never from the process — a source that accepts your
connection and delivers nothing is indistinguishable from a healthy one at every layer except
the data. That is not hypothetical for this ingest: MEASURED ON 2026-08-13, a USGS request for a
series a site does not serve returns HTTP 200 WITH AN EMPTY ARRAY. The client hard-fails on that
(app/ingest/usgs_client.py), but the general shape — a source that answers cheerfully and sends
nothing — is exactly what a job-status check reports as healthy. Successful job_runs rows, recent
activity, no errors, and a table whose newest row is four days old.

tests/orchestration/test_heartbeat.py::test_a_job_with_recent_runs_but_stale_data_is_still_flagged
is that scenario written down.

Phase 2 deliberately shipped NO registry rather than an empty one: a check that iterates over
nothing, finds nothing wrong, and reports healthy is a monitor whose green light means "I looked
at zero things." The registry is built here, in the commit that has something to put in it, which
is what CLAUDE.md § 12 requires of every ingest client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import db
from app.orchestration import cadence as cadence_module
from app.orchestration.job import job

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------------------------
# The freshness registry.
# ---------------------------------------------------------------------------------------------
#
# WHY THIS IS NOT IN cadence.py, given how firmly § 12 says the cadence table is the single
# source of truth: it is a different fact about a different thing. The cadence table answers "how
# often should this job fire, and how long may it go without succeeding" — a statement about a
# PROCESS. This answers "how old may this table's newest row be" — a statement about DATA. They
# are related but not derivable from one another: a job can succeed on schedule while its source
# sends nothing, which is the entire reason this registry exists.
#
# What IS enforced is that the two cannot drift apart in the way that matters: every entry here
# must name a job that the cadence table schedules, checked at import below. A freshness entry
# for a job nothing runs would report a stale table forever with no way to fix it.


@dataclass(frozen=True)
class Freshness:
    """One ingested table's liveness contract.

    job_name       The job that writes this table. Must exist in the cadence table.
    table          The ingested table.
    timestamp_column  The column carrying the SOURCE's timestamp — not an inserted_at. An
                   inserted_at column measures that we wrote something, which is the process
                   measurement this check exists to replace.
    max_staleness  How old the newest row may be before this is stale.
    """

    job_name: str
    table: str
    timestamp_column: str
    max_staleness: timedelta


FRESHNESS: tuple[Freshness, ...] = (
    Freshness(
        job_name="usgs_ingest",
        table="gauge_readings",
        timestamp_column="ts",
        # SIX HOURS, AND GENEROUS RELATIVE TO THE HOURLY POLL ON PURPOSE.
        #
        # The arithmetic behind the number: two of the four seeded sites record hourly, and USGS
        # transmits hourly rather than continuously, so a MAX(ts) that is already one to two
        # hours behind wall clock is the NORMAL steady state, not a symptom. Add the poll's own
        # hourly interval and a late transmission, and three to four hours old is still healthy.
        #
        # Six hours means roughly four consecutive failed polls, or a genuine upstream outage,
        # before this speaks. Tighter and it would fire on ordinary USGS lateness — and an alert
        # that fires routinely is an alert everyone mutes, which costs more than the delay.
        max_staleness=timedelta(hours=6),
    ),
)


_unscheduled = [f.job_name for f in FRESHNESS if f.job_name not in cadence_module.BY_NAME]
if _unscheduled:  # pragma: no cover - a drift would be caught at import
    raise ValueError(
        f"freshness registry names job(s) with no cadence entry: {_unscheduled}. Nothing would "
        f"ever write those tables, so they would be reported stale forever with no way to fix it."
    )


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


@dataclass(frozen=True)
class FreshnessVerdict:
    """One ingested table's state, as of one heartbeat run."""

    job_name: str
    table: str
    newest: datetime | None
    age: timedelta | None
    max_staleness: timedelta
    stale: bool
    error: str | None = None

    def describe(self) -> str:
        if self.error is not None:
            return f"{self.table}: CANNOT BE CHECKED - {self.error}"
        if self.newest is None:
            return (
                f"{self.table}: EMPTY - no rows at all "
                f"(threshold {self.max_staleness}, written by {self.job_name})"
            )
        state = "STALE" if self.stale else "fresh"
        return (
            f"{self.table}: {state} - newest row {self.newest.isoformat()} "
            f"({self.age} old, threshold {self.max_staleness})"
        )


def newest_row(conn, entry: Freshness) -> datetime | None:
    """MAX(ts) on the ingested table. The measurement this whole check is about.

    The table and column are interpolated rather than parameterized because they are identifiers,
    which SQL parameters cannot carry. They come from the frozen registry above — module
    constants, never user input — and the identifier check below is a belt-and-braces guard so
    that a future registry built from configuration cannot turn this into an injection point.
    """
    if not entry.table.isidentifier() or not entry.timestamp_column.isidentifier():
        raise ValueError(
            f"freshness entry {entry.job_name!r} has a non-identifier table/column "
            f"({entry.table!r}, {entry.timestamp_column!r})"
        )
    row = conn.execute(
        f"SELECT max({entry.timestamp_column}) FROM {entry.table}"
    ).fetchone()
    return row[0] if row else None


def check_freshness(conn, now: datetime | None = None, registry=None) -> list[FreshnessVerdict]:
    """One verdict per freshness entry. Liveness measured from the DATA.

    `registry` defaults to the live one READ AT CALL TIME, for the same reason check() reads the
    cadence table at call time: it is what lets a test mutate an entry and observe the verdict
    follow it. A module-level binding would freeze the value at import and the guard would be
    untestable, which in practice means unguarded.

    A TABLE WITH NO ROWS AT ALL IS STALE, NOT QUIET. Same reasoning as a job with no successful
    run being overdue rather than silent: an ingest table that has never received a row is the
    most alarming state it can be in, and treating a NULL MAX(ts) as "nothing to report" is how a
    source that never once delivered stays quiet forever. This DOES mean the heartbeat alerts
    about gauge_readings from the moment 0005 is applied until the backfill puts rows in it —
    expected, once, exactly like the heartbeat's own first run alerting about itself.
    """
    now = now or datetime.now(timezone.utc)
    registry = FRESHNESS if registry is None else registry

    verdicts = []
    for entry in registry:
        error = None
        newest = None
        try:
            newest = newest_row(conn, entry)
        except Exception as exc:
            # A registered table that cannot be queried — it does not exist, the migration was
            # never applied, a permission changed — is reported as a FAILED CHECK and alerts. It
            # is not skipped: CLAUDE.md § 13, a skipped check must never read as green. Rolled
            # back first because psycopg leaves the transaction unusable after an error, and the
            # remaining entries still need to be checked.
            conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("freshness check failed for table %s", entry.table)

        age = None if newest is None else now - newest
        verdicts.append(
            FreshnessVerdict(
                job_name=entry.job_name,
                table=entry.table,
                newest=newest,
                age=age,
                max_staleness=entry.max_staleness,
                # Unqueryable or empty both count as stale. Neither is a healthy table, and
                # neither should need a second kind of alert to be noticed.
                stale=(error is not None or age is None or age > entry.max_staleness),
                error=error,
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
        freshness = check_freshness(conn, now=now)

    overdue = [v for v in verdicts if v.overdue]
    stale = [v for v in freshness if v.stale]

    for verdict in verdicts:
        logger.info("%s", verdict.describe())
    for verdict in freshness:
        logger.info("%s", verdict.describe())

    # ONE ALERT, not one per category. Two sinks' worth of messages for one heartbeat run is two
    # notifications for one event, and a broad outage — which trips both checks at once — would
    # produce exactly that, at the moment the reader can least afford noise.
    problems = []
    if overdue:
        problems.append(
            f"{len(overdue)} of {len(verdicts)} job(s) overdue:\n"
            + "\n".join(f"  {v.describe()}" for v in overdue)
        )
    if stale:
        problems.append(
            f"{len(stale)} of {len(freshness)} ingested table(s) stale:\n"
            + "\n".join(f"  {v.describe()}" for v in stale)
        )

    if problems:
        _emit(sink, "\n".join(problems))

    return None
