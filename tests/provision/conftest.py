"""Fixtures for tests/provision/ — see infra/provision/mount_data_volume.py and CLAUDE.md § 9.

Builds tmp_path trees standing in for /sys and /etc/fstab, and a fake subprocess.run that
answers blkid/mkfs/mount/findmnt calls from canned responses while recording every call in
order, so the load-bearing logic is testable without root, a real device, or an instance.
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "provision"))

import mount_data_volume as mdv  # noqa: E402


@pytest.fixture
def fake_sysfs(tmp_path):
    """Return a builder: fake_sysfs({"nvme0n1": "vol0deadbeef\n", ...}) -> Path usable as
    --sysfs-root / sysfs_root=. Each call gets its own subtree, so a test can build more than
    one independent fixture without one contaminating the other. Serial strings should include
    the trailing newline real sysfs reads produce, unless a test deliberately omits it.
    """
    counter = itertools.count()

    def _build(devices: dict) -> Path:
        root = tmp_path / f"sys{next(counter)}"
        block_dir = root / "block"
        block_dir.mkdir(parents=True)
        for name, serial in devices.items():
            device_dir = block_dir / name / "device"
            device_dir.mkdir(parents=True)
            (device_dir / "serial").write_text(serial)
        return root

    return _build


@pytest.fixture
def fake_fstab(tmp_path):
    """A realistic /etc/fstab: a root entry, a comment, and a blank line. Returns its path.

    The root entry's fields are separated by irregular whitespace (real fstab files are often
    hand-aligned in columns), deliberately not what a naive .split() + " ".join() rewrite would
    reproduce — so a mutation that rebuilds unrelated lines from parsed fields instead of passing
    them through verbatim is actually observable, rather than accidentally reconstructing the
    same single-spaced string it started with.
    """
    path = tmp_path / "fstab"
    path.write_text(
        "# /etc/fstab: static file system information.\n"
        "UUID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee  /     ext4   defaults  0 1\n"
        "\n"
    )
    return path


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class StubRunner:
    """Stands in for subprocess.run: records every call, in order, and answers blkid/mkfs/mount/
    findmnt from canned per-purpose responses. An unrecognized command raises, so a test only
    ever sees the calls it configured for.
    """

    def __init__(self):
        self.calls = []
        self.blkid_type = FakeCompletedProcess(2, "")
        self.blkid_uuid = FakeCompletedProcess(0, "")
        self.findmnt = FakeCompletedProcess(0, "")

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[0] == "blkid" and "TYPE" in cmd:
            return self.blkid_type
        if cmd[0] == "blkid" and "UUID" in cmd:
            return self.blkid_uuid
        if cmd[0] == "mkfs.ext4":
            return FakeCompletedProcess(0)
        if cmd[0] == "mount":
            return FakeCompletedProcess(0)
        if cmd[0] == "findmnt":
            return self.findmnt
        raise AssertionError(f"unstubbed command: {cmd}")

    def calls_with(self, prefix):
        return [c for c in self.calls if c[: len(prefix)] == prefix]


@pytest.fixture
def stub_runner(monkeypatch):
    runner = StubRunner()
    monkeypatch.setattr(mdv.subprocess, "run", runner)
    return runner


@pytest.fixture
def fake_completed_process():
    """Returns the FakeCompletedProcess class itself, so tests can build canned responses
    (fake_completed_process(0, "ext4\n")) via fixture injection rather than
    `from conftest import FakeCompletedProcess` — a bare-name import that collides with
    tests/terraform/conftest.py's own "conftest" module when both suites run in one session.
    """
    return FakeCompletedProcess
