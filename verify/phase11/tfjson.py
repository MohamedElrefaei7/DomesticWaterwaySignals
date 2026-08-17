"""Reading `terraform show -json`, and the two document types it produces.

EVERY ASSERTION IN STAGES C AND D READS THIS JSON, NEVER THE HUMAN-READABLE OUTPUT. The text form
is laid out for people: it changes between Terraform versions, it truncates long values, and it
renders an action as a `-/+` glyph in a margin. The JSON gives `resource_changes[]` with an explicit
`change.actions` array, which IS the thing being asserted. Parsing the text is tempting only because
it is what you have on screen when you decide to write the check.

TWO DOCUMENT TYPES COME OUT OF THE SAME COMMAND, AND CONFUSING THEM IS A VACUOUS PASS.

    terraform show -json                 -> STATE representation, format_version 1.0
    terraform show -json <planfile>      -> PLAN  representation, format_version 1.2

Measured against Terraform 1.15.8 on 2026-08-17, against this project's own state:

    state doc top-level keys: ['checks', 'format_version', 'terraform_version', 'values']
    plan  doc top-level keys: ['applyable', 'complete', 'configuration', 'errored',
                               'format_version', 'planned_values', 'prior_state',
                               'resource_changes', 'terraform_version', 'timestamp']

**A STATE DOCUMENT HAS NO `resource_changes` KEY AT ALL.** So "assert there are no resource changes"
run against `terraform show -json` with no plan file passes on every input, forever, including on a
state migration that silently carried nothing — CLAUDE.md § 2's theme 2 exactly. `require_plan()`
exists to make that impossible: a document without the key is a PRECONDITION failure (exit 2, "I
could not tell"), never a pass.

**A "No changes." PLAN IS NOT AN EMPTY `resource_changes` LIST.** Measured on 1.15.8: a plan over
two unchanged resources carries two entries, each with `"actions": ["no-op"]`, and reports
`"applyable": false`. So the correct assertion is that every entry is a no-op, not that the list is
empty — the empty-list form would fail on a correct migration of 17 resources and be repaired by
whoever hit it, most likely by deleting the check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from verify.phase11.result import Precondition

NO_OP: tuple[str, ...] = ("no-op",)

# An action list containing any of these changes infrastructure. `delete` covers a plain destroy;
# a replacement arrives as ["delete", "create"] or, under create_before_destroy, ["create",
# "delete"], so membership is the test rather than equality.
MUTATING_ACTIONS = frozenset({"create", "update", "delete"})


def load_document(path: str | Path, *, what: str) -> dict[str, Any]:
    """Load a `terraform show -json` document, or raise Precondition.

    An absent or unparseable file is "I could not tell", never "I checked and it is wrong". The
    human is told which file and what was wrong with it, because the fix is theirs.
    """
    path = Path(path)
    if not path.exists():
        raise Precondition(
            f"{what}: {path} does not exist. This verifier never runs `terraform plan` - the "
            f"allow-list in verify/phase11/shell.py omits it - so the plan file is one a human "
            f"created, and the artifact reviewed has to be the artifact applied."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Precondition(f"{what}: {path} could not be read: {exc}") from exc

    if not text.strip():
        raise Precondition(f"{what}: {path} is empty ({path.stat().st_size} bytes)")

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Precondition(
            f"{what}: {path} is not JSON ({exc}). `terraform show -json <planfile>` writes this; "
            f"`terraform show <planfile>` without -json writes the human-readable form."
        ) from exc

    if not isinstance(document, dict):
        raise Precondition(f"{what}: {path} parsed as {type(document).__name__}, expected an object")
    return document


def require_plan(document: dict[str, Any], *, what: str) -> dict[str, Any]:
    """Assert this is a PLAN representation. See the module docstring - this is the vacuity guard.

    Raises Precondition rather than returning a failed check: a state document handed to a plan
    verifier means the operator ran the wrong command, which is not a finding about the
    infrastructure.
    """
    if "resource_changes" not in document:
        keys = sorted(document)
        raise Precondition(
            f"{what}: this is not a plan document - it has no `resource_changes` key. "
            f"observed top-level keys: {keys}. "
            f"A STATE document (from `terraform show -json` with no plan file) never has that key, "
            f"so a 'no resource changes' assertion over one passes vacuously and forever. "
            f"Run `terraform plan -out=<file>` then `terraform show -json <file>`."
        )
    changes = document["resource_changes"]
    if not isinstance(changes, list):
        raise Precondition(
            f"{what}: `resource_changes` is {type(changes).__name__}, expected a list"
        )
    return document


def require_state(document: dict[str, Any], *, what: str) -> dict[str, Any]:
    """Assert this is a STATE representation, the mirror of `require_plan`."""
    if "values" not in document:
        raise Precondition(
            f"{what}: this is not a state document - it has no `values` key. "
            f"observed top-level keys: {sorted(document)}."
        )
    return document


def resource_changes(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(document.get("resource_changes") or [])


def actions(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple((entry.get("change") or {}).get("actions") or [])


def is_no_op(entry: dict[str, Any]) -> bool:
    return actions(entry) == NO_OP


def mutates(entry: dict[str, Any]) -> bool:
    return bool(MUTATING_ACTIONS & set(actions(entry)))


def describe(entry: dict[str, Any]) -> str:
    return f"{entry.get('address', '<no address>')} {list(actions(entry))}"


def created(document: dict[str, Any], resource_type: str) -> list[str]:
    """Addresses of managed resources of `resource_type` whose plan action includes `create`."""
    return sorted(
        entry.get("address", "")
        for entry in resource_changes(document)
        if entry.get("mode") == "managed"
        and entry.get("type") == resource_type
        and "create" in actions(entry)
    )


def _module_resources(module: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from module.get("resources") or []
    for child in module.get("child_modules") or []:
        yield from _module_resources(child)


def managed_addresses(state_document: dict[str, Any]) -> set[str]:
    """Every MANAGED resource address in a state representation, child modules included.

    Data sources are excluded on purpose. `data.aws_caller_identity.current` is re-read on every
    plan and is not infrastructure that can be destroyed, so including it would make the
    protected-set equality assertion flip the first time a data source is added or removed —
    reporting a drift in what exists when nothing exists differently.
    """
    root = ((state_document.get("values") or {}).get("root_module")) or {}
    return {
        resource["address"]
        for resource in _module_resources(root)
        if resource.get("mode") == "managed" and "address" in resource
    }


def prior_state(document: dict[str, Any], *, what: str) -> dict[str, Any]:
    """The `prior_state` embedded in a plan document.

    A plan carries the state it was computed against, so the protected-set equality check needs no
    second command and cannot read a DIFFERENT state than the one the plan is about — which a
    separate `terraform show -json` call could, if somebody applied between the two.

    Absent when the prior state is empty (measured: a plan from an empty state omits the key
    entirely). That is a precondition failure, because a plan with no prior state is not a plan
    against this project's infrastructure.
    """
    state = document.get("prior_state")
    if not isinstance(state, dict) or "values" not in state:
        raise Precondition(
            f"{what}: the plan carries no `prior_state`. Terraform omits it when the prior state "
            f"is empty, so this plan was computed against nothing - which is what an unmigrated or "
            f"freshly-initialised backend looks like."
        )
    return state


# ---------------------------------------------------------------------------------------------
# IAM policy documents
# ---------------------------------------------------------------------------------------------
#
# THE POLICY IS A JSON STRING INSIDE THE PLAN, AND IT IS PARSED, NEVER SUBSTRING-SEARCHED.
#
# `jsonencode(...)` in the configuration becomes a single string in `change.after.policy`. A
# substring search over the plan text for "Delete" matches the word wherever it appears — and it
# appears in this project's own configuration, in `aws_iam_policy.backups`'s description:
#
#     "Write and verify database backups. No delete - retention is a lifecycle rule."
#
# so a text search reports a delete action on the plan that is correct. Parsing the policy makes
# the description invisible to the check, which is the point.
#
# ONLY `aws_iam_policy` RESOURCES, AND ONLY `Effect: Allow` STATEMENTS. Both narrowings are
# load-bearing and neither is obvious:
#
#   * `aws_s3_bucket_policy.backups` legitimately carries `"Action": "s3:*"` in a DENY statement
#     (DenyInsecureTransport), as does the state bucket's. A check that read every policy in the
#     plan and refused `s3:*` would fail on the correct plan, and the repair somebody reaches for
#     is to weaken the check rather than to scope it.
#   * A Deny on `s3:*` is a security control. A check that cannot tell an Allow from a Deny is
#     reporting the presence of a string, not the presence of a grant.


class PolicyStatement:
    """One statement of a parsed IAM policy document, with its list fields normalised.

    IAM permits a bare string or a list for `Action`, `Resource` and `Effect`'s siblings. Handling
    only the list form silently skips the single-string statements, which is how a policy granting
    exactly one dangerous action passes a check written against a fixture that used a list.
    """

    def __init__(self, address: str, raw: dict[str, Any]) -> None:
        self.address = address
        self.raw = raw
        self.sid = raw.get("Sid", "")
        self.effect = raw.get("Effect", "")
        self.actions = _as_list(raw.get("Action"))
        self.not_actions = _as_list(raw.get("NotAction"))
        self.resources = _as_list(raw.get("Resource"))

    @property
    def allows(self) -> bool:
        return self.effect == "Allow"

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{self.address} {self.sid or '(no sid)'} {self.effect} {self.actions}>"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str)]


def iam_policy_statements(document: dict[str, Any], *, what: str) -> list[PolicyStatement]:
    """Every statement of every `aws_iam_policy` planned in this document, parsed.

    An unparseable policy raises rather than being skipped: a policy this check cannot read is a
    policy this check is not checking, and skipping it would report the plan as verified.
    """
    statements: list[PolicyStatement] = []
    for entry in resource_changes(document):
        if entry.get("type") != "aws_iam_policy" or entry.get("mode") != "managed":
            continue
        address = entry.get("address", "<no address>")
        raw_policy = ((entry.get("change") or {}).get("after") or {}).get("policy")
        if raw_policy is None:
            raise Precondition(
                f"{what}: {address} has no rendered `policy` in the plan. An unknown-at-plan-time "
                f"policy cannot be checked, and an unchecked policy must not read as a verified one."
            )
        try:
            policy = json.loads(raw_policy) if isinstance(raw_policy, str) else raw_policy
        except json.JSONDecodeError as exc:
            raise Precondition(f"{what}: {address}'s policy is not JSON: {exc}") from exc

        for raw_statement in _as_statements(policy):
            statements.append(PolicyStatement(address, raw_statement))
    return statements


def _as_statements(policy: Any) -> list[dict[str, Any]]:
    if not isinstance(policy, dict):
        return []
    raw = policy.get("Statement")
    if isinstance(raw, dict):
        return [raw]
    return [item for item in (raw or []) if isinstance(item, dict)]
