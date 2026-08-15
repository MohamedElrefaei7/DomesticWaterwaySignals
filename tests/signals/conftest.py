"""Fixtures for tests/signals/ — see app/signals/ and CLAUDE.md § 18.

Two tiers, the same split the other three suites use:

  Unit tier — no database, no network. Everything that decides what the sweep measures is a pure
  function taking sequences, so the arithmetic is checked against HAND-COMPUTED expectations. This
  matters more here than anywhere else in the project: a statistics module tested against its own
  output would agree with itself in both directions of every mutation, and the mutations in this
  phase are one-character changes that produce plausible numbers - `n` for `n_effective`, `<` for
  `<=`, a missing running minimum.

  Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, and SKIPS WITH A
  STATED REASON when it is absent. It never silently passes.

WHAT IS IN THE INTEGRATION TIER HERE, AND WHY EACH IS THERE
------------------------------------------------------------
Four things, and none of them could be asserted in Python:

    the p-without-q refusal      it is a CHECK constraint. Asserting that the writer never builds
                                 such a row tests the writer; the point is that the DATABASE
                                 refuses it, so a script, a manual INSERT, or a future module
                                 cannot write one either.
    grid_size on every row       a NOT NULL column, same argument.
    the run's git sha and dirty  it is read from the real repo by subprocess and stored.
    every row references a run   a foreign key.

THE FOURTH conftest IN THIS REPO, duplicated rather than shared, for the reason
tests/ingest/conftest.py states: the existing copies already collided once when two suites ran in a
single pytest invocation, and a shared helper would be another import path into the same collision.

THE SEEDER WRITES `features` AND `targets` DIRECTLY rather than running the Phase 5 build. That is
deliberate: these tests are about the SWEEP, and driving them through the builder would make every
one of them also a test of the builder - so a defect in `app/features/` would turn this suite red
in a way that says nothing about what this suite covers. tests/features/ owns that boundary.
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

# The four seeded gauges (migration 0004). Named here so a test can pick one without repeating an
# id inline - and note that app/signals/pairs.py contains none of these, which
# test_pairs.py::test_duplication_is_detected_from_n_observations_not_a_site_list asserts.
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
-- ONE `DROP TABLE` STATEMENT FOR EVERY TABLE, NOT ONE STATEMENT PER TABLE.
--
-- The per-table loop this replaces DEADLOCKED roughly one run in four once Phase 5 added
-- `gauge_daily` and `features`, both of which carry foreign keys to `gauges`. Two things combined:
-- the catalog scan has no ORDER BY, so the drop order varied run to run, and each CASCADE takes
-- locks on that table's dependents. Fifteen separate statements against a widening dependency
-- graph, racing TimescaleDB's background workers on the two hypertables, is fifteen windows for a
-- lock cycle. Phase 6 widens it again: `signals` references both `signal_runs` and `gauges`.
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
def migrated_db(database_url):
    """A database holding this project's real migrations and nothing else.

    The real migrations, not a fixture copy: these tests then run against the actual CHECK
    constraints in 0023 and the actual foreign keys, which is the whole argument for asserting them
    in the database rather than in Python.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    migrate.run(MIGRATIONS_DIR, url=database_url)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def seed_signals(migrated_db):
    """Insert `gauge_daily`, `features` and `targets` rows directly.

    Returns a class of static methods rather than seeding anything itself - several tests want an
    empty database, and a fixture that always writes would make "the sweep over nothing" untestable.
    """

    class Seeder:
        @staticmethod
        def gauge_daily(site_id, dates_and_values, *, n_observations=1, param_code=DISCHARGE):
            """Rows whose `n_observations` is what the duplicate-pair detection reads.

            `n_observations = 1` on every row makes the site DEGENERATE - the state Phase 5
            measured at two of the four gauges, where `value_min` IS the published daily mean.
            Passing anything else on any row makes it not degenerate, which is the case the
            detection must also get right.
            """
            for day, value in dates_and_values:
                migrated_db.execute(
                    "INSERT INTO gauge_daily"
                    " (usgs_site_id, date, param_code, value_mean, value_min, value_max, source,"
                    "  n_observations)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (site_id, day, param_code, value, value, value, "dv", n_observations),
                )
            migrated_db.commit()

        @staticmethod
        def features(site_id, feature_name, rows):
            """`rows` is `(date, value, anomaly, climatology_n_years)` - the builder's own tuple."""
            for day, value, anomaly, n_years in rows:
                migrated_db.execute(
                    "INSERT INTO features"
                    " (date, site_id, feature_name, value, anomaly, climatology_n_years)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (day, site_id, feature_name, value, anomaly, n_years),
                )
            migrated_db.commit()

        @staticmethod
        def targets(weeks_and_values, horizon_days, target_name=None):
            from app.features import targets as targets_module

            name = targets_module.TARGET_NAME if target_name is None else target_name
            for week, value in weeks_and_values:
                migrated_db.execute(
                    "INSERT INTO targets (week_ending, target_name, horizon_days, value)"
                    " VALUES (%s, %s, %s, %s)",
                    (week, name, horizon_days, value),
                )
            migrated_db.commit()

        @staticmethod
        def open_run(**overrides):
            """A `signal_runs` row, so a `signals` row has something to reference.

            Returns the run_id. Every column has a plausible default because the tests that use
            this are about `signals`, and a test that had to spell out nine run parameters to
            assert one CHECK constraint would be a test whose subject was hard to find.
            """
            parameters = {
                "grid_size": 10,
                "lag_min": -21,
                "lag_max": 21,
                "horizons": [7, 14, 21],
                "regimes": ["onset", "recovery", "all"],
                "feature_filter": None,
                "git_sha": "0" * 40,
                "git_dirty": False,
                "seed": None,
            }
            parameters.update(overrides)
            row = migrated_db.execute(
                "INSERT INTO signal_runs"
                " (grid_size, lag_min, lag_max, horizons, regimes, feature_filter, git_sha,"
                "  git_dirty, seed)"
                " VALUES (%(grid_size)s, %(lag_min)s, %(lag_max)s, %(horizons)s, %(regimes)s,"
                "         %(feature_filter)s, %(git_sha)s, %(git_dirty)s, %(seed)s)"
                " RETURNING run_id",
                parameters,
            ).fetchone()
            migrated_db.commit()
            return row[0]

    return Seeder


# A small grid: enough weeks for five folds at every horizon, few enough lags to run in a second.
# The lag range spans zero in both directions because the negative half is half the experiment.
SWEEP_LAG_MIN = -3
SWEEP_LAG_MAX = 3
SWEEP_HORIZONS = (7, 14)
SWEEP_WEEKS = 80

# A fixed sha, so tests that are not about provenance do not shell out to git.
FIXED_GIT = ("f" * 40, False)


@pytest.fixture
def sweepable(seed_signals):
    """A database holding two sites of features and a target series long enough to split.

    Shared by test_sweep.py and test_statistics.py rather than living in either, because the
    p-without-q constraint has two halves that must both be exercised: THE DATABASE REFUSES SUCH A
    ROW (asserted by inserting one directly) and THE WRITER NEVER BUILDS ONE (asserted by running a
    real sweep and reading the table back). A mutation that drops the Benjamini-Hochberg step
    leaves the first half green and must turn the second half red.

    The feature series differs between the two sites, so a sweep that scanned one pair and reused
    the answer could not pass.
    """
    from datetime import date, timedelta

    weeks = [date(2021, 1, 7) + timedelta(days=7 * i) for i in range(SWEEP_WEEKS)]
    first_day = weeks[0] - timedelta(days=30)
    days = [first_day + timedelta(days=i) for i in range((weeks[-1] - first_day).days + 31)]

    for site, phase in ((ST_LOUIS, 0), (MEMPHIS, 5)):
        seed_signals.features(
            site,
            "days_below_p10",
            [(day, float((i + phase) % 40), None, None) for i, day in enumerate(days)],
        )
        seed_signals.features(
            site,
            "discharge_mean",
            [
                (day, float(i % 17), float((i + phase) % 23) - 11.0, 12)
                for i, day in enumerate(days)
            ],
        )

    for horizon in SWEEP_HORIZONS:
        seed_signals.targets(
            [(week, ((i * 13) % 29) / 100.0 - 0.14) for i, week in enumerate(weeks)], horizon
        )

    return {"weeks": weeks, "days": days}


def weekly(start, count, values=None):
    """`(week_ending, value)` on a Thursday-ending weekly grid, the shape barge_rates publishes.

    A helper rather than a fixture because most tests want to build two or three of these and
    compare them.
    """
    from datetime import timedelta

    return [
        (start + timedelta(days=7 * i), (values[i] if values is not None else float(i)))
        for i in range(count)
    ]


def daily(start, count, values=None):
    """`(date, value)` on consecutive days, the shape `features` carries."""
    from datetime import timedelta

    return [
        (start + timedelta(days=i), (values[i] if values is not None else float(i)))
        for i in range(count)
    ]
