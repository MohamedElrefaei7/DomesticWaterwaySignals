"""The two images this project builds: what they run as, what they run, and what they are built on.

The API image is the one that matters most here. It is the process a stranger's request reaches,
and until this commit no application container existed at all — so this is the first commit in
which somebody could give one an entrypoint, run it as root, or teach it to change the schema on
start.
"""

from __future__ import annotations

import re

from . import (
    DOCKERFILE_API_PATH,
    DOCKERFILE_FRONTEND_PATH,
    DIGEST_RE,
    base_images,
    final_stage,
    instruction,
    load_compose,
    read_artifact,
    stages,
)

# The runner, spelled every way a Dockerfile could name it. CLAUDE.md § 3 / § 12: schema changes
# are a CLI a human invokes, and `restart: unless-stopped` would turn a crash loop into a
# schema-change loop.
RUNNER_PATHS = (
    "app.orchestration.migrate",
    "app/orchestration/migrate",
    "orchestration.migrate",
)

# Anything whose presence in a FINAL stage means a compiler shipped to production. Word-bounded,
# so `make` does not match `makefile` in a comment and `gcc` does not match a hash.
TOOLCHAIN = (
    "build-essential",
    "gcc",
    "g++",
    "clang",
    "make",
    "cmake",
    "python3-dev",
    "libpq-dev",
    "npm ci",
    "npm install",
    "npm run build",
)


def test_the_api_image_runs_as_a_non_root_user():
    """Decision 3, and the ordering matters as much as the instruction.

    `USER` after `CMD` is not an error and does nothing — the instruction's effect is on
    subsequent instructions and on the runtime user, and a `USER` line placed below `CMD` still
    sets the runtime user, but a `USER` line in an EARLIER STAGE does not. Both the stage and the
    value are checked, plus the numeric spellings of root, because `USER 0` reads as configured.
    """
    _, lines = final_stage(read_artifact(DOCKERFILE_API_PATH))

    users = instruction(lines, "USER")
    assert users, (
        "the api image declares no USER in its final stage - it runs as root. A container "
        "reachable from the internet running as root is a strictly worse position than one that "
        "is not, for no benefit."
    )

    for value in users:
        assert value not in ("root", "0", "0:0", "root:root"), (
            f"the api image's final USER is {value!r}"
        )

    # Everything the process does after this point runs as that user. A COPY or RUN after USER is
    # fine; what is not fine is USER never appearing before the container starts.
    cmds = [i for i, l in enumerate(lines) if l.upper().startswith("CMD ")]
    user_index = max(i for i, l in enumerate(lines) if l.upper().startswith("USER "))
    assert cmds and user_index < cmds[0], (
        "USER is declared after CMD in the api image's final stage"
    )


def test_the_api_cmd_does_not_invoke_the_migration_runner():
    """Decision 4. The whole file is checked, comments included, and then the CMD's shape.

    Two assertions because they catch two different mutations. Naming the module path is the
    obvious form and a grep finds it; `CMD ["sh", "-c", "migrate && uvicorn ..."]` spells it some
    other way, and what gives that one away is the shell. An exec-form CMD whose first element is
    uvicorn cannot chain anything onto it — there is no shell to chain with.
    """
    text = read_artifact(DOCKERFILE_API_PATH)

    for path in RUNNER_PATHS:
        assert path not in text, (
            f"Dockerfile.api names the schema runner ({path}). Schema changes are a CLI a human "
            f"invokes; a container that applies them on start turns a restart loop into a "
            f"schema-change loop (CLAUDE.md § 3)."
        )

    _, lines = final_stage(text)
    cmds = instruction(lines, "CMD")
    assert len(cmds) == 1, f"expected exactly one CMD in the final stage, found {cmds!r}"

    cmd = cmds[0]
    assert cmd.startswith("[") and cmd.endswith("]"), (
        f"CMD is in shell form: {cmd!r}. Shell form runs through `/bin/sh -c`, which is what makes "
        f"`something && uvicorn ...` possible in the first place."
    )
    assert re.match(r'^\[\s*"uvicorn"', cmd), (
        f"the api CMD does not start with uvicorn: {cmd!r}"
    )
    for chain in ("&&", "||", ";", "|"):
        assert chain not in cmd, f"the api CMD chains commands with {chain!r}: {cmd!r}"


def test_no_entrypoint_script_is_declared_for_the_api():
    """Neither in the Dockerfile nor in the Compose service.

    An entrypoint is where a schema step, a "wait for the database" loop, or a secret-fetching
    shim gets added later, and none of it is visible from the CMD anybody reads. The Compose half
    matters because `entrypoint:` there overrides the image and would not show up in this file at
    all — tests/orchestration/test_migration_ordering.py already forbids the key stack-wide, and
    this states it for the service that would want one.
    """
    text = read_artifact(DOCKERFILE_API_PATH)

    for base, lines in stages(text):
        entrypoints = instruction(lines, "ENTRYPOINT")
        assert not entrypoints, (
            f"Dockerfile.api declares ENTRYPOINT {entrypoints!r} in the stage on {base}"
        )

    assert ".sh" not in text, (
        "Dockerfile.api references a shell script. The container runs uvicorn directly."
    )

    api = load_compose()["services"]["api"]
    assert "entrypoint" not in api and "command" not in api, (
        f"the api Compose service overrides the image's start: {api.get('entrypoint')!r} / "
        f"{api.get('command')!r}"
    )


def test_the_base_image_is_pinned_by_digest():
    """Every FROM in both Dockerfiles, and the two stages of one image agree on the digest.

    The agreement check is the non-obvious half. A multi-stage build whose stages sit on two
    different resolutions of `python:3.12-slim` produces a runtime that is not the interpreter the
    wheels were installed against, and the symptom is an ImportError deep in a C extension that
    reads like a broken dependency rather than like a build mistake. Two digests in one file is
    also exactly what a half-finished hand edit leaves behind — and this digest is hand-edited,
    because `verify/preflight.py --write-digest` only rewrites docker-compose.yml.
    """
    for path in (DOCKERFILE_API_PATH, DOCKERFILE_FRONTEND_PATH):
        text = read_artifact(path)
        references = base_images(text)
        assert references, f"{path.name} declares no FROM line"

        for reference in references:
            assert DIGEST_RE.search(reference), (
                f"{path.name} builds on {reference!r}, which is a floating tag. Pinned by digest, "
                f"resolved on the machine that runs it (CLAUDE.md § 5)."
            )
            name_and_tag = reference.split("@", 1)[0]
            assert ":" in name_and_tag.rsplit("/", 1)[-1], (
                f"{path.name} builds on {reference!r} - a digest with no tag. The tag is how the "
                f"digest is re-derivable (CLAUDE.md § 13)."
            )

        by_name: dict[str, set[str]] = {}
        for reference in references:
            name_and_tag, _, digest = reference.partition("@")
            by_name.setdefault(name_and_tag, set()).add(digest)

        disagreements = {n: d for n, d in by_name.items() if len(d) > 1}
        assert disagreements == {}, (
            f"{path.name} pins the same image to more than one digest: {disagreements}. The "
            f"stages must build on the same base."
        )


def test_no_build_toolchain_survives_into_the_final_stage():
    """A compiler in a discarded stage is free; the same line in the last stage ships.

    Nothing needs a compiler today — psycopg ships wheels — so this is guarding the shape rather
    than a present fact. The shape is what matters: when a dependency does need one, the one-line
    fix has an obvious right place to land, and this test is what makes the wrong place fail.

    The frontend image is checked too, and it is the one with something to lose: `npm ci` in the
    final stage would carry node_modules into the artifact image.
    """
    for path in (DOCKERFILE_API_PATH, DOCKERFILE_FRONTEND_PATH):
        base, lines = final_stage(read_artifact(path))
        body = "\n".join(lines)

        found = [
            token for token in TOOLCHAIN
            if re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", body)
        ]
        assert found == [], (
            f"{path.name}'s final stage (on {base}) contains {found}. Build tooling belongs in an "
            f"earlier stage that is discarded; in the final stage it ships to production."
        )


def test_the_frontend_build_pins_its_node_version_and_serves_no_bind_mounted_checkout():
    """Decision 6, and the Phase 9 finding turned into a guard.

    RENAMED FROM the brief's `test_the_frontend_build_pins_its_node_version` because the brief's
    own mutation table points two different mutations at it — an unpinned Node and a bind-mounted
    `frontend/dist` — and a test named after only the first would have been lying about the
    second. Both are asserted here.

    The Node half: the instance ran Node 18.19.1, `npm ci` warned EBADENGINE on every package and
    EXITED ZERO, and the failure surfaced one command later out of `vite build`. Node had never
    been pinned anywhere in this project. It is pinned here by major version and by digest, and
    the host's Node is never invoked.

    The bind-mount half: what Caddy serves comes from a volume written by this image. A checkout
    mount would change the live site the moment somebody ran `git pull`, and would serve a
    half-written bundle mid-build.
    """
    text = read_artifact(DOCKERFILE_FRONTEND_PATH)
    references = base_images(text)
    assert references, "Dockerfile.frontend declares no FROM line"

    for reference in references:
        assert reference.startswith("node:"), (
            f"Dockerfile.frontend builds on {reference!r}, which is not a node image"
        )
        tag = reference.split("@", 1)[0].split(":", 1)[1]
        major = re.match(r"^(\d+)", tag)
        assert major is not None, (
            f"Dockerfile.frontend's tag {tag!r} does not begin with a major version. `node:latest` "
            f"and `node:lts` both float across majors, which is the version the instance got "
            f"wrong."
        )
        assert int(major.group(1)) >= 22, (
            f"Dockerfile.frontend pins Node {major.group(1)}. Vite 8 and Vitest 4 need >= 20 and "
            f"several packages want 22; the instance's Node 18 failed on `styleText` from "
            f"node:util after a clean `npm ci`."
        )
        assert DIGEST_RE.search(reference), f"{reference!r} is not pinned by digest"

    assert "npm ci" in text, "the frontend image does not install from the lockfile"
    assert "npm run build" in text, "the frontend image does not build the bundle"

    services = load_compose()["services"]

    # Caddy's /srv/frontend must come from the volume the build writes, never from the checkout.
    caddy_mounts = [m for m in services["caddy"].get("volumes", []) if isinstance(m, str)]
    served = [m for m in caddy_mounts if ":/srv/frontend" in m]
    assert len(served) == 1, f"expected exactly one /srv/frontend mount, found {served!r}"

    source = served[0].split(":")[0]
    assert not source.startswith((".", "/")), (
        f"caddy serves /srv/frontend from {source!r}, which is a host path. It must be the named "
        f"volume the frontend-build container writes - a bind-mounted checkout changes the live "
        f"site on `git pull`, before any deliberate deploy (decision 6)."
    )
    assert served[0].endswith(":ro"), "caddy mounts the bundle writable"

    for name, service in services.items():
        for mount in service.get("volumes", []) or []:
            if isinstance(mount, str):
                host = mount.split(":")[0]
                assert "frontend/dist" not in host and "frontend\\dist" not in host, (
                    f"{name} bind-mounts the built bundle from the checkout: {mount!r}"
                )
