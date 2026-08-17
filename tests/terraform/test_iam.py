from conftest import unwrap


def test_instance_role_attaches_only_ssm_core(tf):
    """Exactly one AWS-MANAGED policy, and it is SSM core.

    WIDENED IN PHASE 11, and the widening is the interesting part. This asserted a total of one
    attachment, which was right while iam.tf's own note said "No S3 policy - the backup bucket
    doesn't exist yet (Phase 11)". The backup bucket now exists, so the instance role carries a
    second attachment.

    A count of two would be a weaker test that happens to pass today. What is actually being
    guarded is that no further AWS-managed policy is attached - `AmazonS3FullAccess` is one click
    and one line away, and it would satisfy every backup test in the suite while granting delete
    on every bucket in the account. So the count is asserted over the aws-managed set, and every
    customer-managed attachment is required to point at a policy declared in this configuration,
    where test_backup_bucket_hcl.py reads its actions.
    """
    attachments = tf.resources_of_type("aws_iam_role_policy_attachment")
    assert attachments, "no aws_iam_role_policy_attachment defined"

    declared_policies = tf.resources_of_type("aws_iam_policy")

    aws_managed = {}
    customer_managed = {}
    for name, attrs in attachments.items():
        arn = unwrap(attrs["policy_arn"])
        (aws_managed if arn.startswith("arn:aws:iam::aws:policy/") else customer_managed)[name] = arn

    assert len(aws_managed) == 1, (
        f"expected exactly 1 AWS-managed policy attachment, found {len(aws_managed)}: "
        f"{sorted(aws_managed.values())}. A managed policy is a broad grant nobody in this repo "
        f"can read the contents of."
    )
    (name, arn), = aws_managed.items()
    assert arn.endswith("AmazonSSMManagedInstanceCore"), (
        f"{name}.policy_arn is {arn!r}, expected it to end in AmazonSSMManagedInstanceCore"
    )

    for name, arn in customer_managed.items():
        assert arn.startswith("${aws_iam_policy."), (
            f"{name} attaches {arn!r}, which is neither AWS-managed nor a policy declared in this "
            f"configuration - so its actions are not readable from this repo"
        )
        referenced = arn.removeprefix("${aws_iam_policy.").split(".")[0]
        assert referenced in declared_policies, (
            f"{name} references aws_iam_policy.{referenced}, which is not declared here"
        )

    assert not tf.resources_of_type("aws_iam_role_policy"), "no inline IAM policy is allowed"


def test_instance_has_an_instance_profile(tf):
    instances = tf.resources_of_type("aws_instance")
    profiles = tf.resources_of_type("aws_iam_instance_profile")
    assert instances, "no aws_instance defined"
    assert profiles, "no aws_iam_instance_profile defined"

    for name, attrs in instances.items():
        value = attrs.get("iam_instance_profile")
        assert value is not None, f"aws_instance.{name} does not set iam_instance_profile"
        assert value.startswith("${aws_iam_instance_profile."), (
            f"aws_instance.{name}.iam_instance_profile is not a reference to the profile resource: {value!r}"
        )


# Exactly what the instance role may attach. COMPARED AS A SET, BY EQUALITY.
#
# The customer-managed entry is written as the interpolation Terraform emits rather than a real
# ARN, because the ARN does not exist until apply and the point is to pin WHICH policy resource is
# attached - one declared in this configuration, whose actions test_backup_bucket_hcl.py reads.
EXPECTED_INSTANCE_ROLE_POLICIES = {
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "${aws_iam_policy.backups.arn}",
}


def test_instance_role_policy_set_is_exact(tf):
    """The instance role attaches EXACTLY the SSM managed policy and the backup policy.

    NOT "at least", NOT "contains", and not a count. The test beside this one bounds the AWS-managed
    attachments at one and requires every customer-managed attachment to reference a policy declared
    here - which is real, and is still a lower bound on the whole set: a SECOND customer-managed
    policy, declared in this configuration and granting anything at all, satisfies every assertion in
    it. Widening a guard is how a guard is tidied away, and it always looks justified from inside.

    WHAT THE SET USED TO BE, so the change reads as a change of fact rather than of strictness:

        Phase 10   {AmazonSSMManagedInstanceCore}
        Phase 11   {AmazonSSMManagedInstanceCore, aws_iam_policy.backups}

    The addition is the backup policy from backups.tf, which the nightly job needs to put an object
    in the bucket and the restore test needs to read one back. It carries no delete of any kind -
    retention is a bucket lifecycle rule S3 executes itself, so a compromised instance cannot erase
    the backups (README § What it does not cover).

    Adding a policy is then a two-line change: the attachment, and this set. That is the intended
    cost. An attachment that nobody had to write down here is the one that gets added in a hurry.
    """
    attachments = tf.resources_of_type("aws_iam_role_policy_attachment")
    assert attachments, "no aws_iam_role_policy_attachment defined - this test would pass over nothing"

    roles = tf.resources_of_type("aws_iam_role")
    assert "instance" in roles, (
        f"aws_iam_role.instance is not declared; the roles here are {sorted(roles)}. This test "
        f"pins what the INSTANCE role carries and cannot do that if it has been renamed."
    )

    attached = {}
    for name, attrs in attachments.items():
        role = unwrap(attrs["role"])
        # Only the instance role. A second role in this configuration is not this test's subject,
        # and silently folding its policies in here would make this set describe two things.
        if "aws_iam_role.instance" not in role:
            continue
        attached[name] = unwrap(attrs["policy_arn"])

    assert attached, (
        "no policy attachment references aws_iam_role.instance. Either the role is unused or its "
        "attachments now reference it by a different expression, and this test is asserting an "
        "exact set over an empty collection."
    )

    # THE COUNT, BEFORE THE SET. A set collapses duplicates, so a second attachment of a policy
    # already in the set is invisible to the comparison below - measured, by a mutation that added
    # exactly that and stayed green. Terraform would reject the duplicate at apply, but this test
    # is read as a statement about the attachment list and it should be one.
    assert len(attached) == len(EXPECTED_INSTANCE_ROLE_POLICIES), (
        f"the instance role has {len(attached)} policy attachment(s) and this repo names "
        f"{len(EXPECTED_INSTANCE_ROLE_POLICIES)}: {attached}"
    )

    actual = set(attached.values())
    assert actual == EXPECTED_INSTANCE_ROLE_POLICIES, (
        f"the instance role's policy set is not what this repo says it is.\n"
        f"  unexpected: {sorted(actual - EXPECTED_INSTANCE_ROLE_POLICIES)}\n"
        f"  missing   : {sorted(EXPECTED_INSTANCE_ROLE_POLICIES - actual)}\n"
        f"  attachments: {attached}\n"
        f"A policy added here is a grant nobody reviewed. `AmazonS3FullAccess` is one line away "
        f"and would satisfy every backup test in this suite while granting delete on every bucket "
        f"in the account. If the addition is deliberate, add it to "
        f"EXPECTED_INSTANCE_ROLE_POLICIES with the reason."
    )
