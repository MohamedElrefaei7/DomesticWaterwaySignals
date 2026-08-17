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
