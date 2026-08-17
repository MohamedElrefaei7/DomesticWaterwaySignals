"""Integration tier — the constraints `backups` carries in the database itself (migration 0026).

Same reasoning as test_job_runs_constraints.py: the point of putting insert-once in the schema
rather than in a Python guard is that it also binds the psql session where a human is repairing
something at 2am, and a Python-side check is invisible to that session by construction.

`backups` is the evidentiary record of whether this database is RESTORABLE. job_runs says whether
the backup job ran; a green job_runs row over an archive nobody has restored is CLAUDE.md § 2's
theme 1 exactly. An audit table anything can quietly rewrite is not an audit table, so the
enforcement is structural.
"""

import json

import psycopg
import pytest

from app import db

pytestmark = pytest.mark.integration


MINIMAL = {
    "s3_bucket": "dws-backups-test",
    "s3_key": "backups/daily/2026-08-17T02-00-00Z.dump",
    "byte_size": 12_345_678,
    "row_counts": json.dumps({"public.job_runs": 41, "public.gauges": 4}),
    "compressed_chunks": 17,
    "verified": True,
    "verified_at": "2026-08-17T02:04:00Z",
}


def _insert(conn, **overrides):
    row = {**MINIMAL, **overrides}
    columns = ", ".join(row)
    placeholders = ", ".join(["%s"] * len(row))
    return conn.execute(
        f"INSERT INTO backups ({columns}) VALUES ({placeholders}) RETURNING backup_id",
        tuple(row.values()),
    ).fetchone()[0]


def test_backups_insert_succeeds(migrated_db, database_url):
    """The positive case, or every refusal below could hold for the wrong reason."""
    with db.connection(database_url) as conn:
        backup_id = _insert(conn)
        assert backup_id is not None

        stored = conn.execute(
            "SELECT byte_size, verified, row_counts FROM backups WHERE backup_id = %s",
            (backup_id,),
        ).fetchone()
        assert stored[0] == MINIMAL["byte_size"]
        assert stored[1] is True
        assert stored[2] == {"public.job_runs": 41, "public.gauges": 4}
        conn.rollback()


def test_backups_update_restore_verified_at_allowed(migrated_db, database_url):
    """The three columns the restore test fills in are the three the trigger permits."""
    with db.connection(database_url) as conn:
        backup_id = _insert(conn)

        conn.execute(
            "UPDATE backups SET restore_verified_at = now(), "
            "restore_verified_counts = %s, restore_notes = %s WHERE backup_id = %s",
            (json.dumps({"public.job_runs": 41}), "restored in 41s", backup_id),
        )

        marked = conn.execute(
            "SELECT restore_verified_at, restore_notes FROM backups WHERE backup_id = %s",
            (backup_id,),
        ).fetchone()
        assert marked[0] is not None
        assert marked[1] == "restored in 41s"
        conn.rollback()


def test_backups_update_byte_size_raises(migrated_db, database_url):
    """The message NAMES THE COLUMN.

    This is why the trigger compares column by column rather than `OLD IS DISTINCT FROM NEW` on
    the whole row. The whole-row form is one line and cannot say which column changed, so the
    error it raises sends the reader to diff two rows by hand at exactly the moment they are
    already confused about why an update failed.
    """
    with db.connection(database_url) as conn:
        backup_id = _insert(conn)

        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            conn.execute(
                "UPDATE backups SET byte_size = 1 WHERE backup_id = %s", (backup_id,)
            )
        conn.rollback()

    message = str(excinfo.value)
    assert "byte_size" in message, f"the error does not name the offending column: {message}"
    assert "insert-once" in message


def test_backups_update_s3_key_raises(migrated_db, database_url):
    """And an update that ALSO touches a permitted column is still refused.

    The tempting trigger short-circuits once it sees a permitted column among the changes -
    "restore_verified_at is being set, so this is the restore test, so allow it". That version
    lets the restore test rewrite the archive's location while marking it verified, which is the
    one edit that would make this table lie about which object was checked.
    """
    with db.connection(database_url) as conn:
        backup_id = _insert(conn)

        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            conn.execute(
                "UPDATE backups SET s3_key = %s, restore_verified_at = now() "
                "WHERE backup_id = %s",
                ("backups/daily/someone-elses.dump", backup_id),
            )
        conn.rollback()

    assert "s3_key" in str(excinfo.value)


def test_backups_delete_raises(migrated_db, database_url):
    """Silent deletability of an audit table is how the audit stops being one."""
    with db.connection(database_url) as conn:
        backup_id = _insert(conn)

        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            conn.execute("DELETE FROM backups WHERE backup_id = %s", (backup_id,))
        conn.rollback()

    message = str(excinfo.value)
    assert "append-only" in message
    assert "DISABLE TRIGGER" in message, (
        "the error does not tell a human how to make the correction the contract allows"
    )


def test_backups_verified_true_requires_verified_at(migrated_db, database_url):
    """"Verified but we don't know when" should be unrepresentable."""
    with db.connection(database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            _insert(conn, verified=True, verified_at=None)
        conn.rollback()

    assert "backups_verified_requires_timestamp" in str(excinfo.value)


def test_backups_row_counts_is_jsonb_object(migrated_db, database_url):
    """PER-TABLE COUNTS, NEVER A TOTAL.

    A total cannot distinguish "one table lost its rows and another gained some" from "identical",
    and per-table exactness is the entire value of the monthly restore test. Rejecting a scalar
    here is what stops a future job from writing the cheaper thing.
    """
    with db.connection(database_url) as conn:
        for not_an_object in ("41", '"forty-one"', "[1, 2, 3]"):
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
                _insert(conn, row_counts=not_an_object)
            conn.rollback()
            assert "backups_row_counts_is_object" in str(excinfo.value)

        # And the restore test's own counts are held to the same shape.
        backup_id = _insert(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE backups SET restore_verified_counts = %s WHERE backup_id = %s",
                ("41", backup_id),
            )
        conn.rollback()


def test_backups_row_counts_not_null(migrated_db, database_url):
    """No nullable column whose null would read as "we didn't check" and be treated as fine."""
    with db.connection(database_url) as conn:
        for column in ("row_counts", "byte_size", "verified", "compressed_chunks"):
            with pytest.raises(psycopg.errors.NotNullViolation):
                _insert(conn, **{column: None})
            conn.rollback()
