from conftest import block


def test_data_volume_is_a_top_level_resource(tf):
    assert tf.resources_of_type("aws_ebs_volume"), "no top-level aws_ebs_volume defined"
    assert tf.resources_of_type("aws_volume_attachment"), "no aws_volume_attachment defined"

    for name, attrs in tf.resources_of_type("aws_instance").items():
        assert "ebs_block_device" not in attrs, (
            f"aws_instance.{name} has a nested ebs_block_device — the data volume must be a "
            "top-level resource so prevent_destroy actually applies"
        )


def test_data_volume_has_prevent_destroy_and_encryption(tf):
    volumes = tf.resources_of_type("aws_ebs_volume")
    assert volumes, "no aws_ebs_volume defined"
    for name, attrs in volumes.items():
        lifecycle = block(attrs, "lifecycle")
        assert lifecycle is not None, f"aws_ebs_volume.{name} has no lifecycle block"
        assert lifecycle.get("prevent_destroy") is True, f"aws_ebs_volume.{name} lacks prevent_destroy"
        assert attrs.get("encrypted") is True, f"aws_ebs_volume.{name} is not encrypted"


def test_instance_and_volume_reference_the_same_az_variable(tf):
    instances = tf.resources_of_type("aws_instance")
    volumes = tf.resources_of_type("aws_ebs_volume")
    assert instances, "no aws_instance defined"
    assert volumes, "no aws_ebs_volume defined"

    for name, attrs in instances.items():
        assert attrs.get("availability_zone") == "${var.availability_zone}", (
            f"aws_instance.{name}.availability_zone is not a reference to var.availability_zone"
        )
    for name, attrs in volumes.items():
        assert attrs.get("availability_zone") == "${var.availability_zone}", (
            f"aws_ebs_volume.{name}.availability_zone is not a reference to var.availability_zone"
        )
