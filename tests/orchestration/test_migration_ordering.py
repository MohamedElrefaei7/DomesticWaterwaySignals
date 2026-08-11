"""Unit tier — migration ordering, marker parsing, checksums, and repo shape. No database.

Covers CLAUDE.md § 12 decisions 4 (marker on the first line only), 5 (out-of-order is a hard
failure), 6 (no re-runnable schema.sql), and 7 (migrations never run on container start), plus the
sort order that decision 3's per-file transactions are applied in.
"""

import re
from pathlib import Path

import pytest

from app.orchestration import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_versions_sort_numerically_not_lexically(migrations_dir):
    """0002 before 0010.

    Sorted as strings, '0010' < '0002' is false but '0010' < '0002' as zero-padded text happens to
    work — until the padding runs out. Build the case where lexical and numeric genuinely
    disagree: an unpadded set where '10' sorts before '2'.
    """
    directory = migrations_dir(
        {
            "1_first.sql": "SELECT 1;",
            "2_second.sql": "SELECT 1;",
            "10_tenth.sql": "SELECT 1;",
            "0003_third.sql": "SELECT 1;",
        }
    )

    migrations = migrate.discover(directory)

    assert len(migrations) == 4
    assert [m.version for m in migrations] == [1, 2, 3, 10]
    assert [m.filename for m in migrations] == [
        "1_first.sql",
        "2_second.sql",
        "0003_third.sql",
        "10_tenth.sql",
    ]

    # The specific inversion a lexical sort produces, stated directly so the failure message says
    # what went wrong rather than just showing two lists.
    versions = [m.version for m in migrations]
    assert versions.index(2) < versions.index(10), "10 was applied before 2 - lexical sort"


def test_no_transaction_marker_only_counts_on_the_first_line(migrations_dir):
    """The same text on line 2 does not trigger it. Decision 4.

    A marker matched anywhere in the file means a comment inside a DDL body - or prose explaining
    why some *other* migration needed the marker - silently changes how this file is applied.
    """
    directory = migrations_dir(
        {
            "0001_first_line.sql": "-- migrate:no-transaction\nCREATE INDEX CONCURRENTLY i ON t (c);\n",
            "0002_second_line.sql": "-- ordinary comment\n-- migrate:no-transaction\nSELECT 1;\n",
            "0003_buried.sql": "CREATE TABLE t (\n  c int -- migrate:no-transaction\n);\n",
            "0004_leading_blank.sql": "\n-- migrate:no-transaction\nSELECT 1;\n",
        }
    )

    by_name = {m.filename: m for m in migrate.discover(directory)}
    assert len(by_name) == 4

    assert by_name["0001_first_line.sql"].no_transaction is True
    assert by_name["0002_second_line.sql"].no_transaction is False
    assert by_name["0003_buried.sql"].no_transaction is False
    assert by_name["0004_leading_blank.sql"].no_transaction is False


def test_checksum_differs_when_a_byte_changes():
    """Not normalized, not whitespace-insensitive.

    The question is "is this the file that was applied?", so a trailing newline an editor added is
    a change. A checksum that forgives whitespace forgives exactly the edit most likely to be made
    accidentally to an already-applied file.
    """
    original = "CREATE TABLE t (c int);\n"

    assert migrate.checksum_of(original) == migrate.checksum_of(original)
    assert migrate.checksum_of(original) != migrate.checksum_of(original + "\n")
    assert migrate.checksum_of(original) != migrate.checksum_of("CREATE TABLE t (c INT);\n")
    assert migrate.checksum_of(original) != migrate.checksum_of(original.replace(" ", "  "))

    assert re.fullmatch(r"[0-9a-f]{64}", migrate.checksum_of(original))


def test_pending_version_below_highest_applied_raises(migrations_dir):
    """A merged branch's 0005 arriving after 0007 is applied. Decision 5.

    Named in the message: both the pending version and the highest applied one. An error that says
    only "out of order" leaves the human to work out which file to renumber, at the exact moment
    they are least inclined to read carefully.
    """
    migrations = migrate.discover(
        migrations_dir(
            {
                "0005_from_a_branch.sql": "SELECT 1;",
                "0007_already_shipped.sql": "SELECT 1;",
            }
        )
    )
    applied = {7: ("0007_already_shipped.sql", "abc123")}

    with pytest.raises(migrate.MigrationError) as excinfo:
        migrate.check_ordering(migrations, applied)

    message = str(excinfo.value)
    assert "5" in message and "0005_from_a_branch.sql" in message
    assert "7" in message and "0007_already_shipped.sql" in message

    # The in-order case must not raise, or the check above passes for the wrong reason.
    migrate.check_ordering(migrations, {5: ("0005_from_a_branch.sql", "abc")})


def test_no_schema_sql_exists_in_the_repo():
    """CLAUDE.md § 3: there is no re-runnable schema.sql.

    A fresh database is created by restoring a verified dump, not by executing a monolith. The
    prior project came within one command of unrecoverable loss twice. This guard lives in the
    test suite rather than in a paragraph because a paragraph cannot fail.
    """
    offenders = [
        path.relative_to(REPO_ROOT)
        for path in REPO_ROOT.rglob("schema.sql")
        if ".git" not in path.parts and ".venv" not in path.parts
    ]

    assert offenders == [], (
        f"found {offenders}. There is no re-runnable schema.sql in this project: a fresh database "
        f"is restored from a verified dump, and schema changes are numbered migration files "
        f"(CLAUDE.md § 3)."
    )


def test_compose_file_does_not_invoke_the_migration_runner():
    """CLAUDE.md § 3: migrations never run on container start. Decision 7.

    With `restart: unless-stopped`, a container that migrates on start turns a crash loop into a
    schema-change loop.

    Comments are stripped before the check so docker-compose.yml can explain this rule in its own
    header without tripping the test that enforces it.
    """
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    executable_lines = [
        line for line in compose_text.splitlines() if not line.strip().startswith("#")
    ]
    executable = "\n".join(executable_lines)

    assert "migrate" not in executable, (
        "docker-compose.yml names the migration runner outside a comment - the runner is a CLI a "
        "human invokes, never an entrypoint, healthcheck, or command (CLAUDE.md § 3)."
    )
    assert "migrations" not in executable

    for key in ("command:", "entrypoint:"):
        assert key not in executable, (
            f"docker-compose.yml sets `{key}` on a service. The database service runs the image's "
            f"own entrypoint; overriding it is how migration-on-start gets reintroduced."
        )


def test_db_module_contains_no_credential_literal():
    """app/db.py reads DATABASE_URL and constructs nothing.

    CLAUDE.md § 1 forbids the agent from handling secrets; the code-level counterpart is that no
    module can build a working connection without being told a credential. A default host or a
    fallback password would mean a misconfigured environment connects somewhere unintended and
    reports success.
    """
    source = (REPO_ROOT / "app" / "db.py").read_text(encoding="utf-8")

    assert "postgresql://" not in source.replace("postgresql://` URL", "")
    assert "password=" not in source
    assert "POSTGRES_PASSWORD" not in source
    assert "localhost" not in source
    assert "5432" not in source
