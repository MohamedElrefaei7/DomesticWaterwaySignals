"""See CLAUDE.md § 9 decisions 6-10: UUID-keyed entries, nofail, idempotent-by-mount-point
rewriting, and --dry-run performing no mutation.
"""

import mount_data_volume as mdv


def test_fstab_entry_uses_uuid_not_a_device_path(fake_fstab):
    mdv.upsert_fstab_entry(fake_fstab, "1111-aaaa", "/mnt/data")

    contents = fake_fstab.read_text()
    lines = [line for line in contents.splitlines() if "/mnt/data" in line]
    assert len(lines) == 1
    assert lines[0].startswith("UUID=1111-aaaa")
    assert "/dev/nvme" not in contents


def test_fstab_entry_includes_nofail(fake_fstab):
    mdv.upsert_fstab_entry(fake_fstab, "1111-aaaa", "/mnt/data")

    lines = [line for line in fake_fstab.read_text().splitlines() if "/mnt/data" in line]
    assert len(lines) == 1
    options_field = lines[0].split()[3]
    assert "nofail" in options_field.split(",")


def test_rewriting_replaces_the_existing_mount_point_line(fake_fstab):
    mdv.upsert_fstab_entry(fake_fstab, "1111-aaaa", "/mnt/data")
    mdv.upsert_fstab_entry(fake_fstab, "2222-bbbb", "/mnt/data")

    lines = [line for line in fake_fstab.read_text().splitlines() if "/mnt/data" in line]
    assert len(lines) == 1, f"expected exactly one line for /mnt/data, found {len(lines)}"
    assert lines[0].startswith("UUID=2222-bbbb")


def test_unrelated_fstab_lines_are_preserved_byte_for_byte(fake_fstab):
    before_lines = fake_fstab.read_text().splitlines(keepends=True)

    mdv.upsert_fstab_entry(fake_fstab, "1111-aaaa", "/mnt/data")

    after_lines = fake_fstab.read_text().splitlines(keepends=True)
    assert after_lines[: len(before_lines)] == before_lines


def test_dry_run_writes_nothing(fake_sysfs, fake_fstab, stub_runner, fake_completed_process, tmp_path):
    # An already-formatted volume, so the real UUID is available to preview — the "no
    # filesystem yet" dry-run branch previews the line's shape instead, exercised separately.
    stub_runner.blkid_type = fake_completed_process(0, "ext4\n")
    stub_runner.blkid_uuid = fake_completed_process(0, "existing-uuid\n")

    sysfs_root = fake_sysfs({"nvme1n1": "vol0dryrun\n"})
    mount_point = tmp_path / "mnt" / "data"

    before_content = fake_fstab.read_text()
    before_mtime = fake_fstab.stat().st_mtime_ns

    exit_code = mdv.provision(
        volume_id="vol-0dryrun",
        mount_point=str(mount_point),
        sysfs_root=sysfs_root,
        fstab_path=fake_fstab,
        dry_run=True,
    )

    assert exit_code == 0
    assert fake_fstab.read_text() == before_content
    assert fake_fstab.stat().st_mtime_ns == before_mtime
    assert not stub_runner.calls_with(["mkfs.ext4"])
    assert not stub_runner.calls_with(["mount"])
    assert not stub_runner.calls_with(["findmnt"])
    assert not mount_point.exists()
