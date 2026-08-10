#!/usr/bin/env python3
"""Install Docker Engine and the Compose plugin at exact pinned versions, held against
`apt upgrade`, via Docker's official repository.

Provisioning 2 of 3, part 2 — see CLAUDE.md § 10. ufw and DOCKER-USER rules are a separate,
later commit; this script only installs Docker and pins its version.

Run by a human, on the instance, as root. All four version flags are required, with no
defaults — resolve real current values first with:

    apt-cache madison docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo python3 infra/provision/install_docker.py \
    --docker-ce-version "<v>" --docker-ce-cli-version "<v>" \
    --containerd-version "<v>" --compose-plugin-version "<v>"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

DOCKER_GPG_URL = "https://download.docker.com/linux/ubuntu/gpg"
PGP_HEADER = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
KEYRING_DIR = Path("/etc/apt/keyrings")
KEYRING_PATH = KEYRING_DIR / "docker.asc"
SOURCES_LIST_PATH = Path("/etc/apt/sources.list.d/docker.list")
DEFAULT_OS_RELEASE_PATH = "/etc/os-release"

PACKAGE_NAMES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"]


class OsReleaseParseError(RuntimeError):
    """VERSION_CODENAME missing from /etc/os-release (or its override) — decision 4."""


class GpgKeyValidationError(RuntimeError):
    """The fetched GPG key doesn't look like a PGP public key block — decision 3."""


def _default_run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)


def read_version_codename(os_release_path) -> str:
    """VERSION_CODENAME, read from /etc/os-release, never hardcoded — decision 4. The AMI is
    expected to be bumped to a newer Ubuntu LTS deliberately at some point (CLAUDE.md § 8); a
    hardcoded codename would silently ship a Docker repo entry for the wrong release when that
    happens, and apt-get update would either fail outright or, worse, succeed against a
    mismatched repo.
    """
    os_release_path = Path(os_release_path)
    for line in os_release_path.read_text().splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    raise OsReleaseParseError(f"VERSION_CODENAME not found in {os_release_path}")


def read_dpkg_architecture(run) -> str:
    """Read, never assumed — the sources.list arch= field should match the real system
    architecture, not a hardcoded amd64 that quietly breaks the day this runs on arm64.
    """
    result = run(["dpkg", "--print-architecture"])
    return result.stdout.strip()


def build_sources_list_content(codename: str, arch: str) -> str:
    return (
        f"deb [arch={arch} signed-by={KEYRING_PATH}] "
        f"https://download.docker.com/linux/ubuntu {codename} stable\n"
    )


def fetch_and_install_gpg_key(tmp_dir, run) -> None:
    """Fetch to a temp file, validate it looks like a PGP key block, then dearmor into a
    repo-scoped keyring file — decision 3.

    Never apt-key add: it's deprecated and trusts a key for every repo on the system, not just
    Docker's. Never pipe curl straight into gpg --dearmor: a truncated or failed download piped
    directly through would produce a keyring file from partial or empty input, and the failure
    would surface later as a confusing signature-verification error instead of here, at the
    point where it's obvious.
    """
    tmp_key_path = Path(tmp_dir) / "docker.gpg.asc"
    run(["curl", "-fsSL", DOCKER_GPG_URL, "-o", str(tmp_key_path)])

    content = tmp_key_path.read_text() if tmp_key_path.exists() else ""
    if not content.startswith(PGP_HEADER):
        raise GpgKeyValidationError(
            f"downloaded key at {tmp_key_path} does not start with {PGP_HEADER!r} — refusing "
            "to trust a truncated or failed download"
        )

    run(["install", "-m", "0755", "-d", str(KEYRING_DIR)])
    run(["gpg", "--dearmor", "--yes", "-o", str(KEYRING_PATH), str(tmp_key_path)])


def build_install_command(
    docker_ce_version: str,
    docker_ce_cli_version: str,
    containerd_version: str,
    compose_plugin_version: str,
) -> list[str]:
    """Explicit pkg=version install strings, never a bare `apt-get install docker-ce` — decision
    1. That's `latest` on the database image again, wearing different clothes (CLAUDE.md § 8
    decision 6): an unrelated re-run months from now would resolve to whatever Docker most
    recently shipped, and a daemon behavior change would surface as a mystery instead of a diff.
    """
    return [
        "apt-get", "install", "-y",
        f"docker-ce={docker_ce_version}",
        f"docker-ce-cli={docker_ce_cli_version}",
        f"containerd.io={containerd_version}",
        f"docker-compose-plugin={compose_plugin_version}",
    ]


def install(
    docker_ce_version: str,
    docker_ce_cli_version: str,
    containerd_version: str,
    compose_plugin_version: str,
    os_release_path,
    dry_run: bool,
    run=None,
    sources_list_path=None,
) -> int:
    # sources_list_path has no CLI flag — the interface deliberately doesn't expose one, since
    # the real destination is always the same fixed system path. The parameter exists only so
    # tests can exercise the full non-dry-run install() without writing to a real /etc/apt.
    run = run or _default_run
    sources_list_path = Path(sources_list_path) if sources_list_path else SOURCES_LIST_PATH

    codename = read_version_codename(os_release_path)
    print(f"repo codename: {codename}")

    arch = read_dpkg_architecture(run)
    sources_content = build_sources_list_content(codename, arch)
    install_cmd = build_install_command(
        docker_ce_version, docker_ce_cli_version, containerd_version, compose_plugin_version
    )

    if dry_run:
        print(f"[dry-run] would write {sources_list_path}:\n{sources_content.rstrip()}")
        print("[dry-run] would run: apt-get update")
        print(
            f"[dry-run] would fetch GPG key from {DOCKER_GPG_URL} to a temp file, validate it, "
            f"and dearmor it to {KEYRING_PATH}"
        )
        print(f"[dry-run] would run: {' '.join(install_cmd)}")
        print(f"[dry-run] would run: apt-mark hold {' '.join(PACKAGE_NAMES)}")
        return 0

    sources_list_path.parent.mkdir(parents=True, exist_ok=True)
    sources_list_path.write_text(sources_content)

    with tempfile.TemporaryDirectory() as tmp_dir:
        fetch_and_install_gpg_key(tmp_dir, run)

    run(["apt-get", "update"])
    run(install_cmd)
    # apt-mark hold is the package-manager equivalent of prevent_destroy — decision 2. Without
    # it, the pin holds only until the next unrelated `apt upgrade` silently carries Docker
    # along with it.
    run(["apt-mark", "hold", *PACKAGE_NAMES])
    print(f"installed and held: {', '.join(PACKAGE_NAMES)}")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Docker Engine and the Compose plugin at exact pinned versions."
    )
    parser.add_argument("--docker-ce-version", required=True)
    parser.add_argument("--docker-ce-cli-version", required=True)
    parser.add_argument("--containerd-version", required=True)
    parser.add_argument("--compose-plugin-version", required=True)
    parser.add_argument("--os-release-path", default=DEFAULT_OS_RELEASE_PATH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return install(
            docker_ce_version=args.docker_ce_version,
            docker_ce_cli_version=args.docker_ce_cli_version,
            containerd_version=args.containerd_version,
            compose_plugin_version=args.compose_plugin_version,
            os_release_path=args.os_release_path,
            dry_run=args.dry_run,
        )
    except (OsReleaseParseError, GpgKeyValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
