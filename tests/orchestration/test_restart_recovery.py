"""Integration tier — restart recovery OBSERVED AS BEHAVIOUR, not read as configuration.

WHY THIS FILE EXISTS AT ALL.

Ten scheduler configuration tests were once green while restart recovery did not work. They
asserted `coalesce=True`, a derived `misfire_grace_time`, and a `SQLAlchemyJobStore` - every one
correct - while `add_job(..., replace_existing=True)` recomputed `next_run_time` from now and wrote
it over the persisted past-due value. The behaviour lived in process lifetime, which no value a
test could read describes (CLAUDE.md § 2 theme 2, § 13).

So nothing here asserts a setting. The observable is A ROW IN job_runs.

WHAT MAKES THIS DIFFERENT FROM THE TWO THINGS THAT ALREADY EXIST, because it is additive to both:

  - `tests/orchestration/test_heartbeat.py::test_a_past_due_next_run_time_survives_a_restart`
    asserts the past-due fire time SURVIVES into a second scheduler. That is the mechanism. It
    does not show a run happens.
  - `verify/restart_recovery.py` stops and starts a real process across a real multi-minute
    outage and asserts exactly one prompt fire. That is the live evidence and it is slow by
    construction - the cadence contract forbids an interval at or below the 60-second grace floor
    (§ 12), so a genuine outage cannot be short.

These tests take the middle: A REAL CHILD PROCESS AND A REAL JOB STORE, with the outage SEEDED
rather than waited out. A backdated `next_run_time` in `apscheduler_jobs` is exactly what an outage
leaves behind, and seeding it is the only way to get an outage's aftermath into a test that runs in
seconds. The process is real because process lifetime is the subject: a harness that stubbed the
job store, the scheduler, or the decorator would reproduce the original bug's invisibility exactly.

WHERE THE ROWS GO, since this seeds a job store and writes job_runs rows:

  A DEDICATED TEST DATABASE, via DATABASE_URL and the `migrated_db` fixture, which drops and
  recreates the schema per test. Not the production database.

  `apscheduler_jobs` is cleaned up EXPLICITLY on every exit path, because `register_jobs()` only
  adds and modifies the jobs the cadence table names - it never removes one it does not recognise
  - so a leftover probe would sit in a persistent store and keep firing under whatever scheduler
  starts next, resolving its pickled import path back to this project.

  `job_runs` rows are NOT cleaned up, deliberately. The table is append-only by trigger (§ 12) and
  nothing in this project deletes from it; the per-test schema reset is what keeps them from
  accumulating.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from apscheduler.job import Job
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

REPO_ROOT = Path(__file__).resolve().parents[2]

from app import db  # noqa: E402
from app.orchestration import cadence as cadence_module  # noqa: E402
from app.orchestration import scheduler as scheduler_module  # noqa: E402
from verify import restart_recovery  # noqa: E402

pytestmark = pytest.mark.integration

# 120 SECONDS, AND IT CANNOT BE SMALLER. The cadence contract rejects an entry whose misfire grace
# meets or exceeds its interval, and the derivation is max(60, interval // 2) - so at 120s the
# grace is 60s and the entry is legal by 60 seconds exactly. Below 121s the floor wins and
# Cadence.__post_init__ raises. A probe that needed an exemption from the rule it verifies would
# not be verifying much (§ 12).
PROBE_INTERVAL = 120
PROBE_GRACE = 60

# How long the restarted child runs. Long enough for a catch-up to fire and be recorded, and far
# shorter than one interval so a SECOND, legitimate fire cannot land inside the window and be
# mistaken for a coalescing failure.
OBSERVE_SECONDS = 10.0


def seed_past_due(database_url: str, seconds_ago: float) -> datetime:
    """Put the probe in the persistent job store with a fire time `seconds_ago` in the past.

    WRITTEN STRAIGHT INTO THE STORE WITH NO SCHEDULER RUNNING, which is both deterministic and the
    more honest model of an outage: a process that was killed never ran an orderly shutdown either.
    Driving a live scheduler to produce this state does not work, and test_heartbeat.py records
    why - APScheduler's shutdown sets state to STOPPED before closing the executors and
    _process_jobs only skips when PAUSED, so a wakeup in that window persists a fresh fire time
    over the backdated one.
    """
    jobstore_url = db.sqlalchemy_url(database_url)
    fire_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)

    probe = restart_recovery.install_probe_cadence(PROBE_INTERVAL)
    seeder = scheduler_module.build_scheduler(url=database_url, jobstore_url=jobstore_url)
    store = SQLAlchemyJobStore(url=jobstore_url, tablename=scheduler_module.JOBSTORE_TABLE)
    store.start(seeder, "default")
    store.add_job(
        Job(
            seeder,
            id=restart_recovery.PROBE_JOB_NAME,
            name=restart_recovery.PROBE_JOB_NAME,
            executor="default",
            args=(),
            kwargs={},
            next_run_time=fire_at,
            **scheduler_module.job_kwargs(probe),
        )
    )
    store.shutdown()
    return fire_at


def run_scheduler_process(database_url: str, seconds: float = OBSERVE_SECONDS):
    """One real scheduler lifetime, in a real child process, against the real job store.

    A CHILD PROCESS RATHER THAN A SECOND SCHEDULER OBJECT. The bug this file exists for lived in
    process lifetime - `next_run_time` recomputed at startup - and an in-process scheduler shares
    the parent's already-imported modules and its registry mutations. The child re-imports
    everything and reads the schedule back out of Postgres, which is what a restart is.
    """
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [
            sys.executable, "-m", "verify.restart_recovery", "--child",
            "--interval-seconds", str(PROBE_INTERVAL),
            "--run-seconds", str(seconds),
        ],
        cwd=str(REPO_ROOT), env=environment, capture_output=True, text=True, timeout=seconds + 90,
    )


def probe_rows(database_url: str) -> list[dict]:
    """Every job_runs row the probe produced, oldest first, with the fields that matter."""
    with db.connection(database_url) as conn:
        return [
            {"started_at": row[0], "status": row[1], "rows_written": row[2],
             "error_message": row[3]}
            for row in conn.execute(
                "SELECT started_at, status, rows_written, error_message FROM job_runs "
                "WHERE job_name = %s ORDER BY started_at",
                (restart_recovery.PROBE_JOB_NAME,),
            ).fetchall()
        ]


def probe_runs(database_url: str) -> list[dict]:
    """The rows that represent the function ACTUALLY HAVING BEEN CALLED.

    `missed` rows are excluded, and the exclusion is the point rather than tidying. A `missed` row
    IS a row, so a test that asserted "some row appeared" would pass over a scheduler that caught
    nothing up and merely recorded that it had not - which is exactly what a one-second
    misfire_grace_time produces. Measured: with the grace at the library default the catch-up
    tests below stayed green until this filter existed.
    """
    return [row for row in probe_rows(database_url) if row["status"] != "missed"]


@pytest.fixture
def probe(migrated_db, database_url):
    """Install the probe cadence, and remove the probe from the job store whatever happens.

    The cadence module's globals are restored too. `install_probe_cadence` REPLACES `CADENCES` and
    `JOB_FUNCTIONS` in two modules; leaving them replaced would make every later test in the same
    session see a scheduler with one job in it, and they would fail for a reason that has nothing
    to do with what they assert.
    """
    original = (cadence_module.CADENCES, scheduler_module.CADENCES,
                scheduler_module.JOB_FUNCTIONS)
    try:
        yield
    finally:
        restart_recovery.remove_probe_from_jobstore(database_url)
        (cadence_module.CADENCES, scheduler_module.CADENCES,
         scheduler_module.JOB_FUNCTIONS) = original


# ---------------------------------------------------------------------------------------------
# The catch-up
# ---------------------------------------------------------------------------------------------


def test_past_due_job_runs_once_after_restart(probe, database_url):
    """A row appears in job_runs. That is the whole assertion, and it is not about a setting.

    THREE MISSED SLOTS, WITH THE LAST ONE INSIDE THE GRACE WINDOW. The seeding is
    `2 * interval + 30` seconds ago, so the fire times are at -270s, -150s and -30s: three slots
    the outage swallowed, and the most recent of them 30 seconds old against a 60-second grace.
    That combination is what a real outage ending mid-window looks like, and it is the only
    combination that produces a catch-up rather than a miss - see the misfire test below for why
    the arithmetic matters.
    """
    seed_past_due(database_url, seconds_ago=2 * PROBE_INTERVAL + 30)
    restart_at = datetime.now(timezone.utc)

    completed = run_scheduler_process(database_url)
    assert completed.returncode == 0, (
        f"the scheduler process exited {completed.returncode}\n"
        f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
    )

    rows = probe_runs(database_url)
    assert rows, (
        f"THE JOB DID NOT RUN after the restart. Two different causes produce this and the "
        f"`missed` rows below tell them apart:\n"
        f"  - nothing at all in job_runs: the past-due fire time was DISCARDED rather than caught "
        f"up, which is what `add_job(replace_existing=True)` does, and every configuration test "
        f"stays green through it;\n"
        f"  - a `missed` row instead of a run: the fire time survived but fell outside the "
        f"misfire grace window, so the scheduler recorded that it had not run rather than running "
        f"it. A grace at APScheduler's one-second default drops every job after every outage.\n"
        f"  all rows: {probe_rows(database_url)}\n"
        f"  child stdout: {completed.stdout[-2000:]}"
    )

    delay = (rows[0]["started_at"] - restart_at).total_seconds()
    assert delay < 15, (
        f"the catch-up fired {delay:.1f}s after the restart. A SINGLE fire at the WRONG TIME is "
        f"the signature of the past-due slot being discarded and the job resuming on a fresh "
        f"schedule - the original bug, whose symptom was a correctly-single fire one full "
        f"interval late. A count-only assertion passes over it."
    )


def test_past_due_job_does_not_run_once_per_missed_slot(probe, database_url):
    """EXACTLY ONE ROW, and what coalescing actually changes is NOT the number of runs.

    ===========================================================================================
    MEASURED 2026-08-18, AND IT CONTRADICTS WHAT THIS PROJECT SAID ABOUT coalesce EVERYWHERE.
    ===========================================================================================

    The claim in scheduler.py, cadence.py and verify/restart_recovery.py was that without
    `coalesce=True` a job fires once per missed slot - "sixteen times in a row against a source
    that will rate-limit us for it". Three missed slots, seeded identically, on a real scheduler:

        coalesce=True     1 row:  success
        coalesce=False    3 rows: missed, missed, success

    ONE RUN EITHER WAY. The burst cannot happen, and the reason is this project's own cadence
    contract: § 12 requires `misfire_grace_time` STRICTLY SHORTER than the interval. Consecutive
    slots are one interval apart, so at most ONE missed fire time can be inside the grace window -
    every older one is a misfire and is skipped rather than run. `coalesce` decides whether
    APScheduler evaluates all the missed times or only the last; the grace window then discards
    all but the newest regardless.

    SO WHAT COALESCING OFF ACTUALLY PRODUCES IS SPURIOUS `missed` ROWS, and that is worth catching
    for a reason that is not smaller than the burst - it is quieter. `missed` is supposed to mean
    "a scheduled run was lost". With coalescing off it also means "a slot went by during an
    outage", so a four-hour outage writes rows saying two hours of runs were missed when the state
    of the world is that one run was late. The heartbeat reads those rows, and § 4 already turns
    on `missed` meaning one thing.

    The assertion is therefore on the TOTAL row count, not on runs. Asserting runs would pass over
    the mutation completely - measured, on the first draft of this test.
    """
    seed_past_due(database_url, seconds_ago=2 * PROBE_INTERVAL + 30)

    completed = run_scheduler_process(database_url)
    assert completed.returncode == 0, completed.stderr[-2000:]

    rows = probe_rows(database_url)
    assert len(rows) == 1, (
        f"{len(rows)} job_runs rows for a single restart after 3 missed slots:\n"
        + "\n".join(f"    {r['started_at'].isoformat()}  {r['status']}" for r in rows)
        + "\n  Expected exactly 1. Extra `missed` rows mean coalesce is not collapsing the "
          "missed slots: APScheduler evaluated every slot the outage swallowed, the grace window "
          "rejected all but the newest, and each rejection was recorded as a lost run. It was "
          "not lost - it was one late run. The observation window is far shorter than one "
          "interval, so no row here can be a legitimately scheduled second fire."
    )
    assert rows[0]["status"] == "success", (
        f"the single row is {rows[0]['status']!r}, not a run - see "
        f"test_past_due_job_runs_once_after_restart"
    )


def test_recovered_run_is_not_recorded_as_missed(probe, database_url):
    """A recovered job that records `missed` has not recovered; it has been correctly labelled.

    The two statuses are written by different code paths - `success` by the @job decorator around
    a call that happened, `missed` by the scheduler's event listener for a call that never did -
    so a row alone does not say which occurred. Counting rows without reading `status` would pass
    over a scheduler that caught nothing up and merely recorded that it had not.
    """
    seed_past_due(database_url, seconds_ago=2 * PROBE_INTERVAL + 30)

    completed = run_scheduler_process(database_url)
    assert completed.returncode == 0, completed.stderr[-2000:]

    rows = probe_rows(database_url)
    assert rows, "no row at all; see test_past_due_job_runs_once_after_restart"
    assert len(rows) == 1, f"expected one row, got {[r['status'] for r in rows]}"

    status = rows[0]["status"]
    assert status in ("success", "failed"), (
        f"the recovered run is recorded as {status!r}. `missed` means the function was never "
        f"invoked - the scheduler noticed a slot had passed and recorded that it had not run it. "
        f"That is the opposite of recovery, and it produces a row either way."
    )
    assert status == "success", (
        f"the probe run failed: {rows[0]['error_message']!r}. The probe does nothing but return "
        f"None, so a failure here is the harness or the database, not the scheduler."
    )
    # The probe writes no rows, so it reports no row count. NULL and 0 are distinct (§ 12).
    assert rows[0]["rows_written"] is None


# ---------------------------------------------------------------------------------------------
# The misfire
# ---------------------------------------------------------------------------------------------


def test_job_past_grace_window_records_missed_row(probe, database_url):
    """A misfired job NEVER INVOKES THE FUNCTION, so the decorator cannot record it.

    The @job decorator only exists inside a call that never happens. Without the scheduler's
    EVENT_JOB_MISSED listener a missed run is invisible and, worse, INDISTINGUISHABLE FROM A JOB
    THAT WAS NEVER SCHEDULED: both look like a job with no activity (§ 4).

    THE ARITHMETIC IS WHY THIS SEEDS A SINGLE SLOT RATHER THAN SEVERAL. With `coalesce=True`
    APScheduler evaluates only the LAST missed fire time against the grace window, and that one is
    never more than one interval old. So `missed` is reachable only when the grace is strictly
    shorter than the interval - which § 12 makes a hard requirement in `Cadence.__post_init__` for
    exactly this reason: where it does not hold, an ABSENCE of `missed` rows stops being evidence
    that nothing was missed, silently, for that job only.

    At a 120s interval the grace is 60s, so a slot seeded 90s ago is past it and must be recorded
    as a miss. The catch-up tests above seed their last slot 30s ago, inside it, and must not be.
    """
    assert PROBE_GRACE < PROBE_INTERVAL, (
        "the probe's grace is not shorter than its interval, so a `missed` row is unreachable and "
        "this test could never fail - which is the configuration § 12 rejects"
    )
    seed_past_due(database_url, seconds_ago=PROBE_GRACE + 30)

    completed = run_scheduler_process(database_url)
    assert completed.returncode == 0, completed.stderr[-2000:]

    rows = probe_rows(database_url)
    assert rows, (
        f"NEITHER a run NOR a missed row. The slot passed and nothing anywhere records that it "
        f"did - the job is indistinguishable from one that was never scheduled.\n"
        f"child stdout: {completed.stdout[-2000:]}"
    )
    statuses = [row["status"] for row in rows]
    assert statuses == ["missed"], (
        f"expected exactly one `missed` row, got {statuses}. A slot {PROBE_GRACE + 30}s old is "
        f"past the {PROBE_GRACE}s grace window; running it anyway would mean the grace is not "
        f"being applied, and recording nothing would mean the event listener is not registered."
    )
    assert "misfired" in (rows[0]["error_message"] or ""), (
        f"the missed row does not say why: {rows[0]['error_message']!r}"
    )


# ---------------------------------------------------------------------------------------------
# The configuration test, kept - and explicitly NOT the evidence
# ---------------------------------------------------------------------------------------------


def test_scheduler_uses_persistent_job_store():
    """KEPT, AND IN ADDITION TO THE FOUR ABOVE - never instead of them.

    An in-memory store forgets the schedule on restart and nothing about this assertion would
    change: the settings all still read correctly. That is precisely the shape of the ten green
    tests that accompanied broken restart recovery, and it is why this one is at the bottom of a
    file whose other tests observe rows.

    What it is good for is the case the behavioural tests cannot reach: it fails at import-time
    speed, with a clear name, when somebody swaps the store while refactoring - before anybody
    waits on a scheduler process to find out.
    """
    scheduler = scheduler_module.build_scheduler(
        url=None, jobstore_url="postgresql+psycopg://does-not-connect/unit"
    )
    store = scheduler._jobstores["default"]

    assert isinstance(store, SQLAlchemyJobStore), (
        f"the job store is {type(store).__name__}. An in-memory store forgets the schedule on "
        f"restart, and every configuration test in this project would stay green."
    )
    assert scheduler_module.JOBSTORE_TABLE == "apscheduler_jobs"
