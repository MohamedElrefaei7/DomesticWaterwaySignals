"""Fixtures for tests/api/ — see app/api/ and CLAUDE.md § 20.

THE SIXTH conftest IN THIS REPO, duplicated rather than shared, for tests/ingest/conftest.py's
reason.

TWO TIERS, AND WHERE THE LINE FALLS HERE IS NOT WHERE IT FELL IN THE OTHER SUITES
----------------------------------------------------------------------------------
Unit tier — no database. Everything about SHAPE lives here: which keys a refusal has, whether a
model declares a numeric default, whether `total` is echoed, whether an over-maximum limit is a 422,
whether the cache key includes `as_of`. These are properties of this layer's own code and a
database would only slow them down.

Integration tier — marked @pytest.mark.integration, requires DATABASE_URL, SKIPS WITH A STATED
REASON when it is absent. Everything about whether a VALUE survives the round trip: a NULL rate
serializing as `null`, a zero tonnage serializing as `0`, `total` counting the unpaginated set, the
last-success query ignoring a more recent failure.

WHY `test_last_success_ignores_a_more_recent_failure` IS INTEGRATION THOUGH THE BRIEF DID NOT MARK
IT — AND IT IS THE MOST IMPORTANT SENTENCE IN THIS FILE
---------------------------------------------------------------------------------------------------
`FakeConn` below answers "the last success for job X" from a dict. IT DOES NOT IMPLEMENT
`WHERE status = 'success'` — that predicate IS the rule being tested (CLAUDE.md § 4: a job failing
nightly has recent activity and no recent success). A unit test over this fake would be asserting
that the fake returns what the fake was told to return, in both directions of every mutation. That
is a config test standing where a behavioural one belongs, which this project has shipped ten of
before (CLAUDE.md § 2, theme 2).

So the fake is deliberately incapable of it, and the tests that need it read real `job_runs` rows.
Same reasoning for `test_health_reports_data_freshness_not_process_liveness`: the whole point is a
job whose runs are healthy while its TABLE is quiet, and only a real table can be quiet.

WHAT THE FAKE IS LEGITIMATELY FOR: the mapping and the flag. Whether a verdict becomes the right
JSON keys, whether `degraded` is an OR over both halves, whether a stale job produces 200 rather
than 503. Those are this layer's own arithmetic and the fake cannot fake them.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from app.api import cache as cache_module  # noqa: E402
from app.api.middleware import ratelimit  # noqa: E402
from app.api.dependencies import get_connection, now as now_dependency  # noqa: E402
from app.api.main import app  # noqa: E402
from app.orchestration import migrate  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "migrations"

ST_LOUIS = "07010000"
MEMPHIS = "07032000"
VICKSBURG = "07289000"
BATON_ROUGE = "07374000"

DISCHARGE = "00060"
MEAN = "00003"

# The segment CLAUDE.md § 7's output contract names, spelled as USDA publishes it.
CAIRO_MEMPHIS = "Cairo-Memphis"
TWIN_CITIES = "Twin Cities"


@pytest.fixture(autouse=True)
def _clean_caches():
    """Every test starts with empty caches.

    Autouse because the caches are module-level singletons: a conclusion cached by one test would
    be served to the next, and the symptom would be a test that passes alone and fails in the
    suite - the least debuggable failure available. The cache's own behaviour is asserted
    explicitly in test_conclusion.py rather than relied on incidentally anywhere else.
    """
    cache_module.CONCLUSION_CACHE.clear()
    cache_module.SIGNALS_CACHE.clear()
    # THE RATE LIMITER IS THE SAME KIND OF SINGLETON AND NEEDS THE SAME TREATMENT (Phase 11).
    # Its buckets are module-level and per-process, so a test that makes a hundred requests drains
    # the general bucket for every test after it - and the victim fails with a 429 where it
    # expected a 500, having done nothing wrong. Measured: adding the limiter turned
    # test_no_error_body_contains_sql_or_a_connection_string red in the suite while it passed
    # alone, which is the least debuggable failure available.
    ratelimit.LIMITER.reset()
    yield
    cache_module.CONCLUSION_CACHE.clear()
    cache_module.SIGNALS_CACHE.clear()


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Dependency overrides never leak between tests. `app` is a module-level singleton too."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------------------------
# The client.
# ---------------------------------------------------------------------------------------------


def make_client(conn=None, now=None) -> TestClient:
    """A TestClient with the connection and the clock overridden.

    `raise_server_exceptions=False` SO AN UNHANDLED EXCEPTION BECOMES A RESPONSE. The default
    re-raises it into the test, which would make `test_no_error_body_contains_sql_or_a_connection_string`
    impossible to write - there would be no body to inspect, and the handler that must not leak
    would never run. This is the flag that lets the error contract be tested at all.
    """
    if conn is not None:
        def _connection():
            """Yield the test's connection and DISCARD ITS TRANSACTION on the way out.

            In production `get_connection` opens a connection per request and closes it, which
            rolls back whatever the request left open. A test reuses one connection across several
            requests, so without the rollback a query that raises leaves the transaction aborted
            and EVERY SUBSEQUENT REQUEST FAILS WITH `InFailedSqlTransaction` - a cascade that hides
            which query actually broke. This reproduces the production lifecycle's effect on
            transaction state without giving each request its own connection.
            """
            try:
                yield conn
            finally:
                conn.rollback()

        app.dependency_overrides[get_connection] = _connection
    if now is not None:
        app.dependency_overrides[now_dependency] = lambda: now
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client():
    """A client with NO connection override. Used by the tests that force a database failure."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------------------------
# The unit tier's fake connection.
# ---------------------------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Answers the three query shapes the unit tier needs, and RAISES ON ANYTHING ELSE.

    Raising on an unrecognized query is the decision. A fake that returned an empty result for
    whatever it did not recognize would let a route quietly acquire a new query and keep passing -
    the test would assert a response built from a silent `None`, which is exactly the shape of
    failure this project keeps finding.

    IT DOES NOT IMPLEMENT `WHERE status = 'success'`. See the module docstring.
    """

    def __init__(self, *, last_success=None, newest=None, unqueryable=(), run_summary_row=None):
        self.last_success = dict(last_success or {})
        self.newest = dict(newest or {})
        self.unqueryable = set(unqueryable)
        self.run_summary_row = run_summary_row
        self.rolled_back = 0

    def execute(self, sql, params=None):
        text = " ".join(sql.split())

        if "FROM job_runs" in text:
            job_name = params[0] if isinstance(params, (tuple, list)) else params
            return _FakeCursor([(self.last_success.get(job_name),)])

        if text.startswith("SELECT max(") and " FROM " in text:
            table = text.split(" FROM ")[1].strip()
            if table in self.unqueryable:
                raise RuntimeError(
                    f'relation "{table}" does not exist; SELECT max(ts) FROM {table}'
                )
            return _FakeCursor([(self.newest.get(table),)])

        if "FROM signal_runs" in text:
            return _FakeCursor([self.run_summary_row] if self.run_summary_row else [])

        raise AssertionError(
            f"FakeConn was asked a query it does not model, which means a route acquired one and "
            f"this test is no longer asserting what it says it asserts:\n{text}"
        )

    def rollback(self):
        self.rolled_back += 1


class ExplodingConn:
    """A connection whose every query raises WITH SQL AND A CONNECTION STRING IN THE MESSAGE.

    The message is deliberately the worst case: it names a table, quotes the statement, and carries
    a URL with a password in it. If any of that reaches a response body, the assertions in
    test_contract.py fail on the exact substrings.
    """

    MESSAGE = (
        'relation "barge_rates" does not exist\n'
        "LINE 1: SELECT location, week_ending FROM barge_rates WHERE ...\n"
        "connection: postgresql://waterway_api:hunter2@10.0.1.7:5432/waterway"
    )

    def execute(self, sql, params=None):
        raise RuntimeError(self.MESSAGE)

    def rollback(self):
        pass


# ---------------------------------------------------------------------------------------------
# The integration tier.
# ---------------------------------------------------------------------------------------------


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
-- One DROP statement for every table rather than one statement per table; see
-- tests/analogs/conftest.py for the deadlock that made this necessary.
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

    The real migrations, so the NULL/0 tests run against the actual nullable columns and the actual
    CHECK constraints - which is the entire argument for asserting them against a database.
    """
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute(RESET_SCHEMA_SQL)

    migrate.run(MIGRATIONS_DIR, url=database_url)

    with db.connection(database_url) as conn:
        yield conn


@pytest.fixture
def seed(migrated_db):
    """Direct inserts. Returns a class of static methods rather than seeding anything itself.

    Several tests want an EMPTY table - a gauge with no readings, a database with no sweep run -
    and a fixture that always writes would make those cases untestable. They are the cases where
    the difference between `null`, `0` and an absent row is decided.
    """

    class Seeder:
        @staticmethod
        def job_run(job_name, status, finished_at, rows_written=None):
            migrated_db.execute(
                "INSERT INTO job_runs (job_name, started_at, finished_at, status, rows_written)"
                " VALUES (%s, %s, %s, %s, %s)",
                (job_name, finished_at, finished_at, status, rows_written),
            )
            migrated_db.commit()

        @staticmethod
        def rates(rows, location=CAIRO_MEMPHIS, horizon="nearby"):
            """`rows` is `(week_ending, pct_of_tariff)`; a None rate is written as NULL.

            NULL is what the ingest layer writes for a week USDA published with no `rate` field -
            661 of 774 of them a winter navigation closure. Seeding it directly is the only way to
            assert it survives to the client, because a fixture that only ever writes numbers
            cannot tell a preserved NULL from a coalesced one.
            """
            with migrated_db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff)"
                    " VALUES (%s, %s, %s, %s)",
                    [(location, week, horizon, value) for week, value in rows],
                )
            migrated_db.commit()

        @staticmethod
        def movements(rows):
            """`rows` is `(lock, week_ending, commodity, tons)`.

            Four columns, because migration 0016 dropped `direction` and `barges` after measuring
            that the source publishes neither - a column that would always be NULL is not created.
            """
            with migrated_db.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO lock_movements ("lock", week_ending, commodity, tons)'
                    " VALUES (%s, %s, %s, %s)",
                    list(rows),
                )
            migrated_db.commit()

        @staticmethod
        def daily_values(site_id, rows, param_code=DISCHARGE, stat_cd=MEAN):
            """`rows` is `(date, value)`, written to `gauge_readings_daily`.

            The daily table rather than the instantaneous one: `gauge_series` reads both and
            prefers `iv`, so seeding `dv` exercises the view's own precedence branch and keeps the
            fixture from having to invent timestamps in a particular zone.
            """
            with migrated_db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO gauge_readings_daily"
                    " (usgs_site_id, date, param_code, stat_cd, value, qualifiers)"
                    " VALUES (%s, %s, %s, %s, %s, NULL)",
                    [(site_id, day, param_code, stat_cd, value) for day, value in rows],
                )
            migrated_db.commit()

        @staticmethod
        def signal_run(grid_size=10):
            return migrated_db.execute(
                "INSERT INTO signal_runs"
                " (grid_size, lag_min, lag_max, horizons, regimes, feature_filter, git_sha,"
                "  git_dirty, seed)"
                " VALUES (%s, -21, 21, ARRAY[7,14,21], ARRAY['onset','recovery','all'], NULL,"
                "         %s, false, NULL) RETURNING run_id",
                (grid_size, "f" * 40),
            ).fetchone()[0]

        @staticmethod
        def signal(run_id, *, feature_name, site_id, q_value, passes_gate, grid_size=10,
                   lag_days=0, horizon_days=7, regime="all"):
            migrated_db.execute(
                "INSERT INTO signals"
                " (run_id, feature_name, site_id, series_column, target_name, horizon_days,"
                "  lag_days, regime, status, statistic, p_value, q_value, grid_size,"
                "  n_tests_adjusted, n_observations, n_effective, folds,"
                "  directional_consistency, passes_gate)"
                # n_observations and n_effective are the real Phase 6 numbers for the one surviving
                # row. They are equal because that row is at horizon 7 on a weekly series, where
                # the forward windows do not overlap - and 0023 CHECKs that the effective count
                # never exceeds the raw one, which is the correction being visible rather than
                # merely applied.
                " VALUES (%s, %s, %s, 'value', 'cairo_memphis_nearby_log_return', %s, %s, %s,"
                "         'scanned', -0.137, 0.01, %s, %s, %s, 616, 616, 5, 1.0, %s)",
                (run_id, feature_name, site_id, horizon_days, lag_days, regime, q_value,
                 grid_size, grid_size, passes_gate),
            )
            migrated_db.commit()

        @staticmethod
        def features(site_id, feature_name, rows):
            """`rows` is `(date, value, anomaly, climatology_n_years)`."""
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

    return Seeder


@pytest.fixture
def db_client(migrated_db):
    """A TestClient wired to the migrated database."""
    return make_client(conn=migrated_db)


# ---------------------------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------------------------


def weekly(start: date, values):
    """`(week_ending, value)` on a weekly grid. The shape `barge_rates` publishes."""
    return [(start + timedelta(days=7 * i), value) for i, value in enumerate(values)]


def numeric_leaves(body, path=()):
    """Every numeric leaf in a decoded JSON body, with the key path that reaches it.

    Yields `(path, value)`. Booleans are excluded: `True` is an `int` in Python and a flag is not
    an estimate, and including them would make the refusal walk assert about `git_dirty`.

    A RECURSIVE WALK RATHER THAN A LIST OF FIELD NAMES, because the failure this catches is a
    number somewhere nobody thought to look - in a nested debug block, in a sibling object, three
    levels down. `app/analogs/gate.py` makes the same argument for the same reason.
    """
    if isinstance(body, dict):
        for key, value in body.items():
            yield from numeric_leaves(value, path + (key,))
    elif isinstance(body, (list, tuple)):
        for index, value in enumerate(body):
            yield from numeric_leaves(value, path + (str(index),))
    elif isinstance(body, bool):
        return
    elif isinstance(body, (int, float)):
        yield path, body


UTC = timezone.utc


def utc(*args):
    return datetime(*args, tzinfo=UTC)
