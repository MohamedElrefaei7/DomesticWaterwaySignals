# Runbook — changing a Postgres cluster setting

**Contract:** `CLAUDE.md § 24`. **Values:** `infra/postgres/settings.py`. **Gate:**
`verify/preflight.py`.

Applying a setting is a **human step**, like `terraform apply`. Nothing in this repo issues
`ALTER SYSTEM`, and `tests/deploy/test_cluster_settings.py::test_nothing_in_the_repo_issues_alter_system`
asserts that by walking the arguments of every `.execute()` call in preflight.

---

## Why you cannot just edit `postgresql.conf`

You can, and that is the problem — it is on the data volume at
`/var/lib/postgresql/data/postgresql.conf`, written by `timescaledb-tune` when the image
initialised the volume on 2026-08-11, and **nothing in git would show it changed**. All 33
non-default settings this cluster runs came from there and were untracked until 2026-08-18.

The two fixes that look obvious and are not:

- **Bind-mounting `postgresql.conf` from the repo breaks `initdb` on a fresh volume.** `initdb`
  writes that file itself. A rebuild is the exact case this is meant to make reproducible.
- **`include_dir` is unavailable.** It is commented out in the generated file, and it is one of
  the settings `ALTER SYSTEM` refuses — so there is no way to enable it without editing the file
  that cannot be mounted.

So the property is not "the settings live in git". It is **the committed values are authoritative
and any divergence is detected**, the same shape as the image digests and the client-version pin.

---

## Changing an enforced setting

1. **Add or edit the entry in `infra/postgres/settings.py`**, with its reason. The reason is not
   decoration — `max_locks_per_transaction` is 512 because of an arithmetic that is written out
   there, and a bare number gets tidied downwards by somebody economising on memory.

2. **Apply it on the instance.** `ALTER SYSTEM` writes `postgresql.auto.conf`, which Postgres
   reads *after* `postgresql.conf` and which therefore wins. That file was empty as of
   2026-08-18, so nothing the tuner chose is being overwritten.

   ```sh
   docker compose exec timescaledb \
     psql -U waterway -d waterway -c "ALTER SYSTEM SET max_locks_per_transaction = 512;"
   ```

3. **Check whether a restart is needed**, rather than assuming:

   ```sh
   docker compose exec timescaledb psql -U waterway -d waterway \
     -c "SELECT name, setting, source, pending_restart FROM pg_settings WHERE name = 'max_locks_per_transaction';"
   ```

   `max_locks_per_transaction` is **postmaster-scoped**: `ALTER SYSTEM` succeeds, `setting` still
   reports the old value, and `pending_restart` is `true` until the cluster restarts.

4. **Run preflight before restarting.** It should **FAIL**, naming `RESTART PENDING`. That failure
   is the gate working: it is how you know the setting was written and has not taken effect.

   ```sh
   set -a; . ./.env; set +a
   python verify/preflight.py
   ```

5. **Restart the cluster.**

   ```sh
   docker compose restart timescaledb
   ```

   This drops every connection. The scheduler and API reconnect on their own; if a job was
   mid-run it lands in `job_runs` as a failure, which is correct and should be left there.

6. **Run preflight again.** It should now pass, reporting the running value and the computed slot
   count.

### The two failure messages, and why they are different

| Observed | Gate says | What to do |
|---|---|---|
| running value ≥ floor, `pending_restart = true` | `RESTART PENDING` | Step 5. The value on disk and the value running disagree — including the case where somebody *lowered* it and the change is waiting for the next boot. |
| running value < floor, `pending_restart = false` | `NEVER APPLIED` | Step 2. Nothing is pending; `postgresql.auto.conf` does not carry this setting at all. |

A gate reading only `setting` passes the first row. A gate reading only `pending_restart` passes
the second. Both are checked, and each half has its own test.

---

## Recording the tuner baseline

`TUNER_BASELINE` is **recorded, not enforced**. Its values are a function of the instance's memory
and cpu count, so a rebuild onto a different size derives different ones *correctly* — enforcing
them would make the gate go red on a working cluster.

Capture it from the running cluster; **do not type it**:

```sh
set -a; . ./.env; set +a
python verify/preflight.py --resolve-baseline   # prints the diff, writes nothing
python verify/preflight.py --write-baseline     # records infra/postgres/tuner-baseline.json
```

Then review the diff and commit the file. Until that has been run, the committed file carries the
literal `NEVER-CAPTURED` sentinel rather than `{}` — an empty capture would read as "this cluster
runs nothing but defaults", which is the placeholder-that-resolves failure `§ 12` forbids for
image digests.

After a rebuild, run `--resolve-baseline` and read the diff. That is the whole purpose of the
file: before it existed, a re-derivation that differed was silent.


---

## Migration 0027 — stop the scheduler first

`0027_gauge_readings_iv_chunk_interval.sql` rewrites `gauge_readings_iv`: it archives the old
986-chunk hypertable, creates a new one at a 365-day interval, and copies every row across.

**Part 1 must be applied and verified first.** The copy queries and counts across all 986 chunks,
which is the query that fails at the old lock ceiling.

**Stopping the scheduler is required, not advisory.** `usgs_ingest` runs hourly and writes to this
table; a write landing mid-copy goes into the archive and is lost from the live table — a few
missing readings, with nothing reporting a problem.

```sh
docker compose stop scheduler
set -a; . ./.env; set +a
python3 -m app.orchestration.migrate --status
python3 -m app.orchestration.migrate
docker compose start scheduler
docker compose exec scheduler python -m app.orchestration.run_once usgs_ingest
```

### What the migration guards, and what it does not

| Guard | Catches | Does not catch |
|---|---|---|
| `job_runs` check, refuses with a sentence naming the job | an ingest that is **already** running — you forgot the stop | one that starts after the check |
| `LOCK TABLE … ACCESS EXCLUSIVE` under `lock_timeout = 30s` | a writer holding a conflicting lock; fails in 30s rather than waiting | — |
| Held for the whole transaction | any writer touching either table during the copy | — |

**An advisory lock is deliberately not used.** An advisory lock only detects a party that also
takes it, and `usgs_ingest` takes none — `pg_try_advisory_lock` would succeed against a running
ingest and report the coast clear. That is a guard that reports correct while the thing it guards
against is happening.

**Not claimed:** whether a writer that *blocks* on the table lock and resumes after commit lands in
the new table or the archive depends on Postgres re-resolving the relation name after acquiring the
lock. That was not measured, so it is not relied on. Stop the scheduler.

### The archived table

`gauge_readings_iv_archived_20260818` is left in place — `CLAUDE.md § 3` archives rather than
drops, and only a human runs a `DROP`. **It roughly doubles this table's footprint**, and it will
appear in every nightly backup and in the restore test's per-table `row_counts` until dropped.
Expect `backups.byte_size` to step up on the next run.

Dropping it is a human decision and a human command. There is no migration that will do it.
