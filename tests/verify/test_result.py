"""Unit tier — the three-code exit convention and the stop-at-first-failure runner.

The checks these tests run are fabricated one-liners. The point is the runner's behaviour, not any
stage's assertions, and using real checks here would make the test depend on an instance.
"""

import ast
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify.phase11 import result as result_module  # noqa: E402
from verify.phase11.__main__ import main  # noqa: E402


def _pass(name="ok"):
    return lambda: result_module.passed(name, expected="a", observed="a")


def _fail(name="bad", expected="exactly 1", observed="2"):
    return lambda: result_module.failed(name, expected=expected, observed=observed)


def _boom(name="unreachable"):
    def check():
        raise AssertionError(f"{name} ran, and it must not have")

    return check


def _precondition(message="the plan file is absent"):
    def check():
        raise result_module.Precondition(message)

    return check


# ---------------------------------------------------------------------------------------------
# 8-9: 0, 1 and 2 are three different facts
# ---------------------------------------------------------------------------------------------


def test_exit_code_2_on_unmet_precondition():
    """"I could not tell" is not "I checked and it is wrong", and it is certainly not "fine".

    Collapsing 2 into 1 sends the operator to investigate the infrastructure when the actual fix
    is a missing plan file. Collapsing it into 0 is the CLAUDE.md § 13 failure: a check that
    quietly became a no-op and still reported success.
    """
    stream = io.StringIO()
    code = result_module.report("d-pre", [_precondition()], stream=stream)

    assert code == result_module.EXIT_PRECONDITION
    assert code == 2
    assert code != result_module.EXIT_FAILED

    printed = stream.getvalue()
    assert "PRECONDITION NOT MET" in printed
    assert "the plan file is absent" in printed
    assert "NOT a failed check" in printed


def test_exit_code_2_when_a_precondition_is_raised_mid_stage():
    """A precondition discovered by the third check is still exit 2, not exit 1."""
    stream = io.StringIO()
    code = result_module.report(
        "g", [_pass("first"), _pass("second"), _precondition("waterway_api lacks SELECT")],
        stream=stream,
    )
    assert code == 2
    assert "waterway_api lacks SELECT" in stream.getvalue()


def test_exit_code_1_on_failed_check():
    stream = io.StringIO()
    code = result_module.report("d-pre", [_pass(), _fail()], stream=stream)

    assert code == result_module.EXIT_FAILED
    assert code == 1


def test_exit_code_0_only_when_every_check_passed():
    stream = io.StringIO()
    assert result_module.report("c-post", [_pass("a"), _pass("b")], stream=stream) == 0


def test_an_empty_check_list_is_not_a_pass():
    """A stage that enumerated nothing has checked nothing.

    Same failure as `verify/preflight.py` gate 1 walking an empty collection (CLAUDE.md § 22): a
    gate that passes over an empty set is green forever and watching nothing. Exit 2 rather than
    1, because nothing was found to be wrong - the verifier could not tell.
    """
    stream = io.StringIO()
    code = result_module.report("j", [], stream=stream)

    assert code == result_module.EXIT_PRECONDITION
    assert "declares no checks" in stream.getvalue()
    assert result_module.exit_code([]) == result_module.EXIT_PRECONDITION


# ---------------------------------------------------------------------------------------------
# 10-11: the runner stops, and says what it saw
# ---------------------------------------------------------------------------------------------


def test_runner_stops_at_first_failure():
    """Checks after a failure do not run. `_boom` raises if it is ever called.

    Asserting on the returned list alone would pass an implementation that ran everything and
    reported only the prefix. The check that must not run is the assertion.
    """
    results = result_module.run_checks([_pass("first"), _fail("second"), _boom("third")])

    assert [r.name for r in results] == ["first", "second"]
    assert results[-1].status == result_module.FAIL


def test_runner_stops_at_first_failure_through_report():
    stream = io.StringIO()
    code = result_module.report(
        "d-pre", [_pass("first"), _fail("second"), _boom("third")], stream=stream
    )

    assert code == 1
    printed = stream.getvalue()
    assert "STOPPED at second" in printed
    # The count of what did not run is reported, so the operator knows the report is a prefix.
    assert "2 of 3 checks ran, 1 not run" in printed


def test_failure_report_names_expected_and_observed():
    """"expected exactly 1, observed 2" is actionable; "check failed" is not (CLAUDE.md § 13)."""
    stream = io.StringIO()
    result_module.report(
        "d-pre",
        [_fail("one bucket is created", expected="exactly 1 aws_s3_bucket create", observed="2")],
        stream=stream,
    )

    printed = stream.getvalue()
    assert "one bucket is created" in printed
    assert "expected: exactly 1 aws_s3_bucket create" in printed
    assert "observed: 2" in printed


def test_a_passing_result_also_reports_what_it_observed():
    stream = io.StringIO()
    result_module.report(
        "f",
        [lambda: result_module.passed("migrations", expected="26 applied", observed="26 applied")],
        stream=stream,
    )
    assert "observed: 26 applied" in stream.getvalue()


def test_json_summary_is_machine_readable_and_carries_the_counts():
    """`--json` is what a human transcribes from. It must carry the denominator.

    `checks_run` without `checks_declared` reads as a complete run when the runner stopped early -
    the same disappearing-denominator shape CLAUDE.md § 18 and § 20 are about.
    """
    stream = io.StringIO()
    code = result_module.report(
        "d-pre", [_pass("first"), _fail("second"), _boom("third")], as_json=True, stream=stream
    )

    payload = json.loads(stream.getvalue())
    assert code == 1
    assert payload["stage"] == "d-pre"
    assert payload["exit_code"] == 1
    assert payload["checks_declared"] == 3
    assert payload["checks_run"] == 2
    assert payload["results"][-1]["expected"] == "exactly 1"
    assert payload["results"][-1]["observed"] == "2"


def test_json_summary_of_a_precondition_carries_the_reason():
    stream = io.StringIO()
    code = result_module.report("i", [_precondition("timed out")], as_json=True, stream=stream)

    payload = json.loads(stream.getvalue())
    assert code == 2
    assert payload["exit_code"] == 2
    assert payload["precondition"] == "timed out"
    assert payload["results"] == []


# ---------------------------------------------------------------------------------------------
# The CLI never writes, and an unknown stage is a usage error rather than an empty pass
# ---------------------------------------------------------------------------------------------


def test_unknown_stage_is_exit_2(capsys):
    assert main(["no-such-stage"]) == 2
    assert "no such stage" in capsys.readouterr().err


def test_no_stage_named_is_exit_2(capsys):
    assert main([]) == 2
    assert "no stage named" in capsys.readouterr().err


# Every filesystem write in verify/phase11/, by module and enclosing function, with a COUNT.
#
# An exact-set allow-list with counts, like the commit-helper allow-list (CLAUDE.md § 23), the
# security-group ingress (§ 8), the ufw port set (§ 11) and the published-port set (§ 22). A new
# write fails until somebody writes down what it is; a REMOVED one fails too, so the list cannot
# quietly go stale; and a second write inside an already-permitted function is caught rather than
# absorbed by the entry that is already there.
#
# It is empty in Part 1 and that is the assertion, not a placeholder. Stage E's `/mnt/data`
# free-space baseline (Part 4) is the one write this package is expected to ever make, and when it
# lands it lands here with its reason beside it.
PERMITTED_WRITES: dict[str, int] = {}

_WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "unlink", "rename", "touch", "open"}


def _enclosing_function(tree, lineno: int) -> str:
    """The innermost def containing `lineno`, or "<module>"."""
    best = "<module>"
    best_line = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end and node.lineno > best_line:
                best, best_line = node.name, node.lineno
    return best


def test_the_verifier_writes_only_what_is_written_down():
    """The summary goes to stdout and a human transcribes it into the writeback commit.

    A verifier that writes its own conclusions into CONTEXT.md puts unreviewed claims in the log,
    and the log's value is that every claim in it was looked at. The guard is structural rather
    than behavioural because the property is about which call sites exist: a write that never
    executes on the path anybody tested is exactly as much of a violation as one that does, which
    is the legitimate kind of source test CLAUDE.md § 23 describes.
    """
    package = REPO_ROOT / "verify" / "phase11"
    assert package.is_dir(), (
        f"source tree not resolved: {package}. A scanner that cannot see the package finds no "
        f"writes, which is indistinguishable from a package that makes none."
    )

    modules = sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(modules) >= 4, f"expected >= 4 modules under {package}, found {modules}"

    observed: dict[str, int] = {}
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _WRITE_CALLS:
                key = f"{path.name}:{_enclosing_function(tree, node.lineno)}"
                observed[key] = observed.get(key, 0) + 1

    assert observed == PERMITTED_WRITES, (
        "expected: filesystem writes in verify/phase11/ exactly matching the allow-list\n"
        f"expected: {PERMITTED_WRITES}\n"
        f"observed: {observed}\n"
        "A new write must be added to PERMITTED_WRITES with its reason. A removed one must be "
        "taken out, so the list cannot go stale."
    )
