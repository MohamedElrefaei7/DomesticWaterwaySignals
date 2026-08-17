"""Restart-recovery check: stop a real process, start it again, watch exactly one prompt fire.

THIS HARNESS MOCKS NOTHING, and that is the entire point.

It imports and drives the real `register_jobs()`, the real `@job` decorator, the real
`SQLAlchemyJobStore`, and the real Postgres. It runs the scheduler in an actual child process
which is actually stopped and actually started again.

The reason is specific. The Phase 2 commit shipped `add_job(..., replace_existing=True)`, which
recomputes `next_run_time` from now and writes it over the persisted past-due value, so a restart
after an outage silently discarded the missed run — while every configuration test stayed green,
because the settings were all correct and the behaviour lived in process lifetime. A harness that
stubbed the job store, the scheduler, or the decorator would reproduce that bug's invisibility
exactly. Process lifetime is the thing under test, so a process is what gets tested.

What it asserts, per CLAUDE.md § 13:

    EXACTLY ONE fire after restart, and that one PROMPT.

Both halves are load-bearing. `>= 1` passes when coalescing is broken and the job fires once per
missed slot — the failure `coalesce=True` exists to prevent, which an operator would otherwise
meet as an ingest source rate-limiting them. And a count-only assertion passes the
`replace_existing` bug itself, whose symptom was a correctly-single fire at the wrong time: one
full interval after restart instead of immediately.

The probe job lives here rather than in `app/` because it is apparatus. A probe in `app/` would
mean production code shipping a job that exists only to be watched, and the next reader of the
cadence table would have to work out which entries are real.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration import cadence as cadence_module  # noqa: E402
from app.orchestration import scheduler as scheduler_module  # noqa: E402
from app.orchestration.cadence import MINIMUM_MISFIRE_GRACE_SECONDS, Cadence  # noqa: E402
from app.orchestration.job import job  # noqa: E402
from verify.preflight import FAIL, PASS, Result  # noqa: E402

PROBE_JOB_NAME = "verify_restart_probe"


@job(PROBE_JOB_NAME)
def restart_probe_job():
    """The watched job. Does nothing but exist, and be recorded doing so.

    Decorated with the REAL @job, so every fire lands in job_runs through exactly the code path
    production uses — which is also how the harness observes fires at all. It returns None rather
    than 0: it writes no rows, so it reports no row count (CLAUDE.md § 12).

    Module-level, because SQLAlchemyJobStore serializes a job by import path. A closure or a
    lambda would be unstorable, and the failure would arrive at add_job time looking like a
    pickling bug rather than a design constraint.
    """
    return None


# ---------------------------------------------------------------------------------------------
# The assertion — pure, so it is testable without a database or a clock
# ---------------------------------------------------------------------------------------------


def assess_catch_up(
    fires: list[datetime],
    restart_at: datetime,
    promptness_seconds: float,
    interval_seconds: float | None = None,
) -> Result:
    """Exactly one fire after restart, and it must be prompt.

    Three distinct failures, three distinct messages, each reporting the timestamps observed
    (CLAUDE.md § 13 — a bare FAIL sends the operator to re-derive what the harness already had).
    """
    name = "restart produces exactly one prompt catch-up fire"
    after = sorted(fire for fire in fires if fire >= restart_at)
    listed = "\n".join(
        f"           {fire.isoformat()}  (+{(fire - restart_at).total_seconds():.2f}s)"
        for fire in after
    ) or "           (none)"

    if not after:
        expectation = (
            f"within {promptness_seconds:g}s of restart"
            if interval_seconds is None
            else (
                f"within {promptness_seconds:g}s of restart; a fire one full interval "
                f"({interval_seconds:g}s) later would mean the past-due slot was discarded"
            )
        )
        return Result(
            name,
            FAIL,
            f"observed: NO fire after restart at {restart_at.isoformat()}\n"
            f"         expected exactly one, {expectation}.\n"
            f"         Either the job did not survive the restart at all (check that "
            f"{scheduler_module.JOBSTORE_TABLE} holds a row for {PROBE_JOB_NAME!r}), or the "
            f"observation window closed before it fired.",
        )

    if len(after) > 1:
        return Result(
            name,
            FAIL,
            f"observed: {len(after)} fires after restart at {restart_at.isoformat()}:\n{listed}\n"
            f"         expected exactly 1. Two causes look like this and the timestamps above "
            f"tell them apart:\n"
            f"           - fires bunched within a second or two: coalesce is not collapsing the "
            f"missed slots, and the job is firing once per slot missed. That is the failure "
            f"coalesce=True exists to prevent.\n"
            f"           - fires roughly one interval apart: the observation window straddled the "
            f"next scheduled slot, so the second fire is legitimate. Re-run with a smaller "
            f"--observe-seconds.",
        )

    delay = (after[0] - restart_at).total_seconds()
    if delay > promptness_seconds:
        return Result(
            name,
            FAIL,
            f"observed: exactly one fire, but {delay:.2f}s after restart at "
            f"{restart_at.isoformat()}:\n{listed}\n"
            f"         expected it within {promptness_seconds:g}s. A single fire at the WRONG TIME "
            f"is the signature of the past-due slot being discarded and the job simply resuming on "
            f"a fresh schedule - the bug the Phase 2 commit found, where a count-only assertion "
            f"passes and nothing catches up. Check that register_jobs() reconciles rather than "
            f"calling add_job(..., replace_existing=True).",
        )

    return Result(
        name,
        PASS,
        f"one fire, {delay:.2f}s after restart at {restart_at.isoformat()}\n"
        f"         {after[0].isoformat()}",
    )


# ---------------------------------------------------------------------------------------------
# Probe cadence injection
# ---------------------------------------------------------------------------------------------


def install_probe_cadence(interval_seconds: int) -> Cadence:
    """Point the real scheduler module at the probe job, and only the probe job.

    register_jobs() and build_scheduler() read `CADENCES` and `JOB_FUNCTIONS` from the scheduler
    module's namespace, so replacing both there is what makes the real registration path run
    against the probe. Replacing rather than appending keeps the run isolated: the heartbeat's own
    rows would otherwise interleave with the probe's in job_runs and have to be filtered back out.

    overdue_after is set well past the interval only to satisfy Cadence's own validation; nothing
    in this harness reads it.
    """
    probe = Cadence(
        job_name=PROBE_JOB_NAME,
        interval=timedelta(seconds=interval_seconds),
        overdue_after=timedelta(seconds=interval_seconds * 10),
    )
    cadence_module.CADENCES = (probe,)
    scheduler_module.CADENCES = (probe,)
    scheduler_module.JOB_FUNCTIONS = {PROBE_JOB_NAME: restart_probe_job}
    return probe


def probe_fires(url: str | None = None, since: datetime | None = None) -> list[datetime]:
    """Every recorded run of the probe, newest last. Read from job_runs, the real record."""
    sql = "SELECT started_at FROM job_runs WHERE job_name = %s"
    params: tuple = (PROBE_JOB_NAME,)
    if since is not None:
        sql += " AND started_at >= %s"
        params = (PROBE_JOB_NAME, since)
    sql += " ORDER BY started_at"
    with db.connection(url) as conn:
        return [row[0] for row in conn.execute(sql, params).fetchall()]


def remove_probe_from_jobstore(url: str | None = None) -> bool:
    """Delete the probe's row from apscheduler_jobs. ALWAYS run, even on failure.

    Without this the probe outlives the verification run. register_jobs() only adds and modifies
    the jobs in the cadence table — it never removes ones it does not recognise — so a leftover
    probe would sit in the persistent store and keep firing under the production scheduler, which
    would resolve its pickled import path to this very module and run it. A verification harness
    that leaves a job running in production is worse than no harness.

    This touches only apscheduler_jobs, never job_runs: those rows are the record of the
    verification and job_runs is append-only by trigger (CLAUDE.md § 12).
    """
    with db.connection(url) as conn:
        # The job store creates its table on the scheduler's FIRST start, so on a fresh database
        # this runs before the table exists. That is "nothing to clean", not a failure - and it
        # has to be checked rather than caught, because an UndefinedTable error aborts the
        # transaction and would take the commit down with it.
        table_exists = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (scheduler_module.JOBSTORE_TABLE,)
        ).fetchone()[0]
        if not table_exists:
            return False

        deleted = conn.execute(
            f"DELETE FROM {scheduler_module.JOBSTORE_TABLE} WHERE id = %s",  # noqa: S608
            (PROBE_JOB_NAME,),
        ).rowcount
        conn.commit()
    return bool(deleted)


# ---------------------------------------------------------------------------------------------
# Child process: the scheduler that gets stopped and started
# ---------------------------------------------------------------------------------------------


def run_child(interval_seconds: int, run_seconds: float) -> int:
    """One scheduler lifetime. Started, allowed to run, shut down."""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    install_probe_cadence(interval_seconds)

    scheduler = scheduler_module.build_scheduler()
    scheduler_module.start(scheduler)
    print(f"CHILD: started, probe every {interval_seconds}s", flush=True)
    try:
        time.sleep(run_seconds)
    finally:
        scheduler.shutdown(wait=True)
        print("CHILD: stopped", flush=True)
    return 0


def _spawn_child(interval_seconds: int, run_seconds: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "verify.restart_recovery",
            "--child",
            "--interval-seconds",
            str(interval_seconds),
            "--run-seconds",
            str(run_seconds),
        ],
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------------------------
# Parent: orchestrate the outage
# ---------------------------------------------------------------------------------------------


def run_check(
    interval_seconds: int,
    stop_for_seconds: float,
    promptness_seconds: float,
    observe_seconds: float,
) -> list[Result]:
    results: list[Result] = []

    install_probe_cadence(interval_seconds)
    remove_probe_from_jobstore()

    # Phase 1: establish the job in the persistent store and let it fire at least once, so there
    # is a real schedule for the outage to interrupt.
    first_run_seconds = interval_seconds + 2
    print(f"phase 1: running the scheduler for {first_run_seconds}s to establish the schedule")
    first = _spawn_child(interval_seconds, first_run_seconds)
    if first.returncode != 0:
        return [
            Result(
                "restart produces exactly one prompt catch-up fire",
                FAIL,
                f"observed: the first scheduler process exited {first.returncode}\n"
                f"         stdout: {first.stdout.strip()[-500:]}\n"
                f"         stderr: {first.stderr.strip()[-500:]}",
            )
        ]

    before_outage = probe_fires()
    print(f"phase 1: {len(before_outage)} fire(s) recorded")
    if not before_outage:
        return [
            Result(
                "restart produces exactly one prompt catch-up fire",
                FAIL,
                f"observed: the probe never fired during phase 1 ({first_run_seconds}s at a "
                f"{interval_seconds}s interval)\n"
                f"         there is no established schedule for an outage to interrupt, so the "
                f"restart assertion below would be meaningless. Check job_runs and the child's "
                f"output:\n         {first.stdout.strip()[-500:]}",
            )
        ]

    # Phase 2: the outage. The process is genuinely gone for this whole window.
    missed_slots = int(stop_for_seconds // interval_seconds)
    print(f"outage: {stop_for_seconds}s with no scheduler at all (~{missed_slots} missed slot(s))")
    time.sleep(stop_for_seconds)

    # Phase 3: restart, and watch.
    restart_at = datetime.now(timezone.utc)
    print(f"phase 3: restarting at {restart_at.isoformat()}, observing for {observe_seconds}s")
    second = _spawn_child(interval_seconds, observe_seconds)
    if second.returncode != 0:
        results.append(
            Result(
                "the restarted scheduler process starts cleanly",
                FAIL,
                f"observed: exit {second.returncode}\n"
                f"         stderr: {second.stderr.strip()[-800:]}",
            )
        )

    fires = probe_fires(since=restart_at)
    print(f"phase 3: {len(fires)} fire(s) recorded after restart")

    results.append(
        assess_catch_up(
            fires,
            restart_at=restart_at,
            promptness_seconds=promptness_seconds,
            interval_seconds=interval_seconds,
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stop and start a real scheduler process and assert exactly one prompt catch-up fire. "
            "Configuration tests are not evidence of restart behaviour (CLAUDE.md section 13)."
        )
    )
    # The probe obeys the cadence contract rather than bypassing it. CLAUDE.md § 12 forbids an
    # entry whose misfire grace meets or exceeds its interval, and the 60-second grace floor puts
    # a hard minimum of 61s on any interval - so the probe cannot use the few-second interval that
    # would make this check quick. A verification harness that needed an exemption from the rule
    # it verifies would not be verifying much.
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=90,
        help=(
            "probe interval. Must exceed the cadence grace floor of "
            f"{MINIMUM_MISFIRE_GRACE_SECONDS}s (CLAUDE.md section 12), so 61 is the practical "
            "minimum and 90 the default"
        ),
    )
    parser.add_argument(
        "--stop-for-seconds",
        type=float,
        default=270.0,
        help=(
            "how long the scheduler is gone. A whole multiple of the interval is what you want: "
            "phase 1 runs interval+2 seconds, so the restart then lands ~2s past a slot boundary, "
            "which is comfortably inside the grace window AND leaves almost a full interval "
            "before the next scheduled fire (the default 270/90 is 3 intervals)"
        ),
    )
    parser.add_argument(
        "--promptness-seconds",
        type=float,
        default=5.0,
        help="how soon after restart the catch-up must fire to count as prompt",
    )
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=None,
        help="how long the restarted process runs. Must be shorter than the interval; "
        "defaults to a quarter of it",
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="remove the probe job from the persistent job store and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the sequence that would run, and run none of it",
    )
    args = parser.parse_args(argv)

    if args.child:
        return run_child(args.interval_seconds, args.run_seconds)

    observe_seconds = (
        args.observe_seconds
        if args.observe_seconds is not None
        else max(2.0, args.interval_seconds / 4)
    )

    if args.dry_run:
        print(
            f"restart-recovery check would run:\n\n"
            f"  1. remove any stale {PROBE_JOB_NAME!r} row from "
            f"{scheduler_module.JOBSTORE_TABLE}\n"
            f"  2. spawn a real scheduler process for {args.interval_seconds + 2}s to establish "
            f"the schedule\n"
            f"  3. stop it, and stay stopped for {args.stop_for_seconds}s "
            f"(~{int(args.stop_for_seconds // args.interval_seconds)} missed slot(s))\n"
            f"  4. spawn a second real process and observe for {observe_seconds}s\n"
            f"  5. assert EXACTLY ONE fire, within {args.promptness_seconds}s of restart\n"
            f"  6. remove the probe from the job store again, pass or fail\n\n"
            f"Nothing is mocked: the real register_jobs(), the real @job decorator, the real\n"
            f"SQLAlchemyJobStore, and a process that is genuinely stopped and started.\n"
            f"job_runs rows written by the probe are left in place - they are the record of the\n"
            f"verification, and job_runs is append-only."
        )
        return 0

    if not os.environ.get("DATABASE_URL"):
        print(
            "DATABASE_URL is not set. Run: set -a; . ./.env; set +a",
            file=sys.stderr,
        )
        return 2

    if args.cleanup_only:
        removed = remove_probe_from_jobstore()
        print(f"probe job {'removed from' if removed else 'was not in'} the job store")
        return 0

    if args.interval_seconds <= MINIMUM_MISFIRE_GRACE_SECONDS:
        # Caught here as well as in Cadence, because the constructor error arrives as a traceback
        # out of the middle of a run that has already started doing things.
        print(
            f"--interval-seconds ({args.interval_seconds}) must be greater than the cadence grace "
            f"floor of {MINIMUM_MISFIRE_GRACE_SECONDS}s. At or below it the derived "
            f"misfire_grace_time meets or exceeds the interval, which CLAUDE.md § 12 rejects: such "
            f"a job can never record a `missed` row. Use 61 or more; the default is 90.",
            file=sys.stderr,
        )
        return 2

    if observe_seconds >= args.interval_seconds:
        print(
            f"--observe-seconds ({observe_seconds}) must be shorter than --interval-seconds "
            f"({args.interval_seconds}), or the observation window spans a scheduled slot and a "
            f"legitimate second fire is indistinguishable from broken coalescing.",
            file=sys.stderr,
        )
        return 2

    try:
        results = run_check(
            interval_seconds=args.interval_seconds,
            stop_for_seconds=args.stop_for_seconds,
            promptness_seconds=args.promptness_seconds,
            observe_seconds=observe_seconds,
        )
    finally:
        # Always, on every path. A probe left in the persistent store keeps firing in production.
        try:
            remove_probe_from_jobstore()
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: could not remove the probe job from "
                f"{scheduler_module.JOBSTORE_TABLE}: {exc}\n"
                f"Remove it before starting the production scheduler:\n"
                f"    python3 -m verify.restart_recovery --cleanup-only",
                file=sys.stderr,
            )

    print("\nrestart recovery\n")
    for result in results:
        print(result.render())
        print()

    from verify.preflight import exit_code

    return exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
