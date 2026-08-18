# Phase 11 deployment runbook

**Status: Phase 11 and Stage B are code-complete and unapplied.** Nothing below has been run against
AWS or on the instance. This document replaces the runbook that lived in a chat transcript, which is
exactly the class of artifact this project has decided is not ground truth (`CLAUDE.md` § 0).

**Every stop-condition here is `exit != 0`.** That is the point of `verify/phase11/`: the condition is
enforced rather than remembered. Where a step says "read the plan", it means read it — a verifier
checks what can be checked mechanically and says so, and the rest is yours.

## The exit-code contract, once

| code | meaning | what to do |
|---|---|---|
| `0` | every check ran and passed | continue |
| `1` | a check ran and failed | stop. The report names the check, what was expected, and what was observed |
| `2` | usage, or an unmet precondition | stop. Nothing was verified either way — satisfy the precondition and re-run |

`1` and `2` send you to two different investigations. `1` is about the infrastructure; `2` is about
the verifier's own inputs — an absent plan file, an unreachable backend, a missing GRANT, running
Stage J from the wrong machine. A verifier that could not tell never exits `0`.

Add `--json` to any invocation for a machine-readable summary. **Nothing here writes to `CONTEXT.md`
or any tracked file** — you transcribe, so that every claim in the log is one somebody looked at.

## Placeholders

Every `<...>` below is a placeholder. **Keep the angle brackets when you copy a line, and replace the
whole token.** Each of these has been pasted literally on this project at least once, and the tooling
does not change that.

| placeholder | where it comes from |
|---|---|
| `<admin-cidr>` | your current public address as a `/32`, for `terraform.tfvars` |
| `<planfile>` | the path you passed to `terraform plan -out=` |
| `<plan-json>` | the file you redirected `terraform show -json <planfile>` into |
| `<health-check-id>` | `terraform output api_health_check_id`, after Stage D |
| `<backup-bucket>` | `terraform output backup_bucket_name`, after Stage D |
| `<base-url>` | `https://bargeanalysis.com` unless you are testing another origin |

---

## PHASE 12 CHANGED HOW THREE OF THESE STEPS ARE RUN — READ THIS FIRST

This runbook was written when the scheduler ran from a host venv and the backup spawned containers.
Phase 12 made the scheduler the fifth Compose service, so:

| was | is now |
|---|---|
| `python3 -m migrations.run` | `python3 -m app.orchestration.migrate` |
| `python3 -m app.orchestration.run_once <job>` | `docker compose exec scheduler python -m app.orchestration.run_once <job>` |
| a throwaway restore CONTAINER | a throwaway DATABASE, `dws_restore_test_<suffix>` |

**The host venv is still needed, and still needs activating** — the migration runner and every
`verify.phase11` stage connect from the host, and the runner cannot move into a container because
the images deliberately do not contain `migrations/` (`CLAUDE.md § 3`). What no longer needs it is
the JOBS: they run inside the scheduler container, where `boto3` is installed by the image.

**Two things must be done once on the instance before any job runs**, and both are new:

```
sudo install -d -o 10001 -g 10001 -m 0750 /mnt/data/backups /mnt/data/restore-test
```
The scheduler container runs as uid 10001 and Docker creates a missing bind-mount source as
`root:root`, so an absent directory silently becomes an unwritable one. The backup asserts
writability at job start, so this fails cleanly rather than after `pg_dump` — but it does fail.

**And `Dockerfile.scheduler`'s `postgresql-client-16` version is a placeholder that cannot
resolve.** Resolve it on the instance with the `apt-cache madison` command in that file's header,
write it into the pin, record it in `CONTEXT.md`, and only then `docker compose build scheduler`.
`verify/preflight.py` refuses the placeholder by value until you do.

---

## The six human actions

Everything else in this document is a verifier call or a human reading a plan. These six are yours
and stay yours (`CLAUDE.md` § 1) — the verifiers observe and cannot act, enforced by an allow-list
that omits `apply`, `init`, `plan` and every `docker` verb that changes anything.

1. **A2** — `terraform apply` in `bootstrap/`, creating the state bucket.
2. **C2** — `terraform init -migrate-state`, moving state into that bucket.
3. **D3** — `terraform apply` for the Phase 11 resources.
4. **D5** — clicking the SNS subscription confirmation link in your email.
5. **G2** — `docker compose exec scheduler python -m app.orchestration.run_once backup_nightly`.
6. **H2** — `docker compose exec scheduler python -m app.orchestration.run_once restore_test_monthly`.

---

## Stage A — the state bucket

Run from a laptop, in `infra/terraform/bootstrap/`.

**A1.** `terraform init`

**A2. HUMAN ACTION.** `terraform apply`

Read the plan first. It creates one `aws_s3_bucket`, its versioning, encryption, public-access block
and bucket policy — six resources and nothing else.

**A3.** The local state in `bootstrap/` is **disposable and stays local**. Do not add a backend block
pointing at the bucket beside it; that is a circular dependency that surfaces as an unrecoverable
`init`. If this state is ever lost, the bucket is **imported, never recreated**.

---

## Stage C — moving state into the backend

Run from `infra/terraform/`.

**C1.**
```
python3 -m verify.phase11 c-pre
```
Asserts: `backend.tf` and `bootstrap/main.tf` name the same bucket (the value is written twice
because a `backend` block cannot interpolate); the local state holds exactly the 17 protected
addresses; the bucket has versioning **Enabled**.

Versioning is checked *before* the move because it is the recovery path for a truncated state write,
and a bucket without it reports identically to one with it until the day it matters.

**Stop if `exit != 0`.**

**C2. HUMAN ACTION.** `terraform init -migrate-state`

Answer `yes` when it offers to copy the existing state.

**C3.** Render a plan for the verifier to read:
```
terraform plan -out=<planfile>
terraform show -json <planfile> > <plan-json>
```

**C4.**
```
python3 -m verify.phase11 c-post <plan-json>
```
Asserts: no local `terraform.tfstate` or `.backup` remains; **every `resource_change` is `["no-op"]`
and `applyable` is false**; the remote state holds exactly the 17 addresses; at least one object
version exists at the backend key with a real `VersionId`.

Two things worth knowing about that middle assertion, both measured against Terraform 1.15.8:

- **A "No changes." plan is not an empty `resource_changes` list.** It carries one `no-op` entry per
  resource. An empty list means the plan was computed against empty state — the migration carried
  nothing — and `c-post` fails on it.
- **`c-post` needs a plan file and cannot use `terraform show -json` alone.** That emits a *state*
  document, which has no `resource_changes` key at all, so "assert there are no changes" over it is
  true on every input forever.
- **Do not try to eyeball this with `terraform show <planfile> | grep '^Plan:'`. IT PRINTS
  NOTHING.** The `Plan: N to add, ...` summary is emitted by `terraform plan` as it runs; `show`
  renders the saved plan's contents and has no such line. A grep for it therefore returns empty on
  a plan that adds thirteen resources and on a plan that adds none, which reads as "no changes"
  either way. Read the plan by eye, or let the verifier read the JSON — those are the two checks
  that can tell the difference.

**Stop if `exit != 0`.** A plan that wants to create anything that already exists means applying
would build a second copy of running infrastructure.

**C5. HUMAN, and the tooling does not cover this.** Open two shells and run `terraform plan` in both
at once. The second must block or report a held lock. If both proceed, locking is decorative and two
concurrent applies would each discard the other's record of what they created.

---

## Stage D — the Phase 11 resources

**D1.**
```
terraform plan -out=<planfile>
terraform show -json <planfile> > <plan-json>
```

**D2.**
```
python3 -m verify.phase11 d-pre <plan-json>
```
Seven checks, in order: no mutating action against the protected addresses; the state contains
exactly them (so a resource added later and not written down cannot be silently unprotected); the
13 Phase 11 resources are all created or all already present; **exactly one** `aws_s3_bucket`, not
"at least one"; no IAM Allow grants `s3:Delete*`; no IAM Allow grants `s3:*`, `*` or a `NotAction`;
no IAM Allow resource reaches the state bucket.

**`d-pre` ACCEPTS TWO PLAN SHAPES AND DERIVES WHICH ONE IT IS FROM THE PLAN.** Stage D applied on
2026-08-18 and its thirteen resources joined `protected.py`, which broke four of these checks at
once: the creates can never happen again, the single bucket create can never happen again, the
thirteen it now protects are exactly the ones a rebuild plan creates, and prior state now holds
thirty addresses rather than seventeen. A guard that goes red on the correct state trains its own
removal, so:

- **pre-apply** — the thirteen are all being created and none is in state. This is a REBUILD, which
  is not hypothetical for a project whose backup and restore-test apparatus exists so the stack
  *can* be rebuilt.
- **applied** — the thirteen are all present and all no-op. This is what a plan looks like today.

**Anything else is a hard failure**, and that is the half worth having: a plan where some of the
thirteen exist and others do not is a partial apply, and an empty plan whose thirteen are simply
absent from state still fails — which was the original reason the creates check existed.

The IAM checks **parse the policy JSON** and are scoped to `aws_iam_policy` resources and to
`Effect: Allow`. Both narrowings matter: `aws_s3_bucket_policy.backups` legitimately carries
`"Action": "s3:*"` in a **Deny** statement, and `aws_iam_policy.backups`'s own description reads
*"No delete - retention is a lifecycle rule"*, so a text search reports a delete grant on the plan
that is correct.

**Stop if `exit != 0`.**

**D3. HUMAN ACTION.** `terraform apply <planfile>`

Apply **the same file** the plan was rendered from. That is what makes checking the rendering worth
anything — the verifier never generates its own plan, so the artifact reviewed is the artifact
applied.

**D4.** Note the outputs: `terraform output`. You need `api_health_check_id`, `alerts_topic_arn` and
`backup_bucket_name` below; the verifiers read them from state themselves.

**D5. HUMAN ACTION.** Confirm the SNS subscription from the email AWS just sent.

**D6–D8.**
```
python3 -m verify.phase11 d-post --base-url <base-url>
```
Three checks: the string Route53 is **actually configured to search for** appears in the bytes
`/api/health` **actually returns through Caddy**; the SNS subscription is confirmed and not
`PendingConfirmation`; the budget exists with the limit `variables.tf` configures.

The fetch sets `Accept-Encoding: identity` explicitly. Route53 matches its literal against the first
5,120 bytes **as sent**, so a compressed body leaves every application-side test green while the
monitor goes permanently blind. That is why this is a step and not an inference.

`PendingConfirmation` is a **failure, not a state to wait out**: nothing is delivered until the link
is clicked, and every AWS call about the topic succeeds meanwhile.

**Stop if `exit != 0`.**

---

## Stage E — onto the instance

From here on, on the instance, in `/opt/inland-waterway-signals`.

```
cd /opt/inland-waterway-signals
git pull
source .venv/bin/activate
set -a; . ./.env; set +a
```

**The activation is still load-bearing, but for the migration runner and the verifiers rather than
for the jobs.** Both connect from the host; the runner cannot move into a container because the
images deliberately do not contain `migrations/`. The JOBS no longer need it — from Phase 12 they
run inside the scheduler container, which carries `boto3` and a pinned `postgresql-client`.

The failure the old note described is worth keeping because it still applies to anything you run on
the host: a bare `python3 -m ...` reaches the system interpreter and dies on `ModuleNotFoundError:
boto3`, which names a Python package rather than an interpreter — so it reads as a missing
dependency and the fix somebody reaches for is `pip install boto3`, which succeeds and moves the
failure one import further down.

**E1.**
```
python3 -m verify.phase11 e
```
Asserts: preflight enumerates **eight image references across four files** (parsed from
preflight's own output, not recounted here — two implementations of one fact drift); no running
container's resolved digest differs from the digest `docker-compose.yml` pins; a free-space baseline
is written to `/mnt/data/phase11-verify-baseline.json`.

**Eight across four as of Phase 12** — `Dockerfile.scheduler` added two `FROM` lines. It was six
across three. `verify/preflight.py` also gained a gate asserting the client and server postgres
majors agree, derived from the compose tag and the package pin with neither hardcoded; **it fails
until the client version placeholder is resolved**, by value, naming the command that resolves it.

A digest mismatch means the pin did not hold, which means `verify/preflight.py` gate 1 did not catch
it — worth surfacing loudly rather than shrugging at.

The baseline goes under `/mnt/data` rather than `/tmp` because `/tmp` is cleared on reboot and may be
a tmpfs sized from RAM; a baseline that vanishes leaves the later stages comparing against nothing.

**Stop if `exit != 0`.**

---

## Stage F — migration 0026

**F1. HUMAN.** `python3 -m app.orchestration.migrate` — one pending file, `0026`.

**The module path in the first version of this runbook was wrong.** `migrations.run` does not
exist; the runner is `app/orchestration/migrate.py` and `python3 -m migrations.run` fails with
`No module named migrations`. Run it from the host venv, not the container: the images deliberately
do not contain `migrations/`, so there is nothing for a container to run.

**F2.**
```
python3 -m verify.phase11 f
```
Asserts: `schema_migrations` is at 26; `to_regclass('public.backups')` is non-null; both triggers
exist **and are enabled**.

It reads `tgenabled`, not just `tgname`. `CLAUDE.md` § 3 permits a human to disable the delete
trigger for a genuine correction — "which is a visible act" — and the visible part only works if
something looks. A disabled trigger is exactly as protective as an absent one.

**Stop if `exit != 0`.** If it reports that `waterway_api` lacks `SELECT` on `backups`, **stop and
raise it** — the verifier will not fall back to the owner connection, and granting the missing SELECT
is a modelling-adjacent decision for a human, not something the tooling should do quietly.

**F3. HUMAN, and the tooling does not cover this.** The behavioural proof that the insert-once
trigger works is a genuine write, so it stays yours. Insert a probe row and try to update it:

```
docker compose exec timescaledb psql -U waterway -d waterway
```
```sql
INSERT INTO backups (s3_bucket, s3_key, byte_size, row_counts, compressed_chunks,
                     verified, verified_at)
VALUES ('<backup-bucket>', 'verification/f3-trigger-probe', 1, '{}'::jsonb, 0, false, NULL);

-- MUST BE REFUSED, naming the column:
UPDATE backups SET byte_size = 2 WHERE s3_key = 'verification/f3-trigger-probe';
```

The refusal surfaces as `psycopg.errors.RaiseException` — **not** a constraint violation — and the
message names the column: `refusing to update column byte_size on backup_id=N`.

**The `verification/` prefix is not decoration.** `backups.tf`'s lifecycle rules match
`backups/daily/` and `backups/monthly/` and nothing else, so anything outside `backups/` is a row no
retention rule reaches. Stage G refuses any *backup* written outside those prefixes, and Stage H uses
the same fact to tell a real backup from this probe — so the mark it checks is on the right row.

Leave the probe row in place. It is evidence, and `backups` is append-only.

**F4. HUMAN.** Start the scheduler once, if this instance has never run it, **before Stage G**.
`apscheduler_jobs` is created by `SQLAlchemyJobStore`'s own DDL on first start rather than by a
migration, and the backup asserts its `--exclude-table-data` target exists before dumping. On a
rebuilt instance the backup otherwise refuses with an error about an excluded table that says nothing
about scheduler startup ordering. Stage G checks this first so you read the real cause.

---

## Stage G — the first backup

**G1.** Confirm the read-only role really is read-only, if it has never been observed refusing a
write. This is a human step and stays one — the verifier connects as `waterway_api` precisely so it
cannot issue this.

```
docker compose exec timescaledb psql -U waterway_api -d waterway \
  -c "delete from job_runs where 1=0"
```
Must be refused. A read-only role that has never been observed refusing a write is not known to be
read-only (`CLAUDE.md` § 20).

**G2. HUMAN ACTION.**
```
docker compose exec scheduler python -m app.orchestration.run_once backup_nightly
```
Exit `0` succeeded, `1` the job failed (recorded in `job_runs`), `2` usage.

**Inside the container, not on the host.** `pg_dump` lives in the scheduler image now — the
container has no Docker socket to spawn a matched one-shot container with, because mounting it is
root-equivalent on the host. The job asserts `pg_dump --version`'s major equals the server's before
it dumps anything, so a stale image fails here rather than producing an archive nobody has reason
to trust.

**`docker compose exec`, not `run`.** `run` starts a second container; `exec` uses the one that is
already up, which is the one whose environment and mounts you verified.

**G3.**
```
python3 -m verify.phase11 g
```
Five checks: `apscheduler_jobs` exists; a `backups` row exists; `rows_written` is **NULL, not 0**;
`row_counts` keys equal the public-schema table set exactly, in both directions; every `s3_key` is
under a retained prefix.

The `backups` row is read on a connection this verifier opened, **after the job's process exited**.
That is the whole reason this stage exists: the Phase 11 defect Stage B audited produced a successful
job, a verified archive in S3, and no row, with every layer agreeing with itself.

**Stop if `exit != 0`.**

---

## Stage H — the first restore test

**H1.** Note the free space before: the restore test downloads the archive **and creates a
throwaway database on the production server**, both on `/mnt/data` — the same volume production's
own data files are on.

**H2. HUMAN ACTION.**
```
docker compose exec scheduler python -m app.orchestration.run_once restore_test_monthly
```
This one takes minutes. It downloads the archive **from S3** — never from local staging — creates a
database named `dws_restore_test_<suffix>` `TEMPLATE template0`, creates the timescaledb extension
in it, restores into it, and makes the read-only role attempt a `DELETE` that must be refused.

**A THROWAWAY DATABASE, NOT A CONTAINER, AS OF PHASE 12**, because spawning a container from inside
the scheduler container would need the host's Docker socket. The name guard that bounds the single
`DROP` this system performs is asserted twice — at creation and again immediately before the drop —
with two independent conditions (`CLAUDE.md § 3`).

**ON FAILURE THE THROWAWAY IS KEPT, DELIBERATELY, and the error names it.** It is the only state
that can say why a restore failed. Read the error, then remove it by hand:
```
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '<name>';
DROP DATABASE <name>;
```

**H3.**
```
python3 -m verify.phase11 h
```
Five checks: no `dws_restore_test_*` **database** survives; exactly
`{timescaledb, api, caddy, scheduler}` running and exactly those plus `frontend-build` present;
`frontend-build` exited `0`; `restore_test_monthly` has a success row with `rows_written` NULL;
`restore_verified_at` is set on the **most recent real backup** and on **no** probe row, with
`restore_verified_counts` beside it.

`frontend-build` exits by design (`restart: "no"`, gated by `service_completed_successfully`), which
is why the running set is one smaller than the present set. `scheduler` joined the running set in
Phase 12 — a stack without it looks entirely healthy from the outside and does no work at all.

A leaked throwaway is looked for in `pg_database`, not among containers. **A survivor is not
automatically a bug**: on failure the job keeps it on purpose. Either it is that evidence, or a run
was killed before its `finally` — and those send you to two different places, so the check reports
the name rather than guessing.

**Stop if `exit != 0`.**

**H4. HUMAN, and the tooling deliberately does not cover this.** The `pg_restore` asymmetry check.
This is the phase's most transferable measurement and watching two exit codes disagree is worth more
than a verifier reporting that they did.

Take a real archive, truncate it to **95%**, and run both checks against the cut file:

```
ls -l <archive>
head -c $(( $(stat -c%s <archive>) * 95 / 100 )) <archive> > /tmp/cut.dump

pg_restore --list /tmp/cut.dump   > /dev/null; echo "--list      exit $?"
pg_restore -f /dev/null /tmp/cut.dump      ; echo "full restore exit $?"
```

**`--list` accepts it. The full restore rejects it.** That disagreement is the entire reason
`CLAUDE.md` § 3 binds every backup job to `pg_restore -f /dev/null` and forbids `--list` as
verification.

**Use 95%, not 33%.** At one third the table of contents is destroyed too, so `--list` catches it —
which means a test built from the original incident's own proportions stays green when verification
is swapped to `--list`, and the contract it exists to defend can be deleted underneath it. The
diagnostic cut is the one where the TOC survives and the data does not, and it is *further* from the
incident than the obvious choice.

---

## Stage I — watching the monitor fire

**I1. HUMAN, and the tooling does not cover this.** Cause a degraded response or stop the API. The
verifier polls; it never acts, and the allow-list means there is no `docker stop` to reach for by
accident.

**I2.**
```
python3 -m verify.phase11 i --expect Failure --timeout 600
```
Polls `aws route53 get-health-check-status` every 30 seconds until **every checker region** reports
Failure. Route53 evaluates on a 30-second interval with a failure threshold of 3, so nothing can flip
in under ~90 seconds.

**A timeout is `exit 2`, not `exit 1`.** "I did not see it within the window" is not "I saw the wrong
thing", and only one of those is evidence about the monitor. Exit 2 carries every observation on the
way, so you can see whether it was moving.

**I3. HUMAN.** Confirm the email arrived. **This step is the whole point of the monitoring part** —
`§ Up Next` item 6 has been open with status unknown since Phase 10, and an alarm nobody has watched
fire is an alarm nobody knows is wired up.

**I4.** Restore the service, then:
```
python3 -m verify.phase11 i --expect Success --timeout 600
```

---

## Stage J — the rate limiter

**Run this from a laptop. Not from the instance.**

```
python3 -m verify.phase11 j --base-url <base-url>
```

The stage refuses to run on the instance and exits 2 if IMDS answers — from there the source address
is the Docker network's, so the limiter buckets the whole run against an address no external client
shares, and the burst still returns plausible status codes including 429s. Nothing would look wrong.

Five checks: 429s appear across a burst of **30 distinct `(site_id, as_of)` pairs**; every 429 carries
an integer `Retry-After`; the 429 body has **no estimate keys at any depth and no unexplained
numbers**; `/api/health` never 429s; `/` never 429s.

The pairs are distinct because that is the whole exposure `CLAUDE.md` § 22's amendment describes —
each distinct pair misses the conclusion cache and runs an analog query. A burst of identical
requests measures the cache and concludes the limiter does not work.

`/` is checked not because it is protected but because § 22 records the bundle, CSS and fonts as an
**accepted** residual exposure at the edge. Seeing an accepted exposure confirmed is worth more than
reading that somebody accepted it.

**Stop if `exit != 0`.**

---

## What this tooling does not cover

A runbook that implies full automation is a runbook someone will trust past its limits.

| not covered | why |
|---|---|
| `terraform apply` in `bootstrap/` (A2) | human-only by `CLAUDE.md` § 1 and § 8 |
| `terraform init -migrate-state` (C2) | writes state; the allow-list omits `init` |
| the two concurrent `terraform plan`s (C5) | needs two shells racing; nothing here can observe a lock contention it did not cause |
| `terraform apply` for Phase 11 (D3) | human-only |
| the SNS confirmation click (D5) | it is an email link |
| the `waterway_api` `DELETE` refusal (G1) | issuing it is a write; the verifier connects as a role that cannot |
| the `backups` trigger refusal (F3) | same — a genuine write, and the proof is that it fails |
| the `pg_restore` asymmetry (H4) | deliberately manual; watching two exit codes disagree is the lesson |
| stopping a service for Stage I (I1) | the verifier polls and never acts |
| the alert email arriving (I3) | it lands in an inbox |
| `docker compose down && up -d` × 3 | the Part 4 healthcheck race is load-dependent, which is why it is repeated rather than observed once — see `§ Up Next` item 11 |
| the free-space *delta* after G and H | Stage E records the baseline; comparing it is a human read for now |

---

## After it all passes

Transcribe the `--json` summaries into a writeback commit on `CONTEXT.md`. **The verifiers do not
write it for you**, and that is deliberate: a verifier that auto-writes puts unreviewed claims into
the log, and the log's whole value is that every claim in it was looked at.

Two things to update by hand in the same commit:

- `verify/phase11/protected.py`'s `PROTECTED_ADDRESSES` — after Stage D the state holds 17 + 13
  managed resources plus the `aws_caller_identity` data source, and `d-pre`'s set-equality check will
  fail on the next plan until the list matches. That failure is correct and is the mechanism working.
- `§ Up Next` — items 1 through 11 are what this document executes.
