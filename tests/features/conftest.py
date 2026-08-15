"""Fixtures for tests/features/ — see app/features/ and CLAUDE.md § 17.

Two tiers, the same split the other suites use:

  Unit tier — no database, no network. The builders are PURE FUNCTIONS (CLAUDE.md § 17), so the
  arithmetic is tested against hand-computed expectations. That is the whole reason they take
  sequences rather than a connection: a test that asserted the climatology by re-reading what the
  build wrote would assert that the code computes what the code computes, and pass in both
  directions forever.

  Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, and SKIPS WITH A
  STATED REASON when it is absent. It never silently passes.

WHY THE ROLLUP'S OWN TESTS ARE IN THE INTEGRATION TIER AND THE BRIEF EXPECTED UNIT ONES.
The rollup is SQL, deliberately and singly - decision 2 requires it to read the `gauge_series` view
so the precedence rule has exactly one implementation, and test 4 pins that by reading the SQL. A
parallel Python implementation of the same aggregation, written so the min/mean/max arithmetic
could be unit-tested, WOULD BE THE SECOND IMPLEMENTATION THAT DIVERGES - the precise failure
decision 2 exists to prevent, reintroduced by the test suite. So the hand-built day is inserted
into a real database and the real SQL is run over it.

The third conftest in this repo, and duplicated rather than shared for the reason
tests/ingest/conftest.py states: the two existing `conftest` modules already collided once when
both suites ran in one pytest invocation, and a shared helper would be a third import path into the
same collision.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration import migrate  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "migrations"

# The four seeded gauges (migration 0004). Named here so a test can pick one without hardcoding a
# site id inline in six places.
ST_LOUIS = "07010000"
MEMPHIS = "07032000"
VICKSBURG = "07289000"
BATON_ROUGE = "07374000"

DISCHARGE = "00060"


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

    The real migrations, not a fixture copy: these tests then run against the actual view, the
    actual CHECK constraints, and the actual foreign key to gauges - which is the argument for
    asserting them in the database rather than in Python.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    migrate.run(MIGRATIONS_DIR, url=database_url)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def seed_readings(migrated_db):
    """Insert instantaneous and daily readings, so the rollup has something real to read.

    Returns two callables rather than writing anything itself: a test that seeds nothing is a test
    that proves the rollup handles an empty database, and several of them want exactly that.
    """

    class Seeder:
        @staticmethod
        def instantaneous(site_id, timestamps_and_values, param_code=DISCHARGE):
            for ts, value in timestamps_and_values:
                migrated_db.execute(
                    "INSERT INTO gauge_readings_iv (usgs_site_id, ts, param_code, value)"
                    " VALUES (%s, %s, %s, %s)",
                    (site_id, ts, param_code, value),
                )
            migrated_db.commit()

        @staticmethod
        def daily(site_id, dates_and_values, param_code=DISCHARGE, stat_cd="00003"):
            for day, value in dates_and_values:
                migrated_db.execute(
                    "INSERT INTO gauge_readings_daily"
                    " (usgs_site_id, date, param_code, stat_cd, value)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (site_id, day, param_code, stat_cd, value),
                )
            migrated_db.commit()

        @staticmethod
        def rates(weeks_and_rates, location="Cairo-Memphis", horizon="nearby"):
            for week, rate in weeks_and_rates:
                migrated_db.execute(
                    "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff)"
                    " VALUES (%s, %s, %s, %s)",
                    (location, week, horizon, rate),
                )
            migrated_db.commit()

    return Seeder
