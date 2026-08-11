"""Integration tier — the @job decorator against a real Postgres.

Covers CLAUDE.md § 12 decisions 8 (separate connection, committed before the work), 9 (NULL vs 0),
and 10 (nesting prevented at runtime).

Every test here needs a real database. The central one — that the `running` row is visible from
another connection while the wrapped function is still running — is a statement about transaction
visibility between sessions, which is precisely the thing no test double can have an opinion about.
"""

import pytest

from app import db
from app.orchestration.job import NestedJobError, active_job, job

pytestmark = pytest.mark.integration


def test_running_row_is_visible_from_another_connection_before_the_function_returns(
    migrated_db, database_url, job_runs
):
    """Decision 8, and the test the whole design exists for.

    The wrapped function queries job_runs on a DIFFERENT connection and finds its own `running`
    row. That can only be true if the bookkeeping INSERT was committed on a session of its own
    before the function was called. Move the bookkeeping onto the work's session and this fails:
    an uncommitted INSERT is invisible to every other session by definition.
    """
    seen = {}

    @job("visibility_probe", url=database_url)
    def work():
        with db.connection(database_url) as observer:
            rows = observer.execute(
                "SELECT run_id, status, started_at, finished_at FROM job_runs"
                " WHERE job_name = 'visibility_probe'"
            ).fetchall()
        seen["rows"] = rows
        return 7

    assert work() == 7

    assert len(seen["rows"]) == 1, (
        "a third-party connection could not see the run row while the job was running - the "
        "bookkeeping INSERT was not committed before the work started"
    )
    run_id, status, started_at, finished_at = seen["rows"][0]
    assert status == "running"
    assert started_at is not None
    assert finished_at is None

    final = job_runs.rows("visibility_probe")
    assert len(final) == 1
    assert final[0]["run_id"] == run_id, "the decorator closed a different row than it opened"
    assert final[0]["status"] == "success"
    assert final[0]["rows_written"] == 7
    assert final[0]["finished_at"] is not None


def test_running_row_survives_a_rollback_inside_the_wrapped_work(
    migrated_db, database_url, job_runs
):
    """Decision 8's consequence: the failure that most needs a record is the one that would not have one.

    The work writes a row, rolls it back, and then RAISES. The raise is what makes this test able
    to detect a shared session at all, and it took a mutation to notice: if the work merely rolls
    back and returns, a decorator holding its own uncommitted transaction still closes the row
    happily, and the test passes while the design is broken. On the failure path a shared session
    unwinds - the `running` row goes with it, the closing UPDATE matches nothing, and job_runs
    ends up with no record of the run at all.

    This is the same property live verification step 8 checks on the instance: the work rolled
    back, the record did not.
    """
    with db.connection(database_url) as setup:
        setup.execute("CREATE TABLE scratch (id int)")
        setup.commit()

    @job("rollback_probe", url=database_url)
    def work():
        with db.connection(database_url) as conn:
            conn.execute("INSERT INTO scratch (id) VALUES (1)")
            conn.rollback()
        raise RuntimeError("the source went away halfway through")

    with pytest.raises(RuntimeError, match="the source went away halfway through"):
        work()

    rows = job_runs.rows("rollback_probe")
    assert len(rows) == 1, (
        "the run left NO record at all. The bookkeeping row was rolled back along with the "
        "wrapped work - the failure that most needs a record is the one that did not get one."
    )
    assert rows[0]["status"] == "failed"
    assert "the source went away halfway through" in rows[0]["error_message"]

    with db.connection(database_url) as conn:
        scratch = conn.execute("SELECT count(*) FROM scratch").fetchone()[0]
    assert scratch == 0, "the wrapped work's rollback did not take effect - test is not meaningful"


def test_exception_marks_failed_records_the_message_and_reraises(
    migrated_db, database_url, job_runs
):
    """The decorator never swallows.

    A job that fails must fail loudly to the scheduler as well as to the table: they are two
    different consumers of the failure, and silencing the exception blinds one of them while
    leaving the other looking fine.
    """

    @job("failing_probe", url=database_url)
    def work():
        raise ValueError("the downstream source returned nothing")

    with pytest.raises(ValueError, match="the downstream source returned nothing"):
        work()

    rows = job_runs.rows("failing_probe")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["finished_at"] is not None
    assert rows[0]["rows_written"] is None
    assert "ValueError" in rows[0]["error_message"]
    assert "the downstream source returned nothing" in rows[0]["error_message"]

    # The guard is released even on the failure path, or one raised job poisons the worker thread
    # for every job scheduled after it.
    assert active_job() is None


def test_return_of_zero_and_return_of_none_are_stored_differently(
    migrated_db, database_url, job_runs
):
    """Decision 9: 0 means "ran and wrote nothing"; NULL means "does not report a count".

    Both are meaningful and they are not the same fact. A decorator that coalesces None to 0
    manufactures a row count nobody measured; one that coalesces 0 to None hides the single most
    diagnostic value a job can report.
    """

    @job("returns_zero", url=database_url)
    def zero():
        return 0

    @job("returns_none", url=database_url)
    def none():
        return None

    zero()
    none()

    zero_row = job_runs.rows("returns_zero")[0]
    none_row = job_runs.rows("returns_none")[0]

    assert zero_row["rows_written"] == 0
    assert zero_row["rows_written"] is not None
    assert none_row["rows_written"] is None

    # Stated as the distinction itself, so the failure message names it.
    assert zero_row["rows_written"] != none_row["rows_written"]


def test_nested_job_raises_and_names_both_jobs(migrated_db, database_url, job_runs):
    """Decision 10: enforced at runtime, not by convention.

    Both names appear in the message because "nested @job" without them sends the reader hunting
    through a call stack for which two.
    """

    @job("inner_job", url=database_url)
    def inner():
        return 1

    @job("outer_job", url=database_url)
    def outer():
        return inner()

    with pytest.raises(NestedJobError) as excinfo:
        outer()

    message = str(excinfo.value)
    assert "inner_job" in message
    assert "outer_job" in message

    # The outer job still gets a failure record; the inner one wrote no row at all, because the
    # guard fires before any INSERT.
    assert len(job_runs.rows("outer_job")) == 1
    assert job_runs.rows("outer_job")[0]["status"] == "failed"
    assert job_runs.rows("inner_job") == []

    assert active_job() is None

    # The escape hatch: calling the undecorated body inside another job is allowed, and is what
    # the error message tells the reader to do.
    @job("outer_job_fixed", url=database_url)
    def outer_fixed():
        return inner.undecorated()

    assert outer_fixed() == 1
    assert job_runs.rows("outer_job_fixed")[0]["status"] == "success"


def test_a_second_job_runs_normally_after_a_first_one_completes(
    migrated_db, database_url, job_runs
):
    """The nesting guard is a guard, not a latch.

    A ContextVar left set after a clean run would make the second scheduled job of the day raise
    NestedJobError - a failure that would look exactly like a nesting bug in unrelated code.
    """

    @job("sequential_probe", url=database_url)
    def work():
        return 1

    work()
    work()

    rows = job_runs.rows("sequential_probe")
    assert len(rows) == 2
    assert [r["status"] for r in rows] == ["success", "success"]
