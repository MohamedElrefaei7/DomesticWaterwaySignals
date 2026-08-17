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


def test_a_tag_without_a_digest_is_a_failure():
    """`name:tag` with no `@sha256:...` is a FLOATING TAG and must not pass.

    It is the one malformation that looks completely ordinary — it is what every Compose file on
    the internet contains — so the temptation is to treat "has a tag" as good enough. `latest` on a
    database image resolved to two different TimescaleDB versions three months apart on the prior
    project (CLAUDE.md § 5), and CLAUDE.md § 22 requires `tag@digest` on every image in this stack.
    """
    for floating in (
        "timescale/timescaledb:2.26.2-pg16",
        "python:3.12-slim",
        "caddy:2-alpine",
        "caddy:latest",
    ):
        result = preflight.check_image_reference(floating)
        assert result.status == preflight.FAIL, (
            f"{floating!r} was accepted with no digest - a floating tag passed the pin gate"
        )
        assert "no `@sha256:...` digest" in result.detail
        assert floating in result.detail, "the observed reference is not reported"

    # The same reference WITH a digest passes, or the assertions above hold for the wrong reason.
    assert (
        preflight.check_image_reference(f"caddy:2-alpine@{GOOD_DIGEST}").status == preflight.PASS
    )


def test_gate_one_enumerates_every_image_reference_in_the_stack():
    """EVERY `image:` line in the compose file, not the first one.

    The version this replaces read `IMAGE_LINE_RE.search(...)` — the first match — and with four
    services it checked one reference out of five while the summary line reported the stack as
    verified. Which one it checked was decided by file order, so reordering the services silently
    re-pointed the only live digest check at a different image.

    Asserted against the repo's own compose file rather than a fixture, because the number that
    matters is how many references this stack actually has.
    """
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    sites = preflight.compose_image_sites(compose_text)

    expected = [
        line.split("image:", 1)[1].strip()
        for line in compose_text.splitlines()
        if line.strip().startswith("image:")
    ]
    assert len(expected) >= 2, (
        "the compose file has fewer than two `image:` lines, so this test cannot tell an "
        "enumerating gate from a first-match one"
    )
    assert [site.reference for site in sites] == expected, (
        f"the walk found {[s.reference for s in sites]}, expected {expected}. A gate that reads "
        f"only the first reference reports the whole stack as verified while checking one image."
    )

    # Every one of them is line-located, so a failure names where to look.
    for site in sites:
        assert site.line_number >= 1
        assert compose_text.splitlines()[site.line_number - 1].strip().endswith(site.reference)

    # And the whole-stack walk carries them plus the Dockerfiles' bases.
    enumeration = preflight.enumerate_image_sites()
    assert set(expected) <= {site.reference for site in enumeration.sites}
    assert preflight.check_enumeration(enumeration).status == preflight.PASS


def test_gate_one_covers_every_dockerfile_from_line():
    """Half the stack's pins live in `FROM` lines, and the gate reads none of them until it does.

    Two of the four services build rather than pull, so a check over compose `image:` keys alone
    reports a fully pinned stack while the api and the frontend build on whatever
    `python:3.12-slim` resolved to that morning (CLAUDE.md § 22).

    The walk is asserted to have found the Dockerfiles at all: a scanner that resolves no files
    passes every assertion written against what it found, which is the vacuous-pass failure this
    project has shipped twice.
    """
    paths = preflight.dockerfile_paths()
    assert len(paths) >= 2, (
        f"found {[p.name for p in paths]} - expected at least Dockerfile.api and "
        f"Dockerfile.frontend. A walk over no Dockerfiles checks no FROM lines and reports green."
    )

    enumeration = preflight.enumerate_image_sites()
    by_file = {}
    for site in enumeration.sites:
        by_file.setdefault(site.path.name, []).append(site.reference)

    for path in paths:
        expected = [
            line.split()[1]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().upper().startswith("FROM ")
        ]
        assert by_file.get(path.name) == expected, (
            f"{path.name}: the walk found {by_file.get(path.name)}, expected {expected}"
        )
        assert expected, f"{path.name} declares no FROM line"

    # A FROM naming an earlier STAGE is not an image and is reported rather than dropped.
    sites, stage_references = preflight.dockerfile_from_sites(
        f"FROM python:3.12-slim@{GOOD_DIGEST} AS build\nFROM build AS runtime\n",
        REPO_ROOT / "Dockerfile.fake",
    )
    assert [site.reference for site in sites] == [f"python:3.12-slim@{GOOD_DIGEST}"]
    assert stage_references == ["Dockerfile.fake:2 FROM build"]


def test_an_enumeration_that_walked_nothing_is_a_failure(tmp_path):
    """A gate over a collection must prove it resolved the collection.

    An empty repo root has no compose file and no Dockerfiles, so every per-reference check below
    would pass over an empty set and the run would exit zero. That is CLAUDE.md § 2's theme 2, and
    it is the same shape as the ingress test that passed because the set it constrained was empty.
    """
    empty = preflight.enumerate_image_sites(repo_root=tmp_path)
    result = preflight.check_enumeration(empty)
    assert result.status == preflight.FAIL, (
        "a walk that found no files reported PASS - the gate is green over an empty set"
    )
    assert "observed:" in result.detail

    # A compose file with references but no Dockerfiles is still a failure: two services build.
    (tmp_path / "docker-compose.yml").write_text(
        f"services:\n  db:\n    image: postgres:16@{GOOD_DIGEST}\n", encoding="utf-8"
    )
    no_dockerfiles = preflight.check_enumeration(preflight.enumerate_image_sites(repo_root=tmp_path))
    assert no_dockerfiles.status == preflight.FAIL
    assert "Dockerfile" in no_dockerfiles.detail


def test_the_repos_own_files_pass_the_image_gate():
    """Not a hypothetical: the files actually in this repo must satisfy the gate they ship with.

    Every reference, in every file — so a placeholder left unresolved in one Dockerfile stage fails
    here rather than at `docker build` on the instance.
    """
    results = preflight.gate_images()
    assert len(results) >= 6, (
        f"gate_images produced {len(results)} result(s): one enumeration report plus one per "
        f"reference, and this stack has five references"
    )

    failures = [result for result in results if result.status != preflight.PASS]
    assert failures == [], "\n\n".join(result.render() for result in failures)


def test_rewriting_the_digest_preserves_the_tag_and_the_comments():
    """--write-digest must not turn a file into a digest-only reference, or reformat it."""
    original = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    sites = preflight.compose_image_sites(original)

    first = sites[0]
    updated = preflight.rewrite_reference_lines(
        original,
        [(first.line_number, first.reference, preflight.resolved_reference(first.reference, OTHER_DIGEST))],
    )

    parsed = preflight.parse_image_reference(preflight.compose_image_sites(updated)[0].reference)
    assert parsed.digest == OTHER_DIGEST
    assert parsed.tag is not None, "the rewrite dropped the tag"

    # Comments survive - this is why the rewrite is a regex and not a YAML round-trip.
    assert updated.count("#") == original.count("#")
    assert len(updated.splitlines()) == len(original.splitlines())

    with pytest.raises(ValueError, match="no tag"):
        preflight.resolved_reference(f"name@{GOOD_DIGEST}", OTHER_DIGEST)

    # A line that does not carry the reference it was told to swap is a refusal, not a silent
    # no-op: a rewrite that "succeeded" without changing anything is how a stale digest survives.
    with pytest.raises(ValueError, match="does not contain"):
        preflight.rewrite_reference_lines("a\nb\n", [(2, "nothing-here", "x")])


def test_write_digest_rewrites_every_reference_not_just_the_first(tmp_path):
    """--write-digest writes EVERY reference, in every file, or the rest stay hand-edited.

    Hand-editing a 64-character content hash is the failure this command exists to remove, and the
    version that wrote only the first `image:` line had already produced a hand-edited Caddy digest
    by the time the gate was widened.

    Driven through `_digest_command` — the real CLI path — against a throwaway tree and a fake
    Docker daemon, so what is exercised is the command rather than a re-implementation of it in the
    test. Different tags resolve to DIFFERENT digests here, so a command that resolved one tag and
    wrote it everywhere would be caught rather than flattered.
    """
    digest_by_tag = {
        "postgres:16": "sha256:" + "11" * 32,
        "caddy:2-alpine": "sha256:" + "22" * 32,
        "python:3.12-slim": "sha256:" + "33" * 32,
    }

    class Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["docker", "image", "inspect"]:
            tag = cmd[3]
            return Completed(0, f'["{tag}@{digest_by_tag[tag]}"]')
        return Completed(0)  # the `git diff` at the end

    (tmp_path / "docker-compose.yml").write_text(
        f"services:\n"
        f"  db:\n"
        f"    # a comment that must survive\n"
        f"    image: postgres:16@{PLACEHOLDER}\n"
        f"  proxy:\n"
        f"    image: caddy:2-alpine@{PLACEHOLDER}\n",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile.api").write_text(
        f"FROM python:3.12-slim@{PLACEHOLDER} AS build\n"
        f"FROM python:3.12-slim@{PLACEHOLDER} AS runtime\n",
        encoding="utf-8",
    )

    before = preflight.enumerate_image_sites(repo_root=tmp_path)
    assert len(before.sites) == 4, (
        f"the scratch tree should offer four references, found {[s.label for s in before.sites]}"
    )

    assert preflight._digest_command(write=True, run=fake_run, repo_root=tmp_path) == 0

    after = preflight.enumerate_image_sites(repo_root=tmp_path)
    assert len(after.sites) == 4

    for site in after.sites:
        assert PLACEHOLDER not in site.reference, (
            f"{site.label} still carries the placeholder - the rewrite reached only some of the "
            f"references, and the rest are left to be hand-edited"
        )
        assert preflight.check_image_reference(site.reference).status == preflight.PASS

        parsed = preflight.parse_image_reference(site.reference)
        assert parsed.digest == digest_by_tag[f"{parsed.name}:{parsed.tag}"], (
            f"{site.label} carries a digest belonging to some other tag"
        )

    # Comments and line count survive the multi-file rewrite.
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    assert "# a comment that must survive" in compose
    assert len(compose.splitlines()) == 6

    # Re-running is a no-op that exits zero, rather than a second rewrite.
    assert preflight._digest_command(write=True, run=fake_run, repo_root=tmp_path) == 0

    # And --resolve-digest exits NON-zero while a file still disagrees with the daemon, because
    # work remains. Exercised on a tree where one reference is stale.
    (tmp_path / "Dockerfile.api").write_text(
        f"FROM python:3.12-slim@{PLACEHOLDER} AS build\n"
        f"FROM python:3.12-slim@{digest_by_tag['python:3.12-slim']} AS runtime\n",
        encoding="utf-8",
    )
    assert preflight._digest_command(write=False, run=fake_run, repo_root=tmp_path) == 1


# ---------------------------------------------------------------------------------------------
# 3b: gate 1's four remaining conditions - interpolation, scratch, digest-only, and drift
# ---------------------------------------------------------------------------------------------
#
# Gate 1 already enumerates every reference (d9acd96). These cover what the enumeration did not:
# a reference that cannot be resolved at all, one that is not a registry reference, one with no
# tag left to resolve FROM, and a pin whose tag has moved underneath it.


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _daemon(digest_by_tag):
    """A fake `docker image inspect` that resolves each tag to a stated digest."""

    def run(cmd, **kwargs):
        if cmd[:3] == ["docker", "image", "inspect"]:
            tag = cmd[3]
            return _Completed(0, f'["{tag}@{digest_by_tag[tag]}"]')
        return _Completed(0)  # the `git diff` at the end

    return run


def _tree(tmp_path, compose_refs=(), dockerfile_lines=()):
    """A throwaway repo root: a compose file plus one Dockerfile, so the walk finds both."""
    services = "".join(
        f"  svc{i}:\n    image: {reference}\n" for i, reference in enumerate(compose_refs)
    )
    (tmp_path / "docker-compose.yml").write_text(f"services:\n{services}", encoding="utf-8")
    (tmp_path / "Dockerfile.api").write_text(
        "".join(f"{line}\n" for line in dockerfile_lines), encoding="utf-8"
    )
    return tmp_path


def test_gate1_fails_on_arg_interpolated_from():
    """`FROM ${BASE_IMAGE}` fails as INTERPOLATION, not as a missing digest.

    Both diagnoses produce a red gate, so the distinction looks cosmetic. It is not. A message
    about a missing digest sends the reader looking for a digest to add, and the fix that suggests
    - `FROM ${BASE_IMAGE}@${BASE_DIGEST}` - satisfies a digest check and pins nothing at all. The
    message has to name the actual remedy, which is writing the base image literally.
    """
    result = preflight.check_image_reference("${BASE_IMAGE}", where="Dockerfile.api:1")

    assert result.status == preflight.FAIL
    assert "literal" in result.detail.lower(), (
        f"the message does not tell the reader to write the base image literally: {result.detail}"
    )
    assert "${BASE_IMAGE}" in result.detail, "the observed reference is not reported"

    # The WRONG diagnosis, asserted by its absence. This is what mutation 1 restores.
    assert "no `@sha256:...` digest" not in result.detail, (
        "an interpolated reference was diagnosed as a missing digest - the reader is sent to add "
        "a digest, and interpolating one too would pass the gate while pinning nothing"
    )

    # A digest bolted onto an interpolated name does not rescue it.
    interpolated_digest = preflight.check_image_reference(f"${{BASE_IMAGE}}@{GOOD_DIGEST}")
    assert interpolated_digest.status == preflight.FAIL
    assert "literal" in interpolated_digest.detail.lower()


def test_gate1_fails_on_bare_dollar_interpolated_from():
    """`FROM $BASE`, no braces. Both spellings are legal shell and legal Dockerfile."""
    for reference in ("$BASE", "$BASE_IMAGE:1.0", "registry.example/$NAME:tag"):
        result = preflight.check_image_reference(reference)
        assert result.status == preflight.FAIL, f"{reference!r} was accepted"
        assert "literal" in result.detail.lower(), (
            f"{reference!r} failed for some other reason: {result.detail}"
        )


def test_gate1_skips_from_scratch():
    """`FROM scratch` is Docker's empty base - no registry reference, nothing to pin.

    Skipped rather than failed, and REPORTED rather than dropped: a reference the walk declined to
    check has to be visible in the walk's own output, or an unmentioned omission is
    indistinguishable from a reference nobody enumerated.
    """
    sites, declined = preflight.dockerfile_from_sites(
        f"FROM {'scratch'}\nCOPY x /x\nFROM python:3.12-slim@{GOOD_DIGEST} AS run\n",
        REPO_ROOT / "Dockerfile.fake",
    )

    assert [site.reference for site in sites] == [f"python:3.12-slim@{GOOD_DIGEST}"], (
        f"`FROM scratch` was treated as a registry reference: {[s.reference for s in sites]}"
    )
    assert declined == ["Dockerfile.fake:1 FROM scratch"], (
        f"`FROM scratch` was dropped silently rather than reported: {declined}"
    )


def test_gate1_rejects_digest_without_tag(tmp_path):
    """A bare `name@sha256:...` is rejected, through the full enumerate-then-check path.

    This is what makes the drift check non-vacuous. `--write-digest` resolves a digest FROM a tag;
    a reference with no tag offers nothing to resolve, so the drift comparison silently applies to
    zero references - CLAUDE.md § 2's theme 2, inside the tool built to catch theme 2.
    """
    root = _tree(
        tmp_path,
        compose_refs=[f"caddy@{GOOD_DIGEST}"],
        dockerfile_lines=[f"FROM python:3.12-slim@{GOOD_DIGEST} AS build"],
    )
    enumeration = preflight.enumerate_image_sites(repo_root=root)
    assert preflight.check_enumeration(enumeration).status == preflight.PASS

    results = [
        preflight.check_image_reference(site.reference, where=site.label)
        for site in enumeration.sites
    ]
    failures = [result for result in results if result.status == preflight.FAIL]

    assert len(failures) == 1, f"expected exactly the untagged reference to fail, got {failures}"
    assert "NO TAG" in failures[0].detail
    assert GOOD_DIGEST in failures[0].detail, "the observed reference is not reported"


def test_gate1_accepts_tag_and_digest():
    """The positive case, or every rejection above could hold for the wrong reason."""
    for reference in (
        f"caddy:2-alpine@{GOOD_DIGEST}",
        f"timescale/timescaledb:2.26.2-pg16@{GOOD_DIGEST}",
        f"registry.example:5000/img:1.0@{GOOD_DIGEST}",
    ):
        result = preflight.check_image_reference(reference)
        assert result.status == preflight.PASS, f"{reference!r} was rejected: {result.detail}"


def test_write_digest_writes_unpinned_reference(tmp_path):
    """Case one of three: no digest, or the placeholder. Both are WRITES.

    The placeholder is the committed marker for "not resolved yet" (CLAUDE.md § 12) and writing it
    is the whole reason this command exists - four were replaced in Phase 10. If it were
    classified as drift it would become the one thing --write-digest refuses to write, sending the
    operator back to the hand-editing this removes.
    """
    resolved = "sha256:" + "44" * 32
    root = _tree(
        tmp_path,
        compose_refs=["caddy:2-alpine", f"postgres:16@{preflight.PLACEHOLDER_DIGEST}"],
        dockerfile_lines=[f"FROM python:3.12-slim@{preflight.PLACEHOLDER_DIGEST} AS build"],
    )
    run = _daemon(
        {"caddy:2-alpine": resolved, "postgres:16": resolved, "python:3.12-slim": resolved}
    )

    assert preflight._digest_command(write=True, run=run, repo_root=root) == 0

    for site in preflight.enumerate_image_sites(repo_root=root).sites:
        assert preflight.parse_image_reference(site.reference).digest == resolved, (
            f"{site.label} was not written: {site.reference}"
        )
        assert preflight.check_image_reference(site.reference).status == preflight.PASS


def test_write_digest_noop_when_pin_resolves_identically(tmp_path):
    """Case two of three: pinned, and the daemon agrees. The FILE BYTES must not change.

    Asserted on bytes rather than on the exit code, because a rewrite that produces identical
    content is still a rewrite - it rewrites mtime, and it is one formatting change away from
    reflowing a file this project keeps comments in.
    """
    root = _tree(
        tmp_path,
        compose_refs=[f"caddy:2-alpine@{GOOD_DIGEST}"],
        dockerfile_lines=[
            "# a comment that must survive",
            f"FROM python:3.12-slim@{GOOD_DIGEST} AS build",
        ],
    )
    before = {
        path: path.read_bytes() for path in (root / "docker-compose.yml", root / "Dockerfile.api")
    }
    run = _daemon({"caddy:2-alpine": GOOD_DIGEST, "python:3.12-slim": GOOD_DIGEST})

    assert preflight._digest_command(write=True, run=run, repo_root=root) == 0

    for path, original in before.items():
        assert path.read_bytes() == original, f"{path.name} was rewritten on a no-op run"


def test_write_digest_raises_when_pin_resolves_differently(tmp_path):
    """Case three of three: pinned, and the tag has MOVED. Raise; do not rewrite.

    A tag that resolves to a new digest is the event worth noticing - `latest` resolving to two
    TimescaleDB versions three months apart cost the prior project a full session (CLAUDE.md § 5).
    Rewriting it converts that event into a one-line diff nobody reads. Accepting the new image is
    a human decision, taken by deleting the old digest first.
    """
    moved = "sha256:" + "99" * 32
    root = _tree(
        tmp_path,
        compose_refs=[f"caddy:2-alpine@{GOOD_DIGEST}"],
        dockerfile_lines=[f"FROM python:3.12-slim@{GOOD_DIGEST} AS build"],
    )
    before = (root / "docker-compose.yml").read_bytes()

    with pytest.raises(preflight.DigestDriftError) as raised:
        preflight._digest_command(
            write=True,
            run=_daemon({"caddy:2-alpine": moved, "python:3.12-slim": GOOD_DIGEST}),
            repo_root=root,
        )

    message = str(raised.value)
    assert GOOD_DIGEST in message, "the message does not report the digest that was written down"
    assert moved in message, "the message does not report what the tag now resolves to"
    assert "docker-compose.yml" in message, "the message does not name the file"

    assert (root / "docker-compose.yml").read_bytes() == before, (
        "the drifting reference was rewritten anyway - the raise is decorative"
    )


def test_gate1_still_enumerates_all_references(tmp_path):
    """Regression guard on d9acd96: the walk covers every reference, not the first.

    A fixture rather than the repo's own files, so it fails on the logic rather than on somebody
    legitimately adding a service. Two multi-stage Dockerfiles plus a compose file, because that
    is the shape that made the old gate check one reference out of five while reporting the stack
    as pinned (CLAUDE.md § 22).
    """
    (tmp_path / "docker-compose.yml").write_text(
        f"services:\n"
        f"  db:\n    image: postgres:16@{GOOD_DIGEST}\n"
        f"  proxy:\n    image: caddy:2-alpine@{GOOD_DIGEST}\n",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile.api").write_text(
        f"FROM python:3.12-slim@{GOOD_DIGEST} AS build\n"
        f"FROM python:3.12-slim@{GOOD_DIGEST} AS runtime\n",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile.frontend").write_text(
        f"FROM node:22-bookworm-slim@{GOOD_DIGEST} AS build\n"
        f"FROM node:22-bookworm-slim@{GOOD_DIGEST} AS artifact\n",
        encoding="utf-8",
    )

    enumeration = preflight.enumerate_image_sites(repo_root=tmp_path)

    assert len(enumeration.sites) == 6, (
        f"the walk found {len(enumeration.sites)} of 6 references: "
        f"{[site.label for site in enumeration.sites]}"
    )
    assert len(enumeration.files_walked) == 3
    assert {site.path.name for site in enumeration.sites} == {
        "docker-compose.yml",
        "Dockerfile.api",
        "Dockerfile.frontend",
    }
    assert preflight.check_enumeration(enumeration).status == preflight.PASS


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


# ---------------------------------------------------------------------------------------------
# The all-zero digest, from both sides of the tool.
# ---------------------------------------------------------------------------------------------
#
# ONE VALUE, TWO MEANINGS, DEPENDING ON WHICH SIDE OF THE TOOL YOU ARE ON.
#
#   To gate 1        it is INVALID. It is 64 valid hex characters, so it passes every shape check
#                    there is; it is wrong by value rather than by form, and letting it through
#                    ships the "not resolved yet" marker to production.
#   To --write-digest it is UNPINNED. It is the committed marker chosen precisely because it cannot
#                    resolve, and writing over it is the entire reason the command exists.
#
# The asymmetry is the design. Treating it as pinned-and-differing would make the marker the one
# thing --write-digest refuses to write, sending the operator back to the hand-editing the command
# exists to remove - which is how two digests got hand-edited in Phase 10. Treating it as valid for
# gate 1 lets the marker ship.
#
# A TEST ON EACH SIDE, rather than one test of a shared helper, because there is no shared helper
# to test: the two sides reach opposite conclusions about the same value, so any single assertion
# about "what the placeholder means" would have to pick one and would be wrong about the other.


def test_gate1_rejects_all_zero_digest():
    """Gate 1 FAILS on the marker. This is about the verdict, not the wording.

    `test_all_zero_digest_is_reported_as_the_placeholder` above asserts the message is distinct
    from the malformed-digest message, which is a different property: it would still pass if both
    branches returned PASS with different text. This one is the verdict, isolated, so that the
    half of the asymmetry that keeps the marker out of production has a test naming it.
    """
    result = preflight.check_image_reference(f"{TAG}@{preflight.PLACEHOLDER_DIGEST}")

    assert result.status == preflight.FAIL, (
        f"gate 1 returned {result.status} for the all-zero placeholder digest. It is 64 valid hex "
        f"characters and passes every shape check, so nothing else in the gate will stop it - "
        f"this is the assertion that keeps the 'not resolved yet' marker out of production."
    )
    assert preflight.PLACEHOLDER_DIGEST in result.detail, (
        "the failure does not report the observed value (CLAUDE.md § 13)"
    )


def test_write_digest_overwrites_all_zero_marker_without_raising(tmp_path):
    """--write-digest treats the marker as UNPINNED and writes it. It must not raise.

    ISOLATED TO THE MARKER. `test_write_digest_writes_unpinned_reference` covers this case, but it
    covers it alongside a reference carrying no digest at all, and its name and docstring are about
    the unpinned case - so a later narrowing of that test to what it says it tests would take this
    with it silently. Here the ONLY thing wrong with any reference is the marker.

    The assertion that matters is the absence of DigestDriftError. If the marker were classified as
    drift, the command would refuse the one write it was built for, and the operator's next move is
    to edit the digest by hand.
    """
    resolved = "sha256:" + "77" * 32
    root = _tree(
        tmp_path,
        compose_refs=[f"caddy:2-alpine@{preflight.PLACEHOLDER_DIGEST}"],
        dockerfile_lines=[f"FROM python:3.12-slim@{preflight.PLACEHOLDER_DIGEST} AS build"],
    )
    run = _daemon({"caddy:2-alpine": resolved, "python:3.12-slim": resolved})

    try:
        exit_code = preflight._digest_command(write=True, run=run, repo_root=root)
    except preflight.DigestDriftError as exc:
        raise AssertionError(
            f"--write-digest raised DigestDriftError on the all-zero marker: {exc}\n"
            f"The marker is the committed 'not resolved yet' value, not a pin that moved. "
            f"Refusing to write it makes it the one thing this command cannot fix, which sends "
            f"the operator back to hand-editing digests - the failure --write-digest exists to "
            f"remove, and the one that produced two hand-edited digests in Phase 10."
        ) from exc

    assert exit_code == 0, f"--write-digest exited {exit_code} while replacing only markers"

    sites = preflight.enumerate_image_sites(repo_root=root).sites
    assert sites, "the enumeration found no image sites; this test would assert over nothing"
    for site in sites:
        assert preflight.parse_image_reference(site.reference).digest == resolved, (
            f"{site.label} still carries the marker after --write-digest: {site.reference}"
        )
        # And the written result is one gate 1 now accepts - the two sides agree afterwards.
        assert preflight.check_image_reference(site.reference).status == preflight.PASS
