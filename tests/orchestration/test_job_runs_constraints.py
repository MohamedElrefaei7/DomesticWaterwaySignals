"""Integration tier — the constraints job_runs carries in the database itself.

Covers CLAUDE.md § 12 decision 11: status is constrained BY THE DATABASE, and the table is
append-only via a BEFORE DELETE trigger with UPDATE deliberately still permitted.

These cannot be unit tests even in principle. The point of putting the constraint in the schema
rather than in a Python enum is that it also binds the psql session where a human is repairing
something at 2am — and a Python-side check is invisible to that session by construction.
"""

import psycopg
import pytest

from app import db

pytestmark = pytest.mark.integration


def _insert(conn, **kwargs):
    columns = ", ".join(kwargs)
    placeholders = ", ".join(["%s"] * len(kwargs))
    return conn.execute(
        f"INSERT INTO job_runs ({columns}) VALUES ({placeholders}) RETURNING run_id",
        tuple(kwargs.values()),
    ).fetchone()[0]


def test_database_rejects_an_invalid_status(migrated_db, database_url):
    """A direct INSERT of 'done' raises — no Python involved in the refusal.

    'done' rather than obvious garbage on purpose: it is the plausible near-miss someone types
    when they mean 'success', and a status vocabulary that quietly accepts synonyms is one the
    heartbeat's `status = 'success'` filter will silently stop matching.
    """
    with db.connection(database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert(conn, job_name="probe", status="done")
        conn.rollback()

    assert "job_runs_status_check" in str(excinfo.value)

    # All four legal values are accepted, asserted by size first so the set is not vacuous.
    legal = ["running", "success", "failed", "missed"]
    assert len(legal) == 4
    with db.connection(database_url) as conn:
        for status in legal:
            _insert(conn, job_name="probe", status=status)
        conn.commit()
        assert conn.execute("SELECT count(*) FROM job_runs").fetchone()[0] == 4


def test_delete_from_job_runs_raises(migrated_db, database_url):
    """The append-only trigger. CLAUDE.md § 4: no code path deletes from job_runs.

    A human making the one-off correction the contract allows must explicitly disable the trigger,
    which is the point — deletion becomes a deliberate act with a stated reason rather than a
    stray statement whose WHERE clause was wider than intended.
    """
    with db.connection(database_url) as conn:
        run_id = _insert(conn, job_name="probe", status="success")
        conn.commit()

    with db.connection(database_url) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            conn.execute("DELETE FROM job_runs WHERE run_id = %s", (run_id,))
        conn.rollback()

    message = str(excinfo.value)
    assert "append-only" in message
    assert str(run_id) in message, "the error does not name the row it refused to delete"

    # An unqualified DELETE is refused too - that is the one that would matter most.
    with db.connection(database_url) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM job_runs")
        conn.rollback()

    with db.connection(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM job_runs").fetchone()[0] == 1


def test_update_of_an_existing_row_is_permitted(migrated_db, database_url):
    """The counterpart, and the reason it needs its own test.

    "Append-only" read strictly would forbid UPDATE as well, and a trigger written
    BEFORE DELETE OR UPDATE is a defensible reading of those words. It would also break the @job
    decorator entirely: the decorator commits a `running` row before the work starts and comes
    back to close that same row. Implementing the words that way breaks the mechanism the words
    exist to protect.
    """
    with db.connection(database_url) as conn:
        run_id = _insert(conn, job_name="probe", status="running")
        conn.commit()

    with db.connection(database_url) as conn:
        conn.execute(
            "UPDATE job_runs SET status = 'success', finished_at = now(), rows_written = 42"
            " WHERE run_id = %s",
            (run_id,),
        )
        conn.commit()

    with db.connection(database_url) as conn:
        status, rows_written = conn.execute(
            "SELECT status, rows_written FROM job_runs WHERE run_id = %s", (run_id,)
        ).fetchone()

    assert status == "success"
    assert rows_written == 42


def test_a_human_can_delete_by_deliberately_disabling_the_trigger(migrated_db, database_url):
    """The escape hatch the contract describes, exercised so it is known to exist.

    CLAUDE.md § 4 permits a human a one-off correction with a stated reason. If the only way to
    make one were to drop the trigger permanently, the first correction would leave the table
    unprotected forever — so the hatch has to work, and has to be conspicuous.
    """
    with db.connection(database_url) as conn:
        run_id = _insert(conn, job_name="probe", status="success")
        conn.commit()

    with db.connection(database_url) as conn:
        conn.execute("ALTER TABLE job_runs DISABLE TRIGGER job_runs_forbid_delete")
        conn.execute("DELETE FROM job_runs WHERE run_id = %s", (run_id,))
        conn.execute("ALTER TABLE job_runs ENABLE TRIGGER job_runs_forbid_delete")
        conn.commit()

    with db.connection(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM job_runs").fetchone()[0] == 0
        # Protection is back on afterwards.
        with pytest.raises(psycopg.errors.RaiseException):
            _insert(conn, job_name="probe", status="success")
            conn.execute("DELETE FROM job_runs")
        conn.rollback()


def test_rows_written_column_distinguishes_null_from_zero_at_the_schema_level(
    migrated_db, database_url
):
    """No DEFAULT 0, no NOT NULL. Decision 9 has to hold in the column, not only in the decorator.

    A `NOT NULL DEFAULT 0` on this column would silently convert every "does not report a count"
    into "wrote nothing", and no amount of care in Python could recover the difference.
    """
    with db.connection(database_url) as conn:
        column = conn.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'job_runs' AND column_name = 'rows_written'"
        ).fetchone()

    assert column is not None, "job_runs has no rows_written column"
    is_nullable, column_default = column
    assert is_nullable == "YES"
    assert column_default is None
