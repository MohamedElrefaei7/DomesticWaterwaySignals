"""The shape of the Compose stack — what is published, what is pinned, what restarts, where the
certificates live.

This is the file that guards the change in threat model. Everything in it is about a line that
would look harmless in a diff and would put something on the public internet, or take something
off the data volume, or leave a container running an image nobody chose.
"""

from __future__ import annotations

import re

from . import (
    DIGEST_RE,
    REPO_ROOT,
    ENV_EXAMPLE_PATH,
    EXPECTED_SERVICES,
    ONE_SHOT_SERVICES,
    image_references,
    load_compose,
    published_ports,
    read_artifact,
)

# 80 and 443. Not "at least" 80 and 443 — the whole stack's published set, compared by equality,
# the same discipline the security group's ingress allowlist and ufw's port set are asserted with
# (CLAUDE.md § 8, § 11). A denylist of forbidden ports passes the day somebody publishes 8001.
PUBLIC_PORTS = {80, 443}


def test_published_ports_across_the_stack_are_exactly_80_and_443():
    """Decision 2, by exact set equality across every service.

    The tempting mistake this catches is publishing 8000 on the api service "for debugging". A
    published container port is DNAT'd and traverses FORWARD, so it is reachable through the
    DOCKER-USER chain's published-port path — it bypasses Caddy, TLS, the security headers, and
    anything else that lives at the edge. In the Compose file it is one short line that reads like
    a convenience.
    """
    compose = load_compose()
    observed = published_ports(compose)

    assert observed == PUBLIC_PORTS, (
        f"published host ports across the stack are {sorted(observed)}, expected "
        f"{sorted(PUBLIC_PORTS)}. Only the reverse proxy publishes; everything else is reachable "
        f"over the Compose network only."
    )


def test_the_api_service_publishes_no_ports():
    """Stated separately from the set equality above because it fails differently.

    The set check tells you the stack's total exposure changed. This one names the service, which
    is what an operator reading a red test at speed actually needs.
    """
    api = load_compose()["services"]["api"]
    assert not api.get("ports"), (
        f"the api service publishes {api.get('ports')!r}. Caddy reaches it at api:8000 over the "
        f"Compose network; a published port is a second route in that has no TLS in front of it."
    )


def test_timescaledb_still_publishes_no_ports():
    """Unchanged from Phase 2, and re-asserted here because this is the commit that adds three
    services to the file the rule lives in.

    The dev override that publishes 5432 on loopback is out-of-repo and stays that way.
    """
    db = load_compose()["services"]["timescaledb"]
    assert not db.get("ports"), (
        f"timescaledb publishes {db.get('ports')!r}. CLAUDE.md § 6 marks it internal-only."
    )


def test_no_service_depends_on_a_published_postgres_port():
    """Decision 1. Nothing in the committed stack needs the dev override to exist.

    The failure this prevents is subtle in a way the others are not: the stack would work
    perfectly on the machine that has the override file and fail on any machine that does not,
    with a connection error pointing at the database rather than at the missing file. Every
    loopback spelling is checked, plus the two escape hatches that reach the host without naming
    it — `network_mode: host` and a `host-gateway` extra host.
    """
    compose = load_compose()

    forbidden = ("localhost", "127.0.0.1", "host.docker.internal", "host-gateway")
    offenders = []

    def walk(node, trail):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}[{index}]")
        elif isinstance(node, str):
            # `${VAR:?message}` carries a human-readable failure message, and the API's says the
            # host must be timescaledb and NOT localhost. Scanning the message would flag the
            # sentence that exists to prevent the mistake, so the message is dropped and the
            # substitution is compared as a variable reference.
            text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):[?-][^}]*\}", r"${\1}", node)
            for needle in forbidden:
                if needle in text:
                    offenders.append((trail, node))

    for name, service in compose["services"].items():
        # The api healthcheck talks to 127.0.0.1 INSIDE its own container, which is the one place
        # loopback is correct — it is the container's own namespace, not the host's.
        checked = {k: v for k, v in service.items() if k != "healthcheck"}
        walk(checked, f"services.{name}")

    assert offenders == [], (
        f"a service reaches the host rather than the Compose network: {offenders}. The published "
        f"5432 lives in an out-of-repo override; nothing committed may require it."
    )

    for name, service in compose["services"].items():
        assert service.get("network_mode") != "host", f"{name} uses host networking"


def test_the_api_service_reaches_postgres_by_service_name_not_localhost():
    """Decision 1, in the three places the answer actually lives.

    The URL itself is a secret and is in .env, which is gitignored — so a check that only read
    docker-compose.yml would be looking at a variable reference and calling it verified, which is
    a vacuous pass over the interesting part. What is checkable, and is checked:

      1. the api service takes API_DATABASE_URL in the required `:?` form, so an unset value is a
         startup failure rather than a container connected to nothing;
      2. the api service is NOT handed DATABASE_URL — app/api/dependencies.py falls back to it
         with a warning, and inside this container there must be nothing to fall back to, or the
         public API can silently connect as the database owner;
      3. .env.example, which IS committed, documents the host as `timescaledb` and not loopback.

    Point 3 is the mutation target: changing the documented host to localhost turns this red.
    """
    api = load_compose()["services"]["api"]
    environment = api.get("environment")
    assert isinstance(environment, dict) and environment, (
        "the api service declares no environment mapping"
    )

    url_spec = environment.get("API_DATABASE_URL")
    assert url_spec is not None, "the api service is not given API_DATABASE_URL"
    assert "${API_DATABASE_URL:?" in url_spec, (
        f"API_DATABASE_URL is passed as {url_spec!r}. It must use the `:?` form so an unset value "
        f"stops the container rather than starting one that cannot connect."
    )

    assert "DATABASE_URL" not in environment, (
        "the api container is given DATABASE_URL. That is the owner role's URL and the API's "
        "fallback; handing it to the public-facing container defeats the read-only role."
    )

    example = read_artifact(ENV_EXAMPLE_PATH)
    documented = [
        line for line in example.splitlines()
        if line.strip().startswith("API_DATABASE_URL=")
    ]
    assert len(documented) == 1, (
        f".env.example declares API_DATABASE_URL {len(documented)} times; expected exactly one"
    )

    value = documented[0].split("=", 1)[1]
    assert "@timescaledb:5432/" in value, (
        f".env.example documents API_DATABASE_URL as {value!r}. Inside the Compose network the "
        f"host is the service name `timescaledb`; loopback is the api container's own namespace, "
        f"where nothing listens on 5432."
    )
    assert "localhost" not in value and "127.0.0.1" not in value


def test_every_image_is_pinned_by_digest():
    """All four services, whether they pull an image or build one.

    Two of the four build, so half the answer is in the Dockerfiles' FROM lines rather than in
    this file — a check that read only `image:` keys would report a fully pinned stack while the
    api and the frontend built on whatever `python:3.12-slim` resolved to that morning
    (CLAUDE.md § 5).

    The placeholder digests are NOT rejected here. They are 64 zeros, they cannot resolve, and
    that is their job: a missed resolution step fails at `docker build` with a manifest error
    rather than falling back to a floating tag (CLAUDE.md § 12). Rejecting them would make this
    suite red on every clean checkout, which is a test everybody learns to ignore.
    """
    references = image_references(load_compose())

    unpinned = {
        service: [ref for ref in refs if not DIGEST_RE.search(ref)]
        for service, refs in references.items()
    }
    unpinned = {service: refs for service, refs in unpinned.items() if refs}

    assert unpinned == {}, (
        f"floating image references: {unpinned}. Every image in the stack is pinned by digest and "
        f"resolved on the machine that runs it. `latest` on a database image resolved to two "
        f"different TimescaleDB versions three months apart on the prior project."
    )

    # tag@digest, never digest alone (CLAUDE.md § 13). The digest is the pin; the tag is how an
    # operator works out what to `docker pull` in order to re-derive it when the pin fails.
    for service, refs in references.items():
        for ref in refs:
            name_and_tag = ref.split("@", 1)[0]
            assert ":" in name_and_tag.rsplit("/", 1)[-1], (
                f"{service} references {ref!r} with a digest and NO TAG. Nobody can work out what "
                f"to pull to recover this digest."
            )


def test_every_long_lived_service_has_restart_unless_stopped():
    """...and the one service that is not long-lived says so explicitly.

    RENAMED FROM the brief's `test_every_service_has_restart_unless_stopped`, and the rename is
    the finding. `frontend-build` runs to completion and exits 0; `restart: unless-stopped`
    restarts a container whenever it is not running, regardless of exit code, so the literal rule
    would have made the frontend rebuild in a loop forever — and it would have looked correct in
    the Compose file and in a test named after it.

    So the partition is asserted rather than the blanket rule: every long-lived service restarts,
    and the one-shot declares `restart: "no"` — quoted, because unquoted `no` is YAML's boolean
    false and would parse as something Compose does not accept.
    """
    compose = load_compose()
    assert set(compose["services"]) == EXPECTED_SERVICES

    for name, service in compose["services"].items():
        policy = service.get("restart")
        if name in ONE_SHOT_SERVICES:
            assert policy == "no", (
                f"{name} runs to completion and declares `restart: {policy!r}`. Under "
                f"`unless-stopped` an exiting container is restarted forever - a rebuild loop."
            )
        else:
            assert policy == "unless-stopped", (
                f"{name} declares `restart: {policy!r}`, expected `unless-stopped` "
                f"(CLAUDE.md § 6)."
            )


def test_the_one_shot_build_is_gated_before_the_proxy_starts():
    """caddy waits for the bundle to exist.

    Without the completion gate the file server starts in front of an empty volume and serves 404s
    from a site that is running perfectly — CLAUDE.md § 2's theme 1 rendered as a web page.
    """
    caddy = load_compose()["services"]["caddy"]
    depends = caddy.get("depends_on")
    assert isinstance(depends, dict), "caddy declares no depends_on mapping"

    gate = depends.get("frontend-build")
    assert gate and gate.get("condition") == "service_completed_successfully", (
        f"caddy depends on frontend-build as {gate!r}. It must wait for completion, not for start."
    )


def test_the_caddy_data_directory_is_on_the_data_volume():
    """Decision 9. /data holds the certificates AND the ACME account key.

    A named Docker volume lives under /var/lib/docker on the ROOT disk, which is the disk this
    project went to some trouble not to store anything durable on; a container-internal path is
    worse still, because an image change discards it. Either way the loss is not "regenerate a
    file" — it is re-issuance against an endpoint that rate-limits per domain per week, which is
    how a site ends up unable to get a certificate for days.
    """
    caddy = load_compose()["services"]["caddy"]
    mounts = caddy.get("volumes") or []
    assert mounts, "the caddy service mounts nothing"

    data_mounts = [m for m in mounts if isinstance(m, str) and m.split(":")[1:2] == ["/data"]]
    assert len(data_mounts) == 1, (
        f"expected exactly one mount at /data, found {data_mounts!r} among {mounts!r}"
    )

    source = data_mounts[0].split(":")[0]
    assert source.startswith("/mnt/data/"), (
        f"caddy's /data comes from {source!r}. It must be a bind mount under /mnt/data - the "
        f"separate EBS volume - not a named volume on the root disk and not container-internal."
    )


# REMOVED: test_the_compose_file_still_names_timescaledb_first.
#
# It was a stopgap for a gap that no longer exists. `verify/preflight.py` gate 1 read the FIRST
# `image:` line in this file and checked that one reference, so which image was gated was decided
# by file order and reordering the services would silently re-point the only live digest check.
# The test was the tripwire for that reorder.
#
# Gate 1 now enumerates EVERY `image:` line here and EVERY `FROM` line in every Dockerfile, and
# reports each by name — so service order is no longer load-bearing and a tripwire guarding it
# asserts nothing. Keeping it would be worse than deleting it: a test named for a property the
# system no longer depends on is a green check that teaches the next reader a rule that is not
# true. See verify/preflight.py::enumerate_image_sites and
# tests/verify/test_preflight_checks.py::test_gate_one_enumerates_every_image_reference_in_the_stack.


def test_api_service_runs_single_uvicorn_worker():
    """EXACTLY ONE uvicorn worker, asserted structurally (CLAUDE.md § 22).

    The rate limiter's bucket state is IN-PROCESS, so `--workers 4` silently quadruples every
    limit: four processes, four independent stores, four full quotas per client. Nothing in the
    code changes, every number still reads correctly, and the only symptom is that the limit is
    four times what it says.

    Shared state would mean a database write path, which § 20's read-only contract forbids. So the
    constraint is one worker, and the guard is this test.

    Checked in BOTH places a worker count can appear - the Dockerfile CMD and a Compose `command:`
    override - because the override wins and a check that read only the image would miss it.
    """
    sources = {
        "Dockerfile.api": (REPO_ROOT / "Dockerfile.api").read_text(encoding="utf-8"),
        "docker-compose.yml": (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
    }
    assert any("uvicorn" in text for text in sources.values()), (
        "neither Dockerfile.api nor docker-compose.yml mentions uvicorn - this test is reading "
        "the wrong files and would pass over anything"
    )

    for name, text in sources.items():
        for match in re.finditer(r"--workers[\"'\s,=]+(\d+)", text):
            count = int(match.group(1))
            assert count == 1, (
                f"{name} runs uvicorn with --workers {count}. The rate limiter's buckets are "
                f"per-process, so every limit is silently multiplied by {count}."
            )
