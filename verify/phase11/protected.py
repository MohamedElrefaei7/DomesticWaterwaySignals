"""The infrastructure that already exists, listed exactly, so a plan can be checked against it.

ASSERTING "THE INSTANCE IS NOT BEING DESTROYED" PROTECTS THE INSTANCE AND NOTHING ELSE. It says
nothing about a resource somebody adds next month and does not add here — that resource is
unprotected and the verifier is green, which is the shape CLAUDE.md § 22 records from preflight
gate 1: a gate over a collection that checks a subset while its summary line reports the whole set.

So there are two assertions, and neither is sufficient alone:

    1. No mutating action against any address in this list.
    2. The set of managed addresses IN STATE equals this list.

Adding infrastructure without updating this file then fails loudly, which is correct. It is the
same exact-set-equality discipline as the security-group ingress (§ 8), the ufw port allowlist
(§ 11), the published-port set (§ 22) and the commit-helper allow-list (§ 23) — and, like those, a
REMOVED entry fails too, so the list cannot quietly go stale while reading as current.

MEASURED, NOT LISTED FROM THE CONFIGURATION FILES. Read out of the live state on 2026-08-17 with

    terraform show -json infra/terraform/terraform.tfstate

which reported these seventeen managed addresses and no data sources. Reading the `.tf` files
instead would list what SHOULD exist; this lists what DOES, and the difference between those two is
the entire subject of Stage C.

Data sources are deliberately absent (see `tfjson.managed_addresses`). `data.aws_caller_identity`
enters state with the Phase 11 apply and is re-read on every plan; it is not infrastructure whose
destruction matters, and including it would make this set flip for a reason that is not a change to
anything real.

THE PHASE 11 RESOURCES WERE NOT IN THIS LIST UNTIL STAGE D'S APPLY. Before it, `d-pre` ran against
a plan that CREATED them, so listing them would have made their creation look like a violation.
Stage D applied on 2026-08-18 (13 added, 0 changed, 0 destroyed), so they are now existing
infrastructure and PROTECTED_ADDRESSES is the union of the original seventeen and those thirteen.
PHASE_11_ADDRESSES is retained as the record of what that apply created, and is the source of the
thirteen merged below rather than a second hand-typed list.
"""

from __future__ import annotations

# The state bucket lives in bootstrap/, which is a separate configuration with its own state, so
# `aws_s3_bucket.state` is deliberately not here.
PROTECTED_ADDRESSES: frozenset[str] = frozenset(
    {
        # Network (network.tf)
        "aws_vpc.main",
        "aws_subnet.public",
        "aws_internet_gateway.main",
        "aws_route_table.public",
        "aws_route_table_association.public",
        # Security group and its rules (security.tf). The ingress set is asserted by exact
        # equality in tests/terraform/; here the resources themselves are what must survive.
        "aws_security_group.instance",
        "aws_vpc_security_group_ingress_rule.ssh_admin",
        "aws_vpc_security_group_ingress_rule.http",
        "aws_vpc_security_group_ingress_rule.https",
        "aws_vpc_security_group_egress_rule.all_outbound",
        # THE DATA VOLUME AND ITS ATTACHMENT (storage.tf). `prevent_destroy` is an attribute of
        # STATE (CLAUDE.md § 8), which is exactly what Stage C is moving, so these two are the
        # reason the whole stage has a verifier.
        "aws_ebs_volume.data",
        "aws_volume_attachment.data",
        # Compute (compute.tf). The EIP is in the list because losing it changes the address the
        # domain resolves to, and re-issuing a certificate against a moved A record is the § 22
        # rate-limited round trip.
        "aws_instance.main",
        "aws_eip.main",
        # Instance role (iam.tf). Phase 11 ATTACHES a new policy to this role; the role itself
        # must not be replaced, because replacing it detaches SSM and removes the recovery path.
        "aws_iam_role.instance",
        "aws_iam_role_policy_attachment.ssm_core",
        "aws_iam_instance_profile.instance",
    }
)

# Resources Stage D's apply is expected to CREATE, from backups.tf and monitoring.tf. Used to
# report what a plan contains, never to permit an action against anything else.
PHASE_11_ADDRESSES: frozenset[str] = frozenset(
    {
        "aws_s3_bucket.backups",
        "aws_s3_bucket_versioning.backups",
        "aws_s3_bucket_server_side_encryption_configuration.backups",
        "aws_s3_bucket_public_access_block.backups",
        "aws_s3_bucket_policy.backups",
        "aws_s3_bucket_lifecycle_configuration.backups",
        "aws_iam_policy.backups",
        "aws_iam_role_policy_attachment.backups",
        "aws_route53_health_check.api",
        "aws_sns_topic.alerts",
        "aws_sns_topic_subscription.alerts_email",
        "aws_cloudwatch_metric_alarm.api_health",
        "aws_budgets_budget.monthly",
    }
)

# After Stage D's apply the Phase 11 resources are existing infrastructure, so they are protected
# on the same terms as everything else. Unioned rather than re-listed: two hand-typed copies of the
# same thirteen addresses would drift, and the drift would be silent in the direction that matters.
PROTECTED_ADDRESSES = PROTECTED_ADDRESSES | PHASE_11_ADDRESSES
