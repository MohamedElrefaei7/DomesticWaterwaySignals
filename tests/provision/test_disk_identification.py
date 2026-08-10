"""See CLAUDE.md § 9 decisions 1-3: dash-stripped serial comparison, reading the block device
rather than the controller, and the zero/multiple-match hard-fail with no fallback.
"""

import re

import pytest

from mount_data_volume import DiskIdentificationError, find_data_volume_device


def test_matches_serial_with_dashes_stripped(fake_sysfs):
    sysfs_root = fake_sysfs({"nvme0n1": "vol0abc\n"})

    device = find_data_volume_device("vol-0abc", sysfs_root)

    assert device == "/dev/nvme0n1"


def test_returns_the_block_device_not_the_controller(fake_sysfs):
    sysfs_root = fake_sysfs({"nvme1n1": "vol0abc\n"})

    device = find_data_volume_device("vol-0abc", sysfs_root)

    assert device == "/dev/nvme1n1"
    assert re.search(r"n\d+$", device), f"{device} does not end in a namespace suffix"


def test_raises_on_zero_matches(fake_sysfs):
    sysfs_root = fake_sysfs({"nvme0n1": "vol0root\n"})

    with pytest.raises(DiskIdentificationError, match="vol-0missing"):
        find_data_volume_device("vol-0missing", sysfs_root)


def test_raises_on_multiple_matches(fake_sysfs):
    sysfs_root = fake_sysfs({"nvme1n1": "vol0dup\n", "nvme2n1": "vol0dup\n"})

    with pytest.raises(DiskIdentificationError):
        find_data_volume_device("vol-0dup", sysfs_root)


def test_never_returns_a_device_on_failure(fake_sysfs):
    # Includes a genuine non-root candidate (nvme1n1) so a "fall back to the sole non-root
    # device" bug has something to fall back to and actually gets caught — a zero-match tree
    # containing only the root device can't exercise that fallback at all.
    zero_match_root = fake_sysfs({"nvme0n1": "vol0root\n", "nvme1n1": "vol0other\n"})
    with pytest.raises(DiskIdentificationError):
        find_data_volume_device("vol-0missing", zero_match_root)

    multi_match_root = fake_sysfs({"nvme1n1": "vol0dup\n", "nvme2n1": "vol0dup\n"})
    with pytest.raises(DiskIdentificationError):
        find_data_volume_device("vol-0dup", multi_match_root)
