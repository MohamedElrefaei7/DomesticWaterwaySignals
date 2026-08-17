"""Stage I — poll Route53 until the health check reports what a human just caused it to report.

    python3 -m verify.phase11 i --expect Failure --timeout 600

THIS STAGE POLLS AND NEVER ACTS. The human stops the API or the scheduler; this watches Route53
notice. CLAUDE.md § 1 keeps the acting side human, and `shell.py`'s allow-list makes it structural -
there is no `docker stop` to reach for even by accident.

IT IS THE WHOLE POINT OF THE MONITORING PART. CONTEXT.md § Up Next item 6 has been open with status
unknown since Phase 10: an alarm nobody has watched fire is an alarm nobody knows is wired up. The
prior project recorded "Completed" while the stack had been down for two and a half months, and
what makes that possible is exactly a monitor that was configured and never observed.

A TIMEOUT IS EXIT 2, NOT EXIT 1. "The status did not change within ten minutes" and "the status
changed to the wrong thing" are different facts, and only one of them is evidence about the
monitor. Route53 evaluates from several regions on a 30-second interval with a failure threshold of
3 (monitoring.tf), so the honest floor is ~90 seconds and the honest window is minutes; a run that
gave up early reporting FAIL would be reporting a defect that is not there.

THE OBSERVED STATUSES ARE ALL REPORTED, not just the last. Route53 returns one observation per
checker region, and a check flipping in three regions and not the fourth is a real thing to see -
it is the difference between "the origin is down" and "one region cannot reach it".
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Sequence

from verify.phase11 import shell
from verify.phase11.result import Check, CheckResult, Precondition, failed, passed

# monitoring.tf: request_interval = 30, failure_threshold = 3. Nothing can flip before ~90s.
POLL_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 600

# What `aws route53 get-health-check-status` reports in `StatusReport.Status`. The strings are
# prose - "Success: HTTP Status Code 200, OK" / "Failure: Connection timed out" - so the verdict is
# a prefix match on the first word rather than an equality test against a sentence AWS may reword.
SUCCESS = "Success"
FAILURE = "Failure"


def observed_statuses(payload: dict[str, Any]) -> list[str]:
    """One `Status` string per checker region."""
    statuses: list[str] = []
    for observation in payload.get("HealthCheckObservations") or []:
        if not isinstance(observation, dict):
            continue
        report = observation.get("StatusReport") or {}
        statuses.append(report.get("Status", ""))
    return statuses


def verdict(statuses: Sequence[str]) -> str | None:
    """`Failure` when EVERY region says failure, `Success` when every region says success, else None.

    None means "in transition", which is a real state during the ~90 seconds Route53 takes to make
    up its mind, and it is neither answer. Returning the majority instead would let this stage
    declare a verdict while the regions still disagree - and then a human writes down a time-to-
    detect that is shorter than the real one.
    """
    if not statuses:
        return None
    if all(status.startswith(FAILURE) for status in statuses):
        return FAILURE
    if all(status.startswith(SUCCESS) for status in statuses):
        return SUCCESS
    return None


def check_verdict_matches(expected: str, statuses: Sequence[str], elapsed: float) -> CheckResult:
    name = f"the Route53 health check reports {expected}"
    wanted = f"every checker region reporting {expected}"
    reached = verdict(statuses)
    if reached != expected:
        return failed(
            name,
            wanted,
            f"verdict={reached!r} after {elapsed:.0f}s; per-region: {list(statuses)}",
        )
    return passed(
        name,
        wanted,
        f"{len(statuses)} region(s) agreed after {elapsed:.0f}s; per-region: {list(statuses)}",
    )


def poll(
    health_check_id: str,
    expect: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: int = POLL_SECONDS,
    reader: Callable[[str], dict[str, Any]] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[str], float]:
    """Poll until the verdict is `expect`, or raise Precondition on timeout.

    The timeout raises rather than returning a failed result, because "I did not see it within the
    window" is not a measurement of the monitor. Everything observed on the way is carried into the
    message, so a human reading exit 2 can see whether it was moving.
    """
    read = reader if reader is not None else _read_status
    started = clock()
    seen: list[list[str]] = []

    while True:
        statuses = observed_statuses(read(health_check_id))
        seen.append(statuses)
        elapsed = clock() - started
        if verdict(statuses) == expect:
            return statuses, elapsed
        if elapsed >= timeout_seconds:
            raise Precondition(
                f"Stage I: {expect} was not reported within {timeout_seconds}s. "
                f"observations, oldest first: {seen}. "
                f"This is exit 2 - the status did not change in the window, which is not the same "
                f"fact as the status changing to the wrong thing. Route53 evaluates every "
                f"{POLL_SECONDS}s with a failure threshold of 3, so nothing can flip in under ~90s."
            )
        sleeper(poll_seconds)


def _read_status(health_check_id: str) -> dict[str, Any]:
    completed = shell.run(
        ["aws", "route53", "get-health-check-status", "--health-check-id", health_check_id]
    )
    if completed.returncode != 0:
        raise Precondition(
            f"Stage I: get-health-check-status exited {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise Precondition(f"Stage I: get-health-check-status output is not JSON: {exc}") from exc


def checks(
    expect: str = FAILURE,
    timeout: str | int = DEFAULT_TIMEOUT_SECONDS,
    health_check_id: str | None = None,
) -> Sequence[Check]:
    from verify.phase11.stage_d import health_check_id_from_state

    if expect not in (SUCCESS, FAILURE):
        raise Precondition(
            f"Stage I: --expect must be {SUCCESS!r} or {FAILURE!r}, got {expect!r}"
        )
    identifier = health_check_id or health_check_id_from_state()
    statuses, elapsed = poll(identifier, expect, timeout_seconds=int(timeout))

    return [lambda: check_verdict_matches(expect, statuses, elapsed)]
