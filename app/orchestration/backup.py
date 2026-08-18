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
  - THE CLIENT'S MAJOR IS ASSERTED AGAINST THE SERVER'S, AT RUNTIME, BEFORE ANYTHING IS DUMPED.
    A `pg_dump` at a different major than the server either refuses or produces a subtly wrong
    archive. Through Phase 11 the dump ran in a one-shot container off the pinned server digest,
    so the two matched mechanically. Phase 12 put this job inside a container, and a container
    cannot `docker run` without the host's Docker socket - which is root-equivalent on the host,
    a permanent widening of blast radius for a convenience (CLAUDE.md § 22).

    So the client lives in the scheduler image and the agreement is CHECKED rather than
    structural. There are two checks and the division between them is the point:
    `verify/preflight.py` compares what the FILES say (the compose tag against the package pin),
    and `assert_client_server_majors_agree` below compares what is RUNNING (the binary's own
    `--version` against `SHOW server_version_num`). A stale image passes the first and fails the
    second, which is exactly the case neither could catch alone.

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

# The client binaries, by bare name, resolved on PATH inside the scheduler image. Not absolute
# paths: Debian puts them under /usr/lib/postgresql/NN/bin with symlinks in /usr/bin, and writing
# the versioned path here would be a THIRD copy of the major - one that agrees with nothing and
# that the version check could not catch, because it would be the thing being checked.
PG_DUMP = "pg_dump"
PG_RESTORE = "pg_restore"

# `pg_dump (PostgreSQL) 16.10 (Debian 16.10-1.pgdg120+1)` -> 16.
_CLIENT_VERSION_RE = re.compile(r"\(PostgreSQL\)\s+(?P<major>\d+)")


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


def assert_staging_writable(staging_dir: Path) -> None:
    """WRITE A FILE AND DELETE IT. Never `os.access`, and never merely `is_dir()`.

    The container runs as uid 10001 and bind-mounts this directory from the host. Docker creates a
    MISSING bind-mount source as root:root, so a provisioning step nobody ran turns into a
    directory that exists, resolves, and cannot be written to - and without this check the first
    thing to discover that is `pg_dump`, several minutes and one exported snapshot later, with an
    error about a file rather than about ownership.

    `os.access(path, os.W_OK)` is the one-line version and it answers a different question. It
    consults the real uid against the mode bits and knows nothing about a read-only mount, a full
    filesystem, or an ACL - so it says yes in cases where the write fails. The check that crosses
    the boundary where the failure lives is the write itself (CLAUDE.md § 13).

    The probe is removed on every exit path. A leaked probe file is harmless and untidy; a leaked
    probe file that a later run refuses to overwrite is neither.
    """
    probe = staging_dir / ".writable-probe"
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise BackupError(
            f"refusing to dump: {staging_dir} is not writable by uid {os.getuid()}: "
            f"{type(exc).__name__}: {exc}\n"
            f"Docker creates a missing bind-mount source as root:root, so this is usually a "
            f"provisioning step that has not been run on this instance:\n"
            f"    sudo install -d -o 10001 -g 10001 -m 0750 /mnt/data/backups "
            f"/mnt/data/restore-test\n"
            f"Refusing here is a clean failure. Discovering it after pg_dump has been invoked is "
            f"a failed job holding an exported snapshot open."
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


def client_major(run=subprocess.run, binary: str = PG_DUMP) -> int:
    """The major of the pg_dump binary ACTUALLY ON PATH, from its own `--version`.

    Not read from the Dockerfile, not read from the image tag, not passed in. The whole value of
    this check over preflight's is that it interrogates the running artifact - an image built
    before the pin was corrected passes every file-based check there is.
    """
    completed = run([binary, "--version"], capture_output=True, text=True)
    if completed.returncode != 0:
        raise BackupError(
            f"could not run `{binary} --version` (exit {completed.returncode}): "
            f"{(completed.stderr or '').strip() or '(no stderr)'}\n"
            f"The client lives in the scheduler image (Dockerfile.scheduler); its absence means "
            f"the image is not the one this job is supposed to be running in."
        )

    output = (completed.stdout or "").strip()
    match = _CLIENT_VERSION_RE.search(output)
    if match is None:
        raise BackupError(
            f"could not parse a major version out of `{binary} --version`: {output!r}\n"
            f"Refusing rather than guessing: an unparsed version compared against the server "
            f"would be a check that cannot fail."
        )
    return int(match.group("major"))


def server_major(conn) -> int:
    """The server's major from `server_version_num`, which is an INTEGER and unambiguous.

    `SHOW server_version` returns a display string that has carried suffixes ("16.10 (Debian ...)")
    and, historically, a two-part major ("9.6"). `server_version_num` is 160010 for 16.10 and
    90600 for 9.6, so integer division by 10000 is correct on both sides of the version-scheme
    change and needs no parsing at all.
    """
    return int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000


def assert_client_server_majors_agree(conn, run=subprocess.run) -> int:
    """THE RUNTIME HALF OF THE PAIR. Fails the job; never warns and proceeds.

    EQUALITY, NOT COMPATIBILITY, for `verify/preflight.py`'s reason: `pg_dump` older than the
    server refuses outright, and newer than the server usually works and is not what anything here
    was verified against. A job that warned and carried on would produce an archive nobody had
    reason to trust, and the warning would be in a log nobody reads at 03:00.
    """
    client = client_major(run=run)
    server = server_major(conn)

    if client != server:
        raise BackupError(
            f"refusing to dump: pg_dump is major {client} and the server is major {server}.\n"
            f"These are pinned in two files - the client in Dockerfile.scheduler's "
            f"postgresql-client-NN version, the server in docker-compose.yml's image tag - "
            f"because the scheduler container has no Docker socket to run a matched one-shot "
            f"container with (CLAUDE.md § 22). `verify/preflight.py` compares what those files "
            f"SAY; this compares what is INSTALLED, so a mismatch here with preflight green means "
            f"the running image is older than the checkout. Rebuild it."
        )
    return client


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
    archive_path: Path,
    snapshot_id: str,
    host: str,
    port: int,
    database: str,
    user: str,
) -> list[str]:
    """The `pg_dump` argv. Built as a list so there is no shell and therefore no pipe.

    A DIRECT INVOCATION, NOT `docker run`. The container-spawning version is DELETED rather than
    kept behind a flag: a retained branch reintroduces the Docker-socket requirement the moment
    somebody sets the flag, and dead code with a plausible use case is the code that comes back.
    The client it invokes is the one installed in this image (Dockerfile.scheduler), whose major is
    asserted against the server's before this function is called.

    The `--user`/`-v`/`--network host` juggling the container form needed is gone with it. The
    process already runs as uid 10001, already sees /mnt/data/backups, and already reaches
    `timescaledb` over the compose network.
    """
    return [
        PG_DUMP,
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


def pgpass_environment(pgpass_path: Path, environ: dict | None = None) -> dict:
    """The child's environment: PGPASSFILE pointing at the 0600 file, and NO PGPASSWORD.

    PGPASSFILE IS A PATH, NOT A SECRET, which is the whole reason this shape is permitted where
    `PGPASSWORD` is not. A process environment is readable - /proc/<pid>/environ to the same uid,
    and `docker inspect` when the value is set on the container - so the password itself never
    goes there. A path to a file only this uid can read gives a reader nothing.

    Any inherited PGPASSWORD is stripped rather than left alone. It would take precedence over
    PGPASSFILE, so leaving one in place means the file this job took care to write at 0600 is
    silently not the thing being used.
    """
    child = dict(os.environ if environ is None else environ)
    child.pop("PGPASSWORD", None)
    child["PGPASSFILE"] = str(pgpass_path)
    return child


def verify_archive(archive_path: Path, run=subprocess.run) -> None:
    """A FULL RESTORE TO /dev/null. Exit 0 AND empty stderr.

    NOT `pg_restore --list`. `--list` reads only the archive's table of contents; the dump that was
    one third its correct size passed it cleanly, matched its own SHA-256 across three machines,
    and failed on restore (CLAUDE.md § 3).

    STDERR IS CHECKED SEPARATELY FROM THE EXIT CODE. pg_restore reports several classes of damage
    as warnings on stderr while still exiting zero, so an exit-code-only check is green over an
    archive that has already told you it is broken.

    THE INVOCATION CHANGED IN PHASE 12 AND THE VERIFICATION DID NOT. This used to be a `docker run`
    off the pinned server digest; it is now the pg_restore in this image, for the reason in the
    module docstring. What is checked - a full restore of every block to /dev/null, exit 0 AND
    empty stderr - is character for character what it was.
    """
    completed = run(
        [PG_RESTORE, "--file", "/dev/null", str(archive_path)],
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
    # BEFORE ANYTHING ELSE, AND BEFORE THE SNAPSHOT IS EXPORTED. The container runs as uid 10001
    # against a bind mount Docker will have created as root:root if provisioning did not. Every
    # later step - the connection, the snapshot, the dump - would succeed right up to the moment
    # pg_dump opens the output file.
    assert_staging_writable(staging_dir)

    archive_path = staging_dir / archive_name(now)
    pgpass_path = staging_dir / ".pgpass-backup"

    parts = connection_parts(url)

    with db.connection(url) as pre:
        # The runtime half of the version pin (see the module docstring). Inside the same
        # connection block as the other pre-flight reads, and before the counting transaction, so
        # a mismatch fails with nothing open and nothing written.
        assert_client_server_majors_agree(pre, run=run)
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
                    archive_path=archive_path,
                    snapshot_id=snapshot.snapshot_id,
                    host=parts["host"], port=parts["port"],
                    database=parts["database"], user=parts["user"],
                ),
                capture_output=True,
                text=True,
                # The path to the 0600 file, never the password itself. See pgpass_environment.
                env=pgpass_environment(pgpass_path),
            )
            if completed.returncode != 0:
                raise BackupError(
                    f"pg_dump exited {completed.returncode}: "
                    f"{(completed.stderr or '').strip() or '(no stderr)'}"
                )
            counting.execute("COMMIT")
    finally:
        pgpass_path.unlink(missing_ok=True)

    verify_archive(archive_path, run=run)

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
