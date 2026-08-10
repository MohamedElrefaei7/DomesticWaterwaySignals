import ipaddress

from conftest import unwrap


def test_ingress_ports_are_exactly_ssh_http_https(tf):
    rules = tf.resources_of_type("aws_vpc_security_group_ingress_rule")
    assert rules, "no ingress rules defined"
    assert len(rules) == 3, f"expected exactly 3 ingress rules, found {len(rules)}"

    pairs = {(attrs["from_port"], attrs["to_port"]) for attrs in rules.values()}
    assert pairs == {(22, 22), (80, 80), (443, 443)}


def test_no_ingress_rule_range_contains_postgres(tf):
    rules = tf.resources_of_type("aws_vpc_security_group_ingress_rule")
    assert rules, "no ingress rules defined"
    for name, attrs in rules.items():
        from_port, to_port = attrs["from_port"], attrs["to_port"]
        assert not (from_port <= 5432 <= to_port), f"{name} range {from_port}-{to_port} covers Postgres"


def test_ssh_ingress_cidr_is_a_variable_reference_not_a_literal(tf):
    rules = tf.resources_of_type("aws_vpc_security_group_ingress_rule")
    ssh_rules = {n: a for n, a in rules.items() if a.get("from_port") == 22 and a.get("to_port") == 22}
    assert ssh_rules, "no port-22 ingress rule found"

    for name, attrs in ssh_rules.items():
        cidr = attrs["cidr_ipv4"]
        assert cidr == "${var.ssh_admin_cidr}", f"{name}.cidr_ipv4 is not a reference to var.ssh_admin_cidr"

        literal_candidate = unwrap(cidr)
        try:
            ipaddress.ip_network(literal_candidate)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{name}.cidr_ipv4 parses as a literal CIDR: {literal_candidate!r}")


def test_ssh_admin_cidr_has_no_default_and_rejects_broad_prefixes(tf):
    var = tf.variables.get("ssh_admin_cidr")
    assert var is not None, "variable ssh_admin_cidr is not declared"
    assert "default" not in var, "ssh_admin_cidr must not declare a default"

    validations = var.get("validation")
    assert validations, "ssh_admin_cidr has no validation block"

    prefix_length_check = any(
        "split" in v.get("condition", "") and "/" in v.get("condition", "")
        for v in validations
    )
    assert prefix_length_check, "no validation condition references a prefix-length check"


def test_explicit_egress_rule_exists(tf):
    egress = tf.resources_of_type("aws_vpc_security_group_egress_rule")
    assert egress, "no explicit egress rule defined — the provider's default allow-all gets revoked"
