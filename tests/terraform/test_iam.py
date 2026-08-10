from conftest import unwrap


def test_instance_role_attaches_only_ssm_core(tf):
    attachments = tf.resources_of_type("aws_iam_role_policy_attachment")
    assert attachments, "no aws_iam_role_policy_attachment defined"
    assert len(attachments) == 1, f"expected exactly 1 policy attachment, found {len(attachments)}"

    (name, attrs), = attachments.items()
    policy_arn = unwrap(attrs["policy_arn"])
    assert policy_arn.endswith("AmazonSSMManagedInstanceCore"), (
        f"{name}.policy_arn is {policy_arn!r}, expected it to end in AmazonSSMManagedInstanceCore"
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
