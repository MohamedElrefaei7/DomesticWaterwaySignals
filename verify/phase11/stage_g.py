"""Stage G — after the first `backup_nightly`: the row is there, and it says the right things.

    python3 -m verify.phase11 g

THIS STAGE EXISTS BECAUSE THE ROW ONCE WAS NOT THERE. The Phase 11 defect Stage B audited: the
`backups` INSERT was discarded on close, the job returned, `job_runs` recorded success, and S3 held
a verified archive. Every layer agreed with itself. The only thing wrong was that the row was
missing, and nothing anywhere looked.

SO THE CENTRAL ASSERTION READS `backups` FROM A CONNECTION THIS VERIFIER OPENED, after the job's
process has exited. That is CLAUDE.md § 23's discipline: a test that verifies a write through the
session that made it cannot distinguish committed from uncommitted, and here the stronger claim is
available for free - the writer was a different PROCESS, so its transaction has certainly ended.

`apscheduler_jobs` IS CHECKED FIRST, and it is not a formality. § 12: the backup asserts its
`--exclude-table-data` target exists before dumping, and that table is created by
`SQLAlchemyJobStore`'s own DDL on the scheduler's first start rather than by a migration. On a
rebuilt instance where the backup runs before the scheduler has ever started, the backup refuses
with an error about an excluded table that says nothing about scheduler startup ordering. Checking
it first means the operator reads the real cause rather than a confusing symptom.

`rows_written IS NULL`, NOT `0`. § 4 and § 12: `NULL` means the job writes no rows to THIS database
and `0` means it counts rows and today counted none. The backup writes to S3. Accepting `0` here
would make the column mean two things depending on which job wrote it, which is precisely the
distinction the schema declines to collapse (no `NOT NULL`, no `DEFAULT 0`).

`row_counts` KEYS EQUAL THE CURRENT PUBLIC TABLE SET, in both directions. § 3: comparing only the
intersection hides a dropped table and an unexpected one, and per-table exactness is the entire
value of the monthly restore test that reads these counts later.
"""

from __future__ import annotations

from typing import Any, Sequence

from verify.phase11 import readonly
from verify.phase11.result import Check, CheckResult, failed, passed

BACKUP_JOB = "backup_nightly"

# backups.tf's lifecycle rules match `backups/daily/` and `backups/monthly/` and nothing else. An
# object written outside those prefixes gets NO retention at all - it is never expired and bills
# forever - so the prefix is not a naming convention, it is where retention applies. Stage H uses
# the same constant to tell a real backup from a probe row a human inserted.
RETAINED_PREFIX = "backups/"

# app/orchestration/backup.py's COUNTED_SCHEMA. row_counts keys are QUALIFIED (`public.job_runs`),
# so an unqualified comparison reports every table as both missing and unexpected.
COUNTED_SCHEMA = "public"


def check_apscheduler_jobs_exists(regclass: str | None) -> CheckResult:
    name = "public.apscheduler_jobs exists"
    expected = "to_regclass('public.apscheduler_jobs') is not null"
    if not regclass:
        return failed(
            name,
            expected,
            "null. It is created by SQLAlchemyJobStore's DDL on the scheduler's FIRST START, not "
            "by a migration, and the backup asserts its --exclude-table-data target exists before "
            "dumping (§ 3). On a rebuilt instance the backup therefore refuses with an error about "
            "an excluded table that says nothing about scheduler startup ordering. Start the "
            "scheduler once, then run the backup.",
        )
    return passed(name, expected, regclass)


def check_a_backup_row_exists(rows: Sequence[tuple]) -> CheckResult:
    """Read on a connection this process opened, after the job's process exited.

    The defect this stage exists for produced a successful job, a verified archive in S3, and no
    row. Every layer agreed with itself.
    """
    name = "the backup wrote a backups row"
    expected = ">= 1 row in public.backups"
    if not rows:
        return failed(
            name,
            expected,
            "0 rows. If job_runs shows a successful backup_nightly and this is empty, that is the "
            "Stage B defect exactly: the INSERT was discarded on close while every layer reported "
            "success. Check that the write path goes through session.writing().",
        )
    return passed(name, expected, f"{len(rows)} row(s)")


def check_rows_written_is_null(job_rows: Sequence[tuple[Any, Any]]) -> CheckResult:
    """`NULL`, never `0`. They are different claims and both are meaningful."""
    name = f"{BACKUP_JOB} recorded rows_written as NULL"
    expected = "rows_written IS NULL on the most recent successful run"
    if not job_rows:
        return failed(
            name,
            expected,
            f"no successful {BACKUP_JOB} run in job_runs. 'Last success' is the most recent "
            f"SUCCESS row's finished_at, never the most recent row of any status (§ 4).",
        )
    finished_at, rows_written = job_rows[0]
    if rows_written is not None:
        return failed(
            name,
            expected,
            f"rows_written={rows_written!r} (finished_at={finished_at}). `0` claims the job counts "
            f"rows written to THIS database and today counted none; the backup writes to S3. "
            f"Accepting 0 makes the column mean two things depending on which job wrote it.",
        )
    return passed(name, expected, f"NULL, finished_at={finished_at}")


def check_row_counts_keys_match_tables(
    row_counts: dict[str, Any] | None, tables: Sequence[str]
) -> CheckResult:
    """Exact set equality in both directions, with every mismatch reported (§ 3)."""
    name = "row_counts covers exactly the public tables"
    expected = f"{len(tables)} keys, equal to the current public-schema table set"

    if not isinstance(row_counts, dict):
        return failed(name, expected, f"row_counts is {type(row_counts).__name__}, not an object")

    observed = set(row_counts)
    wanted = set(tables)
    missing = sorted(wanted - observed)
    extra = sorted(observed - wanted)

    if missing or extra:
        parts = []
        if missing:
            parts.append(f"{len(missing)} table(s) absent from row_counts: {missing}")
        if extra:
            parts.append(f"{len(extra)} key(s) with no such table: {extra}")
        return failed(
            name,
            expected,
            "; ".join(parts)
            + ". Comparing only the intersection hides a dropped table AND an unexpected one, "
            "which is the whole value of the per-table counts (§ 3).",
        )
    return passed(name, expected, f"{len(observed)} keys, sets equal in both directions")


def check_backup_keys_are_under_the_retained_prefix(rows: Sequence[tuple]) -> CheckResult:
    """Every backup object is somewhere the lifecycle rules actually reach.

    backups.tf expires `backups/daily/` after 35 days and `backups/monthly/` after 400. An object
    written anywhere else is matched by no rule: it is never expired, it bills forever, and nothing
    reports it. This is also the discriminator Stage H uses to tell a real backup row from the
    probe row a human inserts during Stage F.
    """
    name = "every backup s3_key is under a retained prefix"
    expected = f"every s3_key starting with {RETAINED_PREFIX!r}"
    if not rows:
        return failed(name, expected, "0 rows, so this asserted nothing")

    stray = [
        f"backup_id={backup_id} s3://{bucket}/{key}"
        for backup_id, bucket, key, *_ in rows
        if not str(key).startswith(RETAINED_PREFIX)
    ]
    if stray:
        return failed(
            name,
            expected,
            f"{len(stray)} outside the retained prefixes: {stray}. backups.tf's lifecycle rules "
            f"match `backups/daily/` and `backups/monthly/` only, so these are never expired.",
        )
    return passed(name, expected, f"{len(rows)} row(s), all under {RETAINED_PREFIX!r}")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def read(conn) -> dict[str, Any]:
    readonly.assert_select_granted(conn)

    apscheduler = readonly.query(conn, "SELECT to_regclass('public.apscheduler_jobs')::text")[0][0]
    rows = readonly.query(
        conn,
        """
        SELECT backup_id, s3_bucket, s3_key, byte_size, row_counts, verified, verified_at
        FROM backups
        ORDER BY backup_id DESC
        """,
    )
    job_rows = readonly.query(
        conn,
        """
        SELECT finished_at, rows_written
        FROM job_runs
        WHERE job_name = %s AND status = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        [BACKUP_JOB],
    )
    # THE SAME QUERY THE BACKUP ITSELF USES, and qualified the same way.
    # app/orchestration/backup.py:233 reads `pg_tables WHERE schemaname = 'public'` and writes keys
    # as `public.<tablename>` (backup.py:249). This mirrors both rather than approximating them
    # with a pg_class walk: `pg_class relkind='r'` and `pg_tables` are not the same set, and a
    # comparison against a slightly different set would report a mismatch that is about the query
    # rather than about the backup.
    tables = [
        f"{COUNTED_SCHEMA}.{name}"
        for (name,) in readonly.query(
            conn,
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            [COUNTED_SCHEMA],
        )
    ]
    return {"apscheduler": apscheduler, "rows": rows, "job_rows": job_rows, "tables": tables}


def checks() -> Sequence[Check]:
    with readonly.connection() as conn:
        state = read(conn)

    latest = state["rows"][0] if state["rows"] else None
    row_counts = latest[4] if latest else None

    return [
        lambda: check_apscheduler_jobs_exists(state["apscheduler"]),
        lambda: check_a_backup_row_exists(state["rows"]),
        lambda: check_rows_written_is_null(state["job_rows"]),
        lambda: check_row_counts_keys_match_tables(row_counts, state["tables"]),
        lambda: check_backup_keys_are_under_the_retained_prefix(state["rows"]),
    ]
