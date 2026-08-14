# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-13 (Phase 3 ingest written; live steps outstanding)

---

## Current state

**PHASE 3 (USGS INGEST AND BACKFILL) IS WRITTEN AND VERIFIED OFFLINE, as of 2026-08-13. It has
not run against the instance, and the compression ratio has therefore NOT been measured.**

- **156 tests green with ZERO SKIPS**, including the whole integration tier. Offline (no
  `DATABASE_URL`) the same suite is `116 passed, 40 skipped`. The integration tier ran against a
  **throwaway local TimescaleDB container on the pinned image** (`timescale/timescaledb:2.26.2-pg16`,
  same digest as `docker-compose.yml`), not against the instance — so the schema, the hypertable,
  the compression settings, and the upsert semantics are verified against the real engine, while
  everything requiring real USGS data or the real deployment is not.
- **All 15 mutation-table rows confirmed**: each performed, observed to turn its named test red
  *on that test's own assertion*, then restored, with the suite re-verified green afterwards. One
  needed redoing — the first attempt at "resume from a checkpoint instead of `MAX(ts)`" failed
  with a `NameError`, which proves only that the test runs; it was rewritten as a functional
  checkpoint store that merely disagrees with the data, and then failed on the real assertion.
- **The pinned image's catalog API was read back rather than assumed**, since several TimescaleDB
  objects were renamed across 2.x. Observed on 2.26.2: `create_hypertable(..., by_range(...))`
  applies; the settings view is `timescaledb_information.compression_settings`; the stats function
  is `hypertable_compression_stats`; `segmentby` reads back as `['usgs_site_id', 'param_code']`,
  `orderby` as `[('ts','DESC')]`, the chunk interval as 7 days, and the policy registers as
  `policy_compression` with `compress_after = 30 days`. Both the stats function and the settings
  view are discovered from the server's own catalog at call time, so an image bump that completes
  the rename fails loudly rather than silently.

**What is NOT done, and is the human's:** every step of the live procedure at the end of
`§ Up Next` — the migrations on the instance, the one-site rehearsal, the full backfill, the
per-site `min(ts)` comparison against the seeded `record_start`, **the compression measurement**,
and the scheduler/freshness checks. No agent has connected to the server (`CLAUDE.md § 9`).

**No USGS endpoint was called by this commit.** The three fixtures under `tests/ingest/fixtures/`
are captured response shape; no test in the repo makes a live HTTP request. Two consequences worth
knowing before step 3: the `startDT`/`endDT` explicit-UTC form (`2026-08-01T00:00:00Z`) and the
pinned `format=json,1.1` have not been exercised against the live service. Both fail loudly if
wrong — a rejected request, not a quiet one.

### What Phase 3 built

- `migrations/0004_gauges.sql` — the site registry, seeded with **exactly four human-approved
  sites** and carrying `available_params`, `native_cadence_minutes`, and `record_start` **per
  site**. `0005` creates `gauge_readings` and converts it to a hypertable **while empty**, 7-day
  chunks. `0006` enables compression (segment by `usgs_site_id, param_code`, order by `ts DESC`,
  policy at 30 days) and deliberately **records no ratio**.
- `app/ingest/usgs_client.py` — parses, and **asserts the returned `(site, parameter)` set equals
  the requested set**. Verification runs when the function is *called*, before a single reading is
  yielded, so a caller writing as it iterates cannot commit half a batch.
- `app/ingest/gauges.py` — the runtime loader reads the deployed table; a parser reads the seed
  out of `0004` directly so the **unit tier can guard the site list with no database**. One copy
  of the seed, two readers — rather than a Python mirror of the four rows, which would be two
  tables of the same fact.
- `app/ingest/usgs_ingest.py` — the upsert, the hourly poll, and the compression-measurement
  query. `app/ingest/backfill.py` — windowed, resumable, a CLI.
- `app/orchestration/heartbeat.py` — **the freshness registry**, which `CLAUDE.md § 12` has
  required since Phase 2 and which Phase 2 deliberately did not ship empty.

### Decisions worth reading before changing anything here

- **Discharge (`00060`) only. The absence of stage is recorded, not worked around.** Stage is
  unavailable at Memphis and Vicksburg (USGS states their gage height comes from the USACE Memphis
  District). **Deriving stage from discharge via a rating curve is REJECTED, not deferred**: USGS
  publishes ratings as provisional and shifting with channel features, so applying a current
  rating to 2008 discharge yields a stage that gauge never read — a fabricated number that looks
  plausible, in a layer with no confidence gate to catch it. **The thesis is therefore now stated
  in discharge**, which is the physical quantity the draft constraint actually runs on. Stage from
  USACE Rivergages is a possible later addition **whose absence degrades nothing**.
- **The "raw 15-minute readings vs. hourly aggregates" open question is answered more precisely
  than it was asked: it is native cadence PER SITE — 15, 30, and 60 minutes.** Nothing aggregates
  and nothing resamples; the client stores whatever timestamps arrive. `native_cadence_minutes` is
  documentation of what was observed and is never used to filter, which is commented in the
  migration because someone will otherwise reach for it.
- **This revises the volume estimate sharply downward.** The earlier figure was ~20M rows, built
  on ~15 sites × 2 params × 96 readings/day. The real shape is **4 sites × 1 param** at 96/48/24/24
  readings per day — roughly **1.3M rows** for the full backfill, about 6% of the earlier estimate.
  Volume is now decisively not a factor in any storage decision, and the compression ratio is
  worth measuring for the engineering claim rather than for the disk.
- **`rows_written` counts rows that actually changed the database**, not rows parsed. The upsert
  carries `WHERE (value, qualifiers) IS DISTINCT FROM (EXCLUDED...)` and counts `RETURNING`, so a
  rerun over unchanged data reports 0 — truthfully — instead of reporting its whole input.
- **A registered ingest table with no rows is STALE, not quiet.** So the heartbeat alerts about
  `gauge_readings` from the moment `0005` applies until the backfill puts rows in it. Expected,
  once, exactly like the heartbeat's own first run alerting about itself.

**PHASE 2 IS VERIFIED ON THE INSTANCE, as of 2026-08-11.** Recorded the same day, per the process
note at the end of `§ Up Next`. Reported back from the machine:

- **125 of 125 tests green on the instance, with ZERO SKIPS.** This is the number that matters and
  the reason step 5 asked for a comparison: offline the run is `101 passed, 24 skipped`, because
  the integration tier skips itself when `DATABASE_URL` is absent. Zero skips means all 24
  integration tests actually executed against the real database rather than quietly vanishing from
  a green report.
- **Tamper guard observed refusing**, against a database carrying real state — not a fixture. The
  runner aborted naming the file and both checksums. `CLAUDE.md § 3`'s guard has now been seen
  saying no; a guard that has never refused is not a guard.
- **Restart recovery: one fire, 0.43s after restart**, following a three-slot outage. Exactly one,
  and prompt — not once per missed slot, and not one full interval late. This is the behaviour no
  configuration test can establish, and the one whose absence this project shipped and caught
  earlier in Phase 2.
- **Failure-survives: all three observations.** The `failed` row present with its message, the
  sentinel absent, the exception propagated. The sentinel's absence alongside the record's
  presence is what demonstrates the `@job` bookkeeping ran on a separate connection.

**Not reported back, so not recorded as done:** the digest resolution and rewrite (steps 1–2), the
full `preflight` gate run (step 3), and the `docker compose restart` reconnect check (step 8).
Steps 4–7 could not have produced the results above without a working database, `.env`, and applied
migrations, so those preconditions clearly held — but "clearly held" is an inference and the gates'
own output was not pasted. Run `python3 -m verify.preflight` and confirm it exits zero with no
`SKIP`, and the record is complete.

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

## Measured against the live USGS API, 2026-08-13

Taken by the human against the real service. **These contradicted the original handoff**, and
several Phase 3 decisions are what they are because of them.

| Site | Name | `00060` discharge | `00065` stage | Native cadence | Seeded `record_start` |
|---|---|---|---|---|---|
| 07010000 | Mississippi River at St. Louis, MO | yes | yes | 30 min | 2007-10-01 |
| 07032000 | Mississippi River at Memphis, TN | yes | **no** | 60 min | 2007-10-01 |
| 07289000 | Mississippi River at Vicksburg, MS | yes | **no** | 60 min | **2008-01-01** |
| 07374000 | Mississippi River at Baton Rouge, LA | yes | yes | 15 min | 2007-10-01 |

1. **A request for an unavailable series returns HTTP 200 with `"timeSeries":[]`.** No error, no
   flag. When several sites are requested together the missing ones are simply absent from the
   array while the others return normally. This is the single fact Phase 3's client is built
   around, and it is now `CLAUDE.md § 14`'s first bullet.
2. **Parameter availability is per site.** Stage is absent at Memphis and Vicksburg; USGS states
   their gage height is furnished by the USACE Memphis District.
3. **Cadence is per site**, and none of these is uniformly 15-minute. Gaps are ordinary — the
   first eight St. Louis readings on 2026-08-01 skip 02:30.
4. **Period of record is per site.** Vicksburg's IV record appears to begin 2008-01-01, not the
   2007-10-01 the handoff assumed for everything.

---

## Open questions

- ~~**Raw 15-minute gauge readings vs. hourly aggregates on ingest.**~~ **Closed, and the question
  was slightly wrong.** It is **native cadence per site** — 15, 30, and 60 minutes across the four
  seeded gauges (finding 3). Raw readings are stored as published; nothing aggregates or resamples.
  **The volume estimate that framed this question was also wrong by an order of magnitude**: it
  assumed ~15 sites × 2 params ≈ 20M rows, and the real shape is 4 sites × 1 param ≈ **1.3M rows**.
  Volume is not a factor in any decision here.
- ~~**Whether USGS instantaneous-values requests can span the full period of record in one call.**~~
  **Closed by decision rather than by measurement, which is the safer direction.** The backfill
  chunks by 90-day window regardless (`CLAUDE.md § 14`), so the answer no longer gates anything.
  The reason not to test-and-then-trust it: the failure mode when the service declines a huge span
  is not a clean error but a truncated or timed-out response, which looks like a short record.
- **Are the seeded `record_start` values right for the three sites that were not measured?** Only
  Vicksburg's was checked. Live verification step 5 compares each site's `min(ts)` against its
  seed; a large discrepancy means **the seed is what to fix**, in a new numbered migration. The
  backfill logs the first window that actually returned data specifically to make this visible.
- **What is the Cairo, IL site number?** Investigated for Phase 3 and **not confirmed**, so it is
  absent from the seed rather than guessed. Cairo sits at the Ohio confluence and is the most
  obvious gap in the corridor; adding it is a human decision (`CLAUDE.md § 1`).
- **`gauges.lat`/`lon` are seeded NULL and must be filled by a human** before anything draws a
  map. This commit's agent had no way to verify coordinates and did not type them from
  recollection. Obtain them from the USGS site service and apply as a **new** migration:
  `curl 'https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=07010000,07032000,07289000,07374000'`.
  `tests/ingest/test_gauge_seed.py::test_river_mile_and_coordinates_are_null_rather_than_estimated`
  goes red when they land and is **meant to be deleted in that commit**, not weakened.

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

**Phase 2 live verification — DONE, 2026-08-11.** The step list that lived here is retired; the
outcomes are recorded at the top of `§ Current state`. Three items from it are still open and are
listed there: the digest resolve/rewrite (steps 1–2), the full `preflight` gate run (step 3), and
the `docker compose restart` reconnect check (step 8). Everything else passed.

The harness itself stays, and is rerunnable at any time — after a reboot, after a dependency bump,
before trusting the stack again:

    python3 -m verify.preflight            # exits non-zero on any FAIL *or* SKIP
    python3 -m verify.restart_recovery     # ~6.5 minutes; see CLAUDE.md § 12 on why
    python3 -m verify.failure_survives

**When rerunning the orchestration suite on the instance, compare the count to the offline run.**
Offline it is `15 passed, 24 skipped` for `tests/orchestration` and `101 passed, 24 skipped` for
the whole repo; on the instance it must be `39 passed` and `125 passed`, with **zero skips**. If
the skip count has not gone to zero, the `integration` marker is skipping and nothing was verified.

**Host connectivity for anything run from the host venv, and it is a deliberate temporary
deviation — still in force.**
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

**Phase 3 is written; its live verification is outstanding and is the next thing to do.** Run it
in order — step 3 is a deliberate rehearsal and exists so that a bad assumption is discovered
after twenty minutes rather than after six hours.

1. `python3 -m app.orchestration.migrate` — expect **0004, 0005, 0006** applied, **six total**.
2. Confirm the seed: `select usgs_site_id, available_params, native_cadence_minutes, record_start
   from gauges order by 1;` — **four rows, none containing `00065`**.
3. **Backfill one site, one year first.**
   `python3 -m app.ingest.backfill --site 07374000 --start 2025-01-01 --end 2026-01-01`
   Inspect row count and elapsed time before committing to eighteen years. **Report both.** This
   is also the first time the pinned `format=json,1.1` and the explicit-UTC `startDT`/`endDT` form
   meet the live service; both fail loudly if wrong.
4. Full backfill, all four sites: `python3 -m app.ingest.backfill`. Long-running — use `tmux` or
   `nohup` so an SSM disconnect does not kill it. **Report total rows and wall time.** Expect on
   the order of 1.3M rows (see `§ Current state`); an answer far from that is worth understanding
   before moving on.
5. Per-site sanity:
   `select usgs_site_id, count(*), min(ts), max(ts) from gauge_readings group by 1 order by 1;`
   **Compare each `min(ts)` to that site's seeded `record_start`.** A large discrepancy means the
   **seed** is wrong, and the seed is what to fix — in a new numbered migration, never by editing
   0004. The backfill also logs each site's first window that returned data.
6. **THE COMPRESSION MEASUREMENT — the deliverable of this phase.**
   Record the uncompressed size, then compress the eligible chunks and record it again:
   ```
   python3 -m app.ingest.usgs_ingest --compression-stats     # before
   psql -c "SELECT compress_chunk(c) FROM show_chunks('gauge_readings',
            older_than => INTERVAL '30 days') c;"
   python3 -m app.ingest.usgs_ingest --compression-stats     # after
   ```
   **Put the real ratio in `CONTEXT.md` and the README.** No placeholder is written anywhere in
   this repo and no vendor figure is cited, so there is nothing to overwrite — only something to
   fill in. **If it disappoints, report it: the measurement wins (`CLAUDE.md § 0`).**
7. `python3 -m verify.preflight` — still green, and now expecting **six** migrations rather than
   three. Its migration-count gate reads the directory, so it needs no change.
8. Start the scheduler and confirm `usgs_ingest` fires: a `job_runs` row with `status='success'`
   and a **plausible** `rows_written`. Note that a steady-state poll writing **0** is correct and
   truthful — it means nothing new arrived and nothing was revised — so 0 is not the failure
   signal here. Step 9 is.
9. **Freshness check.** Confirm the heartbeat reports `gauge_readings` fresh, then confirm it
   *would* report stale. The cheapest honest way is a **temporary registry threshold**, not
   deleting data — `job_runs` and `gauge_readings` are not to be pruned by hand. A guard that has
   never been seen refusing is not a guard.

**Then Phase 4.** The freshness-registry requirement in `CLAUDE.md § 12` now binds for every
subsequent ingest client, and `CLAUDE.md § 14` is the contract each one is written against.

---

## Housekeeping — open, non-blocking

- **`docker-compose.yml`'s image digest is NOT the all-zero placeholder any more**, contrary to
  what the Phase 2 notes further down still say. It reads
  `timescale/timescaledb:2.26.2-pg16@sha256:332b99…bfd`. So `§ Up Next`'s "steps 1–2 outstanding"
  is stale as written — the digest was resolved and written at some point without being recorded
  here, which is the exact process failure the note at the top of `§ Up Next` exists about. **Not
  corrected further because it was not this commit's to verify**: whether that digest is the one
  the *instance* resolved is still unconfirmed, and `verify/preflight.py` gate 1 is what confirms
  it. Phase 3's local test container ran on that digest successfully, which says the digest is
  real and pullable — not that it was resolved on the right machine.
- **`rows_written = 0` from `usgs_ingest` is the normal steady state, not a symptom.** The upsert
  counts only rows that actually changed, so an hourly poll that finds nothing new and nothing
  revised truthfully reports 0. **Do not add an alert on `rows_written = 0`** — the freshness
  registry is what detects a source that has gone quiet, and it does so from `MAX(ts)` rather than
  from the job's own report about itself.
- **The heartbeat will alert about `gauge_readings` being EMPTY from the moment 0005 applies until
  the backfill runs.** Deliberate: a registered table with no rows is stale, not quiet, for the
  same reason a job that has never succeeded is overdue. Expected once, and it goes quiet after
  the first backfill window lands.
- **The compression ratio is unmeasured and no number is written anywhere.** Live verification
  step 6. Nothing in the repo, the README, or the résumé may quote a ratio until it is taken here.
- **A revision to a reading older than 30 days lands in a compressed chunk** and is markedly
  slower than the same write against a recent one. It works on the pinned version and it is a rare
  path (USGS approving old data), not the ingest's normal one. If it ever becomes routine, widen
  0006's interval — do not stop upserting.
- **`app/orchestration/scheduler.py` was modified outside this commit's stated file list**, and
  had to be: `build_scheduler()` refuses to start when the cadence table and `JOB_FUNCTIONS`
  disagree, so adding the `usgs_ingest` cadence entry without registering its function would have
  broken every existing scheduler test. `tests/orchestration/test_migration_runner.py` was also
  modified — it hardcoded "3 migrations" and now derives the count from the directory, so it stops
  going red on every commit that adds one.
- **`tests/ingest/conftest.py` duplicates the schema-reset SQL from
  `tests/orchestration/conftest.py`** rather than importing it. Deliberate: the two `conftest`
  modules already collided once when both suites ran in one `pytest` invocation (see the
  provisioning-1 notes below), and a shared helper would be a third import path into the same
  collision. If a third suite needs it, promote it to a real module under `tests/` with a
  non-colliding name rather than importing across suites.

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