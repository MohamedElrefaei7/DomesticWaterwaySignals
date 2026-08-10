#!/usr/bin/env python3
"""Identify, format (if needed), and mount the project's separate EBS data volume.

Provisioning 1 of 3 — see CLAUDE.md § 9. Docker installation and firewall rules are separate,
later commits; this script's only job is disk identification and mounting.

Run by a human, on the instance, as root:

    sudo python3 infra/provision/mount_data_volume.py --volume-id vol-0123456789abcdef0

--volume-id has no default; it comes from `terraform output data_volume_id`, pasted by the
human. This script never queries IMDS or the AWS API to discover it, so it stays runnable
offline in tests and carries no credential or network dependency of its own.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MOUNT_POINT = "/mnt/data"  # Fixed constant — never derived from argv[0], __file__, or
                                    # cwd (CLAUDE.md § 5); a wrong value here is a mount over the
                                    # wrong directory. Decision 9.
DEFAULT_SYSFS_ROOT = "/sys"
DEFAULT_FSTAB_PATH = "/etc/fstab"

# nofail so a volume that fails to attach doesn't drop the instance into emergency mode,
# unreachable over SSH *and* SSM; noatime because atime writes are pure amplification under a
# database; the short device timeout avoids a 90-second apparently-hung boot. Decision 7. The
# cost of nofail — a boot that succeeds with /mnt/data empty on the root volume, silently — is
# paid at the application layer: CLAUDE.md § 9 requires the Compose systemd unit to carry
# RequiresMountsFor=/mnt/data. That unit is a later commit; the contract is recorded now.
FSTAB_OPTIONS = "defaults,noatime,nofail,x-systemd.device-timeout=10s"


class DiskIdentificationError(RuntimeError):
    """Zero or multiple NVMe serial matches. No fallback exists — decision 3."""


class FilesystemProbeError(RuntimeError):
    """blkid exited with a code that is neither 0 (has a filesystem) nor 2 (has none) — decision 4."""


class FstabVerificationError(RuntimeError):
    """`findmnt --verify` reported a problem after the fstab write — decision 10."""


def _normalize_serial(value: str) -> str:
    """Dash-stripped, lowercased, whitespace-trimmed.

    AWS presents the volume as 'vol-0123456789abcdef0'; the kernel serial omits the dash
    ('vol0123456789abcdef0'), and sysfs attribute reads carry a trailing newline. Comparing
    without normalizing both sides matches nothing, which reads as "serial matching doesn't
    work" and is the most likely reason someone reaches for a topology-based fallback ("the disk
    that isn't root") — forbidden by CLAUDE.md § 5. Decision 1.
    """
    return value.strip().replace("-", "").lower()


def find_data_volume_device(volume_id: str, sysfs_root: os.PathLike | str) -> str:
    """Return the /dev/<name> block device whose NVMe serial matches volume_id.

    Reads /sys/block/nvme*n*/device/serial — the block device, not the NVMe controller
    (/sys/class/nvme/nvme*/serial) — per CLAUDE.md § 5. Decision 2. The device mkfs needs is a
    namespace beneath the controller, and reading from /sys/block gives the block device name
    directly from the directory entry rather than inferring nvme1n1 from nvme1.

    Zero matches and more than one match are both hard failures with no fallback — decision 3.
    A function that can return a device it did not positively identify is the bug this design
    exists to prevent.
    """
    sysfs_root = Path(sysfs_root)
    target = _normalize_serial(volume_id)

    matches = []
    for entry in sorted(sysfs_root.glob("block/nvme*n*")):
        serial_path = entry / "device" / "serial"
        if not serial_path.is_file():
            continue
        if _normalize_serial(serial_path.read_text()) == target:
            matches.append(entry.name)

    if not matches:
        raise DiskIdentificationError(
            f"no NVMe block device serial matched volume ID {volume_id!r} "
            f"(searched {sysfs_root / 'block'})"
        )
    if len(matches) > 1:
        raise DiskIdentificationError(
            f"{len(matches)} NVMe block devices matched volume ID {volume_id!r}: {matches!r} "
            "— refusing to guess which one is the data volume"
        )
    return f"/dev/{matches[0]}"


def probe_filesystem_type(device: str) -> str | None:
    """Return the filesystem TYPE, or None if the device has none.

    blkid exits 2 for "no recognizable filesystem" and other non-zero codes for real errors —
    missing device, permission denied, malformed arguments. Treating any non-zero exit as "no
    filesystem" formats a healthy volume the moment blkid fails for an unrelated reason. Probes
    the device directly (-p) rather than the cached /run/blkid/blkid.tab, which can be stale in
    the same way a naive exit-code check is. Decision 4 — the decision that can destroy data.
    """
    result = subprocess.run(
        ["blkid", "-p", "-o", "value", "-s", "TYPE", device],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip() or None
    if result.returncode == 2:
        return None
    raise FilesystemProbeError(
        f"blkid -p -o value -s TYPE {device} exited {result.returncode} "
        f"(expected 0 or 2): {result.stderr.strip()}"
    )


def run_mkfs(device: str) -> None:
    """mkfs.ext4 -m 0, on the whole device — no partition table.

    The volume has exactly one purpose and is resized by resizing the EBS volume, so a
    partition table only adds a layer that can drift and changes which object carries the UUID.
    -m 0 because the default 5% root reservation is meaningful on a data volume and pointless
    when only Postgres writes there. Decision 5 — do not "fix" the missing partition table.
    """
    subprocess.run(["mkfs.ext4", "-m", "0", device], check=True)


def read_filesystem_uuid(device: str) -> str:
    """Read the filesystem UUID. Callers must call this after any mkfs, never before — decision
    6. A UUID captured pre-format belongs to a filesystem that is no longer there.
    """
    result = subprocess.run(
        ["blkid", "-p", "-o", "value", "-s", "UUID", device],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def ensure_filesystem(device: str) -> str:
    """Probe, mkfs only if genuinely absent, then read the UUID.

    The UUID read always follows the mkfs decision — on both branches, since the pre-existing-
    filesystem path needs its (unchanged) UUID too, and the freshly-formatted path's UUID does
    not exist until after mkfs runs. Decision 6.
    """
    fs_type = probe_filesystem_type(device)
    if fs_type is None:
        run_mkfs(device)
    return read_filesystem_uuid(device)


def build_fstab_line(uuid: str, mount_point: str) -> str:
    return f"UUID={uuid} {mount_point} ext4 {FSTAB_OPTIONS} 0 2"


def upsert_fstab_entry(fstab_path: os.PathLike | str, uuid: str, mount_point: str) -> None:
    """Replace any existing line whose mount point (field 2) matches; otherwise append.

    Keyed by mount point, never by UUID — decision 8. Re-running provisioning is normal; a
    re-run after a reformat has a new UUID, and matching on the old one would leave the stale
    line behind instead of replacing it. Comments and unrelated lines pass through unchanged.
    Written via a temp file in the same directory plus os.replace, so an interrupted write
    cannot leave a truncated /etc/fstab.
    """
    fstab_path = Path(fstab_path)
    lines = fstab_path.read_text().splitlines(keepends=True)
    new_line = build_fstab_line(uuid, mount_point) + "\n"

    output = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        fields = stripped.split()
        is_target = not stripped.startswith("#") and len(fields) >= 2 and fields[1] == mount_point
        if is_target:
            output.append(new_line)
            replaced = True
        else:
            output.append(line)

    if not replaced:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append(new_line)

    fd, tmp_name = tempfile.mkstemp(dir=str(fstab_path.parent), prefix=".fstab.", text=True)
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(output)
        os.replace(tmp_name, fstab_path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass
        raise


def verify_fstab() -> None:
    """`findmnt --verify` catches a syntactically valid fstab line referencing a nonexistent
    UUID now, rather than at the next reboot. Decision 10.
    """
    result = subprocess.run(
        ["findmnt", "--verify", "--verbose"], capture_output=True, text=True, check=False
    )
    print(result.stdout)
    if result.returncode != 0:
        raise FstabVerificationError(
            f"findmnt --verify reported problems (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


def check_mount_point(mount_point: Path, dry_run: bool) -> None:
    """mkdir -p before mounting. If the directory already exists and is non-empty, something
    already wrote to the root volume at this path — warn loudly and name the files rather than
    silently mounting over and hiding them. Decision 9.
    """
    if mount_point.exists():
        contents = sorted(p.name for p in mount_point.iterdir())
        if contents:
            print(
                f"WARNING: {mount_point} already exists and is not empty — mounting here will "
                f"hide: {contents}"
            )
        return
    if dry_run:
        print(f"[dry-run] would run: mkdir -p {mount_point}")
    else:
        mount_point.mkdir(parents=True)


def mount_volume(mount_point: Path) -> None:
    subprocess.run(["mount", str(mount_point)], check=True)


def provision(
    volume_id: str,
    mount_point: str,
    sysfs_root: os.PathLike | str,
    fstab_path: os.PathLike | str,
    dry_run: bool,
) -> int:
    device = find_data_volume_device(volume_id, sysfs_root)
    print(f"identified data volume {volume_id} as {device}")

    fs_type = probe_filesystem_type(device)
    if fs_type is None:
        print(f"{device}: blkid reports no filesystem (exit 2)")
        if dry_run:
            # The real UUID does not exist until mkfs actually runs, so a first-time dry run
            # (nothing provisioned yet) can only preview the line's shape, not its literal
            # value. A re-run against an already-formatted volume falls through to the branch
            # below instead, where the real UUID is available to preview.
            print(f"[dry-run] would run: mkfs.ext4 -m 0 {device}")
            print(
                "[dry-run] would write fstab line: "
                f"UUID=<assigned-by-mkfs> {mount_point} ext4 {FSTAB_OPTIONS} 0 2"
            )
            print(f"[dry-run] would run: mount {mount_point}")
            return 0
        run_mkfs(device)
    else:
        print(f"{device}: blkid reports existing filesystem type {fs_type!r} — skipping mkfs")

    uuid = read_filesystem_uuid(device)
    print(f"filesystem UUID: {uuid}")

    check_mount_point(Path(mount_point), dry_run)

    fstab_line = build_fstab_line(uuid, mount_point)
    if dry_run:
        print(f"[dry-run] would write fstab line: {fstab_line}")
        print(f"[dry-run] would run: mount {mount_point}")
        return 0

    upsert_fstab_entry(fstab_path, uuid, mount_point)
    verify_fstab()
    mount_volume(Path(mount_point))
    print(f"mounted {device} at {mount_point} (UUID={uuid})")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify, format if needed, and mount the project's EBS data volume."
    )
    parser.add_argument(
        "--volume-id",
        required=True,
        help="AWS EBS volume ID (e.g. from `terraform output data_volume_id`). No default — "
        "never discovered via IMDS or the AWS API.",
    )
    parser.add_argument("--mount-point", default=DEFAULT_MOUNT_POINT)
    parser.add_argument(
        "--sysfs-root",
        default=DEFAULT_SYSFS_ROOT,
        help="Override for tests; points identification at a fixture tree instead of /sys.",
    )
    parser.add_argument(
        "--fstab-path",
        default=DEFAULT_FSTAB_PATH,
        help="Override for tests; points the fstab writer at a fixture file instead of /etc/fstab.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return provision(
            volume_id=args.volume_id,
            mount_point=args.mount_point,
            sysfs_root=args.sysfs_root,
            fstab_path=args.fstab_path,
            dry_run=args.dry_run,
        )
    except (DiskIdentificationError, FilesystemProbeError, FstabVerificationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
