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
