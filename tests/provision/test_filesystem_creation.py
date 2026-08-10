"""See CLAUDE.md § 9 decisions 4-6: blkid's specific exit code — not just its exit status —
decides whether mkfs runs, and the UUID is read only after that decision, never before.
"""

import pytest

import mount_data_volume as mdv


def test_skips_mkfs_when_blkid_reports_a_type(stub_runner, fake_completed_process):
    stub_runner.blkid_type = fake_completed_process(0, "ext4\n")
    stub_runner.blkid_uuid = fake_completed_process(0, "existing-uuid\n")

    mdv.ensure_filesystem("/dev/nvme1n1")

    assert not stub_runner.calls_with(["mkfs.ext4"])


def test_runs_mkfs_when_blkid_exits_two(stub_runner, fake_completed_process):
    stub_runner.blkid_type = fake_completed_process(2, "")
    stub_runner.blkid_uuid = fake_completed_process(0, "fresh-uuid\n")

    mdv.ensure_filesystem("/dev/nvme1n1")

    assert stub_runner.calls_with(["mkfs.ext4"])


@pytest.mark.parametrize("returncode", [1, 4])
def test_raises_when_blkid_exits_with_an_unexpected_code(stub_runner, fake_completed_process, returncode):
    stub_runner.blkid_type = fake_completed_process(returncode, "")

    with pytest.raises(mdv.FilesystemProbeError):
        mdv.ensure_filesystem("/dev/nvme1n1")

    # Asserting the raise alone is not enough — the harm is the format, not the exception.
    assert not stub_runner.calls_with(["mkfs.ext4"])


def test_uuid_is_read_after_mkfs(stub_runner, fake_completed_process):
    stub_runner.blkid_type = fake_completed_process(2, "")
    stub_runner.blkid_uuid = fake_completed_process(0, "post-mkfs-uuid\n")

    uuid = mdv.ensure_filesystem("/dev/nvme1n1")

    assert uuid == "post-mkfs-uuid"
    mkfs_index = next(i for i, c in enumerate(stub_runner.calls) if c[0] == "mkfs.ext4")
    uuid_index = next(
        i for i, c in enumerate(stub_runner.calls) if c[0] == "blkid" and "UUID" in c
    )
    assert mkfs_index < uuid_index, "UUID was read before mkfs ran"
