# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-14 (Phase 3 close-out: measured coverage, corrected seeds, known gaps;
live steps outstanding)

---

## Current state

**PHASE 3 CLOSE-OUT (MEASURED COVERAGE, CORRECTED SEEDS, KNOWN GAPS) IS WRITTEN AND VERIFIED
OFFLINE, as of 2026-08-14. The two compression ratios are STILL UNMEASURED and are still the
outstanding deliverable of Phase 3.**

- **194 tests green with zero skips** against a throwaway local TimescaleDB container on the
  pinned image; offline the same suite is `137 passed, 57 skipped`. Phase 3.5's baseline was
  183/zero-skip.
- **All 8 mutation-table rows confirmed** — each performed, watched red on its own assertion, then
  restored, with the files diffed byte-for-byte against their pre-mutation copies afterwards.
- **Migrations `0011` and `0012` are new; nothing in `0001`–`0010` was edited.**

### The measurement that made this commit necessary, 2026-08-14

The `dv_record_start` values seeded in `0008` came from **single-month January probes generalized
into a period of record**. That method measures presence in one window, not depth, and **it was
wrong for three of the four sites.** The `CONTEXT.md` entry it produced described a corridor depth
that the data does not have.

The correct instrument is a **single full-range request per site, counting values per year**. Run
against `00060`, `statCd=00003`, requested 1990-01-01 to 2026-08-01:

| Site | Serves from | Coverage | Gap |
|---|---|---|---|
| 07010000 St. Louis | ≤1990-01-01 | 365/366 every year, unbroken to 2026 | none |
| 07032000 Memphis | 1990-01-01 | 365 in 1990–1993, 272 in 1994, then **nothing until 2014-10-01**, dense after | **1994-09 → 2014-10** |
| 07289000 Vicksburg | 2008-01-01 | dense, unbroken to 2026 | none |
| 07374000 Baton Rouge | 2004-03-17 | dense except 2023 | **2023-01 → 2023-08-15** (3 days in Jan, resumes 2023-08-15) |

St. Louis's `1990-01-01` is a **bound, not a discovered start**: the request floor was 1990 and the
site answered from its first day, so its real record begins earlier. It is recorded as a bound.

**The catalog is not the seed source either.** `seriesCatalogOutput` reports Memphis `00060/00003`
as 1933-01-01 → 2026-08-12 with 26,886 values. The DV endpoint **will not serve** anything between
1994-09 and 2014-10 however the request is framed — `statCd=00003` stated explicitly, different
window sizes, and `format=rdb` were all tried. **A catalog reports an envelope and a count; it
does not tell you what the endpoint returns.** Seed from what the endpoint serves. This is now a
contract line in `CLAUDE.md § 15`, because the method was the error, not the numbers.

### What this means for the project, stated plainly

- **Both labelled events are covered at all four sites.** The 2023 Baton Rouge gap ends
  **2023-08-15**, before the low-water period, so September–December 2023 is complete there.
- **2010–2026 is dense at all four sites** — roughly **sixteen years** of four-site coverage.
- **Pre-2004 baselines run on St. Louis alone.** Memphis's early segment ends in 1994 and the
  other two had not begun. This corrects the Phase 3.5 claim that pre-2010 history ran on "St.
  Louis and Memphis alone" — Memphis has nothing between 1994 and 2014.
- **The honest framing is one deep site, three shallow, and a corridor-wide window of about
  sixteen years.** The README must not imply eighteen clean years across the corridor.

### Decisions worth reading before changing anything here

- **Memphis is seeded at `2014-10-01` and the 1990–1994 segment is DELIBERATELY ABANDONED**, even
  though the endpoint will serve it. Collecting it means walking twenty years of empty windows on
  every backfill to obtain four years disconnected from everything after them, and **a
  discontinuous series is worse than a shorter continuous one for seasonal adjustment** — a model
  fitted across a twenty-year hole learns the discontinuity, not the season. *"Memphis has data
  back to 1990, why does the seed say 2014"* is the question to expect, and the reasoning is
  written into `0011` where it will be met.
- **`iv_record_start` is NULL at Memphis, Vicksburg and Baton Rouge.** They serve instantaneous
  values on a rolling window of roughly two months, and **a rolling window is not a start date** —
  any value in that column is a claim that is false within weeks, with nothing about reading it to
  say it has expired. NULL means "rolling retention; no fixed start", the column carries a `CHECK`
  permitting NULL and rejecting a sentinel date, and St. Louis keeps `2007-10-01`, which is real.
  The instantaneous backfill now **refuses those three sites by name in `resume_point`** rather
  than crashing on a NULL — it already aborted at their first window on a missing series, and this
  moves that abort earlier and points it at the right thing.
- **Known gaps are rows in `gauge_known_gaps`, not a comment.** A comment cannot be queried by the
  thing that needs it: the backfill consults the table to decide whether an empty window is
  expected, and Phase 5's features will need it to avoid interpolating across a twenty-year hole.
  Boundaries are **inclusive of the first and last missing day**, stated in a column comment.
- **The backfill reports an empty window as expected or unexplained, and NEITHER IS FATAL.**
  Inside a known gap → INFO; outside every one → WARNING. An empty window has never been fatal and
  must not become fatal now; the fatal case is a missing *series* and it is unchanged, in the
  client. **There is deliberately no "skip ahead to the end of a known gap" optimization** — that
  would let a human-maintained table decide what never to ask for, where a wrong row silently
  skips real data leaving no request, no empty response, and no evidence. A test asserts every
  window inside a gap is still requested.
- **The correction is additive; `0008`'s values were not edited.** The checksum guard would refuse
  the edit, but that is not the reason: a migration records what was believed when it was written,
  and the correction is itself a fact worth having in the sequence.

### Files touched outside the brief's list, and why

- **`app/ingest/backfill.py`** (the instantaneous backfill) — a NULL `iv_record_start` reached
  `resume_point`, which would have raised `AttributeError` on `None.isoformat()`. Left alone, this
  commit would have converted a clean abort into a confusing crash. It now refuses with a message
  naming rolling retention and pointing at the daily backfill.
- **`tests/ingest/test_gauge_seed.py`** — its `iv_record_start is not None` assertion was the old
  claim. Now asserted per site: NULL at exactly the three rolling-retention sites, non-NULL at St.
  Louis. The offline mirror of integration test 9.
- **`tests/ingest/test_backfill_chunking.py`** — `test_a_site_with_no_rows_starts_at_its_own_iv_
  record_start` contrasted Vicksburg's start against St. Louis's to show the start was per site.
  Vicksburg no longer has one, so the test now asserts the shape the data actually takes: St.
  Louis walks from its own start, and each of the three rolling sites is refused before a single
  request is made.
- **`app/ingest/gauges.py`'s seed parser reads the WHOLE migration sequence now**, applying every
  `UPDATE gauges SET <iv|dv>_record_start` in file order, last write wins. Reading only the
  migration that introduced a column is what makes an offline guard go stale — it would still
  report Memphis's floor as 1990-01-01, with the same confidence as a right answer. The same
  change made the INSERT scanner quote-aware, because `0012`'s note text contains a semicolon and
  the old regex terminated the statement in the middle of it.

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

### Why this commit exists — measured against the live API, 2026-08-14

Phase 3 assumed the instantaneous-values service carried the full period of record. **It does
not.** Every line below was measured, not inferred.

| Site | DV 1990 | DV 2000 | DV 2007 | DV 2010+ | IV depth |
|---|---|---|---|---|---|
| 07010000 St. Louis | yes | yes | yes | yes | deep — 223,706 rows loaded from 2007-10-01 |
| 07032000 Memphis | yes | yes | yes | yes | **rolling ~2 months** |
| 07289000 Vicksburg | no | no | no | yes (≤2010) | rolling |
| 07374000 Baton Rouge | no | no | yes | yes | rolling (depth untested) |

1. **Instantaneous retention is a rolling window at three of four sites.** Memphis returned
   nothing at 2025-01, 2025-06 or 2026-01 and data at 2026-06, with the current date 2026-08-14.
2. **The daily endpoint carries the depth.** Memphis and Vicksburg both return a complete
   122-value series for 2022-09-01 to 2022-12-31 — the low-water event, fully covered.
3. **A request entirely outside a site's period of record returns a NON-JSON body**, not an empty
   JSON envelope. That is a third response outcome, distinct from both of Phase 3's two.
4. **Daily timestamps carry no UTC offset** (`2022-10-01T00:00:00.000`) where instantaneous ones
   do (`2026-08-01T00:00:00.000-05:00`).
5. **Daily discharge is `00060` with `stat_cd` `00003` ("Mean")**, delivered under
   `variable.options.option`.
6. **Record starts are per site AND per endpoint**, and the boundaries above are one-month January
   probes — **brackets, not exact dates**. Vicksburg's daily record begins somewhere in 2008–2010;
   Baton Rouge's somewhere in 2005–2006.

**THE TABLE AND FINDING 6 ABOVE ARE SUPERSEDED — the probe method was the error.** A "yes" in the
DV columns means the site answered that one January, which says nothing about the years between.
Memphis reads "yes" at 2000 and 2010 and serves **nothing at all** between 1994 and 2014. The
measured coverage is the table at the top of `§ Current state`; this one is kept as the record of
what Phase 3.5 believed and of how it went wrong.

### THE CONSEQUENCE FOR THE PROJECT, stated plainly

**Every site covers 2010 onward at daily resolution, so the 2022 and 2023 labelled low-water
events are covered at all four gauges** — the natural experiments this project validates against
are intact.

**But the corridor has UNEVEN HISTORICAL DEPTH.** Two sites carry 35+ years; two do not. **Any
baseline needing pre-2010 history effectively runs on St. Louis and Memphis alone.** That is a
real constraint on the ten-year seasonal medians and on the analog search, and it is a fact about
the data rather than a defect to fix — no amount of engineering creates a Vicksburg reading for
1995. It must be stated wherever a "10-year seasonal median" is claimed.

**The Phase 3 backfill aborting at Memphis was NOT a defect.** It was `CLAUDE.md § 14`'s decision-1
guard working exactly as designed: a 200 carrying `"timeSeries": []` was refused rather than
written as zero rows. That refusal is what surfaced the rolling-retention finding at all. Had the
client iterated whatever arrived, Memphis would have silently ingested nothing and reported
success.

### What Phase 3.5 built

- `0007` renames `gauge_readings` → `gauge_readings_iv` (plus its indexes, constraints, and
  `gauges.record_start` → `iv_record_start`). Pure renames; no data moved.
- `0008` creates `gauge_readings_daily` — hypertable on `date`, 365-day chunks, primary key
  `(usgs_site_id, date, param_code, stat_cd)` — and adds `gauges.dv_record_start`.
  `0009` compresses it (segment by site/param/stat, after **1 year**, against the instantaneous
  table's 30 days).
- `0010` creates `gauge_series`, the one place the precedence rule lives.
- `app/ingest/usgs_daily_client.py`, `usgs_daily_ingest.py`, `daily_backfill.py`.

### Decisions worth reading before changing anything here

- **Daily lands in its OWN TABLE, not a `source` column on a shared one.** The shared-table version
  was considered and turned down: a daily mean stamped at midnight and an instantaneous reading at
  14:45 are different kinds of measurement, and with a discriminator column the obvious query
  returns a silent mixture that double-counts every overlapping day. Separate tables make that
  mistake impossible rather than merely discouraged. **"Consolidate these two tables" is the
  tidying to expect, and the reasoning is written into `0008` where it will be met.**
- **`stat_cd` is in the key even though only `00003` arrives today.** This project has a specific
  future interest in the daily **minimum** — the constraint that binds a tow is the low point of
  the day, not the average of it. Adding it to a primary key later means rebuilding the table.
- **`gauge_series` prefers instantaneous where it exists, and exposes `source`.** The two are NOT
  identical measurements: USGS computes its daily mean over a calendar day in the site's LOCAL
  time while the view buckets instantaneous data by UTC date, and the sampling differs. **A series
  that switches source mid-history has a seam**, and `source` is what keeps it visible rather than
  hidden. The day-boundary discrepancy is a known, stated cost — not a rounding error.
- **The backfill NEVER writes to `gauges`.** It reports the first date that actually returned data
  per site and stops. Auto-correcting `dv_record_start` would destroy the only evidence the seed
  was ever wrong.

### Mutation notes — three rows needed a second pass

Recorded because "it went red" is not the claim; "it went red for the reason the test exists" is.

- **"Parse DV timestamps with the IV UTC converter"** first went red because the instantaneous
  converter *refuses* naive input — a good property, but not the silent date shift the test is
  for. Re-run with plain `datetime.fromisoformat(raw).astimezone(utc).date()`, which has no such
  guard: under `TZ=Asia/Tokyo` the fixture's 2022-10-01 parsed as **2022-09-30**. That is the bug.
- **"Make the view prefer DV over IV"** first went red for the wrong reason — a harness bug applied
  two edits to one file against the pristine text, so the second silently discarded the first and
  the mutation behaved like a different row. Fixed and re-run; it then failed on
  `source is 'dv'`, which is the assertion that matters.
- **"Collapse missing-triple into the empty-values path"** cannot turn both named tests red with a
  single edit — the two directions of the collapse are different edits. Both were run: the lenient
  direction (a missing triple silently returning empty) turns test 2 red, and the strict direction
  (a present-but-empty series counted as absent) turns test 3 red.

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
  `0011` and in the table at the top of this file. St. Louis's 1990-01-01 remains a **bound** —
  its record predates the request floor, and reaching further back is a human's decision.
- **How should a rolling-retention endpoint be modelled?** *Partly answered.* The column is now
  NULL for the three sites, which is the honest value, and the instantaneous backfill refuses
  them by name. **What remains open is whether the IV backfill applies to those sites at all** —
  the likely answer is that it does not, and the incremental poll is the only path to their
  instantaneous data. **First candidate for the next ingest commit.**
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

**THE PHASE 3 CLOSE-OUT LIVE VERIFICATION IS THE NEXT THING TO DO, and it supersedes Phase 3.5's
steps 1, 3, 5 and 6.** Phase 3.5's step 2 (read the rename back from the catalog) and step 7 (the
view seam spot-check) are still worth running and are not repeated here. Run this in order:

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

**Then Phase 4.** The freshness-registry requirement in `CLAUDE.md § 12` now binds for every
subsequent ingest client, and `CLAUDE.md § 14` is the contract each one is written against.

---

## Housekeeping — open, non-blocking

- **THE INSTANTANEOUS BACKFILL RUNS FOR ST. LOUIS ONLY, and the other three now say so in the
  data.** `iv_record_start` is NULL at Memphis, Vicksburg and Baton Rouge (migration `0011`) —
  "rolling window" is not a date, so the honest column value is empty rather than a date that
  expires. `app.ingest.backfill` refuses those sites in `resume_point` with a message naming
  rolling retention; it previously aborted at their first window on a missing series, which was
  also correct, and this only moves the abort earlier. **Still open, and still a human's call:
  whether the IV backfill applies to rolling-retention sites at all.** The likely answer is that
  it does not and the incremental poll is the only path. First candidate for the next ingest
  commit.
- **Both compression ratios are unmeasured and no number is written anywhere.** Live verification
  step 7 of the close-out list. Nothing in the repo, the README, or the résumé may quote a ratio
  until it is taken. **Expect the daily one to be unimpressive** — the corrected seeds put the
  daily table at roughly 60k rows, where TimescaleDB is an engineering measurement on a real
  series rather than a storage necessity. Report it anyway; the measurement wins (`CLAUDE.md § 0`).
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