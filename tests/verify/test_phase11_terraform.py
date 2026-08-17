"""Unit tier — the Stage C and Stage D verifiers, against committed plan and state fixtures.

NO TEST HERE MAKES A LIVE AWS CALL OR RUNS TERRAFORM. Every check function takes its input as an
argument rather than reading the world, which is the same split that lets
`tests/verify/test_preflight_checks.py` exist at all.

THE FIXTURES ARE HAND-BUILT AND SANITISED, AND BOTH HALVES OF THAT NEED SAYING. Their SHAPE was
verified against real Terraform 1.15.8 output on 2026-08-17 - `terraform show -json` over this
project's own state, and `terraform show -json <planfile>` over a scratch configuration - and the
two measurements that drove the design are recorded in `verify/phase11/stage_c.py`'s docstring.
Their VALUES are sanitised: `.gitignore` keeps `*.tfstate` out of this repo deliberately, and a
fixture cut from real state would put the account id, the EIP and the instance id back in.
"""

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify.phase11 import shell, stage_c, stage_d, tfjson  # noqa: E402
from verify.phase11.protected import PROTECTED_ADDRESSES  # noqa: E402
from verify.phase11.result import FAIL, PASS, Precondition  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
STATE_BUCKET = "domestic-waterway-signals-tfstate"


def fixture(name: str) -> dict:
    path = FIXTURES / name
    assert path.exists(), f"fixture not resolved: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def expected_plan():
    return fixture("d_pre_expected.plan.json")


# ---------------------------------------------------------------------------------------------
# The two document types, and the vacuous pass between them
# ---------------------------------------------------------------------------------------------


def test_a_state_document_is_refused_as_a_plan():
    """The whole reason `c-post` takes a plan file.

    MEASURED on Terraform 1.15.8: `terraform show -json` with no plan file emits a STATE document
    whose top-level keys are ['checks', 'format_version', 'terraform_version', 'values'] - there is
    no `resource_changes` key at all. So "assert there are no resource changes" over that document
    is true on every input, forever, including on a migration that carried nothing. That is
    CLAUDE.md § 2's theme 2: a check that verifies the exact thing responsible for a failure and
    reports it correct.

    It is a Precondition (exit 2) rather than a FAIL, because the operator ran the wrong command -
    it is not a finding about the infrastructure.
    """
    state = fixture("state_protected.json")
    assert "resource_changes" not in state

    with pytest.raises(Precondition) as excinfo:
        tfjson.require_plan(state, what="c-post plan")

    message = str(excinfo.value)
    assert "not a plan document" in message
    assert "passes vacuously" in message
    # The observed value, per CLAUDE.md § 13.
    assert "'values'" in message


def test_a_no_changes_plan_is_not_an_empty_resource_changes_list():
    """MEASURED on 1.15.8: an unchanged plan carries one ['no-op'] entry per resource.

    Asserting `resource_changes == []` would therefore fail on a correct migration of seventeen
    resources - and a check that goes red when everything is right gets deleted by whoever hits it.
    """
    plan = fixture("c_post_no_changes.plan.json")
    changes = tfjson.resource_changes(plan)

    assert len(changes) == len(PROTECTED_ADDRESSES) == 17
    assert all(tfjson.actions(entry) == ("no-op",) for entry in changes)
    assert plan["applyable"] is False

    assert stage_c.check_plan_has_no_pending_changes(plan).status == PASS


# ---------------------------------------------------------------------------------------------
# c-post
# ---------------------------------------------------------------------------------------------


def test_c_post_fails_when_plan_has_resource_changes():
    """A plan that wants to CREATE what already exists means the migration carried nothing."""
    result = stage_c.check_plan_has_no_pending_changes(fixture("c_post_wants_to_create.plan.json"))

    assert result.status == FAIL
    assert "pending" in result.observed
    assert "aws_ebs_volume.data ['create']" in result.observed
    assert "second copy" in result.observed


def test_c_post_fails_when_local_state_file_present():
    result = stage_c.check_local_state_files_absent(["terraform.tfstate"])

    assert result.status == FAIL
    assert "terraform.tfstate" in result.observed
    assert "second copy of the truth" in result.observed

    assert stage_c.check_local_state_files_absent([]).status == PASS


def test_c_post_fails_when_no_object_versions_at_key():
    result = stage_c.check_object_versions_exist([], STATE_BUCKET, "infra/terraform.tfstate")

    assert result.status == FAIL
    assert "0 versions" in result.observed
    assert "not versioning" in result.observed


def test_c_post_fails_when_every_version_id_is_null():
    """S3 reports VersionId "null" for objects written while versioning was OFF.

    A count alone passes that case, and versioning is the recovery path for a truncated state
    write - the one thing this check exists to establish.
    """
    result = stage_c.check_object_versions_exist(
        [{"Key": "infra/terraform.tfstate", "VersionId": "null"}],
        STATE_BUCKET,
        "infra/terraform.tfstate",
    )
    assert result.status == FAIL
    assert "versioning was OFF" in result.observed

    ok = stage_c.check_object_versions_exist(
        [{"Key": "infra/terraform.tfstate", "VersionId": "3sL9x"}],
        STATE_BUCKET,
        "infra/terraform.tfstate",
    )
    assert ok.status == PASS


def test_c_post_fails_when_the_plan_is_empty():
    """An empty `resource_changes` means the plan was computed against empty state.

    This is the case the prompt's "no resource_changes at all" formulation would have PASSED, and
    it is the exact symptom of a migration that carried nothing.
    """
    empty = dict(fixture("c_post_no_changes.plan.json"), resource_changes=[], applyable=False)
    result = stage_c.check_plan_has_no_pending_changes(empty)

    assert result.status == FAIL
    assert "EMPTY" in result.observed
    assert "carried nothing" in result.observed


def test_c_pre_fails_when_the_state_bucket_is_not_versioned():
    """`get-bucket-versioning` returns an EMPTY document when versioning was never enabled."""
    never = stage_c.check_bucket_versioning_enabled({}, STATE_BUCKET)
    assert never.status == FAIL
    assert "Status=None" in never.observed
    assert "never enabled" in never.observed

    suspended = stage_c.check_bucket_versioning_enabled({"Status": "Suspended"}, STATE_BUCKET)
    assert suspended.status == FAIL

    assert stage_c.check_bucket_versioning_enabled({"Status": "Enabled"}, STATE_BUCKET).status == PASS


def test_backend_literals_are_read_from_the_files_and_agree():
    """The bucket name is written twice by necessity (§ 8); both copies are read, never restated."""
    assert stage_c.state_bucket_name() == STATE_BUCKET
    assert stage_c.bootstrap_bucket_default() == STATE_BUCKET
    assert stage_c.state_key() == "infra/terraform.tfstate"

    assert stage_c.check_backend_literals_agree("a", "a").status == PASS
    drift = stage_c.check_backend_literals_agree("a", "b")
    assert drift.status == FAIL
    assert "'a'" in drift.observed and "'b'" in drift.observed


# ---------------------------------------------------------------------------------------------
# d-pre
# ---------------------------------------------------------------------------------------------


def test_d_pre_passes_on_the_expected_plan(expected_plan):
    """Every check green on the plan Stage D is supposed to produce.

    Includes the bucket policy's DENY on `s3:*`, which is in the fixture verbatim from backups.tf.
    A wildcard check that was not scoped to `aws_iam_policy` and to `Effect: Allow` would fail
    here - on the correct plan - and the repair somebody reaches for is a weaker check.
    """
    results = [
        stage_d.check_protected_addresses_untouched(expected_plan),
        stage_d.check_state_matches_protected_list(expected_plan),
        stage_d.check_plan_creates_the_phase_11_set(expected_plan),
        stage_d.check_exactly_one_bucket_created(expected_plan),
        stage_d.check_iam_allows_no_delete(expected_plan),
        stage_d.check_iam_allows_no_wildcard(expected_plan),
        stage_d.check_iam_excludes_the_state_bucket(expected_plan, STATE_BUCKET),
    ]
    assert [r.status for r in results] == [PASS] * 7, [r.render() for r in results if r.status != PASS]

    # And the deny statement really is in the fixture, or the paragraph above is asserting nothing.
    text = json.dumps(expected_plan)
    assert '\\"Effect\\": \\"Deny\\"' in text and '\\"Action\\": \\"s3:*\\"' in text


def test_d_pre_fails_on_destroy_of_protected_address():
    result = stage_d.check_protected_addresses_untouched(
        fixture("d_pre_destroys_instance.plan.json")
    )
    assert result.status == FAIL
    assert "aws_instance.main ['delete', 'create']" in result.observed


def test_d_pre_fails_on_update_of_protected_address():
    """An in-place update is refused too. "It was only an update" is how a replacement is waved through."""
    result = stage_d.check_protected_addresses_untouched(fixture("d_pre_updates_volume.plan.json"))
    assert result.status == FAIL
    assert "aws_ebs_volume.data ['update']" in result.observed


def test_d_pre_fails_when_state_contains_address_not_in_protected_list():
    """The check above protects what somebody listed. This one protects what actually exists.

    Without it, a resource added next month and not added to protected.py is unprotected and the
    verifier is green - CLAUDE.md § 22's preflight gate 1, on infrastructure.
    """
    result = stage_d.check_state_matches_protected_list(
        fixture("d_pre_state_has_extra_address.plan.json")
    )
    assert result.status == FAIL
    assert "aws_cloudfront_distribution.cdn" in result.observed
    assert "NOT protected" in result.observed


def test_d_pre_reports_a_protected_address_missing_from_state_too():
    """Both directions. An address in the list but not in state is a resource that has gone."""
    plan = fixture("d_pre_expected.plan.json")
    resources = plan["prior_state"]["values"]["root_module"]["resources"]
    plan["prior_state"]["values"]["root_module"]["resources"] = [
        r for r in resources if r["address"] != "aws_ebs_volume.data"
    ]

    result = stage_d.check_state_matches_protected_list(plan)
    assert result.status == FAIL
    assert "ABSENT from state" in result.observed
    assert "aws_ebs_volume.data" in result.observed


def test_d_pre_fails_on_two_buckets_created():
    """Exactly one, not at least one. Two means bootstrap/ was folded into the main configuration."""
    result = stage_d.check_exactly_one_bucket_created(fixture("d_pre_two_buckets.plan.json"))
    assert result.status == FAIL
    assert "2:" in result.observed
    assert "aws_s3_bucket.backups" in result.observed
    assert "aws_s3_bucket.state" in result.observed


def test_d_pre_fails_on_iam_delete_action():
    result = stage_d.check_iam_allows_no_delete(fixture("d_pre_iam_delete.plan.json"))
    assert result.status == FAIL
    assert "s3:DeleteObject" in result.observed
    assert "aws_iam_policy.backups" in result.observed


def test_d_pre_passes_when_delete_appears_only_in_a_description():
    """THE FALSE POSITIVE, and the test that can tell a parse from a substring search.

    A substring search over the plan text for "Delete" still CATCHES `s3:DeleteObject`, so
    `test_d_pre_fails_on_iam_delete_action` stays green under that mutation and cannot distinguish
    the two implementations. This one can: the fixture's policy description says "Delete is
    intentionally not granted" and the policy grants no delete at all.

    This project's real `aws_iam_policy.backups` description already carries the lowercase form -
    "No delete - retention is a lifecycle rule" - so a case-insensitive search fails on the actual
    plan.
    """
    plan = fixture("d_pre_delete_only_in_description.plan.json")
    assert "Delete" in json.dumps(plan), "the fixture must contain the word, or it proves nothing"

    assert stage_d.check_iam_allows_no_delete(plan).status == PASS


def test_d_pre_fails_on_iam_wildcard_action():
    result = stage_d.check_iam_allows_no_wildcard(fixture("d_pre_iam_wildcard.plan.json"))
    assert result.status == FAIL
    assert "s3:*" in result.observed


def test_d_pre_refuses_an_allow_with_not_action():
    """`NotAction` in an Allow grants everything except what it names - a wildcard written backwards."""
    plan = fixture("d_pre_expected.plan.json")
    for entry in plan["resource_changes"]:
        if entry["address"] == "aws_iam_policy.backups":
            policy = json.loads(entry["change"]["after"]["policy"])
            policy["Statement"].append(
                {"Sid": "Sneaky", "Effect": "Allow", "NotAction": ["s3:DeleteBucket"],
                 "Resource": ["*"]}
            )
            entry["change"]["after"]["policy"] = json.dumps(policy)

    result = stage_d.check_iam_allows_no_wildcard(plan)
    assert result.status == FAIL
    assert "NotAction" in result.observed


def test_d_pre_fails_when_iam_resource_includes_state_bucket():
    """An instance that can reach state is an instance that can lie about what infrastructure exists."""
    result = stage_d.check_iam_excludes_the_state_bucket(
        fixture("d_pre_iam_reaches_state_bucket.plan.json"), STATE_BUCKET
    )
    assert result.status == FAIL
    assert STATE_BUCKET in result.observed
    assert "ReadState" in result.observed


def test_d_pre_fails_when_the_plan_contains_no_iam_policy_at_all():
    """A gate over an empty collection must not be green (CLAUDE.md § 22).

    Every refusal in this stage is satisfied by a plan that grants nothing because it creates
    nothing, so the absence of any policy has to be its own failure.
    """
    plan = fixture("d_pre_expected.plan.json")
    plan["resource_changes"] = [
        entry for entry in plan["resource_changes"] if entry["type"] != "aws_iam_policy"
    ]

    result = stage_d.check_iam_allows_no_delete(plan)
    assert result.status == FAIL
    assert "empty collection" in result.observed


def test_d_pre_fails_when_the_plan_creates_something_unexpected():
    result = stage_d.check_plan_creates_the_phase_11_set(fixture("d_pre_two_buckets.plan.json"))
    assert result.status == FAIL
    assert "aws_s3_bucket.state" in result.observed


def test_d_pre_reads_a_plan_that_does_not_exist_as_a_precondition(tmp_path):
    with pytest.raises(Precondition) as excinfo:
        stage_d.checks(str(tmp_path / "absent.json"))
    assert "does not exist" in str(excinfo.value)
    assert "never runs `terraform plan`" in str(excinfo.value)


def test_d_pre_wires_every_check(expected_plan, tmp_path):
    """The builder returns all seven checks, so none is defined and then left unwired."""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(expected_plan), encoding="utf-8")

    built = stage_d.checks(str(path), state_bucket=STATE_BUCKET)
    assert len(built) == 7
    assert [check().status for check in built] == [PASS] * 7


# ---------------------------------------------------------------------------------------------
# The verifiers cannot plan and cannot apply
# ---------------------------------------------------------------------------------------------


def _package_modules() -> list[Path]:
    package = REPO_ROOT / "verify" / "phase11"
    assert package.is_dir(), (
        f"source tree not resolved: {package}. A walk that sees no modules finds no forbidden "
        f"commands, which is indistinguishable from a package that issues none."
    )
    modules = sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(modules) >= 6, f"expected >= 6 modules under {package}, found {modules}"
    return modules


def _command_literals() -> list[tuple[str, int, list[str]]]:
    """Every list-of-string-literals in the package whose first element names an allow-listed binary.

    Keyed on the LITERAL rather than on which function receives it. `shell.run` is not always
    called with the list inline - `stage_c._aws_json` takes an argv and forwards it - so a walk
    that only looked at `shell.run(...)` call sites would report the forwarding helper as opaque
    and see none of the four AWS commands it actually issues. What matters is that every
    command-shaped literal in the package is one the allow-list permits, wherever it is written.

    Only the CONSTANT PREFIX is taken. `["aws", "s3api", "list-object-versions", "--bucket",
    bucket, "--prefix", key]` interpolates its arguments, which is fine and necessary; what must be
    literal is the verb. A command whose SUBCOMMAND is built at runtime is one this walk cannot
    read, and it fails the prefix match rather than being skipped.
    """
    found: list[tuple[str, int, list[str]]] = []
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and first.value in shell.PERMITTED):
                continue
            prefix: list[str] = []
            for element in node.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    prefix.append(element.value)
                else:
                    break
            found.append((path.name, node.lineno, prefix))
    return found


def _dynamic_shell_run_sites() -> list[tuple[str, int]]:
    """`shell.run(...)` calls whose first argument is neither a list literal nor a plain name.

    An argv assembled inline - `["terraform"] + verbs`, or a list whose first element is an
    f-string - is one `_command_literals` cannot read, and an unreadable command is an unchecked
    one.
    """
    offenders: list[tuple[str, int]] = []
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "run"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "shell"):
                continue
            if not node.args:
                offenders.append((path.name, node.lineno))
                continue
            first = node.args[0]
            if isinstance(first, ast.Name):
                continue
            if isinstance(first, ast.List) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in first.elts
            ):
                continue
            offenders.append((path.name, node.lineno))
    return offenders


def test_verifiers_never_invoke_plan_or_apply():
    """Asserted against the Part 1 allow-list, and against what the modules actually call.

    Two halves, because neither is enough. The allow-list assertion proves `plan` and `apply` are
    unreachable through the wrapper; the AST walk proves nothing in the package tries a command the
    wrapper would refuse - which would be a verifier that raises in the middle of a deployment
    rather than one that quietly does the wrong thing, but is still a defect worth catching here
    rather than at 3am.
    """
    for argv in (
        ["terraform", "plan"],
        ["terraform", "plan", "-out=phase11.tfplan"],
        ["terraform", "apply"],
        ["terraform", "apply", "phase11.tfplan"],
        ["terraform", "destroy", "-auto-approve"],
    ):
        assert shell.permitted_entry(argv) is None, argv

    literals = _command_literals()
    # A floor, not a count: later parts add commands and this must not need bumping to stay
    # honest. What it buys is that the walk cannot report a clean package by finding nothing.
    assert len(literals) >= 3, f"the walk resolved almost nothing: {literals}"
    # It must actually have seen the AWS reads, which live in a forwarding helper rather than at a
    # shell.run call site - the case that made a call-site-only walk report nothing.
    assert any(argv[0] == "aws" for _, _, argv in literals), literals
    assert any(argv[0] == "terraform" for _, _, argv in literals), literals

    unpermitted = [
        (name, lineno, argv)
        for name, lineno, argv in literals
        if shell.permitted_entry(argv) is None
    ]
    assert unpermitted == [], (
        "expected: every command literal in verify/phase11/ matches an allow-list entry\n"
        f"observed: {unpermitted}"
    )

    assert _dynamic_shell_run_sites() == [], (
        "expected: every shell.run argv is a literal list or a forwarded name\n"
        f"observed: {_dynamic_shell_run_sites()}"
    )


def test_the_command_walk_would_catch_a_plan_invocation():
    """The walk above is only worth what its precision is worth (CLAUDE.md § 23's inverted check).

    A scanner that always returns [] passes `test_verifiers_never_invoke_plan_or_apply` on any
    codebase. This drives the same predicate over a source string that DOES contain the forbidden
    command and requires it to be caught.
    """
    source = (
        "from verify.phase11 import shell\n"
        "\n"
        "# shell.run(['terraform', 'apply'])  <- a comment, and must be ignored\n"
        "def go():\n"
        "    return shell.run(['terraform', 'plan', '-out=x'])\n"
    )
    argvs = [
        [e.value for e in node.elts]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.List)
        and node.elts
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)
        and node.elts[0].value in shell.PERMITTED
    ]

    assert argvs == [["terraform", "plan", "-out=x"]], argvs
    assert shell.permitted_entry(argvs[0]) is None
