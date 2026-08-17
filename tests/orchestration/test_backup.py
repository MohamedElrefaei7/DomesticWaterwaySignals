"""Unit tier — the nightly backup's decisions, without Docker, S3 or a database.

The container invocation and boto3 are both injected, so what is exercised here is the ARGV the
job builds and the ORDER it does things in. That is most of what can go wrong: `--list` instead of
a full restore, `--exclude-table` instead of `--exclude-table-data`, a snapshot id that never
reaches pg_dump, an ETag comparison that cannot work.

The one thing this tier cannot show is that a truncated archive actually fails. That needs a real
archive and lives in test_backup_integration.py.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.orchestration import backup

NOW = datetime(2026, 8, 17, 2, 0, 0, tzinfo=timezone.utc)
FIRST_OF_MONTH = datetime(2026, 9, 1, 2, 0, 0, tzinfo=timezone.utc)

DIGEST = "sha256:" + "33" * 32
IMAGE = f"timescale/timescaledb:2.26.2-pg16@{DIGEST}"


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _dump_argv(**overrides):
    kwargs = dict(
        image=IMAGE,
        archive_path=Path("/mnt/data/backups/x.dump"),
        pgpass_path=Path("/mnt/data/backups/.pgpass-backup"),
        snapshot_id="00000003-0000001B-1",
        host="127.0.0.1",
        port=5432,
        database="waterway",
        user="waterway",
        uid=1000,
        gid=1000,
    )
    kwargs.update(overrides)
    return backup.dump_command(**kwargs)


# ---------------------------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------------------------


def test_backup_uses_pinned_digest_from_compose():
    """The dump runs off the SAME digest the Compose file pins for the server.

    A host `pg_dump` at a different major version than the server either refuses outright or
    produces a subtly wrong archive. Reading the digest rather than hardcoding it means the two
    match mechanically rather than because somebody remembered to update a second copy.
    """
    reference = backup.timescaledb_image()

    assert "@sha256:" in reference, f"{reference!r} carries no digest"

    compose = backup.COMPOSE_PATH.read_text(encoding="utf-8")
    assert reference in compose, (
        f"the image the job would run ({reference!r}) does not appear in docker-compose.yml"
    )

    # And it is the TIMESCALEDB service's image, not merely the first one in the file.
    assert "timescaledb" in reference, (
        f"the job resolved {reference!r}, which is not the database image"
    )

    argv = _dump_argv(image=reference)
    assert reference in argv, "the resolved digest never reaches the docker run argv"


def test_backup_refuses_an_undigested_compose_image(tmp_path):
    """A floating tag on the server image is a hard failure, not a shrug."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  timescaledb:\n    image: timescale/timescaledb:2.26.2-pg16\n",
        encoding="utf-8",
    )
    with pytest.raises(backup.BackupError, match="no digest"):
        backup.timescaledb_image(compose)


# ---------------------------------------------------------------------------------------------
# The dump invocation
# ---------------------------------------------------------------------------------------------


def test_backup_writes_to_file_not_stdout():
    """`-f` to a bind-mounted path, and NO shell pipe anywhere.

    `docker exec ... pg_dump | cat > file` reintroduces the truncation class directly: a broken
    pipe with a zero exit is precisely "exits zero, writes a third of a file". An argv list has no
    shell to interpret a pipe at all, which is why the command is built as a list.
    """
    argv = _dump_argv()

    assert "--file" in argv or "-f" in argv, f"no output file flag in {argv}"
    index = argv.index("--file") if "--file" in argv else argv.index("-f")
    assert argv[index + 1].endswith(".dump")

    joined = " ".join(argv)
    assert "|" not in joined, f"the invocation contains a pipe: {joined}"
    assert ">" not in joined, f"the invocation contains a redirect: {joined}"
    assert not any(part in ("sh", "bash", "-c") for part in argv), (
        f"the invocation goes through a shell, which is what makes a pipe possible: {argv}"
    )


def test_backup_passes_password_via_pgpassfile_not_env():
    """No PGPASSWORD in the container environment.

    Container environment is readable via `docker inspect` and in any process listing of the
    daemon's children, and § 22 already establishes the Docker socket is effectively root.
    """
    argv = _dump_argv()
    joined = " ".join(argv)

    assert "PGPASSWORD" not in joined, f"the password is passed in the environment: {argv}"
    assert "PGPASSFILE=/tmp/pgpass" in argv
    assert any(part.endswith(":/tmp/pgpass:ro") for part in argv), (
        f"the pgpass file is not mounted READ-ONLY: {argv}"
    )


def test_backup_writes_pgpass_at_mode_0600(tmp_path):
    path = tmp_path / ".pgpass"
    backup.write_pgpass(
        path, host="h", port=5432, database="d", user="u", password="secret"
    )
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert path.read_text().strip() == "h:5432:d:u:secret"


def test_backup_excludes_data_not_schema():
    """`--exclude-table-data`, never `--exclude-table`.

    Restoring stale scheduler state is worse than restoring none - but DROPPING the table leaves
    the restored database structurally different from production, which defeats the restore test
    that exists to compare them. Keep the DDL, drop the rows.
    """
    argv = _dump_argv()
    joined = " ".join(argv)

    assert f"--exclude-table-data={backup.EXCLUDED_DATA_TABLE}" in argv, (
        f"the data exclusion is missing or misspelled: {argv}"
    )
    assert f"--exclude-table={backup.EXCLUDED_DATA_TABLE}" not in joined, (
        "--exclude-table drops the table's DDL too, so the restored database no longer matches "
        "production and the restore test compares two different schemas"
    )


def test_backup_counts_use_exported_snapshot():
    """`--snapshot=<id>` is passed, and its value is the EXPORTED id.

    The wrong version counts on a normal connection before or after the dump. It is one fewer
    moving part and it is wrong: ingest runs concurrently, so the counts describe a state the
    archive does not contain, and the restore test built on them either fails spuriously or gets a
    tolerance wide enough to hide real loss.
    """
    argv = _dump_argv(snapshot_id="00000003-0000001B-1")

    snapshot_flags = [part for part in argv if part.startswith("--snapshot=")]
    assert snapshot_flags, f"pg_dump is not pinned to a snapshot: {argv}"
    assert snapshot_flags == ["--snapshot=00000003-0000001B-1"], (
        f"the snapshot flag does not carry the exported id: {snapshot_flags}"
    )


# ---------------------------------------------------------------------------------------------
# Pre-flight guards
# ---------------------------------------------------------------------------------------------


def test_backup_refuses_when_free_space_below_two_x_last_size(tmp_path):
    """A pre-flight refusal is a clean failure; a partial dump that fills the volume is an outage."""

    class Usage:
        def __init__(self, free):
            self.free = free

    with pytest.raises(backup.BackupError, match="refusing to dump"):
        backup.check_free_space(tmp_path, 1_000_000, usage=lambda _: Usage(1_500_000))

    # Exactly 2x is enough; the refusal is strict-below.
    assert backup.check_free_space(tmp_path, 1_000_000, usage=lambda _: Usage(2_000_000))


def test_backup_free_space_check_is_inapplicable_on_first_run(tmp_path):
    class Usage:
        free = 10

    assert backup.check_free_space(tmp_path, None, usage=lambda _: Usage()) == 10


def test_backup_fails_when_excluded_table_absent():
    """THEME 1 IN A SINGLE FLAG, and the guard against it.

    `pg_dump --exclude-table-data` succeeds SILENTLY when its pattern matches nothing. If the
    scheduler's table is renamed the exclusion becomes a no-op, stale scheduler state ships in
    every backup, and there is no error anywhere - not in pg_dump, not in job_runs, not in the
    archive itself.
    """

    class Conn:
        def __init__(self, found):
            self.found = found

        def execute(self, sql, params=None):
            class Cursor:
                def fetchone(inner):
                    return (self.found,)

            return Cursor()

    with pytest.raises(backup.BackupError, match="matches no table"):
        backup.assert_excluded_table_exists(Conn(0))

    backup.assert_excluded_table_exists(Conn(1))  # present: no raise


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------


def test_backup_verification_is_not_list(tmp_path):
    """The verification invocation is a full restore to /dev/null, never `--list`.

    `--list` reads only the archive's table of contents. The dump that was one third its correct
    size passed it cleanly, matched its own SHA-256 across three machines, and failed on restore.
    """
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return Completed(0, "", "")

    backup.verify_archive(tmp_path / "x.dump", IMAGE, run=run)

    argv = captured["argv"]
    assert "pg_restore" in argv
    assert "--list" not in argv and "-l" not in argv, (
        f"verification uses the table-of-contents read, which a truncated archive passes: {argv}"
    )
    assert "--file" in argv and argv[argv.index("--file") + 1] == "/dev/null", (
        f"verification does not restore to /dev/null: {argv}"
    )


def test_backup_verification_rejects_nonempty_stderr(tmp_path):
    """Exit 0 WITH stderr output is a failure.

    pg_restore reports several classes of damage as warnings on stderr while still exiting zero,
    so an exit-code-only check is green over an archive that has already said it is broken.
    """
    def run(argv, **kwargs):
        return Completed(0, "", "pg_restore: warning: errors ignored on restore: 3")

    with pytest.raises(backup.BackupError) as excinfo:
        backup.verify_archive(tmp_path / "x.dump", IMAGE, run=run)

    assert "errors ignored on restore" in str(excinfo.value), (
        "the observed stderr is not reported, so the operator cannot see what pg_restore said"
    )

    # And the clean case does not raise, or the assertion above holds for the wrong reason.
    backup.verify_archive(tmp_path / "x.dump", IMAGE, run=lambda *a, **k: Completed(0, "", ""))


def test_backup_size_floor_compares_to_last_verified_row():
    with pytest.raises(backup.BackupError, match="below"):
        backup.check_size_floor(100, {"backup_id": 7, "byte_size": 1000})

    backup.check_size_floor(600, {"backup_id": 7, "byte_size": 1000})


def test_backup_size_floor_inapplicable_on_first_run(caplog):
    """No prior row: log it and proceed. Do NOT invent a hardcoded byte constant.

    A constant chosen today is wrong the first time the database grows, and the natural response
    to a check that is wrong is to delete it.
    """
    backup.check_size_floor(1, None)  # no raise


# ---------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------


class FakeS3:
    def __init__(self, content_length=None, fail_head=False):
        self.uploaded = []
        self.copied = []
        self._content_length = content_length
        self._fail_head = fail_head

    def upload_file(self, filename, bucket, key):
        self.uploaded.append((filename, bucket, key))

    def head_object(self, Bucket, Key):
        if self._fail_head:
            raise AssertionError("head_object should not have been reached")
        length = self._content_length
        if length is None:
            length = Path(self.uploaded[-1][0]).stat().st_size
        return {"ContentLength": length, "ETag": '"deadbeef-3"'}

    def copy_object(self, Bucket, Key, CopySource):
        self.copied.append((Bucket, Key, CopySource))


def test_backup_upload_verified_by_content_length_not_etag(tmp_path):
    """No ETag/MD5 comparison anywhere.

    `upload_file` is multipart-capable, and a multipart ETag is a hash of concatenated part hashes
    with a part-count suffix - it is NOT the object's MD5. A check comparing them either always
    fails on large archives or gets deleted the first time somebody investigates why.
    """
    archive = tmp_path / "x.dump"
    archive.write_bytes(b"a" * 4096)

    # ASSERTED BEHAVIOURALLY, not by grepping the source for "ETag" - the module's own docstring
    # explains why it does not use one, and a text scan cannot tell prose from code.
    #
    # The ETag here is a MULTIPART one ("...-3"), which is what a real archive would carry and
    # which is not the object's MD5 of anything. Verification must succeed anyway.
    s3 = FakeS3()
    assert backup.upload_and_verify(s3, "bucket", "backups/daily/x.dump", archive) == 4096

    # And the converse: a CORRECT-looking ETag must not rescue a short object. An implementation
    # that compared ETags would pass this; one that compares ContentLength cannot.
    short = FakeS3(content_length=1024)
    with pytest.raises(backup.BackupError, match="upload verification FAILED"):
        backup.upload_and_verify(short, "bucket", "backups/daily/x.dump", archive)


def test_backup_keeps_local_file_when_upload_verification_fails(tmp_path):
    """The local archive is the only copy KNOWN to restore. Keep it, and say where it is."""
    archive = tmp_path / "x.dump"
    archive.write_bytes(b"a" * 4096)

    s3 = FakeS3(content_length=1024)  # S3 reports a short object
    with pytest.raises(backup.BackupError) as excinfo:
        backup.upload_and_verify(s3, "bucket", "backups/daily/x.dump", archive)

    assert archive.exists(), "the local archive was deleted after a failed upload verification"
    assert str(archive) in str(excinfo.value), (
        f"the error does not say where the kept file is: {excinfo.value}"
    )


def test_backup_copies_to_monthly_prefix_on_first_of_month(tmp_path):
    """Server-side copy: GetObject + PutObject, both already granted. No re-upload, no delete."""
    s3 = FakeS3()
    backup.copy_to_monthly(s3, "bucket", "backups/daily/x.dump", "backups/monthly/x.dump")

    assert s3.copied == [
        ("bucket", "backups/monthly/x.dump", {"Bucket": "bucket", "Key": "backups/daily/x.dump"})
    ]
    assert s3.uploaded == [], "the monthly copy re-uploaded the archive instead of copying it"


def test_backup_prefixes_match_the_terraform_lifecycle_rules():
    """The prefixes the job writes must be the ones the bucket expires.

    A mismatch is silent in both directions: objects under an unmatched prefix are never expired
    (the cost trap), and a lifecycle rule for a prefix nothing writes to expires nothing while
    reading as configured.
    """
    hcl = (
        Path(backup.REPO_ROOT) / "infra" / "terraform" / "backups.tf"
    ).read_text(encoding="utf-8")

    for prefix in (backup.DAILY_PREFIX, backup.MONTHLY_PREFIX):
        assert f'prefix = "{prefix}"' in hcl, (
            f"the job writes to {prefix!r} but no lifecycle rule in backups.tf covers it, so "
            f"those objects are retained forever"
        )


# ---------------------------------------------------------------------------------------------
# The recorded row
# ---------------------------------------------------------------------------------------------


def test_backup_rows_written_is_none():
    """NULL, not 0.

    `rows_written` means rows written TO THE DATABASE. A backup writes none, so "not applicable"
    is NULL while 0 would claim this job counts rows and today counted none. Setting it to the
    DUMPED row count is the tempting wrong version: it looks informative and makes one column mean
    two different things depending on which job wrote the row.
    """
    from app.orchestration import job as job_module

    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert "return None" in source, "backup_nightly_job does not explicitly return None"

    # And the decorator maps that to NULL rather than 0.
    assert job_module._rows_written_from(None, backup.JOB_NAME) is None
    assert job_module._rows_written_from(0, backup.JOB_NAME) == 0, (
        "0 and None must stay distinguishable, or this assertion proves nothing"
    )


def test_backup_records_only_verified_rows():
    """The INSERT hardcodes `verified = true`, and there is no false-writing path.

    A failed run writes no `backups` row at all; the failure lives in job_runs. A
    `verified = false` placeholder would be found by a later query for "the most recent backup".
    """
    captured = {}

    class Conn:
        def execute(self, sql, params=None):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params

            class Cursor:
                def fetchone(inner):
                    return (7,)

            return Cursor()

    snapshot = backup.Snapshot("id", {"public.a": 1}, 3)
    assert backup.record_backup(
        Conn(), bucket="b", key="k", byte_size=99, snapshot=snapshot,
        started_at=NOW, finished_at=NOW,
    ) == 7

    sql = captured["sql"]
    assert "INSERT INTO backups" in sql
    # `verified` is a LITERAL true in the statement, not a bound parameter. A parameter is a value
    # some future caller can pass false for; a literal is not.
    assert "true" in sql.lower(), f"the INSERT does not fix verified to true: {sql}"
    assert False not in (captured["params"] or ()), (
        f"a false was bound into the backups INSERT: {captured['params']}"
    )


def test_backup_snapshot_dataclass_carries_counts_and_chunks():
    snapshot = backup.Snapshot("id", {"public.a": 0, "public.b": 3}, 17)
    assert snapshot.row_counts["public.a"] == 0
    assert snapshot.compressed_chunks == 17

    payload = json.dumps(snapshot.row_counts)
    assert json.loads(payload) == {"public.a": 0, "public.b": 3}
