#!/usr/bin/env python3
"""Configure the host firewall (ufw) and the container firewall (DOCKER-USER) as two gates in
series — see CLAUDE.md § 5 and § 11.

ufw filters the host's own INPUT chain. Traffic to a *published container port* is DNAT'd and
traverses FORWARD instead, bypassing ufw entirely via Docker's DOCKER-USER chain. Neither gate is
redundant and neither substitutes for the other: this script is not "the same firewall configured
twice," it is two separate enforcement points for two different traffic paths.

Provisioning 3 of 3 — see CLAUDE.md § 11. This is the lockout-risk commit: ufw is `deny incoming`,
**`allow outgoing`**, because SSM Session Manager works by the agent making an *outbound* HTTPS
connection, there is no SSH key pair on this instance (CLAUDE.md § 8), and denying outbound leaves
no way back in short of detaching the root volume.

Run by a human, on the instance, as root, only after confirming an SSM session works and only
after provisioning 2 has installed Docker and written /etc/dws/external-interface:

    sudo python3 infra/provision/configure_firewall.py --admin-cidr 47.166.211.114/32

--admin-cidr has no default — it must equal terraform.tfvars' ssh_admin_cidr, and a default here
would silently widen one of the two gates while leaving the other correctly scoped.

The boot unit (dws-docker-firewall.service) invokes this script with --docker-user-only, which
reapplies only the DOCKER-USER rules and never touches ufw — ufw's own configuration already
persists across reboot; raw iptables rules do not.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_INTERFACE_FILE = "/etc/dws/external-interface"

UFW_SSH_PORT = 22
UFW_WEB_PORTS = (80, 443)
UFW_ENABLE_COMMAND = ["ufw", "--force", "enable"]  # --force: bare `ufw enable` prompts for
# confirmation on stdin, decision 3. Run non-interactively — from a script, from systemd, from an
# SSM session with no tty — that prompt hangs forever or reads EOF and aborts, leaving the
# firewall in whatever partial state it was in when it stopped, which this script would then have
# no way to detect and would report as configured.

DOCKER_RESTART_COMMAND = ["systemctl", "restart", "docker"]

DOCKER_USER_CHAIN = "DOCKER-USER"
DOCKER_USER_WEB_DPORTS = "80,443"


class InterfaceFileError(RuntimeError):
    """The interface file is missing or empty — decision 12. Raised before a single command is
    issued; a partially-applied firewall looks configured and is not, which is worse than none.
    """


class FirewallCommandError(RuntimeError):
    """A mutating ufw/iptables/ip6tables/systemctl command exited non-zero. Every mutating
    command in this script is routed through here rather than trusted on an unchecked exit —
    that check *is* "verify the rules were added"; a separate post-hoc `ufw status` parse would
    only be re-deriving what the exit code of the add command already told us.
    """


def _default_run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)


def _run_checked(run, cmd) -> None:
    result = run(cmd)
    if result.returncode != 0:
        raise FirewallCommandError(
            f"{' '.join(cmd)} exited {result.returncode}: {(result.stderr or '').strip()}"
        )


def read_external_interface(interface_file) -> str:
    """The interface name is read, never derived — provisioning 2's discover_external_interface.py
    already solved that problem (CLAUDE.md § 10) and wrote the answer here. Missing or empty is
    fatal, before any command runs: no fallback to a guessed default, no `eth0` — decision 12.
    """
    path = Path(interface_file)
    if not path.is_file():
        raise InterfaceFileError(
            f"{path} does not exist — run discover_external_interface.py (or its boot unit, "
            "dws-external-interface.service) before this script"
        )
    interface = path.read_text().strip()
    if not interface:
        raise InterfaceFileError(f"{path} exists but is empty")
    return interface


# --- ufw -------------------------------------------------------------------------------------


def ufw_default_commands() -> list[list[str]]:
    """`deny incoming`, `allow outgoing` — never the reverse. Outbound is the SSM lifeline;
    denying it strands an instance that has no SSH key pair to fall back on. Decision 1.
    """
    return [
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
    ]


def ufw_allow_commands(admin_cidr: str) -> list[list[str]]:
    """SSH is scoped to --admin-cidr; there is no bare `ufw allow 22` or `ufw allow ssh` anywhere
    in this script — decision 4. 80 and 443 are open to the world, matching the allowlist `{22,
    80, 443}` asserted by exact set equality, same discipline as the security group (CLAUDE.md §
    8).
    """
    return [
        ["ufw", "allow", "from", admin_cidr, "to", "any", "port", str(UFW_SSH_PORT), "proto", "tcp"],
        *[["ufw", "allow", f"{port}/tcp"] for port in UFW_WEB_PORTS],
    ]


def apply_ufw(admin_cidr: str, run, dry_run: bool) -> None:
    """All rules are added before `ufw --force enable`, never after — decision 2. `ufw enable`
    activates default-deny immediately; any existing SSH session survives on conntrack, but a
    fresh connection would find the admin-CIDR rule not yet in place if enable ran first.
    """
    commands = ufw_default_commands() + ufw_allow_commands(admin_cidr)

    if dry_run:
        for cmd in commands:
            print(f"[dry-run] would run: {' '.join(cmd)}")
        print(f"[dry-run] would run: {' '.join(UFW_ENABLE_COMMAND)}")
        return

    for cmd in commands:
        _run_checked(run, cmd)
    _run_checked(run, UFW_ENABLE_COMMAND)


# --- DOCKER-USER -------------------------------------------------------------------------------


def docker_user_append_rules(interface: str) -> list[list[str]]:
    """The four DOCKER-USER rules, interface-scoped, in the order decision 6 requires.

    Rule 1 (conntrack RETURN on -i) must be first. Without it, a container can still send
    outbound packets via rule 2, but every reply arrives on -i <interface> and hits the terminal
    DROP — a container that resolves DNS, opens a socket, and then hangs forever, which looks
    like a slow upstream rather than a firewall bug.

    RETURN, not ACCEPT, on every rule (decision 7): ACCEPT would short-circuit Docker's own
    per-container and port-publishing chains; RETURN sends the packet back to FORWARD so Docker's
    own filtering still applies.
    """
    return [
        ["-A", DOCKER_USER_CHAIN, "-i", interface, "-m", "conntrack",
         "--ctstate", "RELATED,ESTABLISHED", "-j", "RETURN"],
        ["-A", DOCKER_USER_CHAIN, "-o", interface, "-j", "RETURN"],
        ["-A", DOCKER_USER_CHAIN, "-i", interface, "-p", "tcp", "-m", "multiport",
         "--dports", DOCKER_USER_WEB_DPORTS, "-j", "RETURN"],
        ["-A", DOCKER_USER_CHAIN, "-i", interface, "-j", "DROP"],
    ]


def _apply_docker_user_for_binary(
    binary: str, interface: str, run, dry_run: bool, create_chain: bool
) -> None:
    """One binary (iptables or ip6tables): optionally create the chain, flush it, then append the
    four rules. Flushing before appending — decision 8 — is what makes re-running provisioning
    idempotent; this project owns DOCKER-USER by convention (Docker creates it and never writes
    to it), so flushing it is safe in a way that flushing a chain this project doesn't own would
    not be.
    """
    if create_chain:
        create_cmd = [binary, "-N", DOCKER_USER_CHAIN]
        if dry_run:
            print(f"[dry-run] would run: {' '.join(create_cmd)} (ignoring 'chain already exists')")
        else:
            # Decision 9: ip6tables may not have DOCKER-USER yet — Docker's IPv6 support is off
            # by default, so the chain this script otherwise assumes exists (iptables' copy,
            # which Docker itself creates) may simply not exist here yet. -N on a chain that
            # already exists on a later re-run exits non-zero for a reason that isn't a failure —
            # the one place in this script a non-zero exit is expected rather than fatal, so this
            # call deliberately bypasses _run_checked.
            run(create_cmd)

    flush_cmd = [binary, "-F", DOCKER_USER_CHAIN]
    append_cmds = [[binary, *rule] for rule in docker_user_append_rules(interface)]

    if dry_run:
        print(f"[dry-run] would run: {' '.join(flush_cmd)}")
        for cmd in append_cmds:
            print(f"[dry-run] would run: {' '.join(cmd)}")
        return

    _run_checked(run, flush_cmd)
    for cmd in append_cmds:
        _run_checked(run, cmd)


def apply_docker_user_rules(interface: str, run, dry_run: bool) -> None:
    """IPv4 first, then IPv6 with the identical rule set (decision 9) — never "handled" by
    turning IPv6 off. Docker's IPv6 support is off by default today, which makes skipping
    ip6tables look harmless right up until someone enables it and every rule here silently covers
    only half the traffic.
    """
    _apply_docker_user_for_binary("iptables", interface, run, dry_run, create_chain=False)
    _apply_docker_user_for_binary("ip6tables", interface, run, dry_run, create_chain=True)


def run_docker_user_stage(interface_file, run, dry_run: bool) -> str:
    """Read the interface (fatal before any command — decision 12), then apply both rule sets.
    Shared by --docker-user-only mode and the full run, so both paths fail the same way on a bad
    interface file.
    """
    interface = read_external_interface(interface_file)
    apply_docker_user_rules(interface, run, dry_run)
    return interface


# --- orchestration -----------------------------------------------------------------------------


def configure(
    admin_cidr: str,
    interface_file,
    docker_user_only: bool,
    dry_run: bool,
    run=None,
) -> int:
    run = run or _default_run

    if docker_user_only:
        # Boot-unit mode: never touches ufw. ufw's own configuration is already persistent across
        # reboot; re-applying it on every boot would be redundant at best and, if this ever grew
        # a bug, a way to silently re-widen or re-narrow it outside of a human running this script
        # deliberately.
        interface = run_docker_user_stage(interface_file, run, dry_run)
        print(f"applied DOCKER-USER rules for interface {interface!r} (ufw untouched)")
        return 0

    # Decision 12 is not scoped to --docker-user-only: reading the interface file before the
    # first ufw command means a bad file fails the whole run atomically, rather than leaving ufw
    # enabled with the DOCKER-USER stage still to fail.
    interface = read_external_interface(interface_file)

    apply_ufw(admin_cidr, run, dry_run)

    # Decision 10: ufw --force enable rewrites the filter table via iptables-restore, which
    # discards Docker's own chains. Docker rebuilds them on daemon restart — so restart happens
    # after ufw enable and before the DOCKER-USER rules are applied, never before either.
    if dry_run:
        print(f"[dry-run] would run: {' '.join(DOCKER_RESTART_COMMAND)}")
    else:
        _run_checked(run, DOCKER_RESTART_COMMAND)

    apply_docker_user_rules(interface, run, dry_run)
    print(f"firewall configured: ufw enabled, DOCKER-USER rules applied for interface {interface!r}")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure ufw (host) and DOCKER-USER (containers) as two gates in series."
    )
    parser.add_argument(
        "--admin-cidr",
        default=None,
        help="CIDR allowed to reach SSH (port 22). No default — must equal terraform.tfvars' "
        "ssh_admin_cidr. Required unless --docker-user-only, which never touches ufw and so "
        "never needs it (dws-docker-firewall.service's boot-time invocation omits it).",
    )
    parser.add_argument(
        "--interface-file",
        default=DEFAULT_INTERFACE_FILE,
        help="Override for tests; points interface reads at a fixture file instead of "
        f"{DEFAULT_INTERFACE_FILE}.",
    )
    parser.add_argument(
        "--docker-user-only",
        action="store_true",
        help="Reapply only the DOCKER-USER rules; never touch ufw. Used by "
        "dws-docker-firewall.service at boot, since raw iptables rules do not persist across "
        "reboot but ufw's own configuration does.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.docker_user_only and not args.admin_cidr:
        print("error: --admin-cidr is required unless --docker-user-only", file=sys.stderr)
        return 2
    try:
        return configure(
            admin_cidr=args.admin_cidr,
            interface_file=args.interface_file,
            docker_user_only=args.docker_user_only,
            dry_run=args.dry_run,
        )
    except (InterfaceFileError, FirewallCommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
