#!/usr/bin/env python3
"""Identify the network interface carrying the default route and write its name to a fixed,
boot-ordered file for provisioning 3 (the firewall commit) to read.

Provisioning 2 of 3, part 1 — see CLAUDE.md § 10. Invoked at boot by
dws-external-interface.service, after network-online.target. The systemd unit is the real
invocation path; running this by hand is for --dry-run verification only.

sudo python3 infra/provision/discover_external_interface.py --output-path /etc/dws/external-interface
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_OUTPUT_PATH = "/etc/dws/external-interface"
DEFAULT_PROC_NET_ROUTE_PATH = "/proc/net/route"
DEFAULT_ROUTE_DESTINATION = "00000000"


class InterfaceDiscoveryError(RuntimeError):
    """Zero or multiple default-route entries — decision 6. No fallback exists."""


def find_default_route_interface(proc_net_route_path: os.PathLike | str) -> str:
    """Return the interface carrying the default route (Destination == 00000000 in
    /proc/net/route), never "the first interface that isn't loopback" — decision 5.

    That heuristic works cleanly on a freshly booted instance with one NIC and no Docker yet.
    The moment Docker starts, it creates docker0 and, once containers run, veth* pairs — all
    non-loopback, none of them the interface we want. After this commit runs, "first
    non-loopback" becomes ambiguous on the very instance it's meant to configure. Same category
    of bug CLAUDE.md § 9 already forbids for disk identification — inferring identity from
    topology instead of an authoritative source — applied to networking instead of storage.

    Zero or multiple matches is a hard failure — decision 6. A function that returns an
    interface it did not unambiguously find is the bug this design exists to prevent.
    """
    proc_net_route_path = Path(proc_net_route_path)
    lines = proc_net_route_path.read_text().splitlines()
    if not lines:
        raise InterfaceDiscoveryError(f"{proc_net_route_path} is empty")

    header = lines[0].split()
    try:
        iface_idx = header.index("Iface")
        dest_idx = header.index("Destination")
    except ValueError as exc:
        raise InterfaceDiscoveryError(
            f"{proc_net_route_path} header is missing expected columns: {header}"
        ) from exc

    matches = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) <= max(iface_idx, dest_idx):
            continue
        if fields[dest_idx] == DEFAULT_ROUTE_DESTINATION:
            matches.append(fields[iface_idx])

    if not matches:
        raise InterfaceDiscoveryError(
            f"no default route (Destination={DEFAULT_ROUTE_DESTINATION}) found in "
            f"{proc_net_route_path} — networking may not be up yet; this unit should run after "
            "network-online.target"
        )
    if len(matches) > 1:
        raise InterfaceDiscoveryError(
            f"{len(matches)} default route entries found in {proc_net_route_path}: {matches!r} "
            "— refusing to guess which interface is external"
        )
    return matches[0]


def write_interface_file(output_path: os.PathLike | str, interface: str) -> None:
    """Exactly one line, no trailing garbage — decision 7. Written via a temp file in the same
    directory plus os.replace, so an interrupted write can't leave a truncated file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=".external-interface.", text=True
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(interface + "\n")
        os.replace(tmp_name, output_path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass
        raise


def discover(output_path, proc_net_route_path, dry_run: bool) -> int:
    interface = find_default_route_interface(proc_net_route_path)
    print(f"identified external interface: {interface}")

    if dry_run:
        print(f"[dry-run] would write {interface!r} to {output_path}")
        return 0

    write_interface_file(output_path, interface)
    print(f"wrote {output_path}")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify the default-route interface and write it to a fixed file."
    )
    parser.add_argument("--output-path", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--proc-net-route-path",
        default=DEFAULT_PROC_NET_ROUTE_PATH,
        help="Override for tests; points discovery at a fixture file instead of /proc/net/route.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return discover(
            output_path=args.output_path,
            proc_net_route_path=args.proc_net_route_path,
            dry_run=args.dry_run,
        )
    except InterfaceDiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
