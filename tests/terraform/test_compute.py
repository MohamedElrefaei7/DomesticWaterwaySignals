from conftest import block, unwrap


def test_ami_is_pinned_not_resolved_at_plan_time(tf):
    instances = tf.resources_of_type("aws_instance")
    assert instances, "no aws_instance defined"
    for name, attrs in instances.items():
        assert attrs.get("ami") == "${var.ami_id}", f"aws_instance.{name}.ami is not a reference to var.ami_id"

    assert not tf.data_sources_of_type("aws_ami"), "no aws_ami data source is allowed anywhere"


def test_imdsv2_is_required(tf):
    instances = tf.resources_of_type("aws_instance")
    assert instances, "no aws_instance defined"
    for name, attrs in instances.items():
        meta = block(attrs, "metadata_options")
        assert meta is not None, f"aws_instance.{name} has no metadata_options block"
        assert unwrap(meta.get("http_tokens")) == "required", f"aws_instance.{name} does not require IMDSv2"


def test_eip_has_prevent_destroy(tf):
    eips = tf.resources_of_type("aws_eip")
    assert eips, "no aws_eip defined"
    for name, attrs in eips.items():
        lifecycle = block(attrs, "lifecycle")
        assert lifecycle is not None, f"aws_eip.{name} has no lifecycle block"
        assert lifecycle.get("prevent_destroy") is True, f"aws_eip.{name} lacks prevent_destroy"


def test_imds_hop_limit_is_two(tf):
    """TWO, not one, because a container reaches the metadata service through an extra hop.

    LIVES HERE RATHER THAN IN tests/deploy/ BECAUSE THIS IS A FACT ABOUT compute.tf, and the
    parser for it is the session-scoped hcl2 fixture in this package. It is a Phase 12 test all
    the same: from Phase 12 the backup and restore-test jobs run INSIDE a container, and boto3
    obtains the instance role's credentials over IMDS.

    THE FAILURE MODE IS WHY IT IS ASSERTED RATHER THAN ASSUMED. At a hop limit of 1 the PUT that
    fetches an IMDSv2 token dies one hop short of the container, so every S3 call fails with a
    credentials error - which reads as an IAM misconfiguration and sends the reader to iam.tf, to
    the bucket policy, and to the instance profile, none of which are wrong. The value is already
    2 and has been since Phase 1; this is the tripwire against somebody "tightening" it, which is
    exactly what a hop limit of 1 looks like in a security review.
    """
    instances = tf.resources_of_type("aws_instance")
    assert instances, "no aws_instance defined"
    for name, attrs in instances.items():
        meta = block(attrs, "metadata_options")
        assert meta is not None, f"aws_instance.{name} has no metadata_options block"
        observed = meta.get("http_put_response_hop_limit")
        assert observed == 2, (
            f"aws_instance.{name} sets http_put_response_hop_limit = {observed!r}, expected 2. "
            f"At 1 the token request does not reach a container, and every boto3 call from the "
            f"scheduler fails with a credentials error that names nothing about hop limits."
        )
