"""Fixtures for tests/ingest/ — see app/ingest/ and CLAUDE.md § 14.

Two tiers, the same split tests/orchestration/ uses:

  Unit tier — no database, no network, no credentials. The client parses captured fixtures; the
  seed guards parse the migration file. Runs anywhere.

  Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, and SKIPS WITH A
  STATED REASON when it is absent. It never silently passes.

NO TEST IN THIS DIRECTORY MAKES A LIVE HTTP REQUEST. The USGS client takes its transport as an
injected callable, so the real parsing and verification code runs against fixtures captured from
the live service rather than against a mock of itself.

The fixture JSON is real response shape with two deliberate edits: the geoLocation coordinates
are zeroed (nothing parses them, and this repo does not carry unverified coordinates - see
migrations/0004_gauges.sql), and the value blocks are trimmed to a handful of readings.
"""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration import migrate  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture
def iv_payload():
    """Return a loader: iv_payload("ok") -> the parsed fixture, freshly copied each call.

    A fresh deepcopy per call because several tests mutate the payload to build a shape that has
    no fixture of its own - a present series with an empty `values` array, most importantly.
    Sharing one parsed dict across tests would let one test's edit decide another test's result.
    """

    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"iv_response_{name}.json"
        if not path.is_file():
            raise AssertionError(
                f"no fixture {path.name} in {FIXTURES_DIR}. Fixtures are captured from the live "
                f"service by a human; this suite never calls it."
            )
        return deepcopy(json.loads(path.read_text(encoding="utf-8")))

    return _load


@pytest.fixture
def dv_payload():
    """Return a loader: dv_payload("ok") -> the parsed daily fixture, freshly copied each call.

    Separate from `iv_payload` rather than parameterized by prefix, mirroring the split between
    the two clients: the daily fixture's timestamps are naive and carry a statistic code, and a
    single loader would be the first place the two shapes started sharing a path.
    """

    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"dv_response_{name}.json"
        if not path.is_file():
            raise AssertionError(
                f"no fixture {path.name} in {FIXTURES_DIR}. Fixtures are captured from the live "
                f"service by a human; this suite never calls it."
            )
        return deepcopy(json.loads(path.read_text(encoding="utf-8")))

    return _load


@pytest.fixture
def dv_raw_body():
    """Return a loader for a NON-JSON captured body: dv_raw_body("non_json") -> str.

    Deliberately returns text rather than a parsed object. The behaviour under test is what
    happens when the body cannot be parsed at all, so a fixture that had already been parsed
    would test nothing (CLAUDE.md § 15).
    """

    def _load(name: str) -> str:
        path = FIXTURES_DIR / f"dv_response_{name}.txt"
        if not path.is_file():
            raise AssertionError(f"no fixture {path.name} in {FIXTURES_DIR}")
        return path.read_text(encoding="utf-8")

    return _load


@pytest.fixture
def socrata_body():
    """Return a loader: socrata_body("page_1") -> the fixture's RAW TEXT.

    Text, not a parsed object, because the Socrata paging tests are about what the client does
    with a body: a page of rows, an empty page, and an error document are all valid JSON, and
    handing the tests something already parsed would skip the one decision under test (CLAUDE.md
    § 16).
    """

    def _load(name: str) -> str:
        path = FIXTURES_DIR / f"socrata_{name}.json"
        if not path.is_file():
            raise AssertionError(
                f"no fixture {path.name} in {FIXTURES_DIR}. Fixtures are captured from the live "
                f"service by a human; this suite never calls it."
            )
        return path.read_text(encoding="utf-8")

    return _load


@pytest.fixture
def recording_bodies():
    """Return a builder: recording_bodies([body, ...]) -> (fetch_callable, calls list).

    Separate from `recording_fetch` rather than sharing it: that one serializes payload OBJECTS,
    and a Socrata page IS a list, so passing one there would be read as a list of payloads. This
    takes bodies already in their wire form, which is also what lets a test supply a body that is
    not JSON at all.

    `calls` accumulates every URL requested. Several tests assert it is EMPTY - "no request was
    issued" is the actual claim in the unresolved-dataset case, and only the request log can make
    it.
    """

    def _build(bodies):
        remaining = list(bodies)
        calls: list[str] = []

        def fetch(url, timeout=None):
            calls.append(url)
            if not remaining:
                raise AssertionError(
                    f"the client made {len(calls)} request(s) but only {len(bodies)} body/bodies "
                    f"were provided. Extra request: {url}"
                )
            return remaining.pop(0)

        return fetch, calls

    return _build


@pytest.fixture
def recording_fetch():
    """Return a builder: recording_fetch(payload) -> (fetch_callable, calls list).

    The injected transport. `calls` accumulates every URL requested, which is how the backfill
    tests assert what window was actually asked for rather than what the code says it would ask
    for. Accepts a single payload, or a list of payloads returned in order.
    """

    def _build(payloads):
        if isinstance(payloads, dict):
            payloads = [payloads]
        remaining = list(payloads)
        calls: list[str] = []

        def fetch(url, timeout=None):
            calls.append(url)
            if not remaining:
                raise AssertionError(
                    f"the client made {len(calls)} request(s) but only "
                    f"{len(payloads)} payload(s) were provided. Extra request: {url}"
                )
            return json.dumps(remaining.pop(0))

        return fetch, calls

    return _build


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url():
    """The integration tier's entry condition.

    Skips loudly, naming the variable and how to satisfy it. A skip that says "no database" and a
    pass are very different lines in a test report.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "DATABASE_URL is not set: the integration tier needs a real Postgres with "
            "TimescaleDB. These tests are SKIPPED, not passed - nothing below has been verified. "
            "Start a database and export DATABASE_URL to run them."
        )
    return url


# Identical to tests/orchestration/conftest.py's reset, and deliberately duplicated rather than
# imported across suites: the two `conftest` modules already collided once when both suites ran
# in one pytest invocation (see CONTEXT.md, provisioning 1). A shared helper here would be a
# third import path into the same collision.
RESET_SCHEMA_SQL = """
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT format('%I.%I', n.nspname, c.relname) AS ident
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND NOT EXISTS (
                 SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %s CASCADE', obj.ident);
    END LOOP;

    FOR obj IN
        SELECT format('%I.%I(%s)', n.nspname, p.proname,
                      pg_get_function_identity_arguments(p.oid)) AS ident
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND NOT EXISTS (
                 SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', obj.ident);
    END LOOP;
END $$;
"""


@pytest.fixture
def migrated_db(database_url):
    """A database holding this project's real migrations and nothing else.

    The real migrations, not a fixture copy: these tests then run against the actual hypertable,
    the actual compression settings, and the actual foreign key to gauges - which is the whole
    argument for asserting them in the database rather than in Python.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    migrate.run(MIGRATIONS_DIR, url=database_url)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def readings_table(migrated_db, database_url):
    """Helpers for seeding and reading gauge_readings_iv from an independent connection."""

    class ReadingsTable:
        url = database_url

        @staticmethod
        def rows(site_id=None):
            sql = "SELECT usgs_site_id, ts, param_code, value, qualifiers FROM gauge_readings_iv"
            params = ()
            if site_id is not None:
                sql += " WHERE usgs_site_id = %s"
                params = (site_id,)
            sql += " ORDER BY usgs_site_id, ts, param_code"
            with db.connection(database_url) as conn:
                cur = conn.execute(sql, params)
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]

        @staticmethod
        def count():
            with db.connection(database_url) as conn:
                return conn.execute("SELECT count(*) FROM gauge_readings_iv").fetchone()[0]

    return ReadingsTable


@pytest.fixture
def job_runs(migrated_db, database_url):
    """Read job_runs from an independent connection. Same shape as the orchestration suite's."""

    class JobRuns:
        @staticmethod
        def rows(job_name=None):
            sql = (
                "SELECT run_id, job_name, started_at, finished_at, status, rows_written,"
                " error_message FROM job_runs"
            )
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
                    "INSERT INTO job_runs (job_name, status, started_at, finished_at,"
                    " rows_written) VALUES (%s, %s, %s, %s, %s)",
                    (job_name, status, started_at, finished_at, rows_written),
                )
                conn.commit()

    return JobRuns
