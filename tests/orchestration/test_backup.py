"""Unit tier — the nightly backup's decisions, without Docker, S3 or a database.

The subprocess runner and boto3 are both injected, so what is exercised here is the ARGV the job
builds and the ORDER it does things in. That is most of what can go wrong: `--list` instead of a
full restore, `--exclude-table` instead of `--exclude-table-data`, a snapshot id that never reaches
pg_dump, an ETag comparison that cannot work.

PHASE 12 CHANGED HOW pg_dump IS INVOKED AND NOTHING ELSE ABOUT THIS JOB. It used to be a
`docker run` off the pinned server digest; a container cannot do that without the host's Docker
socket, and mounting the socket is root-equivalent on the host. So the client lives in the
scheduler image and is invoked directly. What that costs is a version pin in two files, and the
runtime half of closing that gap is `assert_client_server_majors_agree`, tested here.

The one thing this tier cannot show is that a truncated archive actually fails. That needs a real
archive and lives in test_backup_integration.py.
"""

import ast
import contextlib
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
        archive_path=Path("/mnt/data/backups/x.dump"),
        snapshot_id="00000003-0000001B-1",
        host="timescaledb",
        port=5432,
        database="waterway",
        user="waterway",
    )
    kwargs.update(overrides)
    return backup.dump_command(**kwargs)


class FakeConn:
    """A connection that answers every query the backup job issues BEFORE the dump.

    IT ANSWERS THE WHOLE PRE-DUMP PATH, not just the query under test, and that is what makes the
    ordering assertions in this file mean anything. A thinner fake fails somewhere in the middle -
    measured: an `IndexError: tuple index out of range` out of `last_verified_backup` - and a
    mutation that removes a guard then goes red on THAT rather than on the assertion. Red for the
    wrong reason proves the test runs, not that it watches anything (CLAUDE.md § 0).

    So with a guard removed the job gets all the way to invoking pg_dump, which is precisely the
    observation the ordering tests make: the runner's call list.
    """

    def __init__(self, server_version_num=160010, excluded_found=1):
        self.server_version_num = server_version_num
        self.excluded_found = excluded_found
        self.statements = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.statements.append(text)

        one = (0,)
        many = []
        if "server_version_num" in text:
            one = (self.server_version_num,)
        elif "FROM backups WHERE verified" in text:
            one = None                      # no prior verified backup: first run
        elif "information_schema.tables" in text:
            one = (self.excluded_found,)
        elif "pg_export_snapshot" in text:
            one = ("00000003-0000001B-1",)
        elif "FROM pg_tables" in text:
            many = [("probe_tbl",)]
        elif "timescaledb_information.chunks" in text:
            one = (0,)

        class Cursor:
            def fetchone(inner):
                return one

            def fetchall(inner):
                return many

        return Cursor()


def version_runner(version_line, *, returncode=0):
    """A `run` that answers `pg_dump --version` and records everything else it was asked."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--version":
            return Completed(returncode, version_line, "" if returncode == 0 else "boom")
        return Completed(0, "", "")

    run.calls = calls
    return run


# ---------------------------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------------------------


def docker_string_literals(source: str) -> list[str]:
    """Every string literal in `source` that names the docker CLI, DOCSTRINGS EXCLUDED.

    AN AST WALK, NOT A REGEX, AND CLAUDE.md § 23 NAMES THIS EXACT CASE: "the modules this guard
    covers contain the forbidden call in their own docstrings, in the sentences explaining why it
    is forbidden; a regex matches its own explanation, fails permanently, and the fix somebody
    reaches for is a weaker pattern."

    That is not hypothetical here. app/orchestration/backup.py's module docstring says the job
    "cannot `docker run` without the host's Docker socket", and a line-based scan that stripped
    `#` comments matched that sentence and failed on a correct file. Stripping docstrings too, by
    line, is the weaker pattern - it works until somebody writes the word in a different shape.

    THIS IS THE LEGITIMATE KIND OF SOURCE TEST (§ 23's other half): the call site IS the
    invariant. A `["docker", "run", ...]` list that is never executed is exactly as much of a
    violation as one that is, because what is forbidden is the code path existing at all.
    """
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                body = node.body[0]
                if isinstance(body, ast.Expr) and isinstance(body.value, ast.Constant):
                    docstrings.add(id(body.value))

    constants = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert constants, "the AST walk found no string constants at all - it parsed the wrong thing"

    def names_the_cli(value: str) -> bool:
        # `"docker"` as an argv element, `/usr/bin/docker` as an absolute one, or a shell string
        # beginning with the command. NOT `docker-compose.yml`, which is a filename this module
        # legitimately reads and which a `docker-` prefix match flags - measured, on this file.
        return value == "docker" or value.endswith("/docker") or value.startswith("docker ")

    return [
        f"line {node.lineno}: {node.value!r}"
        for node in constants
        if id(node) not in docstrings and names_the_cli(node.value)
    ]


def test_the_docker_scanner_ignores_prose_and_still_catches_code():
    """THE INVERTED MUTATION (CLAUDE.md § 23), because a strict guard is not a correct one.

    Two halves, and the first is the one that matters: a module whose DOCSTRINGS discuss `docker
    run` at length must produce NO findings. A guard that fails on its own justification gets
    repaired by weakening it, which is worse everywhere else.

    The second half proves the first is not vacuous - the same scanner, over an argv list, finds
    it. Without this, "no findings" would be satisfied by a scanner that finds nothing ever.
    """
    prose_only = '''
"""This module does not `docker run` anything.

A container cannot docker run without /var/run/docker.sock.
"""

def f():
    """Never `docker exec`, and never "docker" as a command."""
    return ["pg_dump", "--file", "x"]
'''
    assert docker_string_literals(prose_only) == [], (
        "the scanner matched its own explanation - the failure § 23 describes"
    )

    with_code = prose_only + '''

def g():
    return ["docker", "run", "--rm", "image", "pg_dump"]
'''
    found = docker_string_literals(with_code)
    assert len(found) == 1 and "'docker'" in found[0], found

    # AND A FILENAME IS NOT A COMMAND. `docker-compose.yml` is a path this module reads; a
    # `docker-` prefix match flags it, which is a false positive measured on the real file and
    # repaired here rather than by loosening what counts as a finding.
    assert docker_string_literals('x = "docker-compose.yml"\ny = "/usr/bin/docker"\n') == [
        "line 2: '/usr/bin/docker'"
    ]


def test_backup_invokes_pg_dump_directly_not_docker():
    """NO `docker run` ANYWHERE IN THIS MODULE. Not in the dump, not in the verification.

    A container cannot `docker run` without /var/run/docker.sock, and mounting it is
    root-equivalent on the host: a compromise of the container whose job is running scheduled
    Python would become a compromise of the instance (CLAUDE.md § 22).

    THE CONTAINER PATH IS DELETED, NOT KEPT BEHIND A FLAG. A retained branch reintroduces the
    socket requirement the moment somebody sets the flag, and dead code with a plausible use case
    is the code that comes back. So this asserts absence from the SOURCE as well as from the argv:
    an argv assertion alone stays green over a `if use_container:` branch nobody took today.

    Comments are stripped first - this module explains at length why the container invocation is
    gone, and a raw search would match the explanation (CLAUDE.md § 23).
    """
    argv = _dump_argv()
    assert argv[0] == backup.PG_DUMP, f"the dump does not invoke pg_dump directly: {argv}"
    assert "docker" not in argv, f"the dump still goes through docker: {argv}"

    verify_argv = {}

    def run(command, **kwargs):
        verify_argv["argv"] = command
        return Completed(0, "", "")

    backup.verify_archive(Path("/mnt/data/backups/x.dump"), run=run)
    assert verify_argv["argv"][0] == backup.PG_RESTORE, (
        f"verification does not invoke pg_restore directly: {verify_argv['argv']}"
    )
    assert "docker" not in verify_argv["argv"], (
        f"verification still goes through docker: {verify_argv['argv']}"
    )

    offenders = docker_string_literals(Path(backup.__file__).read_text(encoding="utf-8"))
    assert offenders == [], (
        f"app/orchestration/backup.py still builds a docker invocation: {offenders}. The "
        f"container-spawning path is deleted rather than disabled - a branch that can be "
        f"re-enabled reintroduces the Docker socket requirement, and dead code with a plausible "
        f"use case is the code that comes back."
    )


def test_backup_fails_when_client_major_differs_from_server():
    """THE RUNTIME HALF OF THE VERSION PIN, and it fails the job rather than warning.

    verify/preflight.py compares what the FILES say - the compose tag against the package pin in
    Dockerfile.scheduler. This compares what is RUNNING - the binary's own --version against the
    server's server_version_num. A stale image passes the first and fails here, which is exactly
    the case neither check catches alone, and it is the whole reason both exist.

    EQUALITY, NOT COMPATIBILITY: pg_dump older than the server refuses outright, and newer than
    the server usually works and is not what anything here was verified against.
    """
    agreeing = backup.assert_client_server_majors_agree(
        FakeConn(server_version_num=160010),
        run=version_runner("pg_dump (PostgreSQL) 16.10 (Debian 16.10-1.pgdg120+1)"),
    )
    assert agreeing == 16

    with pytest.raises(backup.BackupError) as excinfo:
        backup.assert_client_server_majors_agree(
            FakeConn(server_version_num=160010),
            run=version_runner("pg_dump (PostgreSQL) 15.14 (Debian 15.14-1.pgdg120+1)"),
        )
    message = str(excinfo.value)
    assert "major 15" in message and "major 16" in message, (
        f"the refusal does not report BOTH observed majors: {message}"
    )

    # A client NEWER than the server is refused too. "Usually works" is not an assertable property,
    # and the relaxation that admits it is the one that produces a subtly wrong archive.
    with pytest.raises(backup.BackupError):
        backup.assert_client_server_majors_agree(
            FakeConn(server_version_num=160010),
            run=version_runner("pg_dump (PostgreSQL) 17.2 (Debian 17.2-1.pgdg120+1)"),
        )


def test_backup_reads_the_server_major_from_server_version_num_not_the_display_string():
    """`server_version_num` is an integer; `server_version` is a display string.

    160010 is 16.10 and 90600 is 9.6, so integer division by 10000 is correct on both sides of
    the version-scheme change. Parsing the display string means parsing "16.10 (Debian ...)" and,
    on an old server, a two-part major - and getting 9 out of "9.6" is a wrong answer that looks
    like a right one.
    """
    conn = FakeConn(server_version_num=160010)
    assert backup.server_major(conn) == 16
    assert any("server_version_num" in s for s in conn.statements), (
        f"server_major did not query server_version_num: {conn.statements}"
    )
    assert backup.server_major(FakeConn(server_version_num=90600)) == 9


def test_backup_refuses_an_unparseable_client_version():
    """Refuse rather than guess. An unparsed version compared against the server cannot fail."""
    with pytest.raises(backup.BackupError, match="could not parse"):
        backup.client_major(run=version_runner("pg_dump: something else entirely"))

    with pytest.raises(backup.BackupError, match="could not run"):
        backup.client_major(run=version_runner("", returncode=127))


def make_unwritable(path: Path) -> None:
    """chmod a directory shut, or SKIP if this process can write to it anyway.

    ROOT IGNORES MODE BITS. Measured: this file's two unwritability tests FAILED when the suite was
    run inside a container as uid 0, because `chmod 0o500` restricts nobody with CAP_DAC_OVERRIDE
    and the probe write succeeded. That is a false red - the code is correct and the test's own
    precondition was not met - and a false red on a correct system trains its own removal.

    It is also the sharpest possible illustration of why `assert_staging_writable` writes a file
    instead of calling `os.access`: whether a write succeeds is a property of the process, the
    mount and the filesystem together, and no permission-bit inspection knows about any of them.

    The production container runs as uid 10001, so the guard is live where it matters.
    """
    path.chmod(0o500)
    probe = path / ".root-check"
    try:
        probe.write_bytes(b"")
    except OSError:
        return
    probe.unlink(missing_ok=True)
    path.chmod(0o700)
    pytest.skip(
        "this process can write to a 0500 directory, so it is running as root (or on a "
        "filesystem that ignores mode bits) and cannot construct the unwritable case. The "
        "scheduler container runs as uid 10001, where the guard is live."
    )


def test_backup_asserts_staging_writable_before_dumping(tmp_path, monkeypatch):
    """The probe WRITES a file. `os.access` answers a different question.

    Docker creates a missing bind-mount source as root:root and the container runs as uid 10001,
    so a provisioning step nobody ran becomes a directory that exists, resolves, and cannot be
    written to. Without this the first thing to discover that is pg_dump, after the counting
    transaction has exported a snapshot.

    `os.access(path, os.W_OK)` consults the real uid against the mode bits and knows nothing about
    a read-only mount, a full filesystem, or an ACL - so it says yes in cases where the write
    fails. The check that crosses the boundary where the failure lives is the write itself.
    """
    staging = tmp_path / "backups"
    staging.mkdir()

    backup.assert_staging_writable(staging)  # writable: no raise
    assert list(staging.iterdir()) == [], "the probe file was left behind"

    make_unwritable(staging)
    try:
        with pytest.raises(backup.BackupError) as excinfo:
            backup.assert_staging_writable(staging)
    finally:
        staging.chmod(0o700)

    message = str(excinfo.value)
    assert "not writable" in message
    assert "install -d -o 10001" in message, (
        f"the refusal does not name the provisioning command that fixes it: {message}"
    )


@contextlib.contextmanager
def _fake_connection(conn):
    yield conn


def _drive_job(monkeypatch, staging, *, run, conn=None):
    """Drive the REAL job entrypoint with the database faked out; return whatever it raised.

    THE DATABASE IS FAKED AND THE JOB IS NOT. Both tests below are about what the job does BEFORE
    the dump, and an earlier version of them let the job reach a real `db.connection` - so
    deleting the guard under test failed on `failed to resolve host 'h'` rather than on the
    assertion. Red for the wrong reason is not a confirmed guard (CLAUDE.md § 0).

    IT DOES NOT REQUIRE A PARTICULAR EXCEPTION, and that is deliberate. With the guard present the
    job raises BackupError early; with it removed the job proceeds, invokes pg_dump against a fake
    runner, and then fails on something else entirely - a missing archive file. Insisting on
    BackupError here would make the mutation red for THAT reason instead of for the ordering the
    test is named for. The observation is the runner's call list; the exception is returned so the
    caller can assert on its message when it has one.
    """
    monkeypatch.setattr(
        backup.db, "connection", lambda *a, **k: _fake_connection(conn or FakeConn())
    )
    try:
        backup.backup_nightly_job.undecorated(
            "postgresql://u:p@h:5432/d", bucket="b", now=NOW,
            staging_dir=staging, s3=object(), run=run,
        )
    except BaseException as exc:  # noqa: BLE001 - the caller decides what it means
        return exc
    return None


def test_backup_refuses_before_pg_dump_when_staging_is_unwritable(tmp_path, monkeypatch):
    """ORDER, not merely presence: the job stops before any subprocess is invoked at all.

    A writability check placed after the snapshot export would still fail the job, and would leave
    an exported snapshot and a REPEATABLE READ transaction open while it did. Asserting the
    ordering means asserting the runner was never called - and the runner's first call in a
    healthy run is `pg_dump --version`, so an empty call list is a strong statement about how
    early this happens.
    """
    staging = tmp_path / "backups"
    staging.mkdir()
    make_unwritable(staging)

    run = version_runner("pg_dump (PostgreSQL) 16.10")
    try:
        raised = _drive_job(monkeypatch, staging, run=run)
    finally:
        staging.chmod(0o700)

    assert run.calls == [], (
        f"the job invoked a subprocess before the writability check: {run.calls}"
    )
    assert isinstance(raised, backup.BackupError) and "not writable" in str(raised), (
        f"expected a BackupError naming the unwritable directory, got {raised!r}"
    )


def test_backup_job_checks_the_version_before_dumping(tmp_path, monkeypatch):
    """THE JOB ASKS, and that is a different claim from "the function refuses".

    Measured: deleting the job's call to `assert_client_server_majors_agree` left the test that
    exercises the function directly entirely green. The function was proven to refuse while
    nothing proved anything ever asked it - CLAUDE.md § 2's theme 2, in the guard added to close
    a theme-1 gap.

    So this drives the real entrypoint with a client whose major disagrees with the server's, and
    asserts BOTH that the job fails AND that pg_dump was never invoked. `--version` is expected in
    the call list; a dump is not.
    """
    staging = tmp_path / "backups"
    staging.mkdir()

    run = version_runner("pg_dump (PostgreSQL) 15.14 (Debian 15.14-1.pgdg120+1)")
    raised = _drive_job(
        monkeypatch, staging, run=run, conn=FakeConn(server_version_num=160010)
    )

    dumps = [argv for argv in run.calls if "--file" in argv]
    assert dumps == [], (
        f"the job dumped despite the major mismatch: {dumps}. A version check that runs after the "
        f"dump is a check on an archive that has already been written."
    )
    assert any(argv[-1] == "--version" for argv in run.calls), (
        f"the job never asked the client for its version at all: {run.calls}"
    )
    assert isinstance(raised, backup.BackupError), f"expected a BackupError, got {raised!r}"
    assert "major 15" in str(raised) and "major 16" in str(raised), str(raised)


def test_backup_refuses_an_undigested_compose_image(tmp_path):
    """A floating tag on the server image is a hard failure, not a shrug.

    `timescaledb_image()` NO LONGER HAS A CALLER IN THIS MODULE as of Phase 12 - the dump and the
    verification both invoke the in-image client. It survives this commit only because
    app/orchestration/restore_test.py still calls it, and it goes when that does.
    """
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


def test_backup_still_uses_pgpassfile_not_env():
    """PGPASSFILE, never PGPASSWORD, and removing the container did not change the reasoning.

    A process environment is readable - /proc/<pid>/environ to the same uid - so the password
    itself never goes there. PGPASSFILE is a PATH, and a path to a 0600 file gives a reader
    nothing. That distinction is the whole reason one spelling is permitted and the other is not.

    AN INHERITED PGPASSWORD IS STRIPPED, which is the half that is easy to miss. libpq prefers
    PGPASSWORD over PGPASSFILE, so leaving one in the environment means the file this job took
    care to write at 0600 is silently not the thing being used - and the dump would still succeed,
    which is what makes it invisible.
    """
    argv = _dump_argv()
    joined = " ".join(argv)
    assert "PGPASSWORD" not in joined, f"the password reaches the argv: {argv}"
    assert "--password" not in argv and "-W" not in argv

    child = backup.pgpass_environment(
        Path("/mnt/data/backups/.pgpass-backup"),
        environ={"PATH": "/usr/bin", "PGPASSWORD": "leftover-from-somewhere"},
    )
    assert child["PGPASSFILE"] == "/mnt/data/backups/.pgpass-backup"
    assert "PGPASSWORD" not in child, (
        f"an inherited PGPASSWORD survived into the child environment: {sorted(child)}. libpq "
        f"prefers it over PGPASSFILE, so the 0600 file would be silently unused."
    )
    assert child["PATH"] == "/usr/bin", "the rest of the environment was discarded"


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


def test_backup_still_passes_snapshot_id():
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

    backup.verify_archive(tmp_path / "x.dump", run=run)

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
        backup.verify_archive(tmp_path / "x.dump", run=run)

    assert "errors ignored on restore" in str(excinfo.value), (
        "the observed stderr is not reported, so the operator cannot see what pg_restore said"
    )

    # And the clean case does not raise, or the assertion above holds for the wrong reason.
    backup.verify_archive(tmp_path / "x.dump", run=lambda *a, **k: Completed(0, "", ""))


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
