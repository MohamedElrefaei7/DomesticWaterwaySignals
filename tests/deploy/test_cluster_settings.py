"""The cluster's settings: enforced overrides, a recorded baseline, and the gate between them.

`postgresql.conf` lives in PGDATA on the data volume and cannot be committed - see the header of
`infra/postgres/settings.py` for why mounting it breaks a fresh `initdb` and why `include_dir` is
not available. What IS achievable is that the committed values are authoritative and a divergence
is detected, and these tests are about the detecting.

THE TWO HALVES OF THE GATE ARE TESTED SEPARATELY AND NEITHER SUBSUMES THE OTHER. A gate reading
the running value alone passes a cluster that has been ALTER SYSTEM'd downwards and not yet
restarted; a gate reading `pending_restart` alone passes a cluster where nobody ever applied
anything. Those are `test_..._pending_restart_is_true` and `test_..._running_value_is_lower`, and
deleting either half of the implementation turns exactly one of them red.
"""

from __future__ import annotations

import ast
import inspect

from infra.postgres import settings as cluster_settings
from infra.postgres.settings import REQUIRED_SETTINGS, RequiredSetting
from verify import preflight

from . import REPO_ROOT

SETTINGS_SOURCE_PATH = REPO_ROOT / "infra" / "postgres" / "settings.py"

LOCKS = RequiredSetting(
    name="max_locks_per_transaction",
    minimum=512,
    reason="a fixture, not the committed reason",
)


def _observed(value: int, *, pending: bool = False, source: str = "configuration file"):
    return preflight.ObservedSetting(
        name=LOCKS.name, value=value, source=source, pending_restart=pending
    )


# ---------------------------------------------------------------------------------------------
# The gate's verdict
# ---------------------------------------------------------------------------------------------


def test_required_settings_gate_passes_when_values_agree():
    result = preflight.check_required_setting(LOCKS, _observed(512))

    assert result.status == preflight.PASS, result.detail
    assert "512" in result.detail, (
        f"a passing gate still reports the observed value (CLAUDE.md § 13): {result.detail!r}"
    )


def test_required_settings_gate_passes_above_the_floor():
    """The requirement is a FLOOR, not an equality.

    A future session raising this for its own reason must not have to edit REQUIRED_SETTINGS in
    the same commit to keep the gate green - equality would only guarantee that nobody may ever be
    more generous than us.
    """
    assert preflight.check_required_setting(LOCKS, _observed(1024)).status == preflight.PASS


def test_required_settings_gate_fails_when_running_value_is_lower():
    """The never-applied case: nothing pending, running below the floor.

    THIS IS THE CASE A `pending_restart`-ONLY GATE PASSES, and it is the state this instance was
    actually in - 128, cluster default, nobody had ever run ALTER SYSTEM - while its largest table
    was not fully queryable.
    """
    result = preflight.check_required_setting(LOCKS, _observed(128, pending=False))

    assert result.status == preflight.FAIL
    assert "128" in result.detail and "512" in result.detail, (
        f"the failure must report both the observed value and the floor: {result.detail!r}"
    )


def test_required_settings_gate_fails_when_pending_restart_is_true():
    """The lowered-and-not-yet-restarted case: running value MEETS the floor, and is about to not.

    THE FIXTURE IS THE POINT. `_observed(512, pending=True)` is a cluster whose running value
    satisfies the requirement, so a gate reading `setting` alone reports PASS - and the restart
    that makes it false will happen at boot, unattended, long after the ALTER SYSTEM has left
    anybody's shell history. A fixture of `_observed(128, pending=True)` would go red under that
    mutation too, for the wrong reason, and would prove nothing about `pending_restart`.
    """
    result = preflight.check_required_setting(LOCKS, _observed(512, pending=True))

    assert result.status == preflight.FAIL, (
        "a cluster whose configuration on disk disagrees with what it is RUNNING is not verified, "
        "even when the running value happens to meet the floor"
    )
    assert "512" in result.detail


def test_gate_distinguishes_pending_restart_from_never_set():
    """Two failures, two messages, two different actions - restart it, or apply it."""
    pending = preflight.check_required_setting(LOCKS, _observed(512, pending=True)).detail
    never = preflight.check_required_setting(LOCKS, _observed(128, pending=False)).detail

    assert pending != never
    assert "RESTART PENDING" in pending and "RESTART PENDING" not in never
    assert "NEVER APPLIED" in never and "NEVER APPLIED" not in pending
    assert "restart" in pending.lower(), (
        f"the pending failure must name the fix, which is a restart: {pending!r}"
    )


def test_gate_fails_when_the_cluster_reports_no_such_setting():
    result = preflight.check_required_setting(LOCKS, None)

    assert result.status == preflight.FAIL
    assert LOCKS.name in result.detail


def test_gate_skips_rather_than_passes_without_a_database(monkeypatch):
    """No DATABASE_URL means the setting was NOT CHECKED, and the run must exit non-zero.

    CLAUDE.md § 13: a skipped check that exits zero reads as green in every log and in the memory
    of the person who ran it.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    results = preflight.gate_cluster_settings()

    assert results, "the gate returned nothing at all, which reads as nothing to object to"
    assert all(result.status == preflight.SKIP for result in results), (
        f"expected every result to SKIP without a database, got "
        f"{[(r.name, r.status) for r in results]}"
    )
    assert preflight.exit_code(results) != 0, "a SKIP must not exit zero"
    assert all("not a pass" in result.detail for result in results)


# ---------------------------------------------------------------------------------------------
# Recorded, not enforced
# ---------------------------------------------------------------------------------------------


def _gate_functions():
    """Every gate preflight actually runs, by source. The walk asserts it found them."""
    source = (REPO_ROOT / "verify" / "preflight.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("gate_")
    ]
    assert len(gates) >= 5, (
        f"the walk found {len(gates)} gate functions in verify/preflight.py, which means it "
        f"resolved the wrong tree or the naming convention changed - a scan that finds nothing "
        f"passes vacuously (CLAUDE.md § 21)"
    )
    return gates


def test_tuner_baseline_is_recorded_not_enforced():
    """NO GATE READS THE BASELINE, and that is asserted at the call site rather than behaviourally.

    The baseline's values are a function of the instance's memory and cpu count. A rebuild onto a
    larger instance derives a larger `shared_buffers` CORRECTLY, so a gate enforcing the baseline
    would go red on a working cluster - the shape recorded in CONTEXT.md from `d-pre`, which gets
    a guard disabled rather than fixed.

    This is CLAUDE.md § 23's legitimate kind of source test: the call site IS the invariant. A
    baseline comparison that is read and then not acted on is exactly as much of a violation as
    one that returns FAIL, because the next edit makes it act.
    """
    forbidden = {"load_tuner_baseline", "TUNER_BASELINE_PATH", "TunerBaseline"}
    found = []
    for gate in _gate_functions():
        for node in ast.walk(gate):
            if isinstance(node, ast.Name) and node.id in forbidden:
                found.append((gate.name, node.id))
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                found.append((gate.name, node.attr))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "tuner-baseline" in node.value or "tuner_baseline" in node.value:
                    found.append((gate.name, node.value))

    assert not found, (
        f"a preflight gate reads the tuner baseline: {found}. The baseline is RECORDED, NOT "
        f"ENFORCED - enforcing it makes the gate fail on a correctly re-derived cluster of a "
        f"different size. Compare it with --resolve-baseline, which returns no verdict."
    )


def test_gate_fails_rather_than_passes_when_no_settings_are_required(monkeypatch):
    """An emptied REQUIRED_SETTINGS must not read as a clean run over nothing."""
    monkeypatch.setattr(preflight, "REQUIRED_SETTINGS", ())

    results = preflight.gate_cluster_settings()

    assert results and all(r.status == preflight.FAIL for r in results), (
        f"an empty REQUIRED_SETTINGS produced {[(r.name, r.status) for r in results]} - a gate "
        f"over an empty collection reports every member of it as verified (CLAUDE.md § 22)"
    )


def test_committed_baseline_placeholder_cannot_be_mistaken_for_a_capture():
    """Until `--write-baseline` has run, the baseline must say so rather than look empty.

    `{}` would read as "captured, and this cluster has no non-default settings", which is the
    placeholder-that-resolves failure CLAUDE.md § 12 forbids for image digests.
    """
    baseline = cluster_settings.load_tuner_baseline()

    if not baseline.settings:
        assert not baseline.is_captured, (
            "the baseline holds no settings but reports itself captured - an empty capture is "
            "indistinguishable from a cluster running nothing but defaults"
        )
    else:
        assert baseline.is_captured and baseline.captured_at


# ---------------------------------------------------------------------------------------------
# The written reason
# ---------------------------------------------------------------------------------------------


def test_required_settings_carries_the_lock_arithmetic():
    """The file states slots = value * (max_connections + max_prepared_transactions).

    CLAUDE.md § 23'S LEGITIMATE KIND OF SOURCE TEST, and the distinction is worth stating because
    the illegitimate kind looks identical. There is no behaviour here to mutate: the arithmetic is
    not computed by this module, it is the REASON the number is 512 rather than 256, and the
    written reason is the only thing standing between this value and somebody economising on
    shared memory who cannot see what it was sized against. Its absence IS the defect.
    """
    source = SETTINGS_SOURCE_PATH.read_text(encoding="utf-8")

    for term in ("max_locks_per_transaction", "max_connections", "max_prepared_transactions"):
        assert term in source, f"the arithmetic's terms are incomplete: {term!r} is absent"

    assert "max_locks_per_transaction * (max_connections + max_prepared_transactions)" in source, (
        "the file does not state the slot formula. A bare 512 with no arithmetic beside it is a "
        "round number, and a round number gets tidied."
    )
    for evidence in ("3,200", "12,800", "986", "258,739"):
        assert evidence in source, (
            f"the file does not state {evidence!r} - the reason must carry the measured demand "
            f"the floor was sized against, not only the formula"
        )
    assert "270 bytes" in source, "the file does not state what the extra slots cost"


def test_the_lock_setting_is_required_at_512():
    by_name = {required.name: required for required in REQUIRED_SETTINGS}

    assert "max_locks_per_transaction" in by_name, (
        "max_locks_per_transaction is not enforced. It is the setting that made the project's "
        "largest table unqueryable; see infra/postgres/settings.py."
    )
    assert by_name["max_locks_per_transaction"].minimum == 512


def test_lock_table_evidence_reports_this_cluster_s_own_arithmetic():
    """The slot count is COMPUTED from what the cluster reports, never copied from this instance."""
    evidence = preflight.lock_table_evidence(
        {"max_connections": 25, "max_prepared_transactions": 0}, _observed(512)
    )

    assert "12,800" in evidence, evidence
    assert "25" in evidence

    bigger = preflight.lock_table_evidence(
        {"max_connections": 100, "max_prepared_transactions": 0}, _observed(512)
    )
    assert "51,200" in bigger, (
        f"the evidence did not recompute against a different max_connections: {bigger!r} - a "
        f"hardcoded slot count would report this instance's number on every instance"
    )


# ---------------------------------------------------------------------------------------------
# The route this commit did not take
# ---------------------------------------------------------------------------------------------


def test_compose_still_has_no_command_or_entrypoint():
    """Regression guard: the settings did NOT arrive as `command: postgres -c ...`.

    That is the shortest way to get a committed setting onto the cluster, and
    tests/orchestration/test_migration_ordering.py forbids both keys because they are the
    mechanism by which migration-on-start returns. Weakening a structural guard to a lexical one
    to make room for this commit would have been the wrong trade, so the guard is asserted again
    here, from the side of the change that wanted it.
    """
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in compose_text.splitlines() if not line.strip().startswith("#")
    )

    for key in ("command:", "entrypoint:"):
        assert key not in executable, (
            f"docker-compose.yml sets `{key}`. Cluster settings are applied by ALTER SYSTEM (see "
            f"docs/runbooks/cluster-settings.md), never by overriding the image's entrypoint."
        )


def test_nothing_in_the_repo_issues_alter_system():
    """Applying a setting is a human step, like `terraform apply` (CLAUDE.md § 1).

    THE SUBJECT IS WHAT GETS EXECUTED, NOT WHAT THE FILE SAYS, and the first version of this test
    got that wrong in the way this project has now hit four times: it scanned every string
    constant, matched `check_required_setting`'s own failure message - which names ALTER SYSTEM to
    explain the fix - and reported a correct file as broken. Same shape as preflight's version
    parser reading a Dockerfile comment, and the two module docstrings that name the call they
    forbid. A CHECK WHOSE SUBJECT IS TEXT MUST EXCLUDE THE TEXT THAT DOCUMENTS THE CHECK, and the
    way to do that is to narrow the subject rather than to weaken the pattern: this walks the
    arguments of `.execute(...)` calls, so prose is not in scope at all.

    Confirmed by an INVERTED mutation, per CLAUDE.md § 23: the docstrings and messages naming
    ALTER SYSTEM are already present, and this test is green. A guard that is merely strict is not
    the same as one that is correct.
    """
    tree = ast.parse(inspect.getsource(preflight))

    executed = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                executed.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                executed.append(ast.unparse(arg))

    assert executed, (
        "the walk found no .execute() calls in verify/preflight.py at all - a scan that finds "
        "nothing passes vacuously (CLAUDE.md § 21), and this gate queries pg_settings"
    )
    offending = [sql for sql in executed if "ALTER SYSTEM" in sql.upper()]
    assert not offending, (
        f"verify/preflight.py EXECUTES an ALTER SYSTEM: {offending}. Preflight reads the cluster "
        f"and reports; applying a setting is the human's step (CLAUDE.md § 1)."
    )
