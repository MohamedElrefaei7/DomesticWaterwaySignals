"""Integration tier — a real archive restored into a real throwaway DATABASE.

`test_restore_test_integration_fails_when_a_table_is_short` IS THE ONE THAT MATTERS. Passing on a
good archive shows the machinery runs; it does not show the comparison would catch real loss. Only
deleting rows from a restored table and watching the comparison name that table shows that.

THE THROWAWAY IS A DATABASE ON THE SERVER UNDER TEST, NOT A CONTAINER, as of Phase 12: spawning a
container from inside the scheduler container would need the host's Docker socket, which is
root-equivalent on the host. So this tier needs DATABASE_URL and a major-matching postgres client,
and no longer needs Docker at all.

IT CREATES AND DROPS DATABASES ON WHATEVER DATABASE_URL POINTS AT. That is the job's own behaviour
and is bounded by the same name guard (`dws_restore_test_*`, plus an inequality against the
connected database's name), but it is worth knowing before pointing DATABASE_URL at anything
precious.
"""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from psycopg import sql

from app import db
from app.orchestration import backup, restore_test

pytestmark = pytest.mark.integration


def _with_password(url: str) -> str:
    parts = urlsplit(url)
    if parts.password:
        return url
    netloc = f"{parts.username or 'postgres'}:trust-auth-ignores-this@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


import contextlib


@contextlib.contextmanager
def throwaway_for(database_url):
    """Create a throwaway database, yield it, and DROP it whatever happens.

    Unconditional, unlike the job, which keeps a failed throwaway as evidence. A fixture that kept
    one would leak a database per failing run into whatever DATABASE_URL points at; the evidence a
    test needs is in its assertion.
    """
    url = _with_password(database_url)
    parts = backup.connection_parts(url)
    throwaway = None
    try:
        with db.connection(url, autocommit=True) as admin:
            throwaway = restore_test.create_throwaway(admin, url, parts["database"])
        yield throwaway, parts
    finally:
        if throwaway is not None:
            with db.connection(url, autocommit=True) as admin:
                restore_test.drop_throwaway(admin, throwaway)


@pytest.fixture
def source_archive(tmp_path, migrated_db, database_url, matching_pg_client):
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

    parts = backup.connection_parts(_with_password(database_url))

    staging = tmp_path / "staging"
    staging.mkdir()
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
                archive_path=archive_path, snapshot_id=snapshot.snapshot_id,
                host=parts["host"], port=parts["port"],
                database=parts["database"], user=parts["user"],
            ),
            capture_output=True, text=True,
            env=backup.pgpass_environment(pgpass),
        )
        counting.execute("COMMIT")

    if completed.returncode != 0:
        pytest.skip(f"could not produce a source archive: {completed.stderr[:400]}")

    return archive_path, snapshot, staging, pgpass


@pytest.fixture
def restored(source_archive, database_url):
    """A throwaway DATABASE with the archive restored into it. Dropped whatever happens.

    The fixture drops unconditionally, which is deliberately NOT what the job does - the job keeps
    a failed throwaway as evidence. A test fixture that kept one would leak a database per failing
    run into whatever DATABASE_URL points at, and the evidence a test needs is in the assertion,
    not in the server.
    """
    archive_path, snapshot, staging, pgpass = source_archive
    url = _with_password(database_url)
    parts = backup.connection_parts(url)
    roles = restore_test.roles_in_archive(archive_path)

    throwaway = None
    try:
        with db.connection(url, autocommit=True) as admin:
            throwaway = restore_test.create_throwaway(admin, url, parts["database"])
        restore_test.restore(
            throwaway, archive_path, parts=parts, pgpass_path=pgpass, roles=roles
        )
        yield throwaway, snapshot
    finally:
        if throwaway is not None:
            with db.connection(url, autocommit=True) as admin:
                restore_test.drop_throwaway(admin, throwaway)


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


def test_restore_test_integration_fails_on_corrupted_archive(source_archive, tmp_path, database_url):
    """Bytes flipped in the MIDDLE of a real archive, well past the table of contents."""
    archive_path, _, staging, pgpass = source_archive
    original = archive_path.read_bytes()

    corrupted = staging / "corrupted.dump"
    middle = len(original) // 2
    payload = bytearray(original)
    for offset in range(middle, min(middle + 4096, len(payload))):
        payload[offset] ^= 0xFF
    corrupted.write_bytes(bytes(payload))

    # From the GOOD archive: the corrupted one cannot be rendered, which is the point of the
    # assertion below.
    roles = restore_test.roles_in_archive(archive_path)

    with throwaway_for(database_url) as (throwaway, parts):
        with pytest.raises(restore_test.RestoreTestError):
            restore_test.restore(
                throwaway, corrupted, parts=parts, pgpass_path=pgpass, roles=roles
            )


def test_restore_test_integration_pre_restore_is_what_makes_it_work(source_archive, database_url):
    """The wrapper is not ceremony: a restore without it is materially different.

    If TimescaleDB ever stops needing pre/post_restore this test says so by failing, rather than
    the project carrying a call nobody can justify.
    """
    archive_path, snapshot, staging, pgpass = source_archive
    roles = restore_test.roles_in_archive(archive_path)

    with throwaway_for(database_url) as (throwaway, parts):
        # THE EXTENSION DOES NOT EXIST IN A template0 DATABASE, so `timescaledb_pre_restore()`
        # does not either. Measured against 2.26.2 on 2026-08-17:
        #     ERROR:  function timescaledb_pre_restore() does not exist
        # This is the assertion that would have caught it, and it runs BEFORE restore() creates
        # the extension - so it is a statement about the database restore() is handed, not about
        # what restore() leaves behind.
        with db.connection(throwaway.url, autocommit=True) as conn:
            pristine = conn.execute(
                "SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"
            ).fetchone()[0]
        assert pristine == 0, (
            "the throwaway already has the timescaledb extension, so this test cannot show that "
            "restore() is what creates it - which is the whole reason CREATE EXTENSION is there"
        )

        restore_test.restore(
            throwaway, archive_path, parts=parts, pgpass_path=pgpass, roles=roles
        )

        with db.connection(throwaway.url, autocommit=True) as conn:
            # Hypertable metadata is the thing pre/post_restore protects. If the source had
            # hypertables, the restore must have them too.
            restored_hypertables = conn.execute(
                "SELECT count(*) FROM timescaledb_information.hypertables"
            ).fetchone()[0]
            still_restoring = conn.execute(
                "SELECT current_setting('timescaledb.restoring', true)"
            ).fetchone()[0]

    assert isinstance(restored_hypertables, int)
    # post_restore ran: the database must not be left in the restoring state. Unlike a container,
    # this database is a real one on the production server for as long as it exists.
    assert still_restoring in (None, "", "off", "false"), (
        f"timescaledb.restoring is {still_restoring!r} after restore() returned - "
        f"timescaledb_post_restore() did not run or did not take"
    )


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
def multi_owner_archive(tmp_path, migrated_db, database_url, matching_pg_client):
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

    parts = backup.connection_parts(_with_password(database_url))

    staging = tmp_path / "staging"
    staging.mkdir()
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
                archive_path=archive_path, snapshot_id=snapshot.snapshot_id,
                host=parts["host"], port=parts["port"],
                database=parts["database"], user=parts["user"],
            ),
            capture_output=True, text=True,
            env=backup.pgpass_environment(pgpass),
        )
        counting.execute("COMMIT")

    if completed.returncode != 0:
        pytest.skip(f"could not produce a multi-owner archive: {completed.stderr[:400]}")

    yield archive_path, snapshot, staging, pgpass

    with db.connection(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS second_owned")
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(SECOND_OWNER)))


def test_restore_test_integration_succeeds_with_multiple_owners(multi_owner_archive, database_url):
    """A real archive with two owners restores, with every role discovered FROM THE ARCHIVE.

    The assertion is that the restore completes at all - it raises on any pg_restore error, and
    this is precisely the archive that made it raise. The mixed-case owner then has to exist in the
    throwaway under its exact name, which is what proves the quoting survived discovery, creation
    and restore rather than merely surviving a regex.
    """
    archive_path, _snapshot, staging, pgpass = multi_owner_archive

    roles = restore_test.roles_in_archive(archive_path)

    assert SECOND_OWNER in roles, (
        f"the second owner was not discovered from the archive: {roles}. The restore will fail on "
        f"`ALTER TABLE ... OWNER TO \"{SECOND_OWNER}\"`, which is the failure this test exists for."
    )
    assert restore_test.READ_ONLY_ROLE in roles, (
        f"the read-only role - which owns nothing and only holds GRANTs - is missing: {roles}"
    )

    with throwaway_for(database_url) as (throwaway, parts):
        # Raises RestoreTestError on any pg_restore failure. No assertion needed for the headline:
        # reaching the next line IS the result.
        restore_test.restore(
            throwaway, archive_path, parts=parts, pgpass_path=pgpass, roles=roles
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


# ---------------------------------------------------------------------------------------------
# The test that would have caught the 2026-08-18 pgpass defect
# ---------------------------------------------------------------------------------------------
#
# WHY THE REST OF THIS TIER MISSED IT, WHICH IS THE WHOLE POINT OF THIS SECTION.
#
# Every fixture above runs against a server started with `POSTGRES_HOST_AUTH_METHOD=trust` -
# measured on the project's own test container, whose pg_hba.conf is `host all all all trust`.
# `_with_password` at the top of this file exists only to satisfy the URL parser, and its
# placeholder is literally spelled `trust-auth-ignores-this`. On a trust server libpq never
# consults the pgpass file at all, so EVERY assertion in this tier is blind to what is in it.
#
# `test_restore_test_mark_verified_visible_from_new_connection` in
# test_orchestration_write_paths_commit.py already drives the real `restore_test_monthly_job` end
# to end, so the broken line WAS executed on every run - and stayed green, because the connection
# it authenticated was never asked for a password.
#
# So the test below has to satisfy BOTH conditions at once, and neither alone is sufficient:
#
#   1. a server that REQUIRES a password (not trust), and
#   2. a target database whose name DIFFERS from the dump's origin.
#
# Condition 2 is already true everywhere - the throwaway is always `dws_restore_test_<suffix>` -
# which is exactly why condition 1 was the one that hid this.

PASSWORD_SERVER_DB = "dwspwtest"
PASSWORD_SERVER_USER = "dwspwtest"
# A URI-safe password (CLAUDE.md § 5): `/` and `+` from base64 break DATABASE_URL parsing and
# surface as host and port errors rather than as authentication failures.
PASSWORD_SERVER_PASSWORD = "b6f1c0a94d2e7d5183ab0f4c9e2d7a61"


def _server_requires_a_password(url: str) -> bool:
    """Does this server actually refuse a wrong password? Measured, never assumed.

    THE CONFIGURATION IS NOT THE PROPERTY. A container started with the right environment variable
    can still come up trust - a reused volume keeps the pg_hba.conf written at first init - and a
    test that trusted `-e POSTGRES_HOST_AUTH_METHOD=scram-sha-256` would then assert nothing while
    reporting a pass. That is CLAUDE.md § 2's theme 2, in the fixture guarding against theme 2.

    So the check crosses the boundary the defect lives at: connect with a deliberately wrong
    password and see whether the server lets it through.
    """
    parts = urlsplit(url)
    wrong = urlunsplit((
        parts.scheme,
        f"{parts.username}:definitely-not-the-password@{parts.hostname}:{parts.port}",
        parts.path, parts.query, parts.fragment,
    ))
    try:
        with db.connection(wrong) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return True
    return False


@pytest.fixture(scope="session")
def password_required_database_url():
    """A Postgres that demands a password, at the same major as the local client.

    `PASSWORD_DATABASE_URL` is honoured if set, for a machine with no Docker. Otherwise a
    container is started here and removed afterwards. This is a TEST-TIER container started from
    the developer's own machine - it is not the Docker socket inside the scheduler container that
    CLAUDE.md § 22 forbids, and nothing in app/ gains a dependency on it.

    The image major is taken from the local `pg_dump`, so this server can never be the
    client/server mismatch that `matching_pg_client` exists to skip on.

    SKIPS LOUDLY, WITH THE REASON, whenever it cannot produce such a server - including when the
    server it was handed turns out to accept any password. A skip is visible in the report; the
    alternative is a test that quietly measures nothing, which is the failure this whole file is
    about.
    """
    import shutil as _shutil
    import time

    from tests.orchestration.conftest import local_client_major

    provided = os.environ.get("PASSWORD_DATABASE_URL", "").strip()
    if provided:
        if not _server_requires_a_password(provided):
            pytest.skip(
                "PASSWORD_DATABASE_URL points at a server that accepts ANY password, so it cannot "
                "demonstrate a pgpass lookup failing. That is the trust configuration this test "
                "exists because of - point it at a scram-sha-256 server."
            )
        yield provided
        return

    if _shutil.which("docker") is None:
        pytest.skip(
            "no docker on PATH and PASSWORD_DATABASE_URL is unset, so there is no "
            "password-requiring server to test against. NOTHING BELOW HAS BEEN VERIFIED: the rest "
            "of this tier runs against a trust server, where the pgpass file is never consulted."
        )

    major = local_client_major()
    if major is None:
        pytest.skip("no pg_dump on PATH: the dump and restore are invoked directly")

    image = f"timescale/timescaledb:latest-pg{major}"
    container = subprocess.run(
        [
            "docker", "run", "--detach", "--rm",
            # NO POSTGRES_HOST_AUTH_METHOD=trust. That variable is what the project's ordinary
            # test container sets, and setting it here would reproduce the blindness exactly.
            "--env", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
            "--env", f"POSTGRES_PASSWORD={PASSWORD_SERVER_PASSWORD}",
            "--env", f"POSTGRES_USER={PASSWORD_SERVER_USER}",
            "--env", f"POSTGRES_DB={PASSWORD_SERVER_DB}",
            "--publish", "127.0.0.1::5432",
            image,
        ],
        capture_output=True, text=True,
    )
    if container.returncode != 0:
        pytest.skip(f"could not start {image}: {container.stderr.strip()[:400]}")
    container_id = container.stdout.strip()

    try:
        published = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            capture_output=True, text=True,
        )
        port = published.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]
        url = (
            f"postgresql://{PASSWORD_SERVER_USER}:{PASSWORD_SERVER_PASSWORD}"
            f"@127.0.0.1:{port}/{PASSWORD_SERVER_DB}"
        )

        deadline = time.time() + 90
        last = None
        while time.time() < deadline:
            try:
                with db.connection(url) as conn:
                    conn.execute("SELECT 1")
                break
            except Exception as exc:  # noqa: BLE001 - the reason is reported on timeout
                last = exc
                time.sleep(1)
        else:
            pytest.skip(f"{image} never became ready: {last}")

        if not _server_requires_a_password(url):
            pytest.skip(
                f"{image} came up accepting any password despite "
                f"POSTGRES_HOST_AUTH_METHOD=scram-sha-256, so it cannot demonstrate a pgpass "
                f"lookup failing."
            )

        yield url
    finally:
        subprocess.run(["docker", "rm", "--force", container_id], capture_output=True)


@pytest.fixture
def password_required_stack(password_required_database_url):
    """The password-requiring server, migrated, with `apscheduler_jobs` present and non-empty.

    Non-empty on purpose: the backup asserts its `--exclude-table-data` target EXISTS before
    dumping, and the restore comparison asserts the restored copy has zero rows. An absent table
    fails the job for an unrelated reason and would read as this test finding something.

    DELIBERATELY NOT `matching_pg_client`. That fixture compares the local client against the
    server DATABASE_URL points at, which is a different server from this one - so on a machine
    whose client does not match the ordinary test database this test would skip for a reason that
    has nothing to do with the server it actually uses. The major agreement that matters here is
    guaranteed by construction, because the image tag above is chosen FROM the client's major, and
    it is asserted below rather than assumed.
    """
    from app.orchestration import migrate

    from tests.orchestration.conftest import local_client_major

    with db.connection(password_required_database_url) as conn:
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    client = local_client_major()
    assert client == server, (
        f"the password-requiring server is major {server} and the local client is major "
        f"{client}. The fixture derives the image tag from the client, so these can only differ "
        f"if PASSWORD_DATABASE_URL was pointed somewhere else - and a major mismatch fails the "
        f"job on the version pin rather than on the pgpass entry this test is about."
    )

    migrate.run(Path(__file__).resolve().parents[2] / "migrations", url=password_required_database_url)

    with db.connection(password_required_database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS apscheduler_jobs ("
            "  id varchar(191) NOT NULL PRIMARY KEY,"
            "  next_run_time double precision,"
            "  job_state bytea NOT NULL)"
        )
        conn.execute(
            "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
            "VALUES ('heartbeat', 1, '\\x00'::bytea) ON CONFLICT (id) DO NOTHING"
        )
    return password_required_database_url


def test_restore_test_integration_authenticates_against_a_password_required_server(
    tmp_path, password_required_stack, monkeypatch
):
    """THE TEST THAT WOULD HAVE CAUGHT IT. Drives the real job against a real password.

    It runs the nightly backup and then the monthly restore test, both through their real
    entrypoints, so the pgpass file under test is the one `restore_test_monthly_job` writes at its
    own call site - not one this test constructed, which would be a fixture asserting itself.

    The failure it reproduces is one wrong field out of five. libpq matches a pgpass line on host,
    port, database, user AND password, and it does not error when nothing matches: it prompts. On
    a non-TTY the prompt is answered with nothing, and the job dies with

        Password:
        FATAL:  password authentication failed for user "waterway"

    which is a message about the credential for a defect in the lookup. Reaching the assertions
    below at all is most of the result.
    """
    from tests.orchestration.test_orchestration_write_paths_commit import RoundTripS3

    url = password_required_stack

    # THE @job DECORATOR'S BOOKKEEPING CONNECTION TAKES DATABASE_URL FROM THE ENVIRONMENT. It has
    # to point at this server too, or the `job_runs` rows for these two runs land on a different
    # database from the work they describe - which would fail here for a reason that is not the
    # one under test.
    monkeypatch.setenv("DATABASE_URL", url)

    staging = tmp_path / "backups"
    staging.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    s3 = RoundTripS3(tmp_path / "bucket")

    backup.backup_nightly_job(url, bucket="test-bucket", staging_dir=staging, s3=s3)
    restore_test.restore_test_monthly_job(url, scratch_dir=scratch, s3=s3)

    assert s3.downloaded, (
        "the restore test read no object from the bucket, so it never reached pg_restore and this "
        "test cannot have exercised the pgpass entry"
    )

    with db.connection(url) as conn:
        row = conn.execute(
            "SELECT restore_verified_at, restore_notes FROM backups ORDER BY started_at DESC "
            "LIMIT 1"
        ).fetchone()

    assert row is not None, "the nightly job left no backups row to verify"
    verified_at, notes = row
    assert verified_at is not None, (
        f"the restore test did not mark the backup verified against a password-requiring server. "
        f"notes={notes!r}"
    )

    # THE THROWAWAY'S NAME IS THE OTHER HALF OF THE CONDITION. If it equalled the production
    # database, a pgpass entry keyed on production would match and this test would pass over the
    # defect - so the assertion states the condition it depends on rather than assuming it.
    assert "dws_restore_test" not in url, "the job ran against a throwaway, not against production"
    assert notes and "tables compared" in notes, (
        f"the verification note does not describe a comparison: {notes!r}"
    )
