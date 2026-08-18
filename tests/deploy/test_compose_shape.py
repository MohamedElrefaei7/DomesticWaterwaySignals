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
    LONG_LIVED_SERVICES,
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


def test_timescaledb_healthcheck_requires_tcp_and_credentials():
    """The database probe must be one `initdb`'s temporary server cannot satisfy.

    THE FAILURE THIS REPLACES IS A HEALTHCHECK THAT ANSWERS YES ABOUT THE WRONG SERVER. The
    official Postgres entrypoint runs `initdb` and then starts a TEMPORARY server to apply
    initialisation. That server listens only on a unix socket in a private directory - there is no
    TCP listener at all - and `pg_isready` with no `-h` connects over exactly that socket. So a
    probe that looks correct, and that carries `-U` and `-d` and therefore looks careful, reports
    healthy while the database everything else must reach does not yet exist.

    `api` gates on `condition: service_healthy`, so this made the API's startup ordering decorative
    from Phase 2 onward. It is timing-dependent, which is why nobody has seen it: a slow initdb
    under load is when the API gets released against a server that is still initialising, and the
    symptom is a connection error from an application that had just been told the database was
    ready. CLAUDE.md § 13 already knows this shape - "readiness is confirmed by a real query from
    outside, not by pg_isready" is written there about the restore test's throwaway container, and
    production was not held to it.

    Three properties, each asserted separately so the failure says which one is missing:
      a HOST, so the probe crosses TCP rather than the private socket;
      the real USER and DATABASE, so it is an authenticated session against the real database;
      a QUERY, so the server answers rather than merely accepting the connection.

    NOT SATISFIED BY A LONGER start_period, and that is asserted too. A longer grace makes the race
    rarer rather than absent, and an intermittent startup failure that only appears under load is
    the version of this bug that costs a session.
    """
    compose = load_compose()
    service = compose["services"]["timescaledb"]

    healthcheck = service.get("healthcheck")
    assert healthcheck, (
        "the timescaledb service declares no healthcheck at all, while `api` depends on it with "
        "`condition: service_healthy` - which Compose then cannot evaluate"
    )

    test = healthcheck["test"]
    assert isinstance(test, list) and test, f"healthcheck.test is not a non-empty list: {test!r}"
    command = " ".join(test)

    assert "pg_isready" not in command, (
        f"the probe uses pg_isready: {command!r}\n"
        f"initdb runs a temporary server on a unix socket in a private directory, and pg_isready "
        f"without -h connects over exactly that socket - so it reports healthy about a server "
        f"that is not the one anything else can reach. Use a real query over TCP."
    )

    assert re.search(r"-h\s+\S+", command), (
        f"the probe names no host: {command!r}\n"
        f"Without -h the client uses the unix socket, which is the one transport initdb's "
        f"temporary server offers. The host is what makes this probe unsatisfiable by it."
    )

    assert re.search(r"-U\s+\S+", command), f"the probe names no user: {command!r}"
    assert re.search(r"-d\s+\S+", command), f"the probe names no database: {command!r}"
    assert "POSTGRES_USER" in command and "POSTGRES_DB" in command, (
        f"the probe hardcodes its user or database rather than reading the same variables the "
        f"server is configured from: {command!r}. Two copies of one fact drift silently."
    )

    assert re.search(r"-c\s+'?\s*SELECT", command, re.IGNORECASE), (
        f"the probe issues no query: {command!r}\n"
        f"Accepting a connection and answering a query are different claims, and a server can do "
        f"the first while still starting up."
    )

    # The grace is a bound on a legitimately slow start, never the fix for the race above.
    start_period = healthcheck.get("start_period")
    assert start_period == "30s", (
        f"healthcheck.start_period is {start_period!r}, not 30s. If it was lengthened to make a "
        f"startup failure go away, the failure is still there and is now rarer - which is worse, "
        f"because an intermittent one that appears only under load is the expensive kind."
    )


# ---------------------------------------------------------------------------------------------
# Phase 12 — the scheduler service
# ---------------------------------------------------------------------------------------------
#
# The scheduler is the first service in this stack that needed something the stack could not give
# it without a security decision, so its tests are about what it does NOT have as much as what it
# does. Every one of them is a line that would look harmless in a diff.

# Every spelling of the Docker daemon's socket that a bind mount could name. The path is the one
# that matters; the TCP forms are here because `-H tcp://` reaches the same daemon and somebody
# reaching for a workaround reaches for those next.
DOCKER_SOCKET_SPELLINGS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "docker.sock",
    "docker.socket",
)


def test_no_service_mounts_the_docker_socket():
    """STACK-WIDE, not scheduler-only, and that scope is the whole point of the test.

    Mounting /var/run/docker.sock is root-equivalent on the host: anything that can talk to the
    daemon can start a privileged container with / bind-mounted. A compromise of the container
    whose job is running scheduled Python would become a compromise of the instance - and it also
    voids `application containers run as a non-root user` in substance while satisfying it in
    form, because the process is uid 10001 and can become root whenever it likes.

    A scheduler-only assertion would read as sufficient and would invite the mount onto `api`
    instead, where nothing was watching. The reason applies equally to every service, so the
    assertion does too - and the walk asserts it saw services, because a check over an empty
    collection is green forever and watching nothing (CLAUDE.md § 21).

    The whole service mapping is walked, not just `volumes:`, because `privileged: true`,
    `devices:` and a `-H tcp://` command flag all reach the same daemon by another door.
    """
    compose = load_compose()
    services = compose["services"]
    assert services, "walked no services - this test would pass over nothing"

    offenders = []

    def walk(node, trail):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}[{index}]")
        elif isinstance(node, str):
            for spelling in DOCKER_SOCKET_SPELLINGS:
                if spelling in node:
                    offenders.append((trail, node))

    for name, service in services.items():
        walk(service, f"services.{name}")
        assert service.get("privileged") is not True, (
            f"{name} runs privileged, which is the socket's blast radius without the socket"
        )

    assert offenders == [], (
        f"a service mounts or reaches the Docker daemon: {offenders}. That is root on the host. "
        f"pg_dump and pg_restore live inside Dockerfile.scheduler precisely so that nothing here "
        f"needs it (CLAUDE.md § 22)."
    )


def test_restart_policy_set_is_exact_with_scheduler():
    """The set of services carrying `unless-stopped`, compared by EQUALITY.

    Stated as a set rather than as a per-service loop (which the test above already does), because
    the two fail differently and both failures matter. The loop says "this service has the wrong
    policy"; this says "the PARTITION changed" - a one-shot that acquired a restart policy, or a
    long-lived service that lost one, and either is a different kind of mistake.

    `frontend-build` is excluded because it exits on purpose. Under a policy that restarts a
    container whenever it is not running, an exiting container is an infinite rebuild loop - and
    it would look correct in the Compose file. The scheduler is NOT in that category: it is a
    process that runs until it is stopped.
    """
    compose = load_compose()
    observed = {
        name for name, service in compose["services"].items()
        if service.get("restart") == "unless-stopped"
    }

    assert observed == LONG_LIVED_SERVICES, (
        f"the services carrying `restart: unless-stopped` are {sorted(observed)}, expected "
        f"{sorted(LONG_LIVED_SERVICES)}. A one-shot under this policy is a rebuild loop; a "
        f"long-lived service without it does not come back after a crash or a reboot."
    )
    assert observed | ONE_SHOT_SERVICES == EXPECTED_SERVICES, (
        f"the two sets do not partition the stack: {sorted(observed)} | "
        f"{sorted(ONE_SHOT_SERVICES)} != {sorted(EXPECTED_SERVICES)}. A service in neither has no "
        f"stated restart behaviour at all."
    )


def test_published_port_set_unchanged():
    """Adding a fifth service changed nothing about what is reachable from outside.

    This restates the set equality asserted at the top of this file, and the restatement is the
    finding rather than a duplicate: if adding a service had required EDITING that assertion,
    something would be wrong with the service. The scheduler serves nothing and nothing connects
    to it.
    """
    compose = load_compose()
    assert set(compose["services"]) == EXPECTED_SERVICES, (
        "the service set is not the expected five - this test is not measuring what it says"
    )
    assert "scheduler" in compose["services"], "no scheduler service to have added a port"

    assert published_ports(compose) == PUBLIC_PORTS, (
        f"the published set is {sorted(published_ports(compose))}, expected {sorted(PUBLIC_PORTS)}"
    )


def test_scheduler_has_no_ports_key():
    """Named separately from the set check because it fails differently.

    The set check says the stack's total exposure changed; this names the service, which is what
    an operator reading a red test at speed actually needs.
    """
    scheduler = load_compose()["services"]["scheduler"]
    assert not scheduler.get("ports"), (
        f"the scheduler publishes {scheduler.get('ports')!r}. It serves nothing; a published port "
        f"on it is a route into the container that runs every job, with no edge in front of it."
    )
    assert scheduler.get("network_mode") != "host", "the scheduler uses host networking"


def test_scheduler_depends_on_timescaledb_healthy():
    """`service_healthy`, not `service_started`, and Stage B is what made that mean anything.

    The healthcheck was `pg_isready` through Phase 11, which answers YES to the temporary server
    `initdb` runs on a private unix socket - so a `service_healthy` gate released dependents
    against a database that did not exist yet. It is now a real query over TCP as the real user,
    so this dependency is load-bearing for the first time.

    It matters more for the scheduler than it did for the API. The API's first request would fail
    and be retried; the scheduler's first act on a cold start is to open the persistent job store,
    and a failure there is a process that exits into a restart loop.
    """
    scheduler = load_compose()["services"]["scheduler"]
    depends = scheduler.get("depends_on")
    assert isinstance(depends, dict), (
        f"the scheduler declares depends_on as {depends!r}. The short list form cannot express a "
        f"condition, so it waits only for the container to start."
    )

    gate = depends.get("timescaledb")
    assert gate and gate.get("condition") == "service_healthy", (
        f"the scheduler depends on timescaledb as {gate!r}, expected "
        f"`condition: service_healthy`."
    )


def test_scheduler_mounts_backup_staging_path():
    """Both /mnt/data paths the jobs write to, bind-mounted from the DATA volume.

    /mnt/data/backups is where pg_dump writes the archive before it is verified and uploaded;
    /mnt/data/restore-test is where the monthly job downloads an archive FROM S3 before restoring
    it. Neither may be a named Docker volume: those live under /var/lib/docker on the ROOT disk,
    which is the disk this project went to some trouble not to put anything large on. A dump that
    fills root takes the instance down.

    THE CONTAINER RUNS AS uid 10001 AND DOCKER CREATES A MISSING BIND-MOUNT SOURCE AS root:root,
    so an absent directory silently becomes an unwritable one - and the failure would arrive after
    pg_dump had already been invoked. Provisioning creates them with the right ownership and
    app/orchestration/backup.py asserts writability before dumping; this only asserts the mounts
    exist and come from the right disk.
    """
    scheduler = load_compose()["services"]["scheduler"]
    mounts = [m for m in (scheduler.get("volumes") or []) if isinstance(m, str)]
    assert mounts, "the scheduler mounts nothing - it has nowhere to write an archive"

    for expected in ("/mnt/data/backups", "/mnt/data/restore-test"):
        matching = [m for m in mounts if m.split(":")[1:2] == [expected]]
        assert len(matching) == 1, (
            f"expected exactly one mount at {expected}, found {matching!r} among {mounts!r}"
        )
        source = matching[0].split(":")[0]
        assert source.startswith("/mnt/data/"), (
            f"the scheduler's {expected} comes from {source!r}. It must be a bind mount under "
            f"/mnt/data - the separate EBS volume - not a named volume on the root disk."
        )
        assert not matching[0].endswith(":ro"), (
            f"{matching[0]!r} is mounted read-only. The job writes an archive into it."
        )


def test_scheduler_gets_a_container_reachable_database_url_and_no_aws_keys():
    """The host inside the container is `timescaledb`, and no AWS credential is passed at all.

    Loopback is this container's own namespace, where nothing listens on 5432; `.env`'s
    DATABASE_URL still points at localhost because host-side tooling needs it (the migration
    runner cannot move into a container - the images deliberately do not contain `migrations/`).
    So the container's URL is assembled here from the POSTGRES_* variables rather than passed
    through, which also means no fourth copy of the password in `.env` for the preflight agreement
    gate to not be checking.

    The AWS half is the one that would be quietest if it went wrong: a key pair in `.env` works,
    so nothing fails, and the instance role's scoping - which grants no delete action of any kind
    - is silently replaced by whatever the key can do.
    """
    scheduler = load_compose()["services"]["scheduler"]
    environment = scheduler.get("environment")
    assert isinstance(environment, dict) and environment, (
        "the scheduler declares no environment mapping"
    )

    url = environment.get("DATABASE_URL")
    assert url is not None, "the scheduler is not given DATABASE_URL"
    assert "@timescaledb:5432/" in url, (
        f"the scheduler's DATABASE_URL is {url!r}. Inside the compose network the host is the "
        f"service name; loopback is this container's own namespace."
    )
    assert "${POSTGRES_PASSWORD:?" in url, (
        f"the scheduler's DATABASE_URL does not take the password from POSTGRES_PASSWORD in the "
        f"required `:?` form: {url!r}. A literal would be a second copy of the secret in a "
        f"committed file; a `:-` default would start a container with an empty password."
    )

    bucket = environment.get("BACKUP_BUCKET")
    assert bucket is not None and "${BACKUP_BUCKET:?" in bucket, (
        f"BACKUP_BUCKET is passed as {bucket!r}; it must use the `:?` form so an unset value stops "
        f"the container rather than failing inside the nightly job at 03:00."
    )

    for key in environment:
        assert not key.startswith("AWS_"), (
            f"the scheduler is given {key}. Credentials come from the instance role over IMDS; an "
            f"AWS key in .env works, so nothing fails - it just quietly replaces the policy that "
            f"grants no delete action of any kind."
        )
