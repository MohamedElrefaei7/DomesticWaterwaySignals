# CLAUDE.md — Inland Waterway Signals

This file is the project **contract**. Claude Code reads it as ground truth at the start of every
session. It holds invariants, conventions, and decisions that are not up for re-litigation.

**It does not hold plans.** Current state, recent decisions, and `§ Up Next` live in `CONTEXT.md`.
Contracts here; log there. If you are about to write "next we will…" into this file, it belongs in
`CONTEXT.md`.

**Precedence:** this file > `CONTEXT.md` > any handoff or summary document. If a summary conflicts
with this file, this file wins and the summary is stale.

These contracts were learned expensively on a prior project. Each one exists because its absence
caused a real failure. Do not relax one because it looks like ceremony — if you believe one is
wrong, say so in the commit report and leave it in place.

---

## 0. Working conventions

- **One commit per logical change.** A commit has a single scope and a clean done-condition. Do not
  helpfully expand the diff into adjacent work, even when the adjacent work is obviously needed.
- **Guard non-obvious decisions with tests that go red.** For every decision where the obvious
  implementation is wrong, there must be a test that fails when the decision is reverted. Confirm
  this by **mutation**: revert the decision, watch the named test fail, restore it, and report that
  you did. A test you asserted would catch a regression, without watching it catch one, is a comment
  wearing a test's clothes.
- **Mutation confirmation clears `__pycache__` between the restore and the re-run**, or runs under
  `PYTHONDONTWRITEBYTECODE=1`. A restored file whose stale bytecode is still loaded reads exactly
  like a restore that did not happen — the test stays red, and the natural conclusion is that the
  restore failed or that the test is flaky. Observed on 2026-08-14; it cost a confirmation pass.
- **A mutation that goes red for the wrong reason is not a confirmed guard.** An import error, a
  `NameError`, or a different assertion failing proves only that the test runs. Re-do the mutation
  in a form that reaches the assertion the guard is about, and report both passes.
- **Report what you verified, not what you intended.** Commit reports are checked against the repo
  with `git show` and `grep`. A report describing a change that did not land is worse than no report.
- **When a measurement contradicts the plan, the measurement wins.** Report the contradiction; do not
  reconcile it silently.

---

## 1. Delegation boundaries — hard limits on this agent

**Never handle, request, generate, or echo:** cloud credentials, SSH private keys, database
passwords, webhook URLs, or any other secret. Reference them by environment variable name only.

**Never run:** `terraform apply`, `terraform destroy`, any `DROP` statement, or any command that
deletes data from the database or object storage. Write them if asked, leave them for a human to run,
and say so explicitly in the report.

**Never invent:** the gauge site list, threshold values that define an event (what stage counts as
"low"), analog-matching logic, or confidence-gating logic. These are the human's modelling decisions.
If a task appears to require one, stop and ask rather than picking a plausible default.

Everything else — application code, systemd units, Dockerfiles, the Caddyfile, migration scripts, the
scheduler, ingest clients, the normalizer, the FastAPI surface, the React frontend, backup and
heartbeat jobs, the deploy script, and every test — is yours.

---

## 2. Recurring failure themes — read every change against these first

**Theme 1 — a layer reports success while the thing downstream gets nothing.** Ingest "worked" with a
required field hardcoded to `None`. An aggregation was documented but never written, so 29,650 rows
fed nothing. Orchestration recorded "Completed" while the whole stack had been down for two and a
half months. A `pg_dump` exited zero and wrote a third of a file.

The question is never "did this run?" — it is **"did the thing downstream of this actually receive
what it expected?"** A count of `0` frequently means the query measured something narrower than its
name suggests, not that there was nothing to do.

**Theme 2 — a check verifies the exact thing responsible for a failure and reports it correct.** A
test asserted a firewall rule existed while that rule broke every outbound connection. Ten scheduler
tests asserted the settings that were supposed to guarantee restart recovery, all green, while
recovery did not work — they tested configuration, and the behaviour lived in process lifetime. An
ingress test passed vacuously because the set it constrained was empty.

The discipline: **check that your check would fail if the thing were broken.** Prefer verification
that crosses the boundary where the bug would live. A config test that passes while the behaviour it
describes does not hold has already happened here.

---

## 3. Schema and data

- Schema changes are **additive numbered migration files**, applied by a runner that records
  `version, filename, checksum, applied_at` and **aborts if an already-applied file's checksum
  changed**. Filename-only tracking silently permits editing an applied migration.
- **There is no re-runnable `schema.sql`.** A fresh database is created by restoring a verified dump,
  not by executing a monolith. The prior project came within one command of unrecoverable loss twice.
- Migrations run **one transaction per file**, with the `schema_migrations` insert inside that same
  transaction. Never all-pending-in-one.
- A migration that must run outside a transaction (e.g. `CREATE INDEX CONCURRENTLY`) is marked with a
  first-line `-- migrate:no-transaction` comment that the runner honours.
- **Destructive operations are archived, never dropped:** `ALTER TABLE … RENAME TO …_archived_YYYYMMDD`.
  Only a human runs an actual `DROP`.
- **A dump is verified by `pg_restore -f /dev/null <file>` completing with no stderr output.**
  `pg_restore --list` is *not* verification — it reads only the archive's table of contents. A dump
  that was one-third its correct size once passed `--list` cleanly, matched its own SHA-256 across
  three machines, and failed on restore. This binds every backup job and every restore test.
- A restored database has **no planner statistics**. `ANALYZE` follows every `pg_restore`, as part of
  restoring — not as a migration and not as a scheduled job.
- **Migrations never run on container start.** A restart loop would become a migration loop.

---

## 4. Jobs and monitoring

- Exactly **one `@job` per scheduled unit**, never nested. Job names are stable identifiers and are
  the join key between the cadence table and the heartbeat.
- `rows_written` means **rows written to the database**, never rows examined or processed. `0` and
  `NULL` are distinguishable and both meaningful.
- The `@job` decorator writes a `running` row **before** the call, from a **separate database session
  committed independently**. If the wrapped work rolls back, the failure record must survive. It
  re-raises; it never swallows.
- `job_runs.status` is constrained **by the database** to `running | success | failed | missed`.
- A misfired job never invokes the function, so a **scheduler event listener writes the `missed`
  row**. Without it a missed run is invisible — indistinguishable from never having been scheduled.
- `job_runs` is **append-only**. No code path deletes from it. A human may make a one-off correction
  with a stated reason. **When data is lost, record the loss — never synthesize a replacement.**
- The **cadence table is the single source of truth for both trigger timing and per-job overdue
  thresholds.** The heartbeat imports it and defines no thresholds of its own. Two tables of the same
  fact diverge silently, and the divergence produces false confidence.
- **"Last success" is the most recent `success` row's `finished_at`** — never the most recent row of
  any status. A job failing nightly has recent activity and no recent success.
- **Liveness is measured from the data, never from the process.** A source that accepts your
  connection and delivers nothing is indistinguishable from a healthy one at every layer except the
  data. Check `MAX(ts)` on the ingested table.
- `coalesce=True`, with per-job `misfire_grace_time` proportional to the job's interval, so a restart
  after an outage catches up **once, promptly** — rather than once per missed slot, or not at all.
  The library default grace of one second silently drops everything.
- The scheduler uses a **persistent job store**. An in-memory store forgets the schedule on restart,
  and configuration tests will not catch it.
- **Alert delivery failures never fail the monitoring job itself.**

---

## 5. Infrastructure

- The data volume is **separate from the instance**, carries `prevent_destroy` in Terraform, and is
  referenced in `fstab` by **filesystem UUID** — never a device path. Cloud NVMe device enumeration is
  not stable across stop/start; it changed twice in one week on the prior project.
- Any script that identifies a disk does so by **AWS volume ID matched against the NVMe serial**,
  **dash-stripped on both sides** (AWS presents `vol-0abc...`; the kernel serial omits the dash),
  read from **`/sys/block/<dev>/device/serial`** — the block device, not the controller.
  `/sys/class/nvme/nvme*/serial` names the *controller*; the device `mkfs` needs is a namespace
  beneath it, and inferring its name (`nvme1` → `nvme1n1`) is an assumption that holds until the
  instance where it doesn't. Reading from `/sys/block` gives the block device name directly from
  the directory entry instead. Never identify by topology ("the disk that isn't root").
  **Hard-fail on zero or multiple matches. Never guess.**
- `mkfs` runs **only if the device has no existing filesystem**. Check `blkid` for a `TYPE`, not
  merely for a partition table.
- **ufw and the security group are gates in series, not alternatives.** ufw's default-deny blocks a
  port regardless of what the security group permits. SSH needs an explicit ufw rule scoped to the
  admin CIDR, or provisioning locks you out the moment the firewall activates.
- **Docker bypasses ufw entirely** via the `DOCKER-USER` iptables chain. That chain hooks `FORWARD`
  and carries traffic in *both* directions, so an unqualified `DROP` blocks every container's own
  outbound DNS and package fetches. Rules must be **interface-scoped**: accept `-o $EXT_IFACE`
  egress, accept `-i $EXT_IFACE` on 80/443, drop `-i $EXT_IFACE` otherwise. **Discover the interface
  at boot; never hardcode it.**
- **Every image tag is pinned, and resolved from the machine that runs it** — never from a developer
  laptop's cache. `latest` on a database image resolved to two different TimescaleDB versions three
  months apart and cost a full session.
- Secrets reach containers via Compose `environment:` referencing `.env`, and `.env` is excluded from
  the image build context. **Passwords must be URI-safe** — generate with `openssl rand -hex 32`, not
  `base64`, because `/` and `+` break `DATABASE_URL` parsing and surface as confusing host and port
  errors rather than as auth failures.
- The deploy path is **`git pull` on the server**, then a provisioning script that stages the build
  context, then `docker compose up -d`. Manual `scp` of directories caused four separate stale-file
  incidents in a single session; it is not a deployment mechanism.
- The deploy directory default is a **fixed absolute path**, never derived from a script's own
  location — especially once anything does `rsync --delete` into it. **Refuse to run if the target
  contains a `.git` directory.** **The canonical value is `/opt/inland-waterway-signals`** — every
  provisioning and deploy script references this constant; none derives it from `$0`, `pwd`, or any
  other contextual source.

---

## 6. Architecture — fixed

Five containers on one EC2 instance, one Docker Compose stack, all `restart: unless-stopped`, brought
up at boot by a single systemd unit.

| Service | Contents | Exposed |
|---|---|---|
| `timescaledb` | Postgres 16 + TimescaleDB, volume-backed on a **separate** EBS volume | **no** — internal only |
| `worker` | APScheduler running all jobs | no |
| `api` | FastAPI + uvicorn | internal, proxied |
| `caddy` | TLS termination, serves the built React bundle, proxies `/api` | 80, 443 |

**There is no streaming daemon. Everything is polled on a schedule.** This removes an entire category
of failure the prior project fought: supervising a long-lived socket, cold-start state reconstruction,
reconnection policy, bounded-backoff escalation. **Do not reintroduce it**, including as an
"optimization" for the 15-minute USGS cadence.

Frontend: React + TypeScript via Vite; Recharts or TradingView `lightweight-charts`; MapLibre GL JS
for the river map; Caddy for automatic Let's Encrypt.

**Live USACE LPMS lock-queue scraping is not a dependency of anything.** Weekly lock movements arrive
cleanly via USDA Table 10. Scraping an Oracle APEX web app is the same fragility class as a news-site
scraper; if it is ever built, it is strictly an optional enhancement whose failure degrades nothing.

**Pin external API versions.** USGS is migrating to a modernized OGC API. Build against a specific,
named endpoint set and pin it; never build against a moving default.

---

## 7. Output contract

The user-facing output is **historical analogs, not regression coefficients**:

> Mississippi stage at Memphis has fallen 4.2 ft in 14 days and is now 1.8 ft below the 10-year
> seasonal median. The last 5 times stage fell this far this fast during harvest season, the
> Cairo–Memphis barge rate rose 18–47% within 3 weeks — median +29%, 5 of 5 directionally correct.

**Confidence gate: ≥4 analogs and ≥70% directional consistency, else the system says "insufficient
history."** Manufacturing conviction from three coincidences is the failure this gate exists to
prevent. The lead-lag sweep still runs underneath to *discover* which pairs deserve detectors; it is
not the user-facing output.

**Every number that appears in the README, the UI, or the résumé must be reproducible from a query.**

---

## 8. Terraform conventions

- The data volume is always a top-level `aws_ebs_volume` with `prevent_destroy`; never an
  `ebs_block_device` block, because lifecycle meta-arguments cannot apply to nested blocks.
- Security groups always declare an explicit egress rule; the provider revokes the API's default
  allow-all, and losing egress kills the SSM recovery path.
- Ingress is an allowlist asserted by exact set equality, never a denylist of forbidden ports.
- AMIs are pinned IDs. No `most_recent` data sources, for the same reason no image tag is `latest`.
- IMDSv2 required on every instance.
- IAM roles carry exactly the managed policies they need, added in the commit that needs them.
- `terraform apply` is human-only, as is any operation that destroys or detaches the data volume.
- `.terraform.lock.hcl` is locked for every platform that will run `terraform init` — laptop and
  CI/server architectures alike — via `terraform providers lock -platform=... -platform=...` before
  committing, and the lock file's growth is confirmed, not assumed. A lock file that only carries the
  `h1:` hash for the platform that happened to run `init` first either fails or silently grows on the
  next machine, which is `§ 5`'s "resolved from the machine that runs it" applied to providers.
- AWS resource `description`/`Name`-style string fields are ASCII-only; use a hyphen, never an em
  dash, in any generated string that becomes one.
- On `aws_instance` resources with an associated `aws_eip`, add `lifecycle { ignore_changes =
  [associate_public_ip_address] }` — once the EIP attaches, live state reports this attribute as
  `true` regardless of config, and without the ignore it forces instance replacement on every
  subsequent plan.

---

## 9. Provisioning conventions

- Disks are identified as specified in `§ 5`: AWS volume ID matched against the NVMe serial,
  dash-stripped on both sides, read from `/sys/block/<dev>/device/serial`. Zero or multiple
  matches is a hard failure. There is no topology-based fallback.
- `mkfs` runs only when `blkid` exits `2`. Any other non-zero exit raises. Never `!= 0`.
- fstab entries key on filesystem UUID, carry `nofail` and a short device timeout, and are written
  idempotently by mount point.
- Because `nofail` allows boot to proceed without the volume, **the systemd unit for the Compose
  stack carries `RequiresMountsFor=/mnt/data`.** The mount's absence must stop the application, not
  be discovered in the data.
- Provisioning scripts accept `--dry-run` and filesystem-root overrides so their load-bearing logic
  is testable without an instance.
- Provisioning is run by a human on the instance. No agent connects to the server.

---

## 10. Docker and interface discovery conventions

- Docker packages are installed at exact pinned versions via explicit `pkg=version` strings and
  held with `apt-mark hold`. Never a bare `apt-get install docker-ce`.
- The GPG key is dearmored into a repo-scoped keyring file, never `apt-key add`; content is
  validated before it's trusted.
- The repo codename is always read from `/etc/os-release`, never hardcoded, because the AMI is
  expected to be bumped to a newer Ubuntu LTS deliberately at some point.
- The external interface is always identified from the default-route entry in `/proc/net/route`,
  never by "the interface that isn't loopback" — Docker's own bridge and veth interfaces make that
  heuristic wrong the moment Docker is running, which is always, on this instance.
- Interface discovery writes a single-line file at a fixed path via its own boot-ordered systemd
  unit; downstream consumers (the firewall commit) read the file rather than re-deriving the value.

---

## 11. Firewall conventions

- ufw is `deny incoming`, **`allow outgoing`**. Outbound is the SSM path and there is no SSH key
  pair on this instance; denying outbound makes it unrecoverable short of detaching the root
  volume.
- ufw rules are added before `ufw --force enable`, never after, and enable is always forced. Bare
  `ufw enable` prompts on stdin and hangs or aborts non-interactively, leaving the firewall in an
  indeterminate state that gets reported as configured.
- The ufw port allowlist is asserted by exact set equality (`{22, 80, 443}`), never as a denylist.
  SSH is scoped to `--admin-cidr`; there is no bare `ufw allow 22` or `ufw allow ssh` anywhere.
- ufw filters the host; `DOCKER-USER` filters containers. Neither substitutes for the other —
  published container ports are DNAT'd and traverse `FORWARD`, bypassing ufw entirely.
- Every `DOCKER-USER` rule is interface-scoped, using the interface read from
  `/etc/dws/external-interface` — never hardcoded, never a default. A conntrack
  `RELATED,ESTABLISHED` `RETURN` on `-i` is always the first rule; without it, replies to a
  container's own outbound traffic hit the terminal `DROP` and the container hangs instead of
  erroring. The terminal `DROP` is always `-i`-scoped, never bare.
- `DOCKER-USER` rules use `RETURN`, not `ACCEPT`, so Docker's own per-container and
  port-publishing chains still apply.
- The chain is flushed before rules are appended, so re-running provisioning is idempotent. This
  project owns `DOCKER-USER` by convention — Docker creates it and never writes to it — so
  flushing it is safe.
- `ip6tables` always receives the identical rule set. Turning IPv6 off is not a way of satisfying
  a firewall requirement — Docker's IPv6 support being off by default today is exactly what makes
  skipping `ip6tables` look harmless until someone enables it.
- Docker is restarted after `ufw enable` and before `DOCKER-USER` rules are applied — `ufw
  enable`'s `iptables-restore` discards Docker's own chains, and Docker only rebuilds them on
  daemon restart.
- A missing or empty interface file is fatal, before a single command is issued — no fallback, no
  guessed default. A partially-applied firewall looks configured and is not, which is worse than
  none.
- Raw iptables/ip6tables rules do not persist across reboot the way ufw's own configuration does.
  Any raw-iptables configuration is reapplied by a boot-ordered systemd unit
  (`dws-docker-firewall.service`, `Requires=docker.service`,
  `Wants=dws-external-interface.service`) invoking the script with `--docker-user-only`, which
  never touches ufw. Reboot-persistence is verified by an actual reboot, not inferred from rule
  presence.

---

## 12. Orchestration conventions

**Migrations**

- `schema_migrations` is **bootstrapped by the runner**, idempotently, outside the numbered
  sequence — the table that records applied migrations cannot itself be an applied migration.
  Every other schema change is a numbered file. This bootstrap is the only DDL the runner issues
  that is not backed by one.
- **Checksums of all already-applied migrations are verified before any pending migration is
  applied.** Not the pending ones — those have nothing to compare against. On mismatch the entire
  run aborts, naming the file and both checksums, before the first change is made.
- **One transaction per migration file, with the `schema_migrations` insert inside it.** Never
  all-pending-in-one (a late failure rolls back files that had already succeeded), and never
  commit-then-record (a crash in the window leaves a migration applied and unrecorded).
- `-- migrate:no-transaction` is honoured **only as the literal first line**, and that path is
  **knowingly non-atomic** — a crash between the statement and the record leaves the migration
  applied and unrecorded. That is why it is opt-in per file rather than the default.
- A pending migration numbered **below the highest applied version** is a hard failure, naming
  both. It is neither applied out of order nor skipped.
- There is **no re-runnable `schema.sql`**, and a test asserts none exists anywhere in the repo.
- **Migrations never run on container start.** The runner is a CLI a human invokes; no Compose
  `command`, `entrypoint`, or healthcheck references it, and a test asserts that.

**Jobs**

- **`@job` bookkeeping always uses a separate connection, committed before the work starts, and
  always re-raises.** Sharing the work's session means a rollback in the work takes the failure
  record with it — the failure that most needs a record is the one guaranteed not to have one.
- `rows_written` means **rows written to the database**. `NULL` and `0` are distinct and both
  meaningful, in the decorator and in the column (no `NOT NULL`, no `DEFAULT 0`).
- `job_runs.status` is **constrained by the database**; the table is **append-only by trigger**,
  with **`UPDATE` permitted** so a job can close the row it opened. A human making the one-off
  correction the contract allows must explicitly disable the trigger.
- Exactly one `@job` per scheduled unit, **never nested**, enforced at runtime by a `ContextVar`
  that names both jobs when it fires.
- **Missed runs are recorded by a scheduler event listener, never by the decorator** — a misfired
  job never invokes the function, so the decorator never sees it.

**Scheduling**

- The **cadence table is the single source of truth for trigger timing and overdue thresholds.**
  The heartbeat imports it and defines none of its own, and that is guarded behaviourally — by a
  test that mutates an entry and asserts the verdict flips — not by grepping for literals.
- `coalesce=True`, and `misfire_grace_time` **derived from the interval**, never the library
  default of one second.
- **A cadence entry whose `misfire_grace_time` meets or exceeds its `interval` is a configuration
  error.** With `coalesce=True` APScheduler evaluates only the *last* missed fire time, which is
  never more than one interval old, so such a job can never produce a `missed` row — it always
  catches up instead. The consequence is worse than the lost row: **an absence of `missed` rows is
  then not evidence that nothing was missed**, and the `missed` status quietly means something
  different per job. **Enforced in `Cadence.__post_init__`.** With the current
  half-the-interval-floored-at-60-seconds derivation, the reachable range is an interval of **60
  seconds or less** — there the floor wins and meets or exceeds the interval. Above 60s the
  half-interval term or the floor is always strictly smaller, so no valid entry trips it by
  accident. 61 seconds is therefore the shortest interval the cadence table admits.
- The job store is **persistent**, and **configuration tests cannot prove it works.** Jobs are
  reconciled into it with `add`-if-absent / `modify`-if-present, **never
  `add_job(replace_existing=True)`** — the latter recomputes `next_run_time` from now and
  overwrites the persisted past-due value, so a restart after an outage silently discards the
  missed run while every setting still reads correctly. Restart recovery is verified by stopping
  a real process, not by a test.

**Monitoring**

- **"Last success" is the most recent `success` row's `finished_at`** — never the most recent row
  of any status.
- A job with no successful run on record is **overdue**, not quiet.
- **No ingest client is complete until it registers its table in the heartbeat's freshness
  registry.** Liveness is measured from the data (`MAX(ts)` on the ingested table), never from the
  process. An empty registry is worse than none: it reports healthy because it checks nothing.
- **Alert-delivery failures are logged and swallowed; they never fail the monitoring job.** This
  is the only place in the orchestration layer where swallowing is correct, and it is commented as
  such because it contradicts the `@job` decorator directly.

**Infrastructure**

- Container images are **pinned by digest, resolved on the machine that runs them.** A placeholder
  digest in a committed file must be one that cannot resolve, so a missed step fails loudly rather
  than falling back to a floating tag.

---

## 13. Verification conventions

These bind every check under `verify/`, and any check written anywhere else.

- **Container images are referenced as `tag@digest`, never digest alone.** The digest is the pin;
  Docker resolves by it and ignores the tag, so the two cannot disagree about what runs. The tag is
  how the digest is **re-derivable** — when a pin fails, the operator has to know what to pull in
  order to obtain the correct digest, and a bare `name@sha256:…` reference does not say. A digest
  is also not a value a human should be typing: it is resolved and written by
  `verify/preflight.py --write-digest`, because hand-editing it failed twice.
- **A check reports the observed value on failure, never a bare `FAIL`.** The digest it read, both
  device IDs, the byte count, the fire timestamps. A harness that says `FAIL` without evidence
  sends the operator off to re-derive by hand what the harness already had in a variable.
- **A skipped check exits non-zero, and a `SKIP` never reads as green.** A check that quietly
  becomes a no-op when its precondition is missing, and still reports success, is § 2's theme 2 in
  its purest form. Conversely, no check prints `PASS` for something it did not observe.
- **A mount is verified by comparing `st_dev` against the root device** — never free space, never
  path existence. A bind mount into a directory on the root volume reports the root volume's size
  perfectly happily and the path exists either way. `nofail` in `fstab` (§ 9) makes a
  silently-absent volume a designed-for possibility, so this is the check that has to fail when it
  happens.
- **A secret written in two shapes is compared to the two shapes of itself**, not merely validated
  independently. Two values that are each well formed and different produce a container
  initialized with one and an application authenticating with the other, surfacing much later as
  an auth failure pointing at nothing.
- **Verification never prints a secret's value.** Where a failure needs evidence, it reports the
  value's shape — length, characters outside the permitted alphabet — which is enough to act on
  and not enough to leak.
- **Restart recovery is verified by stopping and starting a real process running the real
  scheduler code**, against the real job store, and asserts **exactly one prompt catch-up fire.**
  Not `>= 1`, which passes when coalescing is broken and the job fires once per missed slot; and
  not a count alone, which passes the `replace_existing` bug whose symptom was a correctly-single
  fire one full interval late. **Configuration tests are not evidence of restart behaviour** — a
  harness that mocked the job store, the scheduler, or the decorator would reproduce that bug's
  invisibility exactly.
- **A check that proves a rollback asserts the absence of the work's own write**, not just the
  presence of the failure record. The `failed` row appears whether or not the bookkeeping used a
  separate session; only the sentinel's absence alongside the record's presence shows the two were
  on different connections.
- **Verification apparatus lives in `verify/`, never in `app/`.** A probe job in `app/` means
  production code shipping a job that exists only to be watched. Anything a harness registers in
  the persistent job store it removes again on every exit path, or it keeps firing in production.
---

## 14. Ingest conventions

Learned from the first ingest client (USGS instantaneous values, Phase 3). The first bullet is the
one the rest exist to protect.

- **Every ingest client asserts that the returned `(entity, parameter)` set equals the requested
  set, and hard-fails on any missing pair. A 200 with an empty payload is a failure, not zero
  rows.** Measured against USGS on 2026-08-13: a request for a series a site does not serve
  returns HTTP 200 with `"timeSeries": []` — no error, no flag, and when several entities are
  requested together the missing ones are simply absent while the others return normally. The
  obvious loop — iterate what arrived, write it — is shorter, never raises, and is
  indistinguishable from correct on every run; an entity that drops out of the feed permanently
  produces a job that reports success forever with a row count nobody notices shrinking.
- **An empty result for an available series is not an error; a missing series is. These are never
  collapsed.** Gaps are ordinary — sensor outages, windows before a record began. Treating an
  empty window as fatal makes a backfill unrunnable; treating a missing series as an empty window
  restores exactly the blindness the assertion removes. The test that guards this holds both
  behaviours at once, because two separate tests can each be satisfied by one wrong
  implementation.
- **Source data is upserted on its natural key. Never `DO NOTHING` on a source that publishes
  revisions.** `DO NOTHING` makes reruns safe, passes every duplicate test, and freezes the
  provisional value over the corrected one permanently and silently.
- **`rows_written` counts rows that actually changed the database** — inserts plus genuine
  revisions, measured from `RETURNING` under a `DO UPDATE ... WHERE ... IS DISTINCT FROM`. A plain
  `DO UPDATE` reports its whole input on every rerun, which is § 4's definition violated by a
  number large enough to look reassuring.
- **Backfills chunk by window and resume from `MAX(ts)` in the data, never from a checkpoint file
  or a progress table.** A checkpoint is a second record of the same fact, and when the two
  disagree the checkpoint is what gets believed. A crash after writing rows but before the
  checkpoint re-fetches — harmless. A crash after the checkpoint but before the rows skips work
  that was never done — silent, permanent, indistinguishable from a complete backfill.
- **A backfill is a CLI a human runs, never a scheduled job.** It runs for hours; `coalesce` and
  `max_instances=1` would leave a scheduled copy permanently `running`, which the heartbeat cannot
  distinguish from healthy.
- **Per-source availability, cadence, and period of record are recorded per entity, never assumed
  uniform.** All three vary per site across the four seeded gauges. A uniform assumption is what
  makes a missing series invisible: you cannot assert you received what you asked for if you do
  not know, per entity, what there was to ask for. Cadence is recorded as *documentation of what
  was observed* and is never used to filter — the client stores whatever timestamps arrive.
- **Timestamps are stored `timestamptz` in UTC, converted from the source's own offset.** Never
  stripped, never assumed fixed. Sources spanning observance boundaries shift an hour twice a year
  in a way that looks like the measured thing moved.
- **A source's declared no-data sentinel is dropped, never stored.** USGS publishes `-999999` as
  `noDataValue` and then emits it as an ordinary value; stored as-is it is a number that breaks
  every aggregate it touches while looking like data. Read the sentinel from the payload — the
  series declares it — rather than hardcoding it.
- **Derived values that a source does not publish are not synthesized to fill a gap.** Stage is
  absent at two of the four gauges, and deriving it from discharge through a USGS rating curve is
  rejected rather than deferred: ratings are published as provisional and shift with channel
  features, so applying a current rating to 2008 discharge yields a stage that gauge never read. A
  fabricated number that looks plausible, in a layer that has no confidence gate to catch it.
- **Every ingest table is registered in the heartbeat freshness registry in the commit that
  creates it** (§ 12). Liveness is measured from the data — `MAX(ts)` on the ingested table —
  never from the process. A registered table with no rows at all is **stale, not quiet**, for the
  same reason a job with no successful run is overdue rather than silent. A registered table that
  cannot be queried is a **failed** check, never a skipped one (§ 13).

---

## 15. Multi-endpoint source conventions

Learned when the USGS daily-values endpoint was added beside the instantaneous one (Phase 3.5) and
the assumption underneath Phase 3 turned out to be false. § 14 governs any one ingest client; this
governs a source that speaks through more than one endpoint.

- **A source's period of record is per entity AND per endpoint. Never infer one endpoint's depth
  from another's.** Phase 3 assumed the instantaneous service carried the full record because one
  site does. Measured: **instantaneous retention is a rolling window of recent weeks at three of
  the four gauges**, while the daily endpoint carries 35 years at two of them. The Phase 3
  backfill aborting at Memphis's first window was § 14's guard working exactly as designed — it
  refused a 200 with the series absent rather than writing it as zero rows — and it is what
  surfaced this. Record a start per entity per endpoint, and give the columns names that say which
  endpoint they mean.
- **Three response outcomes are always distinguished and never collapsed:** a body that does not
  parse, a 200 with the requested series absent, and a present series with no values. They arrive
  looking similar and mean different things, and each points at a different fix — the seed's
  record-start floor, the entity's declared availability, and nothing at all respectively. A
  distinct exception type per outcome, not a flag on one.
- **Timestamps without an offset are stored as the calendar date the source stated, with no
  timezone arithmetic. Offset-bearing and offset-free timestamps never share a parsing path.**
  Applying `.astimezone()` to a naive timestamp makes the local machine's zone decide what it
  meant: the same daily value becomes a different **day** in Tokyo than in Denver, plausibly and
  undetectably. Keep the two parsers separate and guard the separation with a test that breaks one
  and asserts the other still works — a comment saying "do not reuse this" is not a guard.
- **Statistic codes are parsed from the response and asserted against the request, never
  hardcoded, and form part of the key of any table storing aggregated values.** Requesting the
  mean and receiving the minimum is a satisfied request to any check that compares only entity and
  parameter, and a minimum stored under the mean's key is systematically wrong in a direction
  nothing downstream can see. Adding the code to a primary key later means rebuilding the table.
- **Measurements of different kinds live in different tables. A discriminator column on a shared
  table is not sufficient when the rows mean different things.** With a `source` column, the
  obvious query returns a silent mixture and every aggregate over it double-counts the overlap;
  the filter that would prevent it is one every caller must remember forever. Separate tables make
  the mistake impossible rather than discouraged.
- **Where two sources cover the same fact, precedence is encoded ONCE as a database view exposing
  which source each row came from. Consumers never re-derive it.** Three copies of a precedence
  rule diverge silently, because each returns a plausible series and nothing compares them. The
  `source` column is not decoration: sources that cover the same fact rarely measure it
  identically, so a series that switches source mid-history has a **seam**, and the column is what
  keeps that seam visible instead of hidden. State the known differences in the view's own
  definition.
- **A backfill never writes to the seed table it reads from.** Discovered boundaries are reported
  for a human to reconcile, in a new numbered migration. A backfill that corrects its own starting
  assumption destroys the only evidence it ever started from the wrong place — the run that would
  have shown the discrepancy is the run that overwrites it. Seeds are human-owned (§ 1).
- **Renaming a table to say what it holds is worth its own migration.** A name that lets a reader
  avoid deciding which measurement they meant will be read as "the complete one" forever. Renames
  are non-destructive and so do not need § 3's archive treatment, but they carry catalog state
  (hypertable registration, compression settings, policies) that must be **read back from the
  catalog afterwards**, never assumed to have survived.
- **A source's period of record is established by a full-range request counting values per
  period, or by an authoritative catalog count — never by probing sample windows and
  generalizing.** A probe measures presence in one window; a period of record is depth, and the
  two are not the same measurement. Phase 3.5 seeded four daily record starts from one-month
  January probes and was wrong at three of the four sites: Memphis answered January 1990 and
  January 2010 and serves nothing at all between 1994 and 2014. **A catalog's date range reports
  an envelope, not what an endpoint will serve** — the same Memphis series is catalogued as
  1933–2026 with 26,886 values — **and where they disagree, what the endpoint serves is what is
  true.**
- **Known data gaps are recorded in a queryable table, not in prose.** The code that needs them
  cannot read markdown, and two layers need them: the backfill, to report an empty window as
  expected rather than as a surprise, and the feature layer, so nothing interpolates a baseline
  across a hole. Gap boundaries are **inclusive of the first and last missing day**, stated in the
  schema, because an off-by-one here silently reclassifies a real boundary day as missing.
  **A gap table is never consulted to decide what not to request:** that lets a human-maintained
  row skip real data with no request, no empty response, and no evidence it happened. Asking and
  receiving nothing is cheap and self-correcting.

---

## 16. Paged API conventions

Learned reading USDA AgTransport through Socrata (Phase 4). § 14 governs any one ingest client and
§ 15 governs a source with several endpoints; this governs a source that returns its answer in
pages. Every bullet describes a failure that reports success.

- **Pagination terminates on an EMPTY page, never on a short one, and a page cap RAISES rather
  than returning a prefix.** `while len(page) == limit` is shorter, reads naturally, and silently
  truncates a dataset: a filtered query or a server-side row cap can return a short page
  mid-sequence, after which the job reports success with a row count nobody can distinguish from a
  small dataset. Returning what was collected when the cap is hit reintroduces the same truncation
  through the safety valve, in the case where something is already known to be wrong. **The offset
  advances by the requested limit, never by the number of rows received** — advancing by the
  received count looks like it handles short pages and instead overlaps the next page by the
  shortfall, which the upsert absorbs invisibly.
- **Every paged query carries an explicit `$order`; ordering is never left to the server's
  default.** Without one, paging can repeat rows and omit others, and the symptom is not "paging is
  broken" — it is duplicate-key noise on the upsert plus a few missing periods, which reads like a
  source problem and gets investigated as one.
- **An error document is distinguished from an empty page.** Both are valid JSON with a length.
  A rejected query read as "a page with no rows" ends the walk and reports a successful read of
  nothing — § 14's empty-payload failure in a different costume.
- **External dataset identifiers are resolved by a human and stored in a table; a NULL identifier
  raises BEFORE any request is issued.** An invented identifier does not fail as a wrong answer,
  it fails as a 404 that reads like a network fault, and the investigation goes to the network.
  Same rule as the AMI id, the image digest, and the gauge site list (§ 1).
- **Published units are stored exactly as published.** Unit conversion is a modelling decision and
  does not belong in ingest. A percent divided by 100 on the way in is two orders of magnitude out
  in a direction that looks entirely reasonable on a chart, and both versions are smooth positive
  series, so nothing downstream can tell.
- **A reported zero is a value, never a skipped row, and is always distinguishable from NULL.**
  `0` means "reported as none" and `NULL` means "not reported". Skipping zeros deletes exactly the
  observations an extreme event produces; coalescing NULL to zero invents measurements out of
  silence. Both are one line long, both look like tidying, and the upsert's change detection must
  use `IS DISTINCT FROM` so a revision from NULL to 0 counts as a change.
- **Storage technology is chosen per table with a measurement behind it, not for consistency with a
  neighbouring table.** Phase 3's own compression measurement concluded that at ~290k rows
  Postgres alone would have been adequate; a weekly table of ten thousand rows does not become a
  hypertable because the table beside it is one.
- **Where a dimension is split across sibling datasets rather than carried as a column, the
  dimension's value is assigned per dataset from a single total mapping, never inferred from record
  content.** USDA publishes the three rate horizons as three datasets with identical field lists,
  not as one dataset with a horizon column. A mapping that is merely sufficient — covering today's
  keys, defaulting the rest — lets a fourth sibling dataset land silently in an existing series,
  writing two different facts onto one primary key. Assert the mapping is total in both directions;
  a new dataset must fail loudly rather than default.
- **Source vocabularies are stored verbatim, including inconsistencies, and guarded with a `CHECK`
  that is a tripwire for unseen values — never normalized on ingest.** USDA publishes `MS Locks 27`
  beside `MS Lock 15`; the plural is stable and it is stored. A normalization step is where the
  join breaks the week a value arrives that the mapping does not cover, and the failure surfaces as
  **missing weeks** rather than as an unmapped value — a shape that reads like a source problem and
  gets investigated as one. The `CHECK` is not the vocabulary: when it fires, measure the new value
  and add it in a new migration, never drop the constraint and never bend the arriving value.
- **A column that would always be NULL is not created. A field the source does not publish is
  recorded as absent in the log, not as an empty column.** An always-NULL column looks like data,
  and every query filtering on it returns nothing forever with nothing to say why. If the value is
  wanted later it comes from whatever source actually publishes it, as its own commit with its own
  measurement.
- **Row counts measured at seed time are stored so a backfill can be checked against them; landing
  fewer rows than the source reported is a truncation signal.** The count goes stale in the safe
  direction — these sources only grow — so it is a floor, and comparing against a floor is the
  cheapest check that the pager did not stop early. Compare **records received**, not rows written:
  rows written counts only what changed, so a correct rerun writes nothing.
- **An absent field, a null-valued field, and an unparseable value are three distinct conditions.**
  Absence of a legitimately optional field is recorded as NULL; an unparseable value **always
  raises**, naming the value. **A blanket `.get()` collapses the third into the first and is
  forbidden** — it turns a corrupt value into whatever the legitimate NULL means, and where that
  meaning is ordinary (a closed river, an unreported week) the corruption is invisible. Which
  fields are optional is a **measurement**, not a judgement: USDA omits `rate` on 774 of 8,260
  records because the river was closed, while a record with no `date` cannot be keyed at all.
- **A row whose measure is legitimately absent is still written.** Skipping it makes the absence
  invisible to everything downstream: the series simply has no January, which nothing can
  distinguish from an ingest that failed to fetch January. A NULL row states the absence; a missing
  row hides it.
- **Freshness is measured over all rows of a table, never over rows whose measure is non-null.** A
  legitimately empty period is not staleness. "Only count rows that have data" reads as a
  refinement and makes the check fire for a whole season, which gets it muted — after which it is
  not watching in the season that matters either.
- **Structural nullability is established per column by measurement, never by analogy to a sibling
  column that was measured.** A column made nullable on the strength of a neighbour's finding is a
  guess that later reads as verified, because the commit that made it cites a measurement of
  something else.
- **Where a source publishes both an explicit zero and an absent field for the same measure, they
  are two distinct claims and both are preserved. A zero is a measurement; an absence is not.**
  Measure both populations before deciding what either means: USDA publishes `tons = 0` on 8,218 of
  26,144 lock-movement records and omits `tons` on 108, so zero is the *routine* way the source says
  "nothing moved" and silence is therefore saying something else entirely. Where a source has a way
  of stating a zero and uses it, coalescing its silences into zeros fabricates measurements at
  exactly the rate the source declines to report — and it does so in the ingest layer, below every
  gate that exists to catch a fabricated number.
- **The MEANING of a NULL is established per column by measurement, and two columns given the same
  three-state handling may mean entirely different things by it.** `barge_rates.pct_of_tariff` and
  `lock_movements.tons` receive identical absent/null/unparseable treatment; a NULL rate is winter
  navigation closure, seasonal and physical, while a NULL tonnage is a reporting gap that says
  nothing about the river. **A column comment copied from the sibling asserts something unverified**,
  and it is worse than an absent comment: it reads as measured, it is the thing the next reader
  reasons from, and the two shapes look identical in the diff. Same handling is not same meaning.
