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