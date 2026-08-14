"""Integration tier — the migration runner against a real Postgres.

Covers CLAUDE.md § 12 decisions 1 (the runner bootstraps schema_migrations), 2 (checksums of all
applied files verified before anything pending is applied), 3 (one transaction per file with the
record inside it), and 4 (the no-transaction path).

These tests need a real database because the thing being tested IS the transaction boundary. A
fake connection that records calls would let every one of them pass while the real runner left a
migration applied and unrecorded — CLAUDE.md § 2's theme 2 exactly.
"""

import hashlib
import re

import pytest

from app import db
from app.orchestration import migrate

pytestmark = pytest.mark.integration


def _table_exists(url, name):
    with db.connection(url) as conn:
        return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",)).fetchone()[0]


def _recorded(url):
    with db.connection(url) as conn:
        return {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        }


def test_bootstrap_creates_schema_migrations(clean_db, database_url, migrations_dir):
    """Decision 1: the table that records applied migrations cannot itself be an applied migration.

    It is created idempotently by the runner as its first act, outside the numbered sequence -
    there would be nowhere to record it otherwise.
    """
    assert not _table_exists(database_url, "schema_migrations")

    migrate.run(migrations_dir({}), url=database_url)

    assert _table_exists(database_url, "schema_migrations")

    with db.connection(database_url) as conn:
        columns = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'schema_migrations'"
            ).fetchall()
        }

    assert len(columns) == 4
    assert set(columns) == {"version", "filename", "checksum", "applied_at"}
    assert columns["checksum"] == "text"

    # Idempotent: a second bootstrap against an existing table is a no-op, not an error.
    migrate.run(migrations_dir({}), url=database_url)


def test_applying_records_version_filename_and_checksum(clean_db, database_url, migrations_dir):
    """All three, not just the filename.

    Filename-only tracking silently permits editing an applied migration; the checksum column is
    the only thing that makes the tamper check in the next test possible at all (CLAUDE.md § 3).
    """
    directory = migrations_dir(
        {
            "0001_alpha.sql": "CREATE TABLE alpha (id int);\n",
            "0002_beta.sql": "CREATE TABLE beta (id int);\n",
        }
    )

    applied = migrate.run(directory, url=database_url)
    assert len(applied) == 2

    recorded = _recorded(database_url)
    assert len(recorded) == 2
    assert recorded[1][0] == "0001_alpha.sql"
    assert recorded[2][0] == "0002_beta.sql"

    # Computed here with hashlib directly rather than through migrate.checksum_of. Going through
    # the module's own helper makes this assertion self-referential: a runner that recorded a
    # constant would satisfy `recorded == checksum_of(file)` because both sides would be that same
    # constant. This caught exactly that during mutation confirmation.
    for version, filename in ((1, "0001_alpha.sql"), (2, "0002_beta.sql")):
        expected = hashlib.sha256(
            (directory / filename).read_bytes()
        ).hexdigest()
        assert recorded[version][1] == expected, (
            f"{filename}: recorded checksum is not this file's SHA-256"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", recorded[version][1]), (
            f"{filename}: recorded checksum is not a SHA-256 digest at all"
        )

    # Two different files must not record the same checksum - the property the tamper check needs.
    assert recorded[1][1] != recorded[2][1]

    assert _table_exists(database_url, "alpha")
    assert _table_exists(database_url, "beta")


def test_tampered_applied_file_aborts_before_applying_any_pending(
    clean_db, database_url, migrations_dir
):
    """Decision 2, and the half of it that is easy to miss.

    Apply a set, edit an ALREADY-APPLIED file on disk, add a pending one, re-run. Assert both that
    it raises AND that the pending migration did not apply. Asserting only the raise would pass
    against a runner that aborts after doing the damage, which is the outcome the decision exists
    to prevent: once the database and the repo disagree about the schema, applying more changes on
    top of the disagreement is how you get a schema no environment can reproduce.
    """
    directory = migrations_dir({"0001_alpha.sql": "CREATE TABLE alpha (id int);\n"})
    migrate.run(directory, url=database_url)
    original_checksum = _recorded(database_url)[1][1]

    # The smallest possible edit to an applied file: one appended blank line.
    (directory / "0001_alpha.sql").write_text(
        "CREATE TABLE alpha (id int);\n\n", encoding="utf-8"
    )
    (directory / "0002_pending.sql").write_text(
        "CREATE TABLE pending (id int);\n", encoding="utf-8"
    )

    with pytest.raises(migrate.MigrationError) as excinfo:
        migrate.run(directory, url=database_url)

    message = str(excinfo.value)
    assert "0001_alpha.sql" in message
    assert original_checksum in message, "the recorded checksum is not named in the error"
    new_checksum = migrate.checksum_of((directory / "0001_alpha.sql").read_text(encoding="utf-8"))
    assert new_checksum in message, "the file's current checksum is not named in the error"

    # The harm, not just the alarm.
    assert not _table_exists(database_url, "pending"), (
        "the pending migration was applied despite the tamper check raising - it aborted after "
        "doing the damage rather than before"
    )
    assert 2 not in _recorded(database_url)


def test_failed_migration_leaves_no_row_and_no_partial_schema(
    clean_db, database_url, migrations_dir
):
    """Decision 3: one transaction per file, with the schema_migrations INSERT inside it.

    The file below has valid DDL followed by a syntax error. Afterwards the first statement's
    table must not exist and no row must be recorded - if either survives, the file was not
    applied atomically and the next run starts from a state nobody described.
    """
    directory = migrations_dir(
        {
            "0001_alpha.sql": "CREATE TABLE alpha (id int);\n",
            "0002_broken.sql": (
                "CREATE TABLE half_applied (id int);\n"
                "CREATE TABLE oops (id int) THIS IS NOT SQL;\n"
            ),
        }
    )

    with pytest.raises(Exception) as excinfo:
        migrate.run(directory, url=database_url)
    assert not isinstance(excinfo.value, migrate.MigrationError), "expected a database error"

    assert not _table_exists(database_url, "half_applied"), (
        "the first statement of a failed migration survived - the file was not applied in a "
        "single transaction"
    )
    assert 2 not in _recorded(database_url)

    # The migration BEFORE the failure keeps its work: it was a separate, already-committed
    # transaction. Wrapping all pending migrations in one transaction would roll this back too,
    # and the next run would reapply a file that had already succeeded.
    assert _table_exists(database_url, "alpha")
    assert 1 in _recorded(database_url)


def test_no_transaction_migration_applies_and_records(clean_db, database_url, migrations_dir):
    """Decision 4, exercised through CREATE INDEX CONCURRENTLY - which is the only reason it exists.

    A statement that genuinely cannot run inside a transaction block: if the runner applied this
    file transactionally, Postgres itself would reject it.
    """
    directory = migrations_dir(
        {
            "0001_table.sql": "CREATE TABLE t (id int, name text);\n",
            "0002_index.sql": (
                "-- migrate:no-transaction\n"
                "CREATE INDEX CONCURRENTLY t_name_idx ON t (name);\n"
            ),
        }
    )

    applied = migrate.run(directory, url=database_url)

    assert len(applied) == 2
    assert applied[1].no_transaction is True

    with db.connection(database_url) as conn:
        indexes = [
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 't'"
            ).fetchall()
        ]
    assert "t_name_idx" in indexes

    recorded = _recorded(database_url)
    assert len(recorded) == 2
    assert recorded[2][0] == "0002_index.sql", (
        "the no-transaction migration applied but was not recorded - the next run would apply it "
        "a second time"
    )


def test_second_run_applies_nothing(clean_db, database_url):
    """The repo's real migrations, twice.

    Running the actual migrations/ directory rather than a fixture, so decision 4's marker is
    exercised by 0003_job_runs_success_index.sql - a real migration - and not only by a test.

    The counts come from the directory rather than from a literal. A hardcoded number here goes
    red on every commit that adds a migration, which trains whoever is adding one to update it
    without reading it - and the assertions that matter (contiguous versions from 1, the marker
    honoured on exactly the files that carry it, nothing pending afterwards) are the ones that
    would then get updated carelessly too.
    """
    on_disk = migrate.discover(migrate.MIGRATIONS_DIR)
    assert on_disk, "no migrations found on disk; every assertion below would be vacuous"

    first = migrate.run(migrate.MIGRATIONS_DIR, url=database_url)
    assert len(first) == len(on_disk)
    assert [m.version for m in first] == list(range(1, len(on_disk) + 1)), (
        "the applied versions are not a contiguous run from 1 - a migration is missing or "
        "misnumbered"
    )
    # The marker is honoured on exactly the files that carry it on line 1, and on no others.
    assert [m.no_transaction for m in first] == [m.no_transaction for m in on_disk]
    assert any(m.no_transaction for m in first), (
        "no migration in the repo uses -- migrate:no-transaction any more, so this test no "
        "longer exercises that path against a real database"
    )

    second = migrate.run(migrate.MIGRATIONS_DIR, url=database_url)
    assert second == []

    recorded = _recorded(database_url)
    assert len(recorded) == len(on_disk)
    assert _table_exists(database_url, "job_runs")
    assert _table_exists(database_url, "gauges")
    assert _table_exists(database_url, "gauge_readings")

    statuses = migrate.status(migrate.MIGRATIONS_DIR, url=database_url)
    assert len(statuses) == len(on_disk)
    assert all(is_applied for _, _, is_applied in statuses)
