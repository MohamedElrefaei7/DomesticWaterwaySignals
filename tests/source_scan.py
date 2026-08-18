"""Reading this project's own source without reading its explanation of itself.

WHY THIS EXISTS AS A SHARED HELPER RATHER THAN A LINE IN EACH TEST.

CLAUDE.md § 23 splits source-scanning guards in two. A source test is LEGITIMATE when the source
text IS the invariant - "no write path opens a raw connection", "nothing builds a docker
invocation" - because the call site is the whole property, and code that never executes is exactly
as much of a violation as code that does. It is ILLEGITIMATE when the text stands in for behaviour.

It also says how the legitimate kind goes wrong: **the modules such a guard covers contain the
forbidden thing in their own docstrings, in the sentences explaining why it is forbidden.** A regex
matches its own justification, fails permanently on a correct file, and the repair somebody reaches
for is a weaker pattern - which is worse everywhere else.

THAT HAPPENED THREE TIMES IN ONE PHASE, which is why it is a module instead of a habit:

  1. `verify/preflight.py`'s client-pin parser found "Debian bookworm ships postgresql-client-15"
     in Dockerfile.scheduler's header and reported a correct file as pinning client 15.
  2. `test_backup_invokes_pg_dump_directly_not_docker` found "cannot `docker run` without the
     host's Docker socket" in backup.py's module docstring.
  3. `test_restore_test_role_switch_effect_is_asserted_not_its_invocation` found "This was
     `SET LOCAL ROLE` and it did not work" in restore_test.py's own explanation of the fix.

Each time the code was right and the check was wrong. The AST knows which strings are docstrings;
a line scanner does not.
"""

from __future__ import annotations

import ast


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The identity of every string node that is a module/class/function docstring."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


def code_string_literals(source: str) -> list[tuple[int, str]]:
    """Every `(lineno, value)` string literal that is NOT a docstring.

    Comments never appear here at all: the parser discards them, which is the other half of why
    this is an AST walk. A `#` line explaining a forbidden call cannot be mistaken for one.

    The list is asserted non-empty by every caller through `scan_for`, because a scanner that
    resolved nothing reports no findings and reads exactly like a clean file (CLAUDE.md § 21).
    """
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def scan_for(source: str, predicate) -> list[str]:
    """`[<"line N: value">]` for every non-docstring literal the predicate accepts.

    Raises rather than returning `[]` when the walk found no literals at all. An empty result and
    a walk that read nothing are indistinguishable to the caller's `== []`, and the second is the
    failure this project has shipped twice.
    """
    literals = code_string_literals(source)
    if not literals:
        raise AssertionError(
            "the source scan found no string literals outside docstrings at all - it parsed the "
            "wrong text, and every assertion built on it would pass vacuously"
        )
    return [f"line {lineno}: {value!r}" for lineno, value in literals if predicate(value)]
