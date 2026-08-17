"""Integration tier — the analog engine's research log is asserted from a fresh connection.

See tests/ingest/test_ingest_write_paths_commit.py for the audit behind this file. Measured 2026-08-17:
`app/analogs/engine.py:520`'s commit was deletable with all 58 tests in tests/analogs/ green,
because every one of them calls `engine.query(migrated_db, ...)` and reads back through
`migrated_db.execute(...)`.

WHAT IS LOST WHEN THIS ROW DOES NOT LAND IS THE DENOMINATOR. CLAUDE.md § 19 requires a refusal to
be recorded as a row precisely so that an engine refusing ninety-nine times in a hundred cannot
read as an engine that answers. A silently uncommitted `analog_queries` write does not lose an
estimate — it loses the refusals, preferentially, because refusals are the common case on this
dataset. The table left behind is the one § 19 exists to prevent: only the queries that produced an
answer.

The engine is entered here on its PERSISTING path (`persist=True`, its default). The API passes
`persist=False` by contract (§ 20) and that is a different property, guarded elsewhere.
"""

from datetime import date

import pytest

from app import db
from app.analogs import engine

pytestmark = pytest.mark.integration


def test_analog_query_row_visible_from_new_connection(migrated_db, database_url, seed_analogs):
    """`engine.query(persist=True)` writes one `analog_queries` row that must survive its writer.

    Enters through the CLI shape — `with db.connection() as conn: query(conn, ...)` — which is what
    `app/analogs/engine.py:640`'s `main()` does, committing nothing at the call site.

    Deliberately does NOT seed a passing history. A refusal is the expected outcome on this
    dataset and it is the row whose loss is invisible, so it is the one worth guarding.
    """
    as_of = date(2022, 9, 20)

    with db.connection(database_url) as conn:
        result = engine.query(conn, as_of=as_of, site_id="07032000")

    with db.connection(database_url) as conn:
        rows = conn.execute(
            "SELECT query_id, as_of_date, site_id, gate_result FROM analog_queries"
        ).fetchall()

    assert len(rows) == 1, (
        f"engine.query returned {result.gate.result!r} and a new connection sees {len(rows)} "
        f"analog_queries row(s), not 1. The research log was rolled back on close — which loses "
        f"REFUSALS preferentially, since refusals are the common case, leaving exactly the table "
        f"CLAUDE.md § 19 forbids: only the queries that produced an answer."
    )
    assert rows[0][1] == as_of and rows[0][2] == "07032000"
    assert rows[0][3] == result.gate.result, (
        f"the stored gate_result is {rows[0][3]!r} but the engine returned "
        f"{result.gate.result!r}"
    )
