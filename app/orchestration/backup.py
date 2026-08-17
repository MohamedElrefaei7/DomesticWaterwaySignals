"""The nightly backup: dump, count inside the dump's own snapshot, verify by restore, upload.

Every decision here exists because of one sentence in CLAUDE.md § 2: a `pg_dump` exited zero and
wrote a third of a file. The archive matched its own SHA-256 across three machines and passed
`pg_restore --list` cleanly. It failed on restore.

So:

  - VERIFICATION IS A FULL RESTORE TO /dev/null, with exit 0 AND EMPTY STDERR. `--list` reads only
    the archive's table of contents, which is why the truncated dump passed it.
  - NOTHING IS PIPED THROUGH STDOUT. `docker exec ... pg_dump | cat > file` reintroduces the
    truncation class directly: a broken pipe with a zero exit is exactly "exits zero, writes a
    third of a file". The container writes with `-f` to a bind mount.
  - COUNTS COME FROM INSIDE THE DUMP'S OWN SNAPSHOT, via `pg_export_snapshot()` +
    `pg_dump --snapshot`. Counting on a normal connection before or after the dump is one fewer
    moving part and is wrong: ingest runs concurrently, so those counts describe a database state
    the archive does not contain. The monthly restore test built on them would then either fail
    spuriously or acquire a tolerance wide enough to hide real loss.
  - THE DUMP RUNS IN A CONTAINER OFF THE PINNED SERVER DIGEST. A host `pg_dump` at a different
    major version than the server either refuses or produces a subtly wrong archive. Same digest
    means the client and server versions match mechanically rather than by someone remembering.

A failed run writes NO `backups` row. The failure lives in `job_runs`, where failures belong.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app import db
from app.orchestration import session
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "backup_nightly"

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# STAGING IS ON /mnt/data, NEVER /tmp OR THE ROOT VOLUME. A dump that fills root takes the instance
# down; a dump that fills /mnt/data is bad but recoverable and does not stop the stack from booting.
STAGING_DIR = Path("/mnt/data/backups")

# The scheduler table whose DATA is excluded. Restoring stale next_run_time values is worse than
# restoring none - the scheduler would wake up believing it had missed runs it had not.
EXCLUDED_DATA_TABLE = "apscheduler_jobs"

# Extension-owned schemas. Hypertable counts on the PARENT table already span every chunk, so
# counting _timescaledb_internal would double-count the same rows under names that do not survive
# a restore anyway.
COUNTED_SCHEMA = "public"

DAILY_PREFIX = "backups/daily/"
MONTHLY_PREFIX = "backups/monthly/"

# The size floor is a SECOND, INDEPENDENT GATE - not a substitute for the restore check. A dump can
# be the right size and unrestorable, and it can be small for a legitimate reason.
SIZE_FLOOR_FRACTION = 0.5

# Refuse before dumping if free space is below this multiple of the last successful archive.
# A pre-flight refusal is a clean failure; a partial dump that fills the volume is an outage.
FREE_SPACE_MULTIPLE = 2.0

_IMAGE_RE = re.compile(r"^\s*image:\s*(?P<reference>\S+)\s*$")
_SERVICE_RE = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")


class BackupError(RuntimeError):
    """The backup did not produce a verified archive. No `backups` row is written."""


@dataclass(frozen=True)
class Snapshot:
    """What the counting transaction observed, and the id the dump must be pinned to."""

    snapshot_id: str
    row_counts: dict[str, int]
    compressed_chunks: int


# ---------------------------------------------------------------------------------------------
# The pinned image
# ---------------------------------------------------------------------------------------------


def timescaledb_image(compose_path: Path = COMPOSE_PATH) -> str:
    """The `timescaledb` service's pinned `tag@digest`, read from the Compose file.

    READ, NEVER HARDCODED. The digest is already written down once and gated by
    `verify/preflight.py`'s gate 1; a second copy here is a second thing to update and the copy is
    what goes stale. When it does, the dump runs against a different server version than
    production and the failure looks like data corruption.
    """
    text = compose_path.read_text(encoding="utf-8")

    service = None
    for line in text.splitlines():
        service_match = _SERVICE_RE.match(line)
        if service_match is not None:
            service = service_match.group("name")
            continue
        image_match = _IMAGE_RE.match(line)
        if image_match is not None and service == "timescaledb":
            reference = image_match.group("reference")
            if "@sha256:" not in reference:
                raise BackupError(
                    f"the timescaledb image in {compose_path} is {reference!r}, which carries no "
                    f"digest. The dump would run against whatever the tag resolves to today, "
                    f"which is the version-mismatch failure this reads the digest to avoid."
                )
            return reference

    raise BackupError(
        f"no `image:` found for the timescaledb service in {compose_path}. Without it there is no "
        f"way to match the dump's client version to the server's."
    )


# ---------------------------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------------------------


def connection_parts(url: str | None = None) -> dict:
    """Split DATABASE_URL into the pieces pg_dump needs as flags.

    Parsed HERE rather than added to app/db.py, which is deliberately built so that no helper in
    it hands out a password - `redacted()` is the only thing it exposes about one. This is the one
    place that genuinely needs the value, to write a 0600 pgpass file, and keeping the extraction
    local keeps that property of db.py intact.
    """
    parts = urlsplit(url or db.database_url())
    if not parts.hostname or not parts.username:
        raise BackupError(
            "DATABASE_URL is missing a host or user. pg_dump is invoked with explicit flags "
            "rather than a URL, so each piece has to be present."
        )
    # The password may legitimately be absent - a trust-auth throwaway database in a test
    # environment has none. THE REFUSAL LIVES IN write_pgpass, not here: this function only
    # splits, and a parser that refuses is a parser that cannot be used to inspect a URL. On the
    # instance the password is always present, and writing an empty pgpass entry there would
    # produce an authentication failure pointing at nothing.
    return {
        "host": parts.hostname,
        "port": parts.port or 5432,
        "database": (parts.path or "/").lstrip("/"),
        "user": unquote(parts.username),
        "password": unquote(parts.password) if parts.password else None,
    }


def last_verified_backup(conn):
    """The most recent row with `verified = true`, or None on the very first run."""
    row = conn.execute(
        "SELECT backup_id, byte_size FROM backups WHERE verified ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return None if row is None else {"backup_id": row[0], "byte_size": row[1]}


def check_free_space(staging_dir: Path, last_byte_size: int | None, usage=shutil.disk_usage) -> int:
    """Refuse BEFORE dumping if the volume cannot hold twice the last archive.

    A pre-flight refusal is a clean failure that leaves the previous archive intact. A partial dump
    that fills /mnt/data is an outage that also takes the database's own writes with it.
    """
    free = usage(str(staging_dir)).free
    if last_byte_size is None:
        logger.info(
            "free-space pre-flight: no prior verified backup, so there is no size to compare "
            "against. %d bytes free.", free
        )
        return free

    required = int(last_byte_size * FREE_SPACE_MULTIPLE)
    if free < required:
        raise BackupError(
            f"refusing to dump: {free} bytes free on {staging_dir}, need {required} "
            f"({FREE_SPACE_MULTIPLE}x the last verified archive of {last_byte_size} bytes). "
            f"A partial dump that fills this volume is an outage; this refusal is not."
        )
    return free


def assert_excluded_table_exists(conn, table: str = EXCLUDED_DATA_TABLE) -> None:
    """THEME 1 IN A SINGLE FLAG.

    `pg_dump --exclude-table-data` succeeds SILENTLY when its pattern matches nothing. If the
    scheduler's table is ever renamed, the exclusion becomes a no-op, stale scheduler state starts
    shipping in every backup, and there is no error anywhere - not in pg_dump, not in job_runs, not
    in the archive. The only way to notice is to check that the pattern matches something first.
    """
    found = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s",
        (COUNTED_SCHEMA, table),
    ).fetchone()[0]

    if found == 0:
        raise BackupError(
            f"--exclude-table-data pattern {table!r} matches no table in schema "
            f"{COUNTED_SCHEMA!r}. pg_dump would accept the flag silently and ship the rows it was "
            f"meant to exclude. If the table was renamed, update EXCLUDED_DATA_TABLE."
        )


# ---------------------------------------------------------------------------------------------
# Snapshot-consistent counts
# ---------------------------------------------------------------------------------------------


def export_snapshot(conn) -> Snapshot:
    """Open a REPEATABLE READ transaction, export its snapshot, and count inside it.

    THE CALLER MUST HOLD `conn` OPEN UNTIL pg_dump EXITS. A snapshot is only valid while the
    transaction that exported it is alive; closing early makes `pg_dump --snapshot` fail, which is
    the correct and loud behaviour rather than a silent fallback to a different point in time.
    """
    conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    snapshot_id = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]

    tables = [
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (COUNTED_SCHEMA,),
        ).fetchall()
    ]
    if not tables:
        raise BackupError(
            f"no tables found in schema {COUNTED_SCHEMA!r}. A row_counts mapping built from an "
            f"empty table list would compare equal to any restore at all."
        )

    # EVERY TABLE, INCLUDING ZERO-ROW ONES. A table that vanishes between dump and restore is only
    # detectable if its absence from the restored key set can be compared against its presence
    # here - so a table with no rows still needs its key.
    row_counts: dict[str, int] = {}
    for table in tables:
        count = conn.execute(f'SELECT count(*) FROM "{COUNTED_SCHEMA}"."{table}"').fetchone()[0]
        row_counts[f"{COUNTED_SCHEMA}.{table}"] = int(count)

    # In the SAME transaction, so the compression state described is the one the archive contains.
    # Compression is a headline measurement for this project and is exactly the kind of thing that
    # silently does not survive a restore.
    compressed_chunks = int(
        conn.execute(
            "SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed"
        ).fetchone()[0]
    )

    return Snapshot(snapshot_id, row_counts, compressed_chunks)


# ---------------------------------------------------------------------------------------------
# The dump
# ---------------------------------------------------------------------------------------------


def write_pgpass(path: Path, *, host: str, port: int, database: str, user: str, password: str) -> None:
    """A 0600 pgpass file, bind-mounted read-only into the container.

    NOT `-e PGPASSWORD`. Container environment is visible in `docker inspect` and in any process
    listing of the daemon's children, which makes the database password readable by anything that
    can talk to the Docker socket - and § 22 already establishes that the socket is effectively
    root on the host.
    """
    if not password:
        raise BackupError(
            "refusing to write an empty pgpass entry: DATABASE_URL carries no password. On the "
            "instance the password is always present, and an empty entry produces an "
            "authentication failure that points at nothing."
        )
    path.write_text(f"{host}:{port}:{database}:{user}:{password}\n", encoding="utf-8")
    path.chmod(0o600)


def dump_command(
    *,
    image: str,
    archive_path: Path,
    pgpass_path: Path,
    snapshot_id: str,
    host: str,
    port: int,
    database: str,
    user: str,
    uid: int,
    gid: int,
    staging_dir: Path = STAGING_DIR,
) -> list[str]:
    """The full `docker run` argv. Built as a list so there is no shell and no pipe."""
    return [
        "docker", "run", "--rm",
        # So the archive is readable by the host process without a chown.
        "--user", f"{uid}:{gid}",
        "--network", "host",
        "-v", f"{staging_dir}:{staging_dir}",
        # Read-only, and the password never appears in the environment.
        "-v", f"{pgpass_path}:/tmp/pgpass:ro",
        "-e", "PGPASSFILE=/tmp/pgpass",
        image,
        "pg_dump",
        "--host", host,
        "--port", str(port),
        "--username", user,
        "--dbname", database,
        # Custom format, so pg_restore can do a full restore-to-/dev/null pass over it.
        "--format", "custom",
        # PINNED TO THE COUNTING TRANSACTION'S SNAPSHOT. Without this the archive describes a
        # different instant than row_counts does.
        f"--snapshot={snapshot_id}",
        # DATA ONLY. --exclude-table would drop the DDL too, leaving the restored database
        # structurally different from production and defeating the restore test.
        f"--exclude-table-data={EXCLUDED_DATA_TABLE}",
        # -f TO A FILE. Never stdout: a broken pipe with a zero exit is the truncation incident.
        "--file", str(archive_path),
    ]


def verify_archive(archive_path: Path, image: str, run=subprocess.run) -> None:
    """A FULL RESTORE TO /dev/null. Exit 0 AND empty stderr.

    NOT `pg_restore --list`. `--list` reads only the archive's table of contents; the dump that was
    one third its correct size passed it cleanly, matched its own SHA-256 across three machines,
    and failed on restore (CLAUDE.md § 3).

    STDERR IS CHECKED SEPARATELY FROM THE EXIT CODE. pg_restore reports several classes of damage
    as warnings on stderr while still exiting zero, so an exit-code-only check is green over an
    archive that has already told you it is broken.
    """
    completed = run(
        [
            "docker", "run", "--rm",
            "-v", f"{archive_path.parent}:{archive_path.parent}",
            image,
            "pg_restore", "--file", "/dev/null", str(archive_path),
        ],
        capture_output=True,
        text=True,
    )

    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0 or stderr:
        raise BackupError(
            f"archive verification FAILED for {archive_path}.\n"
            f"  exit: {completed.returncode}\n"
            f"  stderr: {stderr or '(empty)'}\n"
            f"A full restore-to-/dev/null is the only check that catches a truncated archive; "
            f"the file is kept for inspection."
        )


def check_size_floor(byte_size: int, last: dict | None) -> None:
    """A second, independent gate. Never a substitute for the restore check.

    Compared against the most recent VERIFIED archive rather than a hardcoded constant: a byte
    constant invented today is wrong the first time the database grows, and the natural response to
    a wrong constant is to delete the check.
    """
    if last is None:
        logger.info(
            "size floor inapplicable: no prior verified backup to compare %d bytes against. "
            "This is the first run.", byte_size
        )
        return

    floor = int(last["byte_size"] * SIZE_FLOOR_FRACTION)
    if byte_size < floor:
        raise BackupError(
            f"archive is {byte_size} bytes, below {floor} "
            f"({SIZE_FLOOR_FRACTION:g}x the last verified archive of {last['byte_size']} bytes, "
            f"backup_id={last['backup_id']}). The archive passed restore verification, so this is "
            f"a size anomaly rather than a corrupt file - but a database does not usually halve."
        )


# ---------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------


def upload_and_verify(s3, bucket: str, key: str, archive_path: Path) -> int:
    """Upload, then verify by comparing `head_object`'s ContentLength to the local size.

    NOT AN ETag/MD5 COMPARISON. `upload_file` is multipart-capable, and for a multipart upload the
    ETag is a hash of concatenated part hashes with a part count suffix - it is NOT the object's
    MD5. A check comparing them either always fails on large archives or gets deleted the first
    time somebody investigates why, and deleting it is how upload verification quietly disappears.
    """
    local_size = archive_path.stat().st_size
    s3.upload_file(str(archive_path), bucket, key)

    head = s3.head_object(Bucket=bucket, Key=key)
    remote_size = head["ContentLength"]

    if remote_size != local_size:
        raise BackupError(
            f"upload verification FAILED for s3://{bucket}/{key}: the object is {remote_size} "
            f"bytes and the local archive is {local_size}. The local file is KEPT at "
            f"{archive_path} - it is the only copy that is known to restore."
        )
    return remote_size


def copy_to_monthly(s3, bucket: str, daily_key: str, monthly_key: str) -> None:
    """Server-side copy. No re-upload, no delete - both already covered by the instance policy."""
    s3.copy_object(
        Bucket=bucket,
        Key=monthly_key,
        CopySource={"Bucket": bucket, "Key": daily_key},
    )


# ---------------------------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------------------------


def record_backup(conn, *, bucket, key, byte_size, snapshot: Snapshot, started_at, finished_at):
    """Written ONLY after upload verification passes, and always with verified = true.

    A failed run writes no row at all. Never a `verified = false` placeholder: a later query for
    "the most recent backup" would find it and report a backup that does not exist.
    """
    import json

    return conn.execute(
        "INSERT INTO backups (started_at, finished_at, s3_bucket, s3_key, byte_size, "
        "row_counts, compressed_chunks, verified, verified_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s) RETURNING backup_id",
        (
            started_at,
            finished_at,
            bucket,
            key,
            byte_size,
            json.dumps(snapshot.row_counts),
            snapshot.compressed_chunks,
            finished_at,
        ),
    ).fetchone()[0]


def archive_name(now: datetime) -> str:
    return f"waterway-{now.strftime('%Y%m%dT%H%M%SZ')}.dump"


@job(JOB_NAME)
def backup_nightly_job(
    url: str | None = None,
    *,
    bucket: str | None = None,
    now: datetime | None = None,
    staging_dir: Path = STAGING_DIR,
    s3=None,
    run=subprocess.run,
) -> None:
    """Returns None, so `rows_written` is recorded as NULL.

    NULL, NOT 0. `rows_written` means rows written TO THE DATABASE (CLAUDE.md § 4); a backup writes
    none, so "not applicable" is NULL while 0 would claim this job counts rows and today counted
    none. Setting it to the DUMPED row count is the tempting wrong version - it looks informative,
    and it makes one column mean two different things depending on which job wrote the row.
    """
    now = now or datetime.now(timezone.utc)
    bucket = bucket or os.environ["BACKUP_BUCKET"]
    started_at = now

    if s3 is None:  # pragma: no cover - exercised on the instance, mocked in tests
        import boto3

        s3 = boto3.client("s3")

    staging_dir.mkdir(parents=True, exist_ok=True)
    archive_path = staging_dir / archive_name(now)
    pgpass_path = staging_dir / ".pgpass-backup"

    image = timescaledb_image()
    parts = connection_parts(url)

    with db.connection(url) as pre:
        last = last_verified_backup(pre)
        assert_excluded_table_exists(pre)
    check_free_space(staging_dir, None if last is None else last["byte_size"])

    write_pgpass(
        pgpass_path,
        host=parts["host"], port=parts["port"], database=parts["database"],
        user=parts["user"], password=parts["password"],
    )

    try:
        # THE COUNTING CONNECTION IS HELD OPEN ACROSS THE DUMP. Closing it early invalidates the
        # exported snapshot and pg_dump fails - loudly, which is correct.
        with db.connection(url) as counting:
            snapshot = export_snapshot(counting)

            completed = run(
                dump_command(
                    image=image,
                    archive_path=archive_path,
                    pgpass_path=pgpass_path,
                    snapshot_id=snapshot.snapshot_id,
                    host=parts["host"], port=parts["port"],
                    database=parts["database"], user=parts["user"],
                    uid=os.getuid(), gid=os.getgid(),
                    staging_dir=staging_dir,
                ),
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise BackupError(
                    f"pg_dump exited {completed.returncode}: "
                    f"{(completed.stderr or '').strip() or '(no stderr)'}"
                )
            counting.execute("COMMIT")
    finally:
        pgpass_path.unlink(missing_ok=True)

    verify_archive(archive_path, image, run=run)

    byte_size = archive_path.stat().st_size
    check_size_floor(byte_size, last)

    daily_key = f"{DAILY_PREFIX}{archive_path.name}"
    upload_and_verify(s3, bucket, daily_key, archive_path)

    if now.day == 1:
        copy_to_monthly(s3, bucket, daily_key, f"{MONTHLY_PREFIX}{archive_path.name}")

    finished_at = datetime.now(timezone.utc)
    # THE COMMIT IS THE CONTEXT MANAGER'S, NOT THIS FUNCTION'S. `session.writing` commits on a
    # clean exit and rolls back on an exception; `db.connection` commits nothing implicitly
    # (app/db.py), which is the correct default and is what this line used to get wrong.
    #
    # The defect this replaces: the job reported success, job_runs recorded success, S3 held a
    # verified archive, and the `backups` row was silently rolled back on close - so the next
    # run's size floor had nothing to compare against and the restore test found no backup to
    # restore. A layer reporting success while the thing downstream receives nothing, § 2's theme
    # 1, caught by a test rather than by review. Stage B then measured that eight of ten write
    # paths could lose their commit with the suite still green, which is why the boundary now
    # lives in a helper instead of in a line every call site has to remember.
    with session.writing(url) as conn:
        backup_id = record_backup(
            conn, bucket=bucket, key=daily_key, byte_size=byte_size,
            snapshot=snapshot, started_at=started_at, finished_at=finished_at,
        )

    # ONLY NOW. If verification had failed the file would still be here, and the error would say
    # where - the local archive is the only copy known to restore.
    archive_path.unlink(missing_ok=True)

    logger.info(
        "backup %d verified and uploaded: s3://%s/%s (%d bytes, %d tables, %d compressed chunks)",
        backup_id, bucket, daily_key, byte_size, len(snapshot.row_counts),
        snapshot.compressed_chunks,
    )
    return None
