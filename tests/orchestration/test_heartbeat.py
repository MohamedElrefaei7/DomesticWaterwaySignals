"""Integration tier — the heartbeat, and the missed-run listener that feeds it.

Covers CLAUDE.md § 12 decisions 12 (missed rows come from a scheduler listener), 13 (the cadence
table is the only source of overdue thresholds), 16 ("last success" means the last SUCCESS row),
and 18 (alert delivery failures never fail the monitoring job).
"""

from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.job import Job
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from app import db
from app.orchestration import cadence as cadence_module
from app.orchestration import heartbeat, scheduler as scheduler_module
from app.orchestration.cadence import Cadence

pytestmark = pytest.mark.integration

FAKE_JOBSTORE_URL = "postgresql+psycopg://does-not-connect/unit"


def test_missed_event_listener_writes_a_missed_row(migrated_db, database_url, job_runs):
    """Decision 12, dispatched through the scheduler rather than by calling the listener directly.

    Calling _on_job_missed() by hand would test the INSERT and nothing else - it would still pass
    with the add_listener() call deleted, which is the mutation that actually makes missed runs
    invisible. Going through _dispatch_event exercises the subscription too.
    """
    scheduler = scheduler_module.build_scheduler(
        url=database_url, jobstore_url=FAKE_JOBSTORE_URL
    )
    scheduled_for = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)

    scheduler._dispatch_event(
        JobExecutionEvent(EVENT_JOB_MISSED, "heartbeat", "default", scheduled_for)
    )

    rows = job_runs.rows("heartbeat")
    assert len(rows) == 1, "no missed row was written - a misfired run left no trace at all"

    row = rows[0]
    assert row["status"] == "missed"
    assert row["started_at"] == scheduled_for, (
        "started_at is not the slot the run was scheduled for - a run missed during a four-hour "
        "outage would look like it was missed just now"
    )
    assert row["rows_written"] is None
    assert "misfired" in row["error_message"]


def test_a_past_due_next_run_time_survives_a_restart(migrated_db, database_url):
    """The restart-recovery guard, and the one test in this repo that would have caught the bug.

    THE BUG THIS EXISTS FOR, because it shipped in this commit's first draft and every
    configuration test stayed green through it: registering jobs with
    `add_job(..., replace_existing=True)` — the form every APScheduler example uses — makes
    _real_add_job compute a fresh next_run_time from now, and update_job then writes it over the
    persisted one. After an outage the past-due fire time is destroyed before APScheduler's
    misfire handling ever sees it, so the job does not catch up, does not record a miss, and just
    quietly resumes on a clean schedule.

    Measured against the real thing rather than asserted about settings: register into a real
    SQLAlchemyJobStore, backdate the stored fire time the way an outage would, then build and
    start a SECOND scheduler against that same store — a different object, the way a restart
    produces a different process — and confirm the past-due time is still there.

    This is still not proof that recovery works end to end; only live verification step 7, with a
    genuinely stopped process, is that. It is proof that the specific mechanism that broke here
    stays fixed.
    """
    jobstore_url = db.sqlalchemy_url(database_url)
    backdated = datetime.now(timezone.utc) - timedelta(hours=4)

    # The post-outage state is written straight into the job store, with no scheduler thread
    # running at any point. That is both deterministic and the more honest model of an outage: a
    # process that was killed never ran an orderly shutdown either.
    #
    # Driving a live scheduler here does not work, and the reason is worth recording. Any wakeup
    # near shutdown loses the value under test: APScheduler's shutdown sets state to STOPPED
    # BEFORE closing the executors, and _process_jobs only skips when state is PAUSED, so a
    # wakeup landing in that window processes the past-due job, reschedules it, and persists a
    # fresh fire time over the backdated one.
    heartbeat_cadence = cadence_module.BY_NAME["heartbeat"]

    seeder = scheduler_module.build_scheduler(url=database_url, jobstore_url=jobstore_url)
    store = SQLAlchemyJobStore(
        url=jobstore_url, tablename=scheduler_module.JOBSTORE_TABLE
    )
    store.start(seeder, "default")
    store.add_job(
        Job(
            seeder,
            id="heartbeat",
            name="heartbeat",
            executor="default",
            args=(),
            kwargs={},
            next_run_time=backdated,
            **scheduler_module.job_kwargs(heartbeat_cadence),
        )
    )
    store.shutdown()

    # A second scheduler object against the same persistent store: what a restart looks like.
    second = scheduler_module.build_scheduler(url=database_url, jobstore_url=jobstore_url)
    try:
        second.start(paused=True)
        scheduler_module.register_jobs(second)
        recovered = second.get_job("heartbeat")

        assert recovered is not None, "the job did not survive the restart at all"
        assert recovered.next_run_time == backdated, (
            f"next_run_time was reset to {recovered.next_run_time} instead of the persisted "
            f"past-due {backdated}. The restart discarded the missed run: the scheduler will "
            f"neither catch up nor record a miss, and every configuration test still passes."
        )
        # The reconciled job keeps its cadence-derived settings, so preserving the fire time did
        # not come at the cost of ignoring the cadence table.
        assert recovered.coalesce is True
        assert recovered.misfire_grace_time == heartbeat_cadence.misfire_grace_time
    finally:
        second.shutdown(wait=False)


def test_last_success_ignores_a_more_recent_failure(migrated_db, database_url, job_runs):
    """Decision 16, and the exact query shape it forbids.

    Seed a success at T-1h and a failure at T-1m. MAX(finished_at) across all statuses returns the
    failure, which is "last activity" wearing the name "last success" - and a job failing every
    night has plenty of recent activity (CLAUDE.md § 4).
    """
    now = datetime.now(timezone.utc)
    success_at = now - timedelta(hours=1)
    failure_at = now - timedelta(minutes=1)

    job_runs.seed("probe", "success", started_at=success_at - timedelta(minutes=1),
                  finished_at=success_at, rows_written=5)
    job_runs.seed("probe", "failed", started_at=failure_at - timedelta(minutes=1),
                  finished_at=failure_at)

    with db.connection(database_url) as conn:
        reported = heartbeat.last_success(conn, "probe")

    assert reported is not None
    assert abs((reported - success_at).total_seconds()) < 1, (
        f"last success reported as {reported}, but the only successful run finished at "
        f"{success_at}. The more recent FAILURE was picked up instead."
    )
    assert reported < failure_at

    # A job with no successful run at all reports None, not the failure's timestamp and not now().
    job_runs.seed("never_worked", "failed", started_at=failure_at, finished_at=failure_at)
    with db.connection(database_url) as conn:
        assert heartbeat.last_success(conn, "never_worked") is None


def test_overdue_verdict_follows_the_cadence_table(
    migrated_db, database_url, job_runs, monkeypatch
):
    """Decision 13, guarded BEHAVIOURALLY.

    Mutate a cadence entry's overdue_after and assert the heartbeat's verdict flips for that job
    and no other. A test that grepped heartbeat.py for numeric literals would pass on the day
    someone reintroduces a threshold as a constant defined in a third file; this one only passes
    if the heartbeat is genuinely reading the value from the cadence table at call time.

    Two entries, so "and no other" is a real assertion rather than a vacuous one.
    """
    now = datetime.now(timezone.utc)
    two_hours_ago = now - timedelta(hours=2)

    for name in ("job_under_test", "control_job"):
        job_runs.seed(name, "success", started_at=two_hours_ago - timedelta(minutes=1),
                      finished_at=two_hours_ago, rows_written=1)

    def table(under_test_overdue_after):
        return (
            Cadence("job_under_test", timedelta(minutes=15), under_test_overdue_after),
            Cadence("control_job", timedelta(minutes=15), timedelta(hours=6)),
        )

    # Threshold of 6 hours against a 2-hour-old success: neither job is overdue.
    monkeypatch.setattr(cadence_module, "CADENCES", table(timedelta(hours=6)))
    with db.connection(database_url) as conn:
        before = {v.job_name: v.overdue for v in heartbeat.check(conn, now=now)}

    assert len(before) == 2
    assert before == {"job_under_test": False, "control_job": False}

    # Same data, same code, one threshold changed in the cadence table.
    monkeypatch.setattr(cadence_module, "CADENCES", table(timedelta(minutes=30)))
    with db.connection(database_url) as conn:
        after = {v.job_name: v.overdue for v in heartbeat.check(conn, now=now)}

    assert len(after) == 2
    assert after["job_under_test"] is True, (
        "the cadence entry's overdue_after was tightened to 30 minutes against a 2-hour-old "
        "success and the verdict did not flip - the heartbeat is not reading the cadence table"
    )
    assert after["control_job"] is False, "the untouched job's verdict changed too"


def test_a_job_that_has_never_succeeded_is_overdue(migrated_db, database_url, monkeypatch):
    """No successful run on record is the most alarming state in the table, not a quiet one.

    Treating a NULL last-success as "nothing to report" is how a job that never once worked stays
    silent forever - CLAUDE.md § 2's theme 1, applied to the monitor itself.
    """
    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("never_ran", timedelta(minutes=15), timedelta(hours=6)),),
    )

    with db.connection(database_url) as conn:
        verdicts = heartbeat.check(conn, now=datetime.now(timezone.utc))

    assert len(verdicts) == 1
    assert verdicts[0].last_success is None
    assert verdicts[0].age is None
    assert verdicts[0].overdue is True
    assert "NO SUCCESSFUL RUN" in verdicts[0].describe()


def test_alert_sink_exception_does_not_fail_the_heartbeat_job(
    migrated_db, database_url, job_runs, monkeypatch
):
    """Decision 18: the one place in this commit where swallowing is correct.

    A monitoring job that fails because it could not report is a monitoring job that stops
    monitoring - and it stops during exactly the kind of broad outage where monitoring matters
    most. The delivery failure is logged; it is not fatal.
    """
    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("definitely_overdue", timedelta(minutes=15), timedelta(hours=6)),),
    )

    calls = []

    def exploding_sink(message):
        calls.append(message)
        raise ConnectionError("slack webhook unreachable")

    # Must not raise.
    result = heartbeat.heartbeat_job(sink=exploding_sink, url=database_url)

    assert result is None
    assert len(calls) == 1, "the sink was never called - the test proves nothing about swallowing"

    rows = job_runs.rows("heartbeat")
    assert len(rows) == 1
    assert rows[0]["status"] == "success", (
        f"the heartbeat's own job_runs row is {rows[0]['status']!r}: the alert sink's exception "
        f"propagated and failed the monitoring job"
    )
    assert rows[0]["error_message"] is None
    # Decision 9: the heartbeat writes no rows, so it reports no count. None, not 0.
    assert rows[0]["rows_written"] is None


def test_heartbeat_job_records_itself_and_alerts_only_when_overdue(
    migrated_db, database_url, job_runs, monkeypatch
):
    """The end-to-end shape: @job wraps it, the cadence table drives it, the sink hears about it."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("fresh_job", timedelta(minutes=15), timedelta(hours=6)),),
    )
    job_runs.seed("fresh_job", "success", started_at=now - timedelta(minutes=5),
                  finished_at=now - timedelta(minutes=4), rows_written=0)

    alerts = []
    heartbeat.heartbeat_job(sink=alerts.append, url=database_url)

    assert alerts == [], "a job that succeeded four minutes ago triggered an overdue alert"

    heartbeat_rows = job_runs.rows("heartbeat")
    assert len(heartbeat_rows) == 1
    assert heartbeat_rows[0]["status"] == "success"

    # Now make it overdue and confirm the alert names the job. The interval shrinks along with
    # the threshold because Cadence refuses overdue_after <= interval - a cadence entry that
    # alerts on a single late run is a cadence entry whose alerts get muted.
    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("fresh_job", timedelta(seconds=30), timedelta(minutes=1)),),
    )
    heartbeat.heartbeat_job(sink=alerts.append, url=database_url)

    assert len(alerts) == 1
    assert "fresh_job" in alerts[0]
    assert "OVERDUE" in alerts[0]
    assert len(job_runs.rows("heartbeat")) == 2
