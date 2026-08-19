# Phase log

The per-phase verification blocks, **chronological** — Phase 1 first, Phase 10 last. Split out of
`CONTEXT.md` on 2026-08-17, when that file reached ~3,500 lines and stopped being read.

Every block below is the text as it was written at the time, moved verbatim. Where a later phase
contradicted an earlier one, the contradiction is recorded in place as an indented note rather than
edited into agreement — a block records what was believed when it was written, the same rule
`CLAUDE.md § 3` applies to migrations.

**What is NOT here:** the measured findings (`findings.md`), the decisions and their rejected
alternatives (`decisions.md`), the verbatim query output (`query-outputs.md`), and everything
current (`../CONTEXT.md`).

**Read order for someone new:** `../CONTEXT.md` → `findings.md` → this file.

**Two corrections are recorded in place below rather than by deletion**, both from 2026-08-18 —
the `16.15-1.pgdg13+2` client version that was filed as "NOT the value to commit", and the
freshness registry's claim that the newest `features` date comes from the daily-values job. **The
reasoning that produced each was sound and the conclusion was wrong**, and both facts matter to a
future reader, so the original stands with a dated correction beneath it.

## Phase 11 — backups, restore verification, monitoring, rate limiting

**VERIFIED ON THE INSTANCE, 2026-08-18.** Fifteen commits (`f29d734`… plus Stage B's seven from
`6607ba7`), code-complete and unapplied since 2026-08-17, applied and exercised over Stages D–J.

### What was applied

**Terraform state migrated** to `domestic-waterway-signals-tfstate` with **native S3 locking**
(`use_lockfile = true`, Terraform 1.15.8) — no DynamoDB table. **Thirteen resources applied:**

- backup bucket `domestic-waterway-signals-backups-065158220014`
- scoped IAM with **no delete action of any kind**
- Route53 health check `38b360cc-531f-46df-a80b-e7df2c265db6`, string-matching `"degraded":false`
- a CloudWatch alarm on it, with `insufficient_data_actions` on the same topic
- SNS topic `arn:aws:sns:us-east-1:065158220014:domestic-waterway-signals-alerts`, subscription
  **confirmed** (not `PendingConfirmation`)
- a $25 monthly budget

### THE FINDING THAT REFRAMED THE PHASE — the scheduler had never run in production

Before Stage E the instance was at `895eaa0` (Phase 10). **`job_runs` held three rows, all written
by `verify/` harnesses on 2026-08-11.** `apscheduler_jobs` existed — created by
`SQLAlchemyJobStore`'s own DDL during a Phase 2 run — and held **zero rows**. There was **no
`dws-scheduler.service`**; the only units were `dws-external-interface`, `dws-docker-firewall` and
`dws-stack`. **Every ingest row in the database had arrived by manual backfill.** A kernel upgrade
to `7.0.0-1010-aws` rebooted the instance on 2026-08-17 and nothing brought a scheduler back,
because none existed.

So `/api/health` had been reporting `degraded: true` **correctly** since Phase 8, and nobody was
watching until Phase 11's health check existed. **The monitor found it within minutes of being
created.** That is the phase's headline, and it is in `README.md § Monitoring` rather than only
here.

### Three placeholder incidents in one Terraform session, and a gap in the verifier

All three caught before apply; full detail in `findings.md § I`. Summarised: `yes` consumed as
`alert_email`; the tfvars *example*'s placeholders (`ami-0XXXXXXXXXXXXXXXX`, TEST-NET-3
`203.0.113.0/24`) taken into effect and planning to replace the production instance and revoke real
SSH; and `yes` consumed into `availability_zone`, forcing replacement of the data volume.

**`d-pre` caught the second. `prevent_destroy` caught the third. Nothing caught the first.** A plan
that *errors* writes no plan file, and `d-pre` reads a saved plan — **a verifier that inspects an
artifact cannot see a failure that prevents the artifact existing.** That is an argument for
`prevent_destroy` on more than the data volume, recorded as a known gap.

**`d-pre` itself needed correcting three times** — a check that could never pass once the resources
existed, a hand-typed second address list replaced by a union (17 → 30), and an IAM policy check
structurally unrunnable on first apply because the document is `(known after apply)`.

### Verified live

`/api/health` returns **`"degraded":false` for the first time since the system existed.** Route53
reports **Success from all 15 checker regions**. The CloudWatch alarm transitioned **`ALARM → OK`
at 2026-08-18T19:54:44-04:00**, its first transition since creation. `d-post` **3 of 3**, `j`
**5 of 5**. Stage J observed **30 consecutive requests to `/` all returning 200** — the accepted
static-asset residual exposure, measured rather than assumed.

---

## Phase 12 — the scheduler as the fifth Compose service

**VERIFIED ON THE INSTANCE, 2026-08-18.** The scheduler exists as a running thing for the first
time in the project's life.

### What was built

**A fifth Compose service**, inheriting `RequiresMountsFor=/mnt/data` and the restart policy from
`dws-stack.service` rather than getting its own unit — a second unit would restate the mount
guarantee, and the second copy is the one that drifts.

**No container gets the Docker socket.** It is root-equivalent on the host. `pg_dump` and
`pg_restore` are installed into the scheduler image instead, pinned to
`postgresql-client-16=16.15-1.pgdg13+2`, with preflight asserting client and server majors agree by
deriving both from the files, and the job asserting it again at runtime against
`SHOW server_version_num`.

### The client-version placeholder encoded a wrong assumption, and failing to resolve is what surfaced it

The committed literal ended **`pgdg120+0` — bookworm**. But `python:3.12-slim@sha256:2c941e86…`
reports **Debian 13 (trixie)**. **A plausible bookworm version would have failed as
package-not-found and sent the reader to the PGDG repository rather than to the base image.** The
unresolvable placeholder is what pointed at the right layer.

> **Correction, 2026-08-18.** `CONTEXT.md` recorded the scratchpad-resolved `16.15-1.pgdg13+2` as
> **"NOT the value to commit"**, on the grounds that `§ 5` requires resolution on the machine that
> runs it. **The rule was right and the conclusion was wrong.** Resolving on the instance produced
> the *identical* string, because both machines derive the codename from the same pinned base
> digest and therefore see the same PGDG suite. `§ 5` still holds — following it is what produced a
> confirmed value rather than an assumed one — but a scratchpad resolution against the same pinned
> digest is not, in fact, a different answer. The original reasoning is left in place above because
> it was sound; only its conclusion was wrong.

**Also found:** the repo carried **all-zero digest placeholders** for python, node and caddy while
the instance held the real resolved values, **never committed back**. The instance's copy was
authoritative.

### First production job runs — all successful

| Job | `rows_written` |
|---|---|
| `usgs_ingest` | 22,147 |
| `usgs_daily_ingest` | 16 |
| `usda_rates_ingest` | 0 |
| `usda_movements_ingest` | 0 |
| `features_build` | 1,046 |
| `heartbeat` | NULL |

**The `rows_written` contract produced all three of its distinct meanings on real data for the
first time** (§ 4): a count; `0` meaning "ran and wrote nothing new"; and `NULL` meaning "this job
writes no rows to this database". A schema that collapsed `NULL` into `0` would have made the
heartbeat's row indistinguishable from the two USDA jobs' genuine no-op.

**The Socrata short-page guard fired on the first live call:**

```
page 1 returned 63 of 1000 requested rows - SHORT, not necessarily last; paging continues until a page is empty
```

**A pager terminating on a short page would have truncated silently here, on the first run**
(§ 16). This is the contract being exercised by production rather than by a fixture.

### Backups

| | Bytes | Tables | Compressed chunks |
|---|---|---|---|
| Backup 1 | 8,535,888 | 18 | 1,016 |
| Backup 2 (post-0027) | 9,210,544 | 19 | 1,035 |

So `gauge_readings_iv_archived_20260818` costs **674,656 bytes per nightly dump, about 8%** — **far
less than the doubling anticipated**, because 986 tiny compressed chunks dump much smaller than
their on-disk footprint suggests.

---

## Phase 13 — cluster settings under version control, and the chunk interval

**VERIFIED ON THE INSTANCE, 2026-08-18.** Five commits: `3dee5c7`, `78eb514`, `2f50cee`, `59150f0`,
`cebe5ee`.

### `3dee5c7` — the cluster's 33 settings were untracked

`infra/postgres/settings.py`, a preflight gate, `docs/runbooks/cluster-settings.md`, 15 tests.

- `REQUIRED_SETTINGS` enforced (`max_locks_per_transaction >= 512`), `TUNER_BASELINE` recorded and
  never enforced. Merging them makes the gate fail on a correctly re-derived cluster of a
  different size.
- The gate reads the running value **and** `pending_restart`, with distinct messages.
- **The prompt's mutation-1 fixture did not work and was corrected.** On a normal *raise* the
  running value is still the old, failing one, so a `setting`-only gate fails anyway. The
  discriminating fixture is a setting being **lowered**: running value 512, `pending_restart` true.
  Under mutation 1 that returns `PASS`.
- **The baseline was not populated and was not invented.** The 33 values are recorded nowhere in
  the repo and no agent connects to the instance (§ 9). `--write-baseline` captures them, as
  `--write-digest` does for digests.

Six mutations confirmed, each restored from a pristine copy, under `PYTHONDONTWRITEBYTECODE=1`.

### `78eb514` — 986 chunks become tens

`migrations/0027_gauge_readings_iv_chunk_interval.sql`, `tests/db/`, 18 tests (9 unit, 9
integration against a real TimescaleDB).

- **`gauge_series` binds by OID and would have followed the rename onto the archive.** Not in the
  prompt; found by reading 0010 before writing the migration. Silent — the archive holds identical
  data at commit time, so the view returns identical rows and diverges only on the next ingest.
- **An advisory lock was rejected, not deferred:** `usgs_ingest` takes none, so
  `pg_try_advisory_lock` would succeed against a running ingest and report the coast clear.
- Seven mutations confirmed. The most valuable was not on the prompt's list: replacing the whole
  rewrite with a bare `set_chunk_time_interval` — the one-statement half-fix the migration's header
  warns about — leaves **312 chunks of 312** and turns six integration tests red.
- `verify/phase11/stage_f.py::EXPECTED_MIGRATIONS` 26 → 27. **The pin going red is the pin
  working.** Its companion test now reads the constant instead of repeating the literal four times.

### `2f50cee` — the tuning candidate was filed at the wrong severity

The chunk interval had been logged as a **ratio** problem — 986 chunks dragging compression down.
It was a **correctness** problem: a bare `SELECT min(ts), max(ts), count(*)` on the table failed
with `out of shared memory`. **`max_locks_per_transaction × (max_connections +
max_prepared_transactions)` = 128 × 25 = 3,200 slots**, cluster-wide despite the name, against a
full-table query needing roughly 2,000 with index locks. Raised to 512 → **12,800 slots**, at a
cost of roughly **3 MB** of shared memory.

**Nothing in the original note was wrong; what it got wrong was the severity, and in a predictable
direction.** The measurable consequence (a worse ratio) sat in a table already being measured. The
unmeasurable one (lock exhaustion at some unknown concurrency) was in no table at all. **A finding
logged against the number you happen to be looking at gets filed at the severity of that number.**

### `59150f0` — the restore test's first run failed on one argument

`restore_test.py` wrote its pgpass entry with `database=production_database` while `pg_restore`
connects to the throwaway. **libpq matches all five fields and does not error on a non-matching
entry — it falls through to prompting**, so it surfaced as
`FATAL: password authentication failed`, which reads like a wrong credential. **The mechanism was
entirely present**: file written, mode 0600, `PGPASSFILE` correctly exported.

Fixed with `database="*"` (the throwaway does not exist when the pgpass is written), plus the
durable half — **`-w` / `--no-password` on both clients**, which neither had carried. That converts
the whole class from "looks like a wrong password" into "says no password was supplied".

**Why the integration tier could not see it:** the harness authenticates with
`POSTGRES_HOST_AUTH_METHOD=trust` and a `pg_hba.conf` reading `host all all all trust`. **On trust,
libpq never consults the pgpass file at all**, so every assertion about it was vacuous — the
helper's placeholder password literally spells `trust-auth-ignores-this`. A test already drove the
real job end to end and **executed the defective line on every run, staying green with the defect
reapplied.** Written up as `CLAUDE.md § 25`.

**The message is TTY-dependent, and the instance's version was not the non-TTY one.** Off a TTY the
failure is client-side `fe_sendauth: no password supplied`; `docker compose exec` allocates a TTY,
which fed the prompt. Same root cause, different message depending on what answers — and `-w`
removes the branch entirely.

**First successful restore test:** 68 seconds, 19 tables compared, 1,035 compressed chunks on both
sides, throwaway dropped after terminating one backend, `restore_verified_at` set on backup 2 only.

### `cebe5ee` — three of five freshness thresholds were tripping on correct data

Measured with **zero jobs overdue and every source publishing on schedule**. Full table and
derivation in `findings.md § J`. The registry moved to
`max_staleness >= cycle + observed_lag + one missed publication`, with the arithmetic committed as
parsed `# DERIVATION:` lines rather than as a bare number.

> **Correction, 2026-08-18.** The registry's comment said the newest `features` date "comes from
> the daily-values job". **It does not.** `features` is built from `gauge_daily`, which
> `app/features/rollup.py` reads from the **`gauge_series` view**, and 0010's precedence rule takes
> the **instantaneous** row wherever one exists. So the newest feature date tracks
> `gauge_readings_iv` — which is why `features` carried a row dated 2026-08-18 while
> `gauge_readings_daily` ended at 08-16, an observation that looks impossible under the old comment
> and is ordinary once you follow the view. The lag is nonetheless set to **2 days, not 0**,
> because IV retention is a rolling window at three of four gauges, so falling back to the DV side
> is normal operation and deriving from the fastest input would reintroduce the boundary-sitting
> defect.

**An existing test had encoded the defect as a guard.**
`tests/ingest/test_usda_movements.py::test_both_usda_tables_are_in_the_freshness_registry` asserted
`9 days < max_staleness < 14 days` — a band that **never counted the lag, permitted the broken 10
days, and would have gone red on the correct value.** It is in the integration tier, so
`-m "not integration"` reported **642 green over a changed contract**; only the full run caught it.

**An ordering inverted, and it is emergent rather than a rule.** `cadence.py` chose
`usda_rates_ingest` `overdue_after=14 days` explicitly so data-stale (then 10 days) would speak
first. It is now job-overdue at 14 and data-stale at 17. **All five entries now have job-overdue
firing first**, which before this change was true only of `gauge_readings_iv`. **That is a
consequence of deriving each window from its source's publication behaviour, not a target that was
aimed at, and it must not be enforced as a rule** — the two thresholds answer different questions
and stay independently derived.

### The advisory-lock question, answered rather than deferred

**An advisory lock only detects a party that also takes it, and `usgs_ingest` takes none** — so
`pg_try_advisory_lock` would have reported the coast clear against a running ingest. Rejected.

What is used instead is a `job_runs` check plus `LOCK TABLE … ACCESS EXCLUSIVE` under a 30s
`lock_timeout`. **The `job_runs` check is a snapshot, not a lock**: it closes the window where an
ingest is *already* running and **cannot** close the window where one starts between the check and
the lock. **Stopping the scheduler remains required, and the refusal must not be read as
sufficient.**


## Contents

| Phase | Built | Verified on the instance |
|---|---|---|
| 1 — Terraform and provisioning | 2026-08-10 | 2026-08-10 |
| 2 — orchestration, scheduler, migrations | 2026-08-11 | 2026-08-11 |
| 3 — USGS instantaneous ingest | 2026-08-13 | 2026-08-14 |
| 3.5 — USGS daily values | 2026-08-14 | 2026-08-14 |
| 4 — USDA rates and lock movements | 2026-08-14 | 2026-08-14 |
| 5 — normalizer and feature layer | 2026-08-14 | 2026-08-15 |
| 6 — the ±lag sweep | 2026-08-15 | 2026-08-15 |
| 7 — analog engine and confidence gate | 2026-08-15 | 2026-08-16 |
| 8 — the FastAPI read layer | 2026-08-16 | 2026-08-16 |
| 9 — the React frontend | 2026-08-16 | 2026-08-16 (browser) |
| 10 — domain, TLS, hardening | 2026-08-16 | **2026-08-17 (public internet)** |
| 11 — backups, restore verification, monitoring | 2026-08-17 | **2026-08-18** |
| 12 — the scheduler as the fifth Compose service | 2026-08-17 | **2026-08-18** |
| 13 — cluster settings and the chunk interval | 2026-08-18 | **2026-08-18** |

**Phases 11, 12 and 13 are written ABOVE this table**, following the convention the Phase 13 block
established: the newest blocks sit at the top of the file where they are read, and Phases 1–10
remain below in chronological order.


---

## Phases 1 and 2 — infrastructure, provisioning, orchestration

*Written 2026-08-10 and 2026-08-11. This block reads NEWEST-FIRST internally — Phase 2, then Phase 1, then provisioning 3, 2, 1, then Terraform — because that is the order it accumulated in and reordering it would mean rewriting rather than moving it.*

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


---

## Phase 3 — USGS instantaneous ingest

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

---

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


---

## Phase 3.5 — USGS daily values as the historical backbone

**PHASE 3.5 (DAILY VALUES AS THE HISTORICAL BACKBONE) IS WRITTEN AND VERIFIED OFFLINE, as of
2026-08-14. It has not run against the instance, and the compression ratios are STILL UNMEASURED.**

- **183 tests green with ZERO SKIPS** against a throwaway local TimescaleDB container on the
  pinned image (`timescale/timescaledb:2.26.2-pg16`, same digest as `docker-compose.yml`).
  Offline the same suite is `130 passed, 53 skipped`. Phase 3's baseline was 156/zero-skip.
- **All 13 mutation-table rows confirmed**: each performed, observed to turn its named test red
  *on that test's own assertion*, then restored, with the suite re-verified afterwards. Three
  needed a second pass and the reasons are recorded below under "mutation notes" — a mutation
  that goes red for the wrong reason is not a confirmed guard.
- **The rename was verified against the catalog, not assumed.** `ALTER TABLE gauge_readings
  RENAME TO gauge_readings_iv` carried the hypertable registration (7-day chunks on `ts`), the
  compression settings (`segmentby usgs_site_id, param_code`; `orderby ts DESC`) and the 30-day
  compression policy through intact. This was the single largest silent-failure risk in the
  commit: a dropped compression policy is invisible until the storage bill.

---

### What Phase 3.5 built

- `0007` renames `gauge_readings` → `gauge_readings_iv` (plus its indexes, constraints, and
  `gauges.record_start` → `iv_record_start`). Pure renames; no data moved.
- `0008` creates `gauge_readings_daily` — hypertable on `date`, 365-day chunks, primary key
  `(usgs_site_id, date, param_code, stat_cd)` — and adds `gauges.dv_record_start`.
  `0009` compresses it (segment by site/param/stat, after **1 year**, against the instantaneous
  table's 30 days).
- `0010` creates `gauge_series`, the one place the precedence rule lives.
- `app/ingest/usgs_daily_client.py`, `usgs_daily_ingest.py`, `daily_backfill.py`.


---

## Phase 3 close-out — measured coverage, corrected seeds, known gaps

### Phase 4 — USDA ingest (written offline, 2026-08-14)

**THIS SECTION IS SUPERSEDED BY THE PHASE 4 CORRECTION AT THE TOP OF THIS FILE.** The identifiers
are resolved, every field name below was wrong, and the two table schemas changed. It is kept as
the record of what Phase 4 believed and of what the honest disclosure bought — the provisional-field
note at the end of this section is the reason the correction cost a field map rather than a
debugging session.

**THE DATASET IDENTIFIERS ARE UNRESOLVED AND THE INGEST CANNOT RUN UNTIL A HUMAN RESOLVES THEM.**
That is the state migration `0013` seeds deliberately, not an incomplete commit. *(Superseded:
`0016` resolves all five.)*

- `0013` `usda_datasets` — three keys (`barge_rates`, `lock_movements`, `cost_indicators`), every
  `dataset_id` **NULL**, every period bound NULL. A Socrata id is a four-four token and this
  project does not guess identifiers (`CLAUDE.md § 1`); an invented one 404s and reads like a
  network fault. Every client path raises `DatasetNotResolvedError` naming the key **before any
  request is issued**, and a test asserts the request log is empty when it does.
- `0014` `barge_rates` — key `(segment, week_ending, horizon)`; `pct_of_tariff` stored **exactly as
  published**. `0015` `lock_movements` — key `(lock_id, week_ending, grain_type, direction)`;
  `barges`/`tons` nullable, because **0 is a reported value and NULL is an unreported week**.
- **Neither is a hypertable**, and that is decided by arithmetic rather than by consistency: these
  are weekly series of thousands of rows, against the 290k where Phase 3's own measurement
  concluded Postgres alone would have sufficed. A test reads the TimescaleDB catalog and fails if
  either is converted.
- `socrata_client.py` pages until an **empty** page — never a short one — and **raises** at its
  page cap rather than returning a prefix. Every query carries an explicit `$order`.
- Two cadence entries (`usda_rates_ingest`, `usda_movements_ingest`), weekly, **separate jobs**:
  one job over two datasets produces one `job_runs` row whose status is the AND of two independent
  sources, and the heartbeat could not then say which one went quiet. Both tables are in the
  freshness registry at **10 days** — weekly publication plus a late holiday week must not alert,
  two consecutive missed publications must.
- **`cost_indicators` is seeded and deliberately not ingested.** No table, no cadence entry, and
  `usda_backfill --dataset` refuses it by name rather than failing somewhere deeper.

**The USDA Socrata field names in `usda_rates.FIELDS` and `usda_movements.FIELDS` are PROVISIONAL.**
They come from the shape the fixtures were written to, not from the live catalog, and confirming
them is part of live verification step 3. Every read goes through `required_field`, which raises
naming the fields a record actually carries — so a wrong name fails loudly on the first record and
never writes NULLs.


**PHASE 3 CLOSE-OUT (MEASURED COVERAGE, CORRECTED SEEDS, KNOWN GAPS) — written 2026-08-14 and
since VERIFIED ON THE INSTANCE; see the top of this section for the measured outcome. The
"compression still unmeasured" line below is superseded: both ratios are recorded above.**

- **194 tests green with zero skips** against a throwaway local TimescaleDB container on the
  pinned image; offline the same suite is `137 passed, 57 skipped`. Phase 3.5's baseline was
  183/zero-skip.
- **All 8 mutation-table rows confirmed** — each performed, watched red on its own assertion, then
  restored, with the files diffed byte-for-byte against their pre-mutation copies afterwards.
- **Migrations `0011` and `0012` are new; nothing in `0001`–`0010` was edited.**

---

### Current state

**PHASE 3 IS COMPLETE AND VERIFIED ON THE INSTANCE, 2026-08-14. The compression measurement —
outstanding since Phase 3 was written — is taken, and both ratios are below.**


---

## Phase 4 — USDA ingest, and its three corrections

### Phase 4 correction — the real USDA identifiers and field maps, measured 2026-08-14

**THE DATASET IDENTIFIERS ARE RESOLVED. EVERY FIELD NAME PHASE 4 ASSUMED WAS WRONG.** Migration
`0016` lands the measured ids, bounds, and row counts, and corrects the two table schemas to the
shape USDA actually publishes. Nothing below has run against the live API yet — the identifiers and
counts are the human's measurement; the ingest against them is live verification's job.

---

### Status

- **231 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `162 passed, 69 skipped`. The pre-correction baseline was 218.
- **All 11 mutation-table rows confirmed**, each watched red on its own assertion, restored, and
  `__pycache__` cleared between restore and re-run. **Row 11 needed a second pass** — see below.
- **`0016` is new; nothing in `0001`–`0015` was edited.**

---

### Phase 4 correction 2 — nullable rates and the corrected segment, measured 2026-08-14

**THE FIRST BACKFILL ATTEMPT FAILED ON ITS OWN TRIPWIRE, AND THAT IS THE SYSTEM WORKING.** `0016`
seeded seven `location` values, five of them from the handoff rather than from a measurement, and
committed in writing that the API would win if they disagreed. It disagreed about one, the run
stopped rather than opening a silent eighth series, and the attempt produced two findings.

---

### Status

- **239 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `167 passed, 72 skipped`. The previous commit's baseline was 231.
- **All 8 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run. No row needed a second pass.
- **`0017` is new; nothing in `0001`–`0016` was edited.**

---

### Phase 4 close-out — `tons` nullability measured, and the analogy was wrong, 2026-08-14

**The previous commit left `tons` alone on the stated grounds that its nullability was an analogy to
`rate` rather than a measurement. It has now been measured, and the analogy would have been wrong.**
The *shape* of the handling is the same; the *meaning* is not, and a comment copied from the rates
module would have asserted something false.

---

### Status

- **244 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `170 passed, 74 skipped`. The previous baseline was 239.
- **All 7 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run.
- **`0018` is new; nothing in `0001`–`0017` was edited.** Eighteen migrations apply clean.
- **VERIFIED ON THE INSTANCE 2026-08-14.** All ten steps ran, including step 9. The outcome —
  including both thesis tables and the observation they produced — is recorded immediately below.

---

### PHASE 4 — VERIFIED ON THE INSTANCE, 2026-08-14. COMPLETE.

**Landed:** three rate horizons at **8,260 rows each — 24,780 total** — plus **26,144 movement
rows**. Every dataset matched its seeded `source_row_count` **exactly**; nothing truncated.

**Paging behaved as designed on every dataset:** a short page mid-sequence, then an empty page
terminating the loop. That is `CLAUDE.md § 16`'s first bullet working live — the `while len(page)
== limit` loop this project refused to write would have stopped at the short page and reported a
truncated dataset as a complete one, on real data, with a plausible row count.


---

## Phase 5 — the normalizer and feature layer

### Phase 5 — the normalizer and feature layer, written offline 2026-08-14

Three migrations (`0019` `gauge_daily`, `0020` `features`, `0021` `targets`), six modules under
`app/features/`, one cadence entry, one freshness entry. **The first derived data in this project**,
which is what the new `CLAUDE.md § 17` exists to govern: everything under `app/ingest/` writes what a
source published, and everything here writes something this project computed — a number with nothing
upstream to contradict it when it is wrong.

---

### Status

- **283 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `201 passed, 82 skipped`. The previous baseline was 244.
- **All 14 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run. **No row needed a second pass.**
- **`0019`–`0021` are new; nothing in `0001`–`0018` was edited.** Twenty-one migrations apply clean.
- **VERIFIED ON THE INSTANCE 2026-08-15.** All ten steps ran. **Step 9 contradicted what this file
  recorded the day before** — the four findings are immediately above.

---

### PHASE 5 — VERIFIED ON THE INSTANCE, 2026-08-15. COMPLETE.

**The build ran, and it changed what this project believes about its own thesis.** The raw-discharge
relationship recorded on 2026-08-14 was substantially calendar; the relationship that survives
deseasonalization is a *duration* one, and it reverses on recovery.

---

### What was built

| | Rows |
|---|---|
| `gauge_daily` | **32,462** |
| `features` | **162,310** — exactly 5 registered features × 32,462 daily rows |
| `targets` | **3,540** — 1,180 Cairo-Memphis weeks × 3 horizons |

**70 seconds from scratch** (`--from-scratch --start 1990-01-01`), and **the idempotent rerun wrote
0 rows** — decision 8's claim measured rather than asserted. `IS DISTINCT FROM` is what makes that a
real number; a plain `DO UPDATE` would have reported all 198,312 rows as written and looked fine.

---

### Deviations and standing debts

- **The two Phase 4 thesis tables are STILL owed their verbatim output**, and now so are the two
  2022 deseasonalized tables. The figures above are the anchor points this session was given; the
  row-by-row output was not, and it is not invented here (`CLAUDE.md § 4`). Every number above is
  checkable by re-running the queries in `§ Up Next`.

  > **PHASE 6 MADE THIS ONE COMMAND — 2026-08-15.** `scripts/capture_thesis_queries.py --out <dir>`
  > runs all four and writes CSV. It deliberately does **not** write to this file: the paste is the
  > step where somebody reads the numbers, and a document that edits itself is a document nobody
  > reviews. **Still open until a human runs it and pastes the four blocks in** — see step 2 of the
  > Phase 6 live procedure in `§ Up Next`.
  >
  > **2026-08-15 — THE SCRIPT HAS RUN. THE FOUR CSVs EXIST AND ARE NOT PASTED IN.** `2022_raw_
  > discharge.csv`, `2023_raw_discharge.csv`, `2022_deseasonalized.csv`, `2023_deseasonalized.csv`.
  > **This debt stays open until the four blocks are in this file**, next to the anchor points they
  > check — capturing them and not pasting them is the same failure in a shorter form.


---

## Phase 6 — the ±lag sweep

### PHASE 6 — THE ±LAG SWEEP. WRITTEN OFFLINE 2026-08-15. **THE BUILD RECORD; THE OUTCOME IS ABOVE.**

Two migrations (`0022` `signal_runs`, `0023` `signals`), five modules under `app/signals/`, one
script under `scripts/`, and six test files. **No cadence entry and no freshness registration** —
see decision 10 below.

**THE HEADLINE IS THAT THERE IS NO HEADLINE.** This commit builds the measuring apparatus; it has
measured nothing about the river. The live procedure at the end of `§ Up Next` is what produces the
first result, and **step 8 of it is the instruction that matters**: if the passing count comes out
near 5% of the grid, the sweep is finding noise at exactly the rate chance predicts, and *that is
the finding* — to be recorded as such rather than mined for its strongest row.

> **STEP 8 LANDED, 2026-08-15.** 271 of 6,966 on the unadjusted p-value — just under the ~348 that
> chance alone predicts — and **1** after Benjamini-Hochberg. The sentence above was written before
> the run and did not need changing after it. See `PHASE 6 — VERIFIED`, above.

---

### The three debts closed here, and the one that needs the instance

1. **`scripts/capture_thesis_queries.py` exists — debt 1a is one command from closed.** It runs the
   four owed queries (2022/2023 raw discharge, 2022/2023 deseasonalized, all Cairo-Memphis nearby
   against Memphis) and writes CSV to a stated `--out`. **It never writes to `CONTEXT.md`**, and
   that is the design: a document that edits itself is a document nobody reviews, and the paste is
   the step where somebody actually reads the numbers. **STILL OPEN until a human runs it and pastes
   the four blocks in.**

   > **2026-08-15 — the script has run and the four CSVs exist. The paste has not happened, so the
   > debt has not closed**, and it is recorded as open rather than as "captured" — a file in `/tmp`
   > that nobody read is the same amount of review as no file at all.
2. **Debt 1b is closed.** `discharge_min` is skipped where `bool_and(n_observations = 1)` holds for a
   site, and the skip is reported with the measured reason. **Detected from the data, never from a
   site list** — a hardcoded list would be wrong the day the instantaneous backfill fills Baton
   Rouge in, and wrong silently. `app/signals/pairs.py` contains no site id literal and a test
   asserts it.
3. **Debt 1c is closed.** `tests/features/test_seasonal.py::test_a_five_year_climatology_yields_null_anomaly_end_to_end`
   builds a deliberately shallow 5-year record at Memphis and asserts, against a real database, that
   every anomaly is NULL, that `climatology_n_years` is present on the refused rows, and that it is
   exactly 5. **The eight-year guard has now fired somewhere.** Finding 4 said it holds by luck of
   coverage rather than by demonstration; it is still true that it has never fired on *real* data,
   and that remains recorded rather than closed.
4. **Debt 1d — `lock_movements` remains unused, deliberately.** No feature in the registry reads it
   and **this commit does not add one.** The reason is the sparsity measured in Phase 4: **MS Lock 15
   reports 1,434 explicit zeros of 2,840 rows**, and `lock_movements` is a sparse *per-commodity
   weekly* series. Differencing a per-commodity series that is half zeros produces a sequence of
   spikes and reversions that **looks like volatility and is mostly the reporting grain.** Using it
   requires deciding whether to aggregate across commodities before differencing — **that is a
   modelling decision, not an oversight**, and it belongs to a human under `CLAUDE.md § 1`. The
   volume half of the target stays unused until it is made.

---

### Status

- **Offline: 316 passed, 0 failed** with a database; **224 passed, 92 skipped** without one.
  Baseline before this commit was 283 with a database.
- **All thirteen mutation rows confirmed** — each red on the guard's own assertion rather than on an
  import error, `__pycache__` cleared between restore and re-run. Rows 2 and 5 were each run in two
  forms; see "Mutation notes" below.
- **A full-grid run against a fixture database** (4 sites, 3,200 days of features, 450 weeks of
  targets) enumerated **6,966 pairs and wrote 6,966 rows in 3.7 seconds** — 7,740 minus the 774
  skipped as duplicates at the two degenerate sites. **The passing count from that run is a property
  of synthetic sinusoidal fixture data and is not reported here as a result.**
- **`0022`–`0023` are new; nothing in `0001`–`0021` was edited.** Twenty-three migrations apply clean.

---

### PHASE 6 — VERIFIED ON THE INSTANCE, 2026-08-15. COMPLETE.

**1 of 6,966 scanned pairs passes the gate.** The denominator is stated in the same sentence as the
survivor because a passing count without one is the dishonest form of this result
(`CLAUDE.md § 18`), and because one row out of six thousand nine hundred and sixty-six is a
different claim from one row out of one.

---

### The harness, after the sweep

- `python -m app.orchestration.migrate` — **0022 and 0023 applied. Twenty-three total.**
- `python -m verify.preflight` — **clean. Six gates green.** Its migration-count gate reads the
  directory, so twenty-three needed no change to it.

---

### STILL OWED FROM THIS RUN

- **The `run_id` and the wall time are not recorded here.** Step 3 asked for grid size, rows
  written, wall time and the `run_id`; this write-up had the grid size and the outcome. Every query
  in this section is written against `run_id = <id>` and this file does not yet say what that id is.
  Both are one query away — `select run_id, started_at, finished_at from signal_runs order by
  started_at desc limit 1;` — and they belong in this section rather than in a later one.
- **DEBT 1a — the four thesis CSVs have been captured and are still not pasted in.** The script ran;
  the files exist; the paste is the review step and it has not happened. **The debt is not closed.**


---

## Phase 7 — the analog engine and the confidence gate

### Phase 7 — the analog engine and the confidence gate. **THE BUILD RECORD; THE OUTCOME IS ABOVE.** Written offline 2026-08-15.

Two migrations (`0024` `analog_queries`, `0025` `analog_matches`), seven modules under
`app/analogs/`, seven test files. **No cadence entry and no freshness registration** — the engine
answers when asked, for the reason the sweep has none.

**THE HEADLINE IS THAT THIS WAS BUILT TO REFUSE.** Phase 6 scanned 6,966 pairs and one passed, at
**lag 0**, with **zero** passing rows at any non-zero lag in either direction. There is no measured
predictive relationship in this dataset, so the engine is expected to return **"insufficient
history"** for most or all queries. **That is the correct output and it is the deliverable.** The
sentence that governs every decision below, and now `CLAUDE.md § 19`'s last bullet:

> **An analog engine that finds confident analogs where the lead-lag sweep found no relationship has
> a bug, not a discovery.**

---

### What is measured so far, and what is not

**Offline: 374 passed, 0 skipped with `DATABASE_URL` set; 268 passed, 106 skipped without it.** All
thirteen mutations in the brief's table were **watched red and restored**, with `__pycache__` cleared
between the restore and the re-run, and none went red for the wrong reason.

**NOTHING ABOUT THE RIVER HAS BEEN MEASURED BY THIS COMMIT.** The integration tier runs against a
**synthetic** eight-year fixture with five seeded low-water events, built so that all three gate
branches are reachable — a passing query, a refusal on too few analogs, and a clean refusal on a
quiet day. That fixture exists to prove the mechanism works in both directions; **it says nothing
about what the real data will do**, and a fixture where the gate could never pass would make "it
refused" indistinguishable from "it is broken".

> **ALL FOUR SLOTS WERE FILLED ON 2026-08-16 AND THE RESULTS ARE AT THE TOP OF THIS FILE.** The gate
> **passed** on both labelled events — 4 analogs at 3 of 4 in 2022, 5 at 4 of 5 in 2023 — on medians
> of **+7%** and **+10%** across ranges spanning zero, with every analog drawn from 2015–2022. The
> collapsed event count at Memphis is **5 and 6**, so the gate *can* pass at this site; the distances
> **cluster within ~4%**, so a cutoff would be all-or-nothing. **Nothing above was tuned afterwards.**

---

### PHASE 7 — RUN ON THE INSTANCE, 2026-08-16. **THE GATE PASSED ON BOTH LABELLED EVENTS, AND THAT IS NOT THE GOOD NEWS IT LOOKS LIKE.**

Both queries passed. **Read the next four sections before quoting either of them**, because what they
passed on is a median move of **+7%** and **+10%** across ranges that span zero, on analogs drawn from
a single twelve-year window, with a direction that **disagrees in sign with the one relationship the
sweep found.**

---

### The checks that confirm this ran against real data, and against the boundary

- **`features` for `days_below_p10` at Memphis: 4,334 rows**, matching the Phase 5 daily count for the
  site exactly. *(Independently consistent with the record start: 2014-10-01 to now is ≈4,338 days,
  which is the same twelve-year window the limitation section is built on.)*
- **`--as-of 2022-09-06` returned `no_current_event`** — the entry threshold had not crossed a week
  before the counter first ticked up. **The detector is date-sensitive across the boundary rather than
  firing indiscriminately**, which is the property decision 1 exists for and the one a fixture cannot
  demonstrate.
- **The climatology depth differs between the two queries — 12 years in 2022, 11 in 2023** — because
  `climatology_n_years` is read per date rather than hardcoded. `CLAUDE.md § 7`'s example says "the
  10-year seasonal median"; **neither of these is 10**, and a hardcoded ten would have been wrong in
  both sentences.

---

### Still unconfirmed from the live procedure

**Steps 1 and 7 were not reported back:** `python -m app.orchestration.migrate` showing **0024 and
0025 applied, twenty-five total**, and `python -m verify.preflight` showing **six gates green**. The
queries above could not have run without the two migrations, so step 1 is implied rather than
recorded — **implied is not recorded**, and preflight is not implied by anything.


---

## Phase 8 — the FastAPI read layer

### Phase 8 — the FastAPI read layer. **WRITTEN OFFLINE 2026-08-16. THE BUILD RECORD; THE OUTCOME IS ABOVE.**

Twelve modules under `app/api/`, six test files under `tests/api/`, two lines added to
`requirements.txt` and one to `requirements-dev.txt`. **No migration, no cadence entry, no
freshness registration, and nothing beneath `app/api/` was touched** — this commit adds a read
layer and changes nothing under it.

**THIS IS THE FIRST COMMIT WHERE THE PROJECT'S HONESTY GUARANTEES HAVE TO SURVIVE A JSON ENCODER.**
Seven phases of guards live in Python objects and database constraints. Every one of them can be
undone here by a line that looks like tidying — a nullable field with a default, a q-value emitted
without its grid size, a refusal serialized with `median_pct: null`. `CLAUDE.md § 20` is the
contract that came out of it.

---

### The endpoints

```
GET /api/health                              per-job + per-table, 200 while degraded, never cached
GET /api/conclusion?site_id=&as_of=          three shapes, discriminated on `gate`
GET /api/gauges                              declared record starts AND observed coverage
GET /api/gauges/{site_id}/series?start=&end=&source=
GET /api/rates?segment=&horizon=&start=&end=
GET /api/movements?lock=&commodity=&start=&end=
GET /api/signals?run_id=&passing_only=       defaults to ALL scanned rows
GET /api/signals/runs
```

**It runs from the host venv under uvicorn on loopback, pending Phase 10.**
`uvicorn app.api.main:app --host 127.0.0.1 --port 8000`. Not containerized here, deliberately:
containerizing it in this commit would mean the live verification runs against a different
execution path than the tests, and the Compose wiring has its own failure modes worth isolating.
**Loopback and not `0.0.0.0`** — the security group and `DOCKER-USER` cover published container
ports and neither covers a host process, and there is no TLS in front of this until Phase 10.

**Versions pinned, resolved on this machine 2026-08-16:** `fastapi==0.141.1`, `uvicorn==0.52.3`
(plain, not `[standard]` — the extras are uvloop/httptools/watchfiles and this is a read API in
front of Postgres, where the time is in the database), and `httpx2==2.10.0` in
`requirements-dev.txt`. pydantic 2 and starlette arrive through fastapi's own metadata and are
**deliberately not re-pinned**: a transitive version written down twice is a second copy that
drifts.

---

### What is measured so far, and what is not

**Offline: 444 passed with `DATABASE_URL` set; 314 passed / 130 skipped without it.** The API suite
contributes 70 tests, 24 of them integration.

**NOTHING IN THIS COMMIT HAS TOUCHED THE INSTANCE.** The integration tier runs against a local
throwaway Postgres with this project's real migrations and hand-seeded rows. It proves that a NULL
in a real nullable column arrives as `null`, that a reported zero arrives as `0`, and that `total`
counts the unpaginated set — **it says nothing about what the real data will do**, and the live
procedure below is what closes that.

---

### PHASE 8 — VERIFIED ON THE INSTANCE, 2026-08-16. COMPLETE.

**The read API has run against real data through the read-only role, and the role has been watched
refusing a write.** Six requests, six responses, and the property that no test could stand in for is
now observed rather than inferred.

---

### 1. THE READ-ONLY ROLE, PROVEN RATHER THAN ASSUMED

`waterway_api` was created with `GRANT SELECT ON ALL TABLES IN SCHEMA public` plus `ALTER DEFAULT
PRIVILEGES … GRANT SELECT ON TABLES`, and nothing else. **The refusal was watched:**

```
docker compose exec timescaledb psql -U waterway_api -d waterway -c "delete from job_runs where 1=0"
ERROR:  permission denied for table job_runs
```

**This is the one property in Phase 8 that no unit or integration test could have stood in for.**
The offline suite proves the *application code* issues no writes — no non-GET route is declared, the
route walk reaches all eight endpoints before asserting anything about their methods, `app/api/`
contains no writing SQL, and `engine.query` is called with `persist=False`. Every one of those is a
statement about this repo. **None of them says anything about what the database would do if the code
tried anyway.** The `DELETE` above is that second statement, and the two halves are now verified
independently: the code does not ask, and the role would refuse if it did. `CLAUDE.md § 20`'s "a
read-only role that has never been observed refusing a write is not known to be read-only" is
discharged.

`where 1=0` so the statement was harmless even if the grant had been wrong — the check had to be
safe to run *before* the answer was known, which is the whole point of running it.

**The uvicorn startup log was checked for the fallback WARNING and it was not there.** When
`API_DATABASE_URL` is absent the API falls back to `DATABASE_URL` — the owner connection — and logs
a warning saying so (`app/api/dependencies.py:53-67`). **No such line appeared**, so the six
responses below came through `waterway_api` and not through the owner. Without that check every
request below would pass identically under either role and this section would be describing a test
of the owner.

---

### 4. STANDING ITEM, NOT A DEFECT — THE SCHEDULER IS NOT A PERSISTENT PROCESS

**Whether the scheduler should run as a persistent background process — eventually a systemd unit —
rather than being started by hand per development session is an open question for Phase 10 or 12.**
It is what produces the `degraded: true` above and the `last_success: null` values behind it. It
does **not** block Phase 8, nothing in this commit addresses it, and nothing should: it is a
deployment decision, it belongs in the phase that containerizes the worker, and changing it now
would mean changing what the health endpoint was measured against in the same session it was first
measured.

---

### Still owed — three sub-steps of the live procedure are NOT covered by the six responses above

Recorded as not-run rather than left to be inferred from the six that were, because a procedure with
twelve steps and six recorded outcomes is otherwise indistinguishable from one where all twelve
passed:

- **Step 9's second half — `curl -s localhost:8000/api/gauges`.** Memphis's `observed_start` against
  its declared `2014-10-01` is the `CLAUDE.md § 15` envelope-versus-served comparison, and it is the
  one measurement in this procedure that is about the *catalog* rather than about serialization. Not
  requested in this session. The series endpoint was, and it is a different question.
- **Step 10's second half — `…&limit=50000` on a valid window, expecting 422 rather than a clamped
  200.** The span limit was confirmed over HTTP; the over-maximum *limit* was not. These are two
  separate rejections in `app/api/dependencies.py` and only one of them has been exercised live.
- **Step 11 — `python -m verify.preflight`, six gates green.** Owed from Phase 7's run as well, and
  now owed from two.

---

### Also still owed, unchanged by this run

- **Phase 7's step 1** — `python -m app.orchestration.migrate` showing twenty-five applied.
- **The sweep's `run_id` and wall time**, which every Phase 6 query in this file is written against.
- **DEBT 1a — the four thesis CSVs.** The script ran, the files exist, the paste has not happened.


---

## Phase 9 — the React frontend

### Phase 9 — the React frontend. **WRITTEN OFFLINE 2026-08-16. THE BUILD RECORD; THE OUTCOME IS ABOVE.**

Twenty-two files under `frontend/`, four views, eighteen numbered guards across six test files, and
**all twelve mutation rows watched red for the named test and restored.** `CLAUDE.md § 21` is the
contract that came out of it. **Nothing under `app/`, `tests/`, `migrations/`, `infra/` or `verify/`
was touched, and the API is unchanged.**

**37 tests pass, `tsc --noEmit` is clean under `strict`, and `vite build` produces a bundle.** No
instance was contacted: the tests run against fixtures captured from the real responses recorded in
the Phase 8 block below.

---

### The four views

```
/            Today      the conclusion (three shapes), the discharge window, the degraded banner
/river       River      four gauges, declared vs observed coverage, all in the no-baseline state
/signals     Signals    1 of 6,966 stated before the table; refused pairs are hatched rows
/health      Health     two tables, two questions, never one status light
```

---

### What is measured, and what is not

**Offline: 37 tests, `tsc --noEmit` clean, `vite build` succeeds.** Bundle: **609.74 kB raw,
183.27 kB gzipped**, plus 15.37 kB CSS and self-hosted woff2. Vite warns that the chunk exceeds
500 kB; **Recharts is the bulk of it** and code-splitting is a Phase 10 question, not a correctness
one.

**NOTHING IN THIS COMMIT HAS TOUCHED THE INSTANCE, AND NO VIEW HAS EVER RENDERED A REAL RESPONSE.**
Every fixture was hand-built from values recorded in this file. The live procedure below is what
closes that, and until it runs Phase 9 is code that passes its own tests.

---

### PHASE 9 — VERIFIED IN BROWSER, 2026-08-16. COMPLETE.

**Four views have rendered real API responses in a browser for the first time.** Everything below was
read off the screen, not inferred from a fixture. Two things came out of the session that are not
about the frontend at all — a toolchain dependency this project has never pinned, and a
process-management chain that cost most of the elapsed time — and they are recorded first because
they are the parts that will be rediscovered otherwise.

**One investigation ran alongside the walk and it has a verdict: the sign disagreement between the
conclusion sentence and the sweep's surviving row is EXPECTED, not a bug.** Section 4. Nothing was
changed in `app/` on the strength of it, and nothing should be.

---

### 3. WHAT WAS CONFIRMED, VIEW BY VIEW

**Today, passing case** (`site_id=07032000`, `as_of=2022-10-11`):

- The sentence rendered.
- **The sweep verdict read `1 of 6,966 pairs passed correction`, visible without interaction**, inside
  the same `estimate-block` element as the median rather than in a footer or behind a hover.
- **The range rendered as the dominant object — `−47.8%` to `+18.1%` — with the median `+7.4%` as a
  smaller tick on the bar.** This is the design's "uncertainty is rendered, not decorated" rule and
  `CLAUDE.md § 21`'s "an uncertainty range is never rendered smaller than the point estimate it
  qualifies", observed rather than asserted. A range spanning zero drawn small beside a large median
  is the one layout that tells a reader the rate rose, which is exactly what this range does not say.

**Today, provisional framing.** The passing result rendered behind a non-dismissible
**"Provisional — not cleared for quotation"** band naming three specific, human-owned open questions:
`MIN_ANALOGS = 4` against a 70% consistency threshold at n=4 (achievable consistencies are
0/25/50/75/100%); the temporal clustering of all four analogs inside 2015–2022; and the sign
disagreement with the sweep. **This exceeds what the Phase 9 prompt specified.** The prompt asked for
the band; **the three named reasons were the frontend session's own addition**, grounded in facts
already recorded in this file. Recorded as a deviation-upward rather than left to look like it was
specified — a band saying "provisional" without saying *why* is a caveat a reader cannot act on.

**Signals.** `1 of 6,966 scanned pairs passed Benjamini-Hochberg correction`, with the unadjusted
noise estimate stated inline: "roughly 348 significant results on pure noise, by construction, every
time". **That 348 is confirmed correct — section 5** — and one thing about how it is rendered is
flagged there.

**Health.** `degraded: true` banner visible. **Six jobs, all `overdue`, all `last success: never`** —
accurate, and not a defect: no scheduler has run continuously across development sessions, and a job
with no successful run on record is overdue rather than quiet (`CLAUDE.md § 12`). **Job-overdue and
data-stale rendered as two separate tables** with the explicit sentence distinguishing them, and
**`barge_rates` and `lock_movements` showed `fresh` while their jobs showed overdue** — the Phase 8
finding rendered as two facts rather than collapsed into one status light. This is the single
property most likely to have been quietly lost between the API and the screen, and it was not.

**River.** Renders as the stated coverage schematic: four gauges, declared record start beside
observed coverage, **"Baseline for a percentile: not served" on every row**, and a hatch legend
explaining why no gauge is coloured by percentile. No mid-scale colour anywhere — an unmeasured
baseline reads as texture, not as an average result.

---

### What is measured, and what is not

**Measured in this session:** four views against real responses, the two suites unchanged
(**314 passed / 130 skipped** offline for the repo, **37 passed** for the frontend — both re-run
during this writeback and both identical to the Phase 9 build record), and the Part 2 investigation
above.

**NOT measured, and not claimed:** the confirmatory sweep-distribution queries in section 4; step 5
of the live procedure (keyboard focus on every interactive element, layout at 375px) was walked but
is not recorded here as a per-element result; and `python -m verify.preflight` is **still owed, now
from four phases.** The Phase 9 procedure's `as_of=2022-09-06` no-current-event case and the Twin
Cities winter-nulls chart **were not reported back from this session**, so they are carried as
unconfirmed against real data rather than assumed to have passed alongside the views that were. All
of it is in `§ Up Next`.


---

## Phase 10 — domain, TLS, and hardening

### Phase 10 — domain, TLS, and hardening. **WRITTEN OFFLINE 2026-08-16. NOT LIVE. NOTHING IS ON THE PUBLIC INTERNET YET.**

Everything in this block is a decision recorded or a file written. **No DNS record was created, no
digest was resolved, no certificate was issued, no container was built, and nothing was rebooted.**
The live procedure is in `§ Up Next` and every step of it is the human's.

---

### 2. THE STACK — FOUR SERVICES, AND WHAT EACH ONE PUBLISHES

| Service | Image | Publishes | Notes |
|---|---|---|---|
| `timescaledb` | `timescale/timescaledb:2.26.2-pg16@sha256:332b99…` | **nothing** | unchanged; still the only resolved digest in the file |
| `api` | built from `Dockerfile.api`, base `python:3.12-slim@sha256:0000…` | **nothing** | non-root uid 10001, `CMD` is uvicorn in exec form, no `migrations/` in the image |
| `frontend-build` | built from `Dockerfile.frontend`, base `node:22-bookworm-slim@sha256:0000…` | **nothing** | runs to completion, writes the bundle into the `frontend_dist` volume, exits |
| `caddy` | `caddy:2-alpine@sha256:0000…` | **80, 443** | the only public face; `/data` bind-mounted from `/mnt/data/caddy/data` |

`worker` is still absent — the scheduler runs from a host venv and containerizing it is Phase 12,
where it needs its own restart-recovery verification.

**`restart: unless-stopped` on three of four, and `restart: "no"` on `frontend-build`.** The brief
asked for a test that every service carries `unless-stopped`; **that rule applied literally would
have made the frontend rebuild in a loop forever** — the policy restarts a container whenever it is
not running, regardless of exit code, and this one exits 0 on purpose. The test asserts the
partition instead, and is renamed to say so.

**`API_DATABASE_URL` changes host from `localhost` to `timescaledb` and that is a live step.**
Inside the Compose network, loopback is the api container's own namespace. Getting it wrong is a
connection-refused at container start — loud, immediate, in `docker compose logs api`. **The api
container is deliberately NOT given `DATABASE_URL`**: `app/api/dependencies.py` falls back to it
with a warning, and inside that container there must be nothing to fall back to, or the public API
can silently connect as the database owner.

---

### 4. THE CLAIM THE BRIEF ASKED TO CHECK — BOTH HALVES TRUE, NOTHING CHANGED

Read and confirmed; no file under `infra/terraform/` or the firewall scripts was modified.

- **`infra/terraform/security.tf` already allows exactly what is needed.** Three ingress rules and
  no more: 22 from `var.ssh_admin_cidr`, 80 from `0.0.0.0/0`, 443 from `0.0.0.0/0`, plus the
  explicit all-outbound egress rule. `tests/terraform/test_security_group.py` asserts the port set
  by equality and asserts no range covers 5432. **No security-group change is required by this
  commit.**
- **`infra/provision/configure_firewall.py` already carries the `DOCKER-USER` rules a published
  port needs**, in the right order: conntrack `RELATED,ESTABLISHED` `RETURN` on `-i` first, then
  `-o $EXT_IFACE` `RETURN`, then `-i $EXT_IFACE --dports 80,443` `RETURN`, then the terminal
  `-i $EXT_IFACE` `DROP`. Interface read from `/etc/dws/external-interface`, never hardcoded.
  **This is the commit that gives those rules something to protect** — until now the security group
  was tighter than the chain, which is why CONTEXT already records that the terminal `DROP` has
  never been observed from outside.

---

### 7. WHAT WAS RUN, AND WHAT IT PROVED

- **471 passed with a database, 341 passed / 130 skipped without.** 27 of those are new; the
  pre-existing 444 are unchanged, including
  `test_compose_file_does_not_invoke_the_migration_runner`, which still passes over a file that
  grew from one service to four. That test is why the frontend's copy step lives in
  `Dockerfile.frontend`'s `CMD` and not in a Compose `command:`.
- **`docker compose config -q` parses the file.**
- **`caddy validate` on the real Caddyfile: `Valid configuration`**, and `caddy fmt` clean. The
  adapted config confirms two things that would otherwise be assumed: `enabling automatic
  HTTP->HTTPS redirects`, and a TLS connection policy added for `srv0`. This ran in a throwaway
  `caddy:2-alpine` container on the laptop, **which is a syntax check and not a pin** — no digest
  from that pull was written anywhere.
- **All seventeen mutation rows watched red for the named test and restored**, with
  `__pycache__` cleared between the restore and the re-run, under `PYTHONDONTWRITEBYTECODE=1`. The
  harness records which test failed and the assertion text, and fails the row if the named test is
  not among them — Phase 9's finding, applied.

**TWO ROWS NEEDED A SECOND FORM, AND BOTH ARE FINDINGS RATHER THAN BOOKKEEPING:**

- **"Build the frontend after `compose up`"** first ran as a *deletion* of the build line, and the
  test went red on `never builds the frontend image` — the presence assertion, not the ordering
  one. The guard is about order, so the second form keeps both lines and swaps them; it then failed
  with `` `docker compose up -d` (line 56) comes before the frontend build (line 57) ``, which is
  the assertion the row is about.
- **"Derive the deploy path from the script's location"** first ran as a replacement of the
  constant and went red on the constant's absence. The realistic version of that mistake keeps the
  constant and overrides it afterwards; the second form does that and reaches the derivation scan:
  `` 'DEPLOY_DIR="$(cd "$(dirname "$0")/../.." && pwd)"' contains '$0' ``.

---

### PHASE 10 — VERIFIED ON THE INSTANCE, 2026-08-17. **THE STACK IS ON THE PUBLIC INTERNET.**

**`https://bargeanalysis.com` serves this project to anyone who asks.** Every step of the live
procedure ran. What follows is what was observed, including the two things that were nearly wrong
and the one thing that did not ship.

#### 1. The five digests, resolved on the instance

All pulled on the instance and written into the files — never from a laptop cache, never from an
agent's recollection (`CLAUDE.md § 5`):

| Tag | Digest | Where |
|---|---|---|
| `python:3.12-slim` | `sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a` | both `FROM` lines of `Dockerfile.api` |
| `node:22-bookworm-slim` | `sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436` | both `FROM` lines of `Dockerfile.frontend` |
| `caddy:2-alpine` | `sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648` | the `caddy` service |
| `timescale/timescaledb:2.26.2-pg16` | `sha256:332b99870c99c20d0d852ec600c097ee00ac93f5e0c75a8e83673394cdb36bfd` | **unchanged** |

The four placeholders were `sha256:` + 64 zeros and could not resolve, which is what made a missed
step fail at `docker build` rather than fall back to a floating tag (`CLAUDE.md § 12`). **All four
were hand-edited**, because `verify/preflight.py --write-digest` wrote only the first compose
`image:` line at the time. That is the gap section 6 of the build record recorded, and it is closed
in this commit — see `findings.md § I`.

#### 2. DNS, and the Cloudflare finding

A record `bargeanalysis.com` → `52.21.107.8`, plus `www`.

**The record was created PROXIED — orange cloud — and that would have broken issuance.** A proxied
record resolves the domain to Cloudflare's own IPs, so Let's Encrypt's HTTP-01 challenge never
reaches the origin and the validation fails against a name that resolves perfectly. It was switched
to DNS-only before Caddy was started, so nothing was rate-limited.

**Proxying is a legitimate future option and is not being ruled out here.** It would partly address
the rate-limit gap in section 5 by putting a CDN in front of `/api/conclusion`. But it needs either
DNS-01 issuance or Cloudflare origin certificates, and coupling that to a first-ever issuance — the
one attempt whose failure is rate-limited per domain per week — was correctly declined.

#### 3. Certificate issuance succeeded on the first attempt

**2026-08-17 02:41 UTC.** Let's Encrypt validated the HTTP-01 challenge from **five distinct IPs**:

```
23.178.112.105
13.59.181.48
44.247.39.78
16.171.0.27
13.212.174.231
```

Each received a **200** from Caddy. The whole exchange took **≈3.4 seconds**. ACME account
`acct/3638088191`.

**Multi-perspective validation passing is a stronger statement than "the certificate issued."** It
means DNS and port 80 are reachable from five vantage points on three continents, not merely from
the operator's own network — which is the check the operator cannot run themselves.

#### 4. Verified from outside the instance, over the public internet

The full evidence is in `query-outputs.md § Phase 10`. In summary:

- **`HTTP/2 200`** with the full header set, including a CSP whose `font-src` is `'self'` with **no
  CDN exception** — Phase 9's decision to self-host fonts through `@fontsource` paying off exactly
  as it was intended to.
- `referrer-policy: no-referrer`; `alt-svc` advertising HTTP/3.
- `http://` → **`308 Permanent Redirect`** to `https://`.
- `/api/health` answering over TLS with the same body Phase 8 verified through an SSM tunnel.
- **`nc` to 5432 and to 8000 HANGS rather than refusing**, and the distinction is the finding — a
  silent drop, not a connection refused. A refusal would mean something answered and declined; a
  hang means the packet was dropped with no reply at all. **That is the externally-visible proof of
  the `DOCKER-USER` terminal `-i ens5 -j DROP` rule**, which the housekeeping list has recorded as
  unobservable since provisioning 3 because the security group was tighter than the chain. It is
  observable now, and it was observed.

**Published ports across the running stack:** `caddy` 80 and 443 only; `api` shows `8000/tcp` with
**no host binding**; `timescaledb` shows `127.0.0.1:5432` **from the out-of-repo dev override
only**. The committed stack's published set is exactly `{80, 443}`, as
`tests/deploy/test_compose_shape.py` asserts by equality.

#### 5. Reboot survival — and the commit where `RequiresMountsFor` finally does something

`dws-stack.service` installed and enabled, then `sudo reboot`. The stack came back **unattended**:

- unit `active (exited)`
- `docker compose up -d` executed at **02:56:26**, ≈30 seconds after boot
- all three long-running containers up (`frontend-build` having run to completion and exited 0)
- `https://bargeanalysis.com` answering **200 before the operator reconnected**

**This is the run in which `RequiresMountsFor=/mnt/data` earns its place.** It was written into
provisioning 1 as the counterpart to choosing `nofail` in `fstab`: `nofail` lets the instance boot
without the data volume, which is right, and which therefore makes a silently-absent volume a real
state the system can be in — one where `/mnt/data` exists as an empty directory on the root disk
and every layer above reads it as a healthy, empty world. Until this reboot nothing had ever
depended on the mount at boot. Now the database's data directory and the ACME account key both do.

#### 6. Still true and still honest

**`/api/health` reports `degraded: true`.** No scheduler has run continuously across sessions, so
no ingest job has a recent success and several have none at all. The endpoint is reporting the
instance accurately, and the 200 alongside it is the decision rather than an oversight
(`CLAUDE.md § 20`). **Phase 12 owns it** — containerizing the worker is what fixes it, and it needs
its own restart-recovery verification.

#### 7. Did not ship: the per-IP rate limit. **The exposure is now live and public.**

Caddy has no rate limiter in core and the plugin needs a custom `xcaddy` build. What shipped is a
16KB request-body cap and three proxy timeouts, which **bound what one slow request can hold open
and do nothing about volume**.

Through Phase 9 this was theoretical, because the only route in was an SSM tunnel from one IP. It
is not theoretical now. **Distinct `(site_id, as_of)` pairs bypass `app/api/cache.py`'s conclusion
cache and each one runs an analog query**, and nothing limits how fast a stranger can ask for them.
**Phase 11 owns it, and it is the first item in `§ Up Next`.**

---

## Live-verification procedures, retained because they are re-runnable

*These are the step lists each phase was verified against. They are kept because a re-run is compared against them — after a reboot, after a dependency bump, before trusting a layer again — not because anything in them is outstanding. Where a step was NOT run, it says so in place.*

### PHASE 9 IS VERIFIED. **PHASE 10 IS NEXT.**

**The live procedure below has been run and its outcomes are recorded at the top of this file**, in
the same session, which is the process note above being followed rather than deferred — the second
phase running to close it. The steps are kept because they are rerunnable, not because the phase is
outstanding. **Three of them are: step 4's `as_of=2022-09-06` case, step 4's Twin Cities winter-nulls
chart, and step 6's `preflight` run.** Everything else in the list was walked and reported.

**Step 4 was the one that could not be inferred, and it paid twice** — once for the frontend, which
rendered every honesty property correctly against real responses, and once for the toolchain, since
nothing in steps 1–3 runs at all on the Node the instance had.

---

### Phase 9 live verification — RUN 2026-08-16, OUTCOMES AT THE TOP OF THIS FILE

**Before step 1 on a clean machine: check `node --version`.** The build needs ≥ 20 and prefers ≥ 22;
`npm ci` will warn `EBADENGINE` on every package and still exit zero. See standing item 1.

1. `cd frontend && npm ci && npm run build` — report bundle size and any warnings. Expect a chunk
   over 500 kB (Recharts).
2. `npm run test` — expect **37 passed**.
3. `npx tsc --noEmit` — clean.
4. Serve `frontend/dist/` and point it at the running API (`uvicorn app.api.main:app --host
   127.0.0.1 --port 8000`; the dev server proxies `/api` there, a static serve needs the same
   origin or a proxy). **Confirm by eye and report each:**
   - Today at `as_of=2022-10-11`, Memphis — the passing conclusion, with **`1 of 6,966` visible
     without interaction** and the provisional band above it.
   - Today at `as_of=2022-09-06` — the no-current-event state, and **no number on screen readable
     as a forecast.**
   - A Twin Cities rates chart across Jan–Mar 2022 — a **broken line** across the winter nulls, not
     a line through zero, and the legend note stating how many points were not measured.
   - Health — every job overdue **and** `barge_rates` not stale, in two distinct columns, with
     `last_success` rendering as **never** on both USDA jobs.
   - The degraded banner on Today.
5. Keyboard focus visible on every interactive element; layout holds at 375px.
6. `python -m verify.preflight` — six gates green. **Owed from Phase 7 and Phase 8 as well; now owed
   from three.**
7. Write the outcome back in the same session; set `§ Up Next` to Phase 10. **Done, 2026-08-16.**

**Also still owed, and none of it blocked Phase 9:** Phase 8's step 9 second half (`/api/gauges` —
this phase's `/river` view depends on it and **it has now been called live**, since `/river` rendered
four gauges with their declared and observed coverage, so what remains owed there is the recorded
response rather than the call), step 10 second half (`limit=50000` → 422), Phase 7's step 1
(`migrate` showing twenty-five applied), the sweep's `run_id` and wall time, and **DEBT 1a — the four
CSVs**.

**The state of the instance as this session left it, because it is not what the procedure assumes:**
the **database is up** — an SSM port-forwarding session reached it and it answered the auth
handshake — and **the API and Caddy are not running.** Anything rerunning step 4, or the standing
item 3 queries, starts by bringing the stack up.

**The three human decisions are still open and Phase 9 did not touch them.** They are what removes
`ProvisionalBand.tsx`, and they are still the only thing standing between a passing gate and a
quotable claim.

---

### PHASE 8 IS VERIFIED. **PHASE 9 WAS NEXT AND IS NOW BUILT.**

**The live procedure below has been run and its outcomes are recorded at the top of this file**, in
the same session, which is the process note above being followed rather than deferred for the first
time in this project. The steps are kept because they are rerunnable — after a reboot, after a
dependency bump, before trusting the read layer again — not because anything is outstanding in them.

**Step 3 was the one that could not be inferred, and it is the one that paid.** `waterway_api` was
watched refusing a `DELETE` with `permission denied for table job_runs`; every other step passes
identically whether the role is `waterway_api` or the owner, which is why the startup log was also
checked for the fallback WARNING that would have meant the owner was under test. It was absent.

**Two things came out of the run that the procedure did not anticipate:** `last_success` is `null`
on both USDA jobs rather than merely old — no scheduled ingest has ever recorded a success — and
that, not any threshold comparison, is why the health response shows overdue jobs beside fresh
tables. Both are written up in § 1–3 of the verification block at the top of this file.

**Three sub-steps below were NOT run and are listed as such in the verification block:** step 9's
`/api/gauges` request (the § 15 envelope-versus-served comparison), step 10's `limit=50000` rejection,
and step 11's `preflight`. **Also still owed and none of it Phase 8:** Phase 7's step 1 (`migrate`
showing twenty-five applied), the sweep's `run_id` and wall time, and **DEBT 1a — the four CSVs**.
The scheduler running as a persistent process rather than being started by hand per session is a
**Phase 10 or 12 question**, recorded as a standing item and deliberately not addressed here.

---

### Phase 8 live verification — RUN 2026-08-16, OUTCOMES AT THE TOP OF THIS FILE

1. `pip install -r requirements.txt` — confirm **`fastapi==0.141.1`** and **`uvicorn==0.52.3`**
   install at those versions. Also `pip install -r requirements-dev.txt` for `httpx2==2.10.0` if
   the suite is to be run there.
2. **Create the read-only role.** `<PASSWORD>` is generated with `openssl rand -hex 32`, **never
   `base64`** — `/` and `+` break `DATABASE_URL` parsing and surface as confusing host and port
   errors rather than as auth failures (`CLAUDE.md § 5`). **This agent neither generates nor sees
   it.**
   ```sql
   CREATE ROLE waterway_api LOGIN PASSWORD '<openssl rand -hex 32>';
   GRANT CONNECT ON DATABASE waterway TO waterway_api;
   GRANT USAGE ON SCHEMA public TO waterway_api;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO waterway_api;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO waterway_api;
   ```
   Add `API_DATABASE_URL` to `.env` using that role. **If it is absent the API falls back to
   `DATABASE_URL` and logs a WARNING saying so** — check the startup log for that line, because its
   presence means step 3 is testing the owner.
3. **PROVE THE ROLE IS READ-ONLY, by watching a write fail:**
   `psql "$API_DATABASE_URL" -c "delete from job_runs where 1=0"`
   **must fail with a permission error.** `where 1=0` so the statement is harmless even if the
   grant is wrong, which is the point: the check must be safe to run *before* you know the answer.
4. `uvicorn app.api.main:app --host 127.0.0.1 --port 8000`. **Loopback, not `0.0.0.0`** — the
   security group and `DOCKER-USER` do not cover a host process and TLS is Phase 10.
5. `curl -s localhost:8000/api/health | python -m json.tool` — confirm **one row per cadence job**
   (six), the `data` block with the five registered tables, and the `degraded` field. Record which
   jobs and tables are degraded; that is a measurement of the instance, not a pass/fail.
6. `curl -s "localhost:8000/api/conclusion?site_id=07032000&as_of=2022-10-11" | python -m json.tool`
   — expect the **passing** shape: `analogs 4`, `consistent 3`, `median_pct` about **+7**, and the
   `sweep` block with `best_q 0.0446`. **Report `scanned_pairs` and `passing_pairs` verbatim** —
   Phase 6 recorded 1 of 6,966 and this is the first time the API states it.
7. `curl -s "localhost:8000/api/conclusion?site_id=07032000&as_of=2022-09-06" | python -m json.tool`
   — expect `"gate": "no_current_event"`, **and read the whole body by eye for any estimate-shaped
   number.** The recursive-walk test asserts this offline; step 7 is a human confirming it against
   real data, which is a different check.
8. `curl -s "localhost:8000/api/rates?segment=Twin%20Cities&horizon=nearby&start=2022-01-01&end=2022-03-31"`
   — a winter window on the segment with the most closures (426 of Twin Cities' records have no
   rate). **Confirm `"pct_of_tariff": null` appears and `0` does not.**
9. `curl -s "localhost:8000/api/gauges/07032000/series?start=2022-09-01&end=2022-11-01"` — confirm
   `total`, `limit` and `offset`, and **whether `total` exceeds `limit`** (62 days should not, but
   record the number). Also `curl -s localhost:8000/api/gauges` — Memphis's `observed_start` against
   its declared `2014-10-01` is the § 15 envelope-versus-served comparison, measured.
10. `curl -s -o /dev/null -w "%{http_code}\n" "localhost:8000/api/rates?start=2000-01-01&end=2026-01-01"`
    — expect **422**. And `...&limit=50000` on a valid window — also **422**, not a clamped 200.
11. `python -m verify.preflight` — six gates green. **NOT RUN 2026-08-16. Still owed from Phase 7's
    run as well, and now owed from two.**
12. **Write the outcomes back into this file in the same session**, and set `§ Up Next` to Phase 9.
    **Done 2026-08-16 — the first time in this project that a live verification and its write-back
    happened in one session.**

**Steps 5, 6, 7, 8, 9 (first half) and 10 (first half) produced the six responses recorded at the
top of this file. Steps 9 (second half), 10 (second half) and 11 were not run.**

---

### Phase 7 live verification — RUN ON THE INSTANCE 2026-08-16, retained for its queries

**Outcomes at the top of this file.** Six of the eight steps were reported back; steps 1 and 7 were
not, and that is recorded there rather than assumed. Retained because these are the queries any
re-run is compared against — and because step 2's instruction is worth keeping in the form it was
written *before* the distances arrived, given what they turned out to be.

**EXPECT REFUSALS.** The engine sits on a sweep that found one contemporaneous relationship out of
6,966. A run where the gate passes everywhere is a reason to look for a bug before celebrating.

> **AND IT PASSED EVERYWHERE IT WAS ASKED.** That sentence was written before the run and it stands:
> the passes are recorded, and so are the three unresolved questions that came with them. **The
> instruction was not softened after the fact.**

1. `python -m app.orchestration.migrate` — expect **0024 and 0025** applied, **twenty-five total**.
   **NOT REPORTED BACK.** Implied by the queries having run at all; implied is not recorded.
2. **LOOK AT THE DISTANCES BEFORE SETTING ANY CUTOFF:**
   `python -m app.analogs.engine --as-of 2022-09-06 --site 07032000 --explain`
   **Report the k distances and whether they cluster or spread.** `SIMILARITY_CUTOFF` is `None` and
   **this is what one would be set from, later, by you** — a cutoff proposed before this step is a
   claim about similarity made before anybody had seen one.
   **RAN, at `--as-of 2022-10-11`. They CLUSTER: 14.718–15.401, a spread of ~4.5% of the mean.**
3. The same for **2023-09-05**.
   **RAN, at `--as-of 2023-09-19`. Tighter still: 7.414–7.693, ~3.7%. A cutoff between rank 1 and
   rank 5 would have to discriminate at the third significant figure — so it would admit all of them
   or none, and step 2 answered the cutoff question by making it moot.**
4. **BOTH LABELLED EVENTS, PLAINLY.** For each: did the gate pass or refuse, and with what counts —
   `n_raw_detections`, `n_collapsed_events`, `n_analogs`, `n_consistent`. **If both refuse, that is
   the headline** and it is recorded as such rather than worked around.
   **BOTH PASSED.** 2022: 77 raw -> 5 collapsed, 4 analogs, 3 consistent. 2023: 161 raw -> 6
   collapsed, 5 analogs, 4 consistent. **Verbatim output at the top of this file** — and the raw
   counts against the collapsed ones are the collapse rule earning its place: 161 detections would
   have satisfied the >=4-analog gate forty times over from six events.
5. **COUNT HOW MANY EVENTS EXIST AT ALL**, because it may settle the question outright:
   ```sql
   select gate_result, count(*) from analog_queries group by 1 order by 2 desc;
   select as_of_date, n_raw_detections, n_collapsed_events, n_analogs, n_consistent, gate_result
     from analog_queries order by created_at desc limit 10;
   ```
   **If the collapsed event count over the full history at Memphis is under 4, the gate can never
   pass at this site**, and that is a fact about the dataset worth stating in one sentence rather
   than discovering repeatedly.
   **IT IS 5 AND 6, SO THE GATE CAN PASS AT MEMPHIS.** The constraint is not that there are too few
   events; it is that the ones there are all fall inside 2015–2022.
6. **A date with no low-water condition** — e.g. `--as-of 2021-05-12` — must refuse **cleanly** with
   `no_current_event` rather than returning distant analogs for a condition that is not happening.
   **CONFIRMED, and at a sharper boundary than this step asked for: `--as-of 2022-09-06` returned
   `no_current_event`** — a week before the counter first ticked up, inside the year of a real event.
   The detector is date-sensitive across the boundary rather than firing indiscriminately, which is
   the property decision 1 exists for and the one a fixture cannot demonstrate.
7. `python -m verify.preflight` — six gates green. Its migration-count gate reads the directory, so
   twenty-five migrations need no change to it.
   **NOT REPORTED BACK, and nothing implies it.** This is the one step still genuinely open.
8. **Write the outcome back in the same session**, including the refusals and the distances, and set
   `§ Up Next` to Phase 8.
   **DONE, in the same session, 2026-08-16 — the second time this project has managed it.**

---

### Phase 8 — after the engine has been run and its outcome is recorded

The FastAPI surface and the React frontend: `CLAUDE.md § 6`'s `api` and `caddy` services, the river
map, and the chart. **It calls `engine.query` and renders `AnalogResult`** — which carries no
estimate on a refusal, so the UI cannot display one by accident. The refusal sentence is a
first-class state in that UI, not an empty chart.

---

### Phase 6 live verification — RUN ON THE INSTANCE 2026-08-15, retained for its queries

**All ten steps ran. Outcomes at the top of this file.** Retained because the queries are the ones
any re-run is compared against, and because step 8's instruction is worth keeping in the form it was
written in *before* the numbers arrived.

1. `python -m app.orchestration.migrate` — expect **0022 and 0023** applied, **twenty-three total**.
2. `python scripts/capture_thesis_queries.py --out /tmp/thesis` — four CSVs. **Paste them into the
   `PHASE 4 — VERIFIED` and `PHASE 5 — VERIFIED` sections as fenced blocks, replacing the notes that
   say the output is still owed. THIS CLOSES DEBT 1a**, which has been open across two phases. The
   script exits non-zero and names the empty files if any query returns no rows — an empty table
   there means the query is measuring something narrower than its name, not that there is nothing
   in the window.
   **RAN. The four CSVs exist. THEY ARE NOT PASTED IN, so debt 1a is still open — this is the one
   step of the ten that is not finished.**
3. **The full sweep:** `time python -m app.signals.sweep --lag-min -21 --lag-max 21`.
   Report **grid size, rows written, wall time and the `run_id`.** Expect the grid near **7,740**
   minus the duplicate skips (`5 × 4 × 3 × 43 × 3`, less one feature at each fully-degenerate site
   across every horizon, lag and regime — **387 per skipped site-feature**). Phase 5 measured Memphis
   and Vicksburg as fully degenerate, so **6,966 is the number to expect if that still holds** — and
   if it does not, the sweep prints which pairs it skipped and why, which is the answer.
4. **THE DENOMINATOR, STATED FIRST.** The CLI prints it, and take it from the database too:
   ```sql
   select count(*) as scanned, count(*) filter (where passes_gate) as passing
     from signals where run_id = <id>;
   ```
   **Report both numbers together, always.** A passing count without its denominator is the
   dishonest form of this result.
5. **The top rows, read as the top of a distribution and not as findings:**
   ```sql
   select feature_name, site_id, horizon_days, lag_days, regime,
          statistic, p_value, q_value, n_effective, folds, directional_consistency
     from signals where run_id = <id> and passes_gate
    order by q_value limit 20;
   ```
6. **Check the negative-lag half explicitly:**
   ```sql
   select regime,
          count(*) filter (where lag_days < 0 and passes_gate) as neg,
          count(*) filter (where lag_days > 0 and passes_gate) as pos
     from signals where run_id = <id> group by 1;
   ```
   If negative lags dominate, the claim changes from **"the physical signal leads"** to **"the market
   prices the forecast."** **Report it either way** — the CLI says so itself when they do.
7. **Compare against the Phase 5 observation.** Find the `days_below_p10` / Memphis rows across lags
   and regimes:
   ```sql
   select horizon_days, lag_days, regime, statistic, q_value, folds, directional_consistency
     from signals
    where run_id = <id> and feature_name = 'days_below_p10' and site_id = '07032000'
    order by regime, horizon_days, lag_days;
   ```
   Does the **onset** regime show what the eyeball suggested, and does **recovery** reverse?
   **READ THE NEXT PARAGRAPH BEFORE INTERPRETING THIS QUERY** — the regime definition and the
   Phase 5 narrative do not line up the way the words suggest.
8. **THE NULL-RESULT CHECK, AND IT IS THE ONE THAT MATTERS.** Confirm the passing count is not close
   to **5% of the grid** (~387 of 7,740). If it is, the sweep is finding significance at exactly the
   rate chance predicts, and **that is the finding** — record it as such rather than reaching for the
   strongest row. A table whose survivors are indistinguishable from noise is a real answer about the
   thesis, and it is the answer this phase was built to be able to give.
9. `python -m verify.preflight` — six gates green. Its migration-count gate reads the directory, so
   twenty-three migrations need no change to it.
10. **Write the outcome back in the same session**, including the denominator, and set `§ Up Next`
    to Phase 7.
    **DONE, in the same session, 2026-08-15 — the first time this project has done that.** The
    denominator leads the write-up. Outstanding from step 3: the `run_id` and the wall time.

---

### Phase 7 — built on 2026-08-15. Its live procedure is at the top of this section.

---

### Phase 5's live verification, retained for its step-9 queries

**Phase 5 — DONE on the instance 2026-08-15.** Outcomes at the top of this file. Step 9 is the one
worth keeping: re-run the 2022 and 2023 thesis queries against `features` where
`feature_name = 'discharge_min'`, using `anomaly` in place of `avg(g.value)`, and paste both tables
into the write-up.

---

### Phase 4's live verification, retained for the two queries still owed their output

**Phase 4 close-out — DONE on the instance 2026-08-14.** Outcomes at the top of this file.

1. `python3 -m app.orchestration.migrate` — expect **0018** applied, **eighteen total**.
2. **Movements backfill:** `time python3 -m app.ingest.usda_backfill --dataset lock_movements`.
   Expect **26,144 rows**, of which **108 have NULL tons** and **8,218 have zero tons**. All three
   numbers are measured; a mismatch is worth understanding before proceeding rather than after.
3. Per-lock confirmation — the backfill prints this table itself, but run it against the database
   too:

   ```sql
   select lock, count(*), count(*) filter (where tons = 0) as zeros,
          count(*) filter (where tons is null) as nulls
     from lock_movements group by 1 order by 1;
   ```

   Expect **AK Lock 1 / MS Locks 27 / OH Olmsted at 4,928 rows each with nulls 71 / 11 / 26**, and
   the other four at **2,840 with zero nulls**.
4. Compare landed rows against `source_row_count` for **all four** ingested datasets. The CLI does
   this itself and exits non-zero if any came up short. **It compares RECORDS RECEIVED, not rows
   written** — a correct rerun writes zero rows.
5. `python3 -m verify.preflight` — six gates green. Its migration-count gate reads the directory, so
   eighteen migrations need no change to it.
6. Start the scheduler. Confirm `usda_rates_ingest` **and** `usda_movements_ingest` both register,
   fire, and write `job_runs` rows with plausible `rows_written`. **A rerun over already-loaded
   weeks may legitimately write 0 — that is correct, not a failure.**
7. Confirm the heartbeat reports both USDA tables fresh, **and that a winter week with NULL rates
   does not read as stale** — the freshness-counts-rows guard from the previous commit, live.
8. Confirm
   `docker compose exec timescaledb psql -U waterway -d waterway -c "select count(*) from barge_rates"`
   returns **24,780** (three horizons × 8,260).

---

### 9. FIRST CONTACT WITH THE THESIS

Both halves are now in one database. Run this and **report what it shows, including if it
contradicts the thesis:**

```sql
select r.week_ending,
       r.pct_of_tariff as cairo_memphis_nearby,
       round(avg(g.value)) as memphis_discharge_cfs
from barge_rates r
left join gauge_series g
  on g.usgs_site_id = '07032000'
 and g.date between r.week_ending - interval '6 days' and r.week_ending
where r.location = 'Cairo-Memphis'
  and r.horizon = 'nearby'
  and r.week_ending between '2022-07-01' and '2022-12-31'
group by r.week_ending, r.pct_of_tariff
order by r.week_ending;
```

What to look for, **in order of what would change the project**:

- **Does the rate rise as discharge falls?** That is the thesis.
- **Does the rate rise *before* discharge falls?** That is the "operators price the forecast" risk
  named in the handoff. It is a **finding, not a failure** — it changes the claim from "the physical
  signal leads" to "the market prices the forecast," and that reversal becomes the story
  (`CLAUDE.md § 0`: when a measurement contradicts the plan, the measurement wins).
- **Does nothing happen?** Also a result. Report it.

Run the same query for **2023-07-01 to 2023-12-31**, the second labelled event.

**DO NOT TUNE ANYTHING ON THE BASIS OF WHAT THIS SHOWS.** It is an observation. Phase 6's lead-lag
sweep is where the relationship gets measured properly, with a walk-forward gap. This step exists so
that a surprise arrives now rather than after three more phases have been built on an assumption.

10. **Write the outcome back in the same session**, including **the step 9 query output verbatim**,
    and set `§ Up Next` to Phase 5.

**STEP 9 — RUN 2026-08-14. THE ANCHOR POINTS ARE RECORDED AT THE TOP OF THIS FILE; THE FULL TABLES
ARE STILL OWED.** The session that wrote the Phase 5 commit received the endpoints, the peaks and
the troughs, and not the row-by-row output — and did not invent the intervening weeks
(`CLAUDE.md § 4`: never synthesize a replacement). **Both queries above are still the ones to
re-run**, and their output belongs in the `PHASE 4 — VERIFIED` section at the top, replacing the
note that says so.

**Known risks worth watching.** `date` is a SoQL type name as well as the column name; if the
service rejects it as a bare identifier, `parse_page` raises `SocrataResponseError` carrying
Socrata's own message — loudly, never as an empty page — and the fix is to quote it in
`usda_rates.ORDER_COLUMN`/`since_clause` and the movements pair. A forward-rate record missing
`rate_month` aborts the run by design, because in that column a silent NULL is indistinguishable
from a legitimate nearby one. And **in both USDA modules a present-but-blank measure raises rather
than being stored as NULL** — `rate` and now `tons` alike. Each source expresses "no value" by
omitting the key, and `tons` additionally publishes an explicit `0` on 31% of records, so a blank is
a different and unmeasured condition in both. **If either fires, measure what those records look
like before changing anything.** For `tons` specifically this will stop the movements backfill dead;
that is the intended behaviour, and the 108 gaps it protects sit on the summary locks.

**Phase 3's close-out verification — DONE on the instance 2026-08-14.** Its outcomes are recorded
at the top of `§ Current state`; the step list is retained below for the record of what was asked.

1. `python3 -m app.orchestration.migrate` — expect **0011 and 0012** applied, **twelve total**.
2. Confirm the corrected seeds and the NULL instantaneous starts:
   `select usgs_site_id, dv_record_start, iv_record_start from gauges order by 1;`
   Expect `1990-01-01 / 2007-10-01`, `2014-10-01 / NULL`, `2008-01-01 / NULL`,
   `2004-03-17 / NULL`.
3. Confirm `gauge_known_gaps` holds **exactly two rows** — Memphis 1994-09-30 → 2014-09-30 and
   Baton Rouge 2023-01-04 → 2023-08-14.
4. **Full daily backfill, all four sites** — no `--site`, no `--start`, so each walks from its own
   corrected seed. Run it inside `tmux`:
   `time python3 -m app.ingest.daily_backfill 2>&1 | tee /tmp/daily_backfill.log`.
   Expect roughly **60k rows total**; report the actual figure and the wall time.
5. Per-site check:
   `select usgs_site_id, count(*), min(date), max(date) from gauge_readings_daily group by 1
   order by 1;` **Compare each `min(date)` to its corrected seed — they should now agree.** A
   discrepancy still means the SEED is what to fix, in a new numbered migration.
6. Confirm the Baton Rouge 2023 gap is reported as expected rather than unexplained:
   `grep -i "2023-0" /tmp/daily_backfill.log | head`. Any `UNEXPLAINED` line is a range nobody has
   measured — it is not a failure, it is the list of things to measure before Phase 5 interpolates
   across one.
7. **THE COMPRESSION MEASUREMENT — the outstanding Phase 3 deliverable.** For **both**
   hypertables: record uncompressed size, compress the chunks older than the policy window, record
   compressed size, **report both ratios** here and in the README. At ~60k daily rows the daily
   ratio may be unimpressive. **Report it anyway** — and note that at this row count TimescaleDB
   is an engineering measurement on a real series rather than a storage necessity, since Postgres
   alone would handle this volume comfortably.
8. `python3 -m verify.preflight` — six gates green. Its migration-count gate reads the directory,
   so twelve migrations need no change to it.
9. Start the scheduler; confirm **both** ingest jobs fire and write `job_runs` rows.
10. **Write the outcome back as its own small commit** — Phase 3 verified on the instance, with the
    row counts, wall times, and both compression ratios. See the process note above: this is the
    rule this file most needs and has never followed.

**Phase 3.5's live verification, below, is retained for its steps 2 and 7.**
Run it in order; steps 2 and 4 are deliberate rehearsals.

1. `python3 -m app.orchestration.migrate` — expect **0007–0010** applied, **ten total**.
2. **CONFIRM THE RENAME PRESERVED TIMESCALEDB STATE, BEFORE ANYTHING ELSE:**
   ```
   select * from timescaledb_information.compression_settings
    where hypertable_name = 'gauge_readings_iv';
   select count(*) from gauge_readings_iv where usgs_site_id = '07010000';
   ```
   Expect the settings intact and the **223,706 St. Louis rows** still present under the new name.
   Verified locally against the pinned image, but the instance is where it counts — a dropped
   compression policy is invisible until the storage bill.
3. Confirm `gauges` carries `dv_record_start` and `iv_record_start`, four rows, floors differing
   per site.
4. **One site, one decade first:** `python3 -m app.ingest.daily_backfill --site 07032000
   --start 2010-01-01 --end 2020-01-01`. Report row count and elapsed time before the full run.
   This is also the first time the pinned `format=json,1.1` and the plain-date `startDT`/`endDT`
   form meet the live daily service.
5. Full daily backfill, all four sites. Report total rows, wall time, and **the per-site
   first-data dates** — those are what the seeded floors get reconciled against.
6. `select usgs_site_id, count(*), min(date), max(date) from gauge_readings_daily group by 1
   order by 1;` Compare each `min(date)` to its seeded `dv_record_start`. **A discrepancy means
   the SEED is wrong**; fix it in a new numbered migration, never by editing `0008`.
7. **Spot-check the view at a seam.** Pick a St. Louis date where both records exist and confirm
   `source` reads `iv`; pick a Memphis 2015 date and confirm it reads `dv`.
8. **THE COMPRESSION MEASUREMENT — STILL OUTSTANDING, STILL THE DELIVERABLE.** Record uncompressed
   size for **both** hypertables, compress the older chunks, record compressed size, and report
   **both ratios** here and in the README:
   ```
   python3 -m app.ingest.usgs_ingest --compression-stats            # gauge_readings_iv
   psql -c "SELECT compress_chunk(c) FROM show_chunks('gauge_readings_iv',
            older_than => INTERVAL '30 days') c;"
   psql -c "SELECT compress_chunk(c) FROM show_chunks('gauge_readings_daily',
            older_than => INTERVAL '1 year') c;"
   ```
   `compression_stats()` takes a table name, so the daily figure comes from the same helper. Real
   numbers taken here, never a vendor claim — **and if a ratio disappoints, report it.**
9. `python3 -m verify.preflight` — six gates green. Its migration-count gate reads the directory,
   so ten migrations need no change to it.
10. Start the scheduler; confirm **both** `usgs_ingest` and `usgs_daily_ingest` fire and write
    `job_runs` rows with plausible `rows_written`.

**Phase 3's own live procedure, below, is superseded except for one step.** Its steps 1–2 and 7–9
are covered above. **Its instantaneous backfill (steps 3–5) should now be run for St. Louis
ONLY** — see the housekeeping note on rolling retention. The rest of that block is kept as the
record of what Phase 3 asked for.

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

**THEN PHASE 5 — the normalizer and the features.** Phase 4 is the last ingest phase: with rates
and movements landed, both halves of the pair exist in the database and nothing further is needed
from an external source to build a feature.

Two things Phase 3 and 4 leave that Phase 5 must respect rather than rediscover:

- **`gauge_known_gaps` exists so nothing interpolates across a hole.** A rolling mean or a seasonal
  baseline computed straight over Memphis 1994–2014 draws a smooth line no gauge ever read. The
  rows are queryable for exactly this reason (`CLAUDE.md § 15`).
- **`0` and `NULL` in `lock_movements` are different facts** and a feature that averages them
  together is wrong in the weeks that matter most (`CLAUDE.md § 16`).

The freshness-registry requirement in `CLAUDE.md § 12` binds for every ingest client, and
`CLAUDE.md § 14`, `§ 15` and `§ 16` are the contracts each one is written against.

---

### Phase 10 live verification — NOT RUN. ORDER MATTERS; DO NOT START CADDY BEFORE STEP 3 PASSES

1. **Point DNS.** At the registrar, an A record: `bargeanalysis.com` → `52.21.107.8`. **Do not add
   `www` to the Caddyfile until a `www` A record exists** — a name that does not resolve blocks
   issuance for the one that does.
2. **Resolve five digests, on the instance, never from a laptop and never from memory.** For each
   of `python:3.12-slim`, `node:22-bookworm-slim`, `caddy:2-alpine`:
   `docker pull <tag> && docker image inspect <tag> --format '{{index .RepoDigests 0}}'`. Paste
   into **both** `FROM` lines of `Dockerfile.api`, **both** of `Dockerfile.frontend`, and the
   `image:` line of the caddy service. All five currently read `sha256:` + 64 zeros and **cannot
   resolve** — a missed step fails at `docker build`, not silently. **Record the exact
   `caddy version`, `python --version` and `node --version` in this file** so the pins trace to
   releases rather than to rolling tags.
3. **Verify DNS BEFORE touching Caddy:** `dig +short bargeanalysis.com` must return `52.21.107.8`.
   If it does not, **wait.** Let's Encrypt rate-limits failed issuance per domain per week.
4. **Create the read-only role if it does not exist, and PROVE it** (`CLAUDE.md § 20`): the GRANTs,
   then a `DELETE` that must fail. Phase 8 watched `waterway_api` refuse one with
   `permission denied for table job_runs`; confirm it still does.
5. **Update `.env`:** add `API_DATABASE_URL` with host **`timescaledb`**, not `localhost` — see
   `.env.example`, which now documents the shape. Confirm both passwords are 64-hex by eye.
6. **Build and start:** `docker compose build && docker compose up -d`, or
   `infra/provision/deploy.sh` once the checkout is at `/opt/inland-waterway-signals`. Then
   `docker compose ps` — **four services, and `PORTS` populated only on `caddy`.**
7. **Watch issuance:** `docker compose logs -f caddy`. Expect a successful ACME exchange. **If it
   fails, read the error and WAIT. Do not restart in a loop.**
8. **From a laptop, not the instance:** `curl -sI https://bargeanalysis.com | head -5` (200 plus
   the headers); `curl -s https://bargeanalysis.com/api/health | python -m json.tool` (the Phase 8
   body, over TLS); `curl -sI http://bargeanalysis.com` (301/308 to https); then open the site and
   walk all four views. **This is the first time this project is reachable without an SSM tunnel.**
9. **Confirm what is NOT exposed:** `nc -zv bargeanalysis.com 5432` must fail; `nc -zv
   bargeanalysis.com 8000` must fail; `/api/health` over https must succeed while both do.
10. **There is no rate limit to prove.** Section 3 of the Phase 10 block. If a burst of requests to
    `/api/health` is issued anyway, **expect no 429** — record that, do not record it as untested.
11. **Install and enable `infra/provision/dws-stack.service`, then `sudo reboot`.** Reconnect and
    confirm the whole stack came back on its own and the site still serves. **Only a reboot proves
    the unit works**, and it is also the only proof `RequiresMountsFor` is doing anything.
12. `python -m verify.preflight` — six gates green. **Owed from Phases 7, 8 and 9 as well; now
    owed from four.** Note that gate 1 checks the timescaledb image only — see section 6.
13. Write the outcome back in the same session and set `§ Up Next` to Phase 11.


---

## Appendix — the housekeeping list as it stood on 2026-08-16

*Moved here whole. `../CONTEXT.md` carries the condensed, still-open version; this is the full text, including the items that have since closed, because several of them carry measurements that exist nowhere else.*

### Housekeeping — open, non-blocking

- **THE INSTANTANEOUS BACKFILL RUNS FOR ST. LOUIS ONLY, and the other three now say so in the
  data.** `iv_record_start` is NULL at Memphis, Vicksburg and Baton Rouge (migration `0011`) —
  "rolling window" is not a date, so the honest column value is empty rather than a date that
  expires. `app.ingest.backfill` refuses those sites in `resume_point` with a message naming
  rolling retention; it previously aborted at their first window on a missing series, which was
  also correct, and this only moves the abort earlier. **Still open, and still a human's call:
  whether the IV backfill applies to rolling-retention sites at all.** The likely answer is that
  it does not and the incremental poll is the only path. First candidate for the next ingest
  commit.
- ~~**Both compression ratios are unmeasured.**~~ **MEASURED on the instance 2026-08-14 and
  recorded at the top of `§ Current state`: 3.36:1 on `gauge_readings_iv`, 7.65:1 on
  `gauge_readings_daily`, with most of the win in index bytes.** What remains open from this item:
  any README or résumé line quoting them must carry the honest framing — real measurements, real
  reductions, and **at ~290k rows Postgres alone would have been adequate**. Also open: the
  **30-day IV chunk-interval tuning candidate**, logged and deliberately not acted on, since a
  chunk interval change affects new chunks only.
- **The USDA dataset ids are NULL and the USDA ingest cannot run until a human resolves them.**
  Live verification steps 2–4. `cost_indicators` is seeded with no ingest path at all, on purpose.
  The Socrata **field names** in `usda_rates.FIELDS` / `usda_movements.FIELDS` are provisional and
  are confirmed at the same visit to the catalog.
- **`gauge_series` buckets instantaneous data by UTC date while USGS computes its daily mean over
  the site's LOCAL calendar day.** On the lower Mississippi that is a 5–6 hour offset at both
  edges of the window. Small for a river that moves in feet per day; not zero. Fixing it properly
  means recording a timezone per gauge, which is a schema change and a human decision. The
  `source` column is what keeps the seam visible in the meantime.
- **`app/ingest/usgs_daily_ingest.py` was created outside the brief's file list**, and
  `app/orchestration/scheduler.py` modified outside it. Both were forced: decision 9 requires a
  `usgs_daily_ingest` cadence entry, a cadence entry with no registered function makes
  `build_scheduler()` refuse to start, and putting a scheduled unit inside `daily_backfill.py`
  would have hidden a job in a module whose name says CLI. Mirroring the Phase 3
  `usgs_ingest.py` / `backfill.py` split was worth one extra file.
- **`tests/ingest/test_compression.py` now also carries the Phase 3.5 schema tests** (rename
  survival, daily hypertable, daily primary key, the `gauges` column split). The brief listed
  those as "Schema/integration" with no file; this module was already the "read the schema back
  from the catalog" suite, so they went here rather than into a fifth test file.
- ~~**The seeded `dv_record_start` floors are BRACKETS from one-month January probes.**~~
  **Corrected 2026-08-14 by `0011`, and the probe method is now named as the error in
  `CLAUDE.md § 15`.** What survives from this item: **St. Louis's 1990-01-01 is a deliberate
  bound, not a discovered start** — USGS daily records at that gauge run to the nineteenth
  century, 35 years exceeds what the ten-year seasonal medians and the analog search need, and
  reaching further back is a human's decision to seed. Memphis's earlier segment is a different
  case: it exists, it is reachable, and it is abandoned on purpose (see `§ Current state`).

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
- ~~Domain not purchased. Blocks Phase 10 only.~~ **Closed:** `bargeanalysis.com` is purchased and
  is a literal throughout `Caddyfile`, `tests/deploy/`, and the Phase 10 block. **The A record does
  not exist yet** — that is step 1 of the Phase 10 live procedure, and nothing else in that
  procedure may start before `dig` returns `52.21.107.8`.
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
