"""Integration tier — the two backup jobs' writes, asserted from a connection they did not use.

See tests/ingest/test_ingest_write_paths_commit.py for the audit behind this family of files.

THIS IS WHERE THE DEFECT ACTUALLY HAPPENED. `app/orchestration/backup.py`'s `backups` INSERT was
silently rolled back in Phase 11: the job returned, `job_runs` recorded success, S3 held a verified
archive, and the row was not there. `test_backup_integration_end_to_end` caught it — it is one of
only two tests in the repo that could, because it drives the real job and reads back on a fresh
connection.

The restore test's own write was NOT covered. Measured 2026-08-17: deleting
`app/orchestration/restore_test.py:572`'s commit left all 113 tests in tests/orchestration/ green,
because `restore_test_monthly_job` was never invoked by any test — only its component functions
were, individually.

WHAT THAT COSTS IS THE VERIFICATION MARK. `mark_verified` writes the three columns migration 0026
permits after insert (`restore_verified_at`, `restore_verified_counts`, `restore_notes`). If they
never commit, a restore that really succeeded leaves a backup that reads as never-verified — and
the failure is in the safe direction only until somebody trusts the column, at which point every
archive looks unverified and the honest response is to stop believing the column.
"""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from app import db
from app.orchestration import backup, restore_test

pytestmark = pytest.mark.integration


# THE PRECONDITION IS THE `matching_pg_client` FIXTURE, not a decorator here.
#
# It was `requires_docker`, skipping because the dump and the throwaway restore both ran in
# containers. Neither does as of Phase 12. The replacement briefly checked only that a client
# EXISTS, which is not enough: both tests below drive the real job entrypoints, and the real
# backup job refuses a client/server MAJOR MISMATCH on purpose - so on a machine with a pg18
# client and a pg16 server they failed on the project's own guard rather than skipping. The
# fixture in conftest.py compares the majors and skips with both of them in the reason.


@pytest.fixture
def scheduler_table(migrated_db, database_url):
    """`apscheduler_jobs`, created the way SQLAlchemyJobStore creates it.

    Duplicated from test_backup_integration.py rather than shared, because it is four lines and
    importing across test modules to save them would couple two files whose failures should be
    read independently. It is NOT created by a migration — see that file's note.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS apscheduler_jobs ("
            " id varchar(191) NOT NULL PRIMARY KEY,"
            " next_run_time double precision,"
            " job_state bytea NOT NULL)"
        )
        conn.execute(
            "INSERT INTO apscheduler_jobs (id, next_run_time, job_state)"
            " VALUES ('heartbeat', 1, '\\x00'::bytea) ON CONFLICT (id) DO NOTHING"
        )
    return True


def _with_password(url: str) -> str:
    """Give the URL a password the trust-auth container ignores. See test_backup_integration."""
    parts = urlsplit(url)
    if parts.password:
        return url
    netloc = f"{parts.username or 'postgres'}:trust-auth-ignores-this@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _reachable_run(argv, **kwargs):
    """Rewrite the loopback host a container cannot reach, as the sibling suites do."""
    return subprocess.run(
        ["host.docker.internal" if part == "127.0.0.1" and "--host" in argv else part
         for part in argv],
        **kwargs,
    )


class RoundTripS3:
    """An S3 stand-in that actually KEEPS what was uploaded, so it can be downloaded back.

    `RecordingS3` in test_backup_integration.py only records, which is enough there. It is not
    enough here: the backup job deletes the local archive once the upload verifies (correctly — it
    is the only copy known to restore, and only after verification), so a restore test that reads
    "from S3" against a recorder finds nothing. Copying on upload is what makes the round trip
    real, and reading the archive FROM the bucket rather than from local staging is CLAUDE.md § 3's
    requirement rather than a convenience.
    """

    def __init__(self, store: Path):
        self.store = store
        self.store.mkdir(parents=True, exist_ok=True)
        self.uploaded = []
        self.downloaded = []

    def _path(self, bucket, key):
        return self.store / f"{bucket}__{key.replace('/', '_')}"

    def upload_file(self, filename, bucket, key):
        shutil.copy2(filename, self._path(bucket, key))
        self.uploaded.append((filename, bucket, key))

    def head_object(self, Bucket, Key):
        return {"ContentLength": self._path(Bucket, Key).stat().st_size}

    def copy_object(self, Bucket, Key, CopySource):
        source = CopySource["Key"] if isinstance(CopySource, dict) else CopySource
        shutil.copy2(self._path(Bucket, source.split("/", 1)[-1]), self._path(Bucket, Key))

    def download_file(self, bucket, key, destination):
        self.downloaded.append((bucket, key))
        shutil.copy2(self._path(bucket, key), destination)


def test_backup_rows_visible_from_new_connection(
    tmp_path, scheduler_table, database_url, matching_pg_client
):
    """`backup_nightly_job`'s `backups` row must outlive the connection that inserted it.

    THE REGRESSION TEST FOR THE PHASE 11 DEFECT, named for the property rather than for the
    end-to-end flow, so that what it guards is findable from the invariant.
    """
    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)
    s3 = RoundTripS3(tmp_path / "bucket")

    backup.backup_nightly_job(
        _with_password(database_url),
        bucket="test-bucket", staging_dir=staging, s3=s3, run=_reachable_run,
    )

    with db.connection(database_url) as conn:
        rows = conn.execute(
            "SELECT backup_id, verified FROM backups ORDER BY started_at DESC"
        ).fetchall()

    assert len(rows) == 1, (
        f"the backup job returned successfully, uploaded {len(s3.uploaded)} object(s), and a new "
        f"connection sees {len(rows)} backups row(s). This is the Phase 11 defect exactly: "
        f"db.connection commits nothing implicitly, so the INSERT is discarded on close while "
        f"every layer above reports success."
    )
    assert rows[0][1] is True, "the recorded backup is not marked verified"


def test_restore_test_mark_verified_visible_from_new_connection(
    tmp_path, scheduler_table, database_url, matching_pg_client
):
    """`restore_test_monthly_job`'s verification mark must outlive its connection.

    Drives BOTH jobs in sequence, because the restore test's input is the nightly job's output and
    a hand-built `backups` row would be a fixture asserting itself. The archive genuinely round
    trips through the stub bucket: uploaded by one job, deleted from staging, downloaded by the
    other.

    Slow — it starts a throwaway container and performs a real restore — and that is the price of
    the only assertion that reaches this commit.
    """
    staging = tmp_path / "backups"
    staging.mkdir()
    staging.chmod(0o777)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch.chmod(0o777)
    s3 = RoundTripS3(tmp_path / "bucket")

    backup.backup_nightly_job(
        _with_password(database_url),
        bucket="test-bucket", staging_dir=staging, s3=s3, run=_reachable_run,
    )

    restore_test.restore_test_monthly_job(
        _with_password(database_url), scratch_dir=scratch, s3=s3, run=_reachable_run,
    )

    assert s3.downloaded, (
        "the restore test read no object from the bucket; it must restore FROM S3, never from "
        "local staging (CLAUDE.md § 3)"
    )

    with db.connection(database_url) as conn:
        row = conn.execute(
            "SELECT restore_verified_at, restore_verified_counts, restore_notes"
            " FROM backups ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    assert row is not None, "no backups row survived the nightly job"
    verified_at, counts, notes = row

    assert verified_at is not None, (
        "the restore test completed — it downloaded the archive, restored it, compared every "
        "table and made the read-only role refuse a DELETE — and a new connection sees "
        "restore_verified_at still NULL. mark_verified's commit (restore_test.py:572) is not "
        "reaching the database, so a backup that really was verified reads as never verified."
    )
    assert isinstance(verified_at, datetime) and verified_at.tzinfo is not None
    assert counts, f"restore_verified_counts is empty: {counts!r}"
    assert notes and "tables compared" in notes, f"restore_notes did not survive: {notes!r}"
