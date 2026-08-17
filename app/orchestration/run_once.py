"""Run ONE registered job, once, and exit with its outcome.

WHAT THIS IS FOR. The first backup ever taken and the first restore test ever run are the two runs
most likely to reveal a problem, and both are otherwise reachable only by waiting for a cron slot
at 03:00. This is the command that runs them deliberately, from an SSM session, with a `job_runs`
row and an exit code.

THE DECISION THAT MATTERS: IT RESOLVES THE JOB FROM `JOB_FUNCTIONS` AND CALLS THE DECORATED
FUNCTION.

The obvious alternative - import the module and call the underlying callable by name - is one line
shorter and creates a SECOND EXECUTION PATH THAT WRITES NO `job_runs` ROW. The consequence is
precise and bad: the first backup ever taken, the one most likely to go wrong, would be the one
nothing recorded. It is the same shape as the rollback defect this stage began with - work that
happened, reported success, and left no trace downstream - arriving through a different door.

The @job decorator is what writes the row, so calling the decorated function is not a detail of how
this is implemented. It is the whole of what makes the run visible.

IT DOES NOT START THE SCHEDULER. Starting it to run one job would fire every OTHER due job as a
side effect - a `--run-once backup_nightly` on an instance that had been down would kick off four
ingests and a feature build, which is not what anybody typed. Nothing here touches
`apscheduler_jobs`: the persistent job store holds the schedule, and a one-off run is not a
schedule change.

CADENCE AGREEMENT IS CHECKED, using the scheduler's own function. A job runnable here but absent
from the cadence table is a job that runs when a human remembers and never otherwise, which is the
state the heartbeat cannot report on.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from app import db
from app.orchestration.scheduler import JOB_FUNCTIONS, check_cadence_agreement

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE = 2


def valid_names() -> list[str]:
    """Every job this command can run, sorted. The same registry the scheduler resolves from."""
    return sorted(JOB_FUNCTIONS)


def run_once(job_name: str, url: str | None = None):
    """Invoke one job's DECORATED function and return whatever it returns.

    Raises KeyError for an unknown name - the CLI turns that into a usage error with the list. The
    function is not resolved by import path or by attribute lookup on a module: it comes out of
    JOB_FUNCTIONS, which is the same mapping the scheduler registers from, so a job that runs here
    is by construction the job that runs on a schedule.
    """
    function = JOB_FUNCTIONS[job_name]
    # `url` is threaded through for tests; in production every job takes DATABASE_URL from the
    # environment exactly as the scheduler leaves it to.
    return function() if url is None else function(url=url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m app.orchestration.run_once",
        description=(
            "Run one registered job once and exit. Writes a job_runs row, exits non-zero if the "
            "job fails. Does NOT start the scheduler and does not change the schedule."
        ),
    )
    parser.add_argument("job_name", nargs="?", help="the job to run; omit to list the valid names")
    parser.add_argument(
        "--list", action="store_true", help="print the runnable job names and exit 0"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Before anything else, and using the SCHEDULER'S OWN CHECK rather than a second copy of it.
    check_cadence_agreement()

    if args.list:
        for name in valid_names():
            print(name)
        return EXIT_OK

    if args.job_name is None:
        print("no job named. Valid names:", file=sys.stderr)
        for name in valid_names():
            print(f"  {name}", file=sys.stderr)
        return EXIT_USAGE

    if args.job_name not in JOB_FUNCTIONS:
        # A LIST, NOT A TRACEBACK. This is run from an SSM session where a traceback scrolls off
        # and the useful line is the one naming what could have been typed instead.
        print(f"unknown job {args.job_name!r}. Valid names:", file=sys.stderr)
        for name in valid_names():
            print(f"  {name}", file=sys.stderr)
        return EXIT_USAGE

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        result = run_once(args.job_name)
    except Exception as exc:
        # THE EXIT CODE REFLECTS THE JOB'S OUTCOME, so a shell step can gate on it. The @job
        # decorator has already written the `failed` row and re-raised by the time this runs; this
        # turns the exception into an exit code without swallowing what it means.
        logger.error("job %r FAILED: %s: %s", args.job_name, type(exc).__name__, exc)
        print(
            f"\njob {args.job_name!r} failed: {type(exc).__name__}: {exc}\n"
            f"The failure is recorded in job_runs. Nothing was retried.",
            file=sys.stderr,
        )
        return EXIT_JOB_FAILED

    print(f"job {args.job_name!r} succeeded; rows_written={result!r}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
