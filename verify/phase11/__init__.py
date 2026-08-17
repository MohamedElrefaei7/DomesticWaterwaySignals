"""Phase 11 stage verifiers — the deployment runbook's conditions, made executable.

WHAT THIS PACKAGE IS. Phase 11 and Stage B are code-complete and unapplied (CONTEXT.md § Up Next).
Bringing them up involves roughly twenty-five conditions a human would otherwise check by reading
output and remembering what to look for. This package turns each one into an assertion with an exit
code, so a stop-condition is `exit != 0` rather than a description of what to notice.

THE DELEGATION BOUNDARY IS THE POINT, NOT A CAVEAT. CLAUDE.md § 1 reserves `terraform apply`,
`terraform destroy`, every `DROP`, and anything that deletes data for a human. A verifier is code
that runs on the instance and against the account, so "it only reads" has to be a property of the
code rather than a claim in a docstring:

  * EVERY subprocess invocation in this package goes through `shell.run`, which enforces an
    ALLOW-LIST of permitted subcommands per binary. `terraform apply` is not refused by name — it
    is refused because it is not on the list, which is also how `terraform state rm` and every
    verb HashiCorp adds after this was written are refused.
  * `tests/verify/test_shell_allowlist.py` walks this package's AST and fails on any direct
    `subprocess.*` call outside `shell.py`. The call site IS the invariant, which is the kind of
    source-text test CLAUDE.md § 23 records as legitimate: a bare `subprocess.run` that never
    executes is exactly as much of a violation as one that does.
  * The database verifiers (Part 4) connect as the read-only role, so read-onlyness is enforced by
    Postgres rather than by review.

WHAT IT DELIBERATELY DOES NOT DO. It never writes to `CONTEXT.md` or any other tracked file. It
emits a machine-readable summary to stdout with `--json`, which a human reads and transcribes into
the writeback commit. A verifier that writes its own conclusions into the log puts unreviewed
claims there, and the log's whole value is that everything in it was looked at.

THE EXIT CODES ARE THREE, NOT TWO, and the third is the one that matters. See `result.py`.

    python3 -m verify.phase11 --help
"""
