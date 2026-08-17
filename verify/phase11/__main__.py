"""`python3 -m verify.phase11 <stage>` — dispatch one stage's checks and exit with its code.

STAGES ARE REGISTERED, NOT IMPORTED BY NAME. `STAGES` maps the runbook's stage identifier to the
function that builds that stage's check list. An unknown stage is a usage error (exit 2) that
prints every registered name, so a typo does not read as a stage with nothing to check.

Parts 2 through 4 add entries. This module holds no assertions of its own and runs no subprocess:
a `subprocess.run(...)` here would be a violation of the package contract, and that call is spelled
out in this sentence deliberately so the AST guard in tests/verify/test_shell_allowlist.py has a
permanent inverted mutation to stay green against (CLAUDE.md § 23).

    python3 -m verify.phase11 c-post
    python3 -m verify.phase11 d-pre /path/to/phase11.tfplan --json

Exit codes, from result.py: 0 all checks passed, 1 a check failed, 2 usage or unmet precondition.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from verify.phase11 import stage_c, stage_d
from verify.phase11.result import EXIT_PRECONDITION, Check, Precondition, report

# stage identifier -> builder taking the stage's positional arguments and returning its checks.
# `report()` refuses to exit 0 over an empty check list, so a stage registered with no checks fails
# rather than reporting a clean run.
STAGES: dict[str, Callable[..., Sequence[Check]]] = {
    "c-pre": stage_c.checks_c_pre,
    "c-post": stage_c.checks_c_post,
    "d-pre": stage_d.checks,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m verify.phase11",
        description=(
            "Phase 11 stage verifiers. READ-ONLY BY CONSTRUCTION: every subprocess goes through "
            "an allow-list of read-only subcommands, and the database verifiers connect as the "
            "read-only role. Exits 0 when every check passed, 1 when a check failed, 2 on usage "
            "or an unmet precondition - a verifier that could not tell never exits 0."
        ),
        epilog=(
            "This never writes to CONTEXT.md or any tracked file. Use --json and transcribe."
        ),
    )
    parser.add_argument(
        "stage",
        nargs="?",
        help="the runbook stage to verify; omit to list the registered stages",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="stage-specific positional arguments (e.g. the plan file for d-pre)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit a machine-readable summary on stdout; a human transcribes it into the log",
    )
    parsed = parser.parse_args(argv)

    registered = ", ".join(sorted(STAGES)) or "(none registered yet)"

    if parsed.stage is None:
        print(f"registered stages: {registered}", file=sys.stderr)
        print("\nusage error: no stage named. Exiting 2.", file=sys.stderr)
        return EXIT_PRECONDITION

    builder = STAGES.get(parsed.stage)
    if builder is None:
        print(
            f"usage error: no such stage {parsed.stage!r}.\nregistered stages: {registered}",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    try:
        checks = builder(*parsed.args)
    except TypeError as exc:
        # Wrong arity for the stage - a usage error, not a failed check.
        print(f"usage error: stage {parsed.stage!r}: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except Precondition as exc:
        # A builder can discover an unmet precondition while assembling its checks (an absent plan
        # file, an unreadable backend). That is exit 2, and report() is not reached.
        print(f"{parsed.stage}\n\nPRECONDITION NOT MET: {exc}\n", file=sys.stderr)
        return EXIT_PRECONDITION

    return report(parsed.stage, checks, as_json=parsed.as_json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
