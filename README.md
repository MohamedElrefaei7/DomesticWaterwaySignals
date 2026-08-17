# Inland Waterway Signals

River discharge on the Mississippi system physically constrains how much grain a barge can carry,
and that constraint propagates into published barge freight rates within days. This system polls
USGS gauge data and USDA barge rates, builds features, runs a lead-lag sweep to discover which
pairs deserve detectors, and answers one question through a public API and a small React frontend:

> The last N times conditions looked like this, what happened to the rate?

**It is designed to say "insufficient history" rather than manufacture conviction.** The confidence
gate is ≥ 4 analogs and ≥ 70% directional consistency; below that, the system refuses, and a
refusal is rendered with the same visual weight as a result.

`https://bargeanalysis.com` — read-only, unauthenticated, and deliberately so. See
**What is publicly reachable** below.

Contracts live in `CLAUDE.md`. Current state, open questions and the phase log live in
`CONTEXT.md` and `docs/`.

---

## What this can currently claim about the river

**Nothing quotable yet, and that is a finding rather than a gap.** The lead-lag sweep scanned
**6,966** pairs; **1** passed multiple-comparison correction, contemporaneously at `lag_days = 0`,
with a **negative** statistic. The analog gate passed on both labelled low-water events at medians
of **+7%** and **+10%**, across ranges that span zero, on analogs drawn entirely from 2015–2022.

Three modelling questions stand between that and a sentence worth quoting, and all three are human
decisions. They are listed in `CONTEXT.md`.

Every number in this file is reproducible from a query.

---

## Architecture

Four containers on one EC2 instance, one Docker Compose stack, brought up at boot by a single
systemd unit. Everything is polled on a schedule; there is no streaming daemon and reintroducing
one is out of scope by contract.

| Service | Contents | Published |
|---|---|---|
| `timescaledb` | Postgres 16 + TimescaleDB, on a **separate** EBS volume | no |
| `api` | FastAPI + uvicorn, read-only, single worker | no — proxied |
| `frontend-build` | Pinned, containerized Vite build; emits a static bundle | no |
| `caddy` | TLS termination, serves the bundle, proxies `/api` | 80, 443 |

Every image is pinned by digest, resolved on the machine that runs it. `verify/preflight.py`
enumerates all six references across the Compose file and both Dockerfiles and fails on any that
is unpinned, untagged, or interpolated.

---

## Backup and recovery posture

**This section states what recovery does NOT cover, because those limits are only defensible when
they are written down.**

### What runs

- **Nightly** (`backup_nightly`): `pg_dump` from a one-shot container started off the *same pinned
  digest as the server*, writing with `-f` to `/mnt/data` — nothing piped through stdout. Per-table
  row counts are captured **inside the dump's own snapshot** (`pg_export_snapshot()` +
  `pg_dump --snapshot`), so the counts describe the state the archive actually contains.
- **Verification is a full `pg_restore -f /dev/null`, requiring exit 0 *and empty stderr*.**
  `pg_restore --list` is not verification: it reads only the archive's table of contents. Measured
  in this repo's own test suite (`test_backup_integration_list_would_not_have_caught_it`), by
  truncating a real archive and running both checks:

  | cut | `--list` | full restore |
  |---|---|---|
  | 33% — the 2024 incident's own proportions | **rejects** | rejects |
  | 95%, 98%, 99% | **accepts** | rejects |

  **The incident's own proportions are the least diagnostic case available.** At one third of this
  database's size the truncation destroys the table of contents as well, so `--list` catches it
  too — which means a test built from a fixture resembling the incident **stays green even if
  verification is downgraded to `--list`**. The cut that can tell the two apart is the one where
  the TOC survives and the data does not, and it is *further* from the original event, not closer.
  Generally: **a fixture that resembles the original incident is not automatically a good test of
  the guard against it.** Pick the fixture that distinguishes the implementations, and confirm by
  mutation that it does.
- **Monthly** (`restore_test_monthly`): the most recent verified archive is downloaded **from S3**,
  restored into a throwaway container, `ANALYZE`d, and compared against the recorded snapshot with
  **no tolerance** and key sets checked in both directions. The restored read-only role is made to
  attempt a `DELETE` and must be refused.
- Objects land under `backups/daily/` (35-day lifecycle expiry) and are server-side-copied to
  `backups/monthly/` on the first of each month (400 days).

### What it does not cover

- **RPO is up to 24 hours.** Dumps are nightly. Writes since the last successful dump are lost in a
  total-loss scenario. USGS and USDA data is re-fetchable from source; derived features are
  recomputable; `job_runs` history is not.
- **There is no WAL archiving, so point-in-time recovery is not available.** Recovery restores to
  the moment of a nightly dump, not to an arbitrary instant.
- **The backup bucket is single-region.** A region-wide S3 failure takes the backups with it.
- **RTO is untested.** The monthly job proves an archive *restores*; nobody has timed a full
  rebuild of the instance from scratch.
- **The instance role holds no S3 delete permission of any kind.** Retention is a bucket lifecycle
  rule, which S3 executes itself — so a compromised instance cannot erase the backups, and equally
  no job can clean up after itself.

---

## What is publicly reachable

The built React bundle and the read-only GET endpoints under `/api`.

**Unauthenticated, deliberately, on three independently checkable grounds:** no non-GET route is
declared, the database role is `SELECT`-only and *has been observed refusing a `DELETE`*, and no
response body carries a secret.

**This is defensible as a decision, not as an inheritance.** A future session adding a write
endpoint voids the premise rather than extending it.

### Rate limiting, and what it leaves uncovered

A per-client-IP limiter runs **in the application**, keyed on the proxy-set `X-Real-IP` — not on
`request.client.host` (which is Caddy's own container address, and would bucket the entire internet
into one client) and not on `X-Forwarded-For` (which the client writes, and could simply be
rotated). Two buckets: a general one across `/api`, and a tighter one on `/api/conclusion`, where
distinct `(site_id, as_of)` pairs miss the conclusion cache and each run an analog query.

**Residual exposure, accepted and not mitigated: the static bundle, CSS and fonts are served by
Caddy and are not rate limited at all.** Caddy has no rate limiter in core, and the third-party
plugin needs an `xcaddy` build that would bring a self-built image inside the digest-pinning
contract. That trade was declined; it is recorded here and in `CLAUDE.md § 22` so it stays a
decision rather than becoming an omission.

`/api/health` is exempt from limiting by exact path match, so the external monitor can never be
throttled into a false alarm.

---

## Monitoring

`/api/health` reports **per-job** liveness and **per-table** data freshness, and returns **200 even
when degraded**. The 200 is deliberate: an uptime monitor that goes red on a stale ingest job is
indistinguishable from one that goes red because the API is down, and those need different
responses at different hours.

A job's `overdue` verdict and its table's `stale` verdict are independent measurements and are
never collapsed into one status — they legitimately disagree, and the disagreement is information.

An external Route53 health check string-matches **`"degraded":false`** in the response body, not the
status code, and a CloudWatch alarm on it notifies an SNS topic. `insufficient_data_actions` fires
the same topic, because an alarm stuck in INSUFFICIENT_DATA is indistinguishable from a healthy one
on a dashboard.

---

## Running it

```
pip install -r requirements.txt -r requirements-dev.txt
pytest                                  # unit tier
DATABASE_URL=postgresql://... pytest    # adds the integration tier
python -m verify.preflight              # the gates, on the instance
```

### Running one job by hand

```
python3 -m app.orchestration.run_once --list             # the runnable job names
python3 -m app.orchestration.run_once backup_nightly
python3 -m app.orchestration.run_once restore_test_monthly
```

Exit codes: `0` succeeded, `1` the job failed (recorded in `job_runs`; nothing is retried), `2`
usage — an unknown name prints the valid ones rather than a traceback.

It runs the job through the same `@job` decorator and the same registry the scheduler uses, so the
run appears in `job_runs` like any other. **It does not start the scheduler**, which is what keeps
a one-off backup from also firing every other job that happens to be due.

**On a freshly rebuilt instance, start the scheduler once before the first backup.** The backup
asserts that `apscheduler_jobs` exists before dumping (it excludes that table's data), and that
table is created by APScheduler's own DDL on the scheduler's first start — not by a migration. The
error otherwise names an excluded table and says nothing about startup ordering.

The integration tier needs a **throwaway** database — it drops this project's tables between tests.
Without `DATABASE_URL` those tests skip with a stated reason rather than passing silently. Some of
them also need Docker, and skip loudly without it.

`terraform apply`, `terraform init -migrate-state`, migrations, and anything that deletes data are
human-run by contract, never by an agent.
