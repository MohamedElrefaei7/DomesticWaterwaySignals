"""The only module in this package permitted to run a subprocess, and it can only run read-only ones.

AN ALLOW-LIST, NOT A DENY-LIST, AND THE DIFFERENCE IS THE WHOLE MODULE.

A deny-list is the tempting shape because the dangerous verbs are the ones you can name: refuse
`apply`, refuse `destroy`, done. It fails on the verb nobody named. `terraform state rm` removes a
resource from state, which is how `prevent_destroy` stops protecting the data volume (CLAUDE.md
§ 8). `terraform import` writes state. `terraform taint` schedules a replacement. `docker rm`,
`docker compose down`, `docker volume prune` all destroy. Each of those is one word, none of them
is spelled "apply" or "destroy", and a deny-list permits every one of them WHILE REPORTING SUCCESS
- the wrapper runs the command, gets a zero exit, and the verifier carries on reading the
aftermath of its own mutation.

An allow-list fails closed on anything unlisted, including every subcommand added to Terraform,
Docker or the AWS CLI after this file was written. That is the property being bought: the failure
mode of a mistake here is a refused command and a stack trace, not a destroyed resource.

`terraform plan` IS DELIBERATELY ABSENT. Part 2's `d-pre` reads a plan file the human already
created. If the verifier could generate its own plan, the artifact reviewed would no longer be the
artifact applied - a gap of minutes in which somebody else's apply, a changed variable, or drift
makes the two different documents. The human creates the plan, reads it, and hands the file over.

`terraform providers lock` IS ABSENT WHILE `terraform providers schema` IS PRESENT, which is why
permitted entries are TUPLES of leading words rather than a single verb: `lock` writes
`.terraform.lock.hcl`, and a bare `providers` entry would permit it. The same reasoning keeps bare
`compose` off the docker list - it would permit `docker compose down`.

MATCHING IS LONGEST-PREFIX OVER THE NON-FLAG TOKENS, AND THAT IS SAFE ONLY BECAUSE NO PERMITTED
ENTRY IS A PROPER PREFIX OF ANOTHER. `aws s3api head-object --bucket b --key k` has non-flag tokens
`[s3api, head-object, b, k]`, so the match has to ignore the trailing positional arguments; but if
`(providers,)` and `(providers, schema)` were both permitted, ignoring trailing tokens would let
`providers lock` fall back onto the shorter entry. `_assert_maximal()` enforces the invariant at
import time and `test_no_permitted_entry_is_a_prefix_of_another` asserts it.

FAIL-CLOSED ON AMBIGUITY. A flag whose value is a separate token (`docker compose --project-name
foo ps`) puts `foo` in the token stream, no entry matches, and the command is refused. That is the
correct direction to be wrong in: a refusal is visible and one edit away from fixed, and the
alternative - guessing which tokens are values - is a parser that is wrong quietly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------------------------
# The allow-list
# ---------------------------------------------------------------------------------------------

PERMITTED: dict[str, frozenset[tuple[str, ...]]] = {
    # `show` reads state or a plan file and writes nothing. `version` is what proves the binary
    # running the verifier is the one the state was written by. `providers schema` is read-only;
    # `providers lock` is not, and is absent.
    "terraform": frozenset(
        {
            ("show",),
            ("version",),
            ("providers", "schema"),
        }
    ),
    # No bare ("compose",) - that would permit `down`, `rm`, `up`, `build` and `run`.
    "docker": frozenset(
        {
            ("ps",),
            ("inspect",),
            ("version",),
            ("image", "inspect"),
            ("compose", "ps"),
            ("compose", "config"),
        }
    ),
    # Read-only verbs, enumerated one at a time. No bare service name anywhere: `aws s3api` alone
    # would permit `delete-object`, and `aws route53` alone would permit `delete-health-check`.
    "aws": frozenset(
        {
            ("sts", "get-caller-identity"),
            ("route53", "get-health-check"),
            ("route53", "get-health-check-status"),
            ("route53", "list-health-checks"),
            ("sns", "get-topic-attributes"),
            ("sns", "list-subscriptions-by-topic"),
            ("s3api", "head-object"),
            ("s3api", "list-object-versions"),
            ("s3api", "get-bucket-versioning"),
            ("cloudwatch", "describe-alarms"),
            ("budgets", "describe-budget"),
            ("budgets", "describe-budgets"),
        }
    ),
}

DEFAULT_TIMEOUT_SECONDS = 120


class RefusedCommand(Exception):
    """The wrapper declined to run a command. Never caught to retry; the fix is the command."""


def _assert_maximal(permitted: dict[str, frozenset[tuple[str, ...]]]) -> None:
    """No permitted entry may be a proper prefix of another. See the module docstring.

    Checked at import time rather than only in a test, because the invariant is what makes
    longest-prefix matching safe, and an allow-list that silently loses that property permits the
    exact subcommands it was written to refuse.
    """
    for binary, entries in permitted.items():
        for entry in entries:
            for other in entries:
                if other is not entry and other[: len(entry)] == entry and len(other) > len(entry):
                    raise ValueError(
                        f"{binary}: permitted entry {entry} is a proper prefix of {other}; "
                        f"longest-prefix matching would fall back onto {entry} and permit "
                        f"everything under it"
                    )


_assert_maximal(PERMITTED)


def permitted_entry(argv: list[str]) -> tuple[str, ...] | None:
    """Return the permitted entry this argv matches, or None if it matches nothing.

    Separated from `run` so the refusal logic is testable without executing anything, and so Part
    2's `test_verifiers_never_invoke_plan_or_apply` can assert against the list directly.
    """
    if not argv:
        return None

    binary = Path(argv[0]).name
    entries = PERMITTED.get(binary)
    if entries is None:
        return None

    tokens = tuple(token for token in argv[1:] if not token.startswith("-"))
    for length in range(len(tokens), 0, -1):
        candidate = tokens[:length]
        if candidate in entries:
            return candidate
    return None


def describe_refusal(argv: list[str]) -> str:
    """The message a refusal carries. Names the observed command and what was permitted."""
    if not argv:
        return "refused: empty command"

    binary = Path(argv[0]).name
    entries = PERMITTED.get(binary)
    observed = " ".join(argv)

    if entries is None:
        return (
            f"refused: {binary!r} is not an allow-listed binary. "
            f"observed: {observed}. "
            f"allow-listed binaries: {', '.join(sorted(PERMITTED))}"
        )

    listing = ", ".join(" ".join(entry) for entry in sorted(entries))
    return (
        f"refused: no allow-listed {binary} subcommand matches. "
        f"observed: {observed}. "
        f"permitted for {binary}: {listing}. "
        f"This is an ALLOW-list: a subcommand is refused for being absent from it, not for being "
        f"recognised as dangerous."
    )


def run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run one allow-listed, read-only command and return its CompletedProcess.

    There is no `shell=` parameter and `argv` must be a list: a string command would be split by a
    shell, and a shell is what makes `terraform show; terraform apply` one command.

    `check` is not a parameter either. Callers inspect `returncode` themselves, because a non-zero
    exit is frequently the thing being verified (the `pg_restore` asymmetry, a health check
    reporting Failure) and a wrapper that raised on it would make the interesting case unreachable.
    """
    if not isinstance(argv, list) or not all(isinstance(token, str) for token in argv):
        raise RefusedCommand(
            f"refused: argv must be a list of strings, observed {type(argv).__name__}. "
            f"A string command implies a shell, and a shell makes chaining possible."
        )

    if permitted_entry(argv) is None:
        raise RefusedCommand(describe_refusal(argv))

    return subprocess.run(
        argv,
        cwd=None if cwd is None else str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
