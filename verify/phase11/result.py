"""One check's outcome, the three-code exit convention, and the runner that stops at the first red.

WHY THIS IS NOT `verify/preflight.py`'s `Result`. Preflight's Result carries `name/status/detail`
and its `exit_code` is binary: zero when every gate passed, one otherwise. That is right for
preflight, which runs on the instance with everything it needs already present. It is wrong here,
because most of these checks depend on a precondition a human has to have satisfied first — a plan
file that exists, a backend that is reachable, a role that holds SELECT — and a verifier that
cannot run its checks must not exit 0 AND must not exit 1.

    0   every check ran and passed
    1   a check ran and failed          -> "I checked, and it is wrong"
    2   usage, or an unmet precondition -> "I could not tell"

Collapsing 1 and 2 is how a broken verifier reads as a passing one: the operator who sees `exit 1`
goes and looks at the thing being checked, and the operator who sees `exit 2` goes and looks at the
verifier's own preconditions. Those are different investigations and only one of them is right.

`expected` AND `observed` ARE SEPARATE FIELDS, and both are required. CLAUDE.md § 13: a check
reports the observed value on failure, never a bare FAIL. "expected exactly 1, observed 2" is
actionable; "check failed" sends the operator off to re-derive by hand what this process already
had in a variable. Making them two fields rather than one prose string is what stops the second
one from being dropped when somebody is in a hurry.

CHECKS RUN IN DECLARED ORDER AND THE RUNNER STOPS AT THE FIRST FAILURE. Later checks in a stage
routinely assume earlier ones passed — `d-post` cannot meaningfully assert what a health check
searches for if the apply that created it did not happen — so continuing produces a cascade of
failures with the real one buried at the top and the noise below it.

NOTHING IN THIS MODULE RUNS A SUBPROCESS. A `subprocess.run(...)` here would be a violation of the
package's own contract, and the sentence you are reading contains that call spelled out on purpose:
`tests/verify/test_shell_allowlist.py` walks the AST rather than grepping, so this docstring must
NOT trip it. That is the inverted mutation CLAUDE.md § 23 asks for, kept permanently in the tree.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Callable, Sequence, TextIO

PASS = "PASS"
FAIL = "FAIL"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PRECONDITION = 2


class Precondition(Exception):
    """A check could not be RUN. Distinct from a check that ran and found the wrong thing.

    Raised for a missing plan file, an unreachable backend, an absent grant, a verifier invoked on
    the wrong host. It becomes exit 2. It must never be raised to report a failed assertion — that
    is what a FAIL result is for — because the two mean different things to whoever is reading.
    """


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome, carrying what it wanted and what it saw."""

    name: str
    status: str
    expected: str
    observed: str

    def render(self) -> str:
        return (
            f"[{self.status:4s}] {self.name}\n"
            f"         expected: {self.expected}\n"
            f"         observed: {self.observed}"
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "expected": self.expected,
            "observed": self.observed,
        }


def passed(name: str, expected: str, observed: str) -> CheckResult:
    """A passing result still reports what it observed.

    CLAUDE.md § 13's rule is stated for failures, but a PASS whose observed value is printed is
    what lets a human transcribe a real number into the writeback commit instead of the word
    "passed". No check here prints PASS for something it did not observe.
    """
    return CheckResult(name=name, status=PASS, expected=expected, observed=observed)


def failed(name: str, expected: str, observed: str) -> CheckResult:
    return CheckResult(name=name, status=FAIL, expected=expected, observed=observed)


Check = Callable[[], CheckResult]


def run_checks(checks: Sequence[Check]) -> list[CheckResult]:
    """Run checks in declared order, stopping at the first non-PASS.

    Returns the results produced up to and including the failure. The checks that never ran are
    absent rather than recorded as skipped: a SKIP that reads as anything other than "not checked"
    is the failure CLAUDE.md § 13 is about, and here the stop is deliberate rather than a
    precondition being missing, so the honest report is the shorter list plus the count.
    """
    results: list[CheckResult] = []
    for check in checks:
        result = check()
        results.append(result)
        if result.status != PASS:
            break
    return results


def exit_code(results: Sequence[CheckResult]) -> int:
    """0 only when there was at least one result and all of them passed.

    An EMPTY result list is not a pass. A stage that enumerated nothing has checked nothing, and a
    gate that reports success over an empty collection is green forever while watching nothing
    (CLAUDE.md § 22's preflight gate 1, § 21's "prove it resolved the source tree first"). It comes
    back as EXIT_PRECONDITION rather than EXIT_FAILED because nothing was found to be wrong — the
    verifier could not tell.
    """
    if not results:
        return EXIT_PRECONDITION
    return EXIT_OK if all(result.status == PASS for result in results) else EXIT_FAILED


def report(
    stage: str,
    checks: Sequence[Check],
    *,
    as_json: bool = False,
    stream: TextIO | None = None,
) -> int:
    """Run a stage's checks and print the outcome. Returns the process exit code.

    This is the only place a stage's result becomes an exit code, so the three-code convention has
    one implementation. `Precondition` raised anywhere inside a check surfaces here as exit 2 with
    its own message, and the checks after it do not run.
    """
    out = sys.stdout if stream is None else stream
    total = len(checks)

    try:
        if total == 0:
            raise Precondition(
                f"stage {stage!r} declares no checks; a stage that checks nothing "
                f"must not report success"
            )
        results = run_checks(checks)
    except Precondition as exc:
        if as_json:
            out.write(
                json.dumps(
                    {
                        "stage": stage,
                        "exit_code": EXIT_PRECONDITION,
                        "precondition": str(exc),
                        "checks_declared": total,
                        "checks_run": 0,
                        "results": [],
                    },
                    indent=2,
                )
                + "\n"
            )
        else:
            out.write(f"{stage}\n\n")
            out.write(f"PRECONDITION NOT MET: {exc}\n\n")
            out.write(
                "Exiting 2. This is NOT a failed check - nothing was verified either way.\n"
                "Satisfy the precondition and run this again.\n"
            )
        return EXIT_PRECONDITION

    code = exit_code(results)

    if as_json:
        out.write(
            json.dumps(
                {
                    "stage": stage,
                    "exit_code": code,
                    "precondition": None,
                    "checks_declared": total,
                    "checks_run": len(results),
                    "results": [result.as_dict() for result in results],
                },
                indent=2,
            )
            + "\n"
        )
        return code

    out.write(f"{stage}\n\n")
    for result in results:
        out.write(result.render() + "\n\n")

    if code == EXIT_OK:
        out.write(f"{len(results)} of {total} checks passed\n")
    else:
        not_run = total - len(results)
        out.write(
            f"STOPPED at {results[-1].name} ({len(results)} of {total} checks ran, "
            f"{not_run} not run)\n\n"
            "Later checks assume the earlier ones held, so they were not run rather than\n"
            "reported - a cascade of failures buries the one that matters.\n"
        )
    return code
