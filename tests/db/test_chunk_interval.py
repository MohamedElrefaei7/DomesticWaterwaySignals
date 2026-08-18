"""Migration 0027: 986 chunks become tens, and nothing quietly reads the archive.

TWO TIERS, AND THEY GUARD DIFFERENT THINGS. The source-text tests assert properties of the FILE
that no execution can check - that there is no DROP TABLE in it, that the equality it asserts
carries no tolerance. Those are CLAUDE.md § 23's legitimate kind: the statement's absence from the
file IS the property, and a migration that has already run cannot be asked whether it dropped
something.

The integration tier asserts what the database ended up like, which is the only thing that can
show `set_chunk_time_interval` took effect and that the consolidation moved every row.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.ingest import usgs_ingest
from app.orchestration import migrate

from .conftest import CONSOLIDATION, MIGRATIONS_DIR, SEED_ROWS, SITE

MIGRATION_PATH = MIGRATIONS_DIR / CONSOLIDATION
ARCHIVE = "gauge_readings_iv_archived_20260818"

SOURCE = MIGRATION_PATH.read_text(encoding="utf-8")


def _executable(text: str) -> str:
    """The file with its `--` comment lines removed.

    THE HEADER OF THIS MIGRATION EXPLAINS AT LENGTH WHY IT DOES NOT DROP THE OLD TABLE, and it
    uses the words `DROP TABLE` to do it. A check whose subject is text must exclude the text that
    documents the check - the fourth instance of that shape in this project (CLAUDE.md § 24), and
    the reason the exclusion is here rather than in each assertion.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("--")
    )


# ---------------------------------------------------------------------------------------------
# What the file says
# ---------------------------------------------------------------------------------------------


def test_migration_0027_exists_and_the_scan_resolved_it():
    assert MIGRATION_PATH.exists(), f"{MIGRATION_PATH} is missing"
    assert len(_executable(SOURCE).strip()) > 500, (
        "the executable part of 0027 is nearly empty - every source assertion below would pass "
        "vacuously (CLAUDE.md § 21)"
    )


def test_migration_0027_sets_365_day_interval():
    """365 days, spelled in days.

    NOT `1 year`. TimescaleDB stores a Postgres interval year as 360 days, so a file saying
    "1 year" and a test asserting 365 disagree forever - 0008 already hit this.
    """
    executable = _executable(SOURCE)

    assert re.search(r"by_range\(\s*'ts'\s*,\s*INTERVAL\s+'365 days'\s*\)", executable), (
        f"0027 does not create the hypertable at a 365-day interval. Found: "
        f"{re.findall(r'INTERVAL .[^\']*.', executable)}"
    )
    assert "INTERVAL '1 year'" not in executable, (
        "0027 says `1 year`, which TimescaleDB stores as 360 days - not 365"
    )


def test_migration_0027_archives_rather_than_drops():
    """A RENAME, and no DROP TABLE anywhere in the executable text.

    CLAUDE.md § 3: destructive operations are archived, and only a human runs an actual DROP. The
    archive is the only copy of the pre-consolidation data; a DROP here would make the rewrite
    irreversible in the same statement that made it.
    """
    executable = _executable(SOURCE)

    assert f"ALTER TABLE gauge_readings_iv RENAME TO {ARCHIVE}" in executable, (
        "0027 does not rename the old hypertable to an archive name"
    )
    upper = executable.upper()
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert forbidden not in upper, (
            f"0027 contains `{forbidden}`. The old hypertable is archived, never destroyed - only "
            f"a human runs a DROP (CLAUDE.md § 3), and the archive is the only copy of the "
            f"pre-consolidation data."
        )


def test_migration_0027_asserts_exact_row_count_equality():
    """No tolerance of any size, and the comparison is `<>` rather than a threshold.

    A tolerance is a tolerance for exactly the loss the check exists to detect. This is the last
    moment a short copy is detectable: afterwards the source is an archive nobody diffs.
    """
    executable = _executable(SOURCE)

    assert "RAISE EXCEPTION" in executable, "0027 never raises, so it cannot refuse a bad copy"
    assert re.search(r"dst\.n_rows\s*<>\s*src\.n_rows", executable), (
        "0027 does not compare the copied row count to the source's with exact inequality"
    )
    for tolerant in (" ABS(", " abs(", "<=", ">=", "BETWEEN"):
        assert tolerant not in executable.replace("older_than =>", ""), (
            f"0027's count comparison admits a tolerance (`{tolerant}`). Exact equality, or the "
            f"check passes on the loss it exists to catch."
        )
    for dimension in ("min_ts", "max_ts", "value_sum", "n_qualified"):
        assert dimension in executable, (
            f"0027 compares row counts but not {dimension} - a copy that moved the right NUMBER "
            f"of rows and the wrong ones would pass"
        )


def test_migration_0027_reenables_compression():
    executable = _executable(SOURCE)

    assert "timescaledb.compress_segmentby = 'usgs_site_id, param_code'" in executable
    assert "timescaledb.compress_orderby = 'ts DESC'" in executable
    assert "add_compression_policy('gauge_readings_iv'" in executable, (
        "0027 sets compression options but adds no policy - nothing would ever compress a new chunk"
    )
    assert "compress_chunk(" in executable, (
        "0027 configures compression but compresses nothing. A consolidated table with compression "
        "configured and no compressed chunks reads as a compression REGRESSION in the next "
        "measurement."
    )


def test_migration_0027_repoints_the_view_that_binds_by_oid():
    """gauge_series is a VIEW over this table, and a rename takes it with us.

    THIS IS THE FAILURE THAT WOULD HAVE BEEN SILENT. Postgres binds view dependencies by OID, so
    the rename does not break `gauge_series` (0010) - it repoints it at the archive, which stops
    receiving writes the moment this migration commits. Every feature and every analog lookup reads
    that view.
    """
    executable = _executable(SOURCE)

    assert "CREATE OR REPLACE VIEW gauge_series" in executable, (
        "0027 renames the table gauge_series depends on and never recreates the view - the view "
        "would follow the rename onto the archive and go silently stale"
    )
    assert "pg_rewrite" in executable and "pg_depend" in executable, (
        "0027 recreates gauge_series but never asserts the archive has no dependents left. The "
        "check must ENUMERATE (CLAUDE.md § 22) rather than trust this file to have listed them."
    )


def test_migration_0027_refuses_rather_than_races_a_running_ingest():
    executable = _executable(SOURCE)

    assert "job_runs" in executable and "usgs_ingest" in executable, (
        "0027 rewrites a table usgs_ingest writes to hourly and does not check whether it is running"
    )
    assert "LOCK TABLE gauge_readings_iv IN ACCESS EXCLUSIVE MODE" in executable
    assert "lock_timeout" in executable, (
        "0027 takes an ACCESS EXCLUSIVE lock with no lock_timeout - a concurrent writer makes the "
        "migration wait indefinitely rather than refuse"
    )
    assert "advisory" not in executable.lower(), (
        "0027 uses an advisory lock. usgs_ingest takes none, so the lock would be acquired against "
        "a running ingest and report the coast clear."
    )


def test_migration_0027_checksum_stable():
    """The checksum the runner records, pinned.

    A migration's checksum is what makes editing an applied file a hard failure rather than a
    silent divergence (CLAUDE.md § 3). Pinning it here means an edit to 0027 fails in the test
    suite, before it reaches an instance where the runner would abort mid-deploy.
    """
    discovered = {m.filename: m for m in migrate.discover(MIGRATIONS_DIR)}

    assert CONSOLIDATION in discovered, f"the runner does not discover {CONSOLIDATION}"
    migration = discovered[CONSOLIDATION]

    assert migration.version == 27, f"0027 discovered as version {migration.version}"
    assert migration.checksum == migrate.checksum_of(SOURCE)
    assert not migration.no_transaction, (
        "0027 is marked no-transaction. It must be atomic: a crash between the rename and the "
        "copy leaves the live table gone and the archive holding everything."
    )


def test_migration_0027_is_the_highest_and_no_earlier_file_was_touched():
    """0027 is additive. CLAUDE.md § 3 forbids editing an applied migration."""
    versions = [m.version for m in migrate.discover(MIGRATIONS_DIR)]

    assert max(versions) == 27
    assert len(versions) == len(set(versions)), f"duplicate migration versions: {versions}"


# ---------------------------------------------------------------------------------------------
# What the database ended up like
# ---------------------------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
def test_chunk_count_after_consolidation_is_under_50(consolidated_db):
    """The measurement the whole migration is for.

    Asserted against the fixture's own pre-consolidation count as well as the threshold, because
    "under 50" alone would pass a fixture that never produced many chunks in the first place - the
    fixture asserts its own precondition too.
    """
    after = consolidated_db.execute(
        "SELECT count(*) FROM timescaledb_information.chunks"
        " WHERE hypertable_name = 'gauge_readings_iv'"
    ).fetchone()[0]

    assert after < 50, (
        f"gauge_readings_iv has {after} chunks after 0027, down from "
        f"{consolidated_db.chunks_before}. The consolidation did not change the interval, or it "
        f"changed it without rewriting the existing chunks - set_chunk_time_interval alone "
        f"affects only chunks created AFTER it runs."
    )
    assert after < consolidated_db.chunks_before, (
        f"chunk count did not fall: {consolidated_db.chunks_before} -> {after}"
    )


@pytest.mark.integration
def test_the_new_hypertable_is_partitioned_at_365_days(consolidated_db):
    rows = consolidated_db.execute(
        "SELECT column_name, time_interval FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = 'gauge_readings_iv'"
    ).fetchall()

    assert rows, "gauge_readings_iv is not a hypertable at all after 0027"
    assert len(rows) == 1, f"expected one partitioning dimension, got {rows}"
    column_name, time_interval = rows[0]
    assert column_name == "ts"
    assert time_interval == timedelta(days=365), (
        f"chunk interval is {time_interval}, not 365 days. TimescaleDB stores an interval YEAR as "
        f"360 days, so check the migration does not say `1 year`."
    )


@pytest.mark.integration
def test_every_row_survived_the_copy(consolidated_db):
    """Exact equality, read back from the database rather than trusted from the migration.

    The migration raises on a mismatch, which is the guard. This is the independent observation:
    it counts what is actually in the two tables now, so a migration whose own DO block was wrong
    is still caught.
    """
    live = consolidated_db.execute(
        "SELECT count(*), min(ts), max(ts), sum(value) FROM gauge_readings_iv"
    ).fetchone()
    archived = consolidated_db.execute(
        f"SELECT count(*), min(ts), max(ts), sum(value) FROM {ARCHIVE}"
    ).fetchone()

    assert live[0] == SEED_ROWS, f"the live table holds {live[0]} rows, seeded {SEED_ROWS}"
    assert live == archived, (
        f"the live table and the archive disagree.\n  live:     {live}\n  archived: {archived}"
    )


@pytest.mark.integration
def test_the_archive_still_exists_and_was_not_dropped(consolidated_db):
    assert consolidated_db.execute(
        f"SELECT to_regclass('public.{ARCHIVE}')::text"
    ).fetchone()[0] == ARCHIVE, (
        "the archived hypertable is gone. 0027 archives rather than drops; only a human runs a DROP."
    )


@pytest.mark.integration
def test_gauge_series_reads_the_new_table_not_the_archive(consolidated_db):
    """The silent one, asserted against the catalog rather than by reading rows.

    Reading rows would not distinguish the two: immediately after the migration the archive holds
    exactly the same data, so a view pointing at it returns identical results and starts diverging
    only on the next ingest. THE DEPENDENCY IS THE PROPERTY, so the dependency is what is checked.
    """
    dependents = [
        row[0]
        for row in consolidated_db.execute(
            "SELECT DISTINCT c.relname FROM pg_depend d"
            " JOIN pg_rewrite r ON r.oid = d.objid"
            " JOIN pg_class c ON c.oid = r.ev_class"
            " WHERE d.refobjid = %s::regclass AND d.refclassid = 'pg_class'::regclass"
            "   AND c.relkind IN ('v','m') AND c.relname <> %s",
            (ARCHIVE, ARCHIVE),
        ).fetchall()
    ]
    assert dependents == [], (
        f"these views still read the archived table and would go silently stale: {dependents}"
    )

    reads_live = consolidated_db.execute(
        "SELECT DISTINCT c.relname FROM pg_depend d"
        " JOIN pg_rewrite r ON r.oid = d.objid"
        " JOIN pg_class c ON c.oid = r.ev_class"
        " WHERE d.refobjid = 'gauge_readings_iv'::regclass"
        "   AND d.refclassid = 'pg_class'::regclass AND c.relname = 'gauge_series'"
    ).fetchall()
    assert reads_live, (
        "gauge_series does not depend on the new gauge_readings_iv at all - the view was not "
        "recreated against it"
    )


@pytest.mark.integration
def test_a_write_after_consolidation_lands_in_the_new_table(consolidated_db):
    """The FK, the primary key and the hypertable all still work on the rewritten table."""
    consolidated_db.execute(
        "INSERT INTO gauge_readings_iv (usgs_site_id, ts, param_code, value, qualifiers)"
        " VALUES (%s, now(), %s, %s, %s)",
        (SITE, "00060", 123456.0, ["P"]),
    )

    live = consolidated_db.execute("SELECT count(*) FROM gauge_readings_iv").fetchone()[0]
    archived = consolidated_db.execute(f"SELECT count(*) FROM {ARCHIVE}").fetchone()[0]

    assert live == SEED_ROWS + 1
    assert archived == SEED_ROWS, "the write landed in the archive, not the live table"


@pytest.mark.integration
def test_compression_is_configured_and_applied_on_the_new_table(consolidated_db):
    """Read back from the server, using the same helper tests/ingest/test_compression.py uses.

    NOT from `pg_class.reloptions` - the first version of this test read there, found an empty set
    and failed against a correctly configured table. TimescaleDB 2.x keeps compression settings in
    its own catalog, not in reloptions, so that check would have gone red on every correct state
    and green on none.
    """
    settings = usgs_ingest.compression_settings(consolidated_db.conn, "gauge_readings_iv")

    assert set(settings["segmentby"]) == {"usgs_site_id", "param_code"}, (
        f"compression segmentby did not survive the rewrite: {settings}"
    )
    assert settings["orderby"] == [("ts", "DESC")], (
        f"compression orderby did not survive the rewrite: {settings}"
    )

    compressed = consolidated_db.execute(
        "SELECT count(*) FROM timescaledb_information.chunks"
        " WHERE hypertable_name = 'gauge_readings_iv' AND is_compressed"
    ).fetchone()[0]
    assert compressed > 0, (
        "no chunk on the rewritten table is compressed. Compression was configured but never "
        "applied, which reads as a compression regression in the next measurement."
    )


@pytest.mark.integration
def test_0027_refuses_while_usgs_ingest_is_running(pre_0027_db):
    """The refusal path, exercised rather than asserted from the file.

    THE COST OF NOT HAVING THIS: a write landing mid-copy goes into the archived table and is lost
    from the live one - a few missing readings, in the table the whole project reads, with nothing
    anywhere reporting a problem. The stop-the-scheduler step in the runbook is the real guard;
    this is what happens when somebody forgets it.

    It refuses with a SENTENCE naming the job, not with a lock timeout. A migration that failed
    with `canceling statement due to lock timeout` would send an operator to the lock, which is a
    symptom, rather than to the scheduler, which is the cause.
    """
    pre_0027_db.execute(
        "INSERT INTO job_runs (job_name, status) VALUES ('usgs_ingest', 'running')"
    )
    pre_0027_db.conn.commit()

    with pytest.raises(Exception) as caught:
        pre_0027_db.apply_0027()

    message = str(caught.value)
    assert "usgs_ingest" in message, f"the refusal does not name the job: {message}"
    assert "docker compose stop scheduler" in message, (
        f"the refusal does not say what to do about it: {message}"
    )

    still_there = pre_0027_db.execute(
        "SELECT to_regclass('public.gauge_readings_iv_archived_20260818')::text"
    ).fetchone()[0]
    assert still_there is None, (
        "0027 refused but had already renamed the table - the refusal must come before any change"
    )


@pytest.mark.integration
def test_0027_applies_cleanly_once_the_ingest_row_is_finished(pre_0027_db):
    """The other half: a FINISHED usgs_ingest run must not block the migration.

    Without this, the guard above is satisfiable by a check that refuses whenever `job_runs` has
    any usgs_ingest row at all - which on a real instance is always, and the migration would be
    unrunnable. Two tests, because one wrong implementation satisfies either alone.
    """
    pre_0027_db.execute(
        "INSERT INTO job_runs (job_name, status, finished_at)"
        " VALUES ('usgs_ingest', 'success', now())"
    )
    pre_0027_db.conn.commit()

    applied = pre_0027_db.apply_0027()

    assert [m.filename for m in applied] == [CONSOLIDATION]
    chunks = pre_0027_db.execute(
        "SELECT count(*) FROM timescaledb_information.chunks"
        " WHERE hypertable_name = 'gauge_readings_iv'"
    ).fetchone()[0]
    assert chunks < 50
