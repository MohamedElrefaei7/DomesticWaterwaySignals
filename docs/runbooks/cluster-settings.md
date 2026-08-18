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

