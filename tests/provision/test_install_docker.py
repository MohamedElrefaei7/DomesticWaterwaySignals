"""See CLAUDE.md § 10 decisions 1-4: exact pinned package versions held with apt-mark hold, no
apt-key, a content-validated repo-scoped keyring, and a codename read from /etc/os-release,
never hardcoded.
"""

from pathlib import Path

import install_docker as idk
import pytest

VERSIONS = dict(
    docker_ce_version="5:27.3.1-1~ubuntu.24.04~noble",
    docker_ce_cli_version="5:27.3.1-1~ubuntu.24.04~noble",
    containerd_version="1.7.22-1",
    compose_plugin_version="2.29.7-1~ubuntu.24.04~noble",
)

PACKAGE_NAMES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"]

FAKE_PGP_BLOCK = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    "not a real key, just needs the right header for the content check\n"
    "-----END PGP PUBLIC KEY BLOCK-----\n"
)


def _write_fake_key(cmd):
    out_path = Path(cmd[cmd.index("-o") + 1])
    out_path.write_text(FAKE_PGP_BLOCK)


def _configure_happy_path(fake_runner):
    fake_runner.on_call(["curl"], _write_fake_key)
    fake_runner.script(["dpkg", "--print-architecture"], stdout="amd64\n")


def test_install_command_pins_all_four_package_versions(fake_runner, fake_os_release, tmp_path):
    _configure_happy_path(fake_runner)
    os_release = fake_os_release("noble")

    idk.install(
        **VERSIONS,
        os_release_path=os_release,
        dry_run=False,
        run=fake_runner,
        sources_list_path=tmp_path / "docker.list",
    )

    install_calls = fake_runner.calls_with(["apt-get", "install"])
    assert len(install_calls) == 1
    tokens = install_calls[0]

    version_tokens = [t for t in tokens if "=" in t]
    assert len(version_tokens) == 4

    expected = {
        f"docker-ce={VERSIONS['docker_ce_version']}",
        f"docker-ce-cli={VERSIONS['docker_ce_cli_version']}",
        f"containerd.io={VERSIONS['containerd_version']}",
        f"docker-compose-plugin={VERSIONS['compose_plugin_version']}",
    }
    assert set(version_tokens) == expected
    for pkg in PACKAGE_NAMES:
        assert pkg not in tokens, f"{pkg} appears without its version suffix"


def test_apt_key_add_is_never_invoked(fake_runner, fake_os_release, tmp_path):
    _configure_happy_path(fake_runner)
    os_release = fake_os_release("noble")

    idk.install(
        **VERSIONS,
        os_release_path=os_release,
        dry_run=False,
        run=fake_runner,
        sources_list_path=tmp_path / "docker.list",
    )

    assert not any(call and call[0] == "apt-key" for call in fake_runner.calls)


def test_gpg_key_is_fetched_to_a_temp_file_before_dearmoring(tmp_path, fake_runner):
    fake_runner.on_call(["curl"], _write_fake_key)

    idk.fetch_and_install_gpg_key(tmp_path, run=fake_runner)

    curl_calls = fake_runner.calls_with(["curl"])
    dearmor_calls = fake_runner.calls_with(["gpg", "--dearmor"])
    assert len(curl_calls) == 1
    assert len(dearmor_calls) == 1

    curl_index = next(i for i, c in enumerate(fake_runner.calls) if c[0] == "curl")
    dearmor_index = next(i for i, c in enumerate(fake_runner.calls) if c[:2] == ["gpg", "--dearmor"])
    assert curl_index < dearmor_index

    fetch_output_path = curl_calls[0][curl_calls[0].index("-o") + 1]
    assert fetch_output_path.startswith(str(tmp_path))
    assert dearmor_calls[0][-1] == fetch_output_path
    assert str(idk.KEYRING_PATH) in dearmor_calls[0]


def test_key_content_is_validated_before_dearmoring(tmp_path, fake_runner):
    def write_bad_key(cmd):
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text("not a real key\n")

    fake_runner.on_call(["curl"], write_bad_key)

    with pytest.raises(idk.GpgKeyValidationError):
        idk.fetch_and_install_gpg_key(tmp_path, run=fake_runner)

    assert not fake_runner.calls_with(["gpg", "--dearmor"])


def test_repo_codename_comes_from_os_release_fixture_not_a_literal(fake_os_release):
    os_release = fake_os_release("jammy")

    codename = idk.read_version_codename(os_release)
    content = idk.build_sources_list_content(codename, "amd64")

    assert "jammy" in content
    assert "noble" not in content


def test_apt_mark_hold_runs_after_install_for_all_four_packages(fake_runner, fake_os_release, tmp_path):
    _configure_happy_path(fake_runner)
    os_release = fake_os_release("noble")

    idk.install(
        **VERSIONS,
        os_release_path=os_release,
        dry_run=False,
        run=fake_runner,
        sources_list_path=tmp_path / "docker.list",
    )

    install_index = next(
        i for i, c in enumerate(fake_runner.calls) if c[:2] == ["apt-get", "install"]
    )
    hold_index = next(
        i for i, c in enumerate(fake_runner.calls) if c[:2] == ["apt-mark", "hold"]
    )
    assert install_index < hold_index

    hold_cmd = fake_runner.calls[hold_index]
    for pkg in PACKAGE_NAMES:
        assert pkg in hold_cmd


def test_dry_run_records_no_apt_or_systemctl_mutating_commands(fake_runner, fake_os_release):
    os_release = fake_os_release("noble")
    fake_runner.script(["dpkg", "--print-architecture"], stdout="amd64\n")

    exit_code = idk.install(**VERSIONS, os_release_path=os_release, dry_run=True, run=fake_runner)

    assert exit_code == 0
    mutating_prefixes = {"apt-get", "apt-mark", "gpg", "curl", "systemctl", "apt-key", "install"}
    for call in fake_runner.calls:
        assert call[0] not in mutating_prefixes, f"dry-run issued mutating command: {call}"
