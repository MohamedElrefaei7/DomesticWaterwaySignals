"""Fixtures for tests/analogs/ — see app/analogs/ and CLAUDE.md § 19.

Two tiers, the same split the other four suites use:

  Unit tier — no database, no network. Detection, similarity, outcomes, the gate and the renderer
  are pure functions over sequences, and their arithmetic is checked against HAND-COMPUTED
  expectations. That matters here for the reason it mattered in tests/signals/: a similarity metric
  tested against its own output agrees with itself in both directions of every mutation, and the
  mutations in this phase are one-line changes that produce plausible numbers — a cutoff, a
  carried-forward rate, a median computed one step too early.

  Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, and SKIPS WITH A
  STATED REASON when it is absent. It never silently passes.

WHAT IS IN THE INTEGRATION TIER HERE, AND WHY EACH IS THERE
------------------------------------------------------------
Four things, and none of them could be asserted in Python:

    the query-event exclusion    it is a property of the engine reading a REAL feature series and
                                 a real rate series. An in-memory version would assert that the
                                 code excludes what the code excludes.
    the passing-gate CHECKs      migration 0024 refuses a 'passed' row on three analogs from the
                                 DATABASE side, so a script or a future module cannot write one
                                 either — the same argument tests/signals/ makes about p-without-q.
    every match references       a foreign key.
    the sweep's verdict          it is a join against `signals`, and the column pair exists so an
                                 output cannot be read without it.

THE FIFTH conftest IN THIS REPO, duplicated rather than shared, for the reason
tests/ingest/conftest.py states.

THE SEEDER WRITES `features` AND `barge_rates` DIRECTLY rather than running the Phase 5 build, for
tests/signals/conftest.py's reason: these tests are about the ANALOG ENGINE, and driving them
through the builder would make every one of them also a test of the builder.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.orchestration import migrate  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "migrations"

ST_LOUIS = "07010000"
MEMPHIS = "07032000"
VICKSBURG = "07289000"
BATON_ROUGE = "07374000"

DISCHARGE = "00060"


@pytest.fixture(scope="session")
def database_url():
    """The integration tier's entry condition. Skips loudly, naming the variable."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "DATABASE_URL is not set: the integration tier needs a real Postgres with "
            "TimescaleDB. These tests are SKIPPED, not passed - nothing below has been verified. "
            "Start a database and export DATABASE_URL to run them."
        )
    return url


RESET_SCHEMA_SQL = """
-- ONE `DROP TABLE` STATEMENT FOR EVERY TABLE, NOT ONE STATEMENT PER TABLE. See
-- tests/signals/conftest.py: the per-table loop deadlocked roughly one run in four once the
-- dependency graph widened, and Phase 7 widens it again (`analog_matches` references
-- `analog_queries`, which references both `gauges` and `signal_runs`).
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
def migrated_db(database_url):
    """A database holding this project's real migrations and nothing else.

    The real migrations, so these tests run against the actual CHECK constraints in 0024 and the
    actual foreign keys in 0025 — which is the whole argument for asserting them in the database.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    migrate.run(MIGRATIONS_DIR, url=database_url)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def seed_analogs(migrated_db):
    """Insert `features`, `barge_rates`, and a `signals` row directly.

    Returns a class of static methods rather than seeding anything itself: several tests want an
    empty history, and a fixture that always writes would make "the engine over nothing" untestable
    — which is the case that must return a refusal rather than an exception.
    """

    class Seeder:
        @staticmethod
        def features(site_id, feature_name, rows):
            """`rows` is `(date, value, anomaly, climatology_n_years)` — the builder's own tuple.

            `executemany` rather than a loop of `execute`: the engine's integration tier seeds
            eight years of five features, and a per-row round trip turns a two-second fixture into
            a forty-second one. A slow suite is a suite that gets run with `-k`, which is how a
            failure stops being read.
            """
            with migrated_db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO features"
                    " (date, site_id, feature_name, value, anomaly, climatology_n_years)"
                    " VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (date, site_id, feature_name) DO UPDATE"
                    "   SET value = excluded.value, anomaly = excluded.anomaly,"
                    "       climatology_n_years = excluded.climatology_n_years",
                    [
                        (day, site_id, feature_name, value, anomaly, n_years)
                        for day, value, anomaly, n_years in rows
                    ],
                )
            migrated_db.commit()

        @staticmethod
        def rates(weeks_and_values, location=None, horizon=None):
            from app.features.targets import TARGET_HORIZON, TARGET_LOCATION

            with migrated_db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff)"
                    " VALUES (%s, %s, %s, %s)",
                    [
                        (
                            TARGET_LOCATION if location is None else location,
                            week,
                            TARGET_HORIZON if horizon is None else horizon,
                            value,
                        )
                        for week, value in weeks_and_values
                    ],
                )
            migrated_db.commit()

        @staticmethod
        def signal(site_id, feature_name, q_value, *, passes_gate=False):
            """One `signals` row so the engine has a sweep verdict to record.

            Deliberately minimal: this suite is not about the sweep, and the column pair exists so
            an analog output cannot be read without a verdict beside it.
            """
            run_id = migrated_db.execute(
                "INSERT INTO signal_runs"
                " (grid_size, lag_min, lag_max, horizons, regimes, feature_filter, git_sha,"
                "  git_dirty, seed)"
                " VALUES (10, -21, 21, ARRAY[7,14,21], ARRAY['onset','recovery','all'], NULL,"
                "         %s, false, NULL) RETURNING run_id",
                ("f" * 40,),
            ).fetchone()[0]
            migrated_db.execute(
                "INSERT INTO signals"
                " (run_id, feature_name, site_id, series_column, target_name, horizon_days,"
                "  lag_days, regime, status, statistic, p_value, q_value, grid_size,"
                "  n_tests_adjusted, n_observations, n_effective, folds,"
                "  directional_consistency, passes_gate)"
                " VALUES (%s, %s, %s, 'value', 'cairo_memphis_nearby_log_return', 7, 0, 'all',"
                "         'scanned', -0.137, 0.01, %s, 10, 10, 616, 616, 5, 1.0, %s)",
                (run_id, feature_name, site_id, q_value, passes_gate),
            )
            migrated_db.commit()
            return run_id

    return Seeder


# ---------------------------------------------------------------------------------------------
# Unit-tier helpers. Sequences, so the arithmetic can be hand-computed.
# ---------------------------------------------------------------------------------------------


def daily(start, values):
    """`(date, value)` on consecutive days. The shape `features` carries."""
    return [(start + timedelta(days=i), value) for i, value in enumerate(values)]


def weekly(start, values):
    """`(week_ending, rate)` on a weekly grid. The shape `barge_rates` publishes."""
    return [(start + timedelta(days=7 * i), value) for i, value in enumerate(values)]


def run_of(start, length, *, before=0, after=0, value=1.0):
    """A quiet stretch, a low-water run of `length` days, then a quiet stretch.

    Values are the `days_below_p10` counter's shape: 0 while the river is fine, counting up while
    it is not. Built here rather than in each test so a 60-day run and a 3-day run differ in one
    number.
    """
    values = [0.0] * before + [value * (i + 1) for i in range(length)] + [0.0] * after
    return daily(start, values)
