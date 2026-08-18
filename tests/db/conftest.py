"""Integration plumbing for the chunk-interval tests.

`RESET_SCHEMA_SQL` IS DUPLICATED HERE ON PURPOSE, as it already is between
`tests/ingest/conftest.py` and `tests/orchestration/conftest.py`. Those two carry the note
explaining why: the two conftest modules collided once when both suites ran in one pytest
invocation, and a shared helper would be a third import path into the same collision. This is the
third copy and it is the same trade, made deliberately - a `conftest` is not an importable module,
and making it one to save forty lines is how the collision comes back.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db
from app.orchestration import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

CONSOLIDATION = "0027_gauge_readings_iv_chunk_interval.sql"

# A site 0004 seeds, so the foreign key to `gauges` is satisfiable without inventing one.
SITE = "07032000"
PARAM = "00060"

# Six years, one reading per week. THE SHAPE IS THE POINT, NOT THE VOLUME: at a 7-day chunk
# interval one reading per week lands in a chunk of its own, so ~313 rows make ~313 chunks - the
# same pathology as production's 986 chunks for 258,739 rows, at a size a test suite can afford.
# Six years rather than one, because at 365 days the consolidated count must be comfortably under
# 50 AND the pre-consolidation count comfortably over it, or the assertion cannot tell them apart.
SEED_YEARS = 6
SEED_START = datetime(2015, 1, 1, tzinfo=timezone.utc)
SEED_STEP = timedelta(days=7)
SEED_ROWS = SEED_YEARS * 365 // 7


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "DATABASE_URL is not set: the integration tier needs a real Postgres with "
            "TimescaleDB. These tests are SKIPPED, not passed - nothing below has been verified."
        )
    return url


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


@dataclass
class Consolidated:
    """The connection, plus what the fixture measured on the way through.

    A dataclass rather than attributes hung on the psycopg connection: the pre-consolidation chunk
    count is evidence the fixture is the only thing that can produce, and a test asserting the
    count DROPPED needs both ends of the comparison.
    """

    conn: object
    chunks_before: int
    seeded: list

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params)


def _seed_rows(conn):
    """One reading per week for six years, into whatever gauge_readings_iv currently is."""
    rows = [
        (SITE, SEED_START + SEED_STEP * n, PARAM, 100000.0 + n, ["P"])
        for n in range(SEED_ROWS)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO gauge_readings_iv (usgs_site_id, ts, param_code, value, qualifiers)"
            " VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    return rows


@pytest.fixture
def consolidated_db(database_url, tmp_path):
    """A database taken to 0026, SEEDED, and only then given 0027.

    THE STAGING IS WHAT MAKES THIS TEST MEAN ANYTHING. Running every migration in one pass applies
    0027 to an empty table: the copy moves zero rows, the equality check compares zero to zero, the
    view is repointed at a table nobody reads, and the whole migration passes without exercising
    one line of what it is for. That is the vacuous-precondition shape CLAUDE.md § 2 theme 2
    describes, and it would have been the easy fixture to write.

    So the migrations are staged in two passes with rows in between, which is also the only
    arrangement that reproduces the real situation: a hypertable that already has years of chunks
    at the old interval.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    staged = tmp_path / "migrations"
    staged.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name != CONSOLIDATION:
            shutil.copy2(path, staged / path.name)

    assert not (staged / CONSOLIDATION).exists()
    migrate.run(staged, url=database_url)

    with db.connection(database_url) as conn:
        seeded = _seed_rows(conn)

    with db.connection(database_url) as conn:
        before = conn.execute(
            "SELECT count(*) FROM timescaledb_information.chunks"
            " WHERE hypertable_name = 'gauge_readings_iv'"
        ).fetchone()[0]
    assert before > 50, (
        f"the fixture produced only {before} chunks before consolidation, so an assertion that "
        f"the count drops below 50 would pass without 0027 doing anything"
    )

    shutil.copy2(MIGRATIONS_DIR / CONSOLIDATION, staged / CONSOLIDATION)
    migrate.run(staged, url=database_url)

    with db.connection(database_url) as conn:
        yield Consolidated(conn=conn, chunks_before=before, seeded=seeded)


@pytest.fixture
def pre_0027_db(database_url, tmp_path):
    """A database at 0026, seeded, with 0027 STAGED BUT NOT APPLIED.

    For the tests that need to observe 0027 refusing. `consolidated_db` has already applied it, so
    the refusal paths are unreachable from there.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    staged = tmp_path / "migrations"
    staged.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name != CONSOLIDATION:
            shutil.copy2(path, staged / path.name)
    migrate.run(staged, url=database_url)

    with db.connection(database_url) as conn:
        _seed_rows(conn)

    shutil.copy2(MIGRATIONS_DIR / CONSOLIDATION, staged / CONSOLIDATION)

    with db.connection(database_url) as conn:
        yield Staged(conn=conn, migrations_dir=staged, url=database_url)


@dataclass
class Staged:
    conn: object
    migrations_dir: Path
    url: str

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params)

    def apply_0027(self):
        return migrate.run(self.migrations_dir, url=self.url)
