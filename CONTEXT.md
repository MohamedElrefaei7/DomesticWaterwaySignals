# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-10 (Phase 2 — orchestration skeleton)

---

## Current state

**PHASE 1 IS COMPLETE AND VERIFIED ON THE INSTANCE, as of 2026-08-10.** Recorded here late — see
the process note at the end of `§ Up Next`, which exists because of this. What was confirmed on
the machine:

- `terraform apply` run: **17 resources created**, matching the plan the tests assert.
- The data volume is **mounted by filesystem UUID** and survived a **reboot**.
- Docker installed at the **pinned, held versions**, with a container run confirmed **after** the
  reboot.
- **`ens5`** discovered as the external interface and persisted by its boot unit.
- **ufw active with exactly the three expected rules**.
- **`DOCKER-USER` carrying four correctly-ordered, interface-scoped rules in both v4 and v6**, all
  confirmed **after a reboot** — which is the only thing that proves raw iptables rules persist.

The paragraphs further down this file that describe provisioning 1/2/3 as "written and
unit-tested, not yet run against the instance" are **superseded by the above**. They are left in
place as the record of what each commit knew at the time; where they conflict with this section,
this section is current.

**Phase 2 verification harness written; 25 unit tests green offline.** The Phase 2 orchestration
layer itself is unchanged by this commit. 124 tests green across the repo (61 Phase 1, 38
orchestration, 25 verification).

- `verify/preflight.py` — five gates: image pinned as `tag@digest` and not the placeholder; `.env`
  mode 600; `POSTGRES_PASSWORD` and `DATABASE_URL`'s password **compared to each other** and both
  64-hex; `/mnt/data/timescaledb` on a **different `st_dev`** than `/`; `schema_migrations` row
  count equal to the number of migration files. Plus `--resolve-digest` / `--write-digest`, which
  exist because hand-editing the digest failed twice — once as an unsaved edit, once as a
  fabricated value.
- `verify/restart_recovery.py` — spawns a **real scheduler process**, stops it, starts it again,
  and asserts **exactly one prompt catch-up fire**. Drives the real `register_jobs()`, the real
  `@job`, the real `SQLAlchemyJobStore`. Mocks nothing, by design.
- `verify/failure_survives.py` — a probe writes a sentinel and raises; asserts the `failed` row
  **and** the sentinel's absence **and** the exception's propagation.
- See `CLAUDE.md § 13` for the conventions all three obey.

- `migrations/` + `app/orchestration/migrate.py` — three numbered migrations and the runner that
  applies them. `schema_migrations` is bootstrapped by the runner outside the numbered sequence;
  checksums of every already-applied file are verified before anything pending is applied; one
  transaction per file with the record inside it; `-- migrate:no-transaction` honoured only on
  line 1 and exercised by a real migration (`0003`, a `CREATE INDEX CONCURRENTLY`) rather than
  only by a test; a pending version below the highest applied one is a hard failure. See
  `CLAUDE.md § 12`.
- `app/orchestration/job.py` — the `@job` decorator. Bookkeeping on its own connection, `running`
  row committed before the wrapped function is called, always re-raises. `rows_written`
  distinguishes `NULL` from `0` in both the decorator and the column.
- `app/orchestration/cadence.py`, `scheduler.py`, `heartbeat.py` — one cadence entry (`heartbeat`,
  15 min, overdue after 45 min, grace 450s), APScheduler with `SQLAlchemyJobStore`, an
  `EVENT_JOB_MISSED` listener writing `missed` rows, and a heartbeat that imports the cadence
  table and defines no threshold of its own.
- `docker-compose.yml` — the database service only. **The image digest is a deliberately
  unresolvable all-zero placeholder** and must be replaced with one resolved on the instance
  (`§ Up Next` step 1). It parses, and it cannot pull.
- All 23 mutation-table rows confirmed: each performed, observed to turn its named test red with
  the message recorded, then restored, and the suite re-verified green.

- **A real bug was found by live measurement and fixed, and it is worth reading in full because it
  is `CLAUDE.md § 2` theme 2 happening again inside the commit that cites it.** The scheduler
  originally registered jobs with `add_job(..., replace_existing=True)`, which is the form every
  APScheduler example uses. `_real_add_job` computes a fresh `next_run_time` from now, and
  `update_job` writes it over the persisted one — so a restart after an outage **discarded the
  past-due fire time before APScheduler's misfire handling ever saw it**. The job neither caught
  up nor recorded a miss; it silently resumed on a clean schedule. Measured with a 20-second
  interval: stopped 00:53:15, restarted 00:54:05, next run scheduled 00:54:25 — a full fresh
  interval later, no `missed` row. **All three configuration tests (`coalesce=True`,
  `misfire_grace_time=450`, `SQLAlchemyJobStore`) were green throughout**, which is precisely the
  prior project's "ten green scheduler tests" failure reproduced. Fixed by `register_jobs()`:
  add-if-absent, modify-if-present, so a past-due `next_run_time` survives into the new process.
  Re-measured after the fix: three missed slots collapsed into **one prompt catch-up**, then
  normal cadence. Guarded by `test_a_past_due_next_run_time_survives_a_restart`, which was itself
  mutation-confirmed — it turns red under the old implementation while all seven configuration
  tests stay green.
- Two tests were found to be **vacuous by mutation and strengthened**, not worked around:
  `test_applying_records_version_filename_and_checksum` compared the recorded checksum against
  `migrate.checksum_of()`, so a runner recording a constant satisfied both sides of the equality;
  it now computes SHA-256 independently and asserts the digest shape.
  `test_running_row_survives_a_rollback_inside_the_wrapped_work` only rolled back and returned,
  which a shared-session decorator survives; the wrapped work now rolls back **and raises**, which
  is the path where a shared session actually unwinds the bookkeeping row.
- `pytest.ini` was added to register the `integration` marker. It is **not in the commit brief's
  file list**, but the brief's Tests section requires the marker be registered, and `pytest.ini` is
  where that goes. Verified it changes nothing else: identical pass/skip counts with and without
  it, differing only in the four `PytestUnknownMarkWarning`s it removes.
- The integration tier **skips with a stated reason** when `DATABASE_URL` is absent — never
  silently passing. Its schema reset drops only non-extension-owned objects (filtered on
  `pg_depend.deptype = 'e'`) rather than `DROP SCHEMA public CASCADE`, which deadlocked against
  TimescaleDB's background workers roughly one run in four.

**Provisioning 3 of 3 written and unit-tested, not yet run against the instance.** Phase 1
(Terraform) is defined but not applied; no `terraform apply` has run.

- `infra/provision/configure_firewall.py` configures ufw (host) and `DOCKER-USER` (containers) as
  two gates in series — see `CLAUDE.md § 11`. ufw is `deny incoming`/`allow outgoing`, all rules
  added before a forced `ufw --force enable`, port allowlist `{22, 80, 443}` with SSH scoped to
  `--admin-cidr` (required, no default). `DOCKER-USER` rules are read from the interface file
  provisioning 2 writes, flushed then re-appended (conntrack `RETURN` first, terminal `DROP` last,
  both `-i`-scoped; `RETURN` never `ACCEPT`), applied identically to `iptables` and `ip6tables`. A
  missing or empty interface file raises before a single command runs. Docker is restarted after
  `ufw enable` and before `DOCKER-USER` rules are (re)applied, since ufw's table rewrite discards
  Docker's own chains. `--docker-user-only` reapplies just the `DOCKER-USER` half (never touches
  ufw) and is what `dws-docker-firewall.service` (`Type=oneshot`, `RemainAfterExit=yes`,
  `After=docker.service dws-external-interface.service`) invokes at boot, because raw iptables
  rules — unlike ufw's own config — do not survive a reboot on their own.
- `tests/provision/` — 65 tests green (46 from provisioning 1+2 plus 19 new: `test_ufw_rules.py`
  and `test_docker_user_rules.py`). All 19 required mutation-table rows confirmed: each performed,
  observed to turn its named test red, then restored, and the full suite re-verified green
  afterward. `conftest.py` extended with a `fake_interface_file` builder (same pattern as
  `fake_os_release`/`fake_proc_net_route`), reusing the existing `fake_runner`/`FakeCommandRunner`
  fixture rather than restructuring it.
- One interface-design correction made while writing the CLI, not called out explicitly in the
  spec: `--admin-cidr` can't be unconditionally `required=True` in argparse, because
  `dws-docker-firewall.service`'s boot-time `--docker-user-only` invocation never passes it (it
  never touches ufw and has no CIDR to scope). `--admin-cidr` defaults to `None` at the argparse
  level; `main()` enforces "required unless `--docker-user-only`" explicitly before calling into
  `configure()`.
- CLI wiring verified end-to-end under `--dry-run` against a real fixture interface file (no
  `ufw`/`iptables` on this dev machine, so `--dry-run` is as close to the real CLI path as is
  reachable off-instance): full-run ordering, `--docker-user-only` (confirmed no `ufw` command
  appears), missing-interface-file, empty-interface-file, and missing-`--admin-cidr` all print/exit
  as designed.
- Not yet done, and out of scope for this write-up: the live verification steps (SSM session
  check, `--dry-run` review, the real `sudo` run, the post-enable and post-reboot checks) — those
  require the instance and are the human's, per `CLAUDE.md § 1` and § 9's "no agent connects to
  the server."
- `DOCKER-USER`/ufw and the Compose systemd unit are no longer "not started" as of this commit —
  the code and unit exist; they are simply not yet applied anywhere.

- `infra/provision/install_docker.py` installs Docker Engine and the Compose plugin at exact
  pinned versions (explicit `pkg=version` install strings, `apt-mark hold` after), via Docker's
  official repository — GPG key fetched to a temp file, content-validated, then dearmored into a
  repo-scoped keyring, never `apt-key add`. `infra/provision/discover_external_interface.py`
  identifies the default-route interface from `/proc/net/route`, never "isn't loopback," and
  writes it to `/etc/dws/external-interface` via its own boot-ordered
  `dws-external-interface.service` (`After=network-online.target`,
  `RemainAfterExit=yes`), so provisioning 3 reads a file instead of re-deriving the value. See
  `CLAUDE.md § 10`.
- `tests/provision/` — 27 tests green (15 from provisioning 1 plus 12 new). All 11 required
  mutation-table rows confirmed: each was performed, observed to turn the named test red, then
  restored. `conftest.py` extended with a `FakeCommandRunner` (an explicitly-injected `run`
  callable, not a global `subprocess.run` monkeypatch like provisioning 1's `StubRunner`, per this
  commit's spec) plus `fake_os_release` and `fake_proc_net_route` builders.
- `install_docker.py`'s real (non-dry-run) `install()` needed a way to avoid writing to the real
  `/etc/apt/sources.list.d/docker.list` during tests; added an internal `sources_list_path`
  parameter with no CLI flag (the interface deliberately doesn't expose one — the real destination
  is always the same fixed path) so tests can inject a tmp path while the real script keeps using
  the constant.
- CLI wiring verified end-to-end: `discover_external_interface.py` against a real fixture
  `/proc/net/route` tree (no subprocess calls at all — pure file I/O, so no stubbing needed);
  `install_docker.py` with stubbed `dpkg`/`curl`/`gpg`/`install`/`apt-get`/`apt-mark` on `PATH`,
  confirming the full command sequence and generated sources.list content, and confirming no real
  `/etc/apt` files were touched on this dev machine.
- **Doc placement judgment call, flagged here rather than silently applied:** the spec's Doc
  updates listed two Terraform-specific bullets (ASCII-only AWS string fields; `ignore_changes =
  [associate_public_ip_address]` on `aws_instance`) under the new `§ 10`. Given last commit's `§ 5`
  / `§ 9` split was exactly this failure shape — a Terraform-specific fact stated somewhere other
  than the Terraform section — they're recorded in `§ 8` (Terraform conventions) instead, and the
  canonical deploy path (`/opt/inland-waterway-signals`, per this commit's decision 9) extends the
  existing `§ 5` deploy-path bullet rather than adding a second, competing statement in `§ 10`.
  **Applied, outside this session:** commit `048fdcf` ("typoes in compute.tf and security.tf")
  landed between the provisioning-2 report and its commit, adding the `ignore_changes` lifecycle
  block to `compute.tf` and fixing the em-dash-to-hyphen `description`/comment strings in
  `compute.tf` and `security.tf` — the same two things `§ 8` now documents as contract. One
  side effect of that pass: a comment on `compute.tf`'s `metadata_options` block read `CLAUDE.md
  § a8` (a stray character from the em-dash cleanup); corrected back to `§ 8` in this commit.
- Docker install, once it runs on the instance, opens a **real, temporary, accepted risk window**:
  Docker inserts its own `DOCKER-USER` iptables chain with no restriction until provisioning 3
  populates it. Container traffic is not firewalled by anything this project controls during that
  window — only the security group, unaffected by any of this. Not a defect; schedule provisioning
  3 promptly rather than leaving a freshly-Dockerized instance unattended.
- `DOCKER-USER`/ufw and the Compose systemd unit are explicitly **not started** — provisioning 3.
  **Resolved as of provisioning 3, above:** `configure_firewall.py` and
  `dws-docker-firewall.service` now exist and close this risk window once applied on the instance;
  the Compose systemd unit remains a later commit (Phase 2 brings up the stack it would start).

- `infra/provision/mount_data_volume.py` identifies the data volume by AWS volume ID matched
  against the NVMe block-device serial (dash-stripped), formats it only if `blkid` reports no
  filesystem, and writes an idempotent, UUID-keyed `fstab` entry with `nofail`. See
  `CLAUDE.md § 9`.
- **Resolved:** the `§ 5` / `§ 9` split flagged in the previous report — `§ 5` said disks are
  identified via the NVMe controller path, `§ 9` said the code actually reads the block-device
  path — has been folded into a single statement in `§ 5` (block-device path, with the reasoning
  inline); `§ 9` now just points to `§ 5` instead of restating it. No more than one place says
  what the identification method is.
- `tests/provision/` (`pytest`, offline, no root/network/instance) — 15 tests green. All 12
  mutation-table rows confirmed: each was performed, observed to turn the named test red, then
  restored. Two fixture gaps were found and fixed during that process, not just the code: the
  zero-match fixture for "never returns a device on failure" had no non-root device for a
  topology-fallback bug to find, and the fstab fixture's root line was already in the exact
  single-space form a "rewrite from parsed fields" bug would reproduce — both let a real mutation
  pass green on the first attempt. Also fixed: a bare `from conftest import X` in the two later
  test files collided with `tests/terraform/conftest.py`'s own `conftest` module name when both
  suites run in one `pytest` invocation; resolved by exposing the shared test double as a fixture
  instead of an import, touching only `tests/provision/`.
- CLI wiring verified end-to-end under `--dry-run` with stubbed `blkid`/`mkfs.ext4`/`mount`/
  `findmnt` on `PATH` (this dev machine has no `blkid`, so this is as close to the real CLI path as
  is reachable off-instance): both the "already formatted" and "no filesystem yet" branches print
  the expected actions, fstab is untouched, and the mount point is not created.
- (At the time provisioning 1 landed: Docker install and interface discovery were not yet
  started. Both landed in provisioning 2; `DOCKER-USER`/ufw landed in provisioning 3, above. The
  Compose systemd unit remains a later commit.)

- `CLAUDE.md` seeded with the contracts carried forward from the prior project (`Trade_Analysis_Project`).
- `.gitignore` committed, verified not self-excluding.
- `infra/terraform/` defines the full environment: purpose-built VPC + public subnet, a
  three-port ingress allowlist with mandatory egress, a separate `prevent_destroy`+encrypted EBS
  data volume, an EC2 instance with IMDSv2 required and a pinned AMI, a `prevent_destroy` EIP, and
  an SSM-only instance role. See `CLAUDE.md § 8`.
- `tests/terraform/` (`pytest`, offline, no AWS credentials/network/binary needed) — 15 tests
  green, and all 10 load-bearing decisions confirmed by mutation: each was reverted, the named
  test was observed to fail, then restored.
- `terraform init && terraform validate` succeed locally (AWS provider resolved to 5.100.0).
  `.terraform.lock.hcl` is committed and correctly not gitignored.
- **Re-verified from a fresh session:** `terraform plan` produces exactly 17 resource addresses,
  matching the spec (VPC, subnet, IGW, route table, route table association, security group, 3
  ingress rules, 1 egress rule, IAM role, instance profile, policy attachment, instance, EIP, EBS
  volume, `aws_volume_attachment.data`) — `test_data_volume_is_a_top_level_resource` asserts both
  the volume and the attachment exist, so the pairing is guarded. `test_ssh_ingress_cidr_is_a_variable_reference_not_a_literal`
  was re-mutated (port range widened to 0–65535): it fails on the `assert ssh_rules` not-found guard
  before touching `cidr_ipv4`, not on an unguarded lookup — confirmed not a vacuous pass.
  `.terraform.lock.hcl` was missing `h1:` hashes for any platform but the one that ran `init`
  (darwin_arm64); ran `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64` and
  confirmed the file grew (one `h1:` hash → two). See `CLAUDE.md § 8`.
- Live-verified: `terraform plan` with a deliberately broad `ssh_admin_cidr` is rejected by the
  variable `validation` block (exit 1, no resources created). One correction to the plan as
  written: Terraform authenticates and prints the full resource-creation preview *before* raising
  the validation error, not before — see the Housekeeping note below.
- AWS budget alert: **not yet configured** — must exist before anything is provisioned.

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

---

## Key decisions

- **Data sources selected for cleanliness, not richness.** The prior project died on raw AIS: five
  distinct corruption mechanisms in arrival detection, 34.8% of stored arrivals with zero supporting
  position pings in the preceding six hours. Selection criterion here is data that arrives already
  clean and structured, from a publisher with an institutional obligation to keep publishing.
- **Full historical backfill on day one.** USGS from 2007-10-01, USDA Socrata with history. The prior
  project's later phases were gated on data accumulation; that gate does not exist here.
- **USACE LPMS routed around** — weekly lock movements come from USDA Table 10 instead. See
  `CLAUDE.md § 6`.
- **CBOT futures / Gulf basis is an extension, not the core.** Barge rate as percent of tariff is a
  legitimate target on its own.

---

## Open questions

- **Raw 15-minute gauge readings vs. hourly aggregates on ingest.** Must be decided before Phase 3.
  Size estimate: ~96 readings/day × ~6,880 days × ~15 sites × 2 params ≈ **20M rows** for the full
  raw backfill — roughly half the prior project's 38.5M, and this row is narrower. Hourly would be
  ~5M. Volume is not the deciding factor at this scale.
- **Whether USGS instantaneous-values requests can span the full period of record in one call.**
  Verify empirically in Phase 3 and chunk the backfill by date window if not. The plan currently
  assumes a single request; that assumption has not been tested.

---

## § Up Next

**Phase 1 is done — the block that used to live here is retired.** It listed the provisioning-3
firewall run, `terraform apply`, and the budget alert as outstanding. All of it has been completed
and reboot-verified on the instance; see `§ Current state`. Nothing from Phase 1 is pending.

**Process note — commits:** after any Claude Code session reports a commit, run `git log --oneline
origin/main` from your own terminal before treating the work as real — three separate sessions
today reported committed work that had not been pushed.

**Process note — live-verification outcomes are written back into `CONTEXT.md` as their own small
commit.** This is the rule this file most needs and has never followed. Every session so far has
written "not yet run against the instance" and none has come back to correct it once the
verification succeeded, so the log sat **three commits behind reality** while `CLAUDE.md § 0` names
this file as the second-highest authority in the project. The Phase 1 status corrected at the top
of `§ Current state` was stale for exactly that reason, and the Phase 2 session then reasoned from
it and wrote a discrepancy note about work that had in fact already been done. Running the
verification and not recording the result is not finishing the verification.

**Phase 2 live verification — on the instance. Now a harness, not a checklist.**

Every step below is a command that checks itself and exits non-zero when it should. Steps 6 and 7
are the ones that matter; the rest is setup. A `SKIP` is not a pass — the run exits non-zero and
says what was not checked. Nothing here has been run yet.

1. `docker pull timescale/timescaledb:2.26.2-pg16` (the tag is in `docker-compose.yml`), then
   `python3 -m verify.preflight --resolve-digest` — inspect the digest it prints.
2. `python3 -m verify.preflight --write-digest`, review the printed `git diff`, commit it. **Do not
   hand-edit the digest** — that is what failed twice. The tag stays on the line; the digest is the
   pin and the tag is how the pin is re-derivable.
3. `python3 -m verify.preflight` — all five gates. Expect the `.env` password-match gate, the
   `st_dev` gate, and the migration-count gate to pass, and the run to exit **zero with no SKIP**.
4. **Tamper test.** Append a blank line to `migrations/0001_extensions.sql`, run the migration
   runner, confirm it aborts naming the file and both digests, `git checkout -- migrations/`, re-run,
   confirm clean. A guard that has never been seen refusing is not a guard.
5. Run the orchestration integration suite on the instance against the real database:
   `python3 -m pytest tests/orchestration -q`. **Compare the count to the offline run** — offline it
   is `14 passed, 24 skipped`; on the instance it must be `38 passed`. If the count has not risen,
   the `integration` marker is skipping and nothing was verified here.
6. `python3 -m verify.restart_recovery` — expect exactly one prompt catch-up fire. **This takes
   about 6½ minutes** and that is not tunable downwards: the probe obeys the cadence contract, and
   `CLAUDE.md § 12`'s grace-floor rule puts a 61-second minimum on any interval, so the defaults
   are a 90s interval and a 270s outage. Paste the fire timestamps and
   `select job_name, status, started_at from job_runs order by started_at desc limit 10`.
7. `python3 -m verify.failure_survives` — expect a `failed` row carrying its message, and no
   sentinel.
8. `docker compose restart timescaledb`, then confirm the scheduler reconnects and the next
   heartbeat records a row.

**Write the outcomes back into this file as their own small commit.** See the process note above.

**Host connectivity for steps 3 and 5–8, and it is a deliberate temporary deviation.**
`docker-compose.yml` publishes **no ports**, per `CLAUDE.md § 6`. The Phase 2 scheduler runs from a
host venv, so it needs host reachability for now. Use an override file kept **outside the repo**,
so it cannot be committed by accident and cannot outlive its reason:

```yaml
# /root/dws-local-ports.yml — TEMPORARY, Phase 2 verification only
services:
  timescaledb:
    ports: ["127.0.0.1:5432:5432"]
```

Bring the stack up with `docker compose -f docker-compose.yml -f /root/dws-local-ports.yml up -d`.
Delete it once the `worker` service is containerized; at that point `DATABASE_URL` becomes
`timescaledb:5432` and nothing needs a published port.

Then, in order: Phase 3 USGS ingest and backfill — the USGS client, the gauge seed list (**the
human's, per `CLAUDE.md § 1`**), the backfill, and enabling TimescaleDB compression with the ratio
**measured, not quoted**; that measurement is what justifies TimescaleDB over a managed database
and it must be a number taken here. Phase 3 is also where the still-open **raw 15-minute readings
vs. hourly aggregates** decision has to be made (see `§ Open questions`), and where the
freshness-registry requirement in `CLAUDE.md § 12` first binds: no ingest client is complete until
it registers its table.

---

## Housekeeping — open, non-blocking

- ~~`CLAUDE.md § 12` forbids a cadence entry whose `misfire_grace_time` >= its `interval`, and
  `cadence.py` does not enforce it.~~ **Closed:** enforced in `Cadence.__post_init__`.
  **And the note that opened this item was wrong on the numbers** — it said the derivation
  produces a violation "for any interval under two minutes." Measured: the reachable range is an
  interval of **60 seconds or less**. Between 61s and 120s the 60-second floor is already strictly
  shorter than the interval, so those are valid. 61s is the shortest interval the table admits.
  Corrected in `CLAUDE.md § 12`.
- **The verification probe jobs write permanent `job_runs` rows** (`verify_restart_probe`,
  `verify_failure_survives_probe`). That is correct — `job_runs` is append-only and those rows are
  the record that the verification ran — but it means the heartbeat would report them overdue if
  they ever appeared in the cadence table. They do not, and must not.
- `verify/restart_recovery.py` removes its probe from `apscheduler_jobs` on every exit path. If it
  ever warns that it could not, run `python3 -m verify.restart_recovery --cleanup-only` **before**
  starting the production scheduler — a leftover probe row keeps firing, because `register_jobs()`
  never removes jobs it does not recognise.

- **`apscheduler_jobs` must be EXCLUDED from dumps when backups land in Phase 11.** Restoring a
  stale scheduler state is worse than restoring none: the rows carry `next_run_time` values from
  whenever the dump was taken, and a job store full of long-past fire times interacts with
  `coalesce` and misfire grace in ways nobody reasoned about. `pg_dump --exclude-table`.
- **The scheduler runs from a host venv, not a container.** Containerizing it as the `worker`
  service is a later commit, and it **will need its own restart-recovery verification** — being
  inside a container with `restart: unless-stopped` changes the process lifetime this entire
  design is about, and this commit has already demonstrated that the settings can all be correct
  while the behaviour is not.
- **Data-liveness checks are deferred to Phase 3 and are now a contract requirement there**
  (`CLAUDE.md § 12`). The Phase 2 heartbeat checks job overdue-ness only. An empty freshness
  registry was deliberately not built: it would report healthy because it checks nothing.
- The heartbeat's first-ever run alerts about itself — it has no successful run on record yet, and
  "never succeeded" is deliberately treated as overdue rather than as quiet. Expected once, on
  first start; it goes quiet after the first success.
- **`missed` rows are only reachable for jobs whose grace is shorter than their interval.** With
  `coalesce=True` APScheduler evaluates only the last missed fire time, which is never more than
  one interval old, so any job hitting the 60-second grace floor always catches up instead of
  recording a miss. The heartbeat (900s interval, 450s grace) can produce both. An absence of
  `missed` rows is not by itself evidence that nothing was missed.
- `infra/terraform/terraform.tfstate` exists in the working tree. **Checked, and clean:** it is
  untracked, correctly matched by `.gitignore:16` (`*.tfstate`), and `git log --all -- '*.tfstate'`
  is empty, so it has never been committed. Noted only because Terraform state carries secrets in
  plaintext and its presence next to a clean `git status` invites the assumption without the check.
- **AWS budget alert — status unknown, and it no longer blocks anything by itself.** It was
  recorded here as blocking `terraform apply`, and `terraform apply` has since run. Whether the
  alert was configured first was not recorded. Confirm it exists now: there is a running instance,
  an EIP, and an EBS volume billing continuously.
- Domain not purchased. Blocks Phase 10 only.
- **State is local, and there is now applied infrastructure behind it.** This was written when
  nothing had been applied, which made it theoretical; it is not any more. If
  `infra/terraform/terraform.tfstate` is lost, `prevent_destroy` protects nothing, because
  Terraform no longer knows the data volume or the EIP exist. An S3 backend with locking was out of
  scope for Phase 1 and is now the highest-value piece of unowned work in `infra/`.
- **State is local this commit.** No S3 backend, no state locking — that is explicitly out of
  scope for Phase 1. If `terraform.tfstate` is lost, `prevent_destroy` protects nothing, because
  Terraform no longer knows the data volume or the EIP exist. Do not treat local state as durable
  once anything has actually been applied.
- **The `DOCKER-USER` terminal `DROP` is not observable from outside while the security group is
  tighter than it.** The security group already blocks every ingress port `DOCKER-USER` would
  drop, so proving the drop end-to-end would require temporarily widening the security group,
  which is out of scope for this commit. What live verification step 7 (above) actually confirms
  is rule presence, order, and interface scoping — plus step 8's proof that legitimate egress
  still flows. This is a real verification gap, not a claim of end-to-end proof.
- `docker-ce-rootless-extras` remains installed unpinned and unheld (provisioning 2 pins and holds
  `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin` only).
- Copying `infra/provision/*.service` unit files into `/etc/systemd/system/` and running `systemctl
  enable` is a manual step on the instance until a deploy script exists to do it.