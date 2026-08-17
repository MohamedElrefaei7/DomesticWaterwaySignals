"""Stage D pre-apply — what the Phase 11 plan must and must not contain.

`d-pre` READS A PLAN FILE THE HUMAN ALREADY CREATED. It never runs `terraform plan`; the allow-list
in `shell.py` omits the subcommand, so that is structural rather than a promise. The reason is that
the artifact reviewed has to be the artifact applied: a verifier generating its own plan leaves a
window - minutes, but enough for a changed variable, someone else's apply, or drift - in which the
plan a human read and the plan they then apply are two different documents.

    terraform -chdir=infra/terraform plan -out=phase11.tfplan     # human
    terraform -chdir=infra/terraform show -json phase11.tfplan > phase11.plan.json   # human
    python3 -m verify.phase11 d-pre phase11.plan.json             # this
    terraform -chdir=infra/terraform apply phase11.tfplan         # human

The apply is the SAME FILE the plan was rendered from, which is what makes checking the rendering
worth anything.
"""

from __future__ import annotations

from typing import Any, Sequence

from verify.phase11 import tfjson
from verify.phase11.protected import PHASE_11_ADDRESSES, PROTECTED_ADDRESSES
from verify.phase11.result import Check, CheckResult, failed, passed

WHAT = "d-pre plan"

# Actions no policy attached to the instance role may ALLOW. The instance writes backups and reads
# them back; retention is a bucket lifecycle rule S3 executes itself (backups.tf), so a delete
# grant would let anything holding the instance role erase every backup.
FORBIDDEN_ALLOW_PREFIXES = ("s3:delete",)
FORBIDDEN_ALLOW_EXACT = ("s3:*", "*")


def check_protected_addresses_untouched(plan: dict[str, Any]) -> CheckResult:
    """No mutating action against anything that already exists.

    Requires `["no-op"]` exactly rather than merely "no delete". An in-place update of the data
    volume or the instance is not destruction, but it is also not something a plan whose stated
    purpose is to add a bucket and a health check should contain, and "it was only an update" is
    how a forced replacement gets waved through.
    """
    offenders = [
        tfjson.describe(entry)
        for entry in tfjson.resource_changes(plan)
        if entry.get("address") in PROTECTED_ADDRESSES and not tfjson.is_no_op(entry)
    ]
    name = "no mutating action against existing infrastructure"
    expected = f"all {len(PROTECTED_ADDRESSES)} protected addresses planned as ['no-op']"
    if offenders:
        return failed(name, expected, f"{len(offenders)} mutated: {'; '.join(sorted(offenders))}")
    return passed(name, expected, f"{len(PROTECTED_ADDRESSES)} protected addresses, all no-op")


def check_state_matches_protected_list(plan: dict[str, Any]) -> CheckResult:
    """The plan's prior state holds EXACTLY the addresses this project has written down.

    Without this, the check above protects the seventeen resources somebody listed and silently
    ignores the eighteenth. Both directions are compared, and both are reported: an address in
    state but not in the list is unprotected infrastructure, and an address in the list but not in
    state is a resource that has already gone.
    """
    state = tfjson.prior_state(plan, what=WHAT)
    observed = tfjson.managed_addresses(state)

    unprotected = sorted(observed - PROTECTED_ADDRESSES)
    missing = sorted(PROTECTED_ADDRESSES - observed)

    name = "state contains exactly the protected address list"
    expected = f"{len(PROTECTED_ADDRESSES)} addresses, equal to verify/phase11/protected.py"
    if unprotected or missing:
        parts = []
        if unprotected:
            parts.append(
                f"{len(unprotected)} in state but NOT protected (add to protected.py or explain "
                f"why not): {unprotected}"
            )
        if missing:
            parts.append(f"{len(missing)} protected but ABSENT from state: {missing}")
        return failed(name, expected, "; ".join(parts))
    return passed(name, expected, f"{len(observed)} addresses, sets equal in both directions")


def check_exactly_one_bucket_created(plan: dict[str, Any]) -> CheckResult:
    """Exactly one `aws_s3_bucket` create. Not "at least one".

    "At least one" passes a plan that creates the backup bucket AND something else — most
    plausibly the Terraform state bucket, if somebody folds bootstrap/ into the main configuration
    to "tidy up". That is the circular dependency bootstrap/main.tf exists to prevent, and it
    arrives looking like an extra resource rather than like a mistake.
    """
    addresses = tfjson.created(plan, "aws_s3_bucket")
    name = "exactly one S3 bucket is created"
    expected = "exactly 1 aws_s3_bucket with a create action"
    if len(addresses) != 1:
        return failed(name, expected, f"{len(addresses)}: {addresses}")
    return passed(name, expected, f"1: {addresses[0]}")


def check_iam_allows_no_delete(plan: dict[str, Any]) -> CheckResult:
    """No `s3:Delete*` in any Allow statement of any planned `aws_iam_policy`.

    The policy is PARSED out of `change.after.policy`, never substring-searched in the plan text.
    This project's own `aws_iam_policy.backups` description reads "No delete - retention is a
    lifecycle rule", so a text search reports a delete grant on the correct plan; the repair
    somebody reaches for at that point is a narrower pattern, and a narrower pattern is what misses
    the real thing.
    """
    return _refuse_actions(
        plan,
        name="no IAM Allow grants a delete action",
        expected="no Allow statement with an action matching s3:Delete*",
        predicate=lambda action: action.lower().startswith(FORBIDDEN_ALLOW_PREFIXES),
    )


def check_iam_allows_no_wildcard(plan: dict[str, Any]) -> CheckResult:
    """No `s3:*` or bare `*` in any Allow statement of any planned `aws_iam_policy`.

    SCOPED TO `aws_iam_policy` AND TO `Effect: Allow`, and both narrowings are load-bearing.
    `aws_s3_bucket_policy.backups` carries `"Action": "s3:*"` in a DENY statement
    (DenyInsecureTransport), as does the state bucket's. A check that refused the string wherever
    it appeared would fail on the correct plan, and a check that could not tell an Allow from a
    Deny would be reporting the presence of a string rather than the presence of a grant.
    """
    return _refuse_actions(
        plan,
        name="no IAM Allow grants a wildcard action",
        expected="no Allow statement with action s3:* or *",
        predicate=lambda action: action.lower() in FORBIDDEN_ALLOW_EXACT,
    )


def _refuse_actions(plan, *, name: str, expected: str, predicate) -> CheckResult:
    statements = tfjson.iam_policy_statements(plan, what=WHAT)
    allow_statements = [statement for statement in statements if statement.allows]

    offenders = [
        f"{statement.address} Sid={statement.sid or '(none)'} action={action}"
        for statement in allow_statements
        for action in statement.actions
        if predicate(action)
    ]
    # A NotAction in an Allow grants everything except what it lists, which is a wildcard written
    # the other way round. It is refused unconditionally rather than pattern-matched.
    offenders += [
        f"{statement.address} Sid={statement.sid or '(none)'} NotAction={statement.not_actions}"
        for statement in allow_statements
        if statement.not_actions
    ]

    observed_counts = (
        f"{len(statements)} statements in aws_iam_policy resources, "
        f"{len(allow_statements)} of them Allow"
    )
    if offenders:
        return failed(name, expected, f"{len(offenders)} offending: {'; '.join(offenders)}")
    if not statements:
        # An empty collection must not report green (CLAUDE.md § 22). A Phase 11 plan that creates
        # no IAM policy at all is not a plan this stage should pass.
        return failed(
            name,
            expected,
            "no aws_iam_policy statements found in the plan at all - a gate over an empty "
            "collection is watching nothing, and this plan is expected to create "
            "aws_iam_policy.backups",
        )
    return passed(name, expected, observed_counts)


def check_iam_excludes_the_state_bucket(
    plan: dict[str, Any], state_bucket: str
) -> CheckResult:
    """No Allow statement's resources reach the Terraform state bucket.

    An instance that can write state is an instance that can lie about what infrastructure exists,
    and `prevent_destroy` is an attribute of state (CLAUDE.md § 8). The bucket name is passed in
    rather than hardcoded here, so this reads the same literal `backend.tf` does.
    """
    statements = tfjson.iam_policy_statements(plan, what=WHAT)
    offenders = [
        f"{statement.address} Sid={statement.sid or '(none)'} resource={resource}"
        for statement in statements
        if statement.allows
        for resource in statement.resources
        if state_bucket in resource
    ]
    name = "no IAM Allow reaches the Terraform state bucket"
    expected = f"no Allow resource containing {state_bucket!r}"
    if offenders:
        return failed(name, expected, "; ".join(offenders))
    scoped = sorted({resource for s in statements if s.allows for resource in s.resources})
    return passed(name, expected, f"Allow resources: {scoped}")


def check_plan_creates_the_phase_11_set(plan: dict[str, Any]) -> CheckResult:
    """The plan creates the Phase 11 resources, and only those.

    Without this, every check above passes on a plan that changes nothing whatsoever - all the
    refusals are satisfied by an empty plan. It also catches the opposite: a plan carrying
    something nobody expected, which is the case where reading the plan by eye goes wrong.
    """
    creating = {
        entry["address"]
        for entry in tfjson.resource_changes(plan)
        if entry.get("mode") == "managed" and "create" in tfjson.actions(entry)
    }
    unexpected = sorted(creating - PHASE_11_ADDRESSES)
    absent = sorted(PHASE_11_ADDRESSES - creating)

    name = "the plan creates exactly the Phase 11 resources"
    expected = f"{len(PHASE_11_ADDRESSES)} creates, equal to PHASE_11_ADDRESSES"
    if unexpected or absent:
        parts = []
        if unexpected:
            parts.append(f"{len(unexpected)} unexpected creates: {unexpected}")
            if absent:
                parts.append(f"{len(absent)} expected creates absent: {absent}")
        elif absent:
            parts.append(f"{len(absent)} expected creates absent: {absent}")
        return failed(name, expected, "; ".join(parts))
    return passed(name, expected, f"{len(creating)} creates, sets equal")


def checks(planfile: str, state_bucket: str | None = None) -> Sequence[Check]:
    """Build `d-pre`'s checks. Order matters: the cheapest and most fundamental first."""
    from verify.phase11.stage_c import state_bucket_name

    plan = tfjson.require_plan(
        tfjson.load_document(planfile, what=WHAT),
        what=WHAT,
    )
    bucket = state_bucket or state_bucket_name()

    return [
        lambda: check_protected_addresses_untouched(plan),
        lambda: check_state_matches_protected_list(plan),
        lambda: check_plan_creates_the_phase_11_set(plan),
        lambda: check_exactly_one_bucket_created(plan),
        lambda: check_iam_allows_no_delete(plan),
        lambda: check_iam_allows_no_wildcard(plan),
        lambda: check_iam_excludes_the_state_bucket(plan, bucket),
    ]
