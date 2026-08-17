"""The one-shot job runner: it goes through @job, it does not start the scheduler, it exits usefully.

THE FAILURE THIS COMMAND IS ONE LINE AWAY FROM. Resolving the job by importing its module and
calling the underlying callable is shorter, reads perfectly well, and creates a second execution
path that writes no `job_runs` row. The consequence is specific: the first backup ever taken - the
run most likely to reveal a problem - would be the one nothing recorded. Same shape as the rollback
defect this stage began with, through a different door, which is why the first test here is about
the row rather than about the return value.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.orchestration import cadence, run_once, scheduler

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_run_once_invokes_decorated_function(monkeypatch):
    """A `job_runs` row is written, because the DECORATED function is what gets called.

    Asserted through the decorator's own machinery rather than against a real database: the
    property is "the call goes through @job", and the row is how that is observable. A stub in
    JOB_FUNCTIONS that is decorated exactly as the real jobs are makes the wiring the subject and
    keeps a backup out of a unit test.
    """
    from app.orchestration.job import job

    opened = []
    closed = []
    monkeypatch.setattr("app.orchestration.job._open_run", lambda url, name: (opened.append(name), 7)[1])
    monkeypatch.setattr(
        "app.orchestration.job._close_run",
        lambda url, run_id, status, rows, err: closed.append((run_id, status, rows)),
    )

    calls = []

    @job("probe_job")
    def probe(url=None):
        calls.append(url)
        return 3

    monkeypatch.setitem(scheduler.JOB_FUNCTIONS, "probe_job", probe)

    result = run_once.run_once("probe_job")

    assert calls, "the job function was never called"
    assert result == 3, f"the job's return value was not passed through: {result!r}"
    assert opened == ["probe_job"], (
        f"no `running` row was opened for the job: {opened}. The runner called the undecorated "
        f"function, so this run writes NO job_runs row at all - and the first backup ever taken "
        f"would be the one nothing recorded."
    )
    assert closed == [(7, "success", 3)], (
        f"the job_runs row was not closed with the run's outcome: {closed}"
    )


def test_run_once_resolves_from_the_shared_registry():
    """It runs what the scheduler runs, because it reads the same mapping.

    A runner with its own table of jobs would be a second copy of the registry, and the copy is
    what goes stale - a job renamed in one place runs under the old name from the other, or stops
    being runnable at all with nothing saying why.
    """
    source = Path(run_once.__file__).read_text(encoding="utf-8")

    assert "JOB_FUNCTIONS" in source, (
        "the runner does not reference JOB_FUNCTIONS; it has its own idea of what the jobs are"
    )
    assert set(run_once.valid_names()) == set(scheduler.JOB_FUNCTIONS), (
        f"the runner's job list disagrees with the scheduler's registry:\n"
        f"  runner:    {sorted(run_once.valid_names())}\n"
        f"  scheduler: {sorted(scheduler.JOB_FUNCTIONS)}"
    )


def test_run_once_does_not_start_scheduler(monkeypatch):
    """No scheduler is constructed, started, or asked for a job store.

    Starting one to run a single job fires every OTHER due job as a side effect: a one-shot backup
    on an instance that had been down would kick off four ingests and a feature build, none of
    which anybody typed. Nothing here touches `apscheduler_jobs` either - the persistent store
    holds the schedule, and a one-off run is not a schedule change.
    """
    # THE STRUCTURAL HALF RUNS FIRST, DELIBERATELY. `run_once` imports what it needs with
    # `from ... import`, so monkeypatching `scheduler.build_scheduler` does NOT intercept a call
    # made through a name bound in run_once's own namespace - measured: the mutation that adds
    # `build_scheduler()` to the runner sails past the patched attribute, reaches the REAL one, and
    # fails on cadence agreement instead. That is a red test for the wrong reason: it would report
    # a configuration problem where the finding is that the runner starts a scheduler.
    #
    # The source scan has no such blind spot, so it goes first and it is what names the failure.
    source = Path(run_once.__file__).read_text(encoding="utf-8")
    assert source.strip(), "run_once.py is empty; this scan would pass over nothing"
    for forbidden in ("build_scheduler", "register_jobs", "SQLAlchemyJobStore", "JOBSTORE_TABLE"):
        assert forbidden not in source, (
            f"run_once.py references {forbidden!r}. Running one job must not construct or start a "
            f"scheduler: doing so fires every OTHER due job as a side effect, so a one-shot backup "
            f"on an instance that had been down would also kick off four ingests and a feature "
            f"build. It must not touch the persistent job store either - the store holds the "
            f"schedule, and a one-off run is not a schedule change."
        )

    started = []

    def exploding_build(*args, **kwargs):
        started.append("build_scheduler")
        raise AssertionError("run_once constructed a scheduler")

    def exploding_start(*args, **kwargs):
        started.append("start")
        raise AssertionError("run_once started the scheduler")

    monkeypatch.setattr(scheduler, "build_scheduler", exploding_build)
    monkeypatch.setattr(scheduler, "start", exploding_start)
    monkeypatch.setattr(scheduler, "register_jobs", exploding_start)

    from app.orchestration.job import job

    monkeypatch.setattr("app.orchestration.job._open_run", lambda url, name: 1)
    monkeypatch.setattr("app.orchestration.job._close_run", lambda *a, **k: None)

    @job("probe_job")
    def probe(url=None):
        return None

    monkeypatch.setitem(scheduler.JOB_FUNCTIONS, "probe_job", probe)

    run_once.run_once("probe_job")

    assert started == [], f"the runner touched the scheduler: {started}"


def test_run_once_unknown_name_exits_nonzero_and_lists_valid_names(capsys):
    """A usage error with the list, not a traceback.

    This is run from an SSM session where a traceback scrolls off the top and the useful line -
    what could have been typed instead - is the one that goes with it.
    """
    exit_code = run_once.main(["no_such_job"])

    assert exit_code == run_once.EXIT_USAGE, f"exited {exit_code}, expected {run_once.EXIT_USAGE}"

    captured = capsys.readouterr()
    assert "no_such_job" in captured.err, "the message does not name what was typed"
    for name in scheduler.JOB_FUNCTIONS:
        assert name in captured.err, (
            f"the valid-name list omits {name!r}; an operator cannot see what to type instead"
        )
    assert "Traceback" not in captured.err and "Traceback" not in captured.out


def test_run_once_exit_code_reflects_job_failure(monkeypatch, capsys):
    """A job that raises exits non-zero, so a shell step can gate on it."""
    from app.orchestration.job import job

    monkeypatch.setattr("app.orchestration.job._open_run", lambda url, name: 1)
    monkeypatch.setattr("app.orchestration.job._close_run", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")

    @job("probe_job")
    def probe(url=None):
        raise RuntimeError("the job failed on purpose")

    monkeypatch.setitem(scheduler.JOB_FUNCTIONS, "probe_job", probe)
    # The stub has no cadence entry, so the shared agreement check refuses it - correctly, and it
    # has its own tests. This test's subject is the exit code, so the check is stood down here
    # rather than the stub being given a fake cadence entry, which would make a second copy of the
    # cadence table inside a test file.
    monkeypatch.setattr(run_once, "check_cadence_agreement", lambda: None)

    exit_code = run_once.main(["probe_job"])

    assert exit_code == run_once.EXIT_JOB_FAILED, (
        f"a failing job exited {exit_code}. A zero exit here makes `run_once backup_nightly && "
        f"echo ok` print ok after a backup that did not happen."
    )
    captured = capsys.readouterr()
    assert "the job failed on purpose" in captured.err, (
        f"the failure's own message is not reported: {captured.err!r}"
    )


def test_run_once_uses_shared_cadence_agreement_check(monkeypatch):
    """The CADENCES/JOB_FUNCTIONS check is the scheduler's, not a second copy.

    Guarded BEHAVIOURALLY - by breaking the shared function and watching the runner fail - rather
    than by grepping for the call. A second implementation would satisfy a grep for the name while
    diverging from the first the moment either changed, and two checks of one fact drift silently.
    """
    called = []

    def recording_check():
        called.append(True)
        raise scheduler.SchedulerConfigurationError("the shared check refused")

    monkeypatch.setattr(run_once, "check_cadence_agreement", recording_check)

    with pytest.raises(scheduler.SchedulerConfigurationError, match="the shared check refused"):
        run_once.main(["heartbeat"])

    assert called, "run_once did not call the shared cadence agreement check"


def test_the_shared_check_is_the_one_build_scheduler_uses(monkeypatch):
    """And the scheduler uses it too, so there is genuinely ONE of them.

    Without this, `run_once` could call a shared-looking function that `build_scheduler` had
    quietly stopped using - which is the divergence the extraction exists to prevent, in the
    direction nobody looks.
    """
    called = []

    def recording_check():
        called.append(True)
        raise scheduler.SchedulerConfigurationError("the shared check refused")

    monkeypatch.setattr(scheduler, "check_cadence_agreement", recording_check)

    with pytest.raises(scheduler.SchedulerConfigurationError):
        scheduler.build_scheduler(url="postgresql://unused@127.0.0.1:1/unused")

    assert called, "build_scheduler no longer calls the shared cadence agreement check"


def test_the_shared_check_catches_both_directions(monkeypatch):
    """A cadence entry with no function, and a function with no cadence entry.

    They fail differently and the second is worse: a registered function with no cadence entry
    never runs at all, and nothing reports that it never ran.
    """
    original = dict(scheduler.JOB_FUNCTIONS)
    entry = cadence.CADENCES[0]

    # Direction one: a cadence entry whose function is gone.
    monkeypatch.setattr(
        scheduler, "JOB_FUNCTIONS", {k: v for k, v in original.items() if k != entry.job_name}
    )
    with pytest.raises(scheduler.SchedulerConfigurationError, match="no registered function"):
        scheduler.check_cadence_agreement()

    # Direction two, from the ORIGINAL mapping rather than from the one just broken - otherwise
    # the first failure fires again and this half never runs.
    monkeypatch.setattr(scheduler, "JOB_FUNCTIONS", {**original, "orphan_job": lambda: None})
    with pytest.raises(scheduler.SchedulerConfigurationError, match="no cadence entry"):
        scheduler.check_cadence_agreement()


@pytest.mark.integration
def test_run_once_writes_a_real_job_runs_row(migrated_db, database_url, job_runs):
    """End to end against a real database: the heartbeat, run once, leaves one row.

    The heartbeat because it writes nothing of its own - so this asserts the RUNNER's row and
    nothing else, and `rows_written` is NULL rather than 0, which is the decorator's own
    distinction holding through a path that did not exist when it was written.
    """
    import os

    os.environ["DATABASE_URL"] = database_url
    exit_code = run_once.main(["heartbeat"])

    assert exit_code == run_once.EXIT_OK, f"the heartbeat exited {exit_code}"

    rows = job_runs.rows("heartbeat")
    assert len(rows) == 1, f"expected exactly one job_runs row, got {rows}"
    assert rows[0]["status"] == "success"
    assert rows[0]["rows_written"] is None, (
        f"rows_written is {rows[0]['rows_written']!r}, not NULL. The heartbeat writes no rows, and "
        f"0 would claim it counts them and today counted none (CLAUDE.md § 4)."
    )


@pytest.mark.integration
def test_run_once_is_invocable_as_a_module(database_url):
    """`python3 -m app.orchestration.run_once --list` works from a shell.

    The runbook's G2 and H2 steps are shell lines, and a module that only imports cleanly is not
    the same claim as one that runs. This is the exact invocation a human types.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "app.orchestration.run_once", "--list"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**dict(__import__("os").environ), "DATABASE_URL": database_url},
    )

    assert completed.returncode == 0, (
        f"`-m app.orchestration.run_once --list` exited {completed.returncode}: {completed.stderr}"
    )
    listed = set(completed.stdout.split())
    assert listed == set(scheduler.JOB_FUNCTIONS), (
        f"--list printed {sorted(listed)}, not the registry {sorted(scheduler.JOB_FUNCTIONS)}"
    )
