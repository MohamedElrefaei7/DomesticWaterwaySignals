"""Unit tier — preflight gate logic. No Docker, no database, no network, no /mnt.

Covers CLAUDE.md § 13: the placeholder digest is its own failure, tag@digest is required, secrets
written twice are compared to each other, mount checks compare st_dev, secrets are never printed,
and a SKIP exits non-zero.

The gates' EFFECTS need an instance; their LOGIC does not, because every parse and comparison
function takes its inputs as arguments rather than reading the world. That split is the only
reason this file can exist at all.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify import preflight  # noqa: E402

TAG = "timescale/timescaledb:2.26.2-pg16"
GOOD_DIGEST = "sha256:" + "3f" * 32
OTHER_DIGEST = "sha256:" + "a1" * 32
PLACEHOLDER = "sha256:" + "0" * 64

# Two valid-but-different secrets. Never real; both are 64 hex characters so they pass every
# shape check, which is the whole point of test 4.
SECRET_A = "a" * 64
SECRET_B = "b" * 64


def _url(password):
    return f"postgresql://waterway:{password}@localhost:5432/waterway"


# ---------------------------------------------------------------------------------------------
# 1-3: the image reference
# ---------------------------------------------------------------------------------------------


def test_all_zero_digest_is_reported_as_the_placeholder():
    """The placeholder gets its OWN message, distinct from a malformed digest.

    `0000...0000` is 64 valid hex characters. It satisfies every shape check there is, so a
    generic "bad digest" message would send the operator to check their typing when the actual
    fix is to resolve and write a real one. Different cause, different remedy, different message.
    """
    result = preflight.check_image_reference(f"{TAG}@{PLACEHOLDER}")

    assert result.status == preflight.FAIL
    assert "PLACEHOLDER" in result.detail
    assert "--write-digest" in result.detail
    # The observed value is reported, per CLAUDE.md § 13.
    assert PLACEHOLDER in result.detail

    # And it must NOT be described as malformed - it is well formed and wrong.
    assert "malformed" not in result.detail.lower()

    malformed = preflight.check_image_reference(f"{TAG}@sha256:abc123")
    assert malformed.status == preflight.FAIL
    assert "malformed" in malformed.detail.lower()
    assert "PLACEHOLDER" not in malformed.detail, (
        "a malformed digest was reported as the placeholder - the two failures are conflated"
    )


def test_malformed_digest_is_rejected():
    """Wrong length, non-hex, and a missing `sha256:` prefix are all rejected."""
    too_short = preflight.check_image_reference(f"{TAG}@sha256:{'a' * 63}")
    too_long = preflight.check_image_reference(f"{TAG}@sha256:{'a' * 65}")
    non_hex = preflight.check_image_reference(f"{TAG}@sha256:{'g' * 64}")
    no_prefix = preflight.check_image_reference(f"{TAG}@{'a' * 64}")
    uppercase = preflight.check_image_reference(f"{TAG}@sha256:{'A' * 64}")

    for result in (too_short, too_long, non_hex, no_prefix, uppercase):
        assert result.status == preflight.FAIL, f"accepted: {result.detail}"
        assert "observed:" in result.detail

    # The good one passes, or every assertion above holds for the wrong reason.
    good = preflight.check_image_reference(f"{TAG}@{GOOD_DIGEST}")
    assert good.status == preflight.PASS
    assert GOOD_DIGEST in good.detail


def test_image_line_without_a_tag_is_rejected():
    """A bare `name@sha256:...` fails, and the message says the TAG is what is missing.

    The digest is the pin; the tag is how the digest is re-derivable. Without it nobody can work
    out what to `docker pull` to recover or re-verify the pin - which is exactly how this line
    failed the first time it was attempted.
    """
    result = preflight.check_image_reference(f"timescale/timescaledb@{GOOD_DIGEST}")

    assert result.status == preflight.FAIL
    assert "tag" in result.detail.lower()
    assert "NO TAG" in result.detail
    assert GOOD_DIGEST in result.detail

    # A registry host with a port must not be mistaken for a tag.
    parsed = preflight.parse_image_reference(f"registry.example:5000/img@{GOOD_DIGEST}")
    assert parsed.tag is None
    assert parsed.name == "registry.example:5000/img"

    tagged = preflight.parse_image_reference(f"registry.example:5000/img:1.0@{GOOD_DIGEST}")
    assert tagged.tag == "1.0"
    assert tagged.name == "registry.example:5000/img"
    assert tagged.digest == GOOD_DIGEST


def test_the_repos_own_compose_file_passes_the_image_gate():
    """Not a hypothetical: the file actually in this repo must satisfy the gate it ships with."""
    reference = preflight.read_image_reference(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    parsed = preflight.parse_image_reference(reference)

    assert parsed.tag is not None, "docker-compose.yml has no tag on its image reference"
    assert parsed.digest is not None, "docker-compose.yml is not pinned by digest"
    assert preflight.check_image_reference(reference).status == preflight.PASS


def test_rewriting_the_digest_preserves_the_tag_and_the_comments():
    """--write-digest must not turn the file into a digest-only reference, or reformat it."""
    original = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    updated = preflight.rewrite_image_digest(original, OTHER_DIGEST)

    parsed = preflight.parse_image_reference(preflight.read_image_reference(updated))
    assert parsed.digest == OTHER_DIGEST
    assert parsed.tag is not None, "the rewrite dropped the tag"

    # Comments survive - this is why the rewrite is a regex and not a YAML round-trip.
    assert updated.count("#") == original.count("#")
    assert len(updated.splitlines()) == len(original.splitlines())

    with pytest.raises(ValueError, match="no tag"):
        preflight.rewrite_image_digest(f"    image: name@{GOOD_DIGEST}\n", OTHER_DIGEST)


# ---------------------------------------------------------------------------------------------
# 4-7: the .env secrets
# ---------------------------------------------------------------------------------------------


def test_matching_passwords_pass_and_differing_passwords_fail():
    """Two valid-but-DIFFERENT 64-hex values must fail.

    This is the case independent validation cannot see. Both satisfy every rule about shape and
    alphabet; they are simply not the same secret. The container would initialize with one and
    the application authenticate with the other, and the error would surface much later as an
    auth failure pointing at nothing.
    """
    agreeing = preflight.check_password_agreement(SECRET_A, _url(SECRET_A))
    assert agreeing.status == preflight.PASS

    differing = preflight.check_password_agreement(SECRET_A, _url(SECRET_B))
    assert differing.status == preflight.FAIL, (
        "two valid but different secrets were accepted - the check validates each independently "
        "instead of comparing them to each other"
    )
    assert "DIFFERENT" in differing.detail


def test_placeholder_password_is_rejected():
    """CHANGEME in either field, since .env.example seeds both."""
    placeholder = "CHANGEME-run-openssl-rand-hex-32-and-paste-the-output-here"

    in_postgres = preflight.check_password_agreement(placeholder, _url(SECRET_A))
    in_url = preflight.check_password_agreement(SECRET_A, _url(placeholder))
    in_both = preflight.check_password_agreement(placeholder, _url(placeholder))

    for result in (in_postgres, in_url, in_both):
        assert result.status == preflight.FAIL
        assert "CHANGEME" in result.detail

    # in_both is the interesting one: the two AGREE, so a pure equality check would pass it.
    assert in_both.status == preflight.FAIL, (
        "two identical CHANGEME placeholders passed - equality alone is not sufficient"
    )


def test_non_hex_password_is_rejected():
    """A value containing `/` or `+` - i.e. produced by `openssl rand -base64`.

    CLAUDE.md § 5: those characters are meaningful inside a URI and break DATABASE_URL parsing as
    a confusing host-and-port error rather than as an auth failure. Asserting the hex alphabet is
    that rule made checkable, and it fails here instead of several layers downstream.
    """
    base64ish = "ab/cd+ef" + "0" * 56
    assert len(base64ish) == 64

    result = preflight.check_password_agreement(base64ish, _url(base64ish))

    assert result.status == preflight.FAIL, (
        "a password containing / and + was accepted - it would break DATABASE_URL parsing"
    )
    assert "0-9a-f" in result.detail
    assert "rand -hex" in result.detail

    # Equal, valid length, and still rejected: the alphabet is what fails it.
    assert base64ish == base64ish


def test_password_values_never_appear_in_output():
    """Capture the failing comparison's report and assert neither secret is in it.

    Decision 9 wants the observed value on failure; decision 4 forbids printing this particular
    one. What gets reported is each value's shape - length and how many characters fall outside
    the alphabet - which is enough to act on and not enough to leak.
    """
    failures = [
        preflight.check_password_agreement(SECRET_A, _url(SECRET_B)),
        preflight.check_password_agreement("f" * 64, _url("e" * 64)),
        preflight.check_password_agreement("ab/cd+ef" + "0" * 56, _url("ab/cd+ef" + "0" * 56)),
    ]
    assert len(failures) == 3
    assert all(result.status == preflight.FAIL for result in failures)

    for result in failures:
        rendered = result.render()
        for secret in (SECRET_A, SECRET_B, "f" * 64, "e" * 64, "ab/cd+ef" + "0" * 56):
            assert secret not in rendered, f"a secret value leaked into the output: {rendered}"

    # A passing comparison must not print it either.
    passing = preflight.check_password_agreement(SECRET_A, _url(SECRET_A))
    assert SECRET_A not in passing.render()


def test_env_permissions_gate():
    """.env holds the database password and must not be group- or world-readable."""
    assert preflight.check_env_permissions(0o100600).status == preflight.PASS
    assert preflight.check_env_permissions(0o100644).status == preflight.FAIL
    assert preflight.check_env_permissions(0o100640).status == preflight.FAIL
    assert "0644" in preflight.check_env_permissions(0o100644).detail


# ---------------------------------------------------------------------------------------------
# 8-9: the mount, and SKIP semantics
# ---------------------------------------------------------------------------------------------


def test_same_device_for_mount_and_root_fails():
    """Equal st_dev means the volume is not mounted, whatever `df` and `ls` say.

    fstab carries `nofail` by design (CLAUDE.md § 9), so booting without the volume is a
    supported outcome - which makes a directory-on-the-root-disk with the right name a real
    state the system can be in, not a hypothetical.
    """
    same = preflight.check_mount_device(2049, 2049)
    assert same.status == preflight.FAIL
    assert "IDENTICAL" in same.detail
    assert "2049" in same.detail, "the observed device IDs are not reported"

    differing = preflight.check_mount_device(2065, 2049)
    assert differing.status == preflight.PASS
    assert "2065" in differing.detail and "2049" in differing.detail


def test_data_bytes_gate_rejects_an_empty_directory():
    """Passing the device check is not enough: the directory must hold a real cluster."""
    empty = preflight.check_data_bytes(0, minimum_bytes=1024)
    assert empty.status == preflight.FAIL
    assert "0 bytes" in empty.detail

    populated = preflight.check_data_bytes(50 * 1024 * 1024, minimum_bytes=1024)
    assert populated.status == preflight.PASS


def test_migration_count_gate():
    """A database nobody ran the runner against looks healthy until the first query."""
    behind = preflight.check_migration_count(applied=0, on_disk=3)
    assert behind.status == preflight.FAIL
    assert "0" in behind.detail and "3" in behind.detail

    assert preflight.check_migration_count(applied=3, on_disk=3).status == preflight.PASS


def test_skip_causes_a_nonzero_exit():
    """A run containing any SKIP exits non-zero.

    The single most important line in preflight.py. A skipped check that exits zero reads as
    green in every log and in the memory of whoever ran it, and the thing it was meant to check
    has not been checked - CLAUDE.md § 2's theme 2 in its purest form.
    """
    all_pass = [
        preflight.Result("a", preflight.PASS, "observed: fine"),
        preflight.Result("b", preflight.PASS, "observed: fine"),
    ]
    assert preflight.exit_code(all_pass) == 0

    with_skip = all_pass + [preflight.Result("c", preflight.SKIP, "precondition absent")]
    assert preflight.exit_code(with_skip) != 0, (
        "a run containing a SKIP exited zero - a check that did not run reported as green"
    )

    with_fail = all_pass + [preflight.Result("d", preflight.FAIL, "observed: broken")]
    assert preflight.exit_code(with_fail) != 0

    assert preflight.exit_code([]) == 0


def test_every_failure_reports_an_observed_value():
    """CLAUDE.md § 13: no check prints a bare FAIL.

    Asserted across every failure this module can produce, so a new gate added later without an
    observed value fails here rather than at 3am on the instance.
    """
    failures = [
        preflight.check_image_reference(f"{TAG}@{PLACEHOLDER}"),
        preflight.check_image_reference(f"{TAG}@sha256:nope"),
        preflight.check_image_reference(f"timescale/timescaledb@{GOOD_DIGEST}"),
        preflight.check_image_reference("timescale/timescaledb:2.26.2-pg16"),
        preflight.check_password_agreement(SECRET_A, _url(SECRET_B)),
        preflight.check_env_permissions(0o100644),
        preflight.check_mount_device(2049, 2049),
        preflight.check_data_bytes(0),
        preflight.check_migration_count(1, 3),
    ]
    assert len(failures) == 9
    assert all(result.status == preflight.FAIL for result in failures)

    for result in failures:
        assert "observed:" in result.detail, f"{result.name} reports no observed value"
        assert len(result.detail) > 40, f"{result.name} has no actionable detail"


def test_resolve_digest_hard_fails_rather_than_guessing():
    """Three refusals, none of which may be papered over with a plausible default."""

    class FakeCompleted:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    # Tag not present locally: inspect does not pull.
    def not_present(cmd, **kwargs):
        return FakeCompleted(1, "", "Error: No such image: timescale/timescaledb:2.26.2-pg16")

    with pytest.raises(RuntimeError, match="docker pull"):
        preflight.resolve_digest(TAG, run=not_present)

    # Locally built, never pushed: no repository digest exists to pin to.
    def no_repo_digests(cmd, **kwargs):
        return FakeCompleted(0, "[]")

    with pytest.raises(RuntimeError, match="no RepoDigests"):
        preflight.resolve_digest(TAG, run=no_repo_digests)

    def null_repo_digests(cmd, **kwargs):
        return FakeCompleted(0, "null")

    with pytest.raises(RuntimeError, match="no RepoDigests"):
        preflight.resolve_digest(TAG, run=null_repo_digests)

    # Ambiguous.
    def two_digests(cmd, **kwargs):
        return FakeCompleted(
            0, f'["repo/a@{GOOD_DIGEST}", "repo/b@{OTHER_DIGEST}"]'
        )

    with pytest.raises(RuntimeError, match="distinct digests"):
        preflight.resolve_digest(TAG, run=two_digests)

    # The happy path, so the refusals above are not the only behaviour exercised.
    def one_digest(cmd, **kwargs):
        assert cmd[:3] == ["docker", "image", "inspect"]
        return FakeCompleted(0, f'["timescale/timescaledb@{GOOD_DIGEST}"]')

    assert preflight.resolve_digest(TAG, run=one_digest) == GOOD_DIGEST
