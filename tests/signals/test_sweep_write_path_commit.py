"""Integration tier — the sweep's writes are asserted from a connection it did not use.

See tests/ingest/test_ingest_write_paths_commit.py for the audit behind this file. Measured 2026-08-17:
BOTH of the sweep's commits — `open_run` at app/signals/sweep.py:524 and `run` at :635 — were
deletable with all 32 tests in tests/signals/ green, because every one of them calls
`sweep.run(migrated_db, ...)` and reads back through `migrated_db.execute(...)`.

TWO COMMITS, TWO TESTS, AND THE SECOND ONE HAD TO BE REWRITTEN TO BE WORTH ANYTHING.

`run` commits the scanned rows at the end; `open_run` commits the `signal_runs` row BEFORE the scan
starts, for the same reason the @job decorator commits its `running` row before the work — a scan
that dies halfway must leave evidence it was attempted, or a crash is indistinguishable from a
sweep nobody ran.

The first version of `test_sweep_rows_visible_from_new_connection` claimed to guard both by
asserting the run row and the scanned rows in the final state. IT DID NOT: deleting open_run's
commit left it green, because `run`'s own commit at the end commits the run row too. Removing an
early commit does not lose the row, it only DELAYS it, and no assertion made after a successful run
can see the difference. The mutation is what said so — the reasoning above was already written in
this docstring and was still wrong about which line it covered.

So the second test kills the scan instead. A run that raises between the two commits can only leave
a `signal_runs` row behind if the first commit really happened, which is the property, stated as
the only condition that distinguishes the two.

WHY IT MATTERS MORE HERE THAN ANYWHERE ELSE. CLAUDE.md § 18: the table of scanned pairs IS the
multiple-comparisons record, and the denominator is the whole point. A sweep whose rows are rolled
back does not report an error — it reports a completed run, and the next reader of `signals` finds
whatever an earlier run left, with a `grid_size` describing an experiment that is not the one whose
rows they are reading.
"""

import pytest

from app import db
from app.signals import regimes, sweep

pytestmark = pytest.mark.integration

FIXED_GIT = ("0" * 40, False)
LAG_MIN, LAG_MAX = -7, 7
HORIZONS = (7,)


def _kwargs():
    return {
        "lag_min": LAG_MIN,
        "lag_max": LAG_MAX,
        "horizons": HORIZONS,
        "regimes": regimes.REGIMES,
        "git": FIXED_GIT,
    }


def _run_ids(database_url):
    """Every signal_runs run_id, read on a connection of its own."""
    with db.connection(database_url) as conn:
        return {row[0] for row in conn.execute("SELECT run_id FROM signal_runs").fetchall()}


def test_sweep_rows_visible_from_new_connection(migrated_db, database_url, sweepable):
    """The scanned rows and their run row must both outlive the writer's connection.

    Enters through the CLI shape — `with db.connection() as conn: run(conn, ...)` — because that is
    what `app/signals/sweep.py:754`'s `main()` does, and it commits nothing at the call site.
    """
    with db.connection(database_url) as conn:
        result = sweep.run(conn, **_kwargs())

    run_id = result["run_id"]
    assert result["grid_size"] > 0, (
        f"the sweep scanned {result['grid_size']} pairs, so this test proves nothing about commits"
    )

    with db.connection(database_url) as conn:
        run_row = conn.execute(
            "SELECT run_id, grid_size FROM signal_runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        scanned = conn.execute(
            "SELECT count(*) FROM signals WHERE run_id = %s", (run_id,)
        ).fetchone()[0]

    # NOT A GUARD ON open_run's COMMIT. A successful run commits this row at the end regardless,
    # so this assertion is satisfied by sweep.py:635 alone — measured. The guard on the early
    # commit is the test below, which is the only shape that can tell the two apart.
    assert run_row is not None, (
        f"the sweep reported run_id {run_id} and a new connection finds no signal_runs row at all"
    )

    # run's commit.
    assert scanned == result["grid_size"], (
        f"the sweep reported a grid of {result['grid_size']} scanned pairs and a new connection "
        f"sees {scanned} rows for run {run_id}. The scanned rows were rolled back on close — and "
        f"the scanned rows ARE the multiple-comparisons record (CLAUDE.md § 18), so what survives "
        f"is a run row whose grid_size describes an experiment whose rows are gone."
    )


def test_sweep_run_row_survives_a_scan_that_dies(
    migrated_db, database_url, sweepable, monkeypatch
):
    """A sweep that raises inside `scan` still leaves its `signal_runs` row behind.

    THE ONLY SHAPE THAT REACHES `open_run`'s COMMIT. On a run that completes, the final commit at
    sweep.py:635 writes the run row too, so deleting the early commit changes nothing an
    after-the-fact assertion can observe. Killing the scan is what makes the early commit the sole
    thing standing between a crash and no evidence.

    The same reasoning as verify/failure_survives.py for the @job decorator, and the same reasoning
    migration 0022 records beside `finished_at`: a row with `finished_at IS NULL` and no `signals`
    rows IS the evidence that a sweep was attempted and died. Without it, a crashed sweep and a
    sweep nobody ran are the same absence.
    """

    class ScanDied(RuntimeError):
        pass

    def exploding_scan(*args, **kwargs):
        raise ScanDied("the scan died partway, as this test requires")

    monkeypatch.setattr(sweep, "scan", exploding_scan)

    before = _run_ids(database_url)

    with pytest.raises(ScanDied):
        with db.connection(database_url) as conn:
            sweep.run(conn, **_kwargs())

    after = _run_ids(database_url)
    new = after - before

    assert len(new) == 1, (
        f"a sweep died inside scan() and a new connection sees {len(new)} new signal_runs row(s), "
        f"not 1. open_run's commit (sweep.py:524) is not reaching the database, so the run row "
        f"was rolled back along with the failure — and a sweep that crashed is then "
        f"indistinguishable from a sweep nobody ran."
    )

    run_id = new.pop()
    with db.connection(database_url) as conn:
        finished_at, scanned = conn.execute(
            "SELECT r.finished_at, (SELECT count(*) FROM signals s WHERE s.run_id = r.run_id)"
            " FROM signal_runs r WHERE r.run_id = %s",
            (run_id,),
        ).fetchone()

    assert finished_at is None, (
        f"the surviving run row for the crashed sweep has finished_at={finished_at!r}. An "
        f"unfinished run must be distinguishable from a completed one."
    )
    assert scanned == 0, f"the crashed sweep left {scanned} signals rows behind"
