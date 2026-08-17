"""APScheduler wiring: persistent job store, cadence-derived triggers, and the missed-run listener.

CLAUDE.md § 12 decisions 12, 14, and 15.

A warning that belongs at the top of this file rather than buried next to the setting it concerns:
NOTHING IN THIS MODULE'S CONFIGURATION TESTS PROVES RESTART RECOVERY WORKS. Asserting that the job
store is a SQLAlchemyJobStore, that coalesce is True, and that misfire_grace_time is the
cadence-derived value is asserting three settings. The prior project had ten green scheduler tests
asserting exactly these settings while restart recovery did not work, because the behaviour lived
in process lifetime and not in any value a test could read (CLAUDE.md § 2, theme 2).

That is not a hypothetical here. THE FIRST VERSION OF THIS FILE HAD THE SAME BUG, and all three
configuration tests were green while it did. It registered every job with `replace_existing=True`,
which is the form every APScheduler example uses. On startup `_real_add_job` computes a fresh
`next_run_time` from now and `update_job` writes it over the persisted one — so a restart after an
outage silently discarded the past-due fire time, resumed on a clean schedule, caught nothing up,
and recorded no miss. Measured: stopped at 00:53:15 with a 20-second interval, restarted at
00:54:05, and the next run was scheduled for 00:54:25 — a full fresh interval later, with no
`missed` row. The persistent job store was doing its job; this module was overwriting its answer.

The fix is `register_jobs()` below: reconcile against what the store already holds rather than
overwriting it, so a past-due `next_run_time` survives into the new process and APScheduler's own
misfire handling gets to see it. `test_a_past_due_next_run_time_survives_a_restart` guards it
behaviourally, and live verification step 7 is still the real evidence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import db
from app.features import build as features_build
from app.ingest import usda_movements, usda_rates, usgs_daily_ingest, usgs_ingest
from app.orchestration import backup, heartbeat, restore_test, session
from app.orchestration.cadence import CADENCES

logger = logging.getLogger(__name__)

# CLAUDE.md § 12 / CONTEXT.md § Housekeeping: this table must be EXCLUDED from dumps when backups
# land in Phase 11. Restoring a stale scheduler state is worse than restoring none — the restored
# rows carry next_run_time values from whenever the dump was taken, and a job store full of
# long-past fire times interacts with coalesce and misfire grace in ways nobody reasoned about.
JOBSTORE_TABLE = "apscheduler_jobs"

# job_name -> the callable the scheduler runs. Kept here rather than on the Cadence record so that
# cadence.py stays a pure data table with no imports of its own; heartbeat.py imports cadence, and
# putting the callable on the record would make that circular.
#
# These must be module-level functions. SQLAlchemyJobStore serializes jobs by import path, so a
# lambda or a closure is unstorable and fails at add_job time — which is a good failure, but only
# if you know to expect it.
JOB_FUNCTIONS = {
    "heartbeat": heartbeat.heartbeat_job,
    "usgs_ingest": usgs_ingest.usgs_ingest_job,
    "usgs_daily_ingest": usgs_daily_ingest.usgs_daily_ingest_job,
    # TWO ENTRIES FOR THE TWO USDA DATASETS, not one covering both. See their cadence entries: one
    # job over two sources produces one job_runs row whose status is the AND of two independent
    # things, and the heartbeat then cannot say which source went quiet.
    "usda_rates_ingest": usda_rates.usda_rates_ingest_job,
    "usda_movements_ingest": usda_movements.usda_movements_ingest_job,
    # THE FIRST NON-INGEST JOB. It reads what the four above have landed and writes the derived
    # layer. Nothing orders it against them - see its cadence entry.
    "features_build": features_build.features_build_job,
    # THE FIRST JOB THAT WRITES NOTHING TO THIS DATABASE'S DATA TABLES. It returns None, so
    # rows_written is NULL rather than 0 - a backup writes no rows, and 0 would claim it counts
    # them and today counted none (CLAUDE.md § 4).
    "backup_nightly": backup.backup_nightly_job,
    # A BACKUP NOBODY HAS RESTORED IS A BACKUP NOBODY KNOWS THEY HAVE. The nightly job proves an
    # archive can be READ; this one proves it can be restored INTO A DATABASE, which is a different
    # claim - extension state, hypertable metadata, roles and grants all live outside the block
    # stream that pg_restore -f /dev/null walks.
    "restore_test_monthly": restore_test.restore_test_monthly_job,
}

# NOT REGISTERED HERE, DELIBERATELY: app/ingest/backfill.py, app/ingest/daily_backfill.py and
# app/ingest/usda_backfill.py.
# Both are CLIs a human runs, both take hours, and max_instances=1 would leave a scheduled copy
# permanently "running" rather than either working or broken — a job the heartbeat cannot
# distinguish from a healthy one.


class SchedulerConfigurationError(RuntimeError):
    """The cadence table and the function registry disagree."""


def _on_job_missed(event, url: str | None = None) -> None:
    """Write the `missed` row. Decision 12.

    A misfired job NEVER INVOKES THE WRAPPED FUNCTION, so the @job decorator cannot record it —
    the decorator only exists inside a call that never happens. Without this listener a missed run
    is invisible and, worse, indistinguishable from a job that was never scheduled: both look like
    a job with no activity (CLAUDE.md § 4).

    started_at is the slot the run was scheduled for, not now. The row's purpose is to mark that
    that slot produced nothing, and stamping it with the discovery time would make a run missed
    during a four-hour outage look like it was missed just now.

    This runs on APScheduler's own dispatch path, so it must not raise: an exception here would
    propagate into the scheduler's event loop, and the failure mode of a monitoring hook that
    takes down the scheduler is considerably worse than the gap it was reporting.
    """
    scheduled_for = getattr(event, "scheduled_run_time", None) or datetime.now(timezone.utc)
    try:
        with session.writing(url) as conn:
            conn.execute(
                "INSERT INTO job_runs (job_name, started_at, finished_at, status, error_message)"
                " VALUES (%s, %s, %s, 'missed', %s)",
                (
                    event.job_id,
                    scheduled_for,
                    datetime.now(timezone.utc),
                    f"misfired: scheduled for {scheduled_for.isoformat()}, not run within the "
                    f"job's misfire grace window",
                ),
            )
        logger.warning("job %r missed its run scheduled for %s", event.job_id, scheduled_for)
    except Exception:
        logger.exception(
            "could not record a missed run for job %r scheduled at %s. THE MISS STILL HAPPENED - "
            "it is simply not in job_runs.",
            event.job_id,
            scheduled_for,
        )


def job_kwargs(cadence) -> dict:
    """The APScheduler settings for one cadence entry. One definition, used by both code paths.

    Split out so that adding a job and reconciling an existing one cannot drift apart — a job
    created with one misfire grace and updated with another would behave differently depending on
    whether the process had ever run before, which is the least debuggable class of bug this
    module can produce.
    """
    return {
        "func": JOB_FUNCTIONS[cadence.job_name],
        "trigger": IntervalTrigger(seconds=cadence.interval_seconds, timezone="UTC"),
        # Decision 14. After a four-hour outage, catch up ONCE, promptly — rather than firing
        # sixteen times in a row against a source that will rate-limit us for it.
        "coalesce": True,
        # Decision 14. Never the library default of one second.
        "misfire_grace_time": cadence.misfire_grace_time,
        # A job still running when its next fire time arrives does not get a second copy. Without
        # this, one slow backfill turns into overlapping backfills.
        "max_instances": 1,
    }


def register_jobs(scheduler) -> None:
    """Reconcile the cadence table into a STARTED scheduler without destroying its schedule.

    This is the restart-recovery fix, and the reason it cannot be `add_job(replace_existing=True)`:

      add_job() computes next_run_time fresh from now whenever the job carries none, and
      replace_existing then writes that over whatever the job store had persisted. After an
      outage, the persisted value is a fire time in the past — exactly the value APScheduler's
      misfire handling needs in order to notice that a run was due. Overwriting it means the
      scheduler never learns a run was missed: it does not catch up, it does not record a miss,
      it just quietly resumes. Every setting still reads correctly while it happens.

    So: add the job only if the store has never seen it, and otherwise modify it in place.
    modify_job() changes the trigger and settings WITHOUT touching next_run_time, so a cadence
    change still takes effect on the next restart while a past-due fire time survives to be
    handled as the misfire it is.

    The scheduler must already be started (paused is fine) — the job store is not queryable
    before that, since it has no connection yet.
    """
    for cadence in CADENCES:
        kwargs = job_kwargs(cadence)
        if scheduler.get_job(cadence.job_name) is None:
            scheduler.add_job(id=cadence.job_name, name=cadence.job_name, **kwargs)
            logger.info("registered new job %r", cadence.job_name)
        else:
            scheduler.modify_job(cadence.job_name, **kwargs)
            logger.info(
                "reconciled existing job %r, preserving next_run_time=%s",
                cadence.job_name,
                scheduler.get_job(cadence.job_name).next_run_time,
            )


def start(scheduler) -> None:
    """Start paused, reconcile, then resume.

    Paused first so that no job fires against a schedule that register_jobs() is halfway through
    updating; resumed after, at which point APScheduler evaluates every past-due next_run_time
    and applies coalesce and misfire grace to it.
    """
    scheduler.start(paused=True)
    register_jobs(scheduler)
    scheduler.resume()


def build_scheduler(url: str | None = None, *, jobstore_url: str | None = None):
    """Construct the scheduler and its listener. Registers no jobs and does not start it.

    Jobs are registered by register_jobs() after start, because reconciling against the persisted
    schedule requires a queryable job store. Use start() for the whole sequence.

    `url` (psycopg DSN, for job bookkeeping) and `jobstore_url` (SQLAlchemy DSN, for the job store)
    are separate parameters only so tests can supply one without a live database behind the other.
    In production both come from DATABASE_URL.
    """
    missing = [c.job_name for c in CADENCES if c.job_name not in JOB_FUNCTIONS]
    if missing:
        raise SchedulerConfigurationError(
            f"cadence entries with no registered function: {missing}. A cadence entry that never "
            f"fires is reported overdue by the heartbeat forever."
        )
    unscheduled = [name for name in JOB_FUNCTIONS if name not in {c.job_name for c in CADENCES}]
    if unscheduled:
        raise SchedulerConfigurationError(
            f"registered functions with no cadence entry: {unscheduled}. These would never run, "
            f"and nothing would report that they never ran."
        )

    # Decision 15: PERSISTENT job store, against the same Postgres.
    #
    # An in-memory store forgets the schedule on restart, and no configuration test will catch it
    # — the settings all still read correctly. Constructing SQLAlchemyJobStore does not connect;
    # the engine is lazy and the table is created on the scheduler's first start.
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=jobstore_url if jobstore_url is not None else db.sqlalchemy_url(url),
            tablename=JOBSTORE_TABLE,
        )
    }

    scheduler = BackgroundScheduler(jobstores=jobstores, timezone="UTC")

    # Decision 12. Registered before any job exists, so a misfire during startup is still caught.
    scheduler.add_listener(lambda event: _on_job_missed(event, url), EVENT_JOB_MISSED)

    return scheduler


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    """Run the scheduler in the foreground until interrupted.

    Phase 2 runs this from a host venv. Containerizing it as the `worker` service is a later
    commit, and will need its own restart-recovery verification — being inside a container with
    `restart: unless-stopped` changes the process lifetime this whole design is about.
    """
    import signal
    import threading

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    scheduler = build_scheduler()
    start(scheduler)
    logger.info(
        "scheduler started with %d job(s): %s",
        len(CADENCES),
        ", ".join(f"{c.job_name} every {c.interval} (grace {c.misfire_grace_time}s)" for c in CADENCES),
    )

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()

    logger.info("shutting down; waiting for running jobs to finish")
    scheduler.shutdown(wait=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
