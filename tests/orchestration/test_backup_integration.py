"""Integration tier — a REAL dump, verified and truncated.

`test_backup_integration_truncated_archive_fails` IS THE LOAD-BEARING TEST OF THIS ENTIRE PHASE.
Everything else asserts that the right flags are passed. This one asserts that the check those
flags configure actually catches the incident it was written for: a `pg_dump` that exited zero and
wrote a third of a file, which passed `pg_restore --list` cleanly and matched its own SHA-256
across three machines (CLAUDE.md § 3).

A unit test cannot show that. It needs a real archive, really truncated, really rejected.

Requires DATABASE_URL and Docker. Skips with a stated reason when either is absent - never passes
silently, because a verification tier that vanishes quietly is worse than one that was never
written.
"""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from app import db
from app.orchestration import backup

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker is not available; the dump runs in a one-shot container off the pinned digest",
)


@pytest.fixture
def scheduler_table(migrated_db, database_url):
    """Create `apscheduler_jobs` the way the scheduler does.

    IT IS NOT CREATED BY A MIGRATION. SQLAlchemyJobStore issues its own DDL when the scheduler
    first starts, so a database that has only had migrations applied does not have it - which is
    exactly what `assert_excluded_table_exists` refused on, correctly, the first time these tests
    ran. On the instance the table exists because the scheduler has run there.

    The column list matches APScheduler's own so the dump's DDL is representative.
    """
    # autocommit: db.connection() deliberately commits nothing implicitly (app/db.py), so DDL
    # written inside it would be rolled back on close and the table would never appear.
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS apscheduler_jobs ("
            "  id varchar(191) NOT NULL PRIMARY KEY,"
            "  next_run_time double precision,"
            "  job_state bytea NOT NULL)"
        )
        conn.execute(
            "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
            "VALUES ('probe', 1, '\\x00'::bytea) ON CONFLICT (id) DO NOTHING"
        )
    yield


@pytest.fixture
def archive(tmp_path, scheduler_table, database_url):
    """A real custom-format archive of the migrated test database, dumped in the pinned image."""
    image = backup.timescaledb_image()
    parts = backup.connection_parts(database_url)

    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)
    archive_path = staging / "test.dump"
    pgpass = staging / ".pgpass"
    backup.write_pgpass(
        pgpass,
        host=parts["host"], port=parts["port"], database=parts["database"],
        user=parts["user"], password=parts["password"] or "x",
    )

    with db.connection(database_url) as counting:
        snapshot = backup.export_snapshot(counting)

        completed = subprocess.run(
            backup.dump_command(
                image=image,
                archive_path=archive_path,
                pgpass_path=pgpass,
                snapshot_id=snapshot.snapshot_id,
                # The container reaches the host's mapped port over host networking.
                host="host.docker.internal",
                port=parts["port"],
                database=parts["database"],
                user=parts["user"],
                uid=0, gid=0,
                staging_dir=staging,
            ),
            capture_output=True,
            text=True,
        )
        counting.execute("COMMIT")

    if completed.returncode != 0:
        pytest.skip(f"could not produce a real dump in this environment: {completed.stderr[:400]}")

    return archive_path, snapshot, image


@requires_docker
def test_backup_integration_verification_passes_on_a_good_archive(archive):
    """The positive case, or the truncation test below could pass for the wrong reason."""
    archive_path, _, image = archive
    assert archive_path.stat().st_size > 0
    backup.verify_archive(archive_path, image)  # no raise


@requires_docker
def test_backup_integration_truncated_archive_fails(archive):
    """THE INCIDENT THIS PHASE EXISTS FOR.

    A real archive, really truncated, must fail verification. If this passes cleanly the
    verification is not verifying, and every green backup in this system means nothing.

    TWO TRUNCATIONS, AND THE 95% ONE IS THE IMPORTANT HALF.

    A one-third cut is the incident's own proportions, but at this database's size it destroys the
    table of contents as well, so `pg_restore --list` rejects it too - which means a version of
    this test using only that cut STAYS GREEN when verification is swapped to `--list`. Measured:
    that mutation left the one-third version passing.

    At 95% the TOC survives and the data does not, which is the incident's SHAPE: an archive that
    lists cleanly and cannot be restored. That is the cut this test needs in order to be able to
    fail for the reason it is named for.
    """
    archive_path, _, image = archive
    original = archive_path.read_bytes()
    assert len(original) > 300, "the archive is too small for a truncation to be meaningful"

    for label, fraction in (("third", 1 / 3), ("toc-intact", 0.95)):
        broken = archive_path.parent / f"broken_{label}.dump"
        broken.write_bytes(original[: int(len(original) * fraction)])

        with pytest.raises(backup.BackupError) as excinfo:
            backup.verify_archive(broken, image)

        message = str(excinfo.value)
        assert "verification FAILED" in message, f"{label}: {message}"
        assert str(broken) in message, (
            f"{label}: the error does not name the archive that failed"
        )


@requires_docker
def test_backup_integration_list_would_not_have_caught_it(archive):
    """WHY `--list` IS FORBIDDEN, demonstrated rather than asserted.

    `pg_restore --list` reads only the archive's table of contents, which in custom format lives
    at the FRONT of the file. So there is a range of truncations that leave the TOC intact and the
    data gone - and that is the shape of the incident in CLAUDE.md § 3: an archive one third its
    correct size that listed cleanly, matched its own SHA-256 across three machines, and failed on
    restore.

    SEVERAL FRACTIONS ARE TRIED rather than one, because which fraction leaves the TOC intact
    depends on how many objects the archive describes and how much data sits behind them. A single
    fraction that happens to cut into the TOC would let this test report "--list catches it too",
    which is the opposite of the finding.

    If no fraction reproduces it, this SKIPS with the observation rather than passing - the
    contract in § 3 rests on a real incident, and a test that quietly implied the distinction does
    not matter would be worse than no test.
    """
    archive_path, _, image = archive
    original = archive_path.read_bytes()

    reproduced = []
    for fraction in (0.33, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99):
        broken = archive_path.parent / f"broken_{int(fraction * 100)}.dump"
        broken.write_bytes(original[: int(len(original) * fraction)])

        listed = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{broken.parent}:{broken.parent}",
                image, "pg_restore", "--list", str(broken),
            ],
            capture_output=True, text=True,
        )
        list_accepted = listed.returncode == 0 and not listed.stderr.strip()

        try:
            backup.verify_archive(broken, image)
            restore_accepted = True
        except backup.BackupError:
            restore_accepted = False

        # The whole point: --list green, full restore red.
        if list_accepted and not restore_accepted:
            reproduced.append(fraction)

        # And whatever else happens, --list must never be MORE strict than the full restore, or
        # the contract would be preferring the weaker check.
        if restore_accepted:
            assert list_accepted, (
                f"at {fraction:.0%} the full restore accepted an archive --list rejected, which "
                f"inverts the relationship § 3 is built on"
            )

    if not reproduced:
        pytest.skip(
            "no truncation fraction in this environment produced an archive that --list accepts "
            "and a full restore rejects. The migrated test database is small, so its table of "
            "contents is a large share of the file and almost any truncation cuts into it. The "
            "full-restore check is still required - the incident in CLAUDE.md § 3 is a real "
            "archive that --list accepted - but it is not reproducible at this database size."
        )


@requires_docker
def test_backup_integration_row_counts_cover_every_public_table(archive, database_url):
    """The recorded key set is EXACTLY the source's public-schema table set.

    Including zero-row tables. A table that vanishes between dump and restore is only detectable
    if its absence from the restored key set can be compared against its presence here.
    """
    _, snapshot, _ = archive

    with db.connection(database_url) as conn:
        actual = {
            f"public.{row[0]}"
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }

    assert set(snapshot.row_counts) == actual, (
        f"recorded counts cover {sorted(set(snapshot.row_counts) ^ actual)} differently from the "
        f"source's own table list"
    )
    assert actual, "the source database has no public tables; this test would pass over nothing"


@requires_docker
def test_backup_integration_row_counts_include_zero_row_tables(archive, database_url):
    """A table with no rows still gets a key, with the value 0.

    Skipping empty tables is the tempting optimisation - there is nothing to compare, so why
    record it. The answer is that its DISAPPEARANCE is what the restore test detects, and a key
    that was never written cannot go missing.
    """
    _, snapshot, _ = archive

    with db.connection(database_url) as conn:
        tables = [
            f"public.{r[0]}"
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        ]
        empty = [
            name for name in tables
            if conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0] == 0
        ]

    assert empty, (
        "the migrated test database has no empty public tables, so this test cannot distinguish "
        "'includes zero-row tables' from 'skips them'"
    )
    # ASKED OF THE DATABASE FIRST, then checked against the recording. Deriving the empty set from
    # `row_counts` itself would make the omission invisible: a job that skipped empty tables would
    # produce an empty list and the test would report that there were none to check.
    missing = [name for name in empty if name not in snapshot.row_counts]
    assert missing == [], (
        f"tables with zero rows were omitted from row_counts: {missing}. Their DISAPPEARANCE is "
        f"what the restore test detects, and a key that was never written cannot go missing."
    )
    for name in empty:
        assert snapshot.row_counts[name] == 0


@requires_docker
def test_backup_integration_records_compressed_chunk_count(archive):
    """Compression is a headline measurement and is exactly what silently fails to survive."""
    _, snapshot, _ = archive
    assert isinstance(snapshot.compressed_chunks, int)
    assert snapshot.compressed_chunks >= 0


def test_backup_integration_snapshot_id_is_a_real_exported_snapshot(migrated_db, database_url):
    """`pg_export_snapshot()` inside a REPEATABLE READ transaction, held open.

    Asserted against a real server rather than a mock, because the failure this guards - a
    snapshot id that has already been invalidated by its transaction closing - is a server-side
    behaviour that no fake reproduces.
    """
    with db.connection(database_url) as conn:
        snapshot = backup.export_snapshot(conn)

        assert snapshot.snapshot_id, "no snapshot id was exported"
        # Postgres snapshot ids look like `00000003-0000001B-1`.
        assert "-" in snapshot.snapshot_id, f"{snapshot.snapshot_id!r} is not a snapshot id"

        # Still inside the exporting transaction: the id is usable.
        conn.execute("COMMIT")


def test_backup_integration_writes_no_backups_row_on_failure(scheduler_table, database_url):
    """A failed run leaves job_runs to record the failure and writes NOTHING here.

    Never a `verified = false` placeholder row: a later query for "the most recent backup" would
    find it and report a backup that does not exist.
    """
    with db.connection(database_url) as conn:
        before = conn.execute("SELECT count(*) FROM backups").fetchone()[0]

    def exploding_run(*args, **kwargs):
        raise AssertionError("the dump should not have been reached")

    with pytest.raises(Exception):
        backup.backup_nightly_job(
            database_url,
            bucket="irrelevant",
            staging_dir=backup.Path("/nonexistent-volume-for-this-test"),
            s3=object(),
            run=exploding_run,
        )

    with db.connection(database_url) as conn:
        after = conn.execute("SELECT count(*) FROM backups").fetchone()[0]

    assert after == before, (
        f"a failed run wrote {after - before} backups row(s). A later query for 'the most recent "
        f"backup' would find one that does not exist."
    )


# ---------------------------------------------------------------------------------------------
# The job itself, end to end
# ---------------------------------------------------------------------------------------------
#
# THE UNIT TIER CANNOT REACH THESE. Tests that call `upload_and_verify` or grep the source for
# `return None` pass whatever the JOB does around them - measured, when the "rows_written = total"
# and "delete before verifying" mutations both left the unit tests green. The contract is about
# what the job does, so the test has to run the job.


class RecordingS3:
    def __init__(self, *, content_length=None):
        self.uploaded = []
        self.copied = []
        self._content_length = content_length

    def upload_file(self, filename, bucket, key):
        self.uploaded.append((filename, bucket, key))

    def head_object(self, Bucket, Key):
        if self._content_length is not None:
            return {"ContentLength": self._content_length}
        return {"ContentLength": Path(self.uploaded[-1][0]).stat().st_size}

    def copy_object(self, Bucket, Key, CopySource):
        self.copied.append((Bucket, Key, CopySource))


def _with_password(url: str) -> str:
    """Ensure the URL carries a password, because the job refuses to write an empty pgpass entry.

    That refusal is correct for the instance, where the password is always present and an empty
    entry produces an authentication failure pointing at nothing. A throwaway trust-auth container
    has no password, and trust ignores whatever is sent - so supplying one here exercises the real
    pgpass path without weakening the guard.
    """
    parts = urlsplit(url)
    if parts.password:
        return url
    netloc = f"{parts.username or 'postgres'}:trust-auth-ignores-this@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _reachable_run(argv, **kwargs):
    """Run the real docker command, rewriting the loopback host the container cannot reach.

    The job builds its argv from DATABASE_URL, which points at 127.0.0.1 on the host. Rewriting
    here rather than adding a host override to the job keeps the production code free of a
    parameter that exists only for tests.
    """
    return subprocess.run(
        ["host.docker.internal" if part == "127.0.0.1" else part for part in argv],
        **kwargs,
    )


@requires_docker
def test_backup_integration_end_to_end(tmp_path, scheduler_table, database_url):
    """A real dump, really verified, "uploaded", and recorded.

    Asserts the two things only a job-level test can see: `rows_written` is NULL in job_runs, and
    the `backups` row's `row_counts` keys equal the source's public-schema table set exactly.
    """
    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)
    s3 = RecordingS3()

    backup.backup_nightly_job(
        _with_password(database_url),
        bucket="test-bucket", staging_dir=staging, s3=s3, run=_reachable_run,
    )

    assert len(s3.uploaded) == 1, f"expected one upload, got {s3.uploaded}"
    _, bucket, key = s3.uploaded[0]
    assert bucket == "test-bucket"
    assert key.startswith(backup.DAILY_PREFIX), f"uploaded to {key!r}, not the daily prefix"

    with db.connection(database_url) as conn:
        run_row = conn.execute(
            "SELECT status, rows_written FROM job_runs WHERE job_name = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (backup.JOB_NAME,),
        ).fetchone()
        row = conn.execute(
            "SELECT s3_bucket, s3_key, byte_size, verified, row_counts, compressed_chunks "
            "FROM backups ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        source_tables = {
            f"public.{r[0]}"
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }

    assert run_row[0] == "success"
    # NULL, NOT 0. A backup writes no rows to this database; 0 would claim it counts rows and
    # today counted none.
    assert run_row[1] is None, (
        f"rows_written is {run_row[1]!r}, expected NULL. Setting it to the DUMPED row count looks "
        f"informative and makes one column mean two things depending on which job wrote the row."
    )

    assert row[0] == "test-bucket"
    assert row[1] == key
    assert row[2] > 0
    assert row[3] is True
    assert set(row[4]) == source_tables, (
        f"row_counts keys differ from the source's table set: "
        f"{sorted(set(row[4]) ^ source_tables)}"
    )
    assert isinstance(row[5], int)

    # The local archive is gone only because verification passed.
    assert list(staging.glob("*.dump")) == [], (
        f"the staging archive survived a successful run: {list(staging.glob('*.dump'))}"
    )
    assert list(staging.glob(".pgpass*")) == [], "the pgpass file was left behind"


@requires_docker
def test_backup_integration_keeps_local_file_when_upload_verification_fails(
    tmp_path, scheduler_table, database_url
):
    """A failed upload verification KEEPS the archive - it is the only copy known to restore."""
    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)
    s3 = RecordingS3(content_length=1)  # S3 reports a one-byte object

    with pytest.raises(backup.BackupError, match="upload verification FAILED"):
        backup.backup_nightly_job(
            _with_password(database_url),
            bucket="test-bucket", staging_dir=staging, s3=s3, run=_reachable_run,
        )

    survivors = list(staging.glob("*.dump"))
    assert survivors, (
        "the archive was deleted after a failed upload verification. It was the only copy known "
        "to restore, and the error message points at a file that no longer exists."
    )

    with db.connection(database_url) as conn:
        count = conn.execute("SELECT count(*) FROM backups").fetchone()[0]
    assert count == 0, "a backups row was written despite the upload failing verification"


@requires_docker
def test_backup_integration_copies_to_monthly_on_first_of_month(
    tmp_path, scheduler_table, database_url
):
    """Server-side copy on the first, and NOT on any other day."""
    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)

    first = RecordingS3()
    backup.backup_nightly_job(
        _with_password(database_url), bucket="b",
        now=datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc),
        staging_dir=staging, s3=first, run=_reachable_run,
    )
    assert len(first.copied) == 1, f"no monthly copy on the first of the month: {first.copied}"
    assert first.copied[0][1].startswith(backup.MONTHLY_PREFIX)
    assert len(first.uploaded) == 1, "the monthly copy re-uploaded instead of copying"

    other = RecordingS3()
    backup.backup_nightly_job(
        _with_password(database_url), bucket="b",
        now=datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc),
        staging_dir=staging, s3=other, run=_reachable_run,
    )
    assert other.copied == [], f"a monthly copy was made on the 2nd: {other.copied}"
