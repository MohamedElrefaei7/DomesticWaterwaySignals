"""Integration tier — the heartbeat, and the missed-run listener that feeds it.

Covers CLAUDE.md § 12 decisions 12 (missed rows come from a scheduler listener), 13 (the cadence
table is the only source of overdue thresholds), 16 ("last success" means the last SUCCESS row),
17 (data liveness is measured from the data, via the freshness registry), and 18 (alert delivery
failures never fail the monitoring job).
"""

from datetime import date, datetime, time as dt_time, timedelta, timezone

import pytest
from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.job import Job
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from app import db
from app.ingest import usgs_daily_ingest, usgs_ingest
from app.ingest.usgs_client import PARAM_DISCHARGE, Reading
from app.ingest.usgs_daily_client import STAT_MEAN, DailyReading
from app.orchestration import cadence as cadence_module
from app.orchestration import heartbeat, scheduler as scheduler_module
from app.orchestration.cadence import Cadence
from app.orchestration.heartbeat import Freshness

pytestmark = pytest.mark.integration

FAKE_JOBSTORE_URL = "postgresql+psycopg://does-not-connect/unit"


@pytest.fixture
def no_freshness_registry(monkeypatch):
    """Empty the freshness registry for tests that are about JOB overdue-ness only.

    Needed because an ingest table with no rows is deliberately STALE, not quiet (see
    heartbeat.check_freshness), so a freshly migrated database has a legitimately alerting
    gauge_readings_iv in it. The tests below that count alerts are about the cadence table; leaving
    the real registry in place would have them passing or failing for a reason they are not
    testing.

    The tests that ARE about freshness install their own registry explicitly.
    """
    monkeypatch.setattr(heartbeat, "FRESHNESS", ())


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
    migrated_db, database_url, job_runs, monkeypatch, no_freshness_registry
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
    migrated_db, database_url, job_runs, monkeypatch, no_freshness_registry
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

    # Now make it overdue and confirm the alert names the job. The numbers here are boxed in from
    # both sides by Cadence's own validation: overdue_after must exceed interval, and the derived
    # misfire grace must be shorter than it - which puts a floor of 61s under any interval. So
    # 90s/2min, against a success seeded four minutes ago.
    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("fresh_job", timedelta(seconds=90), timedelta(minutes=2)),),
    )
    heartbeat.heartbeat_job(sink=alerts.append, url=database_url)

    assert len(alerts) == 1
    assert "fresh_job" in alerts[0]
    assert "OVERDUE" in alerts[0]
    assert len(job_runs.rows("heartbeat")) == 2


# ---------------------------------------------------------------------------------------------
# The freshness registry — CLAUDE.md § 12 decision 17, and § 14's last bullet.
# ---------------------------------------------------------------------------------------------


def _seed_reading(url, ts, site="07010000", value=148000.0):
    """Write one reading straight through the real upsert, on its own connection."""
    with db.connection(url) as conn:
        usgs_ingest.upsert_readings(
            conn, [Reading(site, ts, PARAM_DISCHARGE, value, ("P",))]
        )
        conn.commit()


def test_freshness_registry_flags_a_stale_table(migrated_db, database_url, monkeypatch):
    """An old MAX(ts) is reported stale; a recent one is not.

    Guarded BEHAVIOURALLY, the same way the cadence table is: the registry entry's threshold is
    mutated and the verdict has to follow it. A test that grepped heartbeat.py for '6 hours'
    would pass on the day someone reintroduces the threshold as a constant in a third file.

    The empty case is asserted too, and it is deliberately STALE rather than quiet: an ingest
    table that has never received a row is the most alarming state it can be in, and treating a
    NULL MAX(ts) as "nothing to report" is how a source that never once delivered stays silent.
    """
    now = datetime.now(timezone.utc)
    registry = (
        Freshness("usgs_ingest", "gauge_readings_iv", "ts", timedelta(hours=6)),
    )

    # No rows at all -> stale, not quiet.
    with db.connection(database_url) as conn:
        empty = heartbeat.check_freshness(conn, now=now, registry=registry)
    assert len(empty) == 1
    assert empty[0].newest is None
    assert empty[0].stale is True, "an ingest table with no rows at all was reported healthy"
    assert "EMPTY" in empty[0].describe()

    # A reading four hours old, against a six-hour threshold -> fresh.
    _seed_reading(database_url, now - timedelta(hours=4))
    with db.connection(database_url) as conn:
        fresh = heartbeat.check_freshness(conn, now=now, registry=registry)
    assert fresh[0].stale is False, (
        f"a four-hour-old reading was called stale against a six-hour threshold: "
        f"{fresh[0].describe()}"
    )
    assert fresh[0].age is not None and fresh[0].age < timedelta(hours=5)

    # Same data, same code, one threshold tightened in the registry -> the verdict flips.
    tightened = (
        Freshness("usgs_ingest", "gauge_readings_iv", "ts", timedelta(hours=1)),
    )
    with db.connection(database_url) as conn:
        stale = heartbeat.check_freshness(conn, now=now, registry=tightened)
    assert stale[0].stale is True, (
        "the registry's max_staleness was tightened to one hour against a four-hour-old reading "
        "and the verdict did not flip - the heartbeat is not reading the registry at call time"
    )
    assert "STALE" in stale[0].describe()


def test_a_job_with_recent_runs_but_stale_data_is_still_flagged(
    migrated_db, database_url, job_runs, monkeypatch
):
    """THIS IS THE TEST THAT CATCHES A SOURCE RETURNING 200 AND NOTHING.

    The setup is the failure this project keeps hitting, written down: the job is running on
    schedule, succeeding every time, writing clean `success` rows with no errors - and the table
    it feeds has not received a row in four days. Every process-level signal is green. The prior
    project ran two and a half months in exactly this state, with orchestration recording
    "Completed" over a stack that was entirely down.

    CLAUDE.md § 4: liveness is measured from the DATA, never from the process. A heartbeat that
    checked only job_runs would report this as healthy, which is why the mutation table points
    "have the heartbeat check only job_runs and not data freshness" at this test.
    """
    now = datetime.now(timezone.utc)

    # The job is in perfect health by every process measure: three recent successful runs.
    for minutes_ago in (150, 90, 30):
        finished = now - timedelta(minutes=minutes_ago)
        job_runs.seed(
            "usgs_ingest",
            "success",
            started_at=finished - timedelta(seconds=20),
            finished_at=finished,
            # It even reports rows written. Nothing here looks wrong.
            rows_written=0,
        )

    # And the data behind it is four days old.
    _seed_reading(database_url, now - timedelta(days=4))

    monkeypatch.setattr(
        cadence_module,
        "CADENCES",
        (Cadence("usgs_ingest", timedelta(seconds=3600), timedelta(hours=3)),),
    )
    monkeypatch.setattr(
        heartbeat,
        "FRESHNESS",
        (Freshness("usgs_ingest", "gauge_readings_iv", "ts", timedelta(hours=6)),),
    )

    # The job-status check alone sees nothing wrong. This assertion is the control: without it,
    # the test below could pass because the job looked overdue, not because the data was stale.
    with db.connection(database_url) as conn:
        verdicts = heartbeat.check(conn, now=now)
    assert [v.overdue for v in verdicts] == [False], (
        "the job was already overdue, so this test would pass without the freshness check ever "
        "running - it proves nothing in that state"
    )

    alerts = []
    heartbeat.heartbeat_job(sink=alerts.append, url=database_url, now=now)

    assert len(alerts) == 1, (
        f"expected exactly one alert about stale data, got {len(alerts)}: {alerts}. A job "
        f"succeeding on schedule over a source that has sent nothing for four days was reported "
        f"as healthy."
    )
    assert "gauge_readings_iv" in alerts[0], f"the alert does not name the stale table: {alerts[0]}"
    assert "STALE" in alerts[0]
    # And it says so as a DATA problem, not by pretending the job failed.
    assert "overdue" not in alerts[0].lower(), (
        f"the stale table was reported as an overdue job: {alerts[0]}"
    )


def test_an_unqueryable_registered_table_alerts_rather_than_skipping(
    migrated_db, database_url, monkeypatch
):
    """A registered table that cannot be read is a failed check, never a skipped one.

    CLAUDE.md § 13: a skipped check exits non-zero and a SKIP never reads as green. The tempting
    version catches the exception, logs it, and moves on - which turns a missing table into a
    silent no-op and leaves the heartbeat reporting healthy while checking nothing.

    It also must not take the whole heartbeat down: the remaining entries still need checking,
    which is why the connection is rolled back and the loop continues.
    """
    now = datetime.now(timezone.utc)
    registry = (
        Freshness("usgs_ingest", "table_that_does_not_exist", "ts", timedelta(hours=6)),
        Freshness("usgs_ingest", "gauge_readings_iv", "ts", timedelta(hours=6)),
    )

    with db.connection(database_url) as conn:
        verdicts = heartbeat.check_freshness(conn, now=now, registry=registry)

    assert len(verdicts) == 2, (
        "the unqueryable entry aborted the whole freshness check; the remaining tables were "
        "never looked at"
    )

    missing = verdicts[0]
    assert missing.stale is True, "an unqueryable registered table was reported healthy"
    assert missing.error is not None
    assert "CANNOT BE CHECKED" in missing.describe()

    # The second entry was still checked, on the same connection, after the first one errored.
    assert verdicts[1].table == "gauge_readings_iv"
    assert verdicts[1].error is None


def test_freshness_registry_covers_every_ingest_table(migrated_db, database_url):
    """Every ingest table is registered, and each names a job the cadence table schedules.

    CLAUDE.md § 12: no ingest client is complete until it registers its table. The failure this
    guards against is the cheap one - adding an ingest client, wiring its job, and never
    registering the table, so the newest source is the one nothing watches. It would then be
    reported healthy by omission: the heartbeat's green light would mean "the tables I know about
    are fine."

    Asserted as EXACT SET EQUALITY, not `>=`. A subset assertion passes with a table missing,
    which is precisely the change this catches - so a new ingest table turns this red until it is
    named here, deliberately, in the commit that creates it. Phase 4 adds the two USDA tables.

    PHASE 5 ADDS `features`, WHICH IS NOT AN INGEST TABLE, and that is a deliberate widening of
    what this registry covers rather than an exception slipped past the equality check.

    An ingest table goes stale when a SOURCE goes quiet. `features` goes stale when THE BUILD
    stops - and the build is the one job whose failure is invisible from the data, because every
    table it reads stays perfectly fresh while it does nothing. A heartbeat covering only the four
    ingest tables would report green meaning "everything we collect is arriving", which would be
    true and would say nothing about whether anything was being computed from it: CLAUDE.md § 2's
    theme 2 with a whole layer inside the blind spot.

    `targets` and `gauge_daily` are NOT registered, and the omission is reasoned. All three tables
    are written by the same job in the same transaction, so one entry reports on all three - and
    three entries would produce three alerts for one cause with no way to tell which to read first.
    `features` is the one chosen because it is the layer everything downstream consumes.
    """
    registered = {f.table for f in heartbeat.FRESHNESS}
    assert registered == {
        "gauge_readings_iv",
        "gauge_readings_daily",
        "barge_rates",
        "lock_movements",
        "features",
    }, (
        f"the freshness registry covers {sorted(registered)}. Every ingest table must be "
        f"registered in the commit that creates it (CLAUDE.md § 12), and the derived layer is "
        f"registered too - see this test's docstring for why that is not an exception."
    )

    # Every entry names a job that is actually scheduled - a freshness entry for a job nothing
    # runs reports a stale table forever with no way to fix it.
    for entry in heartbeat.FRESHNESS:
        assert entry.job_name in cadence_module.BY_NAME, (
            f"freshness entry for {entry.table} names job {entry.job_name!r}, which has no "
            f"cadence entry"
        )

    # And each registered table is genuinely queryable through the real check, on a database
    # carrying the real migrations. A registry entry naming a table that does not exist would be
    # reported as a failed check rather than a passing one, but it would still be a defect.
    with db.connection(database_url) as conn:
        verdicts = heartbeat.check_freshness(conn, now=datetime.now(timezone.utc))

    assert len(verdicts) == len(heartbeat.FRESHNESS)
    assert all(v.error is None for v in verdicts), (
        f"a registered table could not be queried: {[v.describe() for v in verdicts if v.error]}"
    )
    # Both are empty on a fresh database, and an empty ingest table is STALE, not quiet.
    assert all(v.stale for v in verdicts)
    assert all(v.newest is None for v in verdicts)


def test_daily_freshness_measures_age_from_a_date_column(migrated_db, database_url, monkeypatch):
    """A `date` column produces an age, anchored at midnight UTC rather than cast in SQL.

    Not in the brief's numbered list, and here because the daily table's timestamp column is a
    calendar DATE while every other registered column is a timestamptz. `now - date` is not a
    subtraction Python will perform, so without normalization the freshness check for the
    backbone table would raise on every heartbeat run - reported as a failed check, loudly, but
    still broken.

    Midnight UTC is the conservative anchor: it makes a daily value look up to 24 hours OLDER,
    so the check errs towards reporting stale. The 48-hour threshold is set with that priced in.
    """
    today = date.today()
    with db.connection(database_url) as conn:
        usgs_daily_ingest.upsert_daily_readings(
            conn,
            [
                DailyReading(
                    usgs_site_id="07032000",
                    date=today,
                    param_code=PARAM_DISCHARGE,
                    stat_cd=STAT_MEAN,
                    value=121000.0,
                    qualifiers=("A",),
                )
            ],
        )
        conn.commit()

    now = datetime.combine(today, dt_time(12, 0), tzinfo=timezone.utc)
    registry = (
        Freshness("usgs_daily_ingest", "gauge_readings_daily", "date", timedelta(hours=48)),
    )

    with db.connection(database_url) as conn:
        verdict = heartbeat.check_freshness(conn, now=now, registry=registry)[0]

    assert verdict.error is None, f"the date column could not be aged: {verdict.describe()}"
    assert verdict.newest == datetime.combine(today, dt_time(0, 0), tzinfo=timezone.utc), (
        f"newest is {verdict.newest}; a date must anchor at midnight UTC, not be cast in SQL "
        f"where the session's TimeZone would decide"
    )
    assert verdict.age == timedelta(hours=12)
    assert verdict.stale is False, "today's daily value was reported stale against a 48h threshold"
