"""The monthly restore test: download from S3, restore into a throwaway, compare exactly.

A backup nobody has restored is a backup nobody knows they have. Part 6's job verifies that an
archive can be READ - `pg_restore -f /dev/null` walks every block and reconstructs every object in
memory. This one verifies that it can be RESTORED INTO A DATABASE, which is a different claim:
extension state, hypertable metadata, roles and grants all live outside the block stream.

The decisions that carry the most weight, in order:

  - THE ARCHIVE IS DOWNLOADED FROM S3, never read from local staging. The local file passing proves
    nothing about what is in the bucket, and on a healthy instance the local file was deleted the
    moment its upload was verified. This is also the only thing that exercises the IAM READ path,
    which is otherwise untested until the day it matters.
  - THE RESTORE IS WRAPPED IN timescaledb_pre_restore() / timescaledb_post_restore(). Without them
    the restore APPEARS TO SUCCEED while hypertable and chunk metadata is wrong - CLAUDE.md § 2's
    theme 1 exactly, surfacing much later as queries that return plausible partial results.
  - ROLES ARE CREATED, NOT STRIPPED. `--no-owner --no-privileges` makes any restore succeed, at the
    cost of never exercising the grants - and the read-only `waterway_api` role is a proven
    invariant of this system (§ 20). After restoring, that role is made to attempt a DELETE and
    must be refused. That assertion is the only thing proving the security property is IN THE
    BACKUP rather than only in production.
  - COUNTS MUST MATCH EXACTLY, IN BOTH KEY DIRECTIONS, WITH NO TOLERANCE. Comparing only the
    intersection hides both a dropped table and an unexpected new one. A tolerance of any size is
    a tolerance for exactly the loss this job exists to detect.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.orchestration import backup, session
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "restore_test_monthly"

SCRATCH_DIR = Path("/mnt/data/restore-test")

# The read-only role whose refusal to write is a proven invariant of this system (CLAUDE.md § 20).
READ_ONLY_ROLE = "waterway_api"

# The one table whose restored count legitimately differs from the recorded one, because Part 6
# excluded its DATA while keeping its DDL.
EXPECTED_EMPTY_TABLE = f"{backup.COUNTED_SCHEMA}.{backup.EXCLUDED_DATA_TABLE}"

CONTAINER_PREFIX = "dws-restore-test-"
RESTORE_DB = "restore_probe"
RESTORE_USER = "postgres"
CONTAINER_READY_TIMEOUT = 120


class RestoreTestError(RuntimeError):
    """The restore test did not prove the backup restorable. No verification mark is written."""


@dataclass
class Throwaway:
    """A disposable TimescaleDB container: unique name, own network, no published ports."""

    name: str
    port: int
    scratch: Path
    logs: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"postgresql://{RESTORE_USER}:probe@127.0.0.1:{self.port}/{RESTORE_DB}"


# ---------------------------------------------------------------------------------------------
# Selecting and fetching the archive
# ---------------------------------------------------------------------------------------------


def most_recent_verified(conn) -> dict:
    """The newest `backups` row with `verified = true`.

    Only verified rows exist by construction - Part 6 writes no row for a failed run and never a
    `verified = false` placeholder - but the predicate is written anyway, because a query that
    relies on an invariant holding elsewhere breaks silently when that invariant is relaxed.
    """
    row = conn.execute(
        "SELECT backup_id, s3_bucket, s3_key, byte_size, row_counts, compressed_chunks "
        "FROM backups WHERE verified ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RestoreTestError(
            "no verified backup on record. There is nothing to restore, which is itself the "
            "finding: either the nightly job has never succeeded or its rows were removed."
        )
    return {
        "backup_id": row[0],
        "s3_bucket": row[1],
        "s3_key": row[2],
        "byte_size": row[3],
        "row_counts": row[4] if isinstance(row[4], dict) else json.loads(row[4]),
        "compressed_chunks": row[5],
    }


def download_archive(s3, bucket: str, key: str, destination: Path) -> Path:
    """FROM S3. Never from a local staging copy.

    The local file passing proves nothing about what is in the bucket - and on a healthy instance
    it does not exist, because Part 6 deletes it once the upload is verified. Downloading is also
    the only exercise the IAM read path gets before the day somebody needs it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(destination))
    if not destination.exists() or destination.stat().st_size == 0:
        raise RestoreTestError(
            f"download of s3://{bucket}/{key} produced no bytes at {destination}"
        )
    return destination


def check_free_space(scratch: Path, byte_size: int, usage=shutil.disk_usage) -> int:
    """The archive AND the restored database both land here, alongside production data."""
    free = usage(str(scratch)).free
    required = int(byte_size * 3)
    if free < required:
        raise RestoreTestError(
            f"refusing to start: {free} bytes free on {scratch}, need {required} (3x the "
            f"{byte_size}-byte archive: the download plus the restored database). Filling this "
            f"volume would take production down with it."
        )
    return free


# ---------------------------------------------------------------------------------------------
# The throwaway container
# ---------------------------------------------------------------------------------------------


def container_name() -> str:
    """A random suffix, so two runs cannot collide and neither can collide with production."""
    return f"{CONTAINER_PREFIX}{secrets.token_hex(6)}"


def assert_no_such_container(name: str, run=subprocess.run) -> None:
    """A name collision is FATAL, never "reuse the existing one".

    Reusing a container of that name would restore into whatever is already there, and if the name
    ever collided with a production service the restore would land on production data. The random
    suffix makes this near-impossible; the check is what makes "near" not matter.
    """
    completed = run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    if (completed.stdout or "").strip():
        raise RestoreTestError(
            f"a container named {name!r} already exists. Refusing to reuse it: restoring into an "
            f"existing container writes into whatever is already in it."
        )


def start_throwaway(image: str, scratch: Path, run=subprocess.run, port: int = 0) -> Throwaway:
    """Start the container on the SAME pinned digest as production.

    Same digest for Part 6's reason and one more: a TimescaleDB extension version mismatch on
    restore produces errors that read like data corruption, so a restore test on a different
    version would report a false failure in the one place a false failure is most expensive.

    NO PUBLISHED PORTS on a public interface - bound to 127.0.0.1 on an ephemeral port. And never
    `docker compose run` against the production stack, where a stray `docker compose down` would
    sweep it and a naming collision would be a genuinely bad afternoon.
    """
    name = container_name()
    assert_no_such_container(name, run=run)
    scratch.mkdir(parents=True, exist_ok=True)

    completed = run(
        [
            "docker", "run", "-d",
            "--name", name,
            # Loopback only. An ephemeral port avoids colliding with anything already bound.
            "-p", f"127.0.0.1:{port}:5432",
            "-e", f"POSTGRES_PASSWORD=probe",
            "-e", f"POSTGRES_DB={RESTORE_DB}",
            "-v", f"{scratch}:{scratch}",
            image,
        ],
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RestoreTestError(
            f"could not start the throwaway container: {(completed.stderr or '').strip()}"
        )

    published = run(
        ["docker", "port", name, "5432/tcp"], capture_output=True, text=True
    )
    mapped = (published.stdout or "").strip().splitlines()
    if not mapped:
        raise RestoreTestError(f"container {name} published no port for 5432")
    resolved_port = int(mapped[0].rsplit(":", 1)[1])

    return Throwaway(name=name, port=resolved_port, scratch=scratch)


def teardown(throwaway: Throwaway | None, run=subprocess.run, keep_logs: bool = False) -> None:
    """`docker rm -f`, in a `finally` that survives KeyboardInterrupt.

    ON FAILURE THE LOGS ARE CAPTURED FIRST. Tearing down the evidence at the moment it becomes
    useful is a small tragedy that recurs, so the logs are read off the container before it is
    removed and the caller reports where they went.
    """
    if throwaway is None:
        return
    if keep_logs:
        completed = run(
            ["docker", "logs", "--tail", "200", throwaway.name],
            capture_output=True, text=True,
        )
        throwaway.logs = ((completed.stdout or "") + (completed.stderr or "")).splitlines()
    run(["docker", "rm", "-f", throwaway.name], capture_output=True, text=True)


def wait_until_ready(throwaway: Throwaway, run=subprocess.run, timeout: int = CONTAINER_READY_TIMEOUT,
                     sleep=time.sleep) -> None:
    """Ready means A REAL QUERY SUCCEEDS FROM OUTSIDE, not that pg_isready said so.

    `pg_isready` is not sufficient and the reason is a genuine trap: the official Postgres image
    runs a TEMPORARY server on a unix socket while initdb sets the database up, then shuts it down
    and starts the real one. `pg_isready` inside the container answers YES to that temporary
    server, so a readiness check built on it returns during initialisation and the restore that
    follows hits a database that is about to be restarted underneath it.

    Measured: this passed in isolation every time and errored under full-suite load, which is the
    signature of exactly that race. Connecting over the published port, from outside, and running
    a query is the check that cannot be satisfied by the temporary server.
    """
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        completed = run(
            ["docker", "exec", throwaway.name, "pg_isready", "-U", RESTORE_USER],
            capture_output=True, text=True,
        )
        if completed.returncode == 0:
            try:
                with db.connection(throwaway.url) as conn:
                    conn.execute("SELECT 1").fetchone()
                return
            except Exception as exc:  # noqa: BLE001 - any connection failure means not ready yet
                last_error = exc
        sleep(1)

    raise RestoreTestError(
        f"throwaway container {throwaway.name} was not accepting queries on port "
        f"{throwaway.port} after {timeout}s. Last error: {last_error}"
    )


# ---------------------------------------------------------------------------------------------
# The restore
# ---------------------------------------------------------------------------------------------


def roles_in_use(conn) -> list[str]:
    """Every role the archive will reference: object owners, schema owners, and the read-only role.

    DISCOVERED FROM THE SOURCE, not listed here. A hardcoded list is a second copy of a fact the
    database already holds, and the copy is what goes stale - measured, the first time this ran:
    only `waterway_api` was created, and the restore failed on `ALTER SCHEMA public OWNER TO
    <owner>` because the OWNER role had not been thought of. The archive references every owner of
    every object in it, not just the interesting one.
    """
    rows = conn.execute(
        "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = %s "
        "UNION SELECT DISTINCT nspowner::regrole::text FROM pg_namespace "
        "WHERE nspname = %s "
        "UNION SELECT current_user",
        (backup.COUNTED_SCHEMA, backup.COUNTED_SCHEMA),
    ).fetchall()

    roles = {row[0] for row in rows if row[0]}
    # The read-only role may own nothing at all - it holds GRANTs, not objects - so it would not
    # appear above. It is the one role whose restoration this job actually asserts.
    roles.add(READ_ONLY_ROLE)
    return sorted(roles)


def create_roles(conn, roles=(READ_ONLY_ROLE,)) -> None:
    """Create every role the archive references BEFORE restoring.

    The alternative is `--no-owner --no-privileges`, which makes the restore succeed by discarding
    exactly the thing worth checking. A backup whose grants were never restored is a backup that
    cannot be used to rebuild this system's security posture, and nothing would say so.
    """
    for role in roles:
        conn.execute(
            f'DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'{role}\') '
            f'THEN CREATE ROLE "{role}" NOLOGIN; END IF; END $$'
        )


def restore_command(*, image: str, archive_path: Path, port: int, scratch: Path) -> list[str]:
    """pg_restore into the throwaway. NO --no-owner, NO --no-privileges."""
    return [
        "docker", "run", "--rm", "--network", "host",
        "-v", f"{scratch}:{scratch}",
        "-e", "PGPASSWORD=probe",
        image,
        "pg_restore",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--username", RESTORE_USER,
        "--dbname", RESTORE_DB,
        # Exit non-zero if anything at all failed, rather than restoring what it can and reporting
        # success - which is the whole failure mode this job exists to detect.
        "--exit-on-error",
        str(archive_path),
    ]


def restore(
    throwaway: Throwaway, image: str, archive_path: Path, run=subprocess.run, roles=(READ_ONLY_ROLE,)
) -> None:
    """pre_restore -> pg_restore -> post_restore, in that order, reconnecting between.

    WITHOUT THE WRAPPER the restore appears to succeed while hypertable and chunk metadata is
    wrong. The symptom arrives much later, as queries returning plausible partial results over
    chunks the catalog no longer knows about.
    """
    with db.connection(throwaway.url, autocommit=True) as conn:
        create_roles(conn, roles)
        conn.execute("SELECT timescaledb_pre_restore()")

    completed = run(
        restore_command(
            image=image, archive_path=archive_path,
            port=throwaway.port, scratch=throwaway.scratch,
        ),
        capture_output=True, text=True,
    )

    # post_restore runs on a NEW connection whatever happened, because pre_restore has left the
    # extension in a state that must not be the state the container is abandoned in.
    with db.connection(throwaway.url, autocommit=True) as conn:
        conn.execute("SELECT timescaledb_post_restore()")

    if completed.returncode != 0:
        raise RestoreTestError(
            f"pg_restore exited {completed.returncode}:\n{(completed.stderr or '').strip()[:2000]}"
        )


def analyze(conn) -> None:
    """A restored database has NO PLANNER STATISTICS. ANALYZE is part of restoring (§ 3)."""
    conn.execute("ANALYZE")


def assert_statistics_exist(conn) -> str:
    """Assert ANALYZE's EFFECT, not its invocation.

    A step that runs ANALYZE and never checks it ran is a step that quietly stops running - the
    call gets moved, or wrapped in a condition, or the connection it ran on turns out to have been
    rolled back, and nothing anywhere says the planner is flying blind.
    """
    row = conn.execute(
        "SELECT relname, last_analyze IS NOT NULL OR last_autoanalyze IS NOT NULL "
        "FROM pg_stat_user_tables ORDER BY n_live_tup DESC NULLS LAST LIMIT 1"
    ).fetchone()

    if row is None:
        raise RestoreTestError(
            "no user tables in pg_stat_user_tables after restore - ANALYZE had nothing to do, "
            "which means the restore landed nothing."
        )
    if not row[1]:
        raise RestoreTestError(
            f"ANALYZE left no statistics on {row[0]!r}: pg_stat_user_tables reports neither "
            f"last_analyze nor last_autoanalyze. The restored database's planner has no "
            f"statistics, and every query against it will choose plans from defaults."
        )
    return row[0]


# ---------------------------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------------------------


def restored_counts(conn) -> dict[str, int]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (backup.COUNTED_SCHEMA,),
        ).fetchall()
    ]
    counts = {}
    for table in tables:
        counts[f"{backup.COUNTED_SCHEMA}.{table}"] = int(
            conn.execute(
                f'SELECT count(*) FROM "{backup.COUNTED_SCHEMA}"."{table}"'
            ).fetchone()[0]
        )
    return counts


def compare_counts(recorded: dict[str, int], restored: dict[str, int]) -> None:
    """Exact, both directions, every mismatch reported.

    NO TOLERANCE. A tolerance of any size is a tolerance for exactly the loss this job detects, and
    "±1%" over a large table is thousands of rows.

    BOTH DIRECTIONS. Comparing only the intersection hides a dropped table (recorded, not restored)
    AND an unexpected one (restored, not recorded) - and the second matters because it means the
    archive contains something production does not.

    EVERY MISMATCH, not the first. Stopping at the first turns one investigation into as many
    round trips as there are broken tables.
    """
    missing = sorted(set(recorded) - set(restored))
    unexpected = sorted(set(restored) - set(recorded))

    problems = []
    if missing:
        problems.append(f"tables recorded but NOT RESTORED: {missing}")
    if unexpected:
        problems.append(f"tables restored but NOT RECORDED: {unexpected}")

    mismatched = []
    for name in sorted(set(recorded) & set(restored)):
        expected = recorded[name]
        # The one legitimate difference, asserted explicitly rather than skipped. Part 6 excluded
        # this table's DATA while keeping its DDL, so it must come back EMPTY - and asserting the
        # expected difference is what proves the exclusion worked rather than assuming it.
        if name == EXPECTED_EMPTY_TABLE:
            if restored[name] != 0:
                mismatched.append(
                    f"{name}: expected 0 rows (its data is excluded from every dump), "
                    f"restored {restored[name]}"
                )
            continue
        if restored[name] != expected:
            mismatched.append(f"{name}: recorded {expected}, restored {restored[name]}")

    if mismatched:
        problems.append("row counts differ:\n    " + "\n    ".join(mismatched))

    if problems:
        raise RestoreTestError(
            "the restored database does not match the recorded snapshot.\n  "
            + "\n  ".join(problems)
        )


def compare_compressed_chunks(recorded: int, conn) -> int:
    """Compression surviving a dump/restore cycle is not something to assume."""
    restored = int(
        conn.execute(
            "SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed"
        ).fetchone()[0]
    )
    if restored != recorded:
        raise RestoreTestError(
            f"compressed chunk count differs: recorded {recorded}, restored {restored}. "
            f"Compression is a headline measurement for this project and is exactly the kind of "
            f"thing that silently does not survive a restore."
        )
    return restored


def assert_read_only_role_cannot_delete(conn, table: str, role: str = READ_ONLY_ROLE) -> None:
    """THE ONLY ASSERTION THAT PROVES THE SECURITY PROPERTY IS IN THE BACKUP.

    A restore with `--no-owner --no-privileges` succeeds and leaves this untestable. Making the
    restored role actually attempt a write is the difference between "the grants restored" and
    "the grants were never exercised".
    """
    conn.execute(f"SET LOCAL ROLE {role}")
    try:
        conn.execute(f"DELETE FROM {table} WHERE false")
    except Exception:
        conn.execute("RESET ROLE")
        return
    conn.execute("RESET ROLE")
    raise RestoreTestError(
        f"the restored {role!r} role was permitted to DELETE from {table}. The read-only property "
        f"is a proven invariant of this system in production (CLAUDE.md § 20); a backup that does "
        f"not carry it cannot be used to rebuild it."
    )


def mark_verified(conn, backup_id: int, counts: dict[str, int], notes: str) -> None:
    """UPDATES EXACTLY THE THREE COLUMNS THE 0026 TRIGGER PERMITS.

    Touching any other column in the same statement raises in the database, which is the point of
    enforcing insert-once structurally rather than by convention.
    """
    conn.execute(
        "UPDATE backups SET restore_verified_at = %s, restore_verified_counts = %s, "
        "restore_notes = %s WHERE backup_id = %s",
        (datetime.now(timezone.utc), json.dumps(counts), notes, backup_id),
    )


# ---------------------------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------------------------


@job(JOB_NAME)
def restore_test_monthly_job(
    url: str | None = None,
    *,
    scratch_dir: Path = SCRATCH_DIR,
    s3=None,
    run=subprocess.run,
) -> None:
    """Returns None, so `rows_written` is NULL. Same reasoning as the backup job."""
    if s3 is None:  # pragma: no cover - exercised on the instance, injected in tests
        import boto3

        s3 = boto3.client("s3")

    image = backup.timescaledb_image()

    with db.connection(url) as conn:
        record = most_recent_verified(conn)
        # Read from the SOURCE, so the throwaway gets every role the archive names.
        roles = roles_in_use(conn)

    scratch_dir.mkdir(parents=True, exist_ok=True)
    check_free_space(scratch_dir, record["byte_size"])

    archive_path = scratch_dir / Path(record["s3_key"]).name
    download_archive(s3, record["s3_bucket"], record["s3_key"], archive_path)

    throwaway = None
    succeeded = False
    try:
        throwaway = start_throwaway(image, scratch_dir, run=run)
        wait_until_ready(throwaway, run=run)
        restore(throwaway, image, archive_path, run=run, roles=roles)

        with db.connection(throwaway.url, autocommit=True) as restored:
            analyze(restored)
            largest = assert_statistics_exist(restored)

            counts = restored_counts(restored)
            compare_counts(record["row_counts"], counts)
            chunks = compare_compressed_chunks(record["compressed_chunks"], restored)
            assert_read_only_role_cannot_delete(
                restored, f'"{backup.COUNTED_SCHEMA}"."{largest}"'
            )

        # ONLY AFTER EVERY ASSERTION. Marking before them would leave a verification mark on a
        # backup whose restore then failed, which is worse than no mark at all.
        with session.writing(url) as conn:
            mark_verified(
                conn,
                record["backup_id"],
                counts,
                f"{len(counts)} tables compared, {chunks} compressed chunks, "
                f"restored from s3://{record['s3_bucket']}/{record['s3_key']}",
            )

        succeeded = True
        logger.info(
            "restore test passed for backup %d: %d tables compared, %d compressed chunks "
            "on both sides",
            record["backup_id"], len(counts), chunks,
        )
    finally:
        # RUNS ON EVERY EXIT PATH, INCLUDING KeyboardInterrupt. A throwaway container that survives
        # a Ctrl-C holds a database's worth of disk on the same volume as production.
        teardown(throwaway, run=run, keep_logs=not succeeded)
        if succeeded:
            archive_path.unlink(missing_ok=True)
        elif throwaway is not None:
            logger.error(
                "restore test FAILED. Evidence kept: archive at %s, container logs (last 200 "
                "lines) captured before removal:\n%s",
                archive_path, "\n".join(throwaway.logs[-200:]),
            )

    return None
