# CONTEXT.md — current state

This is the **log**: where the project is now, what is open, and `§ Up Next`. Stable contracts live
in `CLAUDE.md`, which outranks this file.

**Last updated:** 2026-08-17.

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
gate 1 wrote only the first compose `image:` line at the time. **That gap is closed in this
commit** — gate 1 now enumerates every `image:` line and every Dockerfile `FROM`, **six references
across three files**, and `--write-digest` rewrites all of them.

**What is publicly reachable:** the built React bundle (four views) and all eight Phase 8 GET
endpoints. **Unauthenticated, deliberately, and defensible on three independently checkable
grounds** — no non-GET route is declared, the database role is `SELECT`-only and *has been observed
refusing a `DELETE`*, and no response body carries a secret. **It is defensible as a decision and
not as an inheritance**: a future session adding a write endpoint is voiding its premise, not
extending it (`CLAUDE.md § 22`, and `docs/decisions.md § Phase 10`).

**The one live, unmitigated exposure is request volume.** No per-IP rate limit shipped. Distinct
`(site_id, as_of)` pairs bypass the conclusion cache and each one runs an analog query. Phase 11
owns it and it is the first item below.

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

## § Up Next

**PHASE 11.** Backups, restore tests, S3 for Terraform state, external uptime monitoring, and the
per-IP rate limit Phase 10 did not ship.

1. **THE PER-IP RATE LIMIT. First, because the exposure is live.** Caddy has none in core; the
   plugin needs a custom `xcaddy` build with a version pinned from a catalog somebody has actually
   read, never from recollection (`CLAUDE.md § 16`). What shipped instead is a 16KB body cap and
   three proxy timeouts, which bound one slow request and do nothing about volume. A CDN in front
   (Cloudflare proxying, currently DNS-only) is the alternative and carries its own issuance
   change — see `docs/decisions.md § Phase 10 live run`.
2. **Backups, and `pg_restore -f /dev/null` as the verification.** `pg_dump --list` is not
   verification (`CLAUDE.md § 3`). **`apscheduler_jobs` must be excluded from every dump** —
   restoring stale `next_run_time` values is worse than restoring none.
3. **A restore test that actually restores**, followed by `ANALYZE`, which is part of restoring and
   is neither a migration nor a scheduled job.
4. ~~**S3 backend with locking for Terraform state.**~~ **WRITTEN, PENDING MIGRATION.** HCL landed
   in Part 2 below; `terraform init -migrate-state` is a human step and has not been run yet.
   Until it is, state is still local and this item is not closed.
5. **External uptime monitoring**, alerting on the `degraded` field rather than on the status code —
   `/api/health` returns 200 while degraded by design.
6. **Confirm the AWS budget alert exists.** Status unknown. There is a running instance, an EIP and
   an EBS volume billing continuously.

**Phase 12** containerizes the `worker` service. It closes `degraded: true`, and it **needs its own
restart-recovery verification** — being inside a container with `restart: unless-stopped` changes
the process lifetime this whole design is about, and this project has already demonstrated that the
settings can all be correct while the behaviour is not.

---

## Phase 11 — backups, restore verification, monitoring, rate limiting

In progress. One entry per part, with the commit SHA and what was measured rather than intended.

### Part 1 — gate 1's four remaining conditions (`<sha-part-1>`)

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

### Part 2 — Terraform remote state with locking (`<sha-part-2>`)

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

### Part 3 — backup bucket, scoped IAM, external health check, alarm, budget (`<sha-part-3>`)

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
