"""Fixtures for tests/orchestration/ — see app/orchestration/ and CLAUDE.md § 12.

Two tiers, and the split is not cosmetic:

  Unit tier — no database, no network, no credentials. Ordering, checksum, marker parsing, repo
  shape, and scheduler configuration. Runs anywhere, including in a checkout with nothing
  installed but the requirements.

  Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, and SKIPS WITH A
  STATED REASON when it is absent. It never silently passes: CLAUDE.md § 2's theme 2 is a check
  that verifies the exact thing responsible for a failure and reports it correct, and a test that
  quietly turns into a no-op when a fixture is missing is the purest form of that.

Every integration test gets its own schema, dropped and recreated per test, so no test can pass
because of a row another test left behind.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration import migrate  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture(scope="session")
def database_url():
    """The integration tier's entry condition.

    Skips loudly, naming the variable and how to satisfy it. A skip that says "no database" and a
    pass are very different lines in a test report, and this project has been burned by the
    difference.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "DATABASE_URL is not set: the integration tier needs a real Postgres. These tests are "
            "SKIPPED, not passed - nothing below has been verified. Start a database and export "
            "DATABASE_URL to run them."
        )
    return url


# ---------------------------------------------------------------------------------------------
# The postgres client, and why its major has to match the server under test
# ---------------------------------------------------------------------------------------------
#
# From Phase 12 the dump and the restore invoke `pg_dump`/`pg_restore` DIRECTLY rather than
# spawning a container off the server's pinned digest - the scheduler container has no Docker
# socket, because mounting it is root-equivalent on the host. On the instance the client is pinned
# to the server's major and `verify/preflight.py` gates the two; on a developer machine the client
# is whatever is on PATH.
#
# MEASURED 2026-08-18, and it is the clearest possible demonstration of why the pin exists: a
# pg18 `pg_restore` against the pg16 server emits
#
#     SET transaction_timeout = 0;
#     ERROR:  unrecognized configuration parameter "transaction_timeout"
#
# because `transaction_timeout` arrived in PostgreSQL 17. The restore fails outright. That is the
# loud end of "a client at a different major produces a subtly wrong archive"; the quiet end is
# what the runtime check in backup.py refuses.
#
# So the tests that drive a real dump or a real restore SKIP, with the observed majors in the
# reason, rather than failing. The alternative - stubbing the version check out - would make the
# only tests that exercise the whole path stop exercising the guard that path depends on.


def local_client_major():
    """The major of the pg_dump on PATH, or None if there is not one."""
    if shutil.which("pg_dump") is None:
        return None
    try:
        completed = subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"\(PostgreSQL\)\s+(\d+)", completed.stdout or "")
    return int(match.group(1)) if match else None


@pytest.fixture
def matching_pg_client(database_url):
    """Skip unless the local client's major equals the server's. Returns the major.

    A RUN-TIME skip rather than a `skipif` decorator, because the server's major is a fact about
    DATABASE_URL and does not exist at collection time. A SKIP with this reason is visible in the
    report; it does not read as a pass (CLAUDE.md § 13).
    """
    client = local_client_major()
    if client is None:
        pytest.skip("no pg_dump on PATH: the dump and restore are invoked directly, not in a container")

    with db.connection(database_url) as conn:
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000

    if client != server:
        pytest.skip(
            f"local pg_dump is major {client} and the server under test is major {server}. "
            f"A pg17+ client emits `SET transaction_timeout = 0`, which a pg16 server rejects "
            f"outright - so this is the mismatch the version pin exists to prevent, not a "
            f"defect. It runs on the instance, where the scheduler image's client is pinned to "
            f"the server's major."
        )
    return client


# Drops everything this project's migrations create, and nothing an extension owns.
#
# The obvious version of this is `DROP SCHEMA public CASCADE; CREATE SCHEMA public`, and it is
# what this fixture did first. It is also intermittently deadlock-prone here: CASCADE tears the
# timescaledb extension down with the schema, and the extension's background workers hold catalog
# locks of their own, so the drop and a worker can each end up waiting on the other. It failed
# roughly one run in four.
#
# Filtering on pg_depend.deptype = 'e' is what makes this safe: every object an extension owns
# carries an 'e' dependency on it, so the loops below skip the ~1,600 functions timescaledb
# installs into public and touch only what the migrations under test created. The extension is
# then created once and stays, which is also the more realistic starting state — a database
# restored from a dump already carries it, which is exactly why 0001 says IF NOT EXISTS.
RESET_SCHEMA_SQL = """
-- ONE `DROP TABLE` STATEMENT FOR EVERY TABLE, NOT ONE STATEMENT PER TABLE.
--
-- The per-table loop this replaces DEADLOCKED roughly one run in four once Phase 5 added
-- `gauge_daily` and `features`, both of which carry foreign keys to `gauges`. Two things combined:
-- the catalog scan has no ORDER BY, so the drop order varied run to run, and each CASCADE takes
-- locks on that table's dependents. Fifteen separate statements against a widening dependency
-- graph, racing TimescaleDB's background workers on the two hypertables, is fifteen windows for a
-- lock cycle.
--
-- Naming every table in a single DROP closes the window: Postgres takes the whole lock set in one
-- operation instead of accumulating it across statements. The failure was a FLAKY TEST FIXTURE
-- rather than a defect in anything under app/ - but a suite that fails one run in four is a suite
-- whose failures stop being read, which is the same ending as a muted alert.
DO $$
DECLARE
    idents text;
BEGIN
    SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ')
      INTO idents
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind IN ('r', 'p')
       AND NOT EXISTS (
             SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e');

    -- NULL rather than empty when nothing matched: string_agg over no rows returns NULL, and
    -- `DROP TABLE IF EXISTS  CASCADE` is a syntax error rather than a no-op.
    IF idents IS NOT NULL THEN
        EXECUTE format('DROP TABLE IF EXISTS %s CASCADE', idents);
    END IF;

    SELECT string_agg(
               format('%I.%I(%s)', n.nspname, p.proname,
                      pg_get_function_identity_arguments(p.oid)), ', ')
      INTO idents
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND NOT EXISTS (
             SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e');

    IF idents IS NOT NULL THEN
        EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', idents);
    END IF;
END $$;
"""


@pytest.fixture
def clean_db(database_url):
    """A connection to a database holding none of this project's own objects.

    Dropping tables is normally on this project's never-run list (CLAUDE.md § 1), and it happens
    here only because DATABASE_URL in a test run points at a throwaway database. The guard against
    it pointing anywhere else is that this is the only place in the repo that drops anything, and
    it is under tests/.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def migrated_db(clean_db, database_url):
    """clean_db with the repo's real migrations applied.

    The real ones, not a fixture copy: tests of the @job decorator and the heartbeat then run
    against the actual CHECK constraint and the actual append-only trigger, which is the whole
    argument for constraining status in the database rather than in Python.
    """
    migrate.run(MIGRATIONS_DIR, url=database_url)
    return clean_db


@pytest.fixture
def migrations_dir(tmp_path):
    """Return a builder: migrations_dir({"0001_a.sql": "CREATE TABLE ...;"}) -> Path.

    Each call gets its own directory, so a test can build more than one independent set — needed
    by the tamper test, which applies one set and then re-runs against a modified one.
    """
    counter = iter(range(1000))

    def _build(files: dict) -> Path:
        directory = tmp_path / f"migrations{next(counter)}"
        directory.mkdir()
        for name, sql in files.items():
            (directory / name).write_text(sql, encoding="utf-8")
        return directory

    return _build


@pytest.fixture
def job_runs(migrated_db, database_url):
    """Helpers for reading and seeding job_runs from an independent connection.

    Independent on purpose: the test that proves the @job decorator commits its `running` row
    before the work starts is only meaningful if the observer is a different session from both the
    decorator's bookkeeping and the wrapped work.
    """

    class JobRuns:
        url = database_url

        @staticmethod
        def rows(job_name=None):
            sql = "SELECT run_id, job_name, started_at, finished_at, status, rows_written, error_message FROM job_runs"
            params = ()
            if job_name is not None:
                sql += " WHERE job_name = %s"
                params = (job_name,)
            sql += " ORDER BY run_id"
            with db.connection(database_url) as conn:
                cur = conn.execute(sql, params)
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]

        @staticmethod
        def seed(job_name, status, started_at, finished_at=None, rows_written=None):
            with db.connection(database_url) as conn:
                conn.execute(
                    "INSERT INTO job_runs (job_name, status, started_at, finished_at, rows_written)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (job_name, status, started_at, finished_at, rows_written),
                )
                conn.commit()

    return JobRuns
