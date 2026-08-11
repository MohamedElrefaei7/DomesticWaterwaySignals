"""Unit tier — scheduler configuration. No database.

Covers CLAUDE.md § 12 decisions 14 (coalesce and derived misfire grace) and 15 (persistent store).

READ THIS BEFORE TRUSTING THESE TESTS. Everything below is a configuration assertion. Not one of
them proves restart recovery works. The prior project shipped ten green scheduler tests asserting
exactly these settings while restart recovery did not work, because the behaviour lived in process
lifetime and not in any value a test can read (CLAUDE.md § 2, theme 2). And it happened again in
this very commit: every test in this file was green against a scheduler that discarded its
persisted schedule on every restart. The behavioural guard is
test_a_past_due_next_run_time_survives_a_restart in test_heartbeat.py, and the real evidence is
live verification step 7.

The jobs are inspected here through a MemoryJobStore rather than through the pending-job list, so
that the actual register_jobs() code path runs — including the add-vs-modify branch — without
needing a database.
"""

from datetime import timedelta

import pytest
from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.orchestration import scheduler as scheduler_module
from app.orchestration.cadence import CADENCES, MINIMUM_MISFIRE_GRACE_SECONDS, Cadence

# SQLAlchemyJobStore builds a lazy engine and does not connect, so a syntactically valid DSN
# pointing nowhere is enough for the unit tier.
FAKE_JOBSTORE_URL = "postgresql+psycopg://unit-test-does-not-connect/unit"


@pytest.fixture
def scheduler():
    """The real build_scheduler(), unstarted — for asserting on the job store itself."""
    built = scheduler_module.build_scheduler(url=None, jobstore_url=FAKE_JOBSTORE_URL)
    yield built
    if built.running:  # pragma: no cover - nothing here starts it
        built.shutdown(wait=False)


@pytest.fixture
def registered():
    """A started, paused scheduler on a MemoryJobStore with register_jobs() actually run.

    A MemoryJobStore is the wrong choice in production and the right one here: it needs no
    connection, so the genuine registration path is exercised in the unit tier. The separate
    assertion that production does NOT use one is test_jobstore_is_sqlalchemy_not_memory.
    """
    sched = BackgroundScheduler(jobstores={"default": MemoryJobStore()}, timezone="UTC")
    sched.start(paused=True)
    scheduler_module.register_jobs(sched)
    yield sched
    sched.shutdown(wait=False)


def _pending_jobs(scheduler):
    return scheduler.get_jobs()


def test_every_cadence_job_registers_with_coalesce_true(registered):
    """Decision 14: catch up ONCE after an outage, not once per missed slot.

    Without coalesce, a restart after four hours of a 15-minute job fires sixteen runs back to
    back — against a public API that will rate-limit us for it, and into a database that then
    holds sixteen job_runs rows for one real catch-up.
    """
    jobs = _pending_jobs(registered)

    assert len(jobs) == len(CADENCES)
    assert len(jobs) >= 1, "no jobs registered - every assertion below would pass vacuously"
    assert {job.id for job in jobs} == {c.job_name for c in CADENCES}

    for job in jobs:
        assert job.coalesce is True, f"{job.id} has coalesce={job.coalesce}"
        assert job.max_instances == 1, f"{job.id} permits overlapping runs"
        assert isinstance(job.trigger, IntervalTrigger)


def test_misfire_grace_is_derived_from_interval_and_never_one_second(registered):
    """Decision 14, per job.

    APScheduler's default is one second. A job whose fire time passed while the process was down
    by more than a second is silently dropped — which is every job, after every outage, which is
    the whole case the persistent job store exists to serve.
    """
    jobs = {job.id: job for job in _pending_jobs(registered)}
    assert len(jobs) == len(CADENCES) >= 1

    for entry in CADENCES:
        job = jobs[entry.job_name]

        assert job.misfire_grace_time == entry.misfire_grace_time, (
            f"{entry.job_name}: grace is not the cadence-derived value"
        )
        assert job.misfire_grace_time >= MINIMUM_MISFIRE_GRACE_SECONDS
        assert job.misfire_grace_time != 1, f"{entry.job_name} is on the library default"

        # And the trigger's interval is the cadence's, not some other number that happens to be
        # nearby — otherwise "derived from the interval" is derived from the wrong interval.
        assert job.trigger.interval == entry.interval


def test_misfire_grace_derivation_is_half_the_interval_floored_at_sixty():
    """The derivation itself, at the boundaries, without building a scheduler.

    The floor is the part that matters: without it, a one-minute job would get a 30-second grace
    and would start reporting missed runs for ordinary startup latency.
    """
    assert Cadence("a", timedelta(hours=1), timedelta(hours=3)).misfire_grace_time == 1800
    assert Cadence("b", timedelta(minutes=15), timedelta(minutes=45)).misfire_grace_time == 450
    assert Cadence("c", timedelta(minutes=4), timedelta(minutes=20)).misfire_grace_time == 120

    # Where the half-interval term falls below the floor and the floor takes over.
    assert Cadence("d", timedelta(minutes=2), timedelta(minutes=10)).misfire_grace_time == 60
    assert Cadence("e", timedelta(seconds=90), timedelta(minutes=5)).misfire_grace_time == 60

    # 61s is the shortest interval this table admits: the floor gives it a 60s grace, which is
    # strictly shorter, so it survives the grace-versus-interval check below. A 60s interval does
    # not - see test_a_grace_at_or_above_the_interval_is_rejected.
    assert Cadence("f", timedelta(seconds=61), timedelta(minutes=5)).misfire_grace_time == 60


def test_jobstore_is_sqlalchemy_not_memory(scheduler):
    """Decision 15: the schedule must survive the process that created it.

    A MemoryJobStore forgets everything on restart. Every other assertion in this file still
    passes with one — which is exactly why this assertion cannot be the evidence that restart
    recovery works, only that the most obvious way to break it is absent.
    """
    stores = scheduler._jobstores

    assert len(stores) == 1
    assert set(stores) == {"default"}

    store = stores["default"]
    assert isinstance(store, SQLAlchemyJobStore)
    assert not isinstance(store, MemoryJobStore)
    assert store.pickle_protocol is not None

    # The table CONTEXT.md flags for exclusion from dumps in Phase 11.
    assert scheduler_module.JOBSTORE_TABLE == "apscheduler_jobs"
    assert store.jobs_t.name == "apscheduler_jobs"


def test_missed_event_listener_is_registered(scheduler):
    """Decision 12's wiring, checked structurally here and behaviourally in test_heartbeat.py.

    The row this writes is tested against a real database; that a listener exists for the right
    event mask is checkable without one.
    """
    masks = [mask for _, mask in scheduler._listeners]

    assert len(masks) >= 1
    assert any(mask & EVENT_JOB_MISSED for mask in masks), (
        "no listener is subscribed to EVENT_JOB_MISSED - a misfired run would be invisible and "
        "indistinguishable from a job that was never scheduled"
    )


def test_cadence_and_function_registry_must_agree():
    """A cadence entry with no function never fires, and the heartbeat reports it overdue forever.

    A function with no cadence entry never runs and nothing reports that it never ran - the
    silent half of CLAUDE.md § 2's theme 1.
    """
    assert {c.job_name for c in CADENCES} == set(scheduler_module.JOB_FUNCTIONS)

    for name, func in scheduler_module.JOB_FUNCTIONS.items():
        # SQLAlchemyJobStore serializes by import path; a lambda or closure is unstorable.
        assert hasattr(func, "__module__") and hasattr(func, "__qualname__")
        assert "<lambda>" not in func.__qualname__
        assert "<locals>" not in func.__qualname__, (
            f"{name} is a closure and cannot be persisted by SQLAlchemyJobStore"
        )


def test_overdue_after_must_exceed_interval():
    """Guarded in Cadence.__post_init__, so a bad entry fails at import rather than at 3am."""
    with pytest.raises(ValueError, match="must be longer than interval"):
        Cadence("bad", timedelta(minutes=15), timedelta(minutes=15))

    with pytest.raises(ValueError, match="must be longer than interval"):
        Cadence("worse", timedelta(hours=1), timedelta(minutes=5))


def test_a_grace_at_or_above_the_interval_is_rejected():
    """CLAUDE.md § 12: grace >= interval means the job can never record a `missed` row.

    With coalesce=True only the LAST missed fire time is compared against the grace window, and
    that one is never more than an interval old - so the comparison can never be true and the job
    always catches up instead. The lost row is not the damage; the damage is that an ABSENCE of
    `missed` rows silently stops being evidence that nothing was missed, for that job only, while
    everything else looks normal.

    The floor is what makes this reachable at all: below 61s the 60-second grace floor wins and
    meets or exceeds the interval. Above it, the half-interval term or the floor is always
    strictly smaller, so no valid entry can trip this by accident.
    """
    # A short interval is not obviously wrong to whoever writes it - which is why this is a
    # constructor error rather than a review item.
    for seconds in (15, 20, 30, 45, 59, 60):
        with pytest.raises(ValueError, match="must be shorter than interval") as excinfo:
            Cadence("too_frequent", timedelta(seconds=seconds), timedelta(hours=1))

        message = str(excinfo.value)
        assert "too_frequent" in message
        assert f"{seconds}s" in message, "the offending interval is not named"
        assert "60s" in message, "the derived grace is not named"
        assert "missed" in message

    # 61 seconds is the boundary and must be accepted, or the check is rejecting valid entries.
    assert Cadence("just_ok", timedelta(seconds=61), timedelta(hours=1)).misfire_grace_time == 60

    # And every entry actually in the table satisfies it - asserted by size first, so this cannot
    # pass by iterating over nothing.
    assert len(CADENCES) >= 1
    for entry in CADENCES:
        assert entry.misfire_grace_time < entry.interval_seconds, (
            f"{entry.job_name} can never record a missed run"
        )
