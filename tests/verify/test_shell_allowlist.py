"""Unit tier — the subprocess allow-list, and the AST guard that keeps everything inside it.

No command in this file is ever executed. `permitted_entry` and `describe_refusal` are pure
functions over an argv list, which is what makes it possible to assert that `terraform apply` is
refused without there being any chance of running it.

THE SECOND HALF OF THIS FILE IS A SOURCE-TEXT TEST AND THAT IS DELIBERATE. CLAUDE.md § 23 draws the
line: a source test is legitimate when the source text IS the invariant, and illegitimate when it
stands in for behaviour. "No module outside shell.py calls subprocess" is the first kind - a bare
`subprocess.run` that never executes is exactly as much of a violation as one that does, because
the property being protected is that the allow-list has no way around it.

The walk is an AST walk rather than a regex for the reason § 23 gives, and this package is the
exact case that reason describes: `shell.py`, `result.py` and `__main__.py` all name
`subprocess.run` inside their own docstrings, in the sentences explaining why it is forbidden. A
regex matches its own explanation, fails permanently, and the fix somebody reaches for is a weaker
pattern.
"""

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify.phase11 import shell  # noqa: E402

PACKAGE = REPO_ROOT / "verify" / "phase11"


# ---------------------------------------------------------------------------------------------
# 1-5: the allow-list refuses by absence, not by recognition
# ---------------------------------------------------------------------------------------------


def test_shell_refuses_terraform_apply():
    """The named case. `apply` is refused, and the message says the observed command."""
    argv = ["terraform", "apply", "-auto-approve"]

    assert shell.permitted_entry(argv) is None

    with pytest.raises(shell.RefusedCommand) as excinfo:
        shell.run(argv)

    message = str(excinfo.value)
    # CLAUDE.md § 13: the observed value, not a bare refusal.
    assert "terraform apply -auto-approve" in message
    # And it must say WHY, because the reason is the design: absence, not recognition.
    assert "ALLOW-list" in message


def test_shell_refuses_unlisted_subcommand():
    """An INVENTED verb, so this proves the allow-list rather than a coincidence of naming.

    A deny-list of {apply, destroy} passes `test_shell_refuses_terraform_apply` perfectly. It
    permits this, and it permits `terraform state rm`, `terraform import`, and whatever HashiCorp
    ships next year. This is the test that can tell the two implementations apart.
    """
    invented = ["terraform", "obliterate-everything", "--yes"]

    assert shell.permitted_entry(invented) is None
    with pytest.raises(shell.RefusedCommand):
        shell.run(invented)

    # Real verbs nobody would think to deny, for the same reason.
    for argv in (
        ["terraform", "state", "rm", "aws_ebs_volume.data"],
        ["terraform", "import", "aws_s3_bucket.state", "some-bucket"],
        ["terraform", "taint", "aws_instance.main"],
        ["terraform", "destroy"],
        ["docker", "volume", "prune", "-f"],
        ["docker", "compose", "down"],
    ):
        assert shell.permitted_entry(argv) is None, argv


def test_shell_refuses_terraform_init():
    """`init -migrate-state` is the state migration itself, and it is a human action.

    It also writes `.terraform/` and can rewrite the lock file. Refused for being absent from the
    list, like everything else.
    """
    for argv in (
        ["terraform", "init"],
        ["terraform", "init", "-migrate-state"],
        ["terraform", "-chdir=infra/terraform", "init", "-migrate-state"],
    ):
        assert shell.permitted_entry(argv) is None, argv
        with pytest.raises(shell.RefusedCommand):
            shell.run(argv)


def test_shell_refuses_docker_rm():
    """Destructive docker verbs, including the one that would remove the restore throwaway.

    Stage H asserts the throwaway container is GONE. A verifier that could remove it would be
    checking its own cleanup, which is CLAUDE.md § 2's theme 2 with the verifier as the culprit.
    """
    for argv in (
        ["docker", "rm", "-f", "dws-restore-check-a1b2"],
        ["docker", "stop", "waterway-api"],
        ["docker", "compose", "rm", "-f"],
        ["docker", "compose", "up", "-d"],
    ):
        assert shell.permitted_entry(argv) is None, argv
        with pytest.raises(shell.RefusedCommand):
            shell.run(argv)


def test_shell_permits_terraform_show():
    """The read path, including the global-flag and plan-file forms the verifiers actually use."""
    assert shell.permitted_entry(["terraform", "show", "-json"]) == ("show",)
    assert shell.permitted_entry(
        ["terraform", "-chdir=infra/terraform", "show", "-json", "phase11.tfplan"]
    ) == ("show",)
    assert shell.permitted_entry(["terraform", "version", "-json"]) == ("version",)

    # Trailing positional arguments must not defeat the match - this is why the matcher walks
    # prefixes rather than comparing the whole token list.
    assert shell.permitted_entry(
        ["aws", "s3api", "head-object", "--bucket", "b", "--key", "k"]
    ) == ("s3api", "head-object")
    assert shell.permitted_entry(["docker", "compose", "ps", "--format", "json"]) == (
        "compose",
        "ps",
    )


def test_no_permitted_entry_is_a_prefix_of_another():
    """The invariant that makes longest-prefix matching safe.

    If `("providers",)` were permitted alongside `("providers", "schema")`, then
    `terraform providers lock` would find no two-token match, fall back to the one-token entry,
    and be permitted - the allow-list would have a hole shaped exactly like the subcommand it was
    written to exclude. `shell._assert_maximal` raises at import; this asserts the raise works.
    """
    shell._assert_maximal(shell.PERMITTED)  # the real list, as imported

    with pytest.raises(ValueError) as excinfo:
        shell._assert_maximal({"terraform": frozenset({("providers",), ("providers", "schema")})})
    assert "proper prefix" in str(excinfo.value)

    # And the hole it would open, stated concretely.
    holed = {"terraform": frozenset({("providers",), ("providers", "schema")})}
    tokens = ("providers", "lock")
    assert any(
        tokens[: len(entry)] == entry for entry in holed["terraform"]
    ), "the prefix fallback is what makes this dangerous; if this ever stops holding, re-read the matcher"


def test_shell_refuses_a_string_command():
    """No shell, ever. A string is what makes `terraform show; terraform apply` one command."""
    with pytest.raises(shell.RefusedCommand) as excinfo:
        shell.run("terraform show -json")  # type: ignore[arg-type]
    assert "list of strings" in str(excinfo.value)


def test_shell_refuses_an_unlisted_binary():
    for argv in (["psql", "-c", "delete from job_runs"], ["rm", "-rf", "/mnt/data"], ["sh", "-c", "x"]):
        assert shell.permitted_entry(argv) is None, argv
        with pytest.raises(shell.RefusedCommand) as excinfo:
            shell.run(argv)
        assert "not an allow-listed binary" in str(excinfo.value)


# ---------------------------------------------------------------------------------------------
# 6-7: nothing gets around the wrapper, and the guard is precise rather than merely strict
# ---------------------------------------------------------------------------------------------


def _python_files(root: Path) -> list[Path]:
    """Every .py file under `root`, with the resolution itself asserted.

    CLAUDE.md § 21: a static assertion over a source tree must prove it resolved the source tree
    first. A scanner pointed at a directory that does not exist finds no violations and reports
    green forever.
    """
    if not root.is_dir():
        raise AssertionError(
            f"source tree not resolved: {root} is not a directory. A scanner that cannot see the "
            f"package reports no violations, which is indistinguishable from a clean package."
        )
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def subprocess_call_sites(root: Path, *, exempt: set[str]) -> list[str]:
    """Every AST call whose receiver is the `subprocess` module, outside the exempt filenames.

    Catches `subprocess.run(...)`, `subprocess.Popen(...)`, `subprocess.check_output(...)` and
    anything else reached through the module name, plus a bare `run(...)` imported via
    `from subprocess import run`. Does NOT match the same text inside a docstring or a comment,
    which is the whole reason this is an AST walk - three modules in this package name
    `subprocess.run` in prose while explaining why it is forbidden.
    """
    sites: list[str] = []
    for path in _python_files(root):
        if path.name in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # `from subprocess import run` binds a name that no longer says `subprocess` at the call
        # site, so the import itself is the violation.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                        sites.append(f"{path.name}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "subprocess":
                    names = ", ".join(alias.name for alias in node.names)
                    sites.append(f"{path.name}:{node.lineno}: from subprocess import {names}")
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"
                ):
                    sites.append(f"{path.name}:{node.lineno}: subprocess.{func.attr}(...)")
    return sites


def test_no_direct_subprocess_calls_outside_shell_module():
    """Every subprocess invocation in this package goes through the allow-list.

    This is the structural half of the guarantee that the verifiers cannot act. The allow-list is
    only worth what the absence of a way around it is worth.
    """
    scanned = _python_files(PACKAGE)

    # The scan must have found something. An empty package trivially has no violations.
    assert len(scanned) >= 4, f"expected the phase11 package to have >= 4 modules, found {scanned}"
    assert any(
        path.name == "shell.py" for path in scanned
    ), "shell.py must be among the scanned files, or the exemption is exempting nothing"

    sites = subprocess_call_sites(PACKAGE, exempt={"shell.py"})
    assert sites == [], (
        "expected: no subprocess use outside verify/phase11/shell.py\n"
        f"observed: {sites}\n"
        "Route it through shell.run, which enforces the read-only allow-list."
    )


def test_direct_subprocess_in_a_comment_is_ignored(tmp_path):
    """MUST STAY GREEN. The inverted mutation CLAUDE.md § 23 asks for.

    A guard that is merely strict is not the same as one that is correct. A regex over these
    modules would match the sentences in shell.py, result.py and __main__.py that explain why a
    direct call is forbidden - it would fail permanently, and the repair somebody reaches for is a
    weaker pattern that then misses real calls.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "shell.py").write_text("import subprocess\n", encoding="utf-8")
    (package / "stage_c.py").write_text(
        '"""A stage module.\n'
        "\n"
        "    Nothing here may call subprocess.run(...) directly; it goes through shell.run.\n"
        '    """\n'
        "\n"
        "# subprocess.Popen(['terraform', 'apply'])  <- forbidden, and only a comment\n"
        "from verify.phase11 import shell\n"
        "\n"
        "SUBPROCESS_NOTE = 'subprocess.run is not called here'\n"
        "\n"
        "def checks():\n"
        "    return shell.run(['terraform', 'show', '-json'])\n",
        encoding="utf-8",
    )

    assert subprocess_call_sites(package, exempt={"shell.py"}) == []

    # And the same scanner DOES catch the real thing, so its silence above is precision rather
    # than blindness. Without this half, a scanner that always returns [] passes.
    (package / "stage_d.py").write_text(
        "import subprocess\n\n\ndef checks():\n    return subprocess.run(['docker', 'rm', 'x'])\n",
        encoding="utf-8",
    )
    caught = subprocess_call_sites(package, exempt={"shell.py"})
    assert len(caught) == 2, caught
    assert any("import subprocess" in site for site in caught)
    assert any("subprocess.run(...)" in site for site in caught)


def test_the_scanner_fails_loudly_on_an_unresolved_tree(tmp_path):
    with pytest.raises(AssertionError) as excinfo:
        subprocess_call_sites(tmp_path / "does-not-exist", exempt=set())
    assert "source tree not resolved" in str(excinfo.value)
