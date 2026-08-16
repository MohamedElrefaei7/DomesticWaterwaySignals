"""The deploy script and the systemd unit.

Two of these run the script for real against a tmp_path, because a guard that has never been
watched refusing is not known to refuse (the same argument the live procedure makes about the
read-only database role). The rest read the file.
"""

from __future__ import annotations

import re
import subprocess

from . import (
    DEPLOY_SCRIPT_PATH,
    STACK_UNIT_PATH,
    executable_shell_lines,
    read_artifact,
)

DEPLOY_DIR_CONSTANT = "/opt/inland-waterway-signals"

# Every way a script could work out where it is instead of being told. CLAUDE.md § 5.
DERIVATION_TOKENS = ("$0", "BASH_SOURCE", "dirname", "realpath", "readlink", "$PWD", "$(pwd)")

RUNNER_PATHS = ("app.orchestration.migrate", "app/orchestration/migrate", "orchestration.migrate")


def run_deploy(*args, cwd=None):
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_the_deploy_path_is_a_fixed_absolute_path():
    """CLAUDE.md § 5: the canonical value, written down, never derived from the script's location.

    A path derived from `dirname $0` means something different when the script is run from a copy,
    and running it from a copy is exactly what somebody does when they are trying something out.
    The prior project lost four separate stale-file incidents to hand-copied directories in one
    session.
    """
    text = read_artifact(DEPLOY_SCRIPT_PATH)

    assert f'DEPLOY_DIR_DEFAULT="{DEPLOY_DIR_CONSTANT}"' in text, (
        f"the script does not declare the canonical deploy path {DEPLOY_DIR_CONSTANT!r} as a "
        f"literal constant"
    )

    for line in executable_shell_lines(DEPLOY_SCRIPT_PATH):
        if "DEPLOY_DIR" not in line:
            continue
        for token in DERIVATION_TOKENS:
            assert token not in line, (
                f"the deploy path is derived from the script's own context: {line.strip()!r} "
                f"contains {token!r}"
            )

    # The other half of that § 5 bullet, in the form it was actually protecting: nothing in this
    # script can destroy the checkout it operates on. See the script's header for why the
    # "refuse if the target contains .git" clause is inverted here and this is what stands in for
    # it.
    body = "\n".join(executable_shell_lines(DEPLOY_SCRIPT_PATH))
    for destructive in ("rsync", "--delete", "rm -rf", "rm -fr"):
        assert destructive not in body, (
            f"the deploy script contains {destructive!r}. It operates on a live git checkout at "
            f"{DEPLOY_DIR_CONSTANT}; a destructive sync there removes the thing being deployed."
        )


def test_the_script_refuses_to_run_if_the_target_lacks_a_git_directory(tmp_path):
    """Watched refusing, not asserted about.

    The deploy path is `git pull` on the server (CLAUDE.md § 5), so the target IS a checkout and
    its absence means this is not the deploy directory — most likely a directory somebody made by
    hand or unpacked into. Pulling into it would either fail confusingly or, worse, succeed
    against a tree nobody meant.
    """
    target = tmp_path / "not-a-checkout"
    target.mkdir()
    (target / ".env").write_text("POSTGRES_PASSWORD=irrelevant\n", encoding="utf-8")
    (target / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (target / "Caddyfile").write_text("example {}\n", encoding="utf-8")

    result = run_deploy("--deploy-dir", str(target), "--dry-run")

    assert result.returncode != 0, (
        f"the script exited {result.returncode} against a directory with no .git:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert ".git" in result.stderr, (
        f"the refusal does not name what was missing. A check that says FAIL without evidence "
        f"sends the operator off to re-derive what the script already knew (CLAUDE.md § 13). "
        f"stderr was: {result.stderr!r}"
    )

    # And it passes that same guard once the directory looks like a checkout — otherwise this test
    # is satisfied by a script that refuses everything, which is the vacuous-pass shape one
    # negation away.
    (target / ".git").mkdir()
    ok = run_deploy("--deploy-dir", str(target), "--dry-run")
    assert ok.returncode == 0, (
        f"the script still refused a directory with a .git, .env, compose file and Caddyfile:\n"
        f"{ok.stdout}\n{ok.stderr}"
    )
    assert "DRY-RUN" in ok.stdout


def test_the_script_never_runs_migrations():
    """CLAUDE.md § 3, at the deploy layer.

    A deploy that applies schema changes every time it runs is a deploy that cannot be rolled back
    by re-running an earlier commit. Comments are stripped so the script can explain the rule it
    obeys — the same precedent as
    tests/orchestration/test_migration_ordering.py::test_compose_file_does_not_invoke_the_migration_runner,
    which strips them so docker-compose.yml can carry the same explanation.
    """
    executable = "\n".join(executable_shell_lines(DEPLOY_SCRIPT_PATH))

    for path in RUNNER_PATHS:
        assert path not in executable, f"the deploy script invokes the schema runner ({path})"

    assert "migrate" not in executable, (
        "an executable line of the deploy script names `migrate`. Schema changes are a CLI a human "
        "invokes, deliberately and while watching."
    )
    assert "alembic" not in executable and "flyway" not in executable


def test_the_script_builds_the_frontend_before_bringing_the_stack_up():
    """Ordering, asserted by position rather than by presence.

    Both lines exist in either order and both "work" — Compose would build the image on the way up
    anyway. What the order buys is that a build failure stops the deploy with the PREVIOUS bundle
    still being served, instead of after the stack has been recreated around a broken one.
    """
    lines = executable_shell_lines(DEPLOY_SCRIPT_PATH)

    build_indexes = [
        i for i, line in enumerate(lines)
        if re.search(r"docker\s+compose\s+build\b.*frontend-build", line)
    ]
    up_indexes = [
        i for i, line in enumerate(lines)
        if re.search(r"docker\s+compose\s+up\b.*-d", line)
    ]

    assert build_indexes, "the deploy script never builds the frontend image"
    assert up_indexes, "the deploy script never brings the stack up"
    assert min(build_indexes) < min(up_indexes), (
        f"`docker compose up -d` (line {min(up_indexes)}) comes before the frontend build "
        f"(line {min(build_indexes)})"
    )


def test_the_systemd_unit_carries_requiresmountsfor():
    """Decision 11, and the contract provisioning 1 left owed.

    The fstab entry is `nofail` by design, so boot proceeds without the data volume and /mnt/data
    exists as an empty directory on the root disk. Every layer above reads that as a healthy empty
    world: Postgres initialises a new cluster into it and Caddy finds no ACME account key and asks
    Let's Encrypt for fresh certificates. This line is what makes the mount's absence stop the
    application instead of being discovered in the data.
    """
    unit = read_artifact(STACK_UNIT_PATH)

    assert re.search(r"^RequiresMountsFor=/mnt/data\s*$", unit, re.MULTILINE), (
        "the stack unit does not carry `RequiresMountsFor=/mnt/data`. With `nofail` in fstab, its "
        "absence means the stack starts happily against an empty directory on the root disk."
    )

    assert re.search(r"^Requires=docker\.service\s*$", unit, re.MULTILINE), (
        "the unit does not Require docker.service - without it a failed daemon produces a unit "
        "that reports a start that did not happen"
    )
    assert re.search(r"^After=.*\bdocker\.service\b", unit, re.MULTILINE), (
        "the unit does not order itself after docker.service"
    )
    assert re.search(r"^WorkingDirectory=" + re.escape(DEPLOY_DIR_CONSTANT) + r"\s*$", unit, re.MULTILINE), (
        f"the unit's WorkingDirectory is not {DEPLOY_DIR_CONSTANT} - Compose reads .env from its "
        f"working directory, so this is also where the secrets come from"
    )
    assert "RemainAfterExit=yes" in unit, (
        "a Type=oneshot unit without RemainAfterExit reports itself inactive the moment "
        "`docker compose up -d` returns, and `systemctl status` then describes a running stack as "
        "dead"
    )

    for path in RUNNER_PATHS:
        assert path not in unit, f"the systemd unit invokes the schema runner ({path})"
