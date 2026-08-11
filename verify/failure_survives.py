"""Failure-survives check: the work rolls back, the record does not.

This is the observable consequence of CLAUDE.md § 12's rule that the `@job` decorator does its
bookkeeping on a SEPARATE connection, committed before the wrapped function is called. A probe job
writes a sentinel row and then raises. Afterwards:

    the `failed` row is present   AND   the sentinel is absent   AND   the exception propagated

ALL THREE, and the second one is the one that carries the argument. Asserting only the `failed`
row proves nothing about session separation: that row appears either way, because the decorator
writes it after the work has already unwound. It is the sentinel's ABSENCE alongside the record's
PRESENCE that shows the two were on different connections — the work's transaction died and the
bookkeeping's did not.

The third matters too. A decorator that recorded the failure and swallowed the exception would
satisfy the first two while leaving the scheduler believing the job succeeded (CLAUDE.md § 4: it
re-raises; it never swallows).

This check creates and drops its OWN scratch table. It writes to no table `app/` owns except
job_runs, via the decorator, and it never deletes from job_runs — the append-only trigger would
refuse, and working around it is not on the table.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration.job import job  # noqa: E402
from verify.preflight import FAIL, PASS, Result, exit_code  # noqa: E402

PROBE_JOB_NAME = "verify_failure_survives_probe"
SCRATCH_TABLE = "verify_failure_survives_scratch"
SENTINEL = "sentinel-written-by-the-work-then-rolled-back"
ERROR_MESSAGE = "verification probe failing deliberately after writing a sentinel"


class ProbeFailure(RuntimeError):
    """Raised by the probe on purpose. Its propagation is one of the three assertions."""


@job(PROBE_JOB_NAME)
def failing_probe_job(url: str | None = None):
    """Write a sentinel on the WORK's own connection, then raise before committing it.

    The insert and the raise are inside the same `with db.connection(...)` block, so the
    connection closes without a commit and the sentinel never lands. Meanwhile the decorator's
    `running` row was committed on a different connection before this function was entered, and
    the decorator will come back on a third connection to mark it `failed`.

    If the decorator shared this session, the raise would take its bookkeeping row down with the
    sentinel and job_runs would hold no record of the run at all.
    """
    with db.connection(url) as conn:
        conn.execute(f"INSERT INTO {SCRATCH_TABLE} (note) VALUES (%s)", (SENTINEL,))  # noqa: S608
        raise ProbeFailure(ERROR_MESSAGE)


# ---------------------------------------------------------------------------------------------
# The assertion — pure, so it is testable without a database
# ---------------------------------------------------------------------------------------------


def assess_failure_survives(
    failed_row_present: bool,
    error_message: str | None,
    sentinel_present: bool,
    exception_propagated: bool,
) -> Result:
    """All three conditions, with the sentinel's absence carrying the actual argument."""
    name = "the work rolls back, the failure record does not"
    observed = (
        f"observed: failed row present={failed_row_present}, "
        f"sentinel present={sentinel_present}, "
        f"exception propagated={exception_propagated}, "
        f"error_message={error_message!r}"
    )

    if not failed_row_present:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         job_runs holds NO failed row for {PROBE_JOB_NAME!r}. The bookkeeping row was "
            f"rolled back along with the work - the decorator is using the wrapped function's "
            f"session, so the failure that most needs a record is the one that did not get one "
            f"(CLAUDE.md § 12).",
        )

    if sentinel_present:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the sentinel SURVIVED. The work's insert was committed despite the work "
            f"raising, so the rollback this check depends on did not happen. Either the probe's "
            f"connection is autocommitting or something committed on its behalf - until that is "
            f"resolved, the presence of the failed row proves nothing about session separation.",
        )

    if not exception_propagated:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the exception did NOT propagate. The decorator recorded the failure and "
            f"swallowed it, so the scheduler believes this job succeeded. CLAUDE.md § 4: it "
            f"re-raises; it never swallows.",
        )

    if not error_message or ERROR_MESSAGE not in error_message:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the failed row does not carry the raised message. A failure record without "
            f"the reason is a row that says something went wrong and nothing else.",
        )

    return Result(
        name,
        PASS,
        f"failed row recorded with its message, sentinel absent, exception propagated\n"
        f"         error_message: {error_message}",
    )


# ---------------------------------------------------------------------------------------------
# The live check
# ---------------------------------------------------------------------------------------------


def _create_scratch_table(url=None):
    with db.connection(url) as conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {SCRATCH_TABLE} (note text)")  # noqa: S608
        conn.execute(f"DELETE FROM {SCRATCH_TABLE}")  # noqa: S608
        conn.commit()


def _drop_scratch_table(url=None):
    """Drops a table this harness created moments earlier, in this same run.

    CLAUDE.md § 1 puts DROP on the never-run list for the agent; this statement is written for a
    human to run and is scoped to a table with a `verify_` prefix that nothing else writes to.
    It never touches a table `app/` owns.
    """
    with db.connection(url) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")  # noqa: S608
        conn.commit()


def run_check(url: str | None = None) -> list[Result]:
    _create_scratch_table(url)
    try:
        exception_propagated = False
        try:
            failing_probe_job(url=url)
        except ProbeFailure:
            exception_propagated = True

        with db.connection(url) as conn:
            row = conn.execute(
                "SELECT status, error_message FROM job_runs WHERE job_name = %s"
                " ORDER BY run_id DESC LIMIT 1",
                (PROBE_JOB_NAME,),
            ).fetchone()
            sentinel_count = conn.execute(
                f"SELECT count(*) FROM {SCRATCH_TABLE} WHERE note = %s",  # noqa: S608
                (SENTINEL,),
            ).fetchone()[0]

        failed_row_present = bool(row) and row[0] == "failed"
        error_message = row[1] if row else None

        return [
            assess_failure_survives(
                failed_row_present=failed_row_present,
                error_message=error_message,
                sentinel_present=sentinel_count > 0,
                exception_propagated=exception_propagated,
            )
        ]
    finally:
        _drop_scratch_table(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a job that writes a sentinel and then raises. Assert the failure record survives "
            "and the work does not."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would run, and run none of it",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        print(
            f"failure-survives check would:\n\n"
            f"  1. create table {SCRATCH_TABLE} (its own; nothing in app/ touches it)\n"
            f"  2. call {PROBE_JOB_NAME!r}, which inserts a sentinel WITHOUT committing and raises\n"
            f"  3. assert ALL THREE:\n"
            f"       - job_runs holds a `failed` row carrying the raised message\n"
            f"       - the sentinel is ABSENT (the work's transaction unwound)\n"
            f"       - the exception propagated to the caller\n"
            f"  4. drop {SCRATCH_TABLE}\n\n"
            f"The sentinel's absence is the assertion that carries the argument: the `failed` row\n"
            f"appears whether or not the bookkeeping used a separate session, so on its own it\n"
            f"proves nothing. job_runs is never deleted from - the append-only trigger would\n"
            f"refuse, and it must not be worked around."
        )
        return 0

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set. Run: set -a; . ./.env; set +a", file=sys.stderr)
        return 2

    results = run_check()

    print("failure survives\n")
    for result in results:
        print(result.render())
        print()

    return exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
