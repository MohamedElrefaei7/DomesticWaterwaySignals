"""Shared readers for the Phase 10 deployment artifacts.

These are STRUCTURAL tests over committed files. They cannot prove that TLS is issued, that the
rate limit fires, or that the stack survives a reboot — those are live steps a human runs on the
instance, and CLAUDE.md § 13 is explicit that a configuration test is not evidence of behaviour.
What they can do is fail when a file says something different from what this commit decided, and
the mutation table in the report is what shows each one does.

EVERY READER IN HERE FAILS LOUDLY WHEN IT CANNOT SEE THE FILE IT IS ABOUT.

That is not defensive habit; it is the specific failure this project has already shipped twice. An
ingress test passed because the set it constrained was empty, and Phase 9's source-tree scanners
would have passed vacuously against an empty directory (CLAUDE.md § 2 theme 2, § 21). A structural
test that reads a missing file as "nothing to object to" is green forever and watching nothing, so
`read_artifact` raises on absent and on empty, `load_compose` asserts the service set is non-empty,
and the port reader asserts it walked at least one service.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
CADDYFILE_PATH = REPO_ROOT / "Caddyfile"
DOCKERFILE_API_PATH = REPO_ROOT / "Dockerfile.api"
DOCKERFILE_FRONTEND_PATH = REPO_ROOT / "Dockerfile.frontend"
DOCKERFILE_SCHEDULER_PATH = REPO_ROOT / "Dockerfile.scheduler"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
DEPLOY_SCRIPT_PATH = REPO_ROOT / "infra" / "provision" / "deploy.sh"
STACK_UNIT_PATH = REPO_ROOT / "infra" / "provision" / "dws-stack.service"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# The domain. A literal, purchased, and not a placeholder.
DOMAIN = "bargeanalysis.com"

# The FIVE services the stack is made of. `scheduler` joined in Phase 12; it is the process that
# runs every job, and before it existed the scheduler had never run in production at all.
EXPECTED_SERVICES = {"timescaledb", "api", "frontend-build", "caddy", "scheduler"}

# Everything that is not a one-shot. Derived rather than written out a second time: the two sets
# partition EXPECTED_SERVICES, and a hand-written third list is the one that goes stale.
LONG_LIVED_SERVICES = EXPECTED_SERVICES - {"frontend-build"}

# The one-shot. Everything else is long-lived and carries `restart: unless-stopped`; this one
# exits on purpose, so that policy would make it a rebuild loop.
ONE_SHOT_SERVICES = {"frontend-build"}

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")


def read_artifact(path: Path) -> str:
    """The file's text, or an assertion naming the path.

    Absent and empty are both failures. An empty file satisfies every `not in` assertion written
    against it.
    """
    assert path.exists(), (
        f"{path} does not exist. Every check in tests/deploy/ that reads it would otherwise "
        f"pass over nothing."
    )
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty"
    return text


def load_compose() -> dict:
    """docker-compose.yml, parsed as YAML rather than grepped.

    Parsed because a grep for `ports:` cannot tell a service's key from a word inside the long
    header comment that explains why the key is absent, and because the published-port set has to
    be compared as a SET (CLAUDE.md § 8's allowlist discipline) rather than searched for
    individually.
    """
    document = yaml.safe_load(read_artifact(COMPOSE_PATH))
    assert isinstance(document, dict), "docker-compose.yml did not parse as a mapping"

    services = document.get("services")
    assert isinstance(services, dict) and services, "docker-compose.yml declares no services"

    return document


def services() -> dict:
    return load_compose()["services"]


def published_ports(compose: dict) -> set[int]:
    """Every HOST port published by any service, as a set of ints.

    Both `ports:` shapes are handled. The short form's host port is the field before the last
    colon (`80:80`, `127.0.0.1:5432:5432`); the long form names it `published`. Handling only one
    shape is how a published port becomes invisible to the check that exists to find it.
    """
    found: set[int] = set()
    walked = 0

    for name, service in compose["services"].items():
        walked += 1
        for entry in service.get("ports", []) or []:
            if isinstance(entry, dict):
                published = entry.get("published")
                assert published is not None, (
                    f"{name} declares a long-form port with no `published` key: {entry!r}"
                )
                found.add(int(published))
                continue

            text = str(entry)
            assert ":" in text, (
                f"{name} publishes {text!r} with no host mapping. A bare container port is "
                f"assigned a RANDOM host port by Docker, which is a published port nobody chose."
            )
            host = text.rsplit(":", 1)[0].rsplit(":", 1)[-1]
            found.add(int(host))

    assert walked, "walked no services - the port reader saw nothing"
    return found


def image_references(compose: dict) -> dict[str, list[str]]:
    """service name -> every image reference that service ultimately runs.

    A service either pulls an image (`image:`) or builds one (`build:`), and a build's pin lives in
    the Dockerfile's `FROM` lines rather than here. Reading only `image:` keys would report a
    stack as fully pinned while two of its four services build from a floating base — the check
    would be looking at the wrong file for half the answer.
    """
    references: dict[str, list[str]] = {}

    for name, service in compose["services"].items():
        if "image" in service:
            references[name] = [service["image"]]
            continue

        build = service.get("build")
        assert build is not None, f"service {name} declares neither `image:` nor `build:`"
        dockerfile = build["dockerfile"] if isinstance(build, dict) else None
        assert dockerfile, f"service {name} builds without naming a dockerfile"

        path = REPO_ROOT / dockerfile
        references[name] = base_images(read_artifact(path))
        assert references[name], f"{dockerfile} declares no FROM line"

    assert set(references) == EXPECTED_SERVICES, (
        f"the service set changed: {sorted(references)} != {sorted(EXPECTED_SERVICES)}. "
        f"A new service is a new thing that can publish a port or run as root; add it here "
        f"deliberately."
    )
    return references


# ---------------------------------------------------------------------------------------------
# Dockerfiles
# ---------------------------------------------------------------------------------------------

_FROM_RE = re.compile(r"^\s*FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.IGNORECASE)


def base_images(dockerfile_text: str) -> list[str]:
    """Every `FROM` reference, in file order."""
    return [m.group("ref") for m in (_FROM_RE.match(l) for l in dockerfile_text.splitlines()) if m]


def stages(dockerfile_text: str) -> list[tuple[str, list[str]]]:
    """[(base reference, instruction lines)] per stage, in file order, comments stripped.

    The FINAL stage is the image that actually runs. A toolchain installed in an earlier stage is
    discarded; the same line in the last stage ships. Any check about "the image" that does not
    know where the stages divide is checking the wrong text.
    """
    collected: list[tuple[str, list[str]]] = []
    for raw in dockerfile_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _FROM_RE.match(raw)
        if match:
            collected.append((match.group("ref"), []))
            continue
        assert collected, f"instruction before any FROM: {line!r}"
        collected[-1][1].append(line)

    assert collected, "no stages found - the Dockerfile parsed to nothing"
    return collected


def final_stage(dockerfile_text: str) -> tuple[str, list[str]]:
    return stages(dockerfile_text)[-1]


def instruction(lines: list[str], keyword: str) -> list[str]:
    """Every instruction of a given keyword, with the keyword stripped."""
    prefix = keyword.upper() + " "
    return [l[len(prefix):].strip() for l in lines if l.upper().startswith(prefix)]


# ---------------------------------------------------------------------------------------------
# Caddyfile / shell
# ---------------------------------------------------------------------------------------------


def caddyfile_directives() -> str:
    """The Caddyfile with comment lines removed.

    The file explains at length why there is no rate limiter and why `style-src` carries
    'unsafe-inline'; a check for a CDN hostname or a forbidden directive has to read what is
    CONFIGURED, not what is discussed. Same precedent as
    tests/orchestration/test_migration_ordering.py, which strips comments so docker-compose.yml can
    describe the rule that test enforces.
    """
    text = read_artifact(CADDYFILE_PATH)
    kept = [line for line in text.splitlines() if not line.strip().startswith("#")]
    stripped = "\n".join(kept)
    assert stripped.strip(), "the Caddyfile is entirely comments"
    return stripped


def executable_shell_lines(path: Path) -> list[str]:
    """A shell script's non-comment, non-blank lines."""
    lines = [
        line.rstrip()
        for line in read_artifact(path).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines, f"{path} has no executable lines"
    return lines
