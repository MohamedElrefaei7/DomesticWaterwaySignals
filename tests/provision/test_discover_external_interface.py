"""See CLAUDE.md § 10 decisions 5-7: identify the external interface from the default-route
entry in /proc/net/route, never by "isn't loopback" topology; zero or multiple matches is a
hard failure; the output is a single-line file written only when not a dry run.
"""

import discover_external_interface as die
import pytest


def test_returns_the_interface_of_the_default_route(fake_proc_net_route):
    # docker0 is present and comes before the real default route on purpose: it is
    # non-loopback and non-default, exactly the row a "first interface that isn't lo" heuristic
    # would wrongly pick.
    route_path = fake_proc_net_route(
        [
            ("lo", "0100007F"),
            ("docker0", "000011AC"),
            ("eth0", "00000000"),
        ]
    )

    interface = die.find_default_route_interface(route_path)

    assert interface == "eth0"


def test_raises_on_zero_default_route_entries(fake_proc_net_route):
    route_path = fake_proc_net_route([("lo", "0100007F"), ("eth0", "0000A8C0")])

    with pytest.raises(die.InterfaceDiscoveryError, match="no default route"):
        die.find_default_route_interface(route_path)


def test_raises_on_multiple_default_route_entries(fake_proc_net_route):
    route_path = fake_proc_net_route(
        [
            ("eth0", "00000000"),
            ("eth1", "00000000"),
        ]
    )

    with pytest.raises(die.InterfaceDiscoveryError):
        die.find_default_route_interface(route_path)


def test_output_file_contains_exactly_one_line_with_no_trailing_garbage(fake_proc_net_route, tmp_path):
    route_path = fake_proc_net_route(
        [
            ("lo", "0100007F"),
            ("eth0", "00000000"),
        ]
    )
    output_path = tmp_path / "external-interface"

    die.discover(output_path=output_path, proc_net_route_path=route_path, dry_run=False)

    content = output_path.read_text()
    assert content == "eth0\n"
    assert content.strip() == "eth0"


def test_dry_run_writes_nothing(fake_proc_net_route, tmp_path):
    route_path = fake_proc_net_route([("eth0", "00000000")])
    output_path = tmp_path / "subdir" / "external-interface"

    exit_code = die.discover(output_path=output_path, proc_net_route_path=route_path, dry_run=True)

    assert exit_code == 0
    assert not output_path.exists()
