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

# ---------------------------------------------------------------------------------------------
# THE TWO PLAN SHAPES THIS STAGE ACCEPTS, AND WHY IT HAS TO ACCEPT BOTH
# ---------------------------------------------------------------------------------------------
#
# Stage D applied on 2026-08-18, and `protected.py` correctly folded the thirteen resources it
# created into PROTECTED_ADDRESSES - they are existing infrastructure now. That broke this stage
# for its original purpose in three places at once: the creates it demanded can never happen
# again, the single bucket create can never happen again, and the thirteen addresses it now
# protects are exactly the ones the pre-apply plan CREATES, so `no mutating action` fails on the
# plan the stage was written to bless.
#
# A GUARD THAT GOES RED ON THE CORRECT STATE TRAINS ITS OWN REMOVAL. Whoever hits it next reads
# three red checks against a healthy account and deletes the stage.
#
# So the shape is DERIVED FROM THE PLAN rather than passed in as a flag:
#
#   PRE_APPLY  the thirteen are all being created and none is in prior state.
#              This is a rebuild - which is not hypothetical for a project whose whole backup and
#              restore-test apparatus exists so the stack CAN be rebuilt.
#   APPLIED    the thirteen are all present and all no-op. This is what a plan looks like today.
#
# ANYTHING ELSE IS A HARD FAILURE, and that is the half that keeps the guard worth having. A plan
# where some of the thirteen are being created and others already exist is a partial apply, which
# is the state nobody has reasoned about. And critically: AN EMPTY PLAN WITH THE THIRTEEN ABSENT
# FROM STATE STILL FAILS - that was the original reason for the creates check ("every check above
# passes on a plan that changes nothing whatsoever"), and it survives intact.

PRE_APPLY = "pre-apply"
APPLIED = "applied"


def plan_mode(plan: dict[str, Any]) -> tuple[str | None, str]:
    """`(mode, detail)` for the Phase 11 resources, read out of the plan itself.

    Returns `(None, why)` when the plan is neither shape, so every caller reports the same
    observation rather than three checks each guessing separately.
    """
    managed = {
        entry["address"]: entry
        for entry in tfjson.resource_changes(plan)
        if entry.get("mode") == "managed" and entry.get("address")
    }
    creating = {
        address for address, entry in managed.items() if "create" in tfjson.actions(entry)
    }
    settled = {
        address for address in PHASE_11_ADDRESSES
        if address in managed and tfjson.is_no_op(managed[address])
    }

    phase_11_creating = PHASE_11_ADDRESSES & creating

    if phase_11_creating == PHASE_11_ADDRESSES and not settled:
        return PRE_APPLY, f"all {len(PHASE_11_ADDRESSES)} Phase 11 addresses are being created"
    if not phase_11_creating and settled == PHASE_11_ADDRESSES:
        return APPLIED, f"all {len(PHASE_11_ADDRESSES)} Phase 11 addresses are in state and no-op"

    absent = sorted(PHASE_11_ADDRESSES - phase_11_creating - settled)
    return None, (
        f"the plan is neither shape: {len(phase_11_creating)} of "
        f"{len(PHASE_11_ADDRESSES)} Phase 11 addresses are being created, {len(settled)} are "
        f"present and no-op, and {len(absent)} are neither: {absent}.\n"
        f"         A plan where some already exist and others do not is a PARTIAL APPLY. An empty "
        f"plan whose Phase 11 addresses are simply absent from state lands here too, which is the "
        f"case this refusal has always existed for."
    )

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
    name = "no mutating action against existing infrastructure"
    mode, detail = plan_mode(plan)
    if mode is None:
        return failed(name, "a pre-apply or an applied plan", detail)

    # ON A PRE-APPLY PLAN THE THIRTEEN ARE NOT YET EXISTING INFRASTRUCTURE, so creating them is
    # not a mutation of anything. They are in PROTECTED_ADDRESSES because they exist TODAY; a
    # rebuild plan is a plan taken when they do not, and holding it to today's inventory would
    # report the correct plan as destroying things.
    protected = PROTECTED_ADDRESSES - (PHASE_11_ADDRESSES if mode == PRE_APPLY else frozenset())

    offenders = [
        tfjson.describe(entry)
        for entry in tfjson.resource_changes(plan)
        if entry.get("address") in protected and not tfjson.is_no_op(entry)
    ]
    expected = f"all {len(protected)} protected addresses planned as ['no-op'] ({mode})"
    if offenders:
        return failed(name, expected, f"{len(offenders)} mutated: {'; '.join(sorted(offenders))}")
    return passed(name, expected, f"{len(protected)} protected addresses, all no-op; {detail}")


def check_state_matches_protected_list(plan: dict[str, Any]) -> CheckResult:
    """The plan's prior state holds EXACTLY the addresses this project has written down.

    Without this, the check above protects the seventeen resources somebody listed and silently
    ignores the eighteenth. Both directions are compared, and both are reported: an address in
    state but not in the list is unprotected infrastructure, and an address in the list but not in
    state is a resource that has already gone.
    """
    name = "state contains exactly the protected address list"
    mode, detail = plan_mode(plan)
    if mode is None:
        return failed(name, "a pre-apply or an applied plan", detail)

    state = tfjson.prior_state(plan, what=WHAT)
    observed = tfjson.managed_addresses(state)

    # ON A PRE-APPLY PLAN THE THIRTEEN ARE NOT IN STATE YET - that is what makes it a pre-apply
    # plan. Comparing against today's inventory would report every one of them as "protected but
    # ABSENT from state", which is thirteen alarming lines about a plan that is exactly right.
    expected_addresses = (
        PROTECTED_ADDRESSES - PHASE_11_ADDRESSES if mode == PRE_APPLY else PROTECTED_ADDRESSES
    )

    unprotected = sorted(observed - expected_addresses)
    missing = sorted(expected_addresses - observed)

    expected = (
        f"{len(expected_addresses)} addresses, equal to verify/phase11/protected.py ({mode})"
    )
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
    name = "exactly one S3 bucket is created, or none and it is already there"
    mode, detail = plan_mode(plan)

    if mode == APPLIED:
        # NOTHING MAY BE CREATED, AND THAT IS THE WHOLE ASSERTION HERE.
        #
        # "Zero buckets created" is also what a plan against an account with no bucket at all
        # looks like, and those are opposite situations - so this needs the bucket's PRESENCE too.
        # It does not check that separately, because `mode == APPLIED` already carries it:
        # plan_mode returns APPLIED only when every one of PHASE_11_ADDRESSES is present and
        # no-op, and `aws_s3_bucket.backups` is one of them. A second presence check here would
        # be a branch that cannot fire, and dead code with a plausible use case is the code that
        # comes back. The relationship it depends on is asserted in
        # tests/verify/test_phase11_terraform.py rather than assumed.
        expected = "0 aws_s3_bucket creates; presence carried by the applied-mode check above"
        if addresses:
            return failed(
                name, expected, f"{len(addresses)} created on an applied plan: {addresses}"
            )
        return passed(name, expected, f"0 created; {detail}")

    expected = "exactly 1 aws_s3_bucket with a create action"
    if len(addresses) != 1:
        return failed(name, expected, f"{len(addresses)}: {addresses} ({mode} plan)")
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
    """The Phase 11 resources are either all being created, or all already there.

    THE ORIGINAL REASON SURVIVES: without this, every refusal above is satisfied by a plan that
    changes nothing whatsoever. That is still true, and an empty plan whose Phase 11 addresses are
    absent from state still fails here - `plan_mode` puts it in neither shape.

    What changed is that "creates nothing" is no longer automatically wrong. Stage D applied, so
    the ordinary plan today creates nothing and every one of the thirteen is a no-op in state.
    Demanding creates would make this check red forever on the correct account, and a guard that
    goes red on the correct state trains its own removal.

    It also still catches the opposite: a create nobody expected, which is the case where reading
    a plan by eye goes wrong.
    """
    name = "the Phase 11 resources are all created or all in state"
    mode, detail = plan_mode(plan)
    expected = (
        f"either {len(PHASE_11_ADDRESSES)} creates equal to PHASE_11_ADDRESSES, or 0 creates with "
        f"all {len(PHASE_11_ADDRESSES)} present and no-op"
    )
    if mode is None:
        return failed(name, expected, detail)

    creating = {
        entry["address"]
        for entry in tfjson.resource_changes(plan)
        if entry.get("mode") == "managed" and "create" in tfjson.actions(entry)
    }
    unexpected = sorted(creating - PHASE_11_ADDRESSES)
    if unexpected:
        return failed(
            name, expected,
            f"{len(unexpected)} create(s) nobody expected: {unexpected} ({mode} plan)",
        )
    return passed(name, expected, f"{mode}: {detail}")


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


# =============================================================================================
# Stage D post-apply - D6 (the search string), D7 (the raw bytes), D8 (subscription and budget)
# =============================================================================================
#
# THE SEARCH STRING IS READ FROM DEPLOYED STATE AND ASSERTED AGAINST LIVE RESPONSE BYTES.
#
# Neither half alone is a check. Comparing the Terraform literal to the application's constant is
# two files in a repo agreeing with each other, which they will keep doing while the monitor is
# blind. Fetching the endpoint and looking for a string this module hardcoded proves the API says
# something, not that ROUTE53 is looking for it. So: read what the health check is ACTUALLY
# configured to search for, fetch what the endpoint ACTUALLY returns through Caddy, and assert one
# contains the other.
#
# D7 IS A SEPARATE STEP FROM D6 FOR ONE REASON: compression. Route53 matches the literal against
# the first 5,120 bytes of the body AS SENT. Caddy compresses, and a compressed body leaves every
# app-side test green while the monitor never matches again. `fetch.get` sets
# `Accept-Encoding: identity` explicitly - see that module's docstring for why the urllib default
# happening to work is worse than it failing.

from verify.phase11 import fetch  # noqa: E402
from verify.phase11.result import Precondition  # noqa: E402

HEALTH_PATH = "/api/health"

# The literal string AWS returns for a subscription nobody has clicked the confirmation link for.
# It is a STATE, not an error, and every AWS call about it succeeds - which is why it needs its own
# assertion rather than being noticed.
PENDING_CONFIRMATION = "PendingConfirmation"


def _state_outputs() -> dict[str, Any]:
    from verify.phase11.stage_c import TERRAFORM_DIR

    from verify.phase11 import shell

    completed = shell.run(["terraform", "show", "-json"], cwd=TERRAFORM_DIR)
    if completed.returncode != 0:
        raise Precondition(
            f"d-post: `terraform show -json` exited {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )
    import json as _json

    try:
        document = _json.loads(completed.stdout)
    except _json.JSONDecodeError as exc:
        raise Precondition(f"d-post: `terraform show -json` output is not JSON: {exc}") from exc
    return (document.get("values") or {}).get("outputs") or {}


def _output(name: str) -> str:
    outputs = _state_outputs()
    if name not in outputs:
        raise Precondition(
            f"d-post: no `{name}` output in state. observed outputs: {sorted(outputs)}. "
            f"Stage D's apply creates it, so this usually means the apply has not run."
        )
    return str(outputs[name].get("value", ""))


def health_check_id_from_state() -> str:
    """Route53 health check id, from the Terraform OUTPUT rather than from a note somewhere."""
    return _output("api_health_check_id")


def alerts_topic_arn_from_state() -> str:
    return _output("alerts_topic_arn")


def search_string_from_route53(health_check_id: str) -> str:
    """What the health check is ACTUALLY configured to look for. Never a constant in this file.

    `test_d_post_reads_search_string_from_state_not_a_constant` drives a DIFFERENT string through
    here and requires the verdict to follow it, so a hardcoded `"degraded":false` fails that test
    rather than passing for the wrong reason.
    """
    from verify.phase11 import shell

    completed = shell.run(
        ["aws", "route53", "get-health-check", "--health-check-id", health_check_id]
    )
    if completed.returncode != 0:
        raise Precondition(
            f"d-post: get-health-check exited {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )
    import json as _json

    try:
        payload = _json.loads(completed.stdout)
    except _json.JSONDecodeError as exc:
        raise Precondition(f"d-post: get-health-check output is not JSON: {exc}") from exc

    config = (payload.get("HealthCheck") or {}).get("HealthCheckConfig") or {}
    search = config.get("SearchString")
    if not search:
        raise Precondition(
            f"d-post: health check {health_check_id} has no SearchString "
            f"(Type={config.get('Type')!r}). An HTTPS_STR_MATCH check without one matches nothing, "
            f"and a check of another type is watching only for a status code."
        )
    return search


def check_search_string_is_in_the_body(
    search_string: str, response: fetch.Response
) -> CheckResult:
    """The bytes Route53 looks for are in the bytes the edge actually sends.

    Byte comparison, not string comparison, and no decoding first: a codec normalising the body is
    a codec normalising away the difference being looked for.
    """
    name = "the live body contains Route53's search string"
    expected = f"{search_string!r} present in the first 5120 bytes of the body"
    encoding = response.header("Content-Encoding")
    window = response.body[:5120]
    needle = search_string.encode("utf-8")

    if response.status != 200:
        return failed(name, expected, f"HTTP {response.status} from {HEALTH_PATH}")

    if needle not in window:
        return failed(
            name,
            expected,
            f"absent. Content-Encoding={encoding!r}, {len(response.body)} bytes, "
            f"body starts {window[:120]!r}. "
            f"A Content-Encoding other than identity is the whole reason this step exists: "
            f"Route53 matches the literal against the bytes AS SENT, so a compressed body leaves "
            f"every application-side test green while the monitor never matches again.",
        )

    if encoding not in (None, "identity"):
        return failed(
            name,
            expected,
            f"the string is present but Content-Encoding={encoding!r}. The request asked for "
            f"identity and the edge compressed anyway, so what Route53 receives is not this.",
        )
    return passed(
        name,
        expected,
        f"present, Content-Encoding={encoding!r}, {len(response.body)} bytes",
    )


def check_subscription_is_confirmed(subscriptions: Sequence[dict[str, Any]]) -> CheckResult:
    """`PendingConfirmation` is the literal failure, not a state to wait out.

    An unconfirmed subscription delivers nothing. Every AWS call about it succeeds, the topic
    exists, the alarm is wired to it, and the first anybody knows is the outage nobody was emailed
    about. This is CONTEXT.md § Up Next item 5, made into an assertion.
    """
    name = "the SNS email subscription is confirmed"
    expected = "at least one subscription whose SubscriptionArn is a real ARN"
    if not subscriptions:
        return failed(
            name, expected, "0 subscriptions on the topic - the email was never subscribed"
        )

    pending = [
        s for s in subscriptions if s.get("SubscriptionArn") == PENDING_CONFIRMATION
    ]
    confirmed = [
        s for s in subscriptions if s.get("SubscriptionArn", "").startswith("arn:")
    ]
    if not confirmed:
        return failed(
            name,
            expected,
            f"{len(pending)} of {len(subscriptions)} still {PENDING_CONFIRMATION}. Nothing is "
            f"delivered until somebody clicks the link in the confirmation email.",
        )
    if pending:
        return failed(
            name,
            expected,
            f"{len(confirmed)} confirmed but {len(pending)} still {PENDING_CONFIRMATION}: "
            f"{[s.get('Endpoint') for s in pending]}",
        )
    return passed(
        name,
        expected,
        f"{len(confirmed)} confirmed: {[s.get('Endpoint') for s in confirmed]}",
    )


def check_budget_exists(budget: dict[str, Any], expected_limit: str) -> CheckResult:
    """The budget is real and its limit is the one in `variables.tf`.

    § Up Next item 6 has been open with status unknown since Phase 10. A budget nobody has
    confirmed exists is the same class of thing as an alarm nobody has watched fire.
    """
    name = "the monthly budget exists with the configured limit"
    expected = f"BudgetType COST, LimitAmount {expected_limit}"
    if not budget:
        return failed(name, expected, "no budget returned")

    amount = (budget.get("BudgetLimit") or {}).get("Amount")
    budget_type = budget.get("BudgetType")
    observed = f"BudgetType={budget_type!r} LimitAmount={amount!r} name={budget.get('BudgetName')!r}"

    if budget_type != "COST":
        return failed(name, expected, observed)
    # String comparison on the numeric value: AWS returns "25" or "25.0" depending on how it was
    # set, so the compare is on float with the raw values reported either way.
    try:
        if float(amount) != float(expected_limit):
            return failed(name, expected, observed)
    except (TypeError, ValueError):
        return failed(name, expected, observed)
    return passed(name, expected, observed)


def checks_d_post(base_url: str = "https://bargeanalysis.com") -> Sequence[Check]:
    from verify.phase11.stage_c import _aws_json

    health_check_id = health_check_id_from_state()
    search_string = search_string_from_route53(health_check_id)
    response = fetch.get(f"{base_url}{HEALTH_PATH}")

    topic_arn = alerts_topic_arn_from_state()
    subscriptions = (
        _aws_json(
            ["aws", "sns", "list-subscriptions-by-topic", "--topic-arn", topic_arn],
            what="d-post subscriptions",
        ).get("Subscriptions")
        or []
    )

    identity = _aws_json(["aws", "sts", "get-caller-identity"], what="d-post account id")
    budgets = _aws_json(
        ["aws", "budgets", "describe-budgets", "--account-id", str(identity.get("Account", ""))],
        what="d-post budgets",
    )
    limit = _budget_limit_from_variables()
    budget_name = _budget_name_from_variables()
    budget = next(
        (b for b in (budgets.get("Budgets") or []) if b.get("BudgetName") == budget_name), {}
    )

    return [
        lambda: check_search_string_is_in_the_body(search_string, response),
        lambda: check_subscription_is_confirmed(subscriptions),
        lambda: check_budget_exists(budget, limit),
    ]


def _budget_limit_from_variables() -> str:
    """`var.monthly_budget_usd`'s default, READ from variables.tf rather than restated here."""
    return _variable_default("monthly_budget_usd")


def _budget_name_from_variables() -> str:
    return f"{_variable_default('project_name')}-monthly"


def _variable_default(variable: str) -> str:
    import re as _re

    from verify.phase11.stage_c import TERRAFORM_DIR

    path = TERRAFORM_DIR / "variables.tf"
    if not path.exists():
        raise Precondition(f"d-post: {path} does not exist; cannot read {variable}'s default")
    text = path.read_text(encoding="utf-8")
    start = text.find(f'variable "{variable}"')
    if start == -1:
        raise Precondition(f'd-post: no `variable "{variable}"` block in {path}')
    end = len(text)
    position = text.find('\nvariable "', start + 1)
    if position != -1:
        end = position
    match = _re.search(r'^\s*default\s*=\s*"?([^"\n]+)"?', text[start:end], _re.MULTILINE)
    if match is None:
        raise Precondition(f'd-post: no `default` inside `variable "{variable}"` in {path}')
    return match.group(1).strip()
