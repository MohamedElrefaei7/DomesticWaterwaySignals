"""See CLAUDE.md § 11 decisions 6, 7, 8, 9, 11, 12: every DOCKER-USER rule is interface-scoped
and read from a file rather than hardcoded or derived, the conntrack RELATED,ESTABLISHED RETURN
is always first, RETURN (never ACCEPT) so Docker's own chains still apply, the chain is flushed
before rules are appended so re-running is idempotent, ip6tables gets the identical rule set
(never "handled" by disabling IPv6), and a missing or empty interface file is fatal before a
single command is issued.
"""

import configure_firewall as cf
import pytest

IFACE = "eth7"
FORBIDDEN_INTERFACES = {"ens5", "eth0", "docker0"}


def _append_calls(calls, binary):
    return [c for c in calls if c[:2] == [binary, "-A"]]


def test_interface_comes_from_the_file_not_a_literal(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    append_calls = _append_calls(fake_runner.calls, "iptables") + _append_calls(
        fake_runner.calls, "ip6tables"
    )
    assert len(append_calls) == 8  # 4 rules x 2 protocol families
    for call in append_calls:
        assert IFACE in call, f"append rule missing the interface read from the file: {call}"

    for call in fake_runner.calls:
        assert not (set(call) & FORBIDDEN_INTERFACES), f"literal interface leaked into: {call}"


def test_missing_interface_file_raises_before_any_command(fake_runner, tmp_path):
    missing_path = tmp_path / "does-not-exist"

    with pytest.raises(cf.InterfaceFileError):
        cf.run_docker_user_stage(missing_path, fake_runner, dry_run=False)

    assert fake_runner.calls == []


def test_empty_interface_file_raises_before_any_command(fake_runner, fake_interface_file):
    empty_file = fake_interface_file("")

    with pytest.raises(cf.InterfaceFileError):
        cf.run_docker_user_stage(empty_file, fake_runner, dry_run=False)

    assert fake_runner.calls == []


def test_conntrack_return_rule_is_first(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        appends = _append_calls(fake_runner.calls, binary)
        assert len(appends) == 4
        conntrack_indices = [
            i for i, c in enumerate(appends) if "RELATED,ESTABLISHED" in " ".join(c)
        ]
        assert conntrack_indices == [0], f"{binary}: conntrack RETURN rule is not first: {appends}"


def test_egress_rule_is_scoped_with_output_interface(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        appends = _append_calls(fake_runner.calls, binary)
        egress_calls = [c for c in appends if "-o" in c and IFACE in c]
        assert len(egress_calls) == 1

        for call in appends:
            assert "-i" in call or "-o" in call, f"append rule scoped to neither -i nor -o: {call}"


def test_terminal_drop_is_input_scoped(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        appends = _append_calls(fake_runner.calls, binary)
        drop_calls = [c for c in appends if "DROP" in c]
        assert len(drop_calls) == 1
        assert "-i" in drop_calls[0] and IFACE in drop_calls[0]

        assert not any(c[-2:] == ["-j", "DROP"] and "-i" not in c for c in appends)


def test_ingress_allows_only_http_and_https(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        appends = _append_calls(fake_runner.calls, binary)
        ingress_return_ports = set()
        for call in appends:
            if "-i" in call and "RETURN" in call and "--dports" in call:
                dports = call[call.index("--dports") + 1]
                ingress_return_ports.update(int(p) for p in dports.split(","))
        assert ingress_return_ports == {80, 443}


def test_rules_use_return_not_accept(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        appends = _append_calls(fake_runner.calls, binary)
        assert not any("ACCEPT" in c for c in appends)


def test_chain_is_flushed_before_rules_are_appended(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    for binary in ("iptables", "ip6tables"):
        calls = fake_runner.calls
        flush_index = calls.index([binary, "-F", "DOCKER-USER"])
        append_indices = [i for i, c in enumerate(calls) if c[:2] == [binary, "-A"]]
        assert len(append_indices) == 4
        assert all(flush_index < i for i in append_indices)


def test_ip6tables_receives_the_same_rule_set(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.run_docker_user_stage(interface_file, fake_runner, dry_run=False)

    ipv4_rules = {tuple(c[1:]) for c in _append_calls(fake_runner.calls, "iptables")}
    ipv6_rules = {tuple(c[1:]) for c in _append_calls(fake_runner.calls, "ip6tables")}

    assert len(ipv4_rules) == 4
    assert len(ipv6_rules) == 4
    assert ipv4_rules == ipv6_rules


def test_docker_user_only_mode_touches_no_ufw_command(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.configure(
        admin_cidr=None,
        interface_file=interface_file,
        docker_user_only=True,
        dry_run=False,
        run=fake_runner,
    )

    assert not any(call[0] == "ufw" for call in fake_runner.calls)
    assert not any(call == ["systemctl", "restart", "docker"] for call in fake_runner.calls)


def test_dry_run_records_no_mutating_commands(fake_runner, fake_interface_file):
    interface_file = fake_interface_file(IFACE)

    cf.configure(
        admin_cidr="47.166.211.114/32",
        interface_file=interface_file,
        docker_user_only=False,
        dry_run=True,
        run=fake_runner,
    )

    assert fake_runner.calls == []
