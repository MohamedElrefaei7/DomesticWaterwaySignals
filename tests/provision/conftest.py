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


class FakeCommandRunner:
    """A general-purpose stand-in for an injected `run` callable (install_docker.py,
    discover_external_interface.py), as opposed to provisioning 1's StubRunner, which
    monkeypatches subprocess.run globally. Records every call, in order; answers from a
    per-command-prefix script; and can run a side-effect callback (e.g. writing fake file
    content, the way a real `curl -o <path>` would) without ever touching a real process.
    """

    def __init__(self):
        self.calls = []
        self._responses = {}
        self._side_effects = []

    def script(self, prefix, returncode=0, stdout="", stderr=""):
        self._responses[tuple(prefix)] = FakeCompletedProcess(returncode, stdout, stderr)

    def on_call(self, prefix, effect):
        """effect(cmd) runs for its side effects whenever a recorded command's prefix matches —
        e.g. writing content to the path a fake `curl -o <path>` was asked to write to.
        """
        self._side_effects.append((tuple(prefix), effect))

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        for prefix, effect in self._side_effects:
            if tuple(cmd[: len(prefix)]) == prefix:
                effect(cmd)
        for prefix, response in self._responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return response
        return FakeCompletedProcess(0, "", "")

    def calls_with(self, prefix):
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


@pytest.fixture
def fake_runner():
    return FakeCommandRunner()


@pytest.fixture
def fake_os_release(tmp_path):
    """Return a builder: fake_os_release("jammy") -> Path to a fixture /etc/os-release
    declaring that VERSION_CODENAME. Each call gets its own file.
    """
    counter = itertools.count()

    def _build(codename: str) -> Path:
        path = tmp_path / f"os-release-{next(counter)}"
        path.write_text(
            'PRETTY_NAME="Ubuntu 24.04.1 LTS"\n'
            'NAME="Ubuntu"\n'
            'VERSION_ID="24.04"\n'
            f"VERSION_CODENAME={codename}\n"
            "ID=ubuntu\n"
        )
        return path

    return _build


@pytest.fixture
def fake_interface_file(tmp_path):
    """Return a builder: fake_interface_file("eth7") -> Path to a fixture single-line
    /etc/dws/external-interface, standing in for the file discover_external_interface.py's boot
    unit writes (CLAUDE.md § 10) for configure_firewall.py to read. Each call gets its own file,
    so a test can build more than one independent fixture without one contaminating another. Pass
    "" to build a file that exists but is empty — the fixture for decision 12's fatal-before-any-
    command path.
    """
    counter = itertools.count()

    def _build(interface: str) -> Path:
        path = tmp_path / f"external-interface-{next(counter)}"
        path.write_text(f"{interface}\n" if interface else "")
        return path

    return _build


@pytest.fixture
def fake_proc_net_route(tmp_path):
    """Return a builder: fake_proc_net_route([("eth0", "00000000"), ...]) -> Path to a fixture
    /proc/net/route with a real header row and one data row per (iface, destination_hex) pair.
    Each call gets its own file.
    """
    counter = itertools.count()

    def _build(rows) -> Path:
        path = tmp_path / f"route{next(counter)}"
        header = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        lines = [header]
        for iface, destination in rows:
            lines.append(f"{iface}\t{destination}\t0102A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n")
        path.write_text("".join(lines))
        return path

    return _build
