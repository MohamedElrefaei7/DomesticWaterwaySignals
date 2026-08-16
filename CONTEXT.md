# CONTEXT.md — running log

This is the **log**: current state, decisions as they are made, and `§ Up Next`. Stable contracts
live in `CLAUDE.md`. If something here hardens into an invariant, move it there and note the move.

**Last updated:** 2026-08-15 (**PHASE 6 HAS RUN ON THE INSTANCE, AND THE SWEEP FOUND ESSENTIALLY
NOTHING. THAT IS THE RESULT.** 1 of **6,966** scanned pairs passes the gate; **271** would have
cleared the same threshold applied to the unadjusted p-value, and Benjamini-Hochberg collapsed that
to 1. The one survivor is **contemporaneous** — `lag_days = 0` — and its statistic is **negative**.
**No lead survives correction at any lag, in either direction.** Phases 1–5 remain complete and
verified. **Debt 1a is still open:** the capture script has run and the four CSVs exist, and nothing
has been pasted into this file yet.)

---

## PHASE 6 — VERIFIED ON THE INSTANCE, 2026-08-15. COMPLETE.

**1 of 6,966 scanned pairs passes the gate.** The denominator is stated in the same sentence as the
survivor because a passing count without one is the dishonest form of this result
(`CLAUDE.md § 18`), and because one row out of six thousand nine hundred and sixty-six is a
different claim from one row out of one.

### THE COMPARISON IS THE RESULT, NOT THE SURVIVOR

| | Count | Share of the scanned grid |
|---|---|---|
| Scanned and written to `signals` | **6,966** | — |
| Would have cleared the threshold on the **unadjusted** p-value | **271** | **3.9%** |
| Clear the gate after **Benjamini-Hochberg** | **1** | **0.014%** |

**271 → 1 is what the multiple-comparisons correction did, and it is the entire justification for
`CLAUDE.md § 18` existing.** A sweep that had stored only its survivors would have produced 271 rows
in a table of 271: a page of significant-looking relationships, every one correctly computed, every
one individually reproducible, and almost all of them noise. Nobody would have had to delete
anything for that to happen — the filter happens at write time and leaves no trace of itself.

**And the unadjusted count is BELOW what chance alone predicts.** At α = 0.05 a grid of 6,966
independent tests yields ~**348** significant results on pure noise; this grid yielded **271**. The
tests are not independent — adjacent lags of one feature are very nearly the same test, which widens
the spread around that expectation in both directions — so 271 on its own is not evidence *against*
the thesis. What it forecloses is the reading in the other direction: **nothing in this table is
finding significance at more than the rate chance predicts.**

Step 8 of the live procedure said that if the passing count came out near 5% of the grid, the sweep
was finding noise at exactly the rate chance predicts and *that* was the finding, to be recorded as
such rather than mined for its strongest row. **The raw count came in just under that line, and
after correction it is 1. This section is that recording.**

### THE SINGLE SURVIVOR, AND IT IS CONTEMPORANEOUS RATHER THAN PREDICTIVE

| Column | Value |
|---|---|
| `feature_name` | `days_below_p10` |
| `site_id` | `07032000` — Memphis |
| `horizon_days` | **7** |
| `lag_days` | **0** |
| `regime` | `all` |
| `statistic` | **−0.137** |
| `q_value` | **0.0446** |
| `n_effective` | **616** |
| `directional_consistency` | **1.00**, over **5** folds |

**`lag_days = 0` is the sentence in that table.** The one pair that survives correction is the
feature measured over the same period as the target. It is a **contemporaneous association, not a
lead.** This project's thesis is that the physical constraint *leads* the market; this row is not
evidence for that thesis, it is evidence that the two move together within the same week.

**The sign is negative.** −0.137 says more days below p10 goes with a *lower* forward rate return,
which is the opposite direction from the headline this project started with. It is also roughly what
`regime = all` should be expected to produce if Phase 5's finding 2 holds — that regime averages a
positive onset against a negative recovery — **but that is a story told about a result after seeing
it, and the rows that would test it are the ones below, neither of which supports it.** It is
recorded as an unexplained sign, not as a confirmation.

**q = 0.0446 is one row away from no survivors at all.** It clears 0.05 by 0.0054. `grid_size` and
`n_tests_adjusted` ride on the row for exactly this reason: the same statistic adjusted across a
narrower scan would carry a smaller q, in the same column, in the same units, from a different
experiment.

**The feature it is built on is a stand-in.** `days_below_p10` counts days below a percentile this
project picked because no operational threshold has a source yet (`CLAUDE.md § 1`). `days_below_p05`
and `days_below_p20` were scanned beside it, over the same sites, horizons, lags and regimes, and
**produced no passing rows at all.** Whatever this row is evidence of, it is evidence about p10 —
and the sensitivity of a lone survivor to the arbitrary level underneath it is not reassuring.

**`directional_consistency = 1.00` meets one half of `CLAUDE.md § 7`'s gate and not the other.**
Five of five folds agreed in sign, which clears ≥70% — and five is the minimum this phase admits,
because a fraction of five folds is the coarsest thing that can honestly be compared to 70%. The
**≥4 analogs** half has no counterpart in this table and was deliberately not approximated by
anything fold-shaped. **Nothing here is quotable under the output contract yet.**

**On `n_effective = 616`:** at horizon 7 the overlap correction `n / (horizon_days / 7)` is the
identity, so this is the raw count and not a discounted one. **Horizon 7 is the only horizon in the
scan where those two numbers are equal, and the single survivor is at horizon 7** — worth noticing
rather than explaining away. *(616 weeks is also about the span from Memphis's daily record start of
2014-10-01 to now. Consistent with it; not measured, and not a claim about which rows joined.)*

### 0 NEGATIVE-LAG PASSES, 0 POSITIVE-LAG PASSES

```sql
select regime,
       count(*) filter (where lag_days < 0 and passes_gate) as neg,
       count(*) filter (where lag_days > 0 and passes_gate) as pos
  from signals where run_id = <id> group by 1;
```

**Zero and zero, in every regime.** Forty-three lags spanning ±21 days, across three horizons, four
sites, five features and three regimes, and **no directional signal survives correction at any lag
in either direction.** The only passing row is at lag 0, which this query does not count.

Step 6 said that if negative lags dominated, the project's claim changes from **"the physical signal
leads"** to **"the market prices the forecast"**, and to report it either way. **Neither claim is
supported.** The competing explanation was given a first-class half of the scan precisely so that
its absence would be *observed* rather than assumed — and what was observed is that neither the
thesis nor its competitor clears the bar.

### THE ONSET PATTERN AT HORIZON 14 — VISIBLE, SUB-THRESHOLD, AND EXPLICITLY NOT A FINDING

In the `onset` regime at `horizon_days = 14`, `directional_consistency` rises to **0.8–1.0** and the
statistic **bottoms near lags −3 to 0**. The rows cluster: neighbouring lags agree, consistency is
high across the run of them, and the minimum sits about where Phase 5's eyeball said it would.

**Not one of those rows passes the gate.** They are recorded as a **visible clustering below the
threshold**, and the refusal belongs in the same paragraph as the description:

- **A run of neighbouring lags agreeing with each other is the expected texture of this table, not
  corroboration.** Adjacent lags of one feature are nearly the same test — that is why BH was chosen
  over Bonferroni, and it is equally why a cluster is not evidence.
- **Reading the −3-to-0 bottom as "the market prices the forecast" is the move `CLAUDE.md § 18`
  forbids.** A result at a negative lag is a finding about the world *when it survives correction*.
  This one did not.
- **Nothing is tuned on it.** Not the lag range, not the horizon set, not the minimum folds, not the
  effective-n floor. Raising a threshold after seeing which rows sit under it is choosing a method
  that suited the answer; lowering one admits exactly the short, sparse pairs most likely to produce
  a large correlation by chance.

**If it is real it will still be there when there is more history**, and the run that shows it will
be one nobody had to argue for. That is why it is written down as an observation rather than
dropped — and why it is written down with the refusal attached, so it cannot be quoted without it.

### THE RECOVERY REGIME IS STRUCTURALLY DATA-STARVED

For `days_below_p10` at Memphis — **the exact pair Phase 5's finding 2 was about** — the `recovery`
regime carries **1 to 7 observations at every horizon**, and every one of those rows is written with
status **`insufficient_observations`**. No statistic, no q-value, nothing to set against the onset
side.

**Phase 5's finding 2 is therefore NOT tested by this sweep, in either direction.** The reversal —
the rate peaking at 23 days below p10 and declining through 30, 37, 44, 51 and 58 — is neither
confirmed nor refuted here. **It is unmeasured**, and that is a different thing from a null result,
which is why it is not filed under the section above.

**The reason is structural, and the pre-run note predicted it in writing.** `recovery` means the
counter is falling or has reset. Phase 5's "recovery" — the stretch from 30 through 58 days below —
is a **still-rising** counter, classified `onset` under this definition. So `recovery` holds only the
days after the river came back up and the counter reset: a handful of days per event, across a
handful of events. **The regime Phase 5's most interesting finding lives in is the one this
definition leaves almost empty.**

The refusals are **rows with a status, not omissions** — the pair is visibly enumerated and visibly
refused, rather than absent in a way indistinguishable from never having been scanned.

**This improves with accruing history and with nothing else.** More low-water events, more resets,
more recovery days, arriving through ongoing ingest at the rate the river produces them — one or two
events a year. **It is not fixed by redefining the regime split now that the counts are known.**
Changing that split is a modelling decision for a human, in its own commit, with the current
definition's results measured first so the change has a before. **This section is that before.**

### The harness, after the sweep

- `python -m app.orchestration.migrate` — **0022 and 0023 applied. Twenty-three total.**
- `python -m verify.preflight` — **clean. Six gates green.** Its migration-count gate reads the
  directory, so twenty-three needed no change to it.

### WHAT THIS CHANGES FOR PHASE 7

`signals` holds exactly one row Phase 7's confidence gate could be pointed at; it is
contemporaneous, its sign runs against the thesis, and its q clears 0.05 by 0.0054. **Phase 7 reads
this table and does not re-run the sweep.** What the table says today is that there is nothing here
worth building a detector on.

**Build the analog engine anyway.** The ≥4-analog half of `CLAUDE.md § 7`'s gate has no counterpart
in Phase 6 and has to exist before anything can be refused for the right reason. But it will be
built against a null result, and it should be built to say **"insufficient history"** and mean it —
not to be tuned until this one row comes out the other side looking like a signal.

### STILL OWED FROM THIS RUN

- **The `run_id` and the wall time are not recorded here.** Step 3 asked for grid size, rows
  written, wall time and the `run_id`; this write-up had the grid size and the outcome. Every query
  in this section is written against `run_id = <id>` and this file does not yet say what that id is.
  Both are one query away — `select run_id, started_at, finished_at from signal_runs order by
  started_at desc limit 1;` — and they belong in this section rather than in a later one.
- **DEBT 1a — the four thesis CSVs have been captured and are still not pasted in.** The script ran;
  the files exist; the paste is the review step and it has not happened. **The debt is not closed.**

---

## PHASE 6 — THE ±LAG SWEEP. WRITTEN OFFLINE 2026-08-15. **THE BUILD RECORD; THE OUTCOME IS ABOVE.**

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

### Mutation notes — two rows needed two forms

- **Row 2, "drop the BH adjustment, store only raw p", is two distinct mutations.** Removing the
  adjustment inside `benjamini_hochberg` turns test 6 red on the hand-computed fixture; making the
  *writer* store the raw p in the q column leaves test 6 green and turns test 7 red on the widened
  end-to-end half. Both were run. **Had test 7 not been widened, the writer form would have gone
  undetected by both named tests** — the CHECK constraint permits a p and a q that happen to be
  equal.
- **Row 5, "set the gap to `horizon - 1`", was run once per named test** (12 and 13) rather than
  once. Test 12 fails with "1 training observation has a 7-day forward window reaching into the test
  window" — exactly one, which is the off-by-one this design predicts. Test 13 fails with "horizon 7
  produced gaps [6]".
- **Row 1's message is worth keeping:** with the raw count substituted, `measure()` stored
  `p = 6.06e-09` where the effective-n computation gives `7.03e-05` — **a factor of ~11,600 on that
  fixture**, in the flattering direction, from one identifier.

---

## PHASE 4 — VERIFIED ON THE INSTANCE, 2026-08-14. COMPLETE.

**Landed:** three rate horizons at **8,260 rows each — 24,780 total** — plus **26,144 movement
rows**. Every dataset matched its seeded `source_row_count` **exactly**; nothing truncated.

**Paging behaved as designed on every dataset:** a short page mid-sequence, then an empty page
terminating the loop. That is `CLAUDE.md § 16`'s first bullet working live — the `while len(page)
== limit` loop this project refused to write would have stopped at the short page and reported a
truncated dataset as a complete one, on real data, with a plausible row count.

### Rate nullity — winter navigation closure, as measured

774 of 8,260 on nearby, concentrated December–March on the upper segments: **Twin Cities 426**,
**Mid-Mississippi 303**, and **Cairo-Memphis only 3** — so the segment the output contract names has
**1,177 of 1,180 weeks**. Comparable shapes on the two forward horizons (**705** and **721** absent).

### Movement nullity — a reporting gap, not a closure, as measured

108 of 26,144 absent, confined to the three summary locks (**AK Lock 1 71, OH Olmsted 26, MS Locks
27 11**), 96 of them in 2015–2016, flat across months. Separately, **8,218 records (31%) report an
explicit zero**, which is the published way of saying nothing moved. **The two populations are never
summed** — `0018`'s whole argument, confirmed against the full dataset.

### NEW OBSERVATION — `lock_movements` is SPARSE, and it changes how features may use it

Reported zeros are the majority or near it at several locks: **MS Lock 15 has 1,434 zeros of 2,840
rows.** `lock_movements` is a **sparse per-commodity weekly series**, and any feature built on it
**must decide explicitly whether to aggregate across commodities before differencing.** Differencing
a per-commodity series that is half zeros produces a sequence of spikes and reversions that looks
like volatility and is mostly the reporting grain.

**Nothing in Phase 5 builds a movements feature**, precisely because that decision has not been
made. The feature layer is discharge-only for now; see `§ Up Next`.

### THE FIRST LOOK AT THE THESIS — 2022 and 2023, Cairo-Memphis nearby against Memphis discharge

**2022.** Discharge fell from **~368,000 cfs** (week ending 08-02) to **153,143** (10-25) — a **58%
decline**. The rate rose from **388** to **2,812.5** — a **7.2× rise** — peaking **10-11**.
Discharge began declining around **08-09**; the rate began climbing hard around **08-30**, so **the
rate follows onset by roughly two to three weeks.** Recovery was symmetric: discharge rebounded
through November and the rate fell back with it.

**2023.** The same shape at smaller amplitude. Discharge **372,143** (08-15) → **139,000** (10-17);
the rate **335.9** → **1,688.9**, peaking **09-26**.

> **THE FULL WEEK-BY-WEEK RESULT SETS ARE NOT REPRODUCED HERE, AND THE REASON IS THE POINT.** The
> brief asked for both tables verbatim; the session that wrote this had the anchor points above and
> **not** the row-by-row output, and this project does not fill in a series it was not given
> (`CLAUDE.md § 4`: when data is lost, record the loss — never synthesize a replacement).
> Interpolating ~26 plausible weekly rows between the measured endpoints would produce a table that
> reads as measured and is not. **Re-run the two queries in `§ Up Next` and paste both outputs here**
> — they are cheap, the database holds them, and every number above is checkable against them.

### The observation worth carrying forward — stated as an observation, not a finding

**In both years the rate peaked BEFORE discharge bottomed** — by two weeks in 2022 (rate peak 10-11,
discharge trough 10-25) and by three weeks in 2023 (rate peak 09-26, discharge trough 10-17). Onset
looks like the physical constraint leading; recovery looks like the market anticipating. That is
consistent with **operators pricing published river forecasts**, which is the risk the handoff named
— arriving as a **nuance rather than a refutation**. And it must never be quoted without these
three cautions, which is why they are in the same paragraph as the claim: **(1) n = 2** — the
project's own confidence gate requires ≥4 analogs and ≥70% directional consistency, and two aligned
series eyeballed together is the single most common way to believe a relationship that does not
survive a lead-lag sweep; **(2) seasonality is unaccounted for** — both events are autumn harvest,
when rates rise regardless of hydrology, so some of that 7.2× is calendar rather than constraint,
and removing it is exactly what Phase 5's deseasonalization exists to do; **(3) the join was a
weekly mean over a trailing six days**, which smooths away the sub-weekly timing that would
distinguish leading from lagging.

**NOTHING IN PHASE 5 IS TUNED ON THE BASIS OF THAT OBSERVATION.** Phase 6's ±lag scan measures it.
The recovery-side asymmetry is named in `§ Up Next` as the first thing that scan should be pointed
at.

> **THE SCAN RAN ON 2026-08-15 AND DID NOT FIND IT.** The rate-leads-discharge reading above implies
> a result at a **negative lag**; the sweep scanned ±21 days and **no negative-lag row passes the
> gate, in any regime, at any horizon.** The eyeballed lead of two and three weeks is not corroborated
> by the ±lag scan built to test it. **Caution 1 — n = 2 — was the one that mattered.** See
> `PHASE 6 — VERIFIED`.

> **CAUTION 2 WAS ANSWERED ON 2026-08-15, AND IT LANDED AGAINST THIS SECTION.** Phase 5's
> deseasonalization shows the *level* relationship above is **substantially calendar**: on
> 2022-08-09 the anomaly was `+18,095` — above seasonal normal — while rates were already climbing,
> and 2022's deepest anomalies fall in July and November rather than during the September–October
> spike. **Do not quote the 7.2×-against-58% pairing above as evidence for a level relationship.**
> What survives is a DURATION relationship, and the recovery-side asymmetry sharpens rather than
> softens. See `PHASE 5 — VERIFIED`, findings 1 and 2.

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

## Phase 5 — the normalizer and feature layer, written offline 2026-08-14

Three migrations (`0019` `gauge_daily`, `0020` `features`, `0021` `targets`), six modules under
`app/features/`, one cadence entry, one freshness entry. **The first derived data in this project**,
which is what the new `CLAUDE.md § 17` exists to govern: everything under `app/ingest/` writes what a
source published, and everything here writes something this project computed — a number with nothing
upstream to contradict it when it is wrong.

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

### Status

- **283 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `201 passed, 82 skipped`. The previous baseline was 244.
- **All 14 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run. **No row needed a second pass.**
- **`0019`–`0021` are new; nothing in `0001`–`0018` was edited.** Twenty-one migrations apply clean.
- **VERIFIED ON THE INSTANCE 2026-08-15.** All ten steps ran. **Step 9 contradicted what this file
  recorded the day before** — the four findings are immediately above.

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

## PHASE 5 — VERIFIED ON THE INSTANCE, 2026-08-15. COMPLETE.

**The build ran, and it changed what this project believes about its own thesis.** The raw-discharge
relationship recorded on 2026-08-14 was substantially calendar; the relationship that survives
deseasonalization is a *duration* one, and it reverses on recovery.

### What was built

| | Rows |
|---|---|
| `gauge_daily` | **32,462** |
| `features` | **162,310** — exactly 5 registered features × 32,462 daily rows |
| `targets` | **3,540** — 1,180 Cairo-Memphis weeks × 3 horizons |

**70 seconds from scratch** (`--from-scratch --start 1990-01-01`), and **the idempotent rerun wrote
0 rows** — decision 8's claim measured rather than asserted. `IS DISTINCT FROM` is what makes that a
real number; a plain `DO UPDATE` would have reported all 198,312 rows as written and looked fine.

### FINDING 1 — the deseasonalized LEVEL relationship is WEAK. Caution 2 was right.

This is the finding, and it is a correction to what this file recorded yesterday.

**On 2022-08-09 the discharge anomaly was `+18,095` — ABOVE seasonal normal — while rates had
already begun climbing.** And **the deepest anomalies of 2022 fall in July and in November, not
during the September–October rate spike.** The level was not unusually low when the market moved,
and it was at its most unusual when the market was not moving.

**So the raw-discharge story recorded on 2026-08-14 was substantially calendar.** Both events are
autumn harvest, when rates rise regardless of hydrology; removing the calendar removes most of what
made the two raw series look aligned. That is exactly what caution 2 said might happen, and it
happened. **`CLAUDE.md § 0`: when a measurement contradicts the plan, the measurement wins.** The
7.2× rate move is real and the 58% discharge decline is real; what is now in doubt is that the
*level* of discharge is the variable connecting them.

### FINDING 2 — the DURATION relationship is strong on onset, and REVERSES on recovery

`days_below_p10` at Memphis, against the Cairo-Memphis nearby rate:

| `days_below_p10` | Rate |
|---|---|
| **0**, held for eleven weeks | 335 → 656 (drifting) |
| **2 → 9 → 16 → 23** | **925 → 1,428 → 2,427 → 2,812** |
| **30 → 37 → 44 → 51 → 58** | declining from the 2,812 peak |

**The rate peaked at 23 days below and then declined through 30, 37, 44, 51 and 58 days below.**
Duration drives the ONSET; it does not drive the RECOVERY. The market stops paying for a constraint
that is still tightening.

**This is the recovery-side asymmetry recorded yesterday as an observation, now visible in a
constructed feature rather than in two eyeballed series** — and it is sharper here than it was
there. It is also why a single correlation over a whole event would be near useless: it would
average a strong positive onset against a strong negative recovery and report approximately nothing.

**Still n = 2.** The confidence gate needs ≥4 analogs and ≥70% directional consistency. Nothing
above is a finding the output contract may quote yet.

> **PHASE 6 DID NOT TEST THIS, IN EITHER DIRECTION — 2026-08-15.** The sweep's `recovery` regime for
> this exact pair — `days_below_p10` at Memphis — carries **1 to 7 observations at every horizon**
> and is refused as `insufficient_observations` on all of them. **The reversal is unmeasured, not
> disconfirmed**, and the reason is the regime-definition mismatch recorded in `§ Up Next`: the
> stretch from 30 through 58 days below is a still-rising counter and lands in `onset`, so
> `recovery` holds only post-reset days. **Do not read the sweep's null result as a refutation of
> this finding. It is a refusal to test it.** The onset side did show a sub-threshold clustering at
> horizon 14 — recorded in `PHASE 6 — VERIFIED` as an observation, explicitly not a finding.

### FINDING 3 — `discharge_min` is a duplicate of `discharge_mean` almost everywhere

Wherever `n_observations = 1`, `value_min` **is** the published daily mean — which is what migration
`0019` predicted in writing, and the measurement confirms the scale of it:

- **Memphis and Vicksburg: entirely.** Every row.
- **Baton Rouge: 95%.**
- Real sub-daily minima exist only at **St. Louis IV (6,880 days)** and **Baton Rouge IV (366
  days)**.

`n_observations` is what makes this visible instead of a silent duplication, and this is the column
justifying its existence on the first run. **The consequence for Phase 6: `discharge_min` and
`discharge_mean` are the same series at two of the four gauges, so a sweep treating them as
independent inputs is scanning one variable twice at those sites.** Nothing is changed here on the
strength of it — a real minimum is still the right thing to want, and the coverage may improve as
instantaneous retention accumulates.

### FINDING 4 — the eight-year climatology guard NEVER FIRED, and is therefore untested

`climatology_n_years` runs **11 to 37 across every row, with no NULLs anywhere.** Live verification
step 3 expected a substantial NULL-anomaly population in Memphis's early years and found none.

**The guard holds by luck of coverage, not by demonstration.** Memphis's *daily* record starts
2014-10-01, which was the reasoning behind choosing eight years — but the 15-day smoothing window
pools distinct calendar years across the whole window, and with 35 years available at two sites and
a decade at the others, every day-of-year clears the bar comfortably.

**This is recorded as a gap, not as a success.** The unit tests exercise the guard directly and go
red when it is removed (mutation row 2), so the *mechanism* is confirmed; what has never been
exercised is the guard **on real data**. If a fifth gauge is ever seeded with a short record, that is
the first run where this matters and the first run where nobody will have seen it work.

> **PHASE 6 CLOSED HALF OF THIS — 2026-08-15, debt 1c.**
> `test_a_five_year_climatology_yields_null_anomaly_end_to_end` seeds a deliberately shallow
> **five-year** record at Memphis and asserts, **against a real database**, that every anomaly comes
> back NULL, that `climatology_n_years` is present on the refused rows, and that it is **exactly 5**.
> So the refusal is now known to survive the whole round trip — the builder's return tuple, the
> upsert's six-placeholder parameter list, and `0020`'s `features_anomaly_needs_its_year_count`
> CHECK. A `coalesce(anomaly, 0)` anywhere in that path would now turn a test red.
>
> **What is still not closed:** the guard has never fired on *real* data. `climatology_n_years` on
> the real table still runs 11 to 37 with no NULLs anywhere. The mechanism and the plumbing are both
> confirmed; the coverage question is unchanged.

### FIXED IN THE SAME COMMIT — the test suite had become flaky, and Phase 5 caused it

Writing this up surfaced a **`DeadlockDetected` in the schema-reset fixture, on roughly one full-suite
run in four.** Measured before the fix: 1 error in 4 runs; after: 12 clean runs in 12.

**Phase 5 caused it, and the mechanism is worth keeping.** The reset dropped tables **one statement
per table**, from a catalog scan with no `ORDER BY`, each `CASCADE` taking locks on that table's
dependents. `gauge_daily` and `features` both added foreign keys to `gauges`, widening the dependency
graph — so fifteen separately-locked statements in a varying order, racing TimescaleDB's background
workers on the two hypertables, became fifteen windows for a lock cycle. Naming every table in **one
`DROP`** closes the window: Postgres takes the whole lock set in a single operation.

**This was a flaky test fixture, not a defect in anything under `app/`** — but it is recorded here
rather than fixed quietly, because a suite that fails one run in four is a suite whose failures stop
being read, and that ends the same way a muted alert does. Applied to all three `conftest.py`
copies, which are duplicated deliberately.

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

## Phase 4 close-out — `tons` nullability measured, and the analogy was wrong, 2026-08-14

**The previous commit left `tons` alone on the stated grounds that its nullability was an analogy to
`rate` rather than a measurement. It has now been measured, and the analogy would have been wrong.**
The *shape* of the handling is the same; the *meaning* is not, and a comment copied from the rates
module would have asserted something false.

### Finding 1 — `tons` is absent on 108 of 26,144 records (0.4%), on three locks only

| Lock | Nulls | Rows |
|---|---|---|
| AK Lock 1 | 71 | 4,928 |
| OH Olmsted | 26 | 4,928 |
| MS Locks 27 | 11 | 4,928 |
| IL La Grange, MS Lock 15, MS Lock 25, MS Lock 26 | 0 | 2,840 each |

**By year — 96 of 108 fall in 2015–2016:** 2003 (6), 2006 (2), 2012 (2), 2014 (1), **2015 (79)**,
**2016 (17)**, 2021 (1).

**By month — flat:** 16, 9, 11, 6, 3, 14, 11, 1, 7, 9, 7, 14.

**By commodity — spread across all four:** Corn 46, Other Grain 38, Wheat 19, Soybeans 5.

### Finding 2 — `tons = 0` appears on 8,218 records (31%). This is the finding that matters

USDA publishes explicit zeros **routinely**, on nearly a third of all records. So zero is the
*normal, published* way of saying "no grain moved through this lock this week" — and **a NULL is
therefore not the same statement, and not a rarer spelling of it.** The source has a way to say
"none moved" and uses it 8,218 times; the 108 records that say nothing at all are saying something
else.

### The two NULLs mean different things, and that is the whole commit

| Column | What a NULL means | Evidence |
|---|---|---|
| `barge_rates.pct_of_tariff` | **Seasonal and physical** — winter navigation closure | 774 records, 661 in Dec–Mar, 729 on the two upper segments |
| `lock_movements.tons` | **A reporting gap. It says nothing about the river.** | 108 records, flat across months, three locks, 96 in 2015–2016 |

**Do not reuse the winter-closure language for `tons`.** `0018`'s column comment states the reporting
gap explicitly and names the rates column as *not* the same thing, so the two cannot be read across.

**Why this matters more here than it did for rates.** The three affected locks are the **summary**
locks — `MS Locks 27` is the Mississippi's main southbound gate and the single most load-bearing
series in the dataset. Coalescing a NULL to `0` would assert "no grain moved through Lock 27 that
week" for eleven weeks USDA simply did not report. That is a **fabricated zero**, and it is exactly
the failure `CLAUDE.md § 7`'s confidence gate exists to prevent — arriving in the ingest layer,
where no gate is watching.

### OPEN, UNEXPLAINED — the 2015–2016 concentration

96 of the 108 gaps fall in a two-year window, on three locks, flat across months. **The cause is
unknown and nothing in this commit acts on it.**

**Deliberately not built:** no `gauge_known_gaps`-style table for it, and those weeks are **not
excluded**. A 0.4% gap falling outside both labelled events (2022 and 2023) does not warrant the
machinery, and building the machinery would imply a conclusion about the cause that nobody has
reached. It is recorded here as an observation, and that is all it is.

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

### Status

- **244 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `170 passed, 74 skipped`. The previous baseline was 239.
- **All 7 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run.
- **`0018` is new; nothing in `0001`–`0017` was edited.** Eighteen migrations apply clean.
- **VERIFIED ON THE INSTANCE 2026-08-14.** All ten steps ran, including step 9. The outcome —
  including both thesis tables and the observation they produced — is recorded immediately below.

### Mutation notes — two rows needed a second pass

- **Row 1 (blanket `record.get`)** first went red with a `NameError`, because deleting the
  `optional_field` call left the `tons=` expression referencing a name that no longer existed. That
  proves only that the test runs (`CLAUDE.md § 0`). Redone as a coherent `tons=record.get(...)`
  implementation, it went red on test 3's own assertion — `DID NOT RAISE`.
- **Row 6 (combined figure)** was first applied as "stop counting nulls", which is a different
  mutation. Redone as the actual merge (`if row.tons is None or row.tons == 0`), it produced
  `('MS Locks 27', 3, 2, 0)` — the two populations added together into the single figure the decision
  forbids — and went red on the structural assertion.

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

## Phase 4 correction 2 — nullable rates and the corrected segment, measured 2026-08-14

**THE FIRST BACKFILL ATTEMPT FAILED ON ITS OWN TRIPWIRE, AND THAT IS THE SYSTEM WORKING.** `0016`
seeded seven `location` values, five of them from the handoff rather than from a measurement, and
committed in writing that the API would win if they disagreed. It disagreed about one, the run
stopped rather than opening a silent eighth series, and the attempt produced two findings.

### Finding 1 — the segment is `Lower Illinois`, not `Illinois River`

All seven measured, 1,180 rows each: `Cairo-Memphis`, `Cincinnati`, `Lower Illinois`, `Lower Ohio`,
`Mid-Mississippi`, `St. Louis`, `Twin Cities`.

The handoff document said "Illinois"; `0016` seeded `Illinois River`. **The API wins.** `0017`
replaces the CHECK, and all seven values in it are now measured — none is from a document.

### Finding 2 — 774 of 8,260 rate records have no `rate` field, and the cause is physical

**Not null-valued: the key is absent from the record entirely.** Such a record carries exactly
`['date', 'location', 'month', 'week', 'year']`.

| Month | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Missing | 199 | 181 | 114 | 33 | 19 | 16 | 9 | 5 | 5 | 1 | 25 | 167 |

By location: Twin Cities 426, Mid-Mississippi 303, Lower Illinois 25, St. Louis 7, Cincinnati 5,
Lower Ohio 5, **Cairo-Memphis 3**.

661 of 774 fall in December–March; 729 of 774 are on the two upper segments. **This is winter
navigation closure on the Upper Mississippi.** There is no rate to publish when no barges move.

**So a missing rate is a fact about the river, not a gap in ingest.** The row is written with a
NULL rate. A skipped row would leave a series with no January at all — indistinguishable from an
ingest that failed to fetch January — and Phase 5's seasonal baseline would fit a winter that never
closes.

### What this means for the thesis

- **Cairo-Memphis — the segment `CLAUDE.md § 7`'s output contract names — has 1,177 of 1,180
  weeks.** The target series is effectively complete.
- **The 2022 window's 26 missing rates are an ordinary winter closure, not the autumn low-water
  event.** The 2022 rate spike is intact in the data.

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

### OPEN MEASUREMENT — does `lock_movements.tons` have the same structure?

> **CLOSED by the Phase 4 close-out at the top of this file, 2026-08-14. It does not.** The
> measurement came back 108 of 26,144, flat across months and confined to three locks — a reporting
> gap, not a closure — and the 8,218 explicit zeros beside it are what make that reading
> unavoidable. **The gate below is discharged; movements can be backfilled.** Retained as written
> because refusing to act on the analogy is what made the finding possible.

**Not touched by this commit, deliberately.** A lock with no reported movement in a closure week is
the same shape of fact, and `tons` is already nullable — but **nothing has measured whether USDA
actually omits it**, and making a change on an untested analogy is precisely what gets tidied in
later as though it had been verified. It is step 5 of the live procedure, and **movements must not
be backfilled until it returns zero**.

### Status

- **239 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `167 passed, 72 skipped`. The previous commit's baseline was 231.
- **All 8 mutation-table rows confirmed**, each watched red on its own assertion, restored, with
  `__pycache__` cleared between restore and re-run. No row needed a second pass.
- **`0017` is new; nothing in `0001`–`0016` was edited.**

### One file deviates from the brief's list

`tests/ingest/fixtures/socrata_rates_ok.json` no longer exists — the previous commit split it into
`socrata_rates_nearby.json`, `_1month`, and `_3month`. The rate-absent record was added to
**`socrata_rates_nearby.json`**, and a test now asserts the fixture carries exactly one, so a
fixture that drifted back to an all-rates page could not quietly pass the parser tests that read it.

---

## Phase 4 correction — the real USDA identifiers and field maps, measured 2026-08-14

**THE DATASET IDENTIFIERS ARE RESOLVED. EVERY FIELD NAME PHASE 4 ASSUMED WAS WRONG.** Migration
`0016` lands the measured ids, bounds, and row counts, and corrects the two table schemas to the
shape USDA actually publishes. Nothing below has run against the live API yet — the identifiers and
counts are the human's measurement; the ingest against them is live verification's job.

### What was measured, on 2026-08-14

| Key | Dataset ID | Rows | Range | Fields |
|---|---|---|---|---|
| `barge_rates_nearby` | `deqi-uken` | 8,260 | 2004-01-07 → 2026-08-11 | `date`, `week`, `month`, `year`, `location`, `rate` |
| `barge_rates_1month` | `svms-9yya` | 8,260 | 2004-01-07 → 2026-08-11 | same, plus `rate_month` |
| `barge_rates_3month` | `uuhv-5etw` | 8,260 | 2004-01-07 → 2026-08-11 | same, plus `rate_month` |
| `lock_movements` | `n4pw-9ygw` | 26,144 | 2003-01-04 → 2026-08-08 | `date`, `week`, `month`, `year`, `commodity`, `lock`, `tons` |
| `cost_indicators` | `8uye-ieij` | not measured | not measured | not measured — seeded, not fetched |

**The `required_field` tripwire is what made this correction cheap.** Phase 4 disclosed in writing
that `usda_rates.FIELDS` and `usda_movements.FIELDS` came from the shape the fixtures were written
to rather than from USDA, and routed every read through `required_field`. So all nine wrong names
(`segment`, `week_ending`, `horizon`, `rate_pct_of_tariff`, `lock_id`, `grain_type`, `direction`,
`barges`, and the `barges`/`tons` pairing) were a field map and a migration, not an investigation.
Had the reads used `.get`, this would have been a backfill that reported success over a table of
NULLs — `CLAUDE.md § 2`, theme 1, exactly as written.

### The three structural findings

1. **The three horizons are THREE DATASETS, not a column.** `horizon` stays in the rates primary
   key — 0014's reasoning was right — but its value is assigned by which dataset a row came from,
   through one total mapping (`usda_rates.HORIZON_BY_DATASET_KEY`), and is never read out of a
   record. A test asserts the mapping is total and injective, so a fourth rates dataset fails
   loudly rather than defaulting into an existing series.
2. **Movements publishes TONS ONLY — no barge count and no direction.** The dataset is
   "Downbound Barge Grain Movements (Tons)": downbound by construction, so there is no direction
   dimension to key on, and no barge count is published at all. Both columns are dropped. **A barge
   count, if it is ever wanted, is a DIFFERENT dataset and a separate commit with its own
   measurement** — not a NULL column held open as a placeholder. New key:
   `(lock, week_ending, commodity)`.
3. **`rate_month` is a published calendar month, stored nullable, and is not an offset.** The
   forward datasets quote month 9 and 11 against a publication month of 8. Nearby rows have no such
   field and store NULL, which is correct rather than missing; a database `CHECK` enforces the
   pairing in both directions.

### The two vocabularies, verbatim

**Locks** (all seven measured, with counts): `AK Lock 1` (4,928), `IL La Grange` (2,840),
`MS Lock 15` (2,840), `MS Lock 25` (2,840), `MS Lock 26` (2,840), `MS Locks 27` (4,928),
`OH Olmsted` (4,928). **Note `MS Locks 27` — plural — beside three singular siblings.** That
inconsistency is USDA's, it is stable, and it is stored exactly as published. Normalizing it is the
tidy that breaks the join as *missing weeks* for the joint-largest lock in the dataset.

**Locations** (seeded from the handoff): Twin Cities, Mid-Mississippi, Illinois River, St. Louis,
Cincinnati, Lower Ohio, Cairo-Memphis. **ONLY `Cairo-Memphis` AND `Twin Cities` ARE MEASURED** —
they appear verbatim in captured records. The other five are unconfirmed spellings, and **live
verification step 2 confirms all seven before any backfill runs. Where the API disagrees, the API
wins and the correction lands in `0017`.**

Both vocabularies carry a `CHECK` that is a **tripwire, not a vocabulary**: an unseen value is a
loud insert failure, and the fix is to measure it and add it in a new migration — never to drop the
constraint.

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

### Status

- **231 tests green with zero skips** against a throwaway local TimescaleDB container on the pinned
  image; offline the same suite is `162 passed, 69 skipped`. The pre-correction baseline was 218.
- **All 11 mutation-table rows confirmed**, each watched red on its own assertion, restored, and
  `__pycache__` cleared between restore and re-run. **Row 11 needed a second pass** — see below.
- **`0016` is new; nothing in `0001`–`0015` was edited.**

### Mutation note — row 11 needed a second pass

**"Add `direction` back to the movements key"** first went red for the wrong reason. Adding the
column and putting it in the primary key left the upsert's `ON CONFLICT` naming a key with no
matching unique index, so the test failed on a database error inside `upsert_movements` rather than
on the assertion about the table's columns. That proves the test runs, not that the guard works
(`CLAUDE.md § 0`).

Re-done consistently — column, primary key, dataclass, field map, upsert, dedup key, with the
constant `'Down'` the dataset would imply — the writes succeed and the test fails on its own
assertion: `lock_movements holds ['commodity', 'direction', 'lock', 'tons', 'week_ending']`. Both
passes are reported.

---

## Current state

**PHASE 3 IS COMPLETE AND VERIFIED ON THE INSTANCE, 2026-08-14. The compression measurement —
outstanding since Phase 3 was written — is taken, and both ratios are below.**

### The daily backfill, on the instance

- **30,539 rows across four sites in 12.5 seconds.** Per-site coverage matches the corrected seeds
  **exactly**: St. Louis from 1990-01-01, Memphis from 2014-10-01 (92 days in 2014, full years
  after), Vicksburg from 2008-01-01, Baton Rouge from 2004-03-17. The seeds corrected in `0011`
  were right — `min(date)` per site agrees with each one.
- **Instantaneous table: 258,739 rows** — St. Louis 2007-10-01 to present, plus one year of Baton
  Rouge from the Phase 3 rehearsal.
- **The `gauge_series` view was spot-checked at a seam.** Memphis 2015-06-15 resolves `dv`; St.
  Louis 2015-06-15 resolves `iv` with a computed daily mean of 454695.652…. IV precedence holds and
  the `source` column exposes which side of the seam a row came from — which is the whole reason
  that column exists.

### Compression, measured — both hypertables

| Table | Chunks (compressed) | Before | After | Ratio |
|---|---|---|---|---|
| `gauge_readings_iv` | 986 (980) | 134,791,168 B | 40,140,800 B | **3.36:1, 70.2%** |
| `gauge_readings_daily` | 37 (35) | 10,960,896 B | 1,433,600 B | **7.65:1, 86.9%** |

**MOST OF THE WIN IS IN INDEXES, NOT TABLE DATA**, and recording that is the point rather than
recording the two ratios. IV index bytes 70,369,280 → 16,056,320; daily index bytes 6,455,296 →
573,440 (≈11:1). Table bytes compress far less: 56.4 → 16.1 MB and 4.2 → 0.57 MB.

That explains the counterintuitive headline — **the smaller table compresses better** — which
otherwise invites the wrong conclusion that daily data is somehow more compressible. It is not:
the daily table is proportionally more index than the instantaneous one, and indexes are what
columnar compression flattens.

**The honest framing, and it belongs in any README text:** these are real measurements on a real
~290k-row series and the reductions are genuine, **but at this volume Postgres alone would be
entirely adequate.** TimescaleDB here is a demonstrated engineering choice, not a necessity the
data forced. No README line may imply otherwise.

**Tuning candidate, logged and NOT acted on:** 986 chunks for 258,739 IV rows is the main drag on
that table's ratio — a 7-day interval across 1990–2026 with only one dense site leaves many sparse
chunks carrying fixed per-chunk overhead. A 30-day interval would likely improve both the ratio and
planning. **Chunk interval changes affect NEW chunks only**, so this is a deliberate future
migration on a considered date, not a fix to slip in.

### Two housekeeping items from the same session, both fixed in the Phase 4 commit

1. **`daily_backfill`'s `FIRST DATA` line reported the first date of the RUN, not of the record.**
   On a resumed run it printed `2020-01-01` for Memphis against a **correct** seed of `2014-10-01`,
   under a sentence saying that value is what the seed reconciles against — which would prompt
   "correcting" a seed the close-out commit had just got right. Fixed as a log-wording change (the
   walk was correct; the sentence about it was not): the summary and the log now distinguish
   **FIRST DATA IN THE RECORD** from **first date in THIS RUN**, and only a walk that actually
   began at the seed invites reconciliation. `--start` never counts as such a walk, even when the
   dates coincide, because what makes a date reconcilable is that nothing earlier was skipped.
2. **Mutation confirmation must clear `__pycache__` between restore and re-run.** A restore once
   still read red from stale bytecode, which is indistinguishable from a restore that did not
   happen. Now in `CLAUDE.md § 0`, along with the rule that a mutation going red for the wrong
   reason is not a confirmed guard.

---

## The thesis, as Phase 3 leaves it

Phase 3 changed what this project can honestly claim. Restated plainly, because the earlier
framing in this file no longer matches the data:

- **The feature is DISCHARGE (`00060`), not stage.** Stage is unavailable via USGS IV at Memphis
  and Vicksburg, and deriving it from a rating curve was rejected as fabrication, not deferred
  (`CLAUDE.md § 14`).
- **Corridor depth is uneven: one deep site (St. Louis, 1990→) and three shallower**, with roughly
  **sixteen years of four-site overlap (2010→)**. Both labelled low-water events — 2022 and 2023 —
  are covered at all four sites.
- **Any baseline needing pre-2004 history runs on St. Louis alone.** Not "St. Louis and Memphis":
  Memphis serves nothing between 1994 and 2014.

---

## Phase 4 — USDA ingest (written offline, 2026-08-14)

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

## PHASE 6 HAS RUN. **PHASE 7 IS THE NEXT THING TO DO — AND THE TWO ITEMS BELOW COME FIRST.**

The sweep ran on 2026-08-15; the outcome is recorded at the top of this file under `PHASE 6 —
VERIFIED`, in the same session, which is the first time this project has managed that. **1 of 6,966
pairs passes the gate, contemporaneously, with a negative statistic.**

**Two things are owed before Phase 7 starts, and both are small:**

1. **The `run_id` and the wall time of the sweep run.** `select run_id, started_at, finished_at from
   signal_runs order by started_at desc limit 1;` — every query in the write-up is parameterized on
   a `run_id` the file does not name.
2. **DEBT 1a — paste the four CSVs.** They are captured and unpasted, which is where the debt has
   been in one form or another across three phases. Fenced blocks, into `PHASE 4 — VERIFIED` and
   `PHASE 5 — VERIFIED`, replacing the notes that say the output is still owed.

### Phase 7 — the analog engine and the confidence gate, against a null result

The analog engine and the confidence gate as a consumer. **It reads `signals`; it does not re-run
the sweep**, and `directional_consistency` with its `folds` is the column `CLAUDE.md § 7`'s ≥70%
half consumes. The **≥4 analogs** half has no counterpart in Phase 6 and was deliberately not
approximated by something fold-shaped — that is Phase 7's to build.

**Build it knowing the table is empty of signal.** One passing row, at lag 0, sign against the
thesis, q = 0.0446. The gate's job on this data is to say **"insufficient history"**, and a gate that
cannot be watched saying it on the data currently in the database is a gate nobody has seen work —
the same gap `FINDING 4` records about the eight-year climatology guard. **The null result is the
test case, not an obstacle to it.**

**Phase 7 selects; Phase 6 measured.** Keeping those in separate steps is the whole point of
`CLAUDE.md § 18`'s seventh bullet, and the sweep exposes no accessor that would let Phase 7 shortcut
it.

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

### **READ BEFORE STEP 7 — the regime labels and the Phase 5 narrative are not the same split**

`CLAUDE.md § 18` requires the regime split to come from the predictor, and the implementation follows
the brief literally: **`onset` is any window where the feature counter is RISING, `recovery` is where
it is falling or has reset.**

Phase 5's finding described the rate peaking at **23 days below p10** and then falling through **30,
37, 44, 51 and 58 days below**. Those larger numbers are a **still-rising counter** — so that entire
stretch, including the part Phase 5 called the market's *recovery*, is classified **`onset`** here.

**The consequence is concrete: the onset regime contains both the rate's climb and its fall, and a
correlation over it will be diluted by exactly the averaging the split was introduced to prevent.**
The `recovery` regime holds only the days after the river came back up and the counter reset.

This is **not** a defect to patch during verification, and it is not something to re-tune after
seeing the numbers — that is precisely the move `CLAUDE.md § 18`'s last bullet forbids. It is a
**definition mismatch to be aware of when reading step 7**, and the honest resolutions are:

- read `onset` as **"the constraint is tightening"** rather than as "the rate is rising", which is
  what it actually means, and expect it to be diluted; or
- decide that the split wanted is **rate-of-change of the counter** rather than its sign, or a
  peak-relative split — **which is a modelling decision for the human** (`CLAUDE.md § 1`), and its
  own commit, with the current definition's results measured first so the change has a before.

**Measure it as built before changing it.** The `all` regime is scanned alongside the other two
precisely so the dilution is visible in the table rather than argued about.

> **THIS PLAYED OUT, AND MORE SHARPLY THAN THE WARNING ANTICIPATED — 2026-08-15.** It is not that
> `recovery` was diluted; it is that `recovery` is **almost empty**. For `days_below_p10` at Memphis
> the regime carries **1 to 7 observations at every horizon**, all refused as
> `insufficient_observations`, so **Phase 5's finding 2 was not tested in either direction.** The
> second honest resolution above — rate-of-change or a peak-relative split — is now a live modelling
> question for the human rather than a hypothetical one. **It still gets its own commit, and the
> current definition's results are now measured, so it has a before.** See `PHASE 6 — VERIFIED`.

### Still open, and unchanged by this commit

1. **Absolute operational thresholds are still a human decision awaiting a source.** The `p05`/`p10`/
   `p20` percentiles are stand-ins (`CLAUDE.md § 1`), and the sweep now makes their arbitrariness
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

### Phase 7 — promoted to the top of this section, now that the sweep has run

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