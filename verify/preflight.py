"""Preflight gates: everything that must hold before any other verification means anything.

CLAUDE.md § 13 in executable form. Five gate groups — image pins, `.env` secret agreement,
data-volume identity, data-volume contents, and applied migrations — plus the digest-resolution
helper that exists because hand-editing a digest has now failed three times.

THE IMAGE GATE ENUMERATES THE COLLECTION IT IS NAMED FOR. It reads every `image:` line in
docker-compose.yml and every `FROM` line in every Dockerfile, and it reports each one by name. The
version that read only the first `image:` line checked one reference out of five while reporting
the stack as verified — a gate over a subset is worse than no gate, because the summary line makes
the unchecked references look checked. See the block above `enumerate_image_sites`.

TWO CONVENTIONS RUN THROUGH EVERY CHECK HERE, and they are the reason this file is longer than the
checks themselves would suggest:

  1. A failure reports the OBSERVED VALUE, never a bare FAIL. The digest it read, both device IDs,
     the byte count, the counts that disagreed. A harness that says FAIL without evidence sends
     the operator off to re-derive by hand what the harness already had in a variable.

  2. A SKIP IS NOT A PASS, and the run exits non-zero if anything skipped. A check that quietly
     turns into a no-op when its precondition is missing, and still reads green, is the exact
     failure this project is organised around (CLAUDE.md § 2, theme 2).

The parsing and comparison logic takes its inputs as arguments rather than reading the world, so
the logic is testable offline even though the effects are not. tests/verify/ never touches Docker,
a database, or the filesystem outside tmp_path.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
ENV_PATH = REPO_ROOT / ".env"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

DATA_DIR = Path("/mnt/data/timescaledb")
ROOT_DIR = Path("/")

# An initialized Postgres cluster is tens of megabytes before a single row is inserted. Ten is
# comfortably below that and comfortably above "an empty directory that happens to exist".
MINIMUM_DATA_BYTES = 10 * 1024 * 1024

# 64 valid hex characters. It passes every shape check there is, which is exactly why it needs to
# be checked for BY VALUE and reported with its own message (see check_image_reference).
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")

# `FROM ${BASE_IMAGE}` / `FROM $BASE`. Matches a `$` anywhere in the reference, braced or not,
# because either spelling makes the reference unresolvable at read time.
INTERPOLATION_RE = re.compile(r"\$")

# `FROM scratch` names Docker's empty base. It is a reserved pseudo-stage rather than a registry
# reference: there is nothing to pull and nothing to pin.
SCRATCH = "scratch"


class DigestDriftError(RuntimeError):
    """An already-pinned reference resolved to a digest other than the one written down.

    Raised rather than rewritten. A tag that resolves to a new digest IS the incident - it is
    `latest` resolving to two TimescaleDB versions wearing a different hat (CLAUDE.md § 5) - and
    silently rewriting converts the interesting event into a clean diff nobody reads. Accepting a
    new digest is a human decision, taken by deleting the old one first.
    """


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class Result:
    """One gate's outcome. `detail` carries the observed value, always."""

    name: str
    status: str
    detail: str

    def render(self) -> str:
        return f"[{self.status:4s}] {self.name}\n         {self.detail}"


def exit_code(results: list[Result]) -> int:
    """0 only when every gate passed.

    A SKIP counts as a failure. This is the single most important line in the file: a skipped
    check that exits zero reads as green in every log, dashboard, and memory of the person who
    ran it, and the thing it was supposed to check has not been checked.
    """
    return 0 if all(result.status == PASS for result in results) else 1


# ---------------------------------------------------------------------------------------------
# Image reference
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageReference:
    raw: str
    name: str
    tag: str | None
    digest: str | None


def parse_image_reference(reference: str) -> ImageReference:
    """Split `name:tag@sha256:...` into its parts, tolerating a registry host with a port.

    The port case is why this is not `reference.split(":")`: `registry.example:5000/img:1.0`
    has two colons and only the second one introduces a tag. A tag is present only when the last
    colon falls after the last slash.
    """
    raw = reference.strip()

    digest = None
    name_and_tag = raw
    if "@" in raw:
        name_and_tag, _, digest = raw.partition("@")

    tag = None
    name = name_and_tag
    last_colon = name_and_tag.rfind(":")
    if last_colon != -1 and last_colon > name_and_tag.rfind("/"):
        name = name_and_tag[:last_colon]
        tag = name_and_tag[last_colon + 1 :]

    return ImageReference(raw=raw, name=name, tag=tag, digest=digest)


def check_image_reference(reference: str, where: str | None = None) -> Result:
    """The image pin gate, for ONE reference. Four distinct failures, four distinct messages.

    The placeholder and the malformed-digest cases are separated deliberately. `0000...0000` is
    64 valid hex characters and satisfies any shape validation there is, so a single "bad digest"
    message would send the operator to check their typing when the actual fix is to run
    --write-digest. The two have different causes and different remedies.

    `where` names the file and line the reference was read from. With five references in the stack,
    a result that says only "image is pinned by tag@digest" does not say WHICH image, and an
    operator reading five near-identical lines has to re-derive by hand what the walk already had.
    """
    parsed = parse_image_reference(reference)
    site = f"{where} " if where else ""

    # BEFORE the digest check, deliberately. An interpolated reference has no digest either, so
    # falling through to the check below produces a TRUE failure with a WRONG diagnosis: it sends
    # the reader looking for a digest to add, and the fix that suggests - interpolating a digest
    # variable too - passes the gate and pins nothing.
    if INTERPOLATION_RE.search(parsed.raw):
        return Result(
            f"{site}image reference is written literally",
            FAIL,
            f"observed: {parsed.raw}\n"
            f"         the reference is INTERPOLATED. A base image assembled from a build "
            f"argument or environment variable cannot be statically verified as pinned - what it "
            f"resolves to depends on who invoked the build and with what. Write the base image "
            f"literally as name:tag@sha256:..., and do NOT satisfy this by interpolating the "
            f"digest as well: that passes this gate and pins nothing.",
        )

    if parsed.digest is None:
        return Result(
            f"{site}image is pinned by digest",
            FAIL,
            f"observed: {parsed.raw}\n"
            f"         no `@sha256:...` digest at all - this is a floating tag, and a floating tag "
            f"on a database image resolved to two different versions three months apart on the "
            f"prior project (CLAUDE.md § 5). Run: python3 -m verify.preflight --write-digest",
        )

    if parsed.tag is None:
        return Result(
            f"{site}image reference carries a tag",
            FAIL,
            f"observed: {parsed.raw}\n"
            f"         the reference has a digest but NO TAG. The digest is the pin; the tag is how "
            f"the digest is re-derivable. Without it, nobody can work out what to `docker pull` in "
            f"order to recover or re-verify this digest - which is how this line failed the first "
            f"time. Rewrite as {parsed.name}:<tag>@{parsed.digest}",
        )

    if parsed.digest == PLACEHOLDER_DIGEST:
        return Result(
            f"{site}image digest is not the placeholder",
            FAIL,
            f"observed: {parsed.digest}\n"
            f"         this is the all-zero PLACEHOLDER digest, not a resolved one. It is 64 valid "
            f"hex characters, so it passes every shape check - it is wrong by value, not by form. "
            f"Fix: docker pull {parsed.name}:{parsed.tag} "
            f"&& python3 -m verify.preflight --write-digest",
        )

    if not DIGEST_RE.match(parsed.digest):
        return Result(
            f"{site}image digest is well formed",
            FAIL,
            f"observed: {parsed.digest!r} (length {len(parsed.digest)})\n"
            f"         expected `sha256:` followed by exactly 64 lowercase hex characters. This is "
            f"a malformed value rather than an unreplaced placeholder - most likely a truncated or "
            f"hand-typed digest. Do not retype it: python3 -m verify.preflight --write-digest",
        )

    return Result(
        f"{site}image is pinned by tag@digest",
        PASS,
        f"{parsed.name}:{parsed.tag}\n         pinned at {parsed.digest}",
    )


# ---------------------------------------------------------------------------------------------
# Enumerating every image reference in the stack
# ---------------------------------------------------------------------------------------------
#
# THIS GATE USED TO READ THE FIRST `image:` LINE IN docker-compose.yml AND NOTHING ELSE.
#
# That was unambiguous when the stack was one service. Phase 10 took it to four services and two
# Dockerfiles - five references - and the gate went on checking one of them while the summary line
# said the stack was verified. A gate that checks a subset of what it names is worse than no gate,
# because it reports the whole set as verified: CLAUDE.md § 2's theme 2 arriving inside the tool
# that exists to catch theme 2. The Caddy digest was hand-edited in that window, which is the exact
# failure --write-digest was written to eliminate.
#
# So the collection is enumerated, and the enumeration is itself asserted: a walk that finds no
# Dockerfiles, or a compose file with no `image:` line, is a FAILURE rather than a clean run over
# an empty set (CLAUDE.md § 21 - a static assertion must prove it resolved the source tree first).
#
# Still a regex rather than a YAML parse, and still for the original reason: --write-digest rewrites
# these lines in place, and a round-trip through a YAML library would discard every comment in the
# file - including the blocks above the image lines that explain why the tags are there.

DOCKERFILE_GLOB = "Dockerfile*"

_IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<reference>\S+)\s*$")
_FROM_LINE_RE = re.compile(
    r"^\s*FROM\s+(?P<reference>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class ImageSite:
    """One place an image reference is written down: which file, which line, which reference.

    The line number is what makes the rewrite surgical and the failure message actionable. A
    message naming only the image tells an operator which pin is wrong; one naming the file and
    line tells them where to look, and there are now five places to look.
    """

    path: Path
    line_number: int  # 1-indexed, as an editor counts
    kind: str  # "compose image:" or "Dockerfile FROM"
    reference: str
    stage: str | None = None

    @property
    def label(self) -> str:
        stage = f" [{self.stage}]" if self.stage else ""
        return f"{self.path.name}:{self.line_number}{stage}"


def compose_image_sites(compose_text: str, path: Path = COMPOSE_PATH) -> list[ImageSite]:
    """Every `image:` line in the compose file, in file order.

    Every one, not the first: with four services, which reference the old gate checked was decided
    by file order, so reordering the services silently re-pointed the only live digest check at a
    different image.
    """
    sites = []
    for number, line in enumerate(compose_text.splitlines(), start=1):
        match = _IMAGE_LINE_RE.match(line)
        if match is not None:
            sites.append(
                ImageSite(path, number, "compose image:", match.group("reference"))
            )
    return sites


def dockerfile_from_sites(
    dockerfile_text: str, path: Path
) -> tuple[list[ImageSite], list[str]]:
    """(image references, intra-file stage references) from a Dockerfile's `FROM` lines.

    A `FROM` naming an earlier stage (`FROM build AS runtime`) is not an image and has no digest to
    pin. It is returned separately rather than dropped, because a reference this walk declined to
    check has to be visible in the walk's own report - an unmentioned omission is indistinguishable
    from a reference nobody enumerated.

    `FROM scratch` is declined the same way, by seeding the declared-stage set with it. Docker
    reserves the name for the empty base, so there is no registry reference behind it and nothing
    to pull. Handling it here, as a name rather than as a special case at the check, is what keeps
    the alternative off the table: a broad "skip references that fail to parse" clause at the gate
    would swallow real misses alongside it.
    """
    sites: list[ImageSite] = []
    stage_references: list[str] = []
    declared: set[str] = {SCRATCH}

    for number, line in enumerate(dockerfile_text.splitlines(), start=1):
        match = _FROM_LINE_RE.match(line)
        if match is None:
            continue
        reference = match.group("reference")
        stage = match.group("stage")

        if reference in declared:
            stage_references.append(f"{path.name}:{number} FROM {reference}")
        else:
            sites.append(ImageSite(path, number, "Dockerfile FROM", reference, stage))

        if stage:
            declared.add(stage)

    return sites, stage_references


def dockerfile_paths(repo_root: Path = REPO_ROOT) -> list[Path]:
    """Every Dockerfile in the repo root, sorted. Discovered, never listed.

    A hardcoded list is a second copy of the same fact, and the copy is what goes stale the day a
    third Dockerfile lands - which is exactly how a new build ends up on an unpinned base with a
    green gate above it.
    """
    return sorted(path for path in repo_root.glob(DOCKERFILE_GLOB) if path.is_file())


@dataclass(frozen=True)
class Enumeration:
    """What the walk found, including what it declined to check and what it could not read."""

    sites: list[ImageSite]
    files_walked: list[Path]
    stage_references: list[str]
    unreadable: list[str]


def enumerate_image_sites(
    repo_root: Path = REPO_ROOT, compose_path: Path | None = None
) -> Enumeration:
    """Every image reference the running stack resolves, across the compose file and every
    Dockerfile."""
    compose_path = compose_path or (repo_root / COMPOSE_PATH.name)

    sites: list[ImageSite] = []
    walked: list[Path] = []
    stage_references: list[str] = []
    unreadable: list[str] = []

    try:
        sites.extend(compose_image_sites(compose_path.read_text(encoding="utf-8"), compose_path))
        walked.append(compose_path)
    except OSError as exc:
        unreadable.append(f"{compose_path}: {exc}")

    for path in dockerfile_paths(repo_root):
        try:
            found, stages_named = dockerfile_from_sites(path.read_text(encoding="utf-8"), path)
        except OSError as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        sites.extend(found)
        stage_references.extend(stages_named)
        walked.append(path)

    return Enumeration(sites, walked, stage_references, unreadable)


def check_enumeration(enumeration: Enumeration) -> Result:
    """The walk's own report, and the guard against it having walked nothing.

    A gate over a collection that quietly walked an empty collection is green forever and watching
    nothing. This project has shipped that twice - an ingress test over an empty set, and a source
    scanner that would have passed against an empty directory.
    """
    name = "every image reference in the stack was enumerated"

    if enumeration.unreadable:
        return Result(
            name,
            FAIL,
            "observed: could not read " + "; ".join(enumeration.unreadable) + "\n"
            "         a file this gate is named for was not walked, so the references in it were "
            "not checked.",
        )

    dockerfiles = [path for path in enumeration.files_walked if path.name != COMPOSE_PATH.name]
    if not dockerfiles:
        return Result(
            name,
            FAIL,
            f"observed: 0 files matching {DOCKERFILE_GLOB!r} under {REPO_ROOT}\n"
            f"         two services build rather than pull, so half the stack's pins live in "
            f"`FROM` lines. A walk that finds no Dockerfiles has checked none of them and would "
            f"otherwise report the stack as fully pinned.",
        )

    if not enumeration.sites:
        return Result(
            name,
            FAIL,
            f"observed: 0 image references across {len(enumeration.files_walked)} file(s)\n"
            f"         every per-reference check below would pass vacuously over an empty set.",
        )

    per_file: dict[str, int] = {}
    for site in enumeration.sites:
        per_file[site.path.name] = per_file.get(site.path.name, 0) + 1
    breakdown = ", ".join(f"{name_}: {count}" for name_, count in sorted(per_file.items()))

    detail = f"{len(enumeration.sites)} reference(s) across {len(enumeration.files_walked)} file(s) - {breakdown}"
    if enumeration.stage_references:
        detail += (
            "\n         not checked (no registry reference to pin - intra-file stage names and "
            "`scratch`): " + ", ".join(enumeration.stage_references)
        )
    return Result(name, PASS, detail)


def rewrite_reference_lines(text: str, replacements: list[tuple[int, str, str]]) -> str:
    """Swap references on the given 1-indexed lines, preserving everything else on them.

    Line-scoped rather than a whole-file `replace`, because the same reference appears on both
    `FROM` lines of a multi-stage build and a file-wide replacement would depend on how many times
    it happened to occur. Indentation, the `AS <stage>` suffix, and every comment survive.
    """
    lines = text.splitlines(keepends=True)

    for number, old_reference, new_reference in replacements:
        index = number - 1
        if not 0 <= index < len(lines):
            raise ValueError(f"line {number} is outside a file of {len(lines)} lines")
        if old_reference not in lines[index]:
            raise ValueError(
                f"line {number} does not contain {old_reference!r}: {lines[index].rstrip()!r}"
            )
        lines[index] = lines[index].replace(old_reference, new_reference, 1)

    return "".join(lines)


def resolved_reference(reference: str, digest: str) -> str:
    """`name:tag@digest`, refusing to produce a digest-only reference."""
    parsed = parse_image_reference(reference)
    if parsed.tag is None:
        raise ValueError(
            f"refusing to rewrite {reference!r}: it has no tag, so the new digest would not be "
            f"re-derivable. Add a tag first."
        )
    return f"{parsed.name}:{parsed.tag}@{digest}"


def resolve_digest(tag: str, run=subprocess.run) -> str:
    """Ask the local Docker daemon for `tag`'s repository digest. Hard-fails; never guesses.

    Three refusals, all of which the tempting implementation would paper over:

      - the tag is not present locally. `docker image inspect` does not pull, and inventing a
        digest for an image nobody has is how the second attempt at this failed.
      - RepoDigests is empty. That happens for a locally-built image that was never pushed to a
        registry. It has no repository digest, so there is nothing to pin to, and treating it as
        pinned would produce a reference that works on exactly one machine.
      - more than one RepoDigests entry for a different repository - ambiguous, so stop.
    """
    completed = run(
        ["docker", "image", "inspect", tag, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"`docker image inspect {tag}` failed (exit {completed.returncode}).\n"
            f"{(completed.stderr or '').strip()}\n"
            f"The tag is probably not present locally - inspect does not pull. Run:\n"
            f"    docker pull {tag}"
        )

    raw = (completed.stdout or "").strip()
    repo_digests = _parse_repo_digests(raw)

    if not repo_digests:
        raise RuntimeError(
            f"`docker image inspect {tag}` returned no RepoDigests entry (got {raw!r}).\n"
            f"That means this image has no repository digest - it was built locally and never "
            f"pushed. There is nothing to pin to. Pull the published image instead:\n"
            f"    docker pull {tag}"
        )

    matching = [entry for entry in repo_digests if "@" in entry]
    digests = {entry.partition("@")[2] for entry in matching}
    if len(digests) != 1:
        raise RuntimeError(
            f"`docker image inspect {tag}` returned {len(digests)} distinct digests: "
            f"{sorted(digests)}. Ambiguous - resolve by hand and say which repository is correct."
        )

    return digests.pop()


def _parse_repo_digests(raw: str) -> list[str]:
    """Parse the JSON array Docker prints. `null` is a real answer and means 'none'."""
    import json

    if not raw or raw == "null":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse RepoDigests output {raw!r}: {exc}") from exc
    return list(parsed or [])


# ---------------------------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """KEY=value lines, ignoring comments and blanks. Values are not unquoted or interpolated."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def password_from_url(url: str) -> str | None:
    try:
        return urlsplit(url).password
    except ValueError:
        return None


def _describe_password(label: str, value: str | None) -> str:
    """A description of a secret that is safe to print: never the value, only its shape.

    Decision 9 wants the observed value on failure and decision 4 forbids printing this
    particular one, so what gets reported is length and how many characters fall outside the
    permitted alphabet - enough to act on, and not enough to leak.
    """
    if value is None:
        return f"{label}: absent"
    outside = sum(1 for character in value if character not in "0123456789abcdef")
    return f"{label}: length {len(value)}, {outside} character(s) outside [0-9a-f]"


def check_password_agreement(postgres_password: str | None, database_url: str | None) -> Result:
    """The two secrets are the same secret written twice. Compare them TO EACH OTHER.

    Validating each independently - 64 hex characters, no CHANGEME - passes happily when both are
    valid and DIFFERENT, which initializes the container with one password and authenticates the
    application with another. That failure surfaces much later as an auth error pointing at
    nothing in particular.

    The hex-alphabet assertion is CLAUDE.md § 5's URI-safety rule made checkable: `/` and `+`
    cannot occur in hex, so asserting the alphabet is asserting URI-safety, and it fails here
    rather than as a confusing host-and-port parse error several layers downstream.

    NEITHER VALUE IS EVER PRINTED - only the outcome of the comparison and each value's shape.
    """
    name = ".env secrets agree and are URI-safe"

    if postgres_password is None or database_url is None:
        missing = [
            label
            for label, value in (("POSTGRES_PASSWORD", postgres_password), ("DATABASE_URL", database_url))
            if value is None
        ]
        return Result(name, FAIL, f"observed: missing from .env: {', '.join(missing)}")

    def _placeholder_failure(label):
        return Result(
            name,
            FAIL,
            f"observed: {label} still contains the CHANGEME placeholder from .env.example\n"
            f"         generate one with `openssl rand -hex 32` and put the SAME value in both "
            f"places",
        )

    def _alphabet_failure(label, value):
        return Result(
            name,
            FAIL,
            f"observed: {_describe_password(label, value)}\n"
            f"         expected exactly 64 characters from [0-9a-f]. Generate with `openssl "
            f"rand -hex 32`, NOT `-base64`: CLAUDE.md § 5 - `/` and `+` are meaningful inside a "
            f"URI and break DATABASE_URL parsing as a host-and-port error rather than an auth "
            f"failure.",
        )

    # POSTGRES_PASSWORD is validated BEFORE the URL's password is parsed out, and the ordering is
    # load-bearing rather than stylistic. A password containing `/` makes urlsplit report the URL
    # as having no password at all, because the slash terminates the netloc - which is CLAUDE.md
    # § 5's warning happening inside this very check. Validating the plain value first means such
    # a password is reported as "not hex", which is the actual fault, instead of as "DATABASE_URL
    # has no password", which would send the operator to inspect a URL that is fine.
    if "CHANGEME" in postgres_password:
        return _placeholder_failure("POSTGRES_PASSWORD")
    if not PASSWORD_RE.match(postgres_password):
        return _alphabet_failure("POSTGRES_PASSWORD", postgres_password)

    url_password = password_from_url(database_url)
    if url_password is None:
        return Result(
            name,
            FAIL,
            "observed: DATABASE_URL carries no parseable password component\n"
            "         expected postgresql://USER:PASSWORD@host:port/db. If the password does "
            "contain a value, the usual cause is a character that is meaningful inside a URI - "
            "`/` or `+` from `openssl rand -base64` - which terminates the netloc and makes the "
            "password invisible to any URL parser (CLAUDE.md § 5).",
        )

    if "CHANGEME" in url_password:
        return _placeholder_failure("DATABASE_URL password")
    if not PASSWORD_RE.match(url_password):
        return _alphabet_failure("DATABASE_URL password", url_password)

    if postgres_password != url_password:
        return Result(
            name,
            FAIL,
            f"observed: the two are both well formed but DIFFERENT.\n"
            f"         {_describe_password('POSTGRES_PASSWORD', postgres_password)}\n"
            f"         {_describe_password('DATABASE_URL password', url_password)}\n"
            f"         They are one secret written twice (.env.example says so). The container "
            f"would initialize with one and the application authenticate with the other, surfacing "
            f"as an auth failure several steps later pointing at nothing.",
        )

    return Result(name, PASS, "POSTGRES_PASSWORD and DATABASE_URL carry the same 64-hex secret")


def check_env_permissions(mode: int) -> Result:
    """`.env` holds the database password; it must not be world- or group-readable."""
    permission_bits = stat.S_IMODE(mode)
    if permission_bits & 0o077:
        return Result(
            ".env is mode 600",
            FAIL,
            f"observed: {permission_bits:04o}\n"
            f"         expected 0600. This file holds the database password. Fix: chmod 600 .env",
        )
    return Result(".env is mode 600", PASS, f"{permission_bits:04o}")


# ---------------------------------------------------------------------------------------------
# Data volume
# ---------------------------------------------------------------------------------------------


def check_mount_device(data_st_dev: int, root_st_dev: int, path=DATA_DIR) -> Result:
    """The data volume is a DIFFERENT DEVICE from the root filesystem. Nothing else will do.

    The tempting checks are that the path exists, or that `df` reports roughly the right size.
    Both pass perfectly happily when the volume is not mounted and `/mnt/data/timescaledb` is
    simply a directory on the root disk: the path exists either way, and df reports the root
    volume's size without complaint.

    This matters because CLAUDE.md § 9 puts `nofail` in the fstab entry, which is correct - it
    lets the instance boot without the volume rather than dropping to emergency mode - and which
    therefore makes a silently-absent volume a real, designed-for possibility. Comparing st_dev
    is the only form that distinguishes "mounted" from "a directory with the right name", and
    CLAUDE.md § 2's theme 1 says the check has to be the one that fails when the thing is absent.
    """
    name = "data volume is a separate device from /"
    if data_st_dev == root_st_dev:
        return Result(
            name,
            FAIL,
            f"observed: {path} st_dev={data_st_dev}, / st_dev={root_st_dev} - IDENTICAL.\n"
            f"         The data volume is NOT mounted; this is a directory on the root disk with "
            f"the right name. fstab carries `nofail`, so the instance boots without it and nothing "
            f"else reports this. Check: findmnt /mnt/data && lsblk",
        )
    return Result(
        name,
        PASS,
        f"{path} st_dev={data_st_dev}, / st_dev={root_st_dev} - distinct",
    )


def check_data_bytes(observed_bytes: int, minimum_bytes: int = MINIMUM_DATA_BYTES) -> Result:
    """The volume holds a real cluster, not an empty directory that passed the device check."""
    name = "data volume holds an initialized cluster"
    if observed_bytes < minimum_bytes:
        return Result(
            name,
            FAIL,
            f"observed: {observed_bytes:,} bytes under {DATA_DIR}\n"
            f"         expected at least {minimum_bytes:,}. An initialized Postgres cluster is tens "
            f"of megabytes before a single row is written, so this is an empty or partially "
            f"initialized data directory. Check: docker compose logs timescaledb | tail -50",
        )
    return Result(name, PASS, f"{observed_bytes:,} bytes under {DATA_DIR}")


def directory_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


# ---------------------------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------------------------


def check_migration_count(applied: int, on_disk: int) -> Result:
    """Every numbered migration in the repo is recorded as applied in this database.

    Counts rather than a checksum comparison: the runner already verifies checksums on every run
    and aborts on a mismatch (CLAUDE.md § 12). What this adds is the case the runner never sees -
    a database nobody has run the runner against, which looks identical to a healthy one until
    the first query hits a missing table.
    """
    name = "all migrations applied"
    if applied != on_disk:
        return Result(
            name,
            FAIL,
            f"observed: {applied} row(s) in schema_migrations, {on_disk} migration file(s) in "
            f"{MIGRATIONS_DIR}\n"
            f"         expected equal. Run the migration runner:\n"
            f"             python3 -m app.orchestration.migrate --status\n"
            f"             python3 -m app.orchestration.migrate",
        )
    return Result(name, PASS, f"{applied} of {on_disk} migrations applied")


# ---------------------------------------------------------------------------------------------
# The postgres client/server major agreement
# ---------------------------------------------------------------------------------------------
#
# WHY THIS GATE EXISTS AT ALL: THE SCHEDULER CONTAINER DOES NOT GET THE DOCKER SOCKET.
#
# Through Phase 11 the backup job ran `pg_dump` in a one-shot container off the SAME pinned digest
# as the server, so the client and the server matched mechanically. Containerising the scheduler
# (Phase 12) makes that impossible without mounting /var/run/docker.sock, which is root-equivalent
# on the host - a permanent widening of blast radius in exchange for a convenience.
#
# So the client moved INTO the scheduler image, and the version agreement that used to be a
# property of the digest became two numbers in two files. Two copies of one fact drift silently;
# this project says so in half a dozen places. The trade is accepted because THIS drift is
# detectable, and this is the check that detects it.
#
# THE MAJORS ARE DERIVED FROM THE FILES. NEITHER IS A CONSTANT HERE.
#
# The wrong version of this gate hardcodes `16` as the expected major. It passes forever, it reads
# as careful, and the day somebody bumps the server to pg17 it goes on passing while the client is
# a major behind - a check that cannot fail, reporting correct about the exact thing it stopped
# watching (CLAUDE.md § 2, theme 2). The server major is parsed out of the compose tag and the
# client major out of the package pin, and BOTH observed values are reported on every outcome, so
# a hardcoded expectation is visible in the output rather than only in the source.
#
# EQUALITY, NOT COMPATIBILITY. `pg_dump` older than the server refuses outright; newer than the
# server usually works and is not what anything here was verified against. "Usually works" is not
# an assertable property, and the version that accepts it (`server >= client`) passes a pg17 server
# with a client 16 - which is precisely the case that produces a subtly wrong archive.
#
# The RUNTIME counterpart lives in app/orchestration/backup.py, which compares the actual
# `pg_dump --version` against the actual `SHOW server_version_num`. Both are needed: this reads
# what the files say, that reads what is installed. A stale image passes here and fails there.

SCHEDULER_DOCKERFILE = REPO_ROOT / "Dockerfile.scheduler"

# The compose service whose image tag names the server's major.
SERVER_SERVICE = "timescaledb"

# A marker rather than a full literal, so that no postgres major number is written down in this
# file at all - see the block above. A committed pin containing it is an unresolved placeholder.
PLACEHOLDER_MARKER = "PLACEHOLDER"

_COMPOSE_SERVICE_RE = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")

# `2.26.2-pg16`, `2.26.2-pg16-oss`, `pg16`. The major is the digits after a `pg` that is either at
# the start of the tag or preceded by a separator - so `2.26.2` never reads as a major, and a
# future `-pg17-something` still does.
_PG_MAJOR_RE = re.compile(r"(?:^|[-_.])pg(?P<major>\d+)(?![0-9])")

# `postgresql-client-16=16.10-1.pgdg120+1`, with or without the `=version`. The absence of the
# version part is a distinct finding, not a parse failure: `postgresql-client-16` on its own is a
# FLOATING pin that resolves to whatever point release is current on the machine that builds.
_CLIENT_PIN_RE = re.compile(
    r"postgresql-client-(?P<major>\d+)(?:=(?P<version>[^\s\\\"';]+))?"
)


@dataclass(frozen=True)
class ServerMajor:
    """The postgres major the compose file's server image declares, and what it was read from."""

    major: int | None
    observed: str


@dataclass(frozen=True)
class ClientPin:
    """The postgres client pin the scheduler image declares, and what it was read from."""

    major: int | None
    version: str | None
    observed: str


def server_postgres_major(compose_text: str, service: str = SERVER_SERVICE) -> ServerMajor:
    """Parse the server's postgres major out of the compose `image:` tag.

    READ, NEVER HARDCODED. The digest is already written down once; the major is already written
    down once, inside the tag beside it. A constant here would be a third copy of a fact that
    exists twice, and it would be the copy that never changes.
    """
    current = None
    for line in compose_text.splitlines():
        service_match = _COMPOSE_SERVICE_RE.match(line)
        if service_match is not None:
            current = service_match.group("name")
            continue
        image_match = _IMAGE_LINE_RE.match(line)
        if image_match is None or current != service:
            continue

        reference = image_match.group("reference")
        tag = parse_image_reference(reference).tag
        if tag is None:
            return ServerMajor(None, f"{reference} (no tag, so no major to read)")
        major_match = _PG_MAJOR_RE.search(tag)
        if major_match is None:
            return ServerMajor(None, f"{reference} (tag {tag!r} names no `pgNN` major)")
        return ServerMajor(int(major_match.group("major")), reference)

    return ServerMajor(None, f"no `image:` line for the {service!r} service")


def dockerfile_instructions(dockerfile_text: str) -> str:
    """A Dockerfile with its `#` comment lines removed.

    THE PARSER MUST NOT READ THE FILE'S OWN EXPLANATION OF ITSELF, and this is not hypothetical -
    it was measured on the first run of this gate. Dockerfile.scheduler's header explains that
    "Debian bookworm ships postgresql-client-15, so 16 comes from PGDG", and a search over the raw
    text found that sentence first and reported the image as pinning a client 15 with no version.
    A correct file, read as broken, by a check reading prose as configuration.

    That is CLAUDE.md § 23's rule about a source-scanning guard matching its own justification, and
    the repair it warns against is loosening the pattern until the sentence stops matching - which
    makes the pattern weaker everywhere else too. Strip the comments instead, which is what
    tests/deploy's Caddyfile reader and test_migration_ordering.py already do for the same reason.
    """
    kept = [line for line in dockerfile_text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(kept)


def client_postgres_pin(dockerfile_text: str) -> ClientPin:
    """The client major and exact version the scheduler image INSTALLS, comments excluded.

    EVERY occurrence is collected rather than the first, and the versioned ones decide the answer.
    A first-match parser is correct only for as long as the `apt-get install` line stays above the
    `apt-mark hold` line beside it - and "which line comes first" is precisely the kind of
    load-bearing file order this project has already been bitten by once, in preflight gate 1.
    Order-independence costs four lines here and removes the trap.
    """
    matches = list(_CLIENT_PIN_RE.finditer(dockerfile_instructions(dockerfile_text)))
    if not matches:
        return ClientPin(None, None, "no `postgresql-client-NN` package pin in any instruction")

    majors = sorted({int(m.group("major")) for m in matches})
    if len(majors) > 1:
        return ClientPin(
            None, None,
            f"the image names more than one client major: {majors}. Which one pg_dump ends up "
            f"being is decided by apt, not by this file.",
        )

    versioned = sorted({m.group("version") for m in matches if m.group("version")})
    if len(versioned) > 1:
        return ClientPin(
            majors[0], None,
            f"the image pins postgresql-client-{majors[0]} to more than one version: {versioned}",
        )

    version = versioned[0] if versioned else None
    observed = (
        f"postgresql-client-{majors[0]}={version}" if version
        else f"postgresql-client-{majors[0]} (no `=version`)"
    )
    return ClientPin(majors[0], version, observed)


def check_client_server_major_agreement(server: ServerMajor, client: ClientPin) -> Result:
    """One check, five distinct failures, every one reporting BOTH observed values.

    Both values on every message, including the ones where only one of them is at fault: an
    operator reading "the client pin has no version" still has to know which server it is supposed
    to match, and going to find out is the round trip the harness already had the answer to
    (CLAUDE.md § 13).
    """
    name = "postgres client and server majors agree"
    observed = (
        f"observed: server {server.observed}\n"
        f"         observed: client {client.observed}"
    )

    if server.major is None:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the SERVER major could not be read. Without it there is nothing to compare "
            f"the client against, and a gate that cannot tell must not report agreement.",
        )

    if client.major is None:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the CLIENT major could not be read from {SCHEDULER_DOCKERFILE.name}. The "
            f"scheduler image is where pg_dump comes from now that the Docker socket is not "
            f"mounted; an image with no client pin has no pg_dump at all.",
        )

    if client.version is None:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the client pin carries NO EXACT VERSION. Debian bookworm ships "
            f"postgresql-client-15, so {client.major} comes from PGDG, where a major-only pin "
            f"floats to whatever point release is current on the morning of the build. That is "
            f"`latest` on an image wearing a different hat: it resolves differently on two builds "
            f"three months apart and the difference is invisible until a dump behaves oddly. Pin "
            f"the full version string.",
        )

    if PLACEHOLDER_MARKER in client.version:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         the client version is the committed PLACEHOLDER, not a resolved one - wrong "
            f"by value rather than by form, the same way the all-zero digest is. Resolve it ON THE "
            f"INSTANCE (CLAUDE.md § 5) with the `apt-cache madison` command in "
            f"{SCHEDULER_DOCKERFILE.name}'s header, write the version into the pin, and record it "
            f"in CONTEXT.md.",
        )

    if client.major != server.major:
        return Result(
            name,
            FAIL,
            f"{observed}\n"
            f"         MAJORS DIFFER: server {server.major}, client {client.major}. Equality, not "
            f"compatibility - pg_dump older than the server refuses outright, and newer than the "
            f"server usually works but is not what this was verified against. Since the scheduler "
            f"container has no Docker socket, this pair is the only thing making the dump's client "
            f"match the server it dumps.",
        )

    return Result(
        name,
        PASS,
        f"server major {server.major} ({server.observed})\n"
        f"         client major {client.major} pinned at {client.version}",
    )


# ---------------------------------------------------------------------------------------------
# The gates, run against the real machine
# ---------------------------------------------------------------------------------------------


def gate_images() -> list[Result]:
    """The enumeration's own report, then one result per reference found.

    Not one result for the stack. Five references collapsed into a single PASS/FAIL says nothing
    about which of them is wrong, and a single FAIL stops the operator at the first one rather than
    handing them the whole list.
    """
    enumeration = enumerate_image_sites()
    results = [check_enumeration(enumeration)]
    results.extend(
        check_image_reference(site.reference, where=site.label) for site in enumeration.sites
    )
    return results


def gate_env() -> list[Result]:
    if not ENV_PATH.exists():
        return [
            Result(
                ".env secrets agree and are URI-safe",
                SKIP,
                f"{ENV_PATH} does not exist. Copy .env.example to .env and fill it in. This is a "
                f"SKIP, not a pass - the secrets have not been checked.",
            )
        ]

    values = parse_env_text(ENV_PATH.read_text(encoding="utf-8"))
    return [
        check_env_permissions(ENV_PATH.stat().st_mode),
        check_password_agreement(values.get("POSTGRES_PASSWORD"), values.get("DATABASE_URL")),
    ]


def gate_data_volume() -> list[Result]:
    if not DATA_DIR.is_dir():
        return [
            Result(
                "data volume is a separate device from /",
                SKIP,
                f"{DATA_DIR} does not exist. Expected on the instance only. This is a SKIP, not a "
                f"pass - the volume has not been checked.",
            )
        ]
    return [
        check_mount_device(DATA_DIR.stat().st_dev, ROOT_DIR.stat().st_dev),
        check_data_bytes(directory_bytes(DATA_DIR)),
    ]


def gate_migrations() -> list[Result]:
    on_disk = len([path for path in MIGRATIONS_DIR.glob("*.sql")])

    if not os.environ.get("DATABASE_URL"):
        return [
            Result(
                "all migrations applied",
                SKIP,
                "DATABASE_URL is not set, so the database was not queried. This is a SKIP, not a "
                "pass. Run: set -a; . ./.env; set +a",
            )
        ]

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from app import db  # noqa: PLC0415 - deliberately late, so the gate can SKIP without it

        with db.connection() as conn:
            applied = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - any failure here is a gate failure, with its cause
        return [
            Result(
                "all migrations applied",
                FAIL,
                f"observed: could not query schema_migrations: {type(exc).__name__}: {exc}\n"
                f"         if the table does not exist, the runner has never been run against this "
                f"database.",
            )
        ]

    return [check_migration_count(applied, on_disk)]


def gate_client_server_majors() -> list[Result]:
    """One result. Reads both files; neither major is a constant in this module.

    A missing file is a FAIL rather than a SKIP. The other gates SKIP on a missing `.env` or a
    missing data volume because those are legitimately absent off the instance; a Dockerfile this
    repo commits is not, so its absence means the walk is looking at the wrong tree - and a SKIP
    would read as "nothing to object to" (CLAUDE.md § 13).
    """
    try:
        compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return [Result("postgres client and server majors agree", FAIL,
                       f"observed: could not read {COMPOSE_PATH}: {exc}")]
    try:
        dockerfile_text = SCHEDULER_DOCKERFILE.read_text(encoding="utf-8")
    except OSError as exc:
        return [Result("postgres client and server majors agree", FAIL,
                       f"observed: could not read {SCHEDULER_DOCKERFILE}: {exc}\n"
                       f"         this is where pg_dump comes from now that the scheduler "
                       f"container has no Docker socket.")]

    return [
        check_client_server_major_agreement(
            server_postgres_major(compose_text), client_postgres_pin(dockerfile_text)
        )
    ]


def run_all_gates() -> list[Result]:
    results = gate_images()
    results.extend(gate_client_server_majors())
    results.extend(gate_env())
    results.extend(gate_data_volume())
    results.extend(gate_migrations())
    return results


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _digest_command(write: bool, run=subprocess.run, repo_root: Path = REPO_ROOT) -> int:
    """Resolve - and optionally write - EVERY reference in the stack, with a per-reference diff.

    Every one, because the version of this that wrote only the first left the other four to be
    hand-edited, and hand-editing a 64-character content hash is the failure this command exists to
    remove. It had already produced a hand-edited Caddy digest by the time it was fixed.

    Each tag is resolved once even when several references share it, so the two `FROM` lines of a
    multi-stage build cannot receive two different resolutions of the same tag - which is the
    failure that surfaces as an ImportError in a C extension and reads like a broken dependency.
    """
    enumeration = enumerate_image_sites(repo_root=repo_root)

    if enumeration.unreadable:
        for problem in enumeration.unreadable:
            print(f"FAIL: could not read {problem}", file=sys.stderr)
        return 1

    if not enumeration.sites:
        print(
            "FAIL: no image references found. Nothing was resolved, and a run that resolves "
            "nothing must not exit zero.",
            file=sys.stderr,
        )
        return 1

    print(f"image references in the stack ({len(enumeration.sites)}):\n")

    # tag -> digest, so a tag shared by several sites is asked about once and answers identically
    # everywhere. Resolving per site would let two stages disagree.
    resolved: dict[str, str] = {}
    replacements: dict[Path, list[tuple[int, str, str]]] = {}
    changes = 0

    for site in enumeration.sites:
        parsed = parse_image_reference(site.reference)

        if parsed.tag is None:
            print(
                f"\nFAIL: {site.label} references {site.reference!r}, which has no tag, so there "
                f"is nothing to resolve a digest FROM. A digest-only reference cannot tell anyone "
                f"what to pull. Add a tag first.",
                file=sys.stderr,
            )
            return 1

        tag = f"{parsed.name}:{parsed.tag}"
        if tag not in resolved:
            try:
                digest = resolve_digest(tag, run=run)
            except RuntimeError as exc:
                print(f"\nFAIL: resolving {tag} for {site.label}: {exc}", file=sys.stderr)
                return 1
            if not DIGEST_RE.match(digest):
                print(
                    f"\nFAIL: Docker returned a digest for {tag} that is not sha256+64 hex: "
                    f"{digest!r}",
                    file=sys.stderr,
                )
                return 1
            resolved[tag] = digest

        digest = resolved[tag]
        current = parsed.digest

        print(f"  {site.label:<32} {tag}")
        if current == digest:
            print(f"      {current}   unchanged")
            continue

        # Three cases, distinguished explicitly, because only two of them are writes.
        #
        # THE PLACEHOLDER IS UNPINNED, NOT DRIFT. It is the committed marker for "this digest has
        # not been resolved yet" (CLAUDE.md § 12), chosen precisely because it cannot resolve, and
        # writing it is the entire reason this command exists - four were replaced in Phase 10.
        # Classifying it as drift would make the placeholder the one thing --write-digest refuses
        # to write, and send the operator straight back to the hand-editing this removes.
        if current is not None and current != PLACEHOLDER_DIGEST:
            raise DigestDriftError(
                f"{site.label} is ALREADY PINNED and {tag} now resolves to a different digest.\n"
                f"  written:  {current}\n"
                f"  resolves: {digest}\n"
                f"  file:     {site.path}:{site.line_number}\n\n"
                f"Not rewritten. A tag that resolves to a new digest is the event worth noticing - "
                f"it is `latest` resolving to two TimescaleDB versions three months apart wearing a "
                f"different hat (CLAUDE.md § 5) - and rewriting it here would turn that event into "
                f"a one-line diff nobody reads.\n"
                f"Accepting the new image is a human decision: delete the old digest from the line, "
                f"leaving {tag}, and re-run --write-digest."
            )

        changes += 1
        print(f"      {current or '(none)'}\n   -> {digest}   WOULD WRITE")
        replacements.setdefault(site.path, []).append(
            (site.line_number, site.reference, resolved_reference(site.reference, digest))
        )

    if changes == 0:
        print(f"\nall {len(enumeration.sites)} reference(s) already carry the resolved digest")
        return 0

    if not write:
        print(
            f"\n{changes} of {len(enumeration.sites)} reference(s) disagree with the daemon.\n"
            f"re-run with --write-digest to rewrite them."
        )
        # Non-zero while the files still disagree with the daemon: work remains.
        return 1

    for path, edits in replacements.items():
        original = path.read_text(encoding="utf-8")
        path.write_text(rewrite_reference_lines(original, edits), encoding="utf-8")
        print(f"\nrewrote {path.relative_to(repo_root)} ({len(edits)} reference(s))")

    print("\nReview and commit this diff:\n")
    run(
        ["git", "diff", "--", *sorted(str(path) for path in replacements)],
        cwd=str(repo_root),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight gates for the Phase 2 stack. Run on the instance. Exits non-zero if any "
            "gate fails OR skips - a skipped check is not a passed one."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--resolve-digest",
        action="store_true",
        help=(
            "print the digest Docker reports for every image reference in the stack - "
            "docker-compose.yml and every Dockerfile - and change nothing"
        ),
    )
    group.add_argument(
        "--write-digest",
        action="store_true",
        help=(
            "resolve every image reference in the stack and rewrite each one in place, "
            "then show the diff"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the gates that would run, and run none of them",
    )
    args = parser.parse_args(argv)

    if args.resolve_digest or args.write_digest:
        try:
            return _digest_command(write=args.write_digest, run=subprocess.run)
        except DigestDriftError as exc:
            # Non-zero and loud. Nothing was written; the files still hold the old digests.
            print(f"\nFAIL: {exc}", file=sys.stderr)
            return 1

    if args.dry_run:
        enumeration = enumerate_image_sites()
        walked = ", ".join(path.name for path in enumeration.files_walked) or "(nothing)"
        print("preflight would run these gates, in order:\n")
        for name in (
            f"every image reference across {walked} was enumerated "
            f"({len(enumeration.sites)} found)",
            *(
                f"{site.label} {site.reference.split('@')[0]} is tag@digest, resolved, "
                f"not the placeholder"
                for site in enumeration.sites
            ),
            f"{ENV_PATH.name} is mode 600",
            f"{ENV_PATH.name}: POSTGRES_PASSWORD and DATABASE_URL's password are equal, 64-hex",
            f"{DATA_DIR} is on a different st_dev than /",
            f"{DATA_DIR} holds at least {MINIMUM_DATA_BYTES:,} bytes",
            "schema_migrations row count equals the number of migration files",
        ):
            print(f"  - {name}")
        print(
            "\nA gate whose precondition is absent reports SKIP and the run exits non-zero.\n"
            "No secret value is ever printed - only the outcome of comparing them."
        )
        return 0

    results = run_all_gates()

    print("preflight\n")
    for result in results:
        print(result.render())
        print()

    passed = sum(1 for result in results if result.status == PASS)
    failed = sum(1 for result in results if result.status == FAIL)
    skipped = sum(1 for result in results if result.status == SKIP)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    if skipped:
        print(
            "\nA SKIPPED GATE IS NOT A PASSED ONE. This run is exiting non-zero because something "
            "was not checked, not necessarily because something is broken."
        )

    return exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
