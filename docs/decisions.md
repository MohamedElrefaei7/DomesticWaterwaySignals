# Decisions

Decisions taken in this project and the reason each was taken, **including the alternatives that
were rejected and why**. Split out of `CONTEXT.md` on 2026-08-17.

**What belongs here and what does not.** A decision is a choice this project made that a reader
could reasonably make differently — a rejected alternative, a deliberate deviation from a brief, a
value seeded rather than measured. An *invariant* is not a decision: it lives in `CLAUDE.md`, which
outranks this file. A *measurement* is not a decision: it lives in `findings.md`.

**The rejected alternatives are the point.** Most sections below are headed "Decisions worth
reading before changing anything here", and they exist because the obvious implementation is
usually the wrong one — a shared table with a discriminator column, a `.get()` instead of an
explicit three-state read, a truncate-and-rebuild, a similarity cutoff. Reading only the code shows
what was built; these say what was turned down.

## Contents

- [Project-level](#project-level)
- [Phase 3 — USGS instantaneous ingest](#phase-3--usgs-instantaneous-ingest)
- [Phase 3.5 — USGS daily values](#phase-35--usgs-daily-values)
- [Phase 3 close-out](#phase-3-close-out)
- [Phase 4 — USDA ingest](#phase-4--usda-ingest)
- [Phase 5 — the normalizer and feature layer](#phase-5--the-normalizer-and-feature-layer)
- [Phase 6 — the ±lag sweep](#phase-6--the-lag-sweep)
- [Phase 7 — the analog engine](#phase-7--the-analog-engine)
- [Phase 8 — the read API](#phase-8--the-read-api)
- [Phase 9 — the frontend](#phase-9--the-frontend)
- [Phase 10 — deployment](#phase-10--deployment)


---

## Project-level

### Key decisions

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

## Phase 3 — USGS instantaneous ingest

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


---

## Phase 3.5 — USGS daily values

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


---

## Phase 3 close-out

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

---

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


---

## Phase 4 — USDA ingest

### Decisions worth reading before changing anything here

- **`0014` and `0015` were EMPTY when altered.** No ingest had ever run — there was no resolved
  dataset id to run one against. So `0016`'s renames and drops are ordinary `ALTER`s, not `§ 3`
  archival operations, and **there is no archive table to go looking for.** Stated in the migration
  itself for the same reason.
- **`0013`–`0015` are not edited.** The checksum guard would refuse, but that is not the reason: a
  migration records what was believed when it was written. Same argument `0011` makes about `0008`.
- **The `barge_rates` dataset key was RENAMED to `barge_rates_nearby` by `UPDATE`, not replaced.**
  This project does not delete rows (`§ 1`), and the nearby dataset is the direct successor of what
  the old key meant.
- **`source_row_count` is stored, and it will go stale.** That is fine and is the point: these
  datasets only grow, so the seeded count is a **floor**, and a backfill landing fewer records than
  the floor truncated. The backfill compares **records received** rather than rows written, because
  a correct rerun writes nothing.
- **`cost_indicators` has a real id and NULL bounds.** It was found in the same catalog query and
  nothing has counted it; seeding an unmeasured bound is the Phase 3 failure repeated (`§ 15`). It
  stays out of the ingest path — its id being resolved is not a promise that it is loaded.
- **The column stays `week_ending` although the source field is `date`.** A deliberate divergence:
  `week_ending` says what the value means where `date` says only what type it is. Recorded in the
  field map's comment and guarded by a test, because "rename the column to match the source" is an
  otherwise reasonable-looking tidy.
- **The three rates datasets are ONE `@job` (`usda_rates_ingest`), not three.** One publication, one
  schedule, one table, one meaningful `rows_written`. That is one scheduled unit, so it does not
  violate `§ 4` — the rule's target is two independent SOURCES sharing a status, which is why
  movements remains its own job.
- **`date` is also a SoQL type name.** If the service rejects it as a bare identifier in `$order` or
  `$where`, the rejection arrives as an error document and raises `SocrataResponseError` carrying
  Socrata's own message — loudly, never as an empty page. The fix would be to quote it in the two
  places it is built. Left unquoted because this project does not guess syntax it has not measured.

---

### Files touched outside the brief's list, and why

- **`tests/ingest/test_socrata_client.py`** — held `test_usda_datasets_seed_has_three_keys_with_null_ids`,
  which `0016` makes false. Replaced by test 11 (five keys, real ids, exact set equality on the
  ids and the counts). The unresolved-id path is still exercised, now against a row the test
  inserts rather than against the seed, because no seeded row is unresolved any more. Its fixture
  reference moved from `rates_ok` to `rates_nearby`. `app/ingest/socrata_client.py` itself is
  untouched, as the brief required.
- **`app/ingest/usda_backfill.py` gained a test** (`test_the_backfill_reads_three_rates_datasets_and_checks_itself_against_the_seed`).
  Live verification step 3 runs this code and nothing exercised it: the three-dataset wiring, the
  horizon-from-key, and the truncation comparison are all new in this commit, and shipping them
  unexercised would mean finding a typo by hand on the instance.
- **`seeded_row_count` is read by a small query in `usda_backfill.py`** rather than added to
  `socrata_client.Dataset`, because the brief fenced that file. The count is the CLI's business
  only — nothing on the request path consults it — so this is not a worse home for it, but it is a
  second reader of `usda_datasets` and worth knowing about.

---

### Decisions worth reading before changing anything here

- **Absent, null-valued, and unparseable are THREE conditions** (`socrata_client.optional_field`).
  An absent key and an explicit null both mean "no rate published" and become NULL; a value that
  will not parse **raises, naming the value**. The tempting one-liner — `record.get("rate")` —
  collapses the third into the first, so a corrupt value becomes a winter closure. That is a
  completely ordinary thing for this column to say, 774 rows already say it, and the 775th would
  hide in exactly that camouflage.
- **`required_field` is NARROWED, not weakened.** `date` and `location` still go through it: they
  key the row, and a record missing one is not a closure week, it is a record nothing can ever
  correct or supersede. Its message used to advise against ever making a field optional — written
  when the *field names* were wrong — and now says which case is which, because that advice would
  have been wrong here.
- **A NULL rate is never coalesced to 0.** Zero claims barge freight was free that week, in a
  column every average, seasonal median, and analog comparison reads.
- **Freshness counts rows, not rates.** `newest_row` takes `MAX(week_ending)` over all rows, and
  the reason is now written into the registry entry: in January the upper segments are shut, so
  "only count rows that have data" would report the table stale all winter while ingest was
  perfectly correct — and an alarm that fires all winter gets muted before spring.
- **The completeness report is visibility, NOT enforcement.** The backfill prints rows landed, rows
  with no published rate, and the percentage, per location. No constraint and no alert: the rate is
  legitimately absent 9% of the time overall and 36% at Twin Cities, so any threshold would fire
  constantly or be loosened until it never fired.
- **The table was still empty when altered.** `0016`'s backfill attempt aborted before writing a
  row, so `0017`'s ALTERs move no data — and, as with `0016`, **there is no archive table to go
  looking for.**
- **`barge_rates_pct_positive` was restated as `pct_of_tariff IS NULL OR pct_of_tariff > 0`,
  changing no behaviour.** A CHECK already passes when its expression is NULL; the old form merely
  *read* as a rejection of NULL, and the next constraint written beside it would have copied
  whichever form was there.

---

### One file deviates from the brief's list

`tests/ingest/fixtures/socrata_rates_ok.json` no longer exists — the previous commit split it into
`socrata_rates_nearby.json`, `_1month`, and `_3month`. The rate-absent record was added to
**`socrata_rates_nearby.json`**, and a test now asserts the fixture carries exactly one, so a
fixture that drifted back to an all-rates page could not quietly pass the parser tests that read it.

---

### Decisions worth reading before changing anything here

- **`tons` uses `optional_field`, not `record.get`.** Absent key and explicit null both become NULL;
  an unparseable value **raises, naming the value**. The blanket `.get` is one call shorter and would
  file a corrupt tonnage as a reporting gap — with 108 legitimate gaps on the summary locks to hide
  among.
- **A blank `tons` RAISES rather than becoming NULL, and this is the one condition here argued
  rather than measured.** USDA omits the key when it reports nothing and publishes an explicit `0`
  when nothing moved; a present-but-empty cell is a third spelling nobody has seen. Same call the
  rates module made, for the same camouflage reason. **If it fires on the live backfill, measure what
  those records look like before changing anything** — the run stops, which is the point.
- **The zero test became load-bearing and gained its inverse.** It was written against a
  hypothetical; it now guards a real 8,218-record population against a real 108-record one, and a
  parsed `0` is asserted to survive to the database **as `0` and not as NULL**. Only one direction
  was ever covered.
- **`0018` contains no `ALTER COLUMN … DROP NOT NULL`, and the absence is deliberate.** `0015`
  created `tons` nullable and `0016` never added a NOT NULL, so the ALTER would be a no-op that reads
  like a change. What was missing was the **meaning**, not the structure. The precondition is
  *verified* instead, by a `DO` block that hard-fails if the column has acquired a NOT NULL —
  confirmed to fire by setting one and watching it raise.
- **`lock_movements_tons_non_negative` is left alone.** `0015` already wrote it as
  `tons IS NULL OR tons >= 0`, with both cases spelled out, so `0017`'s rewrite of
  `barge_rates_pct_positive` has no counterpart here.
- **The completeness report prints THREE counts per lock — landed, reported zero, not reported.**
  Never one combined "no data" figure: at 31% zeros a combined number is nearly all zeros, reads as
  an alarming gap, and conceals the 108 rows that are the actual gap. Visibility, not enforcement.
- **`source_row_count` is compared against RECORDS RECEIVED, not rows written.** Restated because
  this is the commit where it first runs against a large dataset: a correct rerun writes zero rows,
  so comparing `rows_written` would report catastrophic truncation on every second run.

---

### Two files deviate from the brief's list

- **`tests/ingest/fixtures/socrata_movements.json` is NEW, not modified.** It did not exist; the
  movements records lived in `socrata_page_1.json`, which `test_socrata_client.py` also uses as its
  generic two-page paging fixture with `page_limit=3` and an exact `len(records) == 3` assertion.
  Adding records there would have broken a paging test for reasons having nothing to do with paging.
  The new file follows the rates fixtures' naming, `socrata_page_1.json` is untouched and still the
  paging fixture, and the movements parser tests now read the new one.
- **`app/ingest/usda_movements.py`'s ingest log line** now reports reported-zero and not-reported
  counts separately, for the same reason the completeness report does.


---

## Phase 5 — the normalizer and feature layer

### Decisions worth reading before changing anything here

- **`gauge_daily` stores mean AND minimum, and the minimum is the thesis-relevant one.** A barge's
  draft is bound by the shallowest point at the moment it transits; an adequate-looking daily mean
  can contain hours during which the channel bound. Both are stored and Phase 6 decides.
- **`n_observations` is what makes `value_min` honest, and it is NOT NULL.** A minimum over one
  observation *is* the mean, and instantaneous retention is a rolling window at three of four gauges
  — so most of history arrives as `dv` rows with `n_observations = 1`. Without the column, a feature
  reading `value_min` would draw conclusions about "the minimum" that are conclusions about the mean
  with a more alarming name.
- **The rollup reads `gauge_series` for the value and source, and `gauge_readings_iv` for dispersion
  only.** The view is already aggregated to a mean, so a rollup reading *only* it would produce
  `value_min = value_max = value_mean` on every row in the database. The precedence decision is still
  the view's alone, and the join cannot misattribute: a `dv` row exists only where no `iv` row does,
  by the view's own `NOT EXISTS`.
- **Climatology is the MEDIAN, smoothed over 15 days, NULL below 8 years.** The 2022 and 2023 events
  are in the history the baseline is fitted on — a mean lets them depress the baseline they are
  measured against, so each event partly erases its own signal. Eight years rather than five or ten:
  five leaves a median of five deciding what "normal October" means; ten would refuse Memphis's whole
  useful record, since its daily series starts 2014-10-01 and the interesting events are 2022–2023.
- **February 29 folds onto day 59.** Its own bucket would hold a quarter of every neighbour's
  observations, so it would be noisier by construction and would fail the eight-year guard for
  thirty-two years while February 28 passed comfortably. Folding also keeps March 1 on day 60 in both
  year types, which is the property the whole day-of-year comparison depends on.
- **Run lengths reset to NULL across a gap, and knowledge returns on the first day at or above the
  threshold** — no run can span such a day, so it is a definite zero regardless of history. That is
  why the unknown state is escaped by an ordinary observation rather than by a rule with edge cases.
- **THE THRESHOLDS ARE PERCENTILE STAND-INS AND ABSOLUTE OPERATIONAL THRESHOLDS ARE STILL A HUMAN
  DECISION AWAITING A SOURCE.** `CLAUDE.md § 1` puts "threshold values that define an event" on the
  never-delegate list. This commit builds the mechanism and seeds it with the 5th, 10th and 20th
  percentiles of each site's own record, because a percentile is a *property of the data* rather than
  a judgement about the river and is self-documenting in a way `LOW_WATER_CFS = 150000` is not. A
  test asserts no absolute level appears in the module. **When operational thresholds arrive with a
  source, they replace the seeds and that is its own commit.**
- **The feature-to-target join is a leakage guard.** Nearest-date matching would let a feature dated
  after a week-ending inform that week's target — one or two days of lookahead, in no schema, making
  the relationship look slightly better than it is. Note the test detail: **against a dense daily
  series both implementations agree**, so the guard has to be built on a sparser series or it is
  vacuous exactly where it matters.
- **Targets are forward log-returns at 7/14/21 days.** The 2022 move was +625% up and −86% back down
  as percent changes — the same move with magnitudes differing sevenfold. The forward week is looked
  up by **exact date**, never by position: `rates[i + 1]` reaches silently across a gap and records a
  fortnight's move under horizon 7.
- **No pandas.** The brief allowed dataframes or sequences; these are sequences. The arithmetic is
  medians, windows and run lengths over tens of thousands of daily rows, and adding pandas to a pinned
  runtime for that is a large permanent cost for convenience in about two hundred lines.
- **NOTHING BUILDS A MOVEMENTS FEATURE**, because of the sparsity finding recorded above: half of
  `MS Lock 15`'s rows are explicit zeros, and whether to aggregate across commodities before
  differencing has not been decided. The feature layer is discharge-only until it is.
- **`features_build` does not wait for the ingest jobs and nothing orders them.** If a source is
  stale the features are stale, and the freshness registry says so. Ordering logic here would be a
  DAG runner inside APScheduler — this phase's version of the streaming daemon `CLAUDE.md § 6`
  refuses. Grace **43,200s against an interval of 86,400s, confirmed by measurement.**

---

### Deviations from the brief, and why

- **`tests/features/test_rollup.py`'s tests 1, 2, 3 and 5 are INTEGRATION, not unit.** The rollup is
  SQL by decision 2 — it must read the view so the precedence rule has one implementation, and test 4
  pins that by reading the SQL text. Writing a parallel Python aggregation so the min/mean/max
  arithmetic could be unit-tested **would be the second implementation decision 2 exists to prevent**,
  reintroduced by the test suite. The hand-built day is inserted into a real database and the real
  SQL is run over it instead. Recorded in the suite's own docstring.
- **`tests/orchestration/test_heartbeat.py` was modified**, which the brief's file list did not
  mention. Its `test_freshness_registry_covers_every_ingest_table` asserts **exact set equality**, so
  registering `features` turned it red — the guard working as designed. Updated deliberately, with
  the reasoning for why a *derived* table belongs in that registry, and why `targets` and
  `gauge_daily` do not (one job, one transaction, one alert).
- **`app/orchestration/scheduler.py` was modified**, also not in the list: a cadence entry with no
  entry in `JOB_FUNCTIONS` makes `build_scheduler()` refuse to start, by design.
- **Migration `0020` uses `site_id`, not `usgs_site_id`**, following the brief's column list. It still
  carries the foreign key to `gauges`.

---

### 1b — `gauge_daily` and `gauge_series` are different things, and neither replaces the other

`gauge_daily` appears in the original handoff's schema sketch as a rollup table; Phase 5's `0019`
creates it. It is **not** a replacement for the Phase 3.5 view, and the distinction is worth stating
because the names invite the confusion:

| | What it answers |
|---|---|
| `gauge_series` (view, `0010`) | **Source precedence** per site-date-param: which of the two reading tables this day's value comes from, and it exposes `source` so the seam stays visible. |
| `gauge_daily` (table, `0019`) | **Derived daily statistics** — mean, min, max, observation count — computed *from* that view plus the sub-daily record. |

The rollup reads the view rather than the reading tables, so the precedence rule has exactly one
implementation (`CLAUDE.md § 15`). `gauge_daily` carries the view's `source` column through, so a
consumer sees the seam without re-joining.


---

## Phase 6 — the ±lag sweep

### The arithmetic this whole phase is arranged around

**5 features × 4 sites × 3 horizons × 43 lags × 3 regimes = 7,740 combinations.** At α = 0.05
roughly **387 clear the threshold on pure noise** — not through a bug, but by construction, on
random data, every time. Every decision below exists because of that number. This is the first phase
that **can be convincingly wrong**: an ingest defect produces a count that does not match its
source, and a sweep with no multiple-comparisons accounting produces a table of significant-looking
relationships that are correctly computed, individually reproducible, and mostly noise.

The contract is now `CLAUDE.md § 18`.

> **THE REALIZED GRID WAS 6,966, AS PREDICTED.** 7,740 less 387 apiece for `discharge_min` at
> Memphis and Vicksburg, the two sites Phase 5 finding 3 measured as fully degenerate. The skip was
> detected from the data rather than from a site list, and the arithmetic above held on the day.

---

### Decisions worth reading before changing anything here

- **`signals` holds a row for every combination scanned, including the nulls and the refusals.** The
  table *is* the multiple-comparisons record. `passes_gate` is computed and stored so consumers
  filter; the writer never selects. A refusal is a row with a `status`, never an omission.
- **No p-value without a q-value, enforced by a bidirectional CHECK**, and both `grid_size` and
  `n_tests_adjusted` on every row. They are different numbers — combinations *enumerated* versus
  p-values BH actually adjusted across — and a q-value is uninterpretable without the second.
- **Benjamini-Hochberg, not Bonferroni.** Adjacent lags of one feature are nearly the same test;
  Bonferroni on ~7,700 correlated tests leaves nothing surviving ever, which is theatre.
- **`n_effective = n / (horizon_days / 7)`, and the p-value comes from it.** The raw count would
  roughly halve every p-value at horizon 14. The correction is the crude non-overlapping-equivalent
  one and it errs toward *fewer* observations, which is stated in the module rather than glossed.
- **The regime split is computed from the feature series and `regimes.classify` takes exactly one
  argument.** There is no parameter a target could arrive through, and a test asserts the signature.
  Onset is the counter rising, recovery is falling-or-reset, **flat is neither** — the flat days are
  the largest population in a run-length series and carrying the previous direction through them
  would assign hundreds of days by an implementation detail.
- **Walk-forward gap = `horizon_days`, asserted at the data level over a *daily* fixture.** The
  splitter admits a training date `d` only where `d < test_start - gap`, so the last admitted row's
  forward window ends before the test window opens. Asserted by reconstructing the folds and hunting
  for a training date whose forward window contains a test date — on a weekly grid alone the
  boundary falls between observations and the guard would be vacuous.
- **Minimum 5 folds, else `insufficient_folds`.** Five because directional consistency is a fraction
  of folds and the gate wants ≥70%: with four folds the only achievable values are 0/25/50/75/100%,
  so the gate would be testing "3 of 4" while claiming to test 70%.
- **`Consistency` carries `fraction` and `folds` and has no constructor producing one alone.**
- **Lags −21…+21 in 1-day steps, and negative lags are first-class.** No CHECK restricts
  `lag_days` to non-negative. If the strongest rows sit at negative lags the project's claim changes
  from "the physical signal leads" to "the market prices the forecast", and the CLI says so in its
  own output.
- **The sweep exposes no best-pair accessor**, asserted by scanning the module's public surface.
  `--top` prints for a human and returns `None`; `_print_top_rows` is private on purpose.
- **`signal_runs` records parameters, `git_sha` and `git_dirty`, and refuses to run without a sha.**
  Not defaulted to `'unknown'`, which would look sha-shaped in every listing afterwards.
- **A CLI, not a scheduled job.** No cadence entry, no freshness entry. A scheduled sweep would
  accumulate runs nobody reads and would eventually be the thing that "found" a signal at 3am that
  nothing validated. `signals` going stale is not a system-health question.
- **`scipy` was NOT added.** The only non-elementary function needed is the t distribution's tail, a
  regularized incomplete beta — ~40 lines of continued fraction using `math.lgamma`, checked against
  published critical values at df 1, 2, 10, 30, 100 and 10⁷ to within 5·10⁻⁴. `requirements.txt`
  stays at three runtime packages.

---

### Deviations from the brief, and why

- **Tests 17, 18 and 19 are INTEGRATION, not unit.** "Every scanned pair is written" is a claim about
  what landed in the table, and the failure guarded against is a filter at *write* time — so the
  assertion has to be on the far side of the write. An in-memory version asserts that the code
  counted what the code counted and passes in both directions of the mutation. Recorded in the
  suite's own docstring.
- **`tests/signals/test_capture_queries.py` is a sixth test file the brief did not list.** Part 1's
  done-condition is "the script runs against a fixture database", which is a test whether or not it
  is written as one — and this project has had to come back and correct unasserted claims before.
- **Test 7 was widened to run a real sweep and read the table back.** The mutation it guards ("drop
  BH, store only raw p") leaves the CHECK constraint perfectly intact, so a test asserting only that
  the constraint exists stays green while every row carries an unadjusted number. The `sweepable`
  fixture moved to `conftest.py` so both suites can use it.
- **Two columns beyond the brief's list.** `signals.n_tests_adjusted` (BH's *m*, which differs from
  `grid_size` whenever a pair was unscannable — and a q-value is meaningless without it) and
  `signals.series_column` (`anomaly` or `value` — decided per (feature, site) from the data, so the
  seam stays visible rather than being inferred later). `signal_runs.git_dirty` is a separate column
  rather than a suffix on `git_sha`, so the sha stays something you can hand to `git show`.
- **`regime`, `status` and `series_column` carry CHECK constraints although `features.feature_name`
  deliberately does not** (migration 0020). The difference is stated in `0023`: those sets are
  *closed by definition* rather than open by design, and unlike feature names they have no registry
  tripwire — so a misspelled `'onsett'` would open a silent fourth regime that every `group by`
  would report as a category.

---

### WHAT THIS CHANGES FOR PHASE 7

`signals` holds exactly one row Phase 7's confidence gate could be pointed at; it is
contemporaneous, its sign runs against the thesis, and its q clears 0.05 by 0.0054. **Phase 7 reads
this table and does not re-run the sweep.** What the table says today is that there is nothing here
worth building a detector on.

**Build the analog engine anyway.** The ≥4-analog half of `CLAUDE.md § 7`'s gate has no counterpart
in Phase 6 and has to exist before anything can be refused for the right reason. But it will be
built against a null result, and it should be built to say **"insufficient history"** and mean it —
not to be tuned until this one row comes out the other side looking like a signal.


---

## Phase 7 — the analog engine

### The delegation boundary — what this commit built, and what it refuses to choose

`CLAUDE.md § 1` puts **analog-matching logic and confidence-gating logic** on the never-delegate
list. This commit builds the mechanism; **every value it is pointed at lives in
`app/analogs/parameters.py`** with a comment saying where it came from. **Four of those values are
`None`, and each `None` is a stated position rather than a gap:**

| Parameter | Seed | Where it came from |
|---|---|---|
| `MIN_ANALOGS` | **4** | `CLAUDE.md § 7`, verbatim |
| `MIN_DIRECTIONAL_CONSISTENCY` | **0.70** | `CLAUDE.md § 7`, verbatim |
| `ENTRY_FEATURE` | `days_below_p10` | the one pair Phase 6 found anything for — **a reason to point the engine here, not evidence it will find anything** |
| `ENTRY_RUN_LENGTH_DAYS` | **1** | **adds no second threshold on top of the percentile.** Any larger value is a new number with no source |
| `MIN_EVENT_SEPARATION_DAYS` | 90 | the brief's seed, roughly the timescale of the 2022 event. Not measured |
| `K_NEAREST` | 10 | the brief's seed |
| `OUTCOME_WINDOW_DAYS` | 21 | `CLAUDE.md § 7`'s "within 3 weeks", **fixed before any outcome was computed** |
| `CONDITION_LOOKBACK_DAYS` | 14 | `CLAUDE.md § 7`'s example. Describes the sentence, does not enter the metric |
| `SIMILARITY_WEIGHTS` | **None** | a fitted weighting is in-sample optimization wearing a metric's clothes |
| `SIMILARITY_CUTOFF` | **None** | **see below — this is the one the human owes an answer to** |
| `SEASON_MATCH_WINDOW_DAYS` | **None** | no seasonal restriction is applied, **so the sentence does not claim one** |

---

### NO SIMILARITY CUTOFF IS SET, AND THAT IS THE POINT

"How similar is similar enough" is a claim about the world that **nobody can make before looking at a
distribution of distances**, and `CLAUDE.md § 1` puts it on the human's side of the line. So the
engine returns the **k nearest whatever they cost**, reports every distance in `--explain`, and
stores all of them in `analog_matches` — and **step 2 of the live procedure is somebody reading those
numbers before proposing a cutoff.**

A cutoff would also be **the quietest way to make the gate pass**: drop the far analogs and what
remains agrees with itself, while the gate counts the filtered set and reports it as the history.

---

### STATED LIMITATION — THE ANALOG COUNT ASSUMES THE ANALOGS ARE INDEPENDENT, AND THEY ARE NOT

**`n_analogs` counts events. It does not discount them for sitting close together in time or for
sharing a cause.** `events.collapse` handles duplication *within* one low-water period; **nothing in
this phase handles correlation *between* periods**, and that gap is structural rather than an
oversight — see `CLAUDE.md § 19`.

**Four droughts in one decade are not four independent draws.** They can share a multi-year climate
regime, one channel configuration, or a single dredging programme, and the barge market's response to
the fourth is not independent of its response to the first — the same operators, the same fleet
positioning, in some cases the same contracts. **4 of 4 directionally consistent is the same number
whether the events span forty years or four, and the rendered sentence is identical either way.**

**This is not hypothetical at Memphis, and the arithmetic is already in this file.** Memphis's daily
record starts **2014-10-01** (`FINDING 4`), and Phase 6's one surviving row carried
**`n_effective = 616`** — about the span from that start to now. So **every analog this engine can
ever find at Memphis comes from a single twelve-year window**, and it is a window containing the
2022–2023 pair, which were consecutive years. A passing gate here is a claim built on events that are
close together by construction.

**The consequence, stated as a reading rule rather than as a correction:**

> **A passing gate whose analogs cluster in one multi-year period is WEAKER EVIDENCE than the same
> count spread across independent decades, and the output does not say so.** Read the dates before
> reading the consistency.

**Nothing is adjusted for this in code**, and that is deliberate under `CLAUDE.md § 1`: a discount for
temporal proximity is a modelling decision with a parameter in it, and inventing one here would put a
number nobody sourced underneath every confidence claim the project makes. What this commit does
instead is **report the dates** — `analog_matches` stores every `event_start`, `--explain` prints
them, and the reader makes the discount. If an adjustment is ever wanted it is its own commit, with
the unadjusted counts measured first so the change has a before.

> **MEASURED ON 2026-08-16, AND IT IS WORSE THAN THIS SECTION PREDICTED.** The prediction was that
> every analog would fall inside one twelve-year window. What the instance returned is tighter still:
> **every analog behind both passes falls inside 2015–2022**, a seven-year span, and the 2023 pass
> includes **2022-09-16 — the immediately preceding year** — as its rank-1 analog *and* the source of
> its `+270%` upper bound. See `PHASE 7 — RUN ON THE INSTANCE` at the top of this file. **The clustering
> is not a risk this site carries; it is the only condition this site has.**

---

### Decisions worth reading before changing anything here

- **An event is detected from observations up to and including the detection date, and `is_entry`
  takes exactly one positional parameter — the history.** The tempting definition ("a period that
  reached 20 days below") cannot be evaluated until the period is over, so every historical event
  defined that way is **defined using its own future**. Enforced by signature, in
  `app/signals/regimes.py`'s style, **and** by a behavioural test against a truncated series. Either
  alone passes vacuously.
- **Detections collapse into events, and both counts are stored.** A sustained low-water period
  produces a detection every day it continues — **the 2022 event alone would satisfy "≥ 4 analogs"
  several times over from one event**, in the exact form the gate cannot see. `n_raw_detections` and
  `n_collapsed_events` are both columns on `analog_queries`; the gate consumes the second.
- **Detection is deliberately naive — the condition holds or it does not — so the collapse carries
  the whole anti-inflation argument visibly.** A crossing-only detector would collapse a sustained
  event for free and make the collapse rule a no-op that looked correct, with nothing to notice its
  removal.
- **The metric is unweighted Euclidean on z-scored features, and it is a placeholder chosen for being
  self-documenting rather than good.** Two known distortions are **recorded rather than corrected**,
  because correcting either is a weighting decision: `discharge_min` IS `discharge_mean` at Memphis
  and Vicksburg (Phase 5 finding 3), so that dimension is counted twice there; and `p05`/`p10`/`p20`
  are three thresholds on one series carrying three of the five dimensions.
- **The z-score population ends at `as_of`, like every other series the engine reads.** Standardizing
  against the full record would score a 2015 condition against a spread that includes 2022 — leakage
  arriving in a number nobody reads as a prediction.
- **`eligible_events` is the single place lookahead is prevented**, and it is handed **every** event
  including the one being asked about. An earlier draft pruned the current event before the call; that
  split the guard in two, after which removing half of it breaks nothing visible.
- **The outcome window is one int, and passing a sequence raises.** Three windows is three tests, the
  strongest of three is not a 21-day result, and **nothing here would record the two that were
  discarded**.
- **The gate runs before the estimate exists.** `outcomes.summarize` is called only on the passing
  branch, so **a refused query has nothing to withhold** — which is stronger than withholding, because
  a value that exists is one refactor away from being displayed. The test watches whether the function
  was *called*.
- **A refused query's returned structure carries no per-analog outcome, while `analog_matches` stores
  every one of them.** The table is the research log; the returned result is a claim. Collapsing the
  two would mean either fabricating a gap in the record or shipping an estimate the gate refused.
- **Migration 0024 enforces the gate's own arithmetic in the database.** A row claiming `passed` on
  three analogs, or on consistency below 0.70, is refused by a `CHECK` — so a script or a future
  module cannot write one either, not merely `gate.py`.
- **A refusal exits zero.** A non-zero exit would make "insufficient history" look like a failure to a
  shell, a cron, or the next person reading a log, and it is the deliverable.

---

### Deviations from the brief, and why

- **`analog_queries` carries two columns the brief's list did not name:** `n_raw_detections` and
  `n_collapsed_events`. Decision 2 requires both counts to be stored and the brief's column list
  included neither. A row where the first is in the hundreds and the second is 2 is the whole story of
  this dataset, and it is only readable if both are kept.
- **`git_dirty` was added beside `git_sha`**, for migration 0022's reason: a dirty run's results are
  worth keeping *and* are not reproducible, and those are two different facts about one row.
- **A fifth refusal reason, `no_current_event`, exists.** The brief names three. Asking about a river
  that is not in a low-water condition is not a coverage problem and must not read as one — step 6 of
  the live procedure is exactly that case.
- **`test_events.py`, `test_similarity.py`, `test_outcomes.py`, `test_gate.py` and `test_render.py`
  each carry two to four tests the brief did not number.** They cover the boundaries the numbered ones
  imply but do not reach: a NULL feature value not opening an event, deterministic tie-breaking (rank
  is in `analog_matches`'s primary key), a zero move not counting as directionally consistent, and the
  rendered sentence not claiming a seasonal filter that was never applied.
- **Tests 21, 22, 23 and 25 are integration, as the brief specifies; test 22 also has a unit
  counterpart** over `eligible_events`, because that is where the exclusion rule is legible as
  arithmetic rather than as an outcome.
- **`app/analogs/engine.py` imports `git_state` from `app/signals/sweep.py`** rather than
  reimplementing it. `CLAUDE.md § 17` forbids a second implementation of a rule that has one, and a
  parallel copy would be the one that quietly starts writing `'unknown'`.

---

### What to record, and what NOT to do with the answer

**Record:** the gate result for both labelled events including refusals; the observed distance
distribution; the collapsed event count per site; and — if the gate refuses everywhere — **that this
is the honest state of the project, with the reason**: one deep site, sixteen years of four-site
overlap, and a sweep that found one contemporaneous relationship out of 6,966.

**DO NOT**, on seeing a refusal:

- lower `MIN_ANALOGS` or `MIN_DIRECTIONAL_CONSISTENCY`. They are `CLAUDE.md § 7`'s numbers, they are
  not this phase's to move, and the moment to change them is never the moment a refusal disappoints.
- set a similarity cutoff to make the surviving analogs agree. That is the filtered-set failure
  `CLAUDE.md § 19` names, and it would leave the gate counting a subset while reporting it as the
  history.
- lower `ENTRY_RUN_LENGTH_DAYS` to manufacture more events. It admits shorter, thinner events —
  exactly the ones most likely to look similar to each other by chance.
- widen `OUTCOME_WINDOW_DAYS` until a move appears.

Each of those is a **human decision in its own commit**, with the current values' results measured
first so the change has a before — and this procedure's output is that before.

> **THE RUN PASSED, SO THE PRESSURE POINTS THE OTHER WAY — 2026-08-16.** Every item above guards
> against loosening the gate after a disappointing refusal. **What actually happened is a pass, and
> the corresponding failure is to quote it.** So, symmetrically:
>
> **DO NOT** put either sentence in a README, a UI or a résumé until the three questions at the top
> of this section are settled. **DO NOT** quote the 2023 `+270%` without saying it is one analog,
> from the immediately preceding year, which is also the rank-1 match. **DO NOT** describe either
> result as the system "working" — the gate passing is a fact about the gate, and the medians it
> passed on are **+7%** and **+10%** across ranges that span zero.
>
> `CLAUDE.md § 7` already requires every quoted number to be reproducible from a query. **These are.
> That is not the same as their being ready to quote.**


---

## Phase 8 — the read API

### READ-ONLY IS TWO PROPERTIES, AND THE SECOND ONE IS INVISIBLE FROM THE ROUTE TABLE

No non-GET route is declared, and a test walks the route tree to say so. **That test cannot see the
one that mattered.** `app/analogs/engine.query` defaults to `persist=True`: it INSERTs an
`analog_queries` row and COMMITS. Left at its default, every request to `/api/conclusion` would
write — on an endpoint declared GET, through a role the live procedure grants SELECT only.

So the route passes `persist=False`, and `test_the_conclusion_route_never_persists_a_query_row`
asserts it at the call site. **The consequence is stated rather than hidden, and it is worth the
human's attention:**

> **`analog_queries` is the CLI's research log and does NOT record questions asked through the
> API.** Phase 7 built that table specifically so an engine that refuses ninety-nine times in a
> hundred could not look like an engine that answers — the disappearing denominator, one layer up.
> A read-only API cannot contribute to it. **If the API becomes the main way queries are asked,
> that denominator stops being complete**, and the fix is a decision (a write path with its own
> role, or an accepted gap recorded here), not something this commit should have picked.

---

### Decisions worth reading before changing anything here

- **A refusal is a different SHAPE, not the same shape with nulls in it.** `RefusedConclusion` does
  not declare `median_pct`, `range_pct` or `matches` — the keys are absent from the body, and a
  client cannot default a key that does not exist. `no_current_event` is a third shape, distinct
  from `refused`, because a quiet river is not a coverage problem.
- **The sweep's verdict rides on all three shapes**, with `scanned_pairs` beside `passing_pairs`.
  `run_summary` is shared between the conclusion route and the signals route so the two cannot
  disagree about a run's denominator, and a test asserts they report the same numbers.
- **Response models declare no defaults at all**, not even `= None`. A nullable field is REQUIRED
  and nullable, so a route that failed to read the column fails loudly rather than emitting a
  plausible zero.
- **`/api/signals` defaults to every scanned row.** The scanned rows are the multiple-comparisons
  record; a default of `passing_only=true` would hand a client 1 row in a table of 1 rather than
  1 in 6,966, at read time, leaving no trace of itself.
- **The cache key is built FROM THE REQUEST**, not assembled by hand from named parameters. A
  hand-assembled key is one somebody forgets to extend, and the symptom is one date's conclusion
  served for another's — real, well-formed, identically shaped, detectable only by already knowing
  the answer. `/api/health` is never cached.
- **`computed_at` comes from the cache, not from the route's own clock.** Otherwise there would be
  two answers to "when was this computed" that agree on a miss and diverge on every hit, in the
  flattering direction — the body would always say "just now".
- **Error bodies carry a code and a correlation id and nothing else.** No exception text, no type
  name: `UndefinedTable` beside `InsufficientPrivilege` tells an unauthorized reader what schema
  they are probing and how far they have got.
- **`segment` and `location` are both accepted on `/api/rates`; the response always says
  `location`.** The column was renamed to `location` in migration 0016 after measuring what USDA
  calls it, and `segment` is the name the brief and the live procedure use. Both map to one column;
  passing both with different values is a 422 rather than a silent preference.

---

### Deviations from the brief, and why

- **Tests 11, 12 and 25 are integration though the brief marked only 13 and 26.** Tests 11
  (`last_success` ignores a more recent failure) and 12 (data freshness, not process liveness) are
  about a `WHERE status = 'success'` predicate and about a table being quiet while its job is
  healthy. The unit tier's `FakeConn` **deliberately does not implement that predicate** — a fake
  that did would make those tests assert what the test set up, which is the config-test-standing-
  where-a-behavioural-one-belongs failure this project has shipped ten of. Test 25 (every list
  response carries `limit`/`offset`/`total`) has both halves: a structural one over the response
  models, and an integration one over the four real endpoints.
- **`lock_movements` has no `direction` and no `barges`.** The brief's response shape implied both;
  migration 0016 dropped them after measuring that the source publishes neither, on the rule that a
  column which would always be NULL is not created. The API does not re-create them.
- **`barge_rates.rate_month` is exposed.** It is a published field on the two forward horizons and
  NULL on `nearby`; omitting it would have made a real published value unreachable.
- **`/api/gauges` reports declared record starts AND observed coverage.** `CLAUDE.md § 15`: a
  catalog's date range is an envelope, not what an endpoint serves, and where they disagree what is
  served is what is true. Reporting only the seeded value would restate an assumption as a
  measurement; reporting only the observed bounds would hide that a seeded assumption exists.
- **Nine tests beyond the brief's thirty.** The ones worth naming: the `persist=False` assertion
  above; a null and a zero tonnage in ONE response, because tests 19 and 20 each seed a single row
  and can each be passed by an implementation that emits one value for everything; that the cache
  actually hits, without which the cache-key test could pass over a cache that never fires; and
  that `app/api/` issues no writing SQL.

---

### What is NOT recorded as a test

**No test asserts `median_pct == 7.35`, `total == 62`, or that five particular rows are null.** Those
are a point-in-time observation on real, changing data — not invariants. The offline suite already
guards the *properties*: that a null survives serialization and a zero does too, each in its own
direction; that a refusal carries no estimate, asserted twice because a `null` key and a numeric key
are different failures; that `passing_pairs` never appears without `scanned_pairs`; that an
over-maximum span is rejected rather than clamped. **This section records one instance of those
properties holding. It does not encode the instance as a new assertion**, which would be a test that
goes red when the river changes.


---

## Phase 9 — the frontend

### TWO THINGS IN THE BRIEF COULD NOT BE BUILT AS WRITTEN, AND BOTH ARE RECORDED RATHER THAN WORKED AROUND

**1. `CONTEXT.md` forbids putting the passing sentence in a UI, and it outranks the phase brief.**
The block headed "THREE QUESTIONS COME BEFORE PHASE 9" says, in terms:

> **DO NOT** put either sentence in a README, a UI or a résumé until the three questions at the top
> of this section are settled.

All three are open and none of them is this agent's (`CLAUDE.md § 1`). `CLAUDE.md`'s precedence line
is **this file > `CONTEXT.md` > any handoff document**, and a phase brief is a handoff document, so
the log wins. **The passing view was built in full — sentence, span bar, sweep denominator adjacent
— inside a persistent, non-dismissible band naming the three unsettled decisions.** It is its own
component (`ProvisionalBand.tsx`) with one usage, so removing it after the questions are settled is
a visible one-line diff rather than an edit somebody makes while doing something else. **Human
decision, taken 2026-08-16.**

**2. The river map's data does not exist in the API, and the API is fixed for this commit.** All
eight endpoints were read. `Gauge` carries no percentile, no anomaly, no climatology year count and
**no coordinates**:

| Wanted | Why it is not there |
|---|---|
| Colour by percentile | No endpoint serves an anomaly or a baseline. `app/api/routes/__init__.py` says the API computes no climatology **by design**, and computing one in a component is a derived statistic (decision 9) — the exact number Phase 5's minimum-years guard exists to refuse. |
| Climatology year count per gauge | Same. Nothing serves it. |
| Geographic placement | No lat/lon on `Gauge`. This file already records that river mile and coordinates are **deliberately NULL rather than estimated**, with a test that goes red when they land. Hardcoding published USGS coordinates would put seed data in a second place where the database could not correct it (`CLAUDE.md § 1`). **MapLibre is therefore not used in this commit.** |
| Locks sized by throughput | `/api/movements` returns rows per commodity with a nullable `tons`. Summing them decides a NULL contributes zero — the coalesce `app/api/models.py` refuses, performed one layer up. |

**So `/river` is a coverage schematic rather than a map**: each gauge's declared record start beside
its observed coverage, which is `CLAUDE.md § 15`'s envelope-versus-served comparison and the one
measurement the Phase 8 procedure asked for and did not get. **Every gauge renders in the
no-baseline state and the legend says why**, rather than letting a colour ramp imply a shared
baseline across records that are 0, 4,335, 6,801 and 13,375 days deep.

**Tests 13 and 14 were adapted, and the adaptation is the honest half.** A test asserting a rendered
year count could only pass against a number the frontend invented. What they assert instead is the
property those tests exist to protect — the baseline is stated **per gauge and never once for the
page**, and a gauge without one renders as unmeasured rather than mid-scale — and both run against a
**mixture** of served and unserved baselines, so neither is vacuous the day an endpoint serves them.

---

### The design, as built

**Brand words: weathered, exacting, unflinching. The physical object is a USACE hydrographic survey
sheet** — buff stock, engraved rules, stencilled numerals — and the enamel gauge plate bolted to a
lock wall. **Light theme, warm buff ground**, derived from who reads it: a dispatcher at a desk in
daylight, reading something whose lineage is a printed government bulletin, not a dark ops console.

**Type: Archivo Narrow** (condensed grotesque, newspaper and forms tradition) against **Zilla Slab**
(mechanical slab, stamped-bulletin register) — contrast on two axes, serif/sans and wide/condensed,
tabular numerals throughout. **Self-hosted and pinned via `@fontsource`**, so the bundle makes no
request to a font CDN and Phase 10's CSP needs no exception for one.

**Palette is OKLCH and deliberately two-hue**, which is a departure from the usual tint-everything-
toward-one-brand-hue advice and is justified by the object: a printed chart *is* two hues. Neutrals
lean warm (hue 84–86, the paper), text and rules lean cold (hue 196, the ink). One accent —
`oklch(51% 0.168 39)`, a survey-stamp vermilion — reserved for **constraint, degraded and overdue**
and nothing else. There is no green "success" colour anywhere: nothing here succeeds, things are
either measured or they are not.

**Three signature elements, and the first one is the thesis rendered:**

1. **THE HATCH.** One visual vocabulary for *not measured*, used identically for a null rate in a
   chart, a gauge with no served baseline, and a sweep pair nobody scanned. Survey sheets hatch
   water nobody sounded. This project spends four layers keeping "not measured", "zero" and "no
   relationship" apart, and a mid-scale grey undoes all of it at the last inch — **a texture cannot
   be read as an average result.**
2. **THE SPAN BAR.** The range is the primary object, drawn against a zero axis; the median is a
   **tick on it**, rendered at a smaller size than the range label. When the range spans zero the
   interface says so in words rather than leaving it to be read off two signs.
3. **RULED BULLETIN BANDS, NOT CARDS.** 1px rules, asymmetric columns, no card-in-card.

**Refusal parity is structural.** All three gate shapes render through one `.verdict` band with one
set of type tokens, so a refusal cannot drift lighter without the shared rule changing for all
three — asserted, rather than intended.

---

### Decisions worth reading before changing anything here

- **The refusal types declare no estimate fields, so `result.median_pct` on a refusal is a COMPILE
  ERROR** rather than an `undefined` that renders blank. `tsc --noEmit` is test 18 for exactly this
  reason: left to a separate CI step it is a check that exists and does not run.
- **`connectNulls={false}` is set explicitly and asserted as a value, not trusted to the default.**
  Recharts already defaults to false; a test passing because of that would prove nothing about this
  codebase and would stay green when somebody set it true.
- **The sweep denominator is asserted to be inside the same container element as the median**, not
  merely somewhere in the document. A document-wide assertion passes over a layout that puts
  "1 of 6,966" in a footer, which is the dishonesty the test exists to prevent.
- **`/api/signals` is called with no `passing_only` parameter at all**, so the API's own default —
  every scanned row — is what the view gets. A client-side default of `true` would hand a reader 1
  row in a table of 1 rather than 1 in 6,966, at read time, leaving no trace of itself.
- **Layout arithmetic is permitted, marked, and enumerated by a test.** Two files position things
  (`SpanBar` places a tick, `Today` picks a calendar window); both carry a `layout-arithmetic:`
  marker and test 17b asserts the marked set is exactly those two, so an unmarked expression added
  later is visible in review. **This is the weakest of the eighteen guards** — a marker is a comment
  somebody could add while doing the wrong thing — and it is recorded as such rather than described
  as airtight.
- **`format.ts` contains no function that takes two numbers and returns a third.** A
  `percentChange(a, b)` there would be the gate bypass wearing a formatter's clothes, and test 17c
  greps for the vocabulary.


---

## Phase 10 — deployment

### 1. THE THREAT MODEL CHANGED, AND THIS IS THE DECISION ON THE RECORD

Through Phase 9 this system was reachable only by the human, through an SSM tunnel, from one IP.
**When the live procedure runs, it becomes reachable by anyone.**

**What becomes publicly reachable, exactly:**

| Reachable | What it serves |
|---|---|
| `https://bargeanalysis.com/` | The built React bundle — all four views, and `try_files` makes every client-side route return the shell |
| `https://bargeanalysis.com/api/*` | All eight Phase 8 GET endpoints: health, conclusion, series, gauges, rates, movements, signals, signal runs |
| `http://bargeanalysis.com/` | Nothing but a redirect to https — Caddy enables the redirect automatically (confirmed in the adapted config) |

**What does not:** Postgres on 5432 (the dev override that publishes it is out-of-repo and nothing
committed requires it), the scheduler (still a host venv process), the API's own port 8000, any
`.env` value, the schema runner, and every sweep and backfill CLI. `caddy` is the only service with
a `ports:` key.

**THE DECISION: everything stays read-only and public, with no authentication.** That is the
human's call and it is defensible on three grounds that are each independently checkable — the API
declares no non-GET route, the database role is `SELECT`-only and **has been observed refusing a
`DELETE` with `permission denied for table job_runs`** (Phase 8), and no response body carries a
secret.

**IT IS DEFENSIBLE AS A DECISION AND NOT AS AN INHERITANCE.** No-auth is predicated on no-writes.
A future session that adds a write endpoint, a triggerable backfill, or an admin route is not
extending this decision — it is voiding its premise, and the sentence it needs to find is this one.
`CLAUDE.md § 22`'s last bullet is the contract form.

**The exposure that is real and unmitigated: request volume.** `/api/conclusion` runs an analog
query. `app/api/cache.py` keys a cache on the full parameter set, so repeat requests for one
`(site_id, as_of)` are cheap — but **distinct pairs bypass the cache, and nothing limits how fast a
stranger can ask for them.** See section 3.

---

### 3. NO RATE LIMIT SHIPPED. STATED PLAINLY, BECAUSE THE ALTERNATIVE IS AN UNMARKED ABSENCE

**Caddy has no rate limiter in core.** The one everybody reaches for
(`github.com/mholt/caddy-ratelimit`) is a third-party plugin requiring a custom binary built with
`xcaddy`, which means a Go module fetch at image-build time and a plugin version pinned from
memory — the same class of mistake as inventing an AMI id (`CLAUDE.md § 16`). **Coupling the
first-ever TLS issuance to a supply-chain step was judged the worse trade**, and the brief
explicitly permitted stating an alternative rather than shipping one silently.

**What shipped instead:** `request_body max_size 16KB` — every route is a GET, so no legitimate
request carries a body at all — and three proxy timeouts (`dial_timeout 5s`,
`response_header_timeout 30s`, `read_timeout 60s`). **They bound what one slow or hung request can
hold open. They do nothing about volume.**

Two places say so where somebody will actually find it: a `NO PER-IP RATE LIMIT SHIPPED` block in
the Caddyfile at the point of use, and the test's own name —
`test_the_api_path_carries_the_documented_edge_limits`, renamed from the brief's
`test_a_rate_limit_applies_to_the_api_path`, **because a green test named for a rate limit is worse
than no test at all.** Phase 11 owns the limit.

---

### 5. DEVIATIONS FROM THE BRIEF — FOUR, ALL RECORDED RATHER THAN SILENT

1. **Three tests renamed, because the brief's names would have been lies.**
   `test_every_service_has_restart_unless_stopped` →
   `test_every_long_lived_service_has_restart_unless_stopped` (plus the one-shot's own assertion in
   the same test); `test_the_frontend_build_pins_its_node_version` →
   `…_and_serves_no_bind_mounted_checkout`, because the brief's own mutation table points two
   different mutations at it and a test named for one would have been silent about the other;
   `test_a_rate_limit_applies_to_the_api_path` → `test_the_api_path_carries_the_documented_edge_limits`.
2. **The deploy script REQUIRES a `.git` directory, inverting `CLAUDE.md § 5`'s refusal clause.**
   The two halves of that bullet describe two different scripts — the refusal guards an
   `rsync --delete` staging target, and the deploy path in the same section is `git pull` on the
   server, which needs a checkout. **The property is held directly instead:** the script contains no
   `rsync`, no `--delete`, and no recursive removal, and a test asserts all four. `CLAUDE.md § 22`
   states the resolution.
3. **`PyYAML==6.0.3` added to `requirements-dev.txt`.** `docker-compose.yml` is parsed, not
   grepped: a grep cannot tell a service's `ports:` key from the word inside the header comment
   explaining why the key is absent, and the published-port set has to be compared as a *set*.
4. **Two extra tests beyond the brief's twenty-five**, both in `test_compose_shape.py` and both
   about something the brief's list would have left unguarded:
   `test_the_one_shot_build_is_gated_before_the_proxy_starts` (caddy waits on
   `service_completed_successfully`, or it serves 404s from a working site) and
   `test_the_compose_file_still_names_timescaledb_first` — see section 6.

---

### 8. NOTES THAT WILL BE REDISCOVERED OTHERWISE

- **Five placeholder digests, all `sha256:` + 64 zeros, all of which CANNOT RESOLVE.** Two in
  `Dockerfile.api`, two in `Dockerfile.frontend`, one on the caddy image. That is the point
  (`CLAUDE.md § 12`): a missed resolution step fails at `docker build` with a manifest error rather
  than falling back to a floating tag. The tests deliberately do **not** reject them — a suite that
  is red on every clean checkout is a suite everybody learns to ignore.
- **`caddy:2-alpine` is the major-2 rolling tag.** A specific `2.x.y` tag would be more re-derivable
  and this agent cannot confirm which ones exist without inventing one. **Record the exact
  `caddy version` output in this file when the digest is resolved**, so the pin traces to a release
  rather than to a moving tag. Same shape for `python:3.12-slim` and `node:22-bookworm-slim`.
- **The CSP carries `'unsafe-inline'` on `style-src` and it is deliberate.** Recharts writes inline
  `style` attributes on the elements it renders; without it the chart still draws and is laid out
  wrongly, which is a broken picture rather than a blocked request. `script-src` stays `'self'`,
  and a test asserts that specifically.
- **`www.bargeanalysis.com` is deliberately NOT in the Caddyfile.** Adding a name before its A
  record exists means Caddy tries to issue a certificate for something that does not resolve, and
  failed issuance is what Let's Encrypt rate-limits. The record comes first, then the Caddyfile.
- **No ACME contact email is configured.** Caddy issues without one; the cost is no expiry
  warnings. An invented address would be worse than none — notices going somewhere nobody reads is
  indistinguishable from having configured it. The human's to add.
- **The api container has a healthcheck that queries the database, and it reports HEALTHY on a
  degraded stack.** Correct: `/api/health` returns 200 with `degraded: true` by design
  (`CLAUDE.md § 20`), and a container health signal that goes red on a stale ingest job is
  indistinguishable from one that goes red because the API is down.
- **The `frontend_dist` volume is a named volume, i.e. on the ROOT disk.** Deliberate: it is a
  rebuildable artifact, and losing it costs one `docker compose run`. The certificate store, which
  is *not* rebuildable without a rate-limited round trip, is on `/mnt/data`.
- **`ExecStop=/usr/bin/docker compose down` means every boot recreates the containers**, including
  a re-run of `frontend-build`. That is cheap and deterministic, and the certificates survive it
  because they are on `/mnt/data` rather than in the container.
- **Nothing in this commit touched `app/`, `migrations/`, `frontend/src/`, `infra/terraform/`,
  `verify/`, `tests/terraform/`, or `tests/provision/`.** This commit changed how things run, not
  what they do.


---

## Phase 10 live run, and the writeback commit — 2026-08-17

### Cloudflare proxying was turned OFF for issuance, and NOT ruled out afterwards

The A record was created proxied and switched to DNS-only before Caddy was started. The decision
worth recording is the second half: **proxying is a legitimate future option and was declined for
this run specifically, not rejected.**

- **What it would buy:** a CDN in front of `/api/conclusion`, which partly addresses the missing
  per-IP rate limit — the one real, live, unmitigated exposure this deployment has.
- **What it costs:** HTTP-01 stops working, so issuance has to move to DNS-01 or to Cloudflare
  origin certificates. Both are fine; neither is a thing to be discovering during the one attempt
  whose failure Let's Encrypt rate-limits per domain per week.
- **The rejected alternative was "set it up properly now, with the CDN in place."** Turned down
  because it couples a supply-chain-shaped change to first-ever issuance, which is the same trade
  that kept the `xcaddy` rate-limit plugin out of Phase 10.

### No ACME contact email is still configured, and that is still deliberate

Caddy issues without one; the cost is no expiry warnings. **An invented address would be worse than
none** — notices going somewhere nobody reads is indistinguishable from having configured it. The
human's to add.

### `CONTEXT.md` was split rather than trimmed

At ~3,500 lines the log had stopped being readable, which is how it drifted three commits behind
reality earlier in this project. The rejected alternatives:

- **Delete the old phases.** Refused. Several blocks carry the only record of a measurement — the
  compression index-byte split, the per-lock null counts, the Phase 3.5 probe method that turned out
  to be the error — and a phase log whose old entries are pruned is a log that cannot show how a
  belief changed.
- **Summarize each phase down to a paragraph.** Refused, and it is the more tempting one. Rule 2 of
  this commit is that no finding may be softened in the move, and a summary is where softening
  happens invisibly: "the deseasonalized level relationship is weak" becomes "needs further study"
  and nobody can see the difference in a diff.
- **What was done instead:** every span moved **verbatim**, by line range, with full coverage of the
  original 4,088 lines asserted before a byte was written, and the numerals extracted from the old
  file and checked against the new set afterwards. The only rewritten material is the preamble and
  `§ Up Next`, both of which were summaries of content that still exists elsewhere.

### The housekeeping list was condensed in `CONTEXT.md` and kept whole in the phase log

Roughly half of its items had closed. Splitting open from closed item-by-item inside `CONTEXT.md`
would have left the closed ones interleaved and the file no shorter, and deleting them would have
lost measurements. **So `CONTEXT.md` carries a condensed list of what is still open, and
`phase-log.md`'s appendix carries the full text as it stood.** A reader looking for "why is this
still open" gets one line; a reader looking for "what did we already close and on what evidence"
gets the original.
