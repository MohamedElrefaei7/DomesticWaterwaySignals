"""Stage H — after the first `restore_test_monthly`: the throwaway is gone and the right row is marked.

    python3 -m verify.phase11 h

"EXACTLY THE PRODUCTION SERVICES" IS WRONG BY ONE, AND THE ONE MATTERS.

`docker-compose.yml` defines five services as of Phase 12, but `frontend-build` carries
`restart: "no"` and is gated by `service_completed_successfully` - it EXITS BY DESIGN once it has
written the bundle into the `frontend_dist` volume. So the RUNNING set is one smaller than the
declared set, and a containment check over the declared set would fail on every correct instance
while an exact-set check over it would fail too. Both are asserted separately and both are exact
sets:

    running   == {timescaledb, api, caddy, scheduler}
    all       == running | {frontend-build}, with frontend-build exited 0

`scheduler` joined the RUNNING set in Phase 12. It is the process that runs every job, and before
it existed the scheduler had never run in production at all - `job_runs` held two verify/ probe
rows and `apscheduler_jobs` held zero. A stage that did not expect it here would report the correct
instance as wrong, which trains its own removal.

An exited-nonzero `frontend-build` is its own failure: caddy's `service_completed_successfully`
gate means the bundle is what it served, so a build that failed and a build that never ran look
identical from the outside - a 404 from a correctly-running site.

THE THROWAWAY IS LOOKED FOR ACROSS EVERY CONTAINER ON THE HOST, not just Compose's. It is created
by `docker run` with a random-suffixed name (`app/orchestration/restore_test.py`'s
`dws-restore-test-`), so it is not a Compose service and `docker compose ps` cannot see it. A
leaked one holds a copy of the production database on the root disk under a name nobody will
recognise in a month.

THE VERIFICATION MARK IS CHECKED ON THE RIGHT ROW, and checking the wrong row is the failure this
stage catches. Stage F's F3 step has a human insert a probe row into `backups` and watch the update
trigger refuse - so `backups` legitimately contains a row that is not a backup. `restore_verified_at`
must be set on the most recent REAL backup and NOT on the probe. Real is not a convention invented
here: backups.tf's lifecycle rules match `backups/daily/` and `backups/monthly/`, so an object
outside `backups/` is one no retention rule reaches, and Stage G already refuses those.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from verify.phase11 import readonly, shell
from verify.phase11.result import Check, CheckResult, Precondition, failed, passed
from verify.phase11.stage_g import RETAINED_PREFIX

RESTORE_JOB = "restore_test_monthly"

# app/orchestration/restore_test.py:59
THROWAWAY_PREFIX = "dws-restore-test-"

# docker-compose.yml. `frontend-build` is deliberately in one set and not the other; see the
# module docstring.
RUNNING_SERVICES = frozenset({"timescaledb", "api", "caddy", "scheduler"})
ONE_SHOT_SERVICES = frozenset({"frontend-build"})
ALL_SERVICES = RUNNING_SERVICES | ONE_SHOT_SERVICES


def check_throwaway_is_gone(container_names: Sequence[str]) -> CheckResult:
    """No `dws-restore-test-*` container survives, running or exited.

    `docker rm -f` runs from a `finally` that survives KeyboardInterrupt (§ 3), so a survivor means
    that path did not run - and the container holds a restored copy of the production database on
    the root disk.
    """
    leaked = sorted(name for name in container_names if name.startswith(THROWAWAY_PREFIX))
    name = "no restore-test throwaway container remains"
    expected = f"0 containers named {THROWAWAY_PREFIX}*"
    if leaked:
        return failed(
            name,
            expected,
            f"{len(leaked)} still present: {leaked}. Each holds a restored copy of the production "
            f"database on the ROOT disk. The teardown is a `finally` that survives "
            f"KeyboardInterrupt, so a survivor means that path did not run.",
        )
    return passed(name, expected, f"0 of {len(container_names)} containers match")


def check_service_sets(running: Sequence[str], present: Sequence[str]) -> CheckResult:
    """Two exact sets, not one containment check.

    Containment passes while a leaked container sits beside the expected ones, and it also passes
    while a service is missing if the check is written the other way round. Exact equality in both
    directions is the same discipline as the ufw port set (§ 11) and the published-port set (§ 22).
    """
    name = "exactly the expected Compose services, running and one-shot"
    expected = f"running == {sorted(RUNNING_SERVICES)}; all == {sorted(ALL_SERVICES)}"

    running_set = set(running)
    present_set = set(present)
    problems = []

    if running_set != RUNNING_SERVICES:
        problems.append(
            f"running is {sorted(running_set)}; "
            f"unexpected={sorted(running_set - RUNNING_SERVICES)} "
            f"absent={sorted(RUNNING_SERVICES - running_set)}"
        )
    if present_set != ALL_SERVICES:
        problems.append(
            f"all is {sorted(present_set)}; "
            f"unexpected={sorted(present_set - ALL_SERVICES)} "
            f"absent={sorted(ALL_SERVICES - present_set)}"
        )
    if problems:
        return failed(
            name,
            expected,
            "; ".join(problems)
            + ". `frontend-build` exits by design (restart: \"no\", gated by "
            "service_completed_successfully), so it belongs in the second set and not the first.",
        )
    return passed(name, expected, f"running={sorted(running_set)} all={sorted(present_set)}")


def check_one_shot_exited_cleanly(exit_codes: dict[str, int]) -> CheckResult:
    name = "frontend-build exited 0"
    expected = "every one-shot service with exit code 0"
    bad = {service: code for service, code in exit_codes.items() if code != 0}
    if bad:
        return failed(
            name,
            expected,
            f"{bad}. caddy's `service_completed_successfully` gate means the bundle is what it "
            f"serves, so a failed build and a build that never ran both surface as 404s from a "
            f"site that is otherwise perfectly healthy.",
        )
    if not exit_codes:
        return failed(name, expected, "no one-shot service was observed at all")
    return passed(name, expected, f"{exit_codes}")


def check_mark_is_on_the_most_recent_real_backup(rows: Sequence[tuple]) -> CheckResult:
    """`restore_verified_at` set on the newest real backup, and on no probe row.

    Verifying the WRONG row is the failure this catches, and it is easy to reach: `backups` holds a
    probe row a human inserted during Stage F's trigger check, and "the most recent row" is one
    `ORDER BY backup_id DESC LIMIT 1` away from being that probe.
    """
    name = "the restore mark is on the most recent real backup, not on a probe row"
    expected = (
        f"restore_verified_at set on the newest s3_key under {RETAINED_PREFIX!r}, "
        f"and NULL on every row outside it"
    )
    if not rows:
        return failed(name, expected, "0 rows in backups")

    real = [row for row in rows if str(row[2]).startswith(RETAINED_PREFIX)]
    probes = [row for row in rows if not str(row[2]).startswith(RETAINED_PREFIX)]

    if not real:
        return failed(
            name,
            expected,
            f"{len(rows)} row(s) and none under {RETAINED_PREFIX!r} - there is no real backup to "
            f"have verified. keys: {[row[2] for row in rows]}",
        )

    marked_probes = [
        f"backup_id={row[0]} s3_key={row[2]!r} restore_verified_at={row[3]}"
        for row in probes
        if row[3] is not None
    ]
    if marked_probes:
        return failed(
            name,
            expected,
            f"the mark is on {len(marked_probes)} row(s) that are not backups: {marked_probes}. "
            f"The restore test verified the wrong row.",
        )

    newest = real[0]
    backup_id, _, s3_key, verified_at, counts = newest[0], newest[1], newest[2], newest[3], newest[4]
    if verified_at is None:
        return failed(
            name,
            expected,
            f"the newest real backup (backup_id={backup_id}, s3_key={s3_key!r}) has "
            f"restore_verified_at IS NULL",
        )
    if not isinstance(counts, dict) or not counts:
        return failed(
            name,
            expected,
            f"backup_id={backup_id} is marked verified at {verified_at} but "
            f"restore_verified_counts is {counts!r}. A mark with no counts beside it is a claim "
            f"with no evidence - the counts ARE the restore test (§ 3).",
        )
    return passed(
        name,
        expected,
        f"backup_id={backup_id} s3_key={s3_key!r} verified at {verified_at}, "
        f"{len(counts)} restored table counts; {len(probes)} non-backup row(s), none marked",
    )


def check_restore_job_succeeded(job_rows: Sequence[tuple]) -> CheckResult:
    name = f"{RESTORE_JOB} has a successful run"
    expected = "a success row in job_runs"
    if not job_rows:
        return failed(
            name,
            expected,
            f"no successful {RESTORE_JOB} in job_runs. 'Last success' is the most recent SUCCESS "
            f"row, never the most recent row of any status (§ 4).",
        )
    finished_at, rows_written = job_rows[0]
    if rows_written is not None:
        return failed(
            name,
            expected + ", with rows_written NULL",
            f"rows_written={rows_written!r}. The restore test writes three columns on one existing "
            f"row rather than inserting; § 4's `rows_written` means rows written to this database, "
            f"and the job's own contract is that it reports NULL.",
        )
    return passed(name, expected, f"finished_at={finished_at}, rows_written=NULL")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def _all_container_names() -> list[str]:
    """Every container on the host, running or not. `docker ps -a`, not `docker compose ps`.

    The throwaway is created by `docker run` outside Compose, so Compose cannot see it.
    """
    listed = shell.run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if listed.returncode != 0:
        raise Precondition(
            f"Stage H: `docker ps -a` exited {listed.returncode}: "
            f"{listed.stderr.strip() or '(no stderr)'}"
        )
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def _compose_services() -> tuple[list[str], list[str], dict[str, int]]:
    """(running services, all services, one-shot exit codes) from `docker compose ps`."""
    from verify.phase11.stage_e import _json_lines

    everything = shell.run(["docker", "compose", "ps", "-a", "--format", "json"])
    if everything.returncode != 0:
        raise Precondition(
            f"Stage H: `docker compose ps -a` exited {everything.returncode}: "
            f"{everything.stderr.strip() or '(no stderr)'}"
        )

    present: list[str] = []
    running: list[str] = []
    exits: dict[str, int] = {}
    for entry in _json_lines(everything.stdout):
        service = entry.get("Service")
        if not service:
            continue
        present.append(service)
        if entry.get("State") == "running":
            running.append(service)
        if service in ONE_SHOT_SERVICES:
            exits[service] = int(entry.get("ExitCode", -1))
    return running, present, exits


def read(conn) -> dict[str, Any]:
    readonly.assert_select_granted(conn)

    rows = readonly.query(
        conn,
        """
        SELECT backup_id, s3_bucket, s3_key, restore_verified_at, restore_verified_counts
        FROM backups
        ORDER BY backup_id DESC
        """,
    )
    job_rows = readonly.query(
        conn,
        """
        SELECT finished_at, rows_written
        FROM job_runs
        WHERE job_name = %s AND status = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        [RESTORE_JOB],
    )
    return {"rows": rows, "job_rows": job_rows}


def checks() -> Sequence[Check]:
    containers = _all_container_names()
    running, present, exits = _compose_services()
    with readonly.connection() as conn:
        state = read(conn)

    return [
        lambda: check_throwaway_is_gone(containers),
        lambda: check_service_sets(running, present),
        lambda: check_one_shot_exited_cleanly(exits),
        lambda: check_restore_job_succeeded(state["job_rows"]),
        lambda: check_mark_is_on_the_most_recent_real_backup(state["rows"]),
    ]
