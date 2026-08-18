# CONTEXT.md — current state

This is the **log**: where the project is now, what is open, and `§ Up Next`. Stable contracts live
in `CLAUDE.md`, which outranks this file.

**Last updated:** 2026-08-17. Phase 11 is code-complete and unapplied.

## Where everything lives

This file was ~3,500 lines on 2026-08-16 and had stopped being readable, which is how it drifted
three commits behind reality earlier in the project. It was split on 2026-08-17. **Every span was
moved verbatim** — nothing was summarized, and no finding was softened in the move.

| File | What is in it |
|---|---|
| **`CONTEXT.md`** (this file) | Current state, open questions, `§ Up Next`, standing items, process notes |
| **`docs/findings.md`** | Every measured finding, by domain, with its date and method |
| **`docs/decisions.md`** | Decisions taken, the reason for each, and the alternatives rejected |
| **`docs/phase-log.md`** | The per-phase verification blocks, chronological, Phase 1 → Phase 10 |
| **`docs/query-outputs.md`** | Verbatim query output — the sweep tables, the gate results, the live responses |

**Precedence is unchanged:** `CLAUDE.md` > this file > `docs/` > any handoff or summary document.
The `docs/` files are the log's own detail, not a second authority; where one of them disagrees with
this file, this file is the current state and that one is the record of a moment.

---

## Current state

**PHASE 10 IS VERIFIED ON THE INSTANCE, 2026-08-17. `https://bargeanalysis.com` IS LIVE AND
PUBLIC.** Certificate issued on the first attempt, validated by Let's Encrypt from five distinct
IPs; `http://` redirects with a 308; the stack survived a real reboot and came back unattended in
≈30 seconds; and `nc` to 5432 and 8000 **hangs rather than refusing**, which is the first
externally-visible proof of the `DOCKER-USER` terminal `DROP` rule. Full block in
`docs/phase-log.md`, evidence in `docs/query-outputs.md § Phase 10`.

**Five containers' worth of pins, all resolved on the instance.** Four placeholder digests were
replaced (`python:3.12-slim`, `node:22-bookworm-slim` ×2 stages each, `caddy:2-alpine`); the
TimescaleDB digest is unchanged. All four were **hand-edited**, because `verify/preflight.py`
gate 1 wrote only the first compose `image:` line at the time. **That gap was closed in
`d9acd96`** — gate 1 enumerates every `image:` line and every Dockerfile `FROM`, **six references
across three files**, and `--write-digest` rewrites all of them. Phase 11 (`f29d734`) added the
four conditions the enumeration did not cover: interpolated references, `FROM scratch`,
digest-only references, and a pin whose tag has moved.

**What is publicly reachable:** the built React bundle (four views) and all eight Phase 8 GET
endpoints. **Unauthenticated, deliberately, and defensible on three independently checkable
grounds** — no non-GET route is declared, the database role is `SELECT`-only and *has been observed
refusing a `DELETE`*, and no response body carries a secret. **It is defensible as a decision and
not as an inheritance**: a future session adding a write endpoint is voiding its premise, not
extending it (`CLAUDE.md § 22`, and `docs/decisions.md § Phase 10`).

**The request-volume exposure is closed in code** (`587d6e2`), in the application rather than at
the edge, under a stated `CLAUDE.md § 22` amendment. **Static assets remain unlimited at the edge**
— an accepted residual exposure, recorded as a decision. Not yet deployed.

**Still degraded, still honestly.** `/api/health` reports `degraded: true` because no scheduler has
run continuously across sessions. Phase 12 containerizes the worker and owns it.

**What the project can claim about the river:** nothing quotable yet. The sweep scanned **6,966**
pairs and **1** passed correction, contemporaneously at `lag_days = 0`, with a **negative**
statistic. The analog gate passed on both labelled events at medians of **+7%** and **+10%** across
ranges that span zero, on analogs drawn entirely from **2015–2022**. The three questions that stand
between that and a quotable sentence are below and all three are the human's.

---

## The thesis (one paragraph, so it stays in view)

River stage on the Mississippi system physically constrains how much grain a barge can carry, and
that constraint propagates into published barge freight rates within days. Water falls → draft
restrictions → light-loading and shorter tows → effective capacity drops while harvest volume does
not → rates rise → Gulf basis widens. Every arrow is mechanical, so a *broken* relationship is
informative rather than noise. Fast feature: 15-minute USGS gauge stage. Slow target: USDA's weekly
barge freight rate as percent of tariff. The 2022 and 2023 low-water events are labelled natural
experiments to validate against.

**Résumé framing that governs tradeoffs:** a real-time inland-waterway signal system on a single
corridor, where a fast-moving physical constraint leads a slow-moving published index, with honest
confidence gating that says "insufficient history" rather than manufacturing conviction.

### The thesis, as Phase 3 leaves it

Phase 3 changed what this project can honestly claim. Restated plainly, because the framing above
no longer matches the data:

- **The feature is DISCHARGE (`00060`), not stage.** Stage is unavailable via USGS IV at Memphis
  and Vicksburg, and deriving it from a rating curve was rejected as fabrication, not deferred
  (`CLAUDE.md § 14`).
- **Corridor depth is uneven: one deep site (St. Louis, 1990→) and three shallower**, with roughly
  **sixteen years of four-site overlap (2010→)**. Both labelled low-water events — 2022 and 2023 —
  are covered at all four sites.
- **Any baseline needing pre-2004 history runs on St. Louis alone.** Not "St. Louis and Memphis":
  Memphis serves nothing between 1994 and 2014.

---

## Phase 12 — the scheduler runs in production

**THE FINDING THAT SCOPES THE PHASE: THE SCHEDULER HAS NEVER RUN IN PRODUCTION.** `job_runs` holds
two probe rows from 2026-08-11 written by `verify/` harnesses and nothing else. `apscheduler_jobs`
exists - created by `SQLAlchemyJobStore`'s own DDL during a Phase 2 run - and holds **zero rows**.
There is no `dws-scheduler.service`; the units are `dws-external-interface`, `dws-docker-firewall`
and `dws-stack`. Every ingest row in the database arrived by manual backfill. So this is not
"containerise an existing process"; it is "make the scheduler exist as a running thing for the
first time". The empty job store is luck: the first real start is from zero entries, with no
past-due backlog to reason about.

### Part 1 — `Dockerfile.scheduler`, and the decision that shapes Parts 1, 3 and 4

**THE SCHEDULER CONTAINER DOES NOT GET THE DOCKER SOCKET.** Mounting it is root-equivalent on the
host, so a compromise of the container whose job is running scheduled Python becomes a compromise
of the instance - a large, permanent widening of blast radius for a convenience. `pg_dump` and
`pg_restore` are installed into the image instead, pinned to the server's major. The cost is a
version pin in two places (`Dockerfile.scheduler` and the compose tag) which can drift; that cost
is accepted because the drift is **detectable** - a preflight gate reads the files, and a runtime
check reads the running binary - **whereas the socket trades a detectable problem for an
undetectable one.** Recorded as a contract in `CLAUDE.md § 3` and `§ 22`.

**THE CLIENT VERSION IS AN UNRESOLVED PLACEHOLDER AND THE BUILD WILL FAIL UNTIL A HUMAN RESOLVES
IT.** `postgresql-client-16=16.0-0.PLACEHOLDER.pgdg120+0` cannot resolve against PGDG, which is the
point: it is the digest placeholder's discipline applied to a package (`CLAUDE.md § 12`). It must
be resolved **on the instance**, never from a laptop, with the `apt-cache madison` command in
`Dockerfile.scheduler`'s header, and **the resolved version recorded here**. Until then
`verify/preflight.py` reports the new gate as FAIL - by value, not by form - naming the command.
**Resolved version: NOT YET RESOLVED.**

**The PGDG signing key is verified by fingerprint** - `B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8`,
the PostgreSQL Global Development Group's Debian repository key - against a literal in the
Dockerfile, and the build exits non-zero on a mismatch. The fingerprint is written down a second,
independent time in `tests/deploy/test_client_server_major_agreement.py`: a test that read the value
out of the file it is checking would assert only that *some* fingerprint is present.

**Preflight now enumerates eight references across four files** (was six across three).
`verify/phase11/stage_e.py`'s `EXPECTED_REFERENCES`/`EXPECTED_FILES` moved with it, in the same
commit, and the test named for the old number was renamed rather than left describing a count it no
longer asserts.

**Measured while writing the gate, and it is `§ 23` in miniature:** the first version searched the
raw Dockerfile text for `postgresql-client-NN` and found the header sentence "Debian bookworm ships
postgresql-client-15, so 16 comes from PGDG" *before* the instruction - reporting a correct file as
pinning client 15 with no version. The parser strips comments; the guard against the repair
somebody reaches for (a weaker pattern) is an **inverted mutation** test that puts two plausible
pins in comments and requires the parser to stay green.

---

### Part 2 — the fifth Compose service, and the scheduler runs in production for the first time

`scheduler` builds from `Dockerfile.scheduler`, publishes nothing, mounts no socket, waits on
`timescaledb: service_healthy`, and carries `restart: unless-stopped`. **The published-port set
across the stack is unchanged at `{80, 443}` and its assertion was not edited** - if adding a
service had required editing that assertion, something would have been wrong with the service.

**No `dws-scheduler.service`, deliberately.** `dws-stack.service` already carries
`RequiresMountsFor=/mnt/data` and `Requires=docker.service`, and the containers' own restart
policies do the supervision. A second unit would restate the mount guarantee, and the second copy
is the one that drifts.

**`.env`'s `DATABASE_URL` STAYS ON `localhost`, WHICH IS NOT WHAT PHASE 11 EXPECTED.** The note in
`.env.example` predicted that containerizing the worker would move it to `timescaledb:5432` and
retire the out-of-repo override publishing 5432 on loopback. It does not: **host-side tooling still
needs a host-reachable DSN** - the migration runner, `verify/preflight.py`'s migration gate, and
every `verify/phase11` stage connect from the host, and the runner cannot move into a container
because the images deliberately do not contain `migrations/` (`CLAUDE.md § 3`). **The override file
stays.** `.env.example` now says so instead of predicting otherwise.

**The container's `DATABASE_URL` is ASSEMBLED IN `docker-compose.yml` from `POSTGRES_USER` /
`POSTGRES_PASSWORD` / `POSTGRES_DB` with the literal host `timescaledb`** - not passed through, and
not a fourth variable in `.env`. A fourth copy of the password would be the copy
`check_password_agreement` does not compare (it reads `POSTGRES_PASSWORD` against `DATABASE_URL`
only), and the copy nothing checks is the copy that drifts (`§ 13`). Composing from variables that
are already gated adds no copy: the only new literal is the compose-network hostname.

**`BACKUP_BUCKET` joins `.env`** in the `:?` form. It is not a secret; it is in `.env` rather than
in the committed compose file because it carries the AWS account id.

**TWO DIRECTORIES MUST EXIST AND BE OWNED BY uid 10001 BEFORE THE JOBS RUN.** Docker creates a
missing bind-mount source as `root:root`, so an absent directory silently becomes an unwritable
one - and for the backup that failure lands *after* `pg_dump` has been invoked. Run once, as root:

```
sudo install -d -o 10001 -g 10001 -m 0750 /mnt/data/backups /mnt/data/restore-test
```

**Chosen: both belt and braces.** The provisioning command above, *and* an assertion at job start
(Part 3), because a provisioning step nobody ran is indistinguishable from one that ran wrong.

`/mnt/data/restore-test` is mounted as well as `/mnt/data/backups`; the prompt named only the
first, but the monthly restore test downloads its archive from S3 into the second and cannot run
without it.

`deploy.sh` now builds `scheduler` alongside `frontend-build` and `api`, before `up -d`, so a build
failure stops the deploy with the previous stack still running.

**No healthcheck on the scheduler, stated rather than omitted.** There is nothing to probe: it
serves no socket, and "is it doing its job" is a question about `job_runs` and `MAX(ts)`, which the
heartbeat already answers from the data (`§ 4`). A healthcheck that only proved the process was
alive would be exactly the process-liveness signal `§ 4` says not to trust.

---

### Part 3 — `backup.py` invokes the in-image client

The `docker run` path is **deleted, not kept behind a flag**: a retained branch reintroduces the
socket requirement the moment somebody sets the flag, and dead code with a plausible use case is
the code that comes back. Both invocations changed - the dump AND `verify_archive`'s full restore
to `/dev/null` - because both were `docker run`. **What is verified did not change**: a full
restore of every block, exit 0 AND empty stderr.

**Two new guards, in this order, before the counting transaction opens:**

1. **Staging writability, proven by writing a file.** `os.access` consults the real uid against
   the mode bits and knows nothing about a read-only mount, a full filesystem, or an ACL.
2. **`pg_dump --version`'s major == `SHOW server_version_num`'s major**, or the job fails. Read
   from `server_version_num` (an integer: 160010 -> 16, 90600 -> 9) rather than the
   `server_version` display string, which has carried suffixes and a two-part major.

**`PGPASSFILE` stays and an inherited `PGPASSWORD` is now STRIPPED.** libpq prefers `PGPASSWORD`,
so one left in the environment means the 0600 file is silently unused - and the dump still
succeeds, which is what makes it invisible.

**Measured, and it is § 2's theme 2 inside the guard added to close a theme-1 gap:** deleting the
JOB'S CALL to the version check left the test that exercises the function directly **entirely
green**. The function was proven to refuse while nothing proved anything ever asked it. Two
job-level tests now drive the real entrypoint with the database faked out and assert the runner's
call list, so "the job asks" is observed rather than assumed. The first attempt at those two tests
also failed the mutation for the **wrong reason** - `failed to resolve host 'h'`, then
`IndexError` from a too-thin fake connection - and neither counts (`§ 0`).

**`timescaledb_image()` has no caller in `backup.py` any more.** It survives this commit only
because `restore_test.py` still calls it, and goes when that does. Worth knowing: **it would not
work inside the container anyway** - it reads `REPO_ROOT/docker-compose.yml`, and the image copies
`app/` only, so there is no compose file at `/srv`.

**The integration tier's precondition changed from Docker to a postgres CLIENT.** The archive-shape
tests run against any recent client; the three job-level tests skip, with a stated reason, when the
local client's major differs from the server under test - because the job refuses a mismatch by
design, and stubbing the check out would make the only tests that exercise the whole path stop
exercising the guard that path depends on.

**RUN LOCALLY, 2026-08-17, against `timescale/timescaledb:2.26.2-pg16` at the pinned digest with
migrations applied:** 8 of 11 pass with an 18 client (3 skip as designed); **11 of 11 pass with a
pg16 client**, including the three job-level tests - a real `pg_dump`, a real restore-to-`/dev/null`
verification, a real `backups` row, `rows_written` NULL, and the staging directory left empty. The
pg16 client came from a **throwaway image built in the scratchpad and never committed**; the client
version it resolved (`16.15-1.pgdg13+2`) is **NOT the value to commit** - `§ 5` requires that
resolution on the instance.

---

### Part 4 — the restore test uses a throwaway DATABASE

The contract amendment is in the same commit and came first (`CLAUDE.md § 3`): one `DROP` is
permitted, bounded by a name guard asserted **twice** - at creation and again immediately before
the `DROP` - with **two independent conditions**, prefix AND inequality with the connected
database's own name, because a prefix check alone fails open if the prefix is ever empty.

**THE `pre_restore` SEQUENCE, MEASURED RATHER THAN ASSUMED (2026-08-17, TimescaleDB 2.26.2):**

1. In a database created `TEMPLATE template0`, `SELECT timescaledb_pre_restore()` fails with
   **`ERROR: function timescaledb_pre_restore() does not exist`**. The function is owned by the
   extension. **`CREATE EXTENSION timescaledb` must come first.**
2. Doing so installs 2.26.2 - the cluster's own version - so there is no version to reconcile.
3. **Pre-creating does not collide with the archive**: `pg_dump` emits `CREATE EXTENSION IF NOT
   EXISTS timescaledb WITH SCHEMA public`, verified against this project's own dump.
4. The working sequence is `CREATE EXTENSION` -> `pre_restore` -> `pg_restore --exit-on-error` ->
   `post_restore`, and a full round trip compared 17 public tables on both sides.
5. `DROP DATABASE` with one idle session attached returns **`ERROR: database "..." is being
   accessed by other users`** - so `pg_terminate_backend`, scoped to that `datname` and excluding
   this backend, is required rather than defensive.

**This was invisible until now because the `timescale/timescaledb` image's own init scripts create
the extension in `POSTGRES_DB`**, so the throwaway CONTAINER always had it and this code never had
to.

**A REAL DEFECT FOUND WHILE PRESERVING "EVERYTHING ELSE UNCHANGED", AND IT WOULD HAVE FAILED EVERY
RUN.** `assert_read_only_role_cannot_delete` used `SET LOCAL ROLE` on an **autocommit** connection.
`SET LOCAL` is scoped to the enclosing transaction and there is none, so the setting is discarded
at the end of the statement that set it. Measured against a real server: `current_user` stayed
`waterway` and the `DELETE` **succeeded** - which this function reports as *"the restored
`waterway_api` role was permitted to DELETE"*. So the monthly restore test would have failed every
time, **accusing the backup's grants**, with the real cause one layer away in session scoping. A
false failure pointing at the wrong layer. Now `SET ROLE`, with `current_user` **read back before
the DELETE is attempted** - the effect asserted, not the invocation, exactly as
`assert_statistics_exist` does for `ANALYZE`.

**The two coverage losses, open rather than resolved:**

1. **Roles are cluster-wide**, so `waterway_api` already exists in the throwaway and Stage B's
   `create_roles`-from-archive work is a **no-op in production runs**. The code and its tests stay
   - the idempotent guard makes the no-op correct - and **its production path is now untested.**
2. **The fresh-cluster property is gone.** A dump depending on some cluster-level object would
   restore cleanly here and fail on a real rebuild. The job now answers *"does this archive restore
   into this server"*, not *"into a new one"*.

**On failure the throwaway is NOT dropped and the error names it**, with the two statements to
remove it by hand. This inverts the container version, which always tore down after capturing
logs: a container's logs are its whole state, while a database's state IS the database.

**`verify/phase11/stage_h.py` now sweeps `pg_database` for `dws_restore_test_*` rather than
containers for `dws-restore-test-*`.** A container sweep would pass over a host where nothing can
create such a container - green, and watching nothing. Its failure message deliberately does not
assert *which* cause a survivor is: deliberate evidence and a killed run send an operator to two
different places.

**`tests/orchestration/test_commit_helper.py`'s allow-list fired exactly as designed** -
`restore_test_monthly_job` went from 2 raw connections to 4, and the per-function COUNTS are what
said so. A function-name allow-list without counts would have absorbed both new ones in silence,
and one of them issues the only `DROP DATABASE` in this system.

**`tests/source_scan.py` is new**, because the same hazard bit three times in this phase: a
source-scanning guard matching the module's own explanation of what it forbids. It is an AST walk
that excludes docstrings, and it raises rather than returning `[]` when it resolved nothing.

**RUN LOCALLY against the pinned pg16 server with a pg16 client: 145 passed, 2 skipped** (the two
unwritability tests, which detect they are running as root). The restore-test integration tier
restored into a real throwaway database, compared counts, watched the read-only role be refused,
and left **no `dws_restore_test_*` database behind**.

---

### Part 5 — restart recovery, observed as behaviour

`tests/orchestration/test_restart_recovery.py` (integration tier). **Nothing in it asserts a
setting**; the observable is a row in `job_runs`. Real child processes, a real `SQLAlchemyJobStore`,
a real Postgres - because process lifetime is the subject and a harness that stubbed the store, the
scheduler or the decorator would reproduce the original bug's invisibility exactly.

**The outage is SEEDED, not waited out.** A backdated `next_run_time` in `apscheduler_jobs` is what
an outage leaves behind, and seeding it is the only way to get an outage's aftermath into a test
that runs in seconds - `verify/restart_recovery.py` still does the multi-minute real-outage version
and remains the live evidence.

**Rows go to a dedicated test database** (`DATABASE_URL` + the per-test schema reset).
`apscheduler_jobs` is cleaned up explicitly on every exit path, because `register_jobs()` never
removes a job it does not recognise, so a leftover probe would keep firing under whatever scheduler
starts next. **`job_runs` rows are deliberately NOT cleaned up**: the table is append-only by
trigger (`§ 12`).

**The probe interval is 120s and cannot be smaller.** `Cadence.__post_init__` rejects a grace at or
above the interval, and the derivation is `max(60, interval // 2)` - so 121s is the true minimum
and 120s is legal by exactly 60 seconds. A probe needing an exemption from the rule it verifies
would not be verifying much.

#### THE MEASUREMENT THAT CONTRADICTED THE PLAN, AND THREE PLACES IN THIS REPO

**`coalesce=False` does not produce a burst of runs. It produces spurious `missed` rows.**

Measured 2026-08-18, three seeded missed slots, real scheduler, identical seeding:

| setting | `job_runs` rows |
|---|---|
| `coalesce=True` | 1: `success` |
| `coalesce=False` | 3: `missed`, `missed`, `success` |

**One run either way.** The burst cannot happen, and the reason is this project's own contract:
`§ 12` requires `misfire_grace_time` **strictly shorter** than the interval, consecutive slots are
one interval apart, so **at most one missed fire time is ever inside the grace window** - every
older one is skipped as a misfire rather than run.

Three places said otherwise and all three were corrected in the same commit:
`app/orchestration/scheduler.py` ("firing sixteen times in a row against a source that will
rate-limit us for it"), `app/orchestration/cadence.py`, and `verify/restart_recovery.py`.

**The failure this actually prevents is quieter and arguably worse than a burst.** `missed` is
supposed to mean "a scheduled run was lost". With coalescing off it also means "a slot went by
during an outage", so a four-hour outage writes rows claiming two hours of runs were missed when
one run was late - and the heartbeat reads those rows.

**The first draft of the test asserted RUNS and passed over the mutation entirely.** Correcting it
to assert the total row count is what made the guard real; the measurement above is what said which
number to assert.

**A second thing the first draft got wrong:** `test_past_due_job_runs_once_after_restart` asserted
"a row appeared", which a `missed` row satisfies - so a `misfire_grace_time` of 1s left it green.
It now filters to rows that represent the function having actually been called.

**RUN: 5 passed in ~43s** against `timescale/timescaledb:2.26.2-pg16` at the pinned digest.

---

## § Up Next

**THE DEPLOYMENT RUNBOOK IS `docs/runbooks/phase-11.md`, IN THE REPO AND VERSIONED.** It executes
items 1–11 below, collapsed to **six human actions** with a verifier call before and after each, and
it names every stop-condition as `exit != 0`. The eleven items stay here as the log's record of what
is pending; the runbook is how to do them. Where the two disagree, the runbook is the procedure and
this is the state.

**PHASE 11 AND STAGE B ARE BOTH CODE-COMPLETE AND UNAPPLIED.** Phase 11 is eight commits beginning
`f29d734`; Stage B is seven more beginning `6607ba7`, which audited the commit boundaries and made
four corrections rather than adding capability. Every part is written, tested and
mutation-confirmed; **nothing has been applied to AWS or run on the instance.** 633 tests pass with
`DATABASE_URL` and Docker.

The pending human steps are listed under each part below and gathered here:

1. `cd infra/terraform/bootstrap && terraform init && terraform apply` — create the state bucket.
2. `cd infra/terraform && terraform init -migrate-state`, then `terraform plan` — **expect "No
   changes."** A plan that wants to create anything means the migration did not carry the state.
3. Two concurrent `terraform plan`s — the second must block or report a held lock. If both proceed,
   locking is decorative.
4. `terraform apply` for the backup bucket, IAM, health check, alarm, SNS and budget.
5. Confirm the SNS subscription and check the ARN is **not** `PendingConfirmation`.
6. Force a degraded health response, wait ~3 minutes, confirm the Route53 check fails and an email
   arrives. **This step is the whole point of the monitoring part.**
7. `python -m migrations.run` — one pending file, `0026`.
8. **Start the scheduler once**, if this instance has never run it, *before* step 9. `apscheduler_jobs`
   is created by APScheduler's own DDL on first start, not by a migration, and the backup asserts
   it exists before dumping — so on a fresh instance the first backup otherwise refuses with an
   error about an excluded table that says nothing about ordering.
9. The two jobs, one at a time, through the real runner Stage B added:
   ```
   cd /opt/inland-waterway-signals
   source .venv/bin/activate          # NOT optional — see below
   set -a; . ./.env; set +a
   python3 -m app.orchestration.run_once backup_nightly
   python3 -m app.orchestration.run_once restore_test_monthly
   ```
   **The activation is load-bearing and its absence does not look like a missing venv.** The jobs
   run from a host venv, not a container (§ Scheduler and jobs), and `boto3` is installed only
   there — so a bare `python3 -m app.orchestration.run_once backup_nightly` reaches the system
   interpreter and dies on `ModuleNotFoundError: boto3`. **Measured against the source, not
   assumed:** `backup.py:479` imports boto3 inside `backup_nightly`, above the `mkdir` and the
   dump, so the failure is early and nothing is left in staging — but the error names a Python
   package rather than an interpreter, so it reads as a missing dependency in the backup job. The
   fix somebody reaches for is `pip install boto3`, which on the system interpreter succeeds and
   moves the failure one import further down.
   Exit `0` succeeded, `1` the job failed (recorded in `job_runs`), `2` usage.
10. Burst `/api/conclusion` from a laptop, not the instance.
11. `docker compose down && docker compose up -d`, then `docker compose ps` — **three times**. The
    API must not report started before `timescaledb` reports healthy. Stage B replaced the
    `pg_isready` probe that made this ordering decorative; the race was load-dependent, which is
    why this is repeated rather than observed once.

**Phase 12 — containerize the `worker` service.** It closes `degraded: true`, and it **needs its
own restart-recovery verification**: being inside a container with `restart: unless-stopped`
changes the process lifetime this whole design is about, and this project has already demonstrated
that the settings can all be correct while the behaviour is not.

**Two dependencies Phase 11 created, recorded now because rediscovering them costs more:**

1. **Containerizing the scheduler moves the backup job into a container**, so Part 6's `docker run`
   becomes docker-in-docker or a mounted Docker socket. **Mounting the socket into the scheduler
   container is root-equivalent on the host.** That is a design decision, not an implementation
   detail, and it interacts directly with `§ 22`'s "application containers run as a non-root user".
   Part 6's container-in-container invocation needs re-verification then.
2. **Part 1's tag-plus-digest requirement applies to any self-built image**, which is what an
   `xcaddy` Caddy would be if `§ 22`'s rate-limiting exception is ever revisited. A locally built
   image has no registry digest to resolve, so it would need a different pinning story.

**Struck from this list, resolved rather than pending: boto3 behind IMDS from inside a container.**
The concern was that a hop limit of 1 would make the instance role unreachable from a container,
failing every S3 call with a credentials error that reads like an IAM problem. **Measured:
`infra/terraform/compute.tf:25` already sets `http_put_response_hop_limit = 2`, which is what a
container needs.** It is not a Phase 12 blocker and is recorded here so nobody re-opens it. **Do not
lower it.**

**A third dependency, added by Stage B:** `apscheduler_jobs` is created by `SQLAlchemyJobStore`'s
own DDL on the scheduler's first start, so it sits outside the numbered migrations and the checksum
regime entirely — a library upgrade can change its shape with nothing here noticing. The backup job
depends on that table existing (it asserts the `--exclude-table-data` target is present before
dumping), so **on a rebuilt instance the scheduler must start once before the first backup.**
Containerizing the worker changes when that first start happens, which is why it belongs beside
dependency 1 rather than in Housekeeping.

---

## Phase 11 — backups, restore verification, monitoring, rate limiting

In progress. One entry per part, with the commit SHA and what was measured rather than intended.

### Part 1 — gate 1's four remaining conditions (`f29d734`)

Gate 1's enumeration was already general (closed in `d9acd96`, standing item 0 below). This part
adds the four conditions the enumeration did not cover:

1. **An interpolated reference is its own failure.** `FROM ${BASE}` and `FROM $BASE` previously fell
   through to the digest check and failed with a message about a missing digest — a true failure
   with a wrong diagnosis, and the fix it suggests (`${BASE}@${DIGEST}`) passes the gate while
   pinning nothing. It now fails saying the base image must be written literally.
2. **`FROM scratch` is skipped and reported**, by seeding the declared-stage set with the name
   rather than by a special case at the check. It previously failed as an unpinned reference.
3. **`--write-digest` raises on drift instead of rewriting.** Three cases are now distinguished:
   unpinned *or placeholder* → write; pinned and identical → no-op with the file bytes untouched;
   **pinned and resolving differently → `DigestDriftError`, naming file, line, and both digests.**
   **The placeholder counts as unpinned, not as drift** — it is the committed "not resolved yet"
   marker (`CLAUDE.md § 12`) and writing it is what the command is for; four were replaced in
   Phase 10. Classifying it as drift would have made the placeholder the one thing
   `--write-digest` refuses to write.
4. **A digest with no tag is rejected** — this already worked; it now has a test through the full
   enumerate-then-check path, because it is what makes condition 3 non-vacuous. A reference with no
   tag offers nothing to resolve *from*, so the drift comparison would silently apply to zero
   references.

**Measured: 6 references across 3 files** — `docker-compose.yml` 2, `Dockerfile.api` 2,
`Dockerfile.frontend` 2 — all PASS. **No Compose file or Dockerfile needed a tag added**; every
reference already carried `name:tag@sha256:…`, so this part touched no image file and changed no
digest.

**Not exercised by the real files:** neither Dockerfile contains a `FROM` naming an earlier stage,
and neither contains `FROM scratch`. Both build services declare two registry `FROM` lines apiece.
The stage-accumulation and `scratch` paths are therefore covered by fixtures only. That is fine —
it is what fixtures are for — but nobody should later read the green gate as evidence that the real
files exercise those branches.

**Tests live in `tests/verify/test_preflight_checks.py`, not `tests/deploy/test_preflight_gate1.py`.**
Every other gate-1 test is already in that file; a second home would split the gate's coverage for
no reason.

### Part 2 — Terraform remote state with locking (`2c2a769`)

**Stop-condition ran clean.** `git ls-files infra/terraform/ | grep tfstate` printed nothing, and
`.gitignore:16-17` carries `*.tfstate` and `*.tfstate.*` — the second covering `.tfstate.backup`.
`git check-ignore -v` confirms all three bootstrap artefacts (`terraform.tfstate`,
`terraform.tfstate.backup`, `.terraform/`) are ignored at that path. **Checked, not assumed.** No
credential exposure and no rotation commit needed.

**Locking mechanism: native S3 (`use_lockfile = true`), not a DynamoDB table.** Installed
Terraform is **v1.15.8**; conditional-write locking against a `.tflock` object arrived in 1.10 and
DynamoDB-based locking was deprecated in 1.11. A lock table would be a second resource, a second
failure mode and a second line on the bill for something S3 now does natively.
`test_backend_has_locking_enabled` accepts either mechanism, so a future move between them does
not require rewriting the guard.

**The state bucket name is written twice and guarded by a test.** A `backend` block is evaluated
before variables, locals and data sources exist, so it cannot interpolate — the account-id suffix
used for Part 3's backup bucket is not available here. `backend.tf` holds the literal
`domestic-waterway-signals-tfstate`; `bootstrap/main.tf` holds it as the default of
`var.state_bucket_name`; `test_backend_bucket_matches_bootstrap_bucket` reads both and asserts
they agree. A global name collision fails the bootstrap apply immediately, before any state moves.

**The state bucket deliberately has no lifecycle rule**, the opposite of Part 3's backup bucket,
guarded by the inverse assertion `test_state_bucket_has_no_lifecycle_expiry`. Each state object
version is a recovery point of a few kilobytes and the day one is wanted is the day somebody is
recovering from a bad apply.

**Still pending (human steps):** `bootstrap` apply, `terraform init -migrate-state`, the clean-plan
check, and the two-shell concurrent-lock test. `§ Up Next` item 4 stays open until they run.

### Part 3 — backup bucket, scoped IAM, external health check, alarm, budget (`2394eb4`)

**The health check string-matches `"degraded":false` on the field the API already has.** No
`status` field was added and no ok token invented — `CLAUDE.md § 20` forbids a bare
`{"status":"ok"}` because that shape is what let the prior project record "Completed" for two and
a half months. Measured: a healthy body renders as
`{"degraded":false,"checked_at":"...","jobs":[...` — the token starts at **byte 1**, far inside
Route53's 5,120-byte window.

**The search string is validated against rendered bytes, not against another literal.**
`test_health_check_search_string_matches_rendered_body` drives the real FastAPI app through
`TestClient` and asserts the Terraform literal appears in `response.content`. A literal-to-literal
comparison catches a typo and misses a change of response class or JSON separators — the failure
that would leave this monitor permanently green. Confirmed by mutating the literal to
`"degraded": false` (one space), which is invisible to a literal comparison and went red here.

**`test_rendered_degraded_body_does_not_contain_search_string` is the load-bearing one.** Renaming
`JobHealth.overdue` to `degraded` produces a body reading
`{"degraded":true,...,"jobs":[{...,"degraded":false}...]}` — top-level degraded, nested healthy —
and the monitor would read the degraded system as healthy. The test caught it.

**The instance role gains a customer-managed policy with no delete action and no `s3:*`**,
scoped by reference to the backup bucket. Retention is the bucket lifecycle rule, which S3
executes itself and the instance cannot reach: `backups/daily/` at 35 days, `backups/monthly/` at
400, both with `noncurrent_version_expiration` (7 and 30 days) so versioning does not retain every
overwritten object forever.

**`test_instance_policy_cannot_reach_state_bucket` was strengthened mid-part.** As first written
it passed under `Resource = ["*"]` — a wildcard reaches the state bucket without naming it, so the
test named for the property was green while only the scoping test caught the mutation. It now
parses every `Resource` entry and requires each to reference the backup bucket.

**`tests/terraform/test_iam.py::test_instance_role_attaches_only_ssm_core` was widened.** It
asserted a total of one attachment, correct while `iam.tf` said "No S3 policy — the backup bucket
doesn't exist yet (Phase 11)". It now asserts exactly one **AWS-managed** attachment (SSM core),
requires every customer-managed attachment to point at a policy declared in this repo, and still
forbids inline policies. A bare count of two would have been a weaker test that passed today;
what is guarded is that nobody attaches `AmazonS3FullAccess`.

**Budget alert added** (`§ Up Next` item 6, open since Phase 10 with status unknown): a `COST`
budget at `var.monthly_budget_usd` (default 25) with both ACTUAL and FORECASTED notifications.
The forecast is the one that arrives while there is still time to act.

**Health check and alarm are pinned to an explicit `aws.us_east_1` aliased provider**, not left to
`var.aws_region`. Route53 health-check metrics exist only there; the comment alone would not
survive somebody moving the default region.

**Still pending (human steps):** `terraform apply`, SNS subscription confirmation and the
`PendingConfirmation` check, `get-health-check-status`, the forced-degradation test, and the
`Accept-Encoding:` curl that confirms what Route53 actually receives rather than what the app
returns.

### Part 4 — rate limiting, with an explicit § 22 amendment (`587d6e2`)

**The § 22 amendment is in the same commit as the middleware it permits**, per § 0. It does not
overturn "rate limiting lives at the edge" — it carves out cost-based limits on endpoints whose
cost is not cacheable, because the edge cannot see the cost, and it **names the residual exposure:
the bundle, CSS and fonts stay unlimited at the edge, accepted and not mitigated.**

**Limits.** General `/api`: 120 burst, 2/s sustained. `/api/conclusion`: **20 burst, 1 per 5s
sustained**. Token buckets, not fixed windows — a fixed window lets a client spend a full quota in
the last instant of one window and again in the first instant of the next. A conclusion request
spends from both buckets, so the tighter one trips first and the general one still backstops a
client spreading load across every endpoint.

**Configuration is module constants**, matching `app/api/dependencies.py`'s `DEFAULT_LIMIT` /
`MAX_LIMIT` / `MAX_SPAN_YEARS`. There is no settings module in this project and this commit does
not introduce one. Nothing reads `os.environ` at request time.

**The endpoint is `/api/conclusion`, singular.** Store bound: 10,000 buckets, LRU eviction plus
lazy idle expiry, with an eviction counter — an unbounded dict keyed by client IP *is* the denial
of service.

**Two of my own tests were bypassing the middleware and had to be rewritten.**
`test_ratelimit_ignores_x_forwarded_for` and `test_ratelimit_exempts_health_exact_path_only`
called `RateLimiter.check` directly, but header selection and the health exemption both live in
`dispatch`. Both mutations passed against the first versions. `client_key` does not even take an
XFF argument, so a test written against it passes whatever `dispatch` reads. Both now drive the
real app.

**Adding the limiter broke a passing test by pollution, which is the § 20 singleton lesson again.**
`test_no_error_body_contains_sql_or_a_connection_string` went red *in the suite* while passing
alone: an earlier test drained the general bucket and it got a 429 where it expected a 500. Fixed
by resetting `LIMITER` in the autouse fixture in `tests/api/conftest.py`, beside the caches.

**`math` could not be imported.** `tests/api/test_contract.py` forbids `app/api/` from importing a
computation module — a guard against this layer reimplementing the analog gate. Rounding a wait up
is not worth an exception to it, so `Retry-After` uses `int(wait) + 1`. Over-waiting by up to a
second is harmless; under-waiting invites an immediate retry.

**A pre-existing Caddyfile test asserted the literal `NO PER-IP RATE LIMIT SHIPPED`.** That was
true and is now false. Updated to assert `STILL NO EDGE RATE LIMIT` and `RESIDUAL EXPOSURE` —
keeping the old string would have forced the Caddyfile to keep claiming an exposure that had been
closed, which is the same failure as claiming a control that does not exist, pointing the other
way.

**`Dockerfile.api` already ran a single uvicorn worker** (`CMD ["uvicorn", ..., "--port", "8000"]`,
no `--workers`), and no Compose `command:` overrides it. `test_api_service_runs_single_uvicorn_worker`
now scans both files, because the Compose override wins and a check reading only the image would
miss it.

**Watch in live verification: `Caddyfile` carries `encode zstd gzip`.** Route53 health checkers do
not send `Accept-Encoding`, so the body should reach them uncompressed — but that is the exact
Theme 1 shape where every app-side test is green and the monitor is blind, which is why Part 3's
step 7 curls with `-H 'Accept-Encoding:'`.

### Part 5 — migration 0026: the `backups` table (`3ce2764`)

**`0026_backups_table.sql`.** 25 migrations were applied, so 0026 is next. Insert-once via a
`BEFORE UPDATE` trigger comparing column by column, unconditional `BEFORE DELETE` trigger,
`CHECK (verified = false OR verified_at IS NOT NULL)`, and `row_counts` / `restore_verified_counts`
constrained to JSON objects so nobody can write a scalar total.

**VERIFIED AGAINST A REAL TIMESCALEDB, NOT JUST WRITTEN.** A `timescale/timescaledb:latest-pg16`
container was already running locally on port 55432, so the migration was actually applied and all
eight integration tests ran green — and all four mutations were confirmed against real Postgres
rather than against a parser. **The full suite is 531 passed / 0 skipped with `DATABASE_URL` set**,
against 393 passed / 138 skipped without it.

**THE CADENCE ENTRIES ARE NOT IN THIS COMMIT.** `app/orchestration/scheduler.py` raises when
`CADENCES` and `JOB_FUNCTIONS` disagree, and `test_cadence_and_function_registry_must_agree`
asserts set equality — correctly, since a cadence entry with no function never fires and the
heartbeat reports it overdue forever. Adding both rows here turned one test red and errored four
others. So `backup_nightly` lands with its job in Part 6 and `restore_test_monthly` with its job in
Part 7, each with its own cadence test. The alternative was leaving the suite red across two
commits to satisfy a file-layout preference.

**Chosen `overdue_after` values**, both confirmed against `Cadence.__post_init__`:

- `backup_nightly`: interval 24h, **`overdue_after` 30h** — deliberately tighter than the
  three-interval convention the other daily jobs use. Those can be caught up from their sources; a
  day nobody backed up is a day that is in no archive.
- `restore_test_monthly`: interval 30d, **`overdue_after` 45d**. Its derived
  `misfire_grace_time` is **~15 days** and that is accepted, not worked around: a restore test has
  no time-of-day semantics, so running on the tenth after an outage is the desired behaviour, and
  with `coalesce=True` it runs once, promptly. Changing a derivation every existing job depends on,
  for one new job, in a phase about backups, is blast radius for nothing.

**Note for live verification:** the trigger's `RAISE EXCEPTION` surfaces as
`psycopg.errors.RaiseException`, not a constraint violation, and the message names the column —
`refusing to update column byte_size on backup_id=N`.

### Part 6 — nightly backup (`f54e915`)

`app/orchestration/backup.py`, registered as `backup_nightly` with its cadence entry (24h interval,
30h `overdue_after`). **`boto3==1.42.9` added to `requirements.txt`** — the phase's one runtime
dependency — and **`moto[s3]==5.1.22` to `requirements-dev.txt`**. No lock/hash file exists in this
project, so nothing else needed regenerating.

**THE INTEGRATION TIER RAN FOR REAL.** A local `timescale/timescaledb` container plus Docker meant
real dumps, real truncations and real `pg_restore`. **563 passed / 0 skipped** with `DATABASE_URL`
set; 414 passed / 149 skipped without.

**Three findings that only running it could produce:**

1. **The end-to-end test caught a real Theme 1 bug in the job.** `db.connection()` deliberately
   commits nothing implicitly (`app/db.py`), so the `backups` INSERT was **silently rolled back**
   while the job reported success, `job_runs` recorded success, and S3 held a verified archive.
   The next run's size floor would have had nothing to compare against and the restore test would
   have found no backup to restore. Fixed with an explicit `conn.commit()`.
2. **`apscheduler_jobs` is not created by any migration.** `SQLAlchemyJobStore` issues its own DDL
   at scheduler startup, so a freshly-migrated database does not have it — and
   `assert_excluded_table_exists` correctly refused to dump. The fixture now creates it the way
   the scheduler does. **On a restored database the table exists only because its DDL was in the
   dump**, which is exactly why `--exclude-table` was rejected in favour of `--exclude-table-data`.
3. **Measured: `pg_restore --list` accepts a truncated archive at 95%, 98% and 99% of full size,
   while a full restore rejects all three.** At one third — the incident's own proportions — the
   TOC is destroyed too and `--list` also fails, *at this database's size*. So the load-bearing
   truncation test uses **both** cuts: the one-third case is the incident's proportions, and the
   95% case is its **shape**, and only the 95% case goes red when verification is swapped to
   `--list`. A version of the test using one third alone stayed green under that mutation.

**Four of my own tests were too weak and were rewritten after mutations escaped them.** Two grepped
the module source for `ETag` and `return None` — and the module's own docstrings contain both
words, explaining why it does not use them. Two called helpers directly (`upload_and_verify`)
rather than the job, so mutations to the job body never reached them. All four are now behavioural
and all eleven mutations are confirmed.

### Part 7 — monthly restore test (`b9544e3`)

`app/orchestration/restore_test.py`, registered as `restore_test_monthly` (30d interval, 45d
`overdue_after`, derived grace ~15 days as accepted in Part 5). **592 passed / 0 skipped** with
`DATABASE_URL` and Docker; 437 passed / 155 skipped without.

**The integration tier really restores.** Real archives, real throwaway containers, real
`timescaledb_pre_restore`/`post_restore`, real teardown — and `docker ps -a` shows no leaked
containers after the suite.

**Two findings that only running it could produce:**

1. **`create_roles` originally created only `waterway_api`, and the restore failed** on
   `ERROR: role "dwstest" does not exist / Command was: ALTER SCHEMA public OWNER TO dwstest`.
   "Create every role the archive references" means the OBJECT OWNER too, not just the interesting
   one. Roles are now **discovered from the source database** rather than listed in code — a
   hardcoded list is a second copy of a fact the database already holds.
2. **`pg_isready` is not a readiness check for this image.** The official Postgres image runs a
   temporary server during `initdb`; `pg_isready` inside the container answers yes to it, so the
   restore that follows hits a database about to be restarted underneath it. Passed in isolation
   every time, errored under full-suite load — the signature of exactly that race. Readiness is
   now a real query over the published port from outside.

**Three of my own tests were confirmed weak by mutations and rewritten.**
`test_restore_test_integration_fails_when_a_table_is_short` deleted the WHOLE table, which any
comparison catches including a percentage tolerance — it now deletes **exactly one row**, the
smallest real loss there is and precisely what a tolerance swallows. The pre/post-restore ordering
test raised `ValueError` from `.index` rather than asserting, so it now asserts presence first.
The S3 download test failed on "no bytes" rather than on "no S3 read", so it now plants a **stale
local copy** — an implementation reading local staging finds it, succeeds, and is caught.

### Part 8 — documentation writeback

`CLAUDE.md` carries the new contract entries, folded in per part rather than in a lump.
`README.md` **did not exist** and was created: it states the recovery posture and, more
importantly, what that posture does **not** cover — RPO of up to 24 hours, no WAL archiving so no
point-in-time recovery, a single-region bucket, and an untested RTO. It also records the
rate-limiting residual exposure, so the decision is findable by someone reading only the README.

**What this prompt got wrong about the repo, corrected rather than adapted around:**

| Assumed | Actual |
|---|---|
| `tests/db/test_backups_table.py` | `tests/orchestration/test_backups_table.py` — every DB-constraint test lives there, beside `test_job_runs_constraints.py` |
| `jobs/registry.py` | `app/orchestration/scheduler.py`'s `JOB_FUNCTIONS` |
| `tests/deploy/test_preflight_gate1.py` | Added to `tests/verify/test_preflight_checks.py`, where every other gate-1 test already lives |
| Cadence rows land in Part 5 | They **cannot**: `scheduler.py` raises when `CADENCES` and `JOB_FUNCTIONS` disagree, so each row lands with its job (Parts 6 and 7) |
| `--write-digest` should raise on any differing pin | The all-zero **placeholder** must stay writable — it is the committed "not resolved yet" marker and writing it is the command's purpose |
| A settings module for limiter config | None exists; module constants match `app/api/dependencies.py`'s existing pattern |
| `python -m jobs.run_once <name>` | **Unverified.** No such module exists. The jobs are callable as `app.orchestration.backup.backup_nightly_job` / `restore_test.restore_test_monthly_job`; there is no one-shot CLI runner in this repo, and writing one was out of scope. **Tell me if you want one and it is a small commit.** — *Resolved in Stage B: it is now `python3 -m app.orchestration.run_once <name>`.* |
| Heartbeat entry point | `app.orchestration.heartbeat.heartbeat_job`; `heartbeat.check()` takes a `cadences` parameter and defaults to the table, so both new jobs appear with no heartbeat-side change — verified by the cadence/registry agreement test |

**Measured across the phase:** 592 tests pass with `DATABASE_URL` and Docker (437 pass / 155 skip
without). **50 mutations applied, all confirmed for the stated reason.** Nine of my own tests were
proven weak by a mutation escaping them and were rewritten — three source-greps that matched their
own module's docstrings, four that called helpers instead of the code path under test, one that
raised `ValueError` instead of asserting, and one that deleted a whole table where a tolerance
mutation needed a single row.

**One real bug was found by a test rather than by review:** the nightly job's `backups` INSERT was
silently rolled back, because `db.connection()` commits nothing implicitly, while the job reported
success and S3 held a verified archive. That is `§ 2`'s theme 1 inside the code written to prevent
it, and only an integration test against a real database could see it.

**Still open, untouched by this phase:** the three analog-engine questions, `SIMILARITY_CUTOFF` and
friends, the Cairo site number, `gauge_series` UTC bucketing, `lock_movements` being unused, the IV
chunk interval, and Node not being pinned in provisioning. None of them were closed, narrowed, or
quietly dropped.

---

## Stage B — the commit-boundary audit and four corrections, before deployment

Seven commits on `main`, `6607ba7`…, all **still unapplied to AWS**. Stage B closed the gaps the
Phase 11 report surfaced rather than adding capability.

### The audit that started it (`6607ba7`)

The Phase 11 rollback bug was treated as a class, not an instance, and the second question — *could
the existing tests have told?* — was answered by measurement: delete each write path's
`conn.commit()`, run the tests covering it, restore.

**Eight of ten write paths' commits were deletable with the suite green.** Two structural causes:
five of the eight job entrypoints were never invoked by any test at all (`usda_rates.ingest` and
`usda_movements.ingest` were never called at *any* level, so those commit lines never executed under
test), and where a path was tested it was called as `build(migrated_db, …)` and asserted through
`migrated_db.execute(…)` — the writing session, which cannot distinguish committed from
uncommitted. Several of those tests also called `migrated_db.commit()` themselves, so they would
have committed the data even with the production commit deleted.

**No further rollback defects were found.** Every writing path did commit. What was missing was any
test able to notice if one stopped.

Two corrections to the audit's own starting point, both worth keeping:

- **`grep 'db.connection()'` finds 6 call sites and misses every scheduled job.** It matches only
  the no-argument form; jobs call `db.connection(url)`. The real count was 29.
- **`app/orchestration/` contained zero bare calls**, so a guard scoped there — as originally
  specified — would have constrained the empty set: green forever, watching nothing.

### What the seven commits changed

| | |
|---|---|
| `6607ba7` | 14 read-back tests, one per write path, each reading on a connection opened after the writer closed |
| *(the commit-helper commit)* | `session.writing()` — commit on clean exit, roll back on `BaseException`, always re-raise; 14 write paths migrated; an AST guard over all of `app/` with an exact-set allow-list. `CLAUDE.md § 23`. |
| *(exact sets)* | `test_iam.py` and the Caddyfile proxy block assert exact sets again, updated to the new truth |
| *(placeholder digest)* | Both sides of the all-zero digest now have a test; **no behaviour changed — both were already correct** |
| *(healthcheck)* | `pg_isready` → a real query over TCP; see below |
| *(roles)* | `create_roles` discovers from the **archive**, not the live source database |
| *(runner)* | `python3 -m app.orchestration.run_once <name>`, and `check_cadence_agreement()` extracted so the scheduler and the runner share one |

### Two things measured rather than reasoned about

**The healthcheck race is real.** With an init script holding `initdb`'s temporary server open the
way a slow start under load would, `pg_isready` (no `-h`) reported UP for **18 consecutive samples**
while a TCP query correctly reported not-ready. `api` gates on `condition: service_healthy`, so the
API's startup ordering has been decorative since Phase 2 — it releases the API against a server
still initialising, and only when `initdb` is slow. `CLAUDE.md § 13` already stated this rule for
the restore test's throwaway container; production was not held to it.

**The `--list` truncation table**, which changes how fixtures get chosen here:

| cut | `--list` | full restore |
|---|---|---|
| 33% (the incident's own proportions) | **rejects** | rejects |
| 95%, 98%, 99% | **accepts** | rejects |

**A fixture that resembles the original incident is not automatically a good test of the guard
against it.** The one-third cut destroys the table of contents too, so a test built only from it
stays green when verification is downgraded to `--list`.

### Three findings from Stage B's own mutations

Each of these was a test this session wrote, believed, and then watched fail to catch its mutation:

1. **A single assertion over final state cannot isolate an early commit.** The sweep's `open_run`
   commit was claimed by a test asserting the run row after a *successful* run — but `run`'s final
   commit writes that row too, so deleting the early one only delays it. The docstring already named
   both commits and was wrong about which line it covered. Fixed by killing the scan partway.
2. **A set comparison collapses duplicates.** The exact-set IAM test passed when a policy already in
   the set was attached a second time. The count is now asserted before the set.
3. **`from … import` defeats attribute monkeypatching.** The "does not start the scheduler" test
   patched `scheduler.build_scheduler`, which a call through `run_once`'s own namespace never
   touches — so the mutation reached the real function and failed on *cadence agreement*, a red test
   pointing at the wrong layer. The source scan runs first now, and it is what names the failure.

### Still open, untouched by Stage B

The three analog-engine questions, `SIMILARITY_CUTOFF` and friends, the Cairo site number,
`gauge_series` UTC bucketing, `lock_movements` being unused, the IV chunk interval, and Node not
being pinned in provisioning. None closed, narrowed, or quietly dropped.

---

## Phase 11 verification tooling — `verify/phase11/`, five commits, unapplied like everything else

Roughly twenty-five manual conditions turned into **six human actions plus ten verifier
invocations**. `python3 -m verify.phase11 <stage>`, stages `c-pre`, `c-post`, `d-pre`, `d-post`,
`e`, `f`, `g`, `h`, `i`, `j`. The runbook is `docs/runbooks/phase-11.md`.

**Read-only by construction, two mechanisms.** An allow-list of permitted subcommands per binary
(`terraform show/version/providers schema`, `docker ps/inspect/compose ps/compose config`, twelve
enumerated AWS read verbs) with `plan`, `apply`, `init` and every destructive docker verb absent —
refused for being unlisted, not for being recognised. An AST walk asserts nothing outside `shell.py`
touches `subprocess`. Everything reading the database connects as `waterway_api`, with no fallback
to the owner. Three exit codes: 0 passed, 1 a check failed, 2 could not tell.

**Five things the prompt specified that turned out not to be checkable as written**, each measured
rather than reasoned about:

1. **`c-post` cannot use `terraform show -json` alone.** With no plan file it emits a STATE
   document, whose keys are `['checks', 'format_version', 'terraform_version', 'values']` — there is
   no `resource_changes` key at all, so "assert there are no resource changes" is true on every
   input forever, including a migration that carried nothing. It takes a plan file, and
   `require_plan` refuses the wrong document type as a precondition.
2. **A "No changes." plan is not an empty `resource_changes` list.** Measured on 1.15.8: one
   `["no-op"]` entry per resource, `"applyable": false`. The empty-list form would go red on a
   correct migration of seventeen resources. An EMPTY list is its own failure — it means the plan
   was computed against empty state.
3. **`d-pre`'s wildcard check has to be scoped to `aws_iam_policy` and to `Effect: Allow`.**
   `aws_s3_bucket_policy.backups` carries `"Action": "s3:*"` in a **Deny** statement, so an
   unscoped check fails on the correct plan — and the repair somebody reaches for is a weaker check
   rather than a scoped one.
4. **Stage H's "exactly the four production services" is wrong by one.** `frontend-build` exits by
   design (`restart: "no"`, gated by `service_completed_successfully`), so the running set is three.
   Two exact sets are asserted instead, plus its exit code.
5. **Two mutation tables named a test that cannot distinguish the implementations.** Substring-
   searching the plan text for `Delete` still catches `s3:DeleteObject`, so
   `test_d_pre_fails_on_iam_delete_action` goes red only on its observed-value assertion while the
   verdict stays correct; the discriminator is
   `test_d_pre_passes_when_delete_appears_only_in_a_description`. And dropping
   `Accept-Encoding: identity` is invisible to a test that drives a gzipped body through the check,
   because that tests the CHECK rather than the REQUEST; a new test inspects the outgoing headers.

**Two defects the tests caught while the tooling was being written:** `bootstrap_bucket_default()`
matched the first `default =` in `bootstrap/main.tf`, which belongs to `variable "aws_region"` and
is `"us-east-1"`; and `row_counts` keys are schema-qualified (`public.job_runs`, `backup.py:249`),
so an unqualified comparison reports every table as both missing and unexpected.

**What the tooling deliberately does not cover**, listed in the runbook and repeated here because
"a runbook that implies full automation is one somebody will trust past its limits": the two
`terraform apply`s, `terraform init -migrate-state`, the two concurrent `plan`s that prove locking,
the SNS confirmation click, the `waterway_api` `DELETE` refusal and the `backups` trigger refusal
(both are genuine writes and the verifier connects as a role that cannot make one), the `pg_restore`
95%-cut asymmetry check, stopping a service for Stage I, the alert email arriving, and the three
`docker compose down/up` cycles for the Part 4 healthcheck race.

**One thing the writeback commit must update by hand:** `verify/phase11/protected.py`'s
`PROTECTED_ADDRESSES` holds the 17 addresses that exist NOW. After Stage D's apply the state holds
17 + 13 plus a data source, and `d-pre`'s set-equality check will fail on the next plan until the
list matches. That failure is the mechanism working, not a defect.

**Fixtures are hand-built and sanitised.** Their shape was verified against real Terraform 1.15.8
output; their values are placeholders, because `.gitignore` keeps `*.tfstate` out of this repo
deliberately and a fixture cut from live state would put the account id, the EIP and the instance id
back in.

---

## Standing items, carried until somebody closes them

0. ~~**`verify/preflight.py` gate 1 covers one image reference out of three, and no Dockerfile
   `FROM` at all.**~~ **CLOSED 2026-08-17.** Gate 1 enumerates every `image:` in
   `docker-compose.yml` and every `FROM` in every Dockerfile — six references across three files —
   reports each by file, line and stage, and `--resolve-digest` / `--write-digest` handle all of
   them. A tag with no digest is a failure, and the enumeration itself is asserted so a walk that
   found nothing cannot report green. `docs/findings.md § I`.

1. **Should Node be a pinned, provisioned dependency the way Docker is?** *(Opened 2026-08-16 by the
   Phase 9 browser verification.)* The instance ran Node 18.19.1; Vite 8, Vitest 4 and rolldown 1.2
   need ≥ 20 and several want ≥ 22. `npm ci` warned `EBADENGINE` on every package and **exited
   zero**; the build failed one command later on `styleText` from `node:util`. **Nothing in this
   project pins Node** — not `CLAUDE.md § 5`, not a provisioning script, not `engines` in
   `package.json`, not an `.nvmrc`. Phase 10's containerized build means the host's Node is never
   invoked, which removes the symptom; the decision is whether Node also gets an entry in
   `CLAUDE.md § 5` and a provisioning step. **A human decision about infrastructure scope, not a
   defect to fix quietly.**
2. **The 348 literal in `src/views/Signals.tsx`.** The grid size is interpolated from the response;
   `348` beside it is hardcoded. They agree today and stop agreeing the first time the grid changes
   size. The fix is an API field or wording that states α — **not a component computing
   `grid_size × 0.05`**, which is a derived statistic (`CLAUDE.md § 21`). `docs/findings.md § H`.
3. **The confirmatory queries behind the sign-disagreement verdict.** The verdict is settled
   (**expected, different quantities** — `docs/findings.md § H`, and it rests on the event
   definition rather than on the data), but the numbers that would *characterize* the slope were
   never pulled. **The SQL is in `docs/query-outputs.md § Owed queries`**, along with the note that
   a positive or near-zero correlation among the low-counter weeks beside the full sample's −0.137
   is the measured form of the whole argument — and that it must not be run as a search for one.
4. **The three human decisions are still open** and neither Phase 9's nor Phase 10's verification
   touched them. They are what removes `ProvisionalBand.tsx`. See the next section.
5. **The sweep's `run_id` and wall time are still not recorded.** Every Phase 6 query in
   `docs/query-outputs.md` is written against `run_id = <id>` and nothing says what that id is. One
   query away: `select run_id, started_at, finished_at from signal_runs order by started_at desc
   limit 1;`
6. **DEBT 1a — the four thesis CSVs are captured and still not pasted in.** Open across seven
   phases. `docs/query-outputs.md § DEBT 1a` has the filenames and what the blocks check.
7. **`python -m verify.preflight` has not been run on the instance since Phase 6.** Owed from
   Phases 7, 8, 9 and 10. Gate 1 has changed in this commit, so the next run is also the first
   observation of the widened gate against a real machine.

---

## THREE QUESTIONS COME BEFORE ANYTHING IS QUOTED, AND THEY ARE ALL HUMAN DECISIONS

**These were written as "before Phase 8"; Phase 8, 9 and 10 all did not need them** — the API
serializes whatever the engine returns, the frontend renders it behind a provisional band, and the
deployment publishes both. They are unchanged and unanswered, and they are still what stands
between a passing gate and a quotable sentence.

The engine ran on the instance on 2026-08-16. **The gate passed on both labelled events**, and the
three things below are what stand between that and a quotable claim. **None of them is a code
change this agent may make** (`CLAUDE.md § 1`, `§ 18`'s last bullet):

1. **Is `MIN_ANALOGS = 4` compatible with a 70% consistency threshold?** At four analogs the
   achievable consistencies are 0/25/50/75/100%, so the 2022 pass at **3 of 4** clears a bar that
   cannot be evaluated at that resolution. **This project already made this exact argument for
   `walkforward.MIN_FOLDS` in Phase 6 and did not carry it across.** Settling it is its own commit,
   and the current values' results are now measured, so the change has a before.
2. **Does the analog count need a discount for temporal clustering?** Every analog behind both passes
   falls inside 2015–2022, and the 2023 rank-1 analog is the immediately preceding year. `CLAUDE.md
   § 19` carries the reading rule; whether it becomes arithmetic is a modelling decision.
3. **Why do the engine and the sweep disagree in sign?** Both sentences say the rate rose; the sweep's
   one surviving row is **−0.137**. They measure different quantities and that may fully explain it —
   **but "may fully explain it" is not a finding, and `CLAUDE.md § 19`'s last bullet asks for this
   check specifically.**

> **The Phase 9 investigation settled that there is NO CODE DEFECT underneath the third question**
> and did not settle the question. The sweep measures a slope across the whole counter range; the
> engine measures the outcome at the single point where the counter is 1; both returns come from
> one shared `ln(forward/now)`, so a sign-convention error could not survive. `docs/findings.md § H`
> has the derivation. **The UI's disagreement warning stays** — two true statements about different
> quantities still read as a contradiction to anybody who did not derive it, and the sentence is the
> unit that gets quoted.

**A similarity cutoff is NOT on that list, and the measurement is why:** the distances cluster within
~4% in both queries, so a cutoff would admit all of them or none. **Step 2 was run and it answered
the question by making it moot.**

**DO NOT** put either sentence in a README, a UI or a résumé until the three questions are settled.
**DO NOT** quote the 2023 `+270%` without saying it is one analog, from the immediately preceding
year, which is also the rank-1 match. **DO NOT** describe either result as the system "working" — the
gate passing is a fact about the gate, and the medians it passed on are **+7%** and **+10%** across
ranges that span zero.

---

## Still open — modelling questions nobody has answered

1. **Absolute operational thresholds are still a human decision awaiting a source.** The `p05`/`p10`/
   `p20` percentiles are stand-ins (`CLAUDE.md § 1`), and the sweep makes their arbitrariness
   concrete: a lag scan over three arbitrary levels is three arbitrary answers, and it will report
   all three with q-values that look equally authoritative.
   **AND THE ONE SURVIVING ROW IS BUILT ON ONE OF THEM.** `days_below_p10` counts days below a
   percentile this project chose as a stand-in, not below a draft anyone operates against. Whatever
   that row is evidence of, it is evidence about `p10` — and `p05` and `p20` were scanned beside it
   and produced nothing. **The source for a real operational threshold is still a human decision
   and it is now the input that would most change this table.**
2. **No movements feature exists.** `lock_movements` is a sparse per-commodity weekly series — MS
   Lock 15 reports 1,434 explicit zeros of 2,840 rows — and nobody has decided whether to aggregate
   across commodities before differencing. Differencing it as-is produces spikes and reversions that
   look like volatility and are mostly the reporting grain. **The volume half of the target stays
   unused until that decision is made**, and it is a decision rather than an oversight.
3. **The eight-year climatology guard has still never fired on real data.** Debt 1c gave it an
   end-to-end test against a deliberately shallow 5-year fixture, so the refusal is now known to
   survive the database round-trip. `climatology_n_years` on the real table still runs 11 to 37 with
   no NULLs anywhere. **If a fifth gauge is ever seeded with a short record, that is the first run
   where it matters.**
4. **The regime split does not test Phase 5's most interesting finding, and it is unmeasured rather
   than disconfirmed.** `recovery` carries 1 to 7 observations at every horizon for
   `days_below_p10` at Memphis, all refused as `insufficient_observations`, because Phase 5's
   "recovery" — the stretch from 30 through 58 days below — is a **still-rising** counter and lands
   in `onset`. Whether the split wanted is rate-of-change of the counter, or peak-relative, is a
   modelling decision in its own commit, with the current definition's results measured first so the
   change has a before. `docs/findings.md § E`.

---

## Open questions

- ~~**Raw 15-minute gauge readings vs. hourly aggregates on ingest.**~~ **Closed, and the question
  was slightly wrong.** It is **native cadence per site** — 15, 30, and 60 minutes across the four
  seeded gauges. Raw readings are stored as published; nothing aggregates or resamples.
  **The volume estimate that framed this question was also wrong by an order of magnitude**: it
  assumed ~15 sites × 2 params ≈ 20M rows, and the real shape is 4 sites × 1 param ≈ **1.3M rows**.
  Volume is not a factor in any decision here.
- ~~**Whether USGS instantaneous-values requests can span the full period of record in one call.**~~
  **Closed by decision rather than by measurement, which is the safer direction.** The backfill
  chunks by 90-day window regardless (`CLAUDE.md § 14`), so the answer no longer gates anything.
  The reason not to test-and-then-trust it: the failure mode when the service declines a huge span
  is not a clean error but a truncated or timed-out response, which looks like a short record.
- ~~**Are the seeded `record_start` values right for the three sites that were not measured?**~~
  **Closed 2026-08-14 by full-range measurement.** The daily values were wrong at three of four
  sites and are corrected in `0011`; the instantaneous values are NULL at the three
  rolling-retention sites. Live verification still compares each `min(date)` against the corrected
  seed — they should now agree, and the backfill still never writes back.
- **What is the Cairo, IL site number?** Investigated for Phase 3 and **still not confirmed** as of
  Phase 3.5, so it remains absent from the seed rather than guessed. Cairo sits at the Ohio
  confluence and is the most obvious gap in the corridor; adding it is a human decision
  (`CLAUDE.md § 1`).
- ~~**Exact daily record starts are still bracketed.**~~ **Closed 2026-08-14.** Measured per site
  by a single full-range request counting values per year; the four corrected values are in
  `0011` and in `docs/findings.md § A`. St. Louis's 1990-01-01 remains a **bound** —
  its record predates the request floor, and reaching further back is a human's decision.
- **How should a rolling-retention endpoint be modelled?** *Partly answered.* The column is now
  NULL for the three sites, which is the honest value, and the instantaneous backfill refuses
  them by name. **What remains open is whether the IV backfill applies to those sites at all** —
  the likely answer is that it does not, and the incremental poll is the only path to their
  instantaneous data. **First candidate for the next ingest commit.**
- **`gauges.lat`/`lon` are seeded NULL and must be filled by a human** before anything draws a
  map. No agent has had any way to verify coordinates and none has typed them from
  recollection. Obtain them from the USGS site service and apply as a **new** migration:
  `curl 'https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=07010000,07032000,07289000,07374000'`.
  `tests/ingest/test_gauge_seed.py::test_river_mile_and_coordinates_are_null_rather_than_estimated`
  goes red when they land and is **meant to be deleted in that commit**, not weakened.

---

## Housekeeping — open, non-blocking

*Condensed on 2026-08-17. The full list as it stood, including the roughly half of it that has since
closed, is in `docs/phase-log.md § Appendix` — several closed items carry measurements that exist
nowhere else.*

**Data and ingest**

- **The instantaneous backfill runs for ST. LOUIS ONLY.** `iv_record_start` is NULL at Memphis,
  Vicksburg and Baton Rouge (`0011`) — a rolling window is not a date, so the honest column value is
  empty rather than a date that expires. **Whether the IV backfill applies to rolling-retention
  sites at all is still a human's call**; the likely answer is that it does not.
- **`gauge_series` buckets instantaneous data by UTC date while USGS computes its daily mean over
  the site's LOCAL calendar day** — a 5–6 hour offset at both edges on the lower Mississippi. Small
  for a river that moves in feet per day; not zero. Fixing it properly means recording a timezone
  per gauge, which is a schema change and a human decision. The `source` column keeps the seam
  visible meanwhile.
- **`rows_written = 0` from `usgs_ingest` is the normal steady state, not a symptom**, and **no
  alert may be added on it.** The upsert counts only rows that actually changed. The freshness
  registry is what detects a source going quiet, from `MAX(ts)` rather than from the job's own
  report about itself.
- **A revision to a reading older than 30 days lands in a compressed chunk** and is markedly slower.
  Rare (USGS approving old data) and it works on the pinned version. If it becomes routine, widen
  `0006`'s interval — do not stop upserting.
- **The 30-day IV chunk-interval tuning candidate** is logged and deliberately not acted on: a chunk
  interval change affects new chunks only, so it is a deliberate future migration on a considered
  date, not a fix to slip in.
- **Any README or résumé line quoting the compression ratios must carry the honest framing** — real
  measurements, real reductions, and **at ~290k rows Postgres alone would have been adequate**.

**Scheduler and jobs**

- **The scheduler runs from a host venv, not a container.** Phase 12, and it needs its own
  restart-recovery verification.
- **`apscheduler_jobs` must be EXCLUDED from dumps when backups land in Phase 11.** Restoring stale
  scheduler state is worse than restoring none — the rows carry `next_run_time` values from whenever
  the dump was taken. `pg_dump --exclude-table`.
- **The verification probe jobs write permanent `job_runs` rows** (`verify_restart_probe`,
  `verify_failure_survives_probe`). Correct — `job_runs` is append-only and those rows are the record
  that verification ran — but they must never appear in the cadence table, or the heartbeat reports
  them overdue.
- **If `verify/restart_recovery.py` ever warns that it could not remove its probe**, run
  `python3 -m verify.restart_recovery --cleanup-only` **before** starting the production scheduler.
  A leftover probe row keeps firing, because `register_jobs()` never removes jobs it does not
  recognise.
- **The heartbeat's first-ever run alerts about itself**, and a registered ingest table with no rows
  is stale rather than quiet. Expected once each; both go quiet after the first success.
- **`missed` rows are only reachable for jobs whose grace is shorter than their interval.** An
  absence of `missed` rows is not by itself evidence that nothing was missed.

**Infrastructure**

- **Terraform state is local, and there is now applied infrastructure behind it.** If
  `terraform.tfstate` is lost, `prevent_destroy` protects nothing. Phase 11 item 4.
- `infra/terraform/terraform.tfstate` exists in the working tree. **Checked and clean:** untracked,
  matched by `.gitignore`, never committed. Noted only because Terraform state carries secrets in
  plaintext.
- **AWS budget alert — status unknown**, and it no longer blocks anything by itself. Confirm it
  exists: there is a running instance, an EIP and an EBS volume billing continuously.
- `docker-ce-rootless-extras` remains installed unpinned and unheld.
- **Copying `infra/provision/*.service` unit files into `/etc/systemd/system/` and running
  `systemctl enable` is still a manual step**, though `infra/provision/deploy.sh` now exists for the
  rest of the deploy path.

---

## Process notes

**Commits:** after any Claude Code session reports a commit, run `git log --oneline origin/main`
from your own terminal before treating the work as real — three separate sessions on 2026-08-10
reported committed work that had not been pushed.

**Live-verification outcomes are written back in the same session, as their own small commit.** This
is the rule this project most needed and went three commits without following: every session wrote
"not yet run against the instance" and none came back to correct it once verification succeeded,
while `CLAUDE.md § 0` names this file as the second-highest authority in the project. Phases 6
through 10 have each managed it. **Running the verification and not recording the result is not
finishing the verification.**

**The verification harness is re-runnable at any time** — after a reboot, after a dependency bump,
before trusting the stack again:

    python3 -m verify.preflight            # exits non-zero on any FAIL *or* SKIP
    python3 -m verify.restart_recovery     # ~6.5 minutes; see CLAUDE.md § 12 on why
    python3 -m verify.failure_survives

**When rerunning the suite on the instance, compare the count to the offline run.** Offline it is
`345 passed, 130 skipped`; with `DATABASE_URL` set the integration tier executes instead of
skipping. **If the skip count has not gone to zero, the `integration` marker is skipping and nothing
was verified.**
