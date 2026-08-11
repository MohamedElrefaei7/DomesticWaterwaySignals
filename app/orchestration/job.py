"""The @job decorator: every scheduled unit's record in job_runs.

CLAUDE.md § 4 in executable form. The whole design turns on one thing being true:

    THE BOOKKEEPING RUNS ON ITS OWN CONNECTION, AND THE `running` ROW IS COMMITTED BEFORE THE
    WRAPPED FUNCTION IS CALLED.

The tidier-looking alternative is to do the bookkeeping on the same session as the work: one
fewer connection, one fewer thing to close, and it reads better. It is also exactly wrong. When
the wrapped work rolls back, the `running` row rolls back with it — so the failure that most needs
a record is the one guaranteed not to have one, and job_runs ends up holding a clean history of
every run that succeeded. That is CLAUDE.md § 2's theme 1: a layer reporting success while the
thing downstream got nothing.

The decorator never swallows. A job that fails must fail loudly to the scheduler as well as to the
table — the scheduler's own error handling and this record are two different consumers, and
silencing the exception blinds one of them. (The single exception in this commit is the
heartbeat's alert sink, which swallows deliberately and says so at length; see heartbeat.py.)
"""

from __future__ import annotations

import contextvars
import functools
import logging
from datetime import datetime, timezone

from app import db

logger = logging.getLogger(__name__)

# Decision 10: nesting is prevented at RUNTIME, not by convention.
#
# CLAUDE.md § 4 requires exactly one @job per scheduled unit, never nested. A ContextVar rather
# than a module global because it is per-task and per-thread: APScheduler runs jobs in a thread
# pool, and a plain global would have one job's guard fire on another job's concurrent run.
#
# The harm a nested decorator does is not the duplicate row itself. It is that the inner job then
# has job_runs activity that is really the outer job's, so the cadence table gets an entry that
# never fires on its own — and the heartbeat reports it overdue forever, which trains everyone to
# ignore the heartbeat.
_ACTIVE_JOB: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_job", default=None
)


class NestedJobError(RuntimeError):
    """A @job was entered while another was already active. Names both."""


def active_job() -> str | None:
    """The job currently running in this context, if any. For tests and diagnostics."""
    return _ACTIVE_JOB.get()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rows_written_from(result, job_name: str):
    """Decision 9: NULL and 0 are distinct facts and both are meaningful.

    `0` means the job ran and wrote nothing. `NULL` means the job does not report a row count.
    Collapsing one into the other destroys the distinction that makes "wrote nothing" detectable,
    and "wrote nothing" is the shape of nearly every failure in CLAUDE.md § 2's theme 1 — ingest
    "worked" with a required field hardcoded to None; 29,650 rows fed nothing.

    bool is excluded explicitly because `True` is an int in Python and `rows_written = 1` is a
    materially wrong claim about what happened.

    A return value that is neither an int nor None is recorded as NULL and logged as a warning
    rather than raised on: the work already succeeded by the time we look at it, and marking a
    successful run `failed` over a return-type mistake would put a false failure in the permanent
    record. The warning names the job and the type so the mistake is still visible.
    """
    if result is None:
        return None
    if isinstance(result, bool):
        logger.warning(
            "job %r returned a bool; rows_written recorded as NULL. Return an int to report a "
            "row count, or None to report that this job does not count rows.",
            job_name,
        )
        return None
    if isinstance(result, int):
        return result

    logger.warning(
        "job %r returned %s; rows_written recorded as NULL. rows_written means rows WRITTEN TO "
        "THE DATABASE (CLAUDE.md § 4) - return that count as an int, or None.",
        job_name,
        type(result).__name__,
    )
    return None


def _open_run(url: str | None, job_name: str) -> int:
    """INSERT the `running` row and COMMIT, on a connection of our own. Decision 8.

    Committed before the caller is allowed to start work. Everything about this function's
    correctness is in the fact that it returns only after the commit.
    """
    with db.connection(url) as conn:
        run_id = conn.execute(
            "INSERT INTO job_runs (job_name, started_at, status) VALUES (%s, %s, 'running')"
            " RETURNING run_id",
            (job_name, _now()),
        ).fetchone()[0]
        conn.commit()
    return run_id


def _close_run(url: str | None, run_id: int, status: str, rows_written, error_message) -> None:
    """UPDATE the row this decorator created, on a fresh connection of its own.

    UPDATE, not INSERT: job_runs is append-only against DELETE (see 0002_job_runs.sql), and the
    trigger permits UPDATE precisely so a job can close the row it opened.

    A fresh connection rather than one held open for the duration of the job: a backfill can run
    for hours, and an idle connection held across it is a connection that a database restart,
    a network blip, or a server-side idle timeout will have quietly killed by the time it matters.
    """
    with db.connection(url) as conn:
        conn.execute(
            "UPDATE job_runs SET status = %s, finished_at = %s, rows_written = %s,"
            " error_message = %s WHERE run_id = %s",
            (status, _now(), rows_written, error_message, run_id),
        )
        conn.commit()


def job(job_name: str, *, url: str | None = None):
    """Wrap a scheduled unit so that every run of it is recorded.

    `url` exists for tests; in production every caller takes DATABASE_URL from the environment.

    The wrapped function's return value is passed through to the caller unchanged, and is also
    interpreted as rows_written per decision 9.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            already_active = _ACTIVE_JOB.get()
            if already_active is not None:
                # Raised BEFORE any row is written, so a nesting bug does not also litter
                # job_runs with rows for a job that never really ran.
                raise NestedJobError(
                    f"@job({job_name!r}) was entered while @job({already_active!r}) is already "
                    f"active. CLAUDE.md § 4: exactly one @job per scheduled unit, never nested. "
                    f"Nesting produces two job_runs rows for one logical unit, and the inner job "
                    f"then has a cadence entry that never fires on its own - which the heartbeat "
                    f"reports overdue forever. Call the inner function's undecorated body, or "
                    f"schedule {job_name!r} separately."
                )

            run_id = _open_run(url, job_name)
            token = _ACTIVE_JOB.set(job_name)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                # BaseException, not Exception: a job killed by KeyboardInterrupt or a timeout
                # implemented with a custom BaseException still deserves a record. The row is
                # closed and the exception continues on its way untouched.
                _close_run(
                    url,
                    run_id,
                    "failed",
                    None,
                    f"{type(exc).__name__}: {exc}",
                )
                raise
            finally:
                _ACTIVE_JOB.reset(token)

            _close_run(url, run_id, "success", _rows_written_from(result, job_name), None)
            return result

        wrapper.job_name = job_name
        # Kept reachable so a caller that legitimately needs this logic inside another job can
        # call the body directly instead of nesting decorators.
        wrapper.undecorated = fn
        return wrapper

    return decorator
