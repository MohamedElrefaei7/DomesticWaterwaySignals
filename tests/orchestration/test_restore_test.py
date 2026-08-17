"""Unit tier — the monthly restore test's decisions, without Docker or S3.

What is exercised here is the ARGV, the ORDER, and the COMPARISON. The comparison is the part with
the most ways to be quietly wrong: an intersection-only key check hides a dropped table, a
tolerance hides the loss the job exists to find, and stopping at the first mismatch turns one
investigation into several.

Whether a real archive really restores lives in test_restore_test_integration.py.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.orchestration import backup, restore_test

IMAGE = "timescale/timescaledb:2.26.2-pg16@sha256:" + "33" * 32


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingRun:
    """Captures every docker invocation, answering the ones the code reads output from."""

    def __init__(self, existing_container=""):
        self.calls = []
        self.existing = existing_container

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-a"]:
            return Completed(0, self.existing)
        if argv[:2] == ["docker", "port"]:
            return Completed(0, "127.0.0.1:54999\n")
        return Completed(0)

    def argv_containing(self, needle):
        return [call for call in self.calls if any(needle in part for part in call)]


class FakeS3:
    def __init__(self):
        self.downloads = []

    def download_file(self, bucket, key, destination):
        self.downloads.append((bucket, key, destination))
        Path(destination).write_bytes(b"archive-bytes")


# ---------------------------------------------------------------------------------------------
# Source of the archive
# ---------------------------------------------------------------------------------------------


def test_restore_test_downloads_from_s3_not_local_path(tmp_path):
    """The archive comes from the BUCKET.

    A local staging copy passing proves nothing about what is in S3 - and on a healthy instance it
    does not exist, because the nightly job deletes it once its upload is verified. This is also
    the only exercise the IAM read path gets before the day somebody needs it.
    """
    s3 = FakeS3()
    destination = tmp_path / "x.dump"

    # A STALE LOCAL FILE IS ALREADY THERE, and it is the whole point of the test. An
    # implementation that read local staging would find this, succeed, and report a verified
    # restore of an archive that is not the one in S3. Without it, a version that never calls S3
    # fails on "no bytes" - a red test for a different reason than the one it is named for.
    destination.write_bytes(b"STALE-LOCAL-COPY-NOT-FROM-S3")

    restore_test.download_archive(s3, "bucket", "backups/daily/x.dump", destination)

    assert s3.downloads == [("bucket", "backups/daily/x.dump", str(destination))], (
        "the archive was not fetched from S3. A local file passing proves nothing about what is "
        "in the bucket, and on a healthy instance the local file does not exist at all."
    )
    assert destination.read_bytes() == b"archive-bytes", (
        "the stale local copy survived, so what would be restored is not what S3 holds"
    )


def test_restore_test_download_of_zero_bytes_is_fatal(tmp_path):
    """A download that produced nothing must not proceed to "restore" an empty file."""

    class EmptyS3:
        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(b"")

    with pytest.raises(restore_test.RestoreTestError, match="no bytes"):
        restore_test.download_archive(EmptyS3(), "b", "k", tmp_path / "x.dump")


def test_restore_test_refuses_on_insufficient_free_space(tmp_path):
    """The archive AND the restored database both land beside production data."""

    class Usage:
        def __init__(self, free):
            self.free = free

    with pytest.raises(restore_test.RestoreTestError, match="refusing to start"):
        restore_test.check_free_space(tmp_path, 1_000_000, usage=lambda _: Usage(2_000_000))

    assert restore_test.check_free_space(tmp_path, 1_000_000, usage=lambda _: Usage(3_000_000))


# ---------------------------------------------------------------------------------------------
# The throwaway container
# ---------------------------------------------------------------------------------------------


def test_restore_test_uses_pinned_digest(tmp_path):
    """Same digest as production.

    Part 6's reason, plus one more: a TimescaleDB extension version mismatch on restore produces
    errors that read like data corruption, so a restore test on a different version would report a
    false failure in the one place a false failure is most expensive.
    """
    run = RecordingRun()
    restore_test.start_throwaway(IMAGE, tmp_path, run=run)

    starts = [c for c in run.calls if c[:3] == ["docker", "run", "-d"]]
    assert len(starts) == 1
    assert IMAGE in starts[0], f"the throwaway does not run the pinned image: {starts[0]}"

    # And the restore itself.
    argv = restore_test.restore_command(
        image=IMAGE, archive_path=tmp_path / "x.dump", port=5999, scratch=tmp_path
    )
    assert IMAGE in argv


def test_restore_test_container_name_collision_is_fatal():
    """Never "reuse the existing one": that restores into whatever is already in it."""
    run = RecordingRun(existing_container="dws-restore-test-abc123\n")

    with pytest.raises(restore_test.RestoreTestError, match="already exists"):
        restore_test.assert_no_such_container("dws-restore-test-abc123", run=run)


def test_restore_test_container_name_is_unique_and_not_a_production_name():
    names = {restore_test.container_name() for _ in range(50)}
    assert len(names) == 50, "container names collide"
    for name in names:
        assert name.startswith(restore_test.CONTAINER_PREFIX)
        assert name not in {"timescaledb", "api", "caddy", "frontend-build", "worker"}


def test_restore_test_publishes_only_on_loopback(tmp_path):
    """No published port on a public interface. The throwaway holds a copy of production data."""
    run = RecordingRun()
    restore_test.start_throwaway(IMAGE, tmp_path, run=run)

    (start,) = [c for c in run.calls if c[:3] == ["docker", "run", "-d"]]
    published = [start[i + 1] for i, part in enumerate(start) if part == "-p"]
    assert published, f"no port mapping in {start}"
    for mapping in published:
        assert mapping.startswith("127.0.0.1:"), (
            f"the throwaway publishes {mapping!r}, which is reachable off the host"
        )


def test_restore_test_container_torn_down_on_exception(tmp_path):
    """`docker rm -f` in a `finally`, and the logs are captured BEFORE removal.

    Tearing down the evidence at the moment it becomes useful is a small tragedy that recurs.
    """
    run = RecordingRun()
    throwaway = restore_test.Throwaway(name="dws-restore-test-x", port=1, scratch=tmp_path)

    restore_test.teardown(throwaway, run=run, keep_logs=True)

    removals = [c for c in run.calls if c[:3] == ["docker", "rm", "-f"]]
    assert removals, f"no `docker rm -f` was issued: {run.calls}"
    assert removals[0][3] == "dws-restore-test-x"

    logs_index = next(i for i, c in enumerate(run.calls) if c[:2] == ["docker", "logs"])
    rm_index = next(i for i, c in enumerate(run.calls) if c[:3] == ["docker", "rm", "-f"])
    assert logs_index < rm_index, "the container was removed before its logs were captured"


def test_restore_test_teardown_is_in_a_finally_block():
    """Asserted on the source's STRUCTURE, via the AST, not by grepping for the word.

    A `finally` is the only construct that survives KeyboardInterrupt, and a throwaway container
    that survives a Ctrl-C holds a database's worth of disk on the same volume as production.
    """
    import ast

    tree = ast.parse(Path(restore_test.__file__).read_text(encoding="utf-8"))
    job_func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "restore_test_monthly_job"
    )
    tries = [node for node in ast.walk(job_func) if isinstance(node, ast.Try) and node.finalbody]
    assert tries, "restore_test_monthly_job has no try/finally at all"

    finally_calls = [
        node.func.id
        for t in tries
        for node in ast.walk(ast.Module(body=t.finalbody, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "teardown" in finally_calls, (
        f"teardown() is not called from a finally block; calls found: {finally_calls}. Outside a "
        f"finally it does not run on KeyboardInterrupt."
    )


# ---------------------------------------------------------------------------------------------
# The restore
# ---------------------------------------------------------------------------------------------


def test_restore_test_does_not_pass_no_privileges(tmp_path):
    """`--no-owner --no-privileges` makes any restore succeed by discarding what is worth checking."""
    argv = restore_test.restore_command(
        image=IMAGE, archive_path=tmp_path / "x.dump", port=5999, scratch=tmp_path
    )
    joined = " ".join(argv)

    assert "--no-privileges" not in joined, f"privileges are stripped: {argv}"
    assert "--no-owner" not in joined, f"ownership is stripped: {argv}"
    assert "--exit-on-error" in argv, (
        "without --exit-on-error pg_restore restores what it can and exits zero, which is the "
        "failure mode this job exists to detect"
    )


def test_restore_test_creates_roles_before_restore():
    """Roles first, or the restore's GRANT statements have nothing to grant to.

    The statement is a psycopg `Composed` rather than a string since role names started coming
    from a parsed archive: composing the identifier is what keeps archive content out of executed
    SQL, and what quotes a mixed-case name correctly. `as_string(None)` renders it for inspection
    without needing a connection.
    """
    statements = []

    class Conn:
        def execute(self, sql, params=None):
            statements.append(" ".join(sql.as_string(None).split()))

    restore_test.create_roles(Conn())

    assert statements, "no role was created"
    assert any(restore_test.READ_ONLY_ROLE in s for s in statements), (
        f"the read-only role is not created: {statements}"
    )


def test_restore_test_calls_pre_and_post_restore_in_order(tmp_path):
    """pre_restore -> pg_restore -> post_restore.

    Without the wrapper the restore APPEARS TO SUCCEED while hypertable and chunk metadata is
    wrong, surfacing much later as queries that return plausible partial results.
    """
    import ast

    source = Path(restore_test.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "restore"
    )
    body = ast.get_source_segment(source, func)

    # PRESENCE FIRST, then order. Asserting order with `.index` alone raises ValueError when a
    # call is missing entirely, which is a red test for the wrong reason - it proves the test ran,
    # not that the guard caught anything. Measured: the "skip pre_restore" mutation did exactly
    # that.
    assert "timescaledb_pre_restore" in body, (
        "timescaledb_pre_restore() is never called. Without it the restore APPEARS TO SUCCEED "
        "while hypertable and chunk metadata is wrong, surfacing much later as queries that "
        "return plausible partial results."
    )
    assert "timescaledb_post_restore" in body, (
        "timescaledb_post_restore() is never called, so the extension is left in restore mode"
    )
    assert "restore_command" in body, "restore() does not invoke pg_restore at all"

    pre = body.index("timescaledb_pre_restore")
    post = body.index("timescaledb_post_restore")
    call = body.index("restore_command")

    assert pre < call < post, (
        f"the order is pre={pre}, pg_restore={call}, post={post}. pre_restore must precede the "
        f"restore and post_restore must follow it."
    )


def test_restore_test_runs_analyze_and_asserts_statistics_exist():
    """ANALYZE's EFFECT is asserted, not its invocation.

    A step that runs ANALYZE and never checks it ran is a step that quietly stops running.
    """
    class Conn:
        def __init__(self, row):
            self.row = row
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(" ".join(sql.split()))

            class Cursor:
                def fetchone(inner):
                    return self.row

            return Cursor()

    analyzed = Conn(("gauge_readings_iv", True))
    restore_test.analyze(analyzed)
    assert "ANALYZE" in analyzed.statements

    assert restore_test.assert_statistics_exist(analyzed) == "gauge_readings_iv"

    # No statistics: the restored planner is flying blind and this must be a failure.
    with pytest.raises(restore_test.RestoreTestError, match="no statistics"):
        restore_test.assert_statistics_exist(Conn(("gauge_readings_iv", False)))

    # No user tables at all: the restore landed nothing.
    with pytest.raises(restore_test.RestoreTestError, match="landed nothing"):
        restore_test.assert_statistics_exist(Conn(None))


# ---------------------------------------------------------------------------------------------
# The comparison — the part with the most ways to be quietly wrong
# ---------------------------------------------------------------------------------------------


BASE = {"public.gauges": 4, "public.barge_rates": 100, restore_test.EXPECTED_EMPTY_TABLE: 3}


def test_restore_test_key_sets_compared_both_directions():
    """A missing table fails, AND an unexpected one fails.

    Comparing only the intersection hides both. The second matters because an unexpected restored
    table means the archive contains something production does not.
    """
    restored = {"public.gauges": 4, "public.barge_rates": 100, restore_test.EXPECTED_EMPTY_TABLE: 0}
    restore_test.compare_counts(BASE, restored)  # the passing case

    dropped = dict(restored)
    del dropped["public.barge_rates"]
    with pytest.raises(restore_test.RestoreTestError) as missing:
        restore_test.compare_counts(BASE, dropped)
    assert "NOT RESTORED" in str(missing.value)
    assert "public.barge_rates" in str(missing.value)

    extra = dict(restored)
    extra["public.something_else"] = 1
    with pytest.raises(restore_test.RestoreTestError) as unexpected:
        restore_test.compare_counts(BASE, extra)
    assert "NOT RECORDED" in str(unexpected.value)
    assert "public.something_else" in str(unexpected.value)


def test_restore_test_reports_all_count_mismatches_not_first():
    """Stopping at the first turns one investigation into as many round trips as broken tables."""
    recorded = {"public.a": 10, "public.b": 20, "public.c": 30}
    restored = {"public.a": 9, "public.b": 19, "public.c": 30}

    with pytest.raises(restore_test.RestoreTestError) as excinfo:
        restore_test.compare_counts(recorded, restored)

    message = str(excinfo.value)
    assert "public.a" in message and "public.b" in message, (
        f"not every mismatch was reported: {message}"
    )
    assert "recorded 10, restored 9" in message
    assert "recorded 20, restored 19" in message


def test_restore_test_has_no_tolerance():
    """A single row short is a failure. Any tolerance is a tolerance for the loss this detects."""
    with pytest.raises(restore_test.RestoreTestError, match="recorded 1000000, restored 999999"):
        restore_test.compare_counts({"public.a": 1_000_000}, {"public.a": 999_999})


def test_restore_test_expects_apscheduler_jobs_zero_rows():
    """The ONE table whose restored count legitimately differs, asserted rather than skipped.

    Part 6 excluded its DATA and kept its DDL, so it must come back EMPTY. Asserting the expected
    difference is what proves the exclusion worked; skipping the table would let a dump that
    shipped stale scheduler state pass unnoticed.
    """
    # Recorded 3, restored 0: correct.
    restore_test.compare_counts(
        {restore_test.EXPECTED_EMPTY_TABLE: 3}, {restore_test.EXPECTED_EMPTY_TABLE: 0}
    )

    # Recorded 3, restored 3: the exclusion did NOT work and stale scheduler state shipped.
    with pytest.raises(restore_test.RestoreTestError) as excinfo:
        restore_test.compare_counts(
            {restore_test.EXPECTED_EMPTY_TABLE: 3}, {restore_test.EXPECTED_EMPTY_TABLE: 3}
        )
    assert "expected 0 rows" in str(excinfo.value)


def test_restore_test_asserts_compressed_chunk_count_matches():
    class Conn:
        def __init__(self, value):
            self.value = value

        def execute(self, sql, params=None):
            class Cursor:
                def fetchone(inner):
                    return (self.value,)

            return Cursor()

    assert restore_test.compare_compressed_chunks(17, Conn(17)) == 17

    with pytest.raises(restore_test.RestoreTestError, match="recorded 17, restored 4"):
        restore_test.compare_compressed_chunks(17, Conn(4))


def test_restore_test_asserts_read_only_role_cannot_delete():
    """The only assertion proving the security property is IN THE BACKUP."""

    class RefusingConn:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            if sql.startswith("DELETE"):
                raise PermissionError("permission denied for table gauges")

    conn = RefusingConn()
    restore_test.assert_read_only_role_cannot_delete(conn, "public.gauges")
    assert any("SET LOCAL ROLE" in s for s in conn.statements)
    assert any(s.startswith("DELETE") for s in conn.statements), (
        "no DELETE was attempted, so nothing was proven about the role"
    )
    assert any("RESET ROLE" in s for s in conn.statements)

    class PermissiveConn:
        def execute(self, sql, params=None):
            return None

    with pytest.raises(restore_test.RestoreTestError, match="permitted to DELETE"):
        restore_test.assert_read_only_role_cannot_delete(PermissiveConn(), "public.gauges")


# ---------------------------------------------------------------------------------------------
# The verification mark
# ---------------------------------------------------------------------------------------------


def test_restore_test_updates_only_permitted_columns():
    """Exactly the three columns migration 0026's trigger permits.

    A fourth column in the same statement raises in the DATABASE, which is the point of enforcing
    insert-once structurally - but catching it here means the failure is a test rather than a
    failed job at 3am.
    """
    captured = {}

    class Conn:
        def execute(self, sql, params=None):
            captured["sql"] = " ".join(sql.split())

    restore_test.mark_verified(Conn(), 7, {"public.a": 1}, "notes")

    sql = captured["sql"]
    assert sql.startswith("UPDATE backups SET")
    assigned = {
        part.split("=")[0].strip()
        for part in sql[len("UPDATE backups SET"): sql.index("WHERE")].split(",")
    }
    assert assigned == {
        "restore_verified_at",
        "restore_verified_counts",
        "restore_notes",
    }, f"the UPDATE touches {sorted(assigned)}; the trigger permits exactly three columns"


def test_restore_test_rows_written_is_none():
    """NULL, not 0. Same reasoning as the backup job."""
    from app.orchestration import job as job_module

    assert job_module._rows_written_from(None, restore_test.JOB_NAME) is None


def test_restore_test_no_verification_mark_before_the_assertions():
    """`mark_verified` is called AFTER the comparison, structurally.

    Marking first would leave a verification mark on a backup whose restore then failed, which is
    worse than no mark: a later reader would see restore_verified_at set and stop looking.
    """
    import ast

    source = Path(restore_test.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "restore_test_monthly_job"
    )
    body = ast.get_source_segment(source, func)

    assert body.index("compare_counts") < body.index("mark_verified"), (
        "the verification mark is written before the counts are compared"
    )
    assert body.index("compare_compressed_chunks") < body.index("mark_verified")
    assert body.index("assert_read_only_role_cannot_delete") < body.index("mark_verified")


def test_restore_test_expected_empty_table_matches_the_backup_exclusion():
    """The table this expects empty must be the one the dump actually excludes.

    Two constants naming one fact drift silently, and the divergence here would mean the restore
    test asserts an exemption for a table nothing excludes - passing over stale scheduler state
    while reporting the exclusion verified.
    """
    assert restore_test.EXPECTED_EMPTY_TABLE == (
        f"{backup.COUNTED_SCHEMA}.{backup.EXCLUDED_DATA_TABLE}"
    )


# ---------------------------------------------------------------------------------------------
# Role discovery: from the ARCHIVE, not from the live source database.
# ---------------------------------------------------------------------------------------------
#
# The first version read `pg_tables` and `pg_namespace` on the SOURCE. That worked, and it was
# describing the wrong object. The source moves on after a dump is taken: a role dropped from it
# would never be created in the throwaway, and one added to it would be created needlessly. Either
# way the throwaway stops matching the artifact under test, and the artifact is the thing being
# verified. It also gave a monthly restore test a hard dependency on production being reachable.


class _RenderedArchive:
    """A `run` stand-in returning prepared `pg_restore -f -` output.

    Stands in at the subprocess boundary, so the regexes, the PUBLIC exclusion and the unquoting
    are all real code under test - only the archive is fabricated.
    """

    def __init__(self, sql_text, returncode=0, stderr=""):
        self.sql_text = sql_text
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)

        class Completed:
            pass

        completed = Completed()
        completed.returncode = self.returncode
        completed.stdout = self.sql_text
        completed.stderr = self.stderr
        return completed


# Taken verbatim from a real archive of this project's own database, rendered on 2026-08-17.
REAL_RENDER = """
ALTER SCHEMA public OWNER TO dwstest;
ALTER FUNCTION public.backups_forbid_delete() OWNER TO dwstest;
ALTER TABLE public.probe_tbl OWNER TO "Mixed-Case_Owner";
REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT SELECT ON TABLE public.probe_tbl TO waterway_api;
GRANT SELECT ON TABLE public.probe_tbl TO PUBLIC;
"""


def test_create_roles_discovers_from_archive_not_live_db(tmp_path):
    """Discovery opens no connection to the source database at all.

    Asserted by SIGNATURE and by call, not by inspection: `roles_in_archive` takes a path and an
    image and has nowhere to put a connection. A version that reached for the source would have to
    grow a parameter, which is a visible change rather than a line inside a function body.
    """
    import inspect

    parameters = inspect.signature(restore_test.roles_in_archive).parameters
    assert "conn" not in parameters and "url" not in parameters, (
        f"roles_in_archive accepts {sorted(parameters)}. It must not be able to reach the source "
        f"database: the archive is the artifact under test and the source has moved on since the "
        f"dump was taken."
    )

    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"not really an archive; the render is stubbed")
    run = _RenderedArchive(REAL_RENDER)

    roles = restore_test.roles_in_archive(archive, "timescale/timescaledb:test", run=run)

    assert run.calls, "roles_in_archive issued no command at all"
    argv = run.calls[0]
    assert "pg_restore" in argv, f"the archive was not rendered with pg_restore: {argv}"
    assert str(archive) in argv, f"the command does not name the archive: {argv}"
    assert "-l" not in argv and "--list" not in argv, (
        f"the roles were read from the TABLE OF CONTENTS: {argv}. The TOC carries each object's "
        f"OWNER and no GRANTEES, so the read-only role - which owns nothing - would be missing; "
        f"and it renders names unquoted, which silently lowercases a mixed-case role."
    )
    assert roles, "no roles were discovered"


def test_create_roles_excludes_public_pseudo_role(tmp_path):
    """PUBLIC is never created. `CREATE ROLE PUBLIC` is an error.

    This bites on the FIRST `GRANT ... TO PUBLIC` in an archive, which is to say immediately: the
    render above is real, and it contains both a GRANT to PUBLIC and a REVOKE from PUBLIC. Without
    the exclusion the whole restore test aborts before the restore starts, with an error about
    role syntax rather than about anything to do with backups.
    """
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"stub")

    roles = restore_test.roles_in_archive(
        archive, "img", run=_RenderedArchive(REAL_RENDER)
    )

    assert not any(role.lower() == "public" for role in roles), (
        f"PUBLIC was discovered as a role to create: {roles}. It is a pseudo-role and CREATE ROLE "
        f"PUBLIC is rejected outright."
    )
    # And the exclusion is specific rather than a blanket filter on the GRANT path: the real
    # grantee beside it survives.
    assert restore_test.READ_ONLY_ROLE in roles, (
        f"excluding PUBLIC also dropped the real grantee: {roles}"
    )


def test_create_roles_preserves_quoted_mixed_case_names(tmp_path):
    """`"Mixed-Case_Owner"` survives as `Mixed-Case_Owner`, not `mixed-case_owner`.

    MEASURED, and this is why rendered SQL is parsed rather than the TOC:

        rendered SQL  ALTER TABLE public.probe_tbl OWNER TO "Mixed-Case_Owner";
        TOC (-l)      300; 1259 1770508 TABLE public probe_tbl Mixed-Case_Owner

    Creating the unquoted form makes a DIFFERENT role. The restore then fails on an owner that
    exists under a name nobody created, and the error names a role that looks right.
    """
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"stub")

    roles = restore_test.roles_in_archive(
        archive, "img", run=_RenderedArchive(REAL_RENDER)
    )

    assert "Mixed-Case_Owner" in roles, (
        f"the mixed-case owner was not preserved: {roles}. A lowercased name creates a different "
        f"role and the restore then fails on an owner that exists under a name nobody created."
    )
    assert "mixed-case_owner" not in roles, f"the name was folded to lower case: {roles}"

    # And the identifier survives composition into the CREATE ROLE statement, quoted.
    statements = []

    class Conn:
        def execute(self, sql, params=None):
            statements.append(sql.as_string(None))

    restore_test.create_roles(Conn(), ["Mixed-Case_Owner"])
    assert any('"Mixed-Case_Owner"' in s for s in statements), (
        f"CREATE ROLE did not quote the mixed-case identifier: {statements}"
    )


def test_create_roles_creates_nologin_roles():
    """NOLOGIN, and no password anywhere in the statement.

    The throwaway needs these roles as ownership and grant targets, never as connection
    identities - the read-only assertion is made with SET ROLE from the superuser session.
    Creating them with LOGIN and a password would put a credential in the test path for nothing.
    """
    statements = []

    class Conn:
        def execute(self, sql, params=None):
            statements.append(sql.as_string(None))

    restore_test.create_roles(Conn(), ["waterway_api", "some_owner"])

    assert len(statements) == 2, f"expected one statement per role, got {statements}"
    for statement in statements:
        assert "NOLOGIN" in statement, f"role created without NOLOGIN: {statement}"
        assert "PASSWORD" not in statement.upper(), (
            f"a password appears in a role-creation statement: {statement}. The throwaway needs "
            f"these roles as grant targets, not as connection identities."
        )


def test_create_roles_is_idempotent_for_existing_role():
    """Guarded by IF NOT EXISTS - `postgres` and the object owner usually already exist."""
    statements = []

    class Conn:
        def execute(self, sql, params=None):
            statements.append(" ".join(sql.as_string(None).split()))

    restore_test.create_roles(Conn(), ["postgres"])

    (statement,) = statements
    assert "IF NOT EXISTS" in statement.upper(), (
        f"role creation is unguarded: {statement}. `postgres` already exists in the throwaway, so "
        f"an unguarded CREATE ROLE fails the restore test before the restore begins."
    )
    assert "PG_ROLES" in statement.upper(), (
        f"the guard does not check pg_roles: {statement}"
    )


def test_roles_in_archive_raises_when_the_render_is_empty(tmp_path):
    """An empty render is a failure, never an empty role set.

    NOT IN THE BRIEF'S LIST, and here because an empty set is the shape that fails quietly: the
    restore would run with no roles created and fail on the first OWNER TO, reporting a missing
    role rather than a discovery step that read nothing. CLAUDE.md § 13 - a check that quietly
    becomes a no-op is theme 2 in its purest form.
    """
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"stub")

    with pytest.raises(restore_test.RestoreTestError) as raised:
        restore_test.roles_in_archive(archive, "img", run=_RenderedArchive(""))

    assert "no SQL" in str(raised.value), f"unhelpful message: {raised.value}"
