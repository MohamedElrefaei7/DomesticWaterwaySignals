"""Unit tier — the restart-recovery and failure-survives assertions. No database, no scheduler.

Covers CLAUDE.md § 13: exactly one prompt catch-up fire, and the failure-survives check requiring
both the record's presence and the work's absence.

The harnesses' EFFECTS need an instance — a real process stopped and started, a real job store.
Their ASSERTIONS do not, because both take their inputs as arguments. That is what makes the
`== 1` and the promptness bound checkable here rather than only at 3am on the instance.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify import failure_survives, preflight  # noqa: E402
from verify.restart_recovery import assess_catch_up  # noqa: E402

RESTART_AT = datetime(2026, 8, 11, 3, 0, 0, tzinfo=timezone.utc)
INTERVAL = 20.0
PROMPTNESS = 5.0


def _at(seconds):
    return RESTART_AT + timedelta(seconds=seconds)


def _assess(fires):
    return assess_catch_up(
        fires, restart_at=RESTART_AT, promptness_seconds=PROMPTNESS, interval_seconds=INTERVAL
    )


# ---------------------------------------------------------------------------------------------
# 10-13: the catch-up assertion
# ---------------------------------------------------------------------------------------------


def test_exactly_one_catch_up_fire_passes():
    """The correct observable: one fire, promptly, after however many slots were missed."""
    result = _assess([_at(0.4)])

    assert result.status == preflight.PASS
    assert "one fire" in result.detail
    assert "0.40s" in result.detail, "the observed delay is not reported"

    # A fire exactly at the promptness bound is still prompt.
    assert _assess([_at(PROMPTNESS)]).status == preflight.PASS

    # Fires from BEFORE the restart are not counted - they are the pre-outage schedule.
    assert _assess([_at(-60), _at(-40), _at(-20), _at(0.5)]).status == preflight.PASS


def test_multiple_catch_up_fires_fail():
    """Three fires after restart. `>= 1` would pass this; `== 1` must not.

    This is the failure coalesce=True exists to prevent: one fire per missed slot, against a
    public API that will rate-limit us for it. An operator would meet it as an ingest source
    refusing requests, several layers from the cause.
    """
    result = _assess([_at(0.3), _at(0.5), _at(0.7)])

    assert result.status == preflight.FAIL, (
        "three fires after restart were accepted - the assertion is `>= 1` rather than `== 1`, "
        "and broken coalescing would pass it"
    )
    assert "3 fires" in result.detail, "the observed count is not named"
    # Every timestamp is reported, so the operator can tell bunched fires (broken coalesce) from
    # fires an interval apart (the window straddled a slot).
    for fire in (_at(0.3), _at(0.5), _at(0.7)):
        assert fire.isoformat() in result.detail
    assert "coalesce" in result.detail


def test_zero_fires_fail():
    """No fire at all: the job did not survive the restart, or never fired in the window."""
    result = _assess([])

    assert result.status == preflight.FAIL
    assert "NO fire" in result.detail

    # Fires that all predate the restart are the same case.
    assert _assess([_at(-30), _at(-10)]).status == preflight.FAIL


def test_a_fire_one_full_interval_after_restart_fails():
    """The `replace_existing` symptom: a single fire, at the wrong time.

    This is the one that matters most. The count is correct — exactly one — so a count-only
    assertion passes it, and every configuration test passes it too. What is wrong is WHEN: the
    past-due slot was discarded and the job simply resumed on a fresh schedule, so it fires one
    full interval after restart instead of immediately. Nothing caught up.
    """
    result = _assess([_at(INTERVAL)])

    assert result.status == preflight.FAIL, (
        "a single fire one full interval after restart was accepted - the promptness assertion "
        "is missing, and the exact bug the Phase 2 commit found would pass"
    )
    assert "20.00s" in result.detail, "the observed delay is not reported"
    assert "WRONG TIME" in result.detail
    assert "replace_existing" in result.detail

    # Just over the bound fails; just under passes. The boundary is where this lives or dies.
    assert _assess([_at(PROMPTNESS + 0.01)]).status == preflight.FAIL
    assert _assess([_at(PROMPTNESS - 0.01)]).status == preflight.PASS


def test_catch_up_failures_all_report_observed_timestamps():
    """CLAUDE.md § 13: never a bare FAIL."""
    failures = [_assess([]), _assess([_at(0.1), _at(0.2)]), _assess([_at(INTERVAL)])]
    assert len(failures) == 3
    assert all(result.status == preflight.FAIL for result in failures)

    for result in failures:
        assert "observed:" in result.detail
        assert RESTART_AT.isoformat() in result.detail, "the restart time is not reported"


# ---------------------------------------------------------------------------------------------
# 14: failure-survives
# ---------------------------------------------------------------------------------------------


def _assess_survives(failed_row=True, message=None, sentinel=False, propagated=True):
    return failure_survives.assess_failure_survives(
        failed_row_present=failed_row,
        error_message=failure_survives.ERROR_MESSAGE if message is None else message,
        sentinel_present=sentinel,
        exception_propagated=propagated,
    )


def test_failure_survives_requires_both_the_failed_row_and_the_missing_sentinel():
    """The `failed` row present AND the sentinel present must FAIL.

    This is the case that shows why the sentinel carries the argument. The `failed` row appears
    whether or not the bookkeeping used a separate session — the decorator writes it after the
    work has already unwound either way. Only the sentinel's ABSENCE alongside the record's
    PRESENCE demonstrates that the two were on different connections.
    """
    both_present = _assess_survives(failed_row=True, sentinel=True)

    assert both_present.status == preflight.FAIL, (
        "a run where the work's sentinel SURVIVED was accepted because the failed row was there - "
        "the check asserts only the record and proves nothing about session separation"
    )
    assert "SURVIVED" in both_present.detail
    assert "sentinel present=True" in both_present.detail

    # The correct shape passes.
    assert _assess_survives().status == preflight.PASS

    # And each of the other two conditions fails on its own.
    missing_record = _assess_survives(failed_row=False)
    assert missing_record.status == preflight.FAIL
    assert "NO failed row" in missing_record.detail

    swallowed = _assess_survives(propagated=False)
    assert swallowed.status == preflight.FAIL
    assert "did NOT propagate" in swallowed.detail

    # A failure record without the reason is a row that says only "something went wrong".
    no_message = _assess_survives(message="")
    assert no_message.status == preflight.FAIL
    assert "does not carry the raised message" in no_message.detail


def test_failure_survives_reports_all_three_observations_on_every_failure():
    """Each failure states all three observed booleans, not only the one that tripped."""
    failures = [
        _assess_survives(failed_row=False),
        _assess_survives(sentinel=True),
        _assess_survives(propagated=False),
        _assess_survives(message="something else entirely"),
    ]
    assert len(failures) == 4
    assert all(result.status == preflight.FAIL for result in failures)

    for result in failures:
        assert "failed row present=" in result.detail
        assert "sentinel present=" in result.detail
        assert "exception propagated=" in result.detail


def test_the_probe_never_deletes_from_job_runs():
    """job_runs is append-only by trigger; the harness must not work around it.

    Read as a source-level assertion because the alternative is discovering it on the instance,
    where the trigger would refuse and the harness would abort mid-verification.
    """
    source = (REPO_ROOT / "verify" / "failure_survives.py").read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )

    assert "DELETE FROM job_runs" not in executable
    assert "DISABLE TRIGGER" not in executable
    # The one DROP it does issue is scoped to its own scratch table.
    assert "DROP TABLE IF EXISTS {SCRATCH_TABLE}" in executable
    assert failure_survives.SCRATCH_TABLE.startswith("verify_")


def test_the_restart_probe_cleans_itself_out_of_the_job_store():
    """A probe left in the persistent store keeps firing under the production scheduler.

    register_jobs() only adds and modifies the jobs in the cadence table; it never removes ones it
    does not recognise. So a leftover probe row in apscheduler_jobs would survive into production,
    resolve its pickled import path back to verify/, and run.
    """
    source = (REPO_ROOT / "verify" / "restart_recovery.py").read_text(encoding="utf-8")

    assert "def remove_probe_from_jobstore" in source
    assert "finally:" in source, "cleanup is not on a finally, so a failed run leaks the probe"
    assert "--cleanup-only" in source, "no manual escape hatch if cleanup itself fails"
    # It cleans the job store, never the record of the verification.
    assert "DELETE FROM job_runs" not in source
