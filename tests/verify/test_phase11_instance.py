"""Unit tier — the instance verifiers (Stages E-H), against simulated database and command output.

NO TEST HERE TOUCHES A LIVE INSTANCE. Every check takes rows or parsed command output as an
argument; the connection helper is exercised against a fake cursor.

The database rows here are TUPLES in the same column order the stages' own queries select, which is
the one place these tests are coupled to something they do not assert. That coupling is deliberate
and narrow: `read()` is the only place a query lives, so a change to a SELECT changes one function
and the fixtures beneath it.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify.phase11 import readonly, stage_e, stage_f, stage_g, stage_h  # noqa: E402
from verify.phase11.result import FAIL, PASS, Precondition  # noqa: E402

NOW = datetime(2026, 8, 18, 3, 5, tzinfo=timezone.utc)

PUBLIC_TABLES = ["public.backups", "public.job_runs", "public.schema_migrations",
                 "public.apscheduler_jobs"]
ROW_COUNTS = {name: 10 for name in PUBLIC_TABLES}


# ---------------------------------------------------------------------------------------------
# The read-only role, and the fallback that must not exist
# ---------------------------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, answers):
        self.answers = answers
        self.result = []

    def execute(self, sql, params=None):
        key = (params[0] if params else sql).replace("public.", "")
        self.result = self.answers.get(key, self.answers.get("*", [(None,)]))

    def fetchall(self):
        return self.result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, answers):
        self.answers = answers

    def cursor(self):
        return FakeCursor(self.answers)


def test_verifier_uses_read_only_role():
    """`API_DATABASE_URL`, and no fallback to `DATABASE_URL` anywhere in the resolution."""
    url = "postgresql://waterway_api:secret@localhost:5432/waterway"
    assert readonly.api_database_url({"API_DATABASE_URL": url}) == url

    # DATABASE_URL present and API_DATABASE_URL absent must still refuse. This is the case
    # app/api/dependencies.py deliberately handles the OTHER way, with a warning, and the
    # difference is the whole point: a verifier that connected as the owner would be reporting a
    # guarantee it was not holding.
    with pytest.raises(Precondition) as excinfo:
        readonly.api_database_url({"DATABASE_URL": "postgresql://waterway:x@localhost/waterway"})
    assert "API_DATABASE_URL is not set" in str(excinfo.value)
    assert "NO fallback" in str(excinfo.value)


def test_verifier_stops_when_read_only_role_lacks_select():
    """Exit 2 and no owner-connection fallback. The fallback is the tempting move.

    `backups` is the realistic case: the role was granted SELECT on all tables in Phase 8, before
    migration 0026 created it.
    """
    conn = FakeConn(
        {
            "job_runs": [(True, True)],
            "backups": [(True, False)],  # exists, not readable
            "schema_migrations": [(True, True)],
        }
    )
    with pytest.raises(Precondition) as excinfo:
        readonly.assert_select_granted(conn)

    message = str(excinfo.value)
    assert "lacks SELECT on: ['backups']" in message
    assert "DOES NOT FALL BACK" in message
    # It names the GRANT, so the human's decision is one paste rather than one recollection.
    assert "GRANT SELECT ON TABLE public.backups TO waterway_api;" in message


def test_a_table_that_does_not_exist_is_reported_separately_from_a_missing_grant():
    """Different causes, different remedies, different messages.

    An absent table means run the migration; an absent grant means run the GRANT. Collapsing them
    sends the operator to the wrong one.
    """
    conn = FakeConn(
        {
            "job_runs": [(True, True)],
            "backups": [(False, False)],
            "schema_migrations": [(True, True)],
        }
    )
    with pytest.raises(Precondition) as excinfo:
        readonly.assert_select_granted(conn)
    assert "do not exist: ['backups']" in str(excinfo.value)
    assert "migrations.run" in str(excinfo.value)


def test_all_grants_present_returns_cleanly():
    conn = FakeConn({"*": [(True, True)]})
    assert readonly.assert_select_granted(conn) == {
        "job_runs": True,
        "backups": True,
        "schema_migrations": True,
    }


# ---------------------------------------------------------------------------------------------
# Stage E
# ---------------------------------------------------------------------------------------------

REAL_PREFLIGHT_LINE = (
    "  - every image reference across docker-compose.yml, Dockerfile.api, Dockerfile.frontend, "
    "Dockerfile.scheduler was enumerated (8 found)\n"
)


def test_e_parses_preflight_rather_than_recounting():
    count, files = stage_e.parse_preflight_enumeration(REAL_PREFLIGHT_LINE)
    assert count == 8
    assert files == [
        "docker-compose.yml", "Dockerfile.api", "Dockerfile.frontend", "Dockerfile.scheduler"
    ]


def test_e_fails_when_preflight_count_is_not_the_measured_one():
    """EIGHT references across FOUR files, measured against this repo 2026-08-17 (Phase 12).

    It was six across three until Dockerfile.scheduler landed. Below the expected count means a
    reference is not being gated - § 22's gate 1 checking one reference out of five while
    reporting the stack as pinned. Above it means something arrived that needs a pin.

    RENAMED from `..._is_not_six`, and the rename is not cosmetic: a test named for a number it no
    longer asserts is a green check teaching the next reader a fact that stopped being true.
    """
    seven = REAL_PREFLIGHT_LINE.replace("(8 found)", "(7 found)")
    count, files = stage_e.parse_preflight_enumeration(seven)
    result = stage_e.check_preflight_enumeration(count, files)

    assert result.status == FAIL
    assert "7 references across 4 files" in result.observed
    assert "not being gated" in result.observed

    count, files = stage_e.parse_preflight_enumeration(REAL_PREFLIGHT_LINE)
    assert stage_e.check_preflight_enumeration(count, files).status == PASS


def test_e_stops_rather_than_guessing_when_preflight_output_changes_shape():
    """A count of zero would report the stack as having no image references at all."""
    with pytest.raises(Precondition) as excinfo:
        stage_e.parse_preflight_enumeration("preflight would run these gates:\n  - something\n")
    assert "could not find preflight's enumeration line" in str(excinfo.value)


def test_e_reads_the_real_preflight_output_and_agrees():
    """Against this repo, not against a fixture of it. The count is a fact about the tree."""
    count, files = stage_e.parse_preflight_enumeration(stage_e._preflight_output())
    assert (count, len(files)) == (8, 4), (count, files)
    assert stage_e.check_preflight_enumeration(count, files).status == PASS


def test_e_fails_when_running_image_id_differs_from_compose_digest():
    pinned = stage_e.compose_digests(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert set(pinned) == {"timescaledb", "caddy"}, pinned

    drifted = dict(pinned, caddy="sha256:" + "de" * 32)
    result = stage_e.check_no_image_was_repulled(drifted, pinned)

    assert result.status == FAIL
    assert "caddy: running sha256:dede" in result.observed
    assert "preflight.py gate 1 did not catch it" in result.observed

    assert stage_e.check_no_image_was_repulled(dict(pinned), pinned).status == PASS


def test_e_image_comparison_over_an_empty_set_is_not_a_pass():
    """A comparison with nothing to compare is watching nothing (CLAUDE.md § 22)."""
    result = stage_e.check_no_image_was_repulled({}, {"caddy": "sha256:aa"})
    assert result.status == FAIL
    assert "empty set" in result.observed


def test_e_writes_baseline_under_mnt_data_not_tmp():
    """`/tmp` is cleared on reboot and may be a tmpfs sized from RAM.

    A baseline that vanishes leaves Stages G and H comparing against nothing - and reporting that
    as zero, which reads like a full disk rather than like a missing file.
    """
    payload = {"free_bytes": 42_000_000_000, "taken_at": NOW.isoformat()}

    assert stage_e.check_baseline_was_written(stage_e.BASELINE_PATH, payload).status == PASS

    in_tmp = stage_e.check_baseline_was_written(
        Path("/tmp/phase11-verify-baseline.json"), payload
    )
    assert in_tmp.status == FAIL
    assert "not under /mnt/data" in in_tmp.observed
    assert "cleared on reboot" in in_tmp.observed


def test_e_baseline_must_carry_a_real_number():
    assert (
        stage_e.check_baseline_was_written(stage_e.BASELINE_PATH, {"free_bytes": 0}).status == FAIL
    )
    assert (
        stage_e.check_baseline_was_written(stage_e.BASELINE_PATH, {}).status == FAIL
    )


def test_e_handles_both_compose_ps_json_shapes():
    """Compose emits either a JSON array or one object per line, depending on version.

    Assuming one shape yields an empty list against the other, and an empty list is a check
    watching nothing rather than a check that fails.
    """
    array = json.dumps([{"Service": "api"}, {"Service": "caddy"}])
    lines = '{"Service": "api"}\n{"Service": "caddy"}\n'

    assert stage_e._json_lines(array) == stage_e._json_lines(lines)
    assert len(stage_e._json_lines(array)) == 2
    assert stage_e._json_lines("") == []


# ---------------------------------------------------------------------------------------------
# Stage F
# ---------------------------------------------------------------------------------------------


def test_f_fails_when_migration_count_is_not_26():
    low = stage_f.check_migration_count(25)
    assert low.status == FAIL
    assert "25 applied" in low.observed
    assert "migrations.run" in low.observed

    assert stage_f.check_migration_count(27).status == FAIL
    assert stage_f.check_migration_count(26).status == PASS


def test_f_migration_count_matches_the_files_on_disk():
    """26 is a fact about this repo, not a number somebody remembered."""
    on_disk = sorted((REPO_ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert len(on_disk) == stage_f.EXPECTED_MIGRATIONS, [p.name for p in on_disk]
    assert on_disk[-1].name.startswith("0026_")


def test_f_asserts_trigger_exists_without_attempting_a_write():
    """The behavioural proof is the human's F3 step; this reads pg_trigger.

    Proving the trigger WORKS means attempting an UPDATE, which is a write, and this verifier
    connects as a role that cannot make one. What it can establish is a different and real fact:
    the trigger is installed and enabled.
    """
    both = [("backups_forbid_delete", "O"), ("backups_forbid_update", "O")]
    assert stage_f.check_triggers_exist_and_are_enabled(both).status == PASS

    missing = stage_f.check_triggers_exist_and_are_enabled([("backups_forbid_update", "O")])
    assert missing.status == FAIL
    assert "missing: ['backups_forbid_delete']" in missing.observed


def test_f_fails_when_a_trigger_is_present_but_disabled():
    """§ 3 permits a human to disable the delete trigger, "which is a visible act".

    The visible part only works if something looks. A disabled trigger is exactly as protective as
    an absent one and reads as present in any query that only counts rows.
    """
    result = stage_f.check_triggers_exist_and_are_enabled(
        [("backups_forbid_delete", "D"), ("backups_forbid_update", "O")]
    )
    assert result.status == FAIL
    assert "DISABLED" in result.observed
    assert "backups_forbid_delete (tgenabled='D')" in result.observed


def test_f_fails_when_the_backups_table_is_absent():
    result = stage_f.check_backups_table_exists(None)
    assert result.status == FAIL
    assert "schema_migrations and the schema disagree" in result.observed

    assert stage_f.check_backups_table_exists("backups").status == PASS


def test_f_makes_no_write_statement():
    """Structural: no stage module contains an INSERT, UPDATE or DELETE.

    The role could not execute one anyway - that is the point of connecting as `waterway_api` - but
    a statement that never runs is exactly as much of a violation as one that does, because the
    property being protected is that these stages have no write path at all (§ 23).
    """
    forbidden = ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE", "DROP ", "ALTER TABLE")
    for module in ("stage_e.py", "stage_f.py", "stage_g.py", "stage_h.py", "readonly.py"):
        source = (REPO_ROOT / "verify" / "phase11" / module).read_text(encoding="utf-8")
        # Only the executable statements - the docstrings discuss these words on purpose.
        code = "\n".join(
            line for line in source.splitlines() if "query(" in line or "execute(" in line
        )
        for keyword in forbidden:
            assert keyword not in code.upper(), f"{module} appears to issue {keyword}"


# ---------------------------------------------------------------------------------------------
# Stage G
# ---------------------------------------------------------------------------------------------


def _backup_row(backup_id=1, key="backups/daily/2026-08-18.dump", counts=None):
    return (backup_id, "dws-backups-0", key, 1_234_567, counts or ROW_COUNTS, True, NOW)


def test_g_fails_when_apscheduler_jobs_absent():
    """A real ordering dependency on a rebuilt instance (§ 12)."""
    result = stage_g.check_apscheduler_jobs_exists(None)

    assert result.status == FAIL
    assert "FIRST START" in result.observed
    assert "says nothing about scheduler startup ordering" in result.observed
    assert stage_g.check_apscheduler_jobs_exists("apscheduler_jobs").status == PASS


def test_g_fails_when_rows_written_is_zero_not_null():
    """`0` and `NULL` are different claims (§ 4, § 12).

    The backup writes to S3, so it writes no rows to THIS database. Accepting 0 makes the column
    mean two things depending on which job wrote it.
    """
    zero = stage_g.check_rows_written_is_null([(NOW, 0)])
    assert zero.status == FAIL
    assert "rows_written=0" in zero.observed
    assert "two things" in zero.observed

    assert stage_g.check_rows_written_is_null([(NOW, None)]).status == PASS


def test_g_fails_when_no_successful_backup_run_exists():
    result = stage_g.check_rows_written_is_null([])
    assert result.status == FAIL
    assert "most recent" in result.observed


def test_g_fails_when_row_counts_keys_differ_from_table_set():
    """Both directions, every mismatch reported (§ 3)."""
    dropped = stage_g.check_row_counts_keys_match_tables(
        {k: 1 for k in PUBLIC_TABLES if k != "public.backups"}, PUBLIC_TABLES
    )
    assert dropped.status == FAIL
    assert "absent from row_counts: ['public.backups']" in dropped.observed

    extra = stage_g.check_row_counts_keys_match_tables(
        dict(ROW_COUNTS, **{"public.gone": 0}), PUBLIC_TABLES
    )
    assert extra.status == FAIL
    assert "no such table: ['public.gone']" in extra.observed

    assert stage_g.check_row_counts_keys_match_tables(ROW_COUNTS, PUBLIC_TABLES).status == PASS


def test_g_row_counts_must_be_a_mapping_not_a_total():
    """§ 3: a total cannot distinguish "one table lost rows and another gained some"."""
    result = stage_g.check_row_counts_keys_match_tables(29650, PUBLIC_TABLES)
    assert result.status == FAIL
    assert "not an object" in result.observed


def test_g_row_counts_keys_are_schema_qualified_like_the_backup_writes_them():
    """app/orchestration/backup.py:249 writes `public.<tablename>`.

    An unqualified comparison reports every table as both missing and unexpected, which reads as a
    catastrophic mismatch and is a bug in the verifier.
    """
    from app.orchestration import backup

    assert backup.COUNTED_SCHEMA == stage_g.COUNTED_SCHEMA
    unqualified = {name.split(".", 1)[1]: 1 for name in PUBLIC_TABLES}
    assert stage_g.check_row_counts_keys_match_tables(unqualified, PUBLIC_TABLES).status == FAIL


def test_g_fails_when_no_backup_row_exists():
    """The Stage B defect: a successful job, a verified archive, and no row."""
    result = stage_g.check_a_backup_row_exists([])
    assert result.status == FAIL
    assert "Stage B defect" in result.observed
    assert "session.writing()" in result.observed


def test_g_fails_when_a_backup_key_is_outside_the_retained_prefixes():
    """backups.tf's lifecycle rules only match `backups/`; anything else is never expired."""
    result = stage_g.check_backup_keys_are_under_the_retained_prefix(
        [_backup_row(key="scratch/manual-dump.sql")]
    )
    assert result.status == FAIL
    assert "never expired" in result.observed

    assert (
        stage_g.check_backup_keys_are_under_the_retained_prefix([_backup_row()]).status == PASS
    )


# ---------------------------------------------------------------------------------------------
# Stage H
# ---------------------------------------------------------------------------------------------

COMPOSE_CONTAINERS = [
    "inland-waterway-signals-timescaledb-1",
    "inland-waterway-signals-api-1",
    "inland-waterway-signals-caddy-1",
    "inland-waterway-signals-frontend-build-1",
]


def test_h_fails_when_throwaway_container_remains():
    """Looked for across every container on the host - Compose cannot see it.

    It is created by `docker run` with a random suffix, so it is not a Compose service.
    """
    leaked = COMPOSE_CONTAINERS + ["dws-restore-test-9f2a1b0c4d5e"]
    result = stage_h.check_throwaway_is_gone(leaked)

    assert result.status == FAIL
    assert "dws-restore-test-9f2a1b0c4d5e" in result.observed
    assert "ROOT disk" in result.observed

    assert stage_h.check_throwaway_is_gone(COMPOSE_CONTAINERS).status == PASS


def test_h_throwaway_prefix_matches_the_job_that_creates_it():
    """Read from restore_test.py rather than remembered - two copies of one string drift."""
    from app.orchestration import restore_test

    assert stage_h.THROWAWAY_PREFIX == restore_test.CONTAINER_PREFIX


def test_h_fails_when_container_set_is_a_superset():
    """Exact sets in both directions, not containment.

    Containment passes while something unexpected sits beside the expected services - which is the
    case a leaked container produces.
    """
    result = stage_h.check_service_sets(
        ["timescaledb", "api", "caddy", "debug-shell"],
        ["timescaledb", "api", "caddy", "frontend-build", "debug-shell"],
    )
    assert result.status == FAIL
    assert "unexpected=['debug-shell']" in result.observed


def test_h_expects_three_running_services_and_four_in_total():
    """`frontend-build` EXITS BY DESIGN, so it is in one set and not the other.

    `restart: "no"` and caddy's `service_completed_successfully` gate. An exact-set check over four
    RUNNING services would fail on every correct instance, and a containment check over four would
    pass while one was missing.
    """
    assert stage_h.RUNNING_SERVICES == {"timescaledb", "api", "caddy"}
    assert stage_h.ALL_SERVICES == {"timescaledb", "api", "caddy", "frontend-build"}

    ok = stage_h.check_service_sets(
        ["timescaledb", "api", "caddy"],
        ["timescaledb", "api", "caddy", "frontend-build"],
    )
    assert ok.status == PASS

    # frontend-build still running is a failure: it is supposed to have exited.
    still_running = stage_h.check_service_sets(
        ["timescaledb", "api", "caddy", "frontend-build"],
        ["timescaledb", "api", "caddy", "frontend-build"],
    )
    assert still_running.status == FAIL


def test_h_service_sets_match_the_compose_file():
    """The four service names are a fact about docker-compose.yml, not a memory of it."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    import re

    services_block = compose.split("\nservices:", 1)[1].split("\nvolumes:", 1)[0]
    declared = set(re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", services_block, re.MULTILINE))
    assert declared == stage_h.ALL_SERVICES, declared


def test_h_fails_when_the_one_shot_build_exited_nonzero():
    result = stage_h.check_one_shot_exited_cleanly({"frontend-build": 1})
    assert result.status == FAIL
    assert "404s" in result.observed

    assert stage_h.check_one_shot_exited_cleanly({"frontend-build": 0}).status == PASS
    assert stage_h.check_one_shot_exited_cleanly({}).status == FAIL


def _h_row(backup_id, key, verified_at, counts=None):
    return (backup_id, "dws-backups-0", key, verified_at, counts)


def test_h_fails_when_verification_mark_is_on_the_test_row():
    """Verifying the WRONG row is the failure this catches.

    `backups` legitimately holds a probe row a human inserted during Stage F's trigger check, and
    "the most recent row" is one ORDER BY away from being that probe.
    """
    rows = [
        _h_row(2, "verification/f3-trigger-probe", NOW, ROW_COUNTS),
        _h_row(1, "backups/daily/2026-08-18.dump", None, None),
    ]
    result = stage_h.check_mark_is_on_the_most_recent_real_backup(rows)

    assert result.status == FAIL
    assert "not backups" in result.observed
    assert "verification/f3-trigger-probe" in result.observed
    assert "verified the wrong row" in result.observed


def test_h_passes_when_the_mark_is_on_the_newest_real_backup():
    rows = [
        _h_row(2, "verification/f3-trigger-probe", None, None),
        _h_row(1, "backups/daily/2026-08-18.dump", NOW, ROW_COUNTS),
    ]
    result = stage_h.check_mark_is_on_the_most_recent_real_backup(rows)

    assert result.status == PASS
    assert "backup_id=1" in result.observed
    assert "1 non-backup row(s), none marked" in result.observed


def test_h_fails_when_the_newest_real_backup_is_unmarked():
    rows = [_h_row(3, "backups/daily/2026-08-18.dump", None, None)]
    result = stage_h.check_mark_is_on_the_most_recent_real_backup(rows)
    assert result.status == FAIL
    assert "restore_verified_at IS NULL" in result.observed


def test_h_fails_when_a_mark_carries_no_counts():
    """A mark with no counts beside it is a claim with no evidence.

    The per-table counts ARE the restore test (§ 3); a timestamp alone says somebody ran something.
    """
    rows = [_h_row(3, "backups/daily/2026-08-18.dump", NOW, None)]
    result = stage_h.check_mark_is_on_the_most_recent_real_backup(rows)
    assert result.status == FAIL
    assert "no evidence" in result.observed


def test_h_fails_when_there_is_no_real_backup_at_all():
    rows = [_h_row(1, "verification/f3-trigger-probe", None, None)]
    result = stage_h.check_mark_is_on_the_most_recent_real_backup(rows)
    assert result.status == FAIL
    assert "no real backup" in result.observed


def test_h_fails_when_the_restore_job_has_no_success():
    result = stage_h.check_restore_job_succeeded([])
    assert result.status == FAIL
    assert "most recent SUCCESS row" in result.observed

    assert stage_h.check_restore_job_succeeded([(NOW, None)]).status == PASS
    assert stage_h.check_restore_job_succeeded([(NOW, 0)]).status == FAIL
