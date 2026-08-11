"""See CLAUDE.md § 11 decisions 1-4, 10: ufw is deny-incoming/allow-outgoing (the SSM lifeline),
rules are added before `ufw --force enable`, enable is always forced, the port allowlist is
{22, 80, 443} by exact set equality with SSH scoped to --admin-cidr, and Docker is restarted
after ufw enable and before DOCKER-USER rules are (re)applied.
"""

import re

import configure_firewall as cf
import pytest

ADMIN_CIDR = "47.166.211.114/32"


@pytest.fixture
def interface_file(fake_interface_file):
    return fake_interface_file("eth7")


def _configure(fake_runner, interface_file, **overrides):
    kwargs = dict(
        admin_cidr=ADMIN_CIDR,
        interface_file=interface_file,
        docker_user_only=False,
        dry_run=False,
        run=fake_runner,
    )
    kwargs.update(overrides)
    return cf.configure(**kwargs)


def _ufw_allow_calls(calls):
    return [c for c in calls if c[:2] == ["ufw", "allow"]]


def _ports_in_allow_calls(calls):
    """Ports appear either as `port 22` or as `<port>/tcp` — extract either shape."""
    ports = set()
    for call in _ufw_allow_calls(calls):
        joined = " ".join(call)
        for match in re.finditer(r"port (\d+)", joined):
            ports.add(int(match.group(1)))
        for match in re.finditer(r"(\d+)/tcp", joined):
            ports.add(int(match.group(1)))
    return ports


def test_default_outgoing_is_allow(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    assert ["ufw", "default", "allow", "outgoing"] in fake_runner.calls
    assert not any(
        call[:2] == ["ufw", "default"] and "outgoing" in call and "deny" in call
        for call in fake_runner.calls
    )
    assert not any("deny" in call and "outgoing" in call for call in fake_runner.calls)


def test_default_incoming_is_deny(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    assert ["ufw", "default", "deny", "incoming"] in fake_runner.calls
    assert not any(
        call[:2] == ["ufw", "default"] and "incoming" in call and "allow" in call
        for call in fake_runner.calls
    )


def test_allowed_ports_are_exactly_ssh_http_https(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    allow_calls = _ufw_allow_calls(fake_runner.calls)
    assert len(allow_calls) == 3

    ports = _ports_in_allow_calls(fake_runner.calls)
    assert len(ports) == 3
    assert ports == {22, 80, 443}


def test_ssh_rule_is_scoped_to_the_admin_cidr(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    ssh_calls = [c for c in _ufw_allow_calls(fake_runner.calls) if "22" in c]
    assert len(ssh_calls) == 1
    assert ADMIN_CIDR in ssh_calls[0]
    assert "from" in ssh_calls[0]

    for call in fake_runner.calls:
        assert call != ["ufw", "allow", "22"]
        assert call != ["ufw", "allow", "22/tcp"]
        assert call != ["ufw", "allow", "ssh"]
        if call[:2] == ["ufw", "allow"] and "22" in " ".join(call):
            assert "from" in call, f"unscoped port-22 rule: {call}"


def test_all_rules_are_added_before_ufw_enable(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    enable_index = fake_runner.calls.index(["ufw", "--force", "enable"])
    rule_indices = [
        i
        for i, c in enumerate(fake_runner.calls)
        if c[:2] == ["ufw", "allow"] or c[:2] == ["ufw", "default"]
    ]
    assert len(rule_indices) == 5
    assert all(i < enable_index for i in rule_indices)


def test_ufw_enable_is_forced(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    enable_calls = [c for c in fake_runner.calls if c[0] == "ufw" and "enable" in c]
    assert len(enable_calls) == 1
    assert "--force" in enable_calls[0]


def test_docker_is_restarted_after_ufw_enable_and_before_docker_user_rules(fake_runner, interface_file):
    _configure(fake_runner, interface_file)

    enable_index = fake_runner.calls.index(["ufw", "--force", "enable"])
    restart_index = fake_runner.calls.index(["systemctl", "restart", "docker"])
    first_docker_user_index = next(
        i for i, c in enumerate(fake_runner.calls) if c[0] in ("iptables", "ip6tables")
    )

    assert enable_index < restart_index < first_docker_user_index
