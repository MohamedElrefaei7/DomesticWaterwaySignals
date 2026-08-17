"""`writing()` — a connection whose transaction boundary is structural rather than remembered.

WHY THIS EXISTS.

`db.connection()` deliberately commits nothing implicitly (app/db.py), and that default is correct:
this project has places where the difference between committed and not committed is the entire
point of the code, and psycopg's own context manager — which commits on a clean exit — would make
that difference accidental.

The cost of that correct default is that every write path has to remember one line. In Phase 11 one
did not. The nightly backup's `backups` INSERT was discarded on close while the job returned
normally, `job_runs` recorded success, and S3 held a verified archive. Nothing anywhere reported a
problem, because from every layer's own point of view nothing had gone wrong.

Stage B's audit then measured how visible that class of defect was elsewhere: deleting each write
path's `conn.commit()` left EIGHT OF TEN paths' tests green. So the missing commit was not a lapse
peculiar to one job — it was the one instance that happened to be caught.

WHAT THIS HELPER CHANGES.

`writing()` moves the boundary from something each call site remembers into something the context
manager guarantees: commit on a clean exit, roll back on any exception, and always re-raise. A path
that forgets to commit no longer loses its work silently, because there is nothing left to forget.

WHAT IT DELIBERATELY DOES NOT COVER, AND WHY THE ALLOW-LIST IS EXPLICIT.

Three kinds of call site must keep using `db.connection` directly, and each is listed by name in
tests/orchestration/test_commit_helper.py's allow-list rather than being detected by a rule:

  * READ-ONLY PATHS. Committing a read-only transaction is harmless and meaningless, and routing
    them through a helper named `writing` would make the read-only property harder to see, not
    easier. The API's connection is the sharpest case — CLAUDE.md § 20 makes read-only a contract
    it connects as a role that cannot write.

  * THE MIGRATION RUNNER. Its transaction boundaries ARE its correctness argument (CLAUDE.md § 3
    and § 12): one transaction per file with the `schema_migrations` insert inside it, and a
    `-- migrate:no-transaction` path that is knowingly non-atomic. A helper that commits on exit
    would be wrong there in a way that is very hard to see afterwards.

  * CONNECTIONS WHOSE TRANSACTION IS THE POINT. The backup job's counting connection holds an
    exported snapshot open across the dump and ends it with a literal `COMMIT`; the restore test's
    throwaway connections run with `autocommit=True` against a container that is about to be
    destroyed.

An allow-list by exact set equality, rather than a heuristic, is the same discipline as the
security-group ingress (§ 8), the ufw port set (§ 11) and the published-port set (§ 22): a new
call site fails until somebody writes down which of the three it is, and a REMOVED one fails too,
so the list cannot quietly go stale.
"""

from __future__ import annotations

import contextlib
import logging

from app import db

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def writing(url: str | None = None):
    """A connection that COMMITS on a clean exit and ROLLS BACK on any exception.

    It never swallows. The @job decorator's contract (CLAUDE.md § 4) is that a failing job fails
    loudly to the scheduler as well as to `job_runs`, and a context manager that returned True from
    its exception path would break exactly that, silently, for every path routed through it.

    `BaseException` rather than `Exception`, matching the @job decorator for the same reason: a job
    killed by KeyboardInterrupt or by a timeout implemented as a custom BaseException must not
    leave a half-written transaction to be committed by something later. The rollback is explicit
    even though closing the connection would discard the transaction anyway — the explicit call is
    what a test can observe, and "the close would have handled it" is exactly the kind of reasoning
    that stops being true when somebody introduces pooling.

    A failure to roll back is logged and dropped so that the ORIGINAL exception is what propagates.
    Raising from the rollback would replace the cause of the failure with a consequence of it,
    which is the report that sends an operator to the wrong layer.
    """
    with db.connection(url) as conn:
        try:
            yield conn
        except BaseException:
            try:
                conn.rollback()
            except Exception:  # pragma: no cover - only reachable on a broken connection
                logger.exception(
                    "rollback failed while unwinding; re-raising the ORIGINAL exception. The "
                    "transaction is discarded on close regardless."
                )
            raise
        conn.commit()
