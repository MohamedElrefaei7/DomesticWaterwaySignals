"""Preflight gates: everything that must hold before any other verification means anything.

CLAUDE.md § 13 in executable form. Five gates — image pin, `.env` secret agreement, data-volume
identity, data-volume contents, and applied migrations — plus the digest-resolution helper that
exists because hand-editing the digest has now failed twice.

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
IMAGE_LINE_RE = re.compile(r"^(?P<indent>\s*)image:\s*(?P<reference>\S+)\s*$", re.MULTILINE)
PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")

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


def check_image_reference(reference: str) -> Result:
    """The image pin gate. Four distinct failures, four distinct messages.

    The placeholder and the malformed-digest cases are separated deliberately. `0000...0000` is
    64 valid hex characters and satisfies any shape validation there is, so a single "bad digest"
    message would send the operator to check their typing when the actual fix is to run
    --write-digest. The two have different causes and different remedies.
    """
    parsed = parse_image_reference(reference)

    if parsed.digest is None:
        return Result(
            "image is pinned by digest",
            FAIL,
            f"observed: {parsed.raw}\n"
            f"         no `@sha256:...` digest at all - this is a floating tag, and a floating tag "
            f"on a database image resolved to two different versions three months apart on the "
            f"prior project (CLAUDE.md § 5). Run: python3 -m verify.preflight --write-digest",
        )

    if parsed.tag is None:
        return Result(
            "image reference carries a tag",
            FAIL,
            f"observed: {parsed.raw}\n"
            f"         the reference has a digest but NO TAG. The digest is the pin; the tag is how "
            f"the digest is re-derivable. Without it, nobody can work out what to `docker pull` in "
            f"order to recover or re-verify this digest - which is how this line failed the first "
            f"time. Rewrite as {parsed.name}:<tag>@{parsed.digest}",
        )

    if parsed.digest == PLACEHOLDER_DIGEST:
        return Result(
            "image digest is not the placeholder",
            FAIL,
            f"observed: {parsed.digest}\n"
            f"         this is the all-zero PLACEHOLDER digest, not a resolved one. It is 64 valid "
            f"hex characters, so it passes every shape check - it is wrong by value, not by form. "
            f"Fix: docker pull {parsed.name}:{parsed.tag} "
            f"&& python3 -m verify.preflight --write-digest",
        )

    if not DIGEST_RE.match(parsed.digest):
        return Result(
            "image digest is well formed",
            FAIL,
            f"observed: {parsed.digest!r} (length {len(parsed.digest)})\n"
            f"         expected `sha256:` followed by exactly 64 lowercase hex characters. This is "
            f"a malformed value rather than an unreplaced placeholder - most likely a truncated or "
            f"hand-typed digest. Do not retype it: python3 -m verify.preflight --write-digest",
        )

    return Result(
        "image is pinned by tag@digest",
        PASS,
        f"{parsed.name}:{parsed.tag}\n         pinned at {parsed.digest}",
    )


def read_image_reference(compose_text: str) -> str:
    """The first `image:` value in the compose file.

    Deliberately not a YAML parse. This function is also used by --write-digest to rewrite the
    line in place, and a round-trip through a YAML library would discard every comment in the
    file - including the block above the image line that explains why the tag is there.
    """
    match = IMAGE_LINE_RE.search(compose_text)
    if match is None:
        raise ValueError(f"no `image:` line found in {COMPOSE_PATH}")
    return match.group("reference")


def rewrite_image_digest(compose_text: str, digest: str) -> str:
    """Replace the digest on the image line, preserving the tag, indentation, and every comment."""
    reference = read_image_reference(compose_text)
    parsed = parse_image_reference(reference)
    if parsed.tag is None:
        raise ValueError(
            f"refusing to rewrite {reference!r}: it has no tag, so the new digest would not be "
            f"re-derivable. Add a tag first."
        )
    updated = f"{parsed.name}:{parsed.tag}@{digest}"
    return compose_text.replace(reference, updated, 1)


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
# The gates, run against the real machine
# ---------------------------------------------------------------------------------------------


def gate_image() -> Result:
    try:
        reference = read_image_reference(COMPOSE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Result("image is pinned by tag@digest", SKIP, f"could not read the image line: {exc}")
    return check_image_reference(reference)


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


def run_all_gates() -> list[Result]:
    results = [gate_image()]
    results.extend(gate_env())
    results.extend(gate_data_volume())
    results.extend(gate_migrations())
    return results


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _digest_command(write: bool, run=subprocess.run) -> int:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    reference = read_image_reference(compose_text)
    parsed = parse_image_reference(reference)

    print(f"compose image reference: {parsed.raw}")

    if parsed.tag is None:
        print(
            f"\nFAIL: the reference has no tag, so there is nothing to resolve a digest FROM.\n"
            f"      A digest-only reference cannot tell anyone what to pull. Add a tag first.",
            file=sys.stderr,
        )
        return 1

    print(f"tag to resolve:          {parsed.name}:{parsed.tag}")

    try:
        digest = resolve_digest(f"{parsed.name}:{parsed.tag}", run=run)
    except RuntimeError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1

    print(f"resolved digest:         {digest}")

    if not DIGEST_RE.match(digest):
        print(f"\nFAIL: Docker returned a digest that is not sha256+64 hex: {digest!r}", file=sys.stderr)
        return 1

    if not write:
        if parsed.digest == digest:
            print("\nthe compose file already carries this digest; nothing to do")
            return 0
        print(
            f"\ncompose currently carries: {parsed.digest}\n"
            f"re-run with --write-digest to rewrite the image line."
        )
        # Non-zero while the file still disagrees with the daemon: work remains.
        return 1

    updated = rewrite_image_digest(compose_text, digest)
    if updated == compose_text:
        print("\nthe compose file already carries this digest; nothing to write")
        return 0

    COMPOSE_PATH.write_text(updated, encoding="utf-8")
    print(f"\nrewrote {COMPOSE_PATH.relative_to(REPO_ROOT)}. Review and commit this diff:\n")
    run(["git", "diff", "--", str(COMPOSE_PATH)], cwd=str(REPO_ROOT))
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
        help="print the digest Docker reports for the tag in docker-compose.yml, and change nothing",
    )
    group.add_argument(
        "--write-digest",
        action="store_true",
        help="resolve the digest and rewrite the image line in docker-compose.yml, then show the diff",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the gates that would run, and run none of them",
    )
    args = parser.parse_args(argv)

    if args.resolve_digest or args.write_digest:
        return _digest_command(write=args.write_digest, run=subprocess.run)

    if args.dry_run:
        print("preflight would run these gates, in order:\n")
        for name in (
            f"image reference in {COMPOSE_PATH.name} is tag@digest, resolved, not the placeholder",
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
