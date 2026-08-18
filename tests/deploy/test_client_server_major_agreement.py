"""The postgres client in the scheduler image, and the server major it has to equal.

WHAT THIS FILE IS ABOUT, IN ONE SENTENCE: the scheduler container does not get the Docker socket,
so `pg_dump` lives inside its image, so the client/server version agreement that used to be a
property of a shared digest is now two numbers in two files - and this is what stops them drifting.

The socket is the alternative and it is worse. Mounting /var/run/docker.sock is root-equivalent on
the host: a compromise of the container whose job is running scheduled Python becomes a compromise
of the instance. That is a permanent widening of blast radius in exchange for a convenience, and it
is undetectable from inside the system. The version pin's failure mode is detectable, and these
tests plus the runtime check in app/orchestration/backup.py are the two things that detect it.

STRUCTURAL, and honest about it (tests/deploy/__init__.py's header). Nothing here proves the image
builds or that pg_dump reports 16. The live steps do that.
"""

from __future__ import annotations

import re

from verify import preflight

from . import (
    DIGEST_RE,
    DOCKERFILE_SCHEDULER_PATH,
    base_images,
    final_stage,
    instruction,
    read_artifact,
    stages,
)

PASS = preflight.PASS
FAIL = preflight.FAIL

# The PGDG Debian repository signing key (pgsql-pkg-debian@postgresql.org). Written here as well as
# in the Dockerfile deliberately: a test that read the fingerprint out of the file it is checking
# would assert only that SOME fingerprint is present, and would stay green if the value were
# swapped for an attacker's. Two independent copies of one constant is the correct shape when the
# constant is a trust anchor - it is the same reasoning CLAUDE.md § 8 applies to the state bucket
# name appearing in both the backend block and the bootstrap configuration.
PGDG_FINGERPRINT = "B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8"


def compose_fixture(tag: str) -> str:
    """A minimal compose file whose timescaledb service carries the given tag."""
    return (
        "services:\n"
        "  api:\n"
        "    image: example/api:1@sha256:" + "ab" * 32 + "\n"
        "  timescaledb:\n"
        f"    image: timescale/timescaledb:{tag}@sha256:" + "cd" * 32 + "\n"
    )


def dockerfile_fixture(pin: str) -> str:
    """A minimal scheduler Dockerfile carrying the given client package pin."""
    return (
        "FROM python:3.12-slim@sha256:" + "ef" * 32 + " AS runtime\n"
        "RUN set -eux; \\\n"
        f"    apt-get install -y --no-install-recommends {pin}\n"
    )


def gate(*, tag: str, pin: str) -> preflight.Result:
    return preflight.check_client_server_major_agreement(
        preflight.server_postgres_major(compose_fixture(tag)),
        preflight.client_postgres_pin(dockerfile_fixture(pin)),
    )


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------


def test_gate_passes_when_majors_agree():
    """The ordinary case, and the one everything else is a deviation from."""
    result = gate(tag="2.26.2-pg16", pin="postgresql-client-16=16.10-1.pgdg120+1")

    assert result.status == PASS, result.render()
    assert "16" in result.detail
    assert "16.10-1.pgdg120+1" in result.detail, (
        f"the passing result does not report the version it accepted: {result.render()}"
    )


def test_gate_fails_when_client_major_differs():
    """A client a major behind the server. pg_dump older than the server refuses outright.

    The failure names BOTH majors, because "the majors differ" without them sends the reader to
    open two files and work out which one is wrong (CLAUDE.md § 13).
    """
    result = gate(tag="2.26.2-pg16", pin="postgresql-client-15=15.14-1.pgdg120+1")

    assert result.status == FAIL, result.render()
    assert "server 16" in result.detail and "client 15" in result.detail, result.render()


def test_gate_fails_when_server_tag_major_changes():
    """A pg17 server with a client 16 pin, which is the case a `>=` comparison lets through.

    EQUALITY, NOT COMPATIBILITY. `pg_dump` newer than the server usually works, and "usually works"
    is not a property anything can assert. The relaxation that reads as reasonable - accept a
    client no newer than the server - passes exactly this pair, and this pair is how a subtly wrong
    archive gets produced by a stack that reports itself pinned.
    """
    result = gate(tag="2.30.0-pg17", pin="postgresql-client-16=16.10-1.pgdg120+1")

    assert result.status == FAIL, result.render()
    assert "server 17" in result.detail and "client 16" in result.detail, result.render()


def test_gate_derives_server_major_from_compose_not_a_constant():
    """The OBSERVED server major moves with the compose tag. Not just the verdict - the value.

    A gate that hardcodes `16` as the expected major passes today, reads as careful, and stops
    meaning anything the day the server is bumped - it is a check that cannot fail, reporting
    correct about the exact thing it has stopped watching (CLAUDE.md § 2, theme 2).

    Asserting the verdict alone does not catch that: with the client pin moved to 17 as well, a
    hardcoded-16 gate would fail and a correct gate would pass, so the STATUS distinguishes them -
    but only because this test moves both. What makes the assertion direct rather than
    circumstantial is reading the number the gate reports back.
    """
    for tag, major in (("2.26.2-pg16", 16), ("2.30.0-pg17", 17), ("2.40.0-pg18", 18)):
        observed = preflight.server_postgres_major(compose_fixture(tag))
        assert observed.major == major, (
            f"compose tag {tag!r} was read as major {observed.major!r}, expected {major}. The "
            f"server major is parsed from the tag; a constant here would not move."
        )

        result = gate(tag=tag, pin=f"postgresql-client-{major}={major}.1-1.pgdg120+1")
        assert result.status == PASS, result.render()
        assert f"server major {major}" in result.detail, (
            f"the gate's own report does not carry the major it read from {tag!r}: "
            f"{result.render()}"
        )


def test_gate_fails_when_client_pin_has_no_exact_version():
    """`postgresql-client-16` with no `=version` is a FLOATING pin.

    Debian bookworm ships postgresql-client-15, so 16 comes from PGDG, where the major-only form
    resolves to whatever point release is current on the morning of the build. That is `latest` on
    an image wearing a different hat (CLAUDE.md § 5): two builds three months apart get different
    binaries and nothing says so until a dump behaves oddly.
    """
    result = gate(tag="2.26.2-pg16", pin="postgresql-client-16")

    assert result.status == FAIL, result.render()
    assert "NO EXACT VERSION" in result.detail, result.render()


def test_gate_fails_on_the_unresolved_placeholder_version():
    """A placeholder version is wrong BY VALUE, the same way the all-zero digest is.

    It parses, it carries a major, it has a `=version` - it satisfies every shape check there is.
    The committed value cannot resolve against PGDG, so `docker build` fails loudly rather than
    installing whatever is current; this gate is what says so BEFORE the build, and names the
    command that resolves it.
    """
    result = gate(tag="2.26.2-pg16", pin="postgresql-client-16=16.0-0.PLACEHOLDER.pgdg120+0")

    assert result.status == FAIL, result.render()
    assert "PLACEHOLDER" in result.detail, result.render()
    assert "madison" in result.detail, (
        f"the placeholder failure does not say how to resolve it: {result.render()}"
    )


def test_the_client_pin_parser_reads_instructions_not_the_files_own_prose():
    """THE INVERTED MUTATION (CLAUDE.md § 23). A pin in a COMMENT must not be read as the pin.

    Measured on this gate's first run: Dockerfile.scheduler's header explains that "Debian bookworm
    ships postgresql-client-15, so 16 comes from PGDG", and a search over the raw text found that
    sentence before the instruction and reported a correct file as pinning client 15 with no
    version. A guard that matches its own justification fails permanently, and the repair somebody
    reaches for is a weaker pattern - which is worse everywhere else.

    A guard that is merely strict is not the same as one that is correct, and only the second
    survives a codebase that documents itself. So this test requires the parser to stay GREEN in
    the presence of prose that would satisfy a naive pattern.
    """
    text = (
        "# Debian bookworm ships postgresql-client-15, which is why this is not it.\n"
        "# An earlier draft installed postgresql-client-14=14.1-1.pgdg120+1 and was wrong.\n"
        "FROM python:3.12-slim@sha256:" + "ef" * 32 + " AS runtime\n"
        "RUN apt-get install -y postgresql-client-16=16.10-1.pgdg120+1; \\\n"
        "    apt-mark hold postgresql-client-16\n"
    )
    pin = preflight.client_postgres_pin(text)

    assert pin.major == 16, f"the parser read the prose: {pin}"
    assert pin.version == "16.10-1.pgdg120+1", f"the parser read the prose: {pin}"


def test_the_client_pin_parser_does_not_depend_on_instruction_order():
    """`apt-mark hold postgresql-client-16` carries no version, and it must not become the answer.

    A first-match parser is correct only while the install line stays above the hold line. Which
    line comes first is exactly the kind of load-bearing file order that re-pointed preflight's
    gate 1 at the wrong image (CLAUDE.md § 22), so it is removed rather than documented.
    """
    reversed_order = (
        "FROM python:3.12-slim@sha256:" + "ef" * 32 + " AS runtime\n"
        "RUN apt-mark hold postgresql-client-16; \\\n"
        "    apt-get install -y postgresql-client-16=16.10-1.pgdg120+1\n"
    )
    pin = preflight.client_postgres_pin(reversed_order)

    assert pin.major == 16 and pin.version == "16.10-1.pgdg120+1", pin


def test_the_committed_files_agree_on_the_major():
    """The real docker-compose.yml against the real Dockerfile.scheduler.

    The fixtures above prove the gate distinguishes agreement from disagreement; this proves the
    committed pair AGREES, which is the invariant the gate exists to hold. The version itself is
    still the unresolved placeholder - `verify/preflight.py` refuses that separately, on the
    instance, where it can be resolved.
    """
    server = preflight.server_postgres_major(read_artifact(preflight.COMPOSE_PATH))
    client = preflight.client_postgres_pin(read_artifact(DOCKERFILE_SCHEDULER_PATH))

    assert server.major is not None, server.observed
    assert client.major is not None, client.observed
    assert client.version is not None, (
        f"the committed client pin carries no exact version: {client.observed}"
    )
    assert client.major == server.major, (
        f"the committed files disagree: server major {server.major} ({server.observed}), client "
        f"major {client.major} ({client.observed})"
    )


# ---------------------------------------------------------------------------------------------
# The image itself
# ---------------------------------------------------------------------------------------------


def test_scheduler_dockerfile_does_not_install_server_package():
    """`postgresql-client-16`, never `postgresql-16`.

    The server package runs initdb at image build time and leaves a second Postgres cluster inside
    the image - wasteful, and a live foot-gun the day anything starts it. The two package names
    differ by one word and the wrong one is the shorter, more obvious spelling.

    Comments are stripped first, for the same reason the pin parser strips them: this file explains
    at length why the server package is not installed, and a raw grep would match the explanation.
    """
    text = preflight.dockerfile_instructions(read_artifact(DOCKERFILE_SCHEDULER_PATH))

    assert "postgresql-client-" in text, (
        "the scheduler image installs no postgres client at all - this test would pass over "
        "nothing, and pg_dump would not exist in the image the backup job runs in"
    )

    offenders = re.findall(r"(?<![\w-])postgresql-\d+(?![\w-])", text)
    assert offenders == [], (
        f"Dockerfile.scheduler installs the postgres SERVER package: {offenders}. That runs initdb "
        f"and creates a second cluster inside the image. Only postgresql-client-NN is wanted."
    )


def test_scheduler_dockerfile_pins_key_fingerprint():
    """The PGDG signing key is verified BY FINGERPRINT, not merely downloaded.

    `curl ... | apt-key add -` trusts whatever answers the request, in a build running as root that
    is about to install packages from the repository that key authorises. The fingerprint literal
    here is independent of the file's (see PGDG_FINGERPRINT), so swapping the value in the
    Dockerfile turns this red rather than moving the goalposts with it.

    The comparison is asserted too, not only the presence of the string: a fingerprint written into
    a comment and never compared is documentation wearing a check's clothes.
    """
    text = preflight.dockerfile_instructions(read_artifact(DOCKERFILE_SCHEDULER_PATH))

    assert PGDG_FINGERPRINT in text, (
        f"Dockerfile.scheduler does not carry the PGDG key fingerprint {PGDG_FINGERPRINT} in any "
        f"instruction. Without it the build trusts whatever the key URL returns."
    )
    assert "--with-fingerprint" in text or "--fingerprint" in text, (
        "the fingerprint is written down but never READ from the fetched key - so nothing compares "
        "them and the literal is decoration"
    )
    assert re.search(r'!=\s*"?\$', text), (
        "the fingerprint is read but never COMPARED. The build must exit non-zero on a mismatch; "
        "a fetched value nobody tests against the literal is the same as no check."
    )
    assert "apt-key" not in text, (
        "Dockerfile.scheduler uses `apt-key`, which trusts the key globally and is deprecated. The "
        "key belongs in a repo-scoped keyring referenced by `signed-by` (CLAUDE.md § 10)."
    )
    assert "signed-by=" in text, (
        "the PGDG sources entry does not scope the key with `signed-by=`, so the key is trusted "
        "for every repository the image has configured"
    )


def test_scheduler_dockerfile_from_lines_carry_tag_and_digest():
    """Both stages pinned by tag@digest, agreeing, and enumerated by preflight's own walk.

    The enumeration half is the point of asserting it here rather than only in the Dockerfile: a
    new Dockerfile that preflight's gate 1 does not walk is an unpinned base with a green gate
    above it, which is the shape CLAUDE.md § 22 records gate 1 shipping once already. The walk is
    a glob over `Dockerfile*`, so this file joining it is automatic - and "automatic" is exactly
    the kind of claim that stops being true silently, so it is measured.
    """
    text = read_artifact(DOCKERFILE_SCHEDULER_PATH)
    references = base_images(text)

    assert len(references) == 2, (
        f"expected a two-stage build, found {len(references)} FROM line(s): {references}"
    )
    for reference in references:
        assert DIGEST_RE.search(reference), f"{reference!r} is not pinned by digest"
        name_and_tag = reference.split("@", 1)[0]
        assert ":" in name_and_tag.rsplit("/", 1)[-1], (
            f"{reference!r} carries a digest and no tag. The digest is the pin; the tag is how it "
            f"is re-derivable (CLAUDE.md § 13)."
        )

    assert len(set(references)) == 1, (
        f"the two stages build on different references: {references}. Two resolutions of one tag "
        f"is a runtime that is not the interpreter the wheels were installed against, and it "
        f"surfaces as an ImportError in a C extension that reads like a broken dependency."
    )

    enumeration = preflight.enumerate_image_sites()
    walked = {path.name for path in enumeration.files_walked}
    assert DOCKERFILE_SCHEDULER_PATH.name in walked, (
        f"preflight's image walk did not read {DOCKERFILE_SCHEDULER_PATH.name}; it walked {walked}"
    )
    assert len(enumeration.sites) == 8, (
        f"preflight enumerates {len(enumeration.sites)} image reference(s) across "
        f"{len(enumeration.files_walked)} file(s), expected 8 across 4 - two compose `image:` "
        f"lines and two `FROM` lines in each of three Dockerfiles. A count that has drifted means "
        f"a reference was added or removed without anybody deciding to."
    )


def test_the_scheduler_image_runs_as_non_root_and_starts_the_scheduler():
    """Non-root in the FINAL stage, exec-form CMD, and no migration runner anywhere.

    This is the image that would find a migration runner convenient - it is the one with a database
    connection and a reason to want the schema current - so the absence is asserted here rather
    than inherited from Dockerfile.api's version of the same test.
    """
    text = read_artifact(DOCKERFILE_SCHEDULER_PATH)
    _, lines = final_stage(text)

    users = instruction(lines, "USER")
    assert users, "the scheduler image declares no USER in its final stage - it runs as root"
    for value in users:
        assert value not in ("root", "0", "0:0", "root:root"), f"final USER is {value!r}"

    cmds = instruction(lines, "CMD")
    assert len(cmds) == 1, f"expected exactly one CMD in the final stage, found {cmds!r}"
    cmd = cmds[0]
    assert cmd.startswith("[") and cmd.endswith("]"), (
        f"CMD is in shell form: {cmd!r}. Shell form is what makes `migrate && scheduler` possible."
    )
    for chain in ("&&", "||", ";", "|"):
        assert chain not in cmd, f"the scheduler CMD chains commands with {chain!r}: {cmd!r}"
    assert "app.orchestration.scheduler" in cmd, f"the CMD does not start the scheduler: {cmd!r}"

    entrypoints = [
        found for _, body in stages(text) for found in instruction(body, "ENTRYPOINT")
    ]
    assert not entrypoints, f"Dockerfile.scheduler declares ENTRYPOINT {entrypoints!r}"

    for path in ("app.orchestration.migrate", "app/orchestration/migrate", "orchestration.migrate"):
        assert path not in text, (
            f"Dockerfile.scheduler names the schema runner ({path}). Migrations never run on "
            f"container start; under `restart: unless-stopped` a crash loop becomes a "
            f"schema-change loop (CLAUDE.md § 3)."
        )
