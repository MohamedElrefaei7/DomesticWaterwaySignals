def test_vpc_and_subnet_are_defined_in_repo(tf):
    assert len(tf.resources_of_type("aws_vpc")) == 1
    assert len(tf.resources_of_type("aws_subnet")) == 1
    assert len(tf.resources_of_type("aws_internet_gateway")) == 1
    assert len(tf.resources_of_type("aws_route_table_association")) == 1
    assert not tf.data_sources_of_type("aws_vpc"), "must not depend on account state via a data source"


def test_subnet_does_not_auto_assign_public_ip(tf):
    subnets = tf.resources_of_type("aws_subnet")
    assert subnets, "no aws_subnet defined"
    for name, attrs in subnets.items():
        assert "map_public_ip_on_launch" in attrs, f"aws_subnet.{name} does not set map_public_ip_on_launch"
        assert attrs["map_public_ip_on_launch"] is False
