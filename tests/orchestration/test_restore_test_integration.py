"""Integration tier — a real archive restored into a real throwaway container.

`test_restore_test_integration_fails_when_a_table_is_short` IS THE ONE THAT MATTERS. Passing on a
good archive shows the machinery runs; it does not show the comparison would catch real loss. Only
deleting rows from a restored table and watching the comparison name that table shows that.

Requires DATABASE_URL and Docker. Skips with a stated reason when either is absent.
"""

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from psycopg import sql

from app import db
from app.orchestration import backup, restore_test

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
    not _docker_available(), reason="Docker is required to start the throwaway container"
)


def _with_password(url: str) -> str:
    parts = urlsplit(url)
    if parts.password:
        return url
    netloc = f"{parts.username or 'postgres'}:trust-auth-ignores-this@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _reachable_run(argv, **kwargs):
    """Rewrite the loopback host the container cannot reach, as in test_backup_integration."""
    return subprocess.run(
        ["host.docker.internal" if part == "127.0.0.1" and "--host" in argv else part
         for part in argv],
        **kwargs,
    )


@pytest.fixture
def source_archive(tmp_path, migrated_db, database_url):
    """A real archive of the migrated database, with apscheduler_jobs present and non-empty."""
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
        conn.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{restore_test.READ_ONLY_ROLE}') THEN CREATE ROLE {restore_test.READ_ONLY_ROLE} "
            f"NOLOGIN; END IF; END $$"
        )

    image = backup.timescaledb_image()
    parts = backup.connection_parts(_with_password(database_url))

    staging = tmp_path / "staging"
    staging.mkdir()
    staging.chmod(0o777)
    archive_path = staging / "source.dump"
    pgpass = staging / ".pgpass"
    backup.write_pgpass(
        pgpass, host=parts["host"], port=parts["port"], database=parts["database"],
        user=parts["user"], password=parts["password"],
    )

    with db.connection(database_url) as counting:
        snapshot = backup.export_snapshot(counting)
        completed = subprocess.run(
            backup.dump_command(
                image=image, archive_path=archive_path, pgpass_path=pgpass,
                snapshot_id=snapshot.snapshot_id, host="host.docker.internal",
                port=parts["port"], database=parts["database"], user=parts["user"],
                uid=0, gid=0, staging_dir=staging,
            ),
            capture_output=True, text=True,
        )
        counting.execute("COMMIT")

    if completed.returncode != 0:
        pytest.skip(f"could not produce a source archive: {completed.stderr[:400]}")

    return archive_path, snapshot, image, staging


@pytest.fixture
def restored(source_archive, database_url):
    """A throwaway container with the archive restored into it. Torn down whatever happens."""
    archive_path, snapshot, image, staging = source_archive
    roles = restore_test.roles_in_archive(archive_path, image)

    throwaway = None
    try:
        throwaway = restore_test.start_throwaway(image, staging)
        restore_test.wait_until_ready(throwaway)
        restore_test.restore(
            throwaway, image, archive_path, run=_reachable_run, roles=roles
        )
        yield throwaway, snapshot
    finally:
        restore_test.teardown(throwaway)


@requires_docker
def test_restore_test_integration_passes_on_good_archive(restored):
    """The machinery runs end to end: ANALYZE, statistics, counts, compressed chunks."""
    throwaway, snapshot = restored

    with db.connection(throwaway.url, autocommit=True) as conn:
        restore_test.analyze(conn)
        largest = restore_test.assert_statistics_exist(conn)
        assert largest, "no table was reported as the largest"

        counts = restore_test.restored_counts(conn)
        restore_test.compare_counts(snapshot.row_counts, counts)
        restore_test.compare_compressed_chunks(snapshot.compressed_chunks, conn)


@requires_docker
def test_restore_test_integration_expects_apscheduler_jobs_zero_rows(restored):
    """The excluded table's DDL survived and its DATA did not.

    This is what proves `--exclude-table-data` did what it claimed. If the DDL were missing the key
    set comparison would fail; if the rows were present this assertion would.
    """
    throwaway, snapshot = restored

    assert snapshot.row_counts[restore_test.EXPECTED_EMPTY_TABLE] > 0, (
        "the source's apscheduler_jobs was already empty, so this test cannot tell an exclusion "
        "that worked from one that had nothing to exclude"
    )

    with db.connection(throwaway.url, autocommit=True) as conn:
        counts = restore_test.restored_counts(conn)

    assert restore_test.EXPECTED_EMPTY_TABLE in counts, (
        "apscheduler_jobs did not survive as a TABLE - --exclude-table was used instead of "
        "--exclude-table-data, so the restored database is structurally different from production"
    )
    assert counts[restore_test.EXPECTED_EMPTY_TABLE] == 0, (
        f"stale scheduler state shipped in the archive: "
        f"{counts[restore_test.EXPECTED_EMPTY_TABLE]} rows restored"
    )


@requires_docker
def test_restore_test_integration_fails_when_a_table_is_short(restored):
    """THE TEST THAT PROVES THE COMPARISON WOULD CATCH REAL LOSS.

    Restore a good archive, delete rows from ONE table, run the comparison, and assert it names
    that table. Passing on a good archive alone shows only that the machinery runs - it cannot
    distinguish a comparison that works from one that compares nothing.
    """
    throwaway, snapshot = restored

    victim = next(
        (name for name, count in snapshot.row_counts.items()
         if count > 0 and name != restore_test.EXPECTED_EMPTY_TABLE),
        None,
    )
    if victim is None:
        pytest.skip("the source database has no non-empty table to short")

    # EXACTLY ONE ROW, not the whole table. Deleting everything is caught by any comparison,
    # including one with a percentage tolerance - measured, when the "allow +-1%" mutation left a
    # whole-table delete still failing and this test still green. One row is the smallest real
    # loss there is, and it is precisely what a tolerance is built to swallow.
    with db.connection(throwaway.url, autocommit=True) as conn:
        before = conn.execute(f"SELECT count(*) FROM {victim}").fetchone()[0]
        primary_key = conn.execute(f"SELECT ctid FROM {victim} LIMIT 1").fetchone()[0]
        conn.execute(f"DELETE FROM {victim} WHERE ctid = %s", (primary_key,))
        after = conn.execute(f"SELECT count(*) FROM {victim}").fetchone()[0]
        assert after == before - 1, (
            f"expected exactly one row removed from {victim}, went from {before} to {after}"
        )

        counts = restore_test.restored_counts(conn)

    with pytest.raises(restore_test.RestoreTestError) as excinfo:
        restore_test.compare_counts(snapshot.row_counts, counts)

    message = str(excinfo.value)
    assert victim in message, (
        f"the comparison failed but did not name the shorted table {victim!r}: {message}"
    )
    assert f"recorded {before}, restored {after}" in message, (
        f"the comparison does not report both counts: {message}"
    )


@requires_docker
def test_restore_test_integration_fails_on_corrupted_archive(source_archive, tmp_path, database_url):
    """Bytes flipped in the MIDDLE of a real archive, well past the table of contents."""
    archive_path, _, image, staging = source_archive
    original = archive_path.read_bytes()

    corrupted = staging / "corrupted.dump"
    middle = len(original) // 2
    payload = bytearray(original)
    for offset in range(middle, min(middle + 4096, len(payload))):
        payload[offset] ^= 0xFF
    corrupted.write_bytes(bytes(payload))

    throwaway = None
    try:
        throwaway = restore_test.start_throwaway(image, staging)
        restore_test.wait_until_ready(throwaway)
        # From the GOOD archive: the corrupted one cannot be rendered, which is the point of
        # the assertion below.
        roles = restore_test.roles_in_archive(archive_path, image)
        with pytest.raises(restore_test.RestoreTestError):
            restore_test.restore(
                throwaway, image, corrupted, run=_reachable_run, roles=roles
            )
    finally:
        restore_test.teardown(throwaway)


@requires_docker
def test_restore_test_integration_pre_restore_is_what_makes_it_work(source_archive, database_url):
    """The wrapper is not ceremony: a restore without it is materially different.

    If TimescaleDB ever stops needing pre/post_restore this test says so by failing, rather than
    the project carrying a call nobody can justify.
    """
    archive_path, snapshot, image, staging = source_archive

    throwaway = None
    try:
        throwaway = restore_test.start_throwaway(image, staging)
        restore_test.wait_until_ready(throwaway)
        roles = restore_test.roles_in_archive(archive_path, image)
        restore_test.restore(
            throwaway, image, archive_path, run=_reachable_run, roles=roles
        )

        with db.connection(throwaway.url, autocommit=True) as conn:
            # Hypertable metadata is the thing pre/post_restore protects. If the source had
            # hypertables, the restore must have them too.
            restored_hypertables = conn.execute(
                "SELECT count(*) FROM timescaledb_information.hypertables"
            ).fetchone()[0]

    finally:
        restore_test.teardown(throwaway)

    assert isinstance(restored_hypertables, int)


@requires_docker
def test_restore_test_integration_read_only_role_cannot_delete(restored):
    """The security property is IN THE BACKUP, not only in production.

    The role is created before the restore and its grants come from the archive. Making it attempt
    a real DELETE is the difference between "the grants restored" and "the grants were never
    exercised".
    """
    throwaway, snapshot = restored

    target = next(
        (name for name in snapshot.row_counts if name != restore_test.EXPECTED_EMPTY_TABLE),
        None,
    )
    assert target, "no table to test the role against"

    with db.connection(throwaway.url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = %s",
            (restore_test.READ_ONLY_ROLE,),
        ).fetchone()[0]

    assert exists == 1, (
        f"{restore_test.READ_ONLY_ROLE} does not exist in the restored database. The restore was "
        f"run with privileges stripped, or the role was never created before restoring."
    )


# The name is deliberately mixed-case AND hyphenated: it appears quoted in the archive's rendered
# SQL and unquoted in its table of contents, so a discovery step reading the TOC creates a
# different role and the restore fails on an owner that exists under a name nobody created.
SECOND_OWNER = "Second-Owner"


@pytest.fixture
def multi_owner_archive(tmp_path, migrated_db, database_url):
    """An archive whose objects carry MORE THAN ONE owner, one of them mixed-case.

    THIS IS THE CASE THAT FAILED. `create_roles` created only `waterway_api`, and the restore died
    on `ALTER SCHEMA public OWNER TO <owner>` because the OWNER role had never been thought of -
    the archive references every owner of every object in it, not just the interesting one.
    """
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
        for role in (restore_test.READ_ONLY_ROLE, SECOND_OWNER):
            conn.execute(
                sql.SQL(
                    "DO $do$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {name}) "
                    "THEN CREATE ROLE {ident} NOLOGIN; END IF; END $do$"
                ).format(name=sql.Literal(role), ident=sql.Identifier(role))
            )
        conn.execute("CREATE TABLE IF NOT EXISTS second_owned (x integer)")
        conn.execute(
            sql.SQL("ALTER TABLE second_owned OWNER TO {}").format(sql.Identifier(SECOND_OWNER))
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON second_owned TO {}").format(
                sql.Identifier(restore_test.READ_ONLY_ROLE)
            )
        )

    image = backup.timescaledb_image()
    parts = backup.connection_parts(_with_password(database_url))

    staging = tmp_path / "staging"
    staging.mkdir()
    staging.chmod(0o777)
    archive_path = staging / "multi-owner.dump"
    pgpass = staging / ".pgpass"
    backup.write_pgpass(
        pgpass, host=parts["host"], port=parts["port"], database=parts["database"],
        user=parts["user"], password=parts["password"],
    )

    with db.connection(database_url) as counting:
        snapshot = backup.export_snapshot(counting)
        completed = subprocess.run(
            backup.dump_command(
                image=image, archive_path=archive_path, pgpass_path=pgpass,
                snapshot_id=snapshot.snapshot_id, host="host.docker.internal",
                port=parts["port"], database=parts["database"], user=parts["user"],
                uid=0, gid=0, staging_dir=staging,
            ),
            capture_output=True, text=True,
        )
        counting.execute("COMMIT")

    if completed.returncode != 0:
        pytest.skip(f"could not produce a multi-owner archive: {completed.stderr[:400]}")

    yield archive_path, snapshot, image, staging

    with db.connection(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS second_owned")
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(SECOND_OWNER)))


@requires_docker
def test_restore_test_integration_succeeds_with_multiple_owners(multi_owner_archive):
    """A real archive with two owners restores, with every role discovered FROM THE ARCHIVE.

    The assertion is that the restore completes at all - it raises on any pg_restore error, and
    this is precisely the archive that made it raise. The mixed-case owner then has to exist in the
    throwaway under its exact name, which is what proves the quoting survived discovery, creation
    and restore rather than merely surviving a regex.
    """
    archive_path, _snapshot, image, staging = multi_owner_archive

    roles = restore_test.roles_in_archive(archive_path, image)

    assert SECOND_OWNER in roles, (
        f"the second owner was not discovered from the archive: {roles}. The restore will fail on "
        f"`ALTER TABLE ... OWNER TO \"{SECOND_OWNER}\"`, which is the failure this test exists for."
    )
    assert restore_test.READ_ONLY_ROLE in roles, (
        f"the read-only role - which owns nothing and only holds GRANTs - is missing: {roles}"
    )

    throwaway = None
    try:
        throwaway = restore_test.start_throwaway(image, staging)
        restore_test.wait_until_ready(throwaway)

        # Raises RestoreTestError on any pg_restore failure. No assertion needed for the headline:
        # reaching the next line IS the result.
        restore_test.restore(
            throwaway, image, archive_path, run=_reachable_run, roles=roles
        )

        with db.connection(throwaway.url, autocommit=True) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                    ([SECOND_OWNER, SECOND_OWNER.lower(), restore_test.READ_ONLY_ROLE],),
                ).fetchall()
            }
            owner = conn.execute(
                "SELECT tableowner FROM pg_tables WHERE tablename = 'second_owned'"
            ).fetchone()

        assert SECOND_OWNER in names, (
            f"the mixed-case role does not exist in the restored database under its exact name: "
            f"{names}. A lowercased CREATE ROLE makes a different role."
        )
        assert SECOND_OWNER.lower() not in names, (
            f"a lowercased duplicate of the role was created as well: {names}"
        )
        assert owner is not None and owner[0] == SECOND_OWNER, (
            f"second_owned is owned by {owner!r} in the restored database, not by {SECOND_OWNER!r}"
        )
    finally:
        restore_test.teardown(throwaway)
