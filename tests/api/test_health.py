"""`/api/health` — per-job, cadence-aware, data-measured, 200 while degraded, never cached.

THE FAILURE THIS ENDPOINT EXISTS TO PREVENT IS ALREADY IN THIS PROJECT'S HISTORY. The prior
project's orchestration recorded "Completed" while the whole stack had been down for two and a half
months (CLAUDE.md § 2). A health check that returns one word cannot say which of six jobs is quiet,
and a health check that reads job status cannot say that a job is succeeding on schedule while the
table it writes has received nothing for four days.

Three of these five are integration tests, and two of those three are marked integration though the
brief did not mark them. tests/api/conftest.py states the reason at length: `FakeConn` deliberately
does not implement `WHERE status = 'success'`, because that predicate IS the rule under test, and a
fake that implemented it would be a test asserting what the test set up.
"""

from datetime import date, timedelta

import pytest

from app.orchestration import cadence as cadence_module
from tests.api.conftest import FakeConn, make_client, utc

NOW = utc(2026, 8, 16, 12, 0, 0)

# Every registered freshness table, fresh. Individual tests make one of them stale.
FRESH_TABLES = {
    "gauge_readings_iv": NOW - timedelta(hours=1),
    "gauge_readings_daily": date(2026, 8, 15),
    "barge_rates": date(2026, 8, 15),
    "lock_movements": date(2026, 8, 15),
    "features": date(2026, 8, 15),
}

ALL_JOBS_RECENT = {entry.job_name: NOW - timedelta(minutes=5) for entry in cadence_module.CADENCES}


def _get(conn):
    return make_client(conn=conn, now=NOW).get("/api/health")


def test_health_reports_per_job_last_success_and_overdue():
    """Test 9. One row per cadence entry, each with its own last success and its own threshold.

    Asserts the SHAPE that makes a one-word health check impossible: every job in the cadence table
    appears by name, and each carries the threshold it was judged against rather than a shared one.
    A response that reported a single boolean would satisfy nothing here.
    """
    response = _get(FakeConn(last_success=ALL_JOBS_RECENT, newest=FRESH_TABLES))

    assert response.status_code == 200
    body = response.json()

    reported = {job["job_name"]: job for job in body["jobs"]}
    assert reported.keys() == {entry.job_name for entry in cadence_module.CADENCES}

    for entry in cadence_module.CADENCES:
        job = reported[entry.job_name]
        assert job["last_success"] is not None
        assert job["overdue"] is False
        # The threshold on the row is THAT JOB'S OWN, read from the cadence table. usgs_ingest is
        # 3 hours and usda_rates_ingest is 14 days; a single shared number would be a second table
        # of the same fact (CLAUDE.md § 4).
        assert job["overdue_after_seconds"] == entry.overdue_after.total_seconds()

    assert body["degraded"] is False
    assert "status" not in body, (
        "a top-level `status` field is how a per-job report becomes a one-word one"
    )


def test_a_job_with_no_successful_run_is_overdue_not_quiet():
    """A job with nothing on record is the most alarming state in the table, not the quietest.

    Not numbered in the brief. It is the case a NULL makes easy to get wrong - `age is None` reads
    as "nothing to report" - and CLAUDE.md § 12 names it explicitly.
    """
    missing = dict(ALL_JOBS_RECENT)
    missing["features_build"] = None

    body = _get(FakeConn(last_success=missing, newest=FRESH_TABLES)).json()
    build = next(job for job in body["jobs"] if job["job_name"] == "features_build")

    assert build["last_success"] is None
    assert build["overdue"] is True
    # NOT 0. A zero age would say it succeeded just now, which inverts what the NULL means.
    assert build["age_seconds"] is None
    assert body["degraded"] is True


def test_a_stale_job_returns_200_with_degraded_true():
    """Test 10, decision 2. A degraded system is a 200 with a field, never a 5xx.

    An uptime monitor that goes red on a stale ingest job is indistinguishable from one that goes
    red because the API is down, and the two need different responses. This test is what the
    "return a 503 when a job is overdue" mutation turns red.
    """
    stale = dict(ALL_JOBS_RECENT)
    stale["usgs_ingest"] = NOW - timedelta(days=2)  # threshold is 3 hours

    response = _get(FakeConn(last_success=stale, newest=FRESH_TABLES))

    assert response.status_code == 200, (
        "a degraded dependency must not be reported as an API failure; `degraded` is the field a "
        "monitor alerts on"
    )
    body = response.json()
    assert body["degraded"] is True

    ingest = next(job for job in body["jobs"] if job["job_name"] == "usgs_ingest")
    assert ingest["overdue"] is True
    assert ingest["age_seconds"] > ingest["overdue_after_seconds"]


def test_a_stale_table_alone_is_enough_to_degrade():
    """`degraded` is an OR over BOTH halves, and the data half is the one that catches the hard one.

    Not numbered. Every job succeeding on schedule while a table goes quiet is the exact scenario
    the freshness registry exists for, and a `degraded` computed from the jobs alone would report
    healthy through all of it.
    """
    stale_tables = dict(FRESH_TABLES)
    stale_tables["barge_rates"] = date(2026, 6, 1)  # threshold is 10 days

    body = _get(FakeConn(last_success=ALL_JOBS_RECENT, newest=stale_tables)).json()

    assert all(job["overdue"] is False for job in body["jobs"])
    assert body["degraded"] is True
    rates = next(table for table in body["data"] if table["table"] == "barge_rates")
    assert rates["stale"] is True


def test_an_unqueryable_table_is_a_failed_check_and_names_no_detail():
    """A registered table that cannot be read is FAILED, never skipped (CLAUDE.md § 13).

    Not numbered. It also pins the error field to a CLASS NAME: the heartbeat's own record carries
    the full exception message, which is where a table name, a role, or a connection string would
    ride across the boundary.
    """
    body = _get(
        FakeConn(
            last_success=ALL_JOBS_RECENT, newest=FRESH_TABLES, unqueryable={"features"}
        )
    ).json()

    features = next(table for table in body["data"] if table["table"] == "features")
    assert features["stale"] is True
    assert features["error"] == "RuntimeError"
    assert "SELECT" not in (features["error"] or "")
    assert body["degraded"] is True


@pytest.mark.integration
def test_last_success_ignores_a_more_recent_failure(migrated_db, seed):
    """Test 11. CLAUDE.md § 4's rule, asserted at the API against real `job_runs` rows.

    A job failing nightly has plenty of recent activity and no recent success. The reported
    `last_success` must be the older SUCCESS row, not the newer FAILED one - and the job must be
    overdue on the strength of it.

    INTEGRATION BECAUSE THE PREDICATE IS THE POINT. Over a fake connection this test would assert
    that the fake returned what it was handed; only a real table can contain both rows and let the
    `WHERE status = 'success'` clause be the thing that chooses.
    """
    from tests.api.conftest import make_client

    now = utc(2026, 8, 16, 12, 0, 0)
    succeeded_at = now - timedelta(days=2)   # usgs_ingest threshold is 3 hours
    failed_at = now - timedelta(minutes=10)

    seed.job_run("usgs_ingest", "success", succeeded_at)
    seed.job_run("usgs_ingest", "failed", failed_at)

    body = make_client(conn=migrated_db, now=now).get("/api/health").json()
    ingest = next(job for job in body["jobs"] if job["job_name"] == "usgs_ingest")

    assert ingest["last_success"].startswith("2026-08-14"), (
        f"last_success reported {ingest['last_success']}; the most recent row is a FAILURE at "
        f"{failed_at.isoformat()} and must not be read as activity"
    )
    assert ingest["overdue"] is True
    assert body["degraded"] is True


@pytest.mark.integration
def test_health_reports_data_freshness_not_process_liveness(migrated_db, seed):
    """Test 12. A job succeeding on schedule while its table receives nothing.

    This is the failure a job-status check cannot see (CLAUDE.md § 4): a source that accepts your
    connection and delivers nothing is indistinguishable from a healthy one at every layer except
    the data. Measured on 2026-08-13, USGS returns HTTP 200 with an empty array for a series a site
    does not serve.

    INTEGRATION because the assertion is that a real table is quiet while real job rows are recent.
    """
    from tests.api.conftest import make_client

    now = utc(2026, 8, 16, 12, 0, 0)
    for entry in cadence_module.CADENCES:
        seed.job_run(entry.job_name, "success", now - timedelta(minutes=5))

    # `barge_rates` gets one row, 40 days old. The job is perfectly healthy; the data is not.
    seed.rates([(date(2026, 7, 7), 250.0)])

    body = make_client(conn=migrated_db, now=now).get("/api/health").json()

    assert all(job["overdue"] is False for job in body["jobs"]), (
        "every job succeeded five minutes ago; the job half of this check must be clean"
    )
    rates = next(table for table in body["data"] if table["table"] == "barge_rates")
    assert rates["stale"] is True
    assert rates["newest"].startswith("2026-07-07")
    assert body["degraded"] is True, (
        "the jobs are fine and the data is 40 days old; a health check that cannot say so is the "
        "one that reported Completed for two and a half months"
    )


@pytest.mark.integration
def test_health_is_never_cached(migrated_db, seed):
    """Test 13, decision 8. Two calls across a state change must differ.

    A cached health check reports the state of the world up to a minute ago, which is unacceptable
    on the one endpoint that is read precisely when somebody suspects something is wrong. The
    request is byte-identical between the two calls, so a cache keyed on anything at all would
    serve the first answer twice.
    """
    from tests.api.conftest import make_client

    now = utc(2026, 8, 16, 12, 0, 0)
    client = make_client(conn=migrated_db, now=now)

    first = client.get("/api/health").json()
    stale = next(job for job in first["jobs"] if job["job_name"] == "features_build")
    assert stale["last_success"] is None

    seed.job_run("features_build", "success", now - timedelta(minutes=5))

    second = client.get("/api/health").json()
    fresh = next(job for job in second["jobs"] if job["job_name"] == "features_build")

    assert fresh["last_success"] is not None, (
        "the second call returned the first call's answer: /api/health is cached, and a cached "
        "health check reports the state of the world 60 seconds ago"
    )
    assert fresh["overdue"] is False
