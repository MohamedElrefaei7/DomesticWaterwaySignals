# Findings

Every measured finding in this project, **by domain**, with its date and the method that produced
it. Split out of `CONTEXT.md` on 2026-08-17.

**The distinction this file is organized around.** A finding here is something that was
*measured* — against the live USGS or USDA API, against the database on the instance, or against a
browser. It is not a decision (`decisions.md`), not a phase's status (`phase-log.md`), and not a
plan. Where a measurement contradicted the plan, the measurement is recorded as it came back and
the contradiction is stated (`CLAUDE.md § 0`).

**Nothing here has been softened in the move.** A weak relationship is still recorded as weak, an
untested guard is still recorded as untested, and an unexplained sign is still recorded as
unexplained.

## Contents

- [A. Gauge data — coverage, record starts, retention, gaps](#a-gauge-data--coverage-record-starts-retention-gaps)
- [B. Storage and compression](#b-storage-and-compression)
- [C. USDA rates and lock movements](#c-usda-rates-and-lock-movements)
- [D. The thesis — first contact, and what deseasonalization did to it](#d-the-thesis--first-contact-and-what-deseasonalization-did-to-it)
- [E. The lead-lag sweep](#e-the-lead-lag-sweep)
- [F. The analog engine](#f-the-analog-engine)
- [G. The read API](#g-the-read-api)
- [H. The frontend](#h-the-frontend)
- [I. Deployment and the verification apparatus](#i-deployment-and-the-verification-apparatus)
- [J. Test apparatus and process](#j-test-apparatus-and-process)


---

## A. Gauge data — coverage, record starts, retention, gaps

### Measured against the live USGS API, 2026-08-13

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

---

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

---

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

---

### What this means for the project, stated plainly

- **Both labelled events are covered at all four sites.** The 2023 Baton Rouge gap ends
  **2023-08-15**, before the low-water period, so September–December 2023 is complete there.
- **2010–2026 is dense at all four sites** — roughly **sixteen years** of four-site coverage.
- **Pre-2004 baselines run on St. Louis alone.** Memphis's early segment ends in 1994 and the
  other two had not begun. This corrects the Phase 3.5 claim that pre-2010 history ran on "St.
  Louis and Memphis alone" — Memphis has nothing between 1994 and 2014.
- **The honest framing is one deep site, three shallow, and a corridor-wide window of about
  sixteen years.** The README must not imply eighteen clean years across the corridor.

---

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


---

## B. Storage and compression

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

### The chunk interval — logged as a tuning candidate, then acted on when it stopped being one

**The original note, kept verbatim because the escalation is the finding:** *986 chunks for 258,739
IV rows is the main drag on that table's ratio — a 7-day interval across 1990–2026 with only one
dense site leaves many sparse chunks carrying fixed per-chunk overhead. A 30-day interval would
likely improve both the ratio and planning. Chunk interval changes affect NEW chunks only, so this
is a deliberate future migration on a considered date, not a fix to slip in.*

**It was filed as a ratio problem and it was a correctness problem.** On 2026-08-18 a bare
`SELECT min(ts), max(ts), count(*) FROM gauge_readings_iv` failed outright:

```
ERROR:  out of shared memory
HINT:  You might need to increase max_locks_per_transaction.
```

A full-table query takes a lock per chunk plus one per index per chunk — roughly **2,000 slots**
against a cluster lock table of `128 × (25 + 0) = 3,200`, cluster-wide and shared. The project's
largest table was **not fully queryable**, and the heartbeat reported `CANNOT BE CHECKED` for it.
Intermittent, because the threshold is concurrency-dependent: it appeared, vanished after a
rebuild, and returned once the nightly backup added lock demand.

**Nothing in the original note was wrong. What it got wrong was the SEVERITY**, and it did so in a
predictable direction: the measurable consequence (a worse ratio) was visible in a table already
being measured, and the unmeasurable one (lock exhaustion at some unknown concurrency) was not in
any table at all. A finding logged against the number you happen to be looking at is filed at the
severity of that number.

Closed by migration `0027` (365-day interval, existing chunks consolidated by rewrite) and by
`max_locks_per_transaction = 512` (`infra/postgres/settings.py`).

### `gauge_readings_iv` before consolidation — 2026-08-18, the baseline 0027 is measured against

`SELECT * FROM hypertable_compression_stats('gauge_readings_iv')`, immediately before 0027:

| | Before compression | After compression | Ratio |
|---|---|---|---|
| Table | 56,451,072 B | 16,072,704 B | **3.51:1** |
| Index | 70,459,392 B | 16,072,704 B | **4.38:1** |
| Toast | 8,036,352 B | 8,036,352 B | 1.00:1 |
| **Total** | **134,946,816 B** | **40,181,760 B** | **3.36:1** |

986 chunks, 981 compressed. **The index was the larger share of the before-size** (70.5 MB against
56.5 MB of table data) and compressed hardest, which is the evidence for the "most of the win is in
indexes" claim above, stated as a split rather than as one headline ratio.

*(These differ slightly from the two-hypertable table above — 134,791,168 → 40,140,800 — because
that measurement was taken earlier, with fewer rows ingested. Both are real; neither supersedes the
other. The 2026-08-18 figures are the ones 0027's after-state must be compared against.)*

### After consolidation — NOT YET MEASURED

**No after-figures are recorded here because none have been taken.** 0027 is committed and
mutation-confirmed but has not been applied to the instance; the after-state comes from live
verification step 6 and is transcribed by a human, per `CLAUDE.md § 13` (a verifier never writes to
a tracked file).

**The direction is genuinely uncertain and is worth stating before the number arrives**, so that
whatever lands is read rather than rationalised. Fewer, larger chunks compress better per segment —
longer runs per `segmentby` group, less per-chunk framing overhead. But the *uncompressed baseline
shrinks too*, because much of that 134.9 MB is per-chunk fixed cost across 986 chunks that will not
exist afterwards. **The ratio could fall while the absolute size falls further**, and that would be
a win being reported as a regression by the headline number. Record both, and record the chunk
counts beside them.


---

## C. USDA rates and lock movements

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

---

### Finding 1 — the segment is `Lower Illinois`, not `Illinois River`

All seven measured, 1,180 rows each: `Cairo-Memphis`, `Cincinnati`, `Lower Illinois`, `Lower Ohio`,
`Mid-Mississippi`, `St. Louis`, `Twin Cities`.

The handoff document said "Illinois"; `0016` seeded `Illinois River`. **The API wins.** `0017`
replaces the CHECK, and all seven values in it are now measured — none is from a document.

---

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

---

### What this means for the thesis

- **Cairo-Memphis — the segment `CLAUDE.md § 7`'s output contract names — has 1,177 of 1,180
  weeks.** The target series is effectively complete.
- **The 2022 window's 26 missing rates are an ordinary winter closure, not the autumn low-water
  event.** The 2022 rate spike is intact in the data.

---

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

---

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

---

### Finding 2 — `tons = 0` appears on 8,218 records (31%). This is the finding that matters

USDA publishes explicit zeros **routinely**, on nearly a third of all records. So zero is the
*normal, published* way of saying "no grain moved through this lock this week" — and **a NULL is
therefore not the same statement, and not a rarer spelling of it.** The source has a way to say
"none moved" and uses it 8,218 times; the 108 records that say nothing at all are saying something
else.

---

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

---

### OPEN, UNEXPLAINED — the 2015–2016 concentration

96 of the 108 gaps fall in a two-year window, on three locks, flat across months. **The cause is
unknown and nothing in this commit acts on it.**

**Deliberately not built:** no `gauge_known_gaps`-style table for it, and those weeks are **not
excluded**. A 0.4% gap falling outside both labelled events (2022 and 2023) does not warrant the
machinery, and building the machinery would imply a conclusion about the cause that nobody has
reached. It is recorded here as an observation, and that is all it is.

---

### Rate nullity — winter navigation closure, as measured

774 of 8,260 on nearby, concentrated December–March on the upper segments: **Twin Cities 426**,
**Mid-Mississippi 303**, and **Cairo-Memphis only 3** — so the segment the output contract names has
**1,177 of 1,180 weeks**. Comparable shapes on the two forward horizons (**705** and **721** absent).

---

### Movement nullity — a reporting gap, not a closure, as measured

108 of 26,144 absent, confined to the three summary locks (**AK Lock 1 71, OH Olmsted 26, MS Locks
27 11**), 96 of them in 2015–2016, flat across months. Separately, **8,218 records (31%) report an
explicit zero**, which is the published way of saying nothing moved. **The two populations are never
summed** — `0018`'s whole argument, confirmed against the full dataset.

---

### NEW OBSERVATION — `lock_movements` is SPARSE, and it changes how features may use it

Reported zeros are the majority or near it at several locks: **MS Lock 15 has 1,434 zeros of 2,840
rows.** `lock_movements` is a **sparse per-commodity weekly series**, and any feature built on it
**must decide explicitly whether to aggregate across commodities before differencing.** Differencing
a per-commodity series that is half zeros produces a sequence of spikes and reversions that looks
like volatility and is mostly the reporting grain.

**Nothing in Phase 5 builds a movements feature**, precisely because that decision has not been
made. The feature layer is discharge-only for now; see `§ Up Next`.


---

## D. The thesis — first contact, and what deseasonalization did to it

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

---

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

---

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

---

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

---

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

---

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


---

## E. The lead-lag sweep

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

---

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

---

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

---

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


---

## F. The analog engine

### WHAT 2022-09-16 DRIVES, STATED AS MEASURED

**It drives the RANGE. It does not drive the median.** The distinction matters and the loose version
of this claim was corrected before it was written down:

- **`2022-09-16`'s outcome is `+270%`, and it is the maximum of the set.** The other four are `+18%`,
  `+10%`, `+5%` and `−48%`. **The range's upper bound IS its value** — remove it and the sentence's
  upper bound falls from `+270%` to `+18%`.
- **The median of the five is `+10%`, which is `2020-10-09`'s value.** Sorted, the log-returns run
  `−0.6509, 0.0451, 0.0968, 0.1664, 1.3076`; the middle one is `2020-10-09`. **2022 sits at the end
  of that ordering, not in the middle of it, so it cannot be what the median is reporting.**
- **It is simultaneously rank 1 by distance, at 7.414** — the nearest analog produced the most
  extreme outcome in the set.

**And that last point does not generalise, which is the observation worth keeping.** Ordered by
distance, the outcomes run `+270%, +10%, +5%, +18%, −48%`. **There is no monotone relationship between
similarity and outcome at all** — rank 3 moved less than rank 4, and the most distant analog produced
the largest move in the opposite direction. **The metric orders the analogs; it does not order what
happened next**, and nothing in the rendered sentence would tell a reader that.

---

### THE CLUSTERING LIMITATION, NO LONGER HYPOTHETICAL

The section below in the build record predicted this from Memphis's record start alone. **The instance
confirmed it exactly:**

| Query | Analog years | Span |
|---|---|---|
| 2022 | 2015, 2016, 2017, 2020 | **5 years** |
| 2023 | 2015, 2016, 2017, 2020, **2022** | **7 years** |

**Every analog behind both passes falls inside 2015–2022**, and the 2023 pass rests on an analog from
**the immediately preceding year** — consecutive drought years, which is the shared-causation case in
its purest form. `2022-09-16` is not an independent draw from `2023-09-19`: same fleet, same channel,
same multi-year regime, and in some cases the same contracts.

**So the 2023 result reads as "5 of 5 independent historical instances" and is nothing of the kind**,
and the one analog closest to being a repeat of the query is the one supplying the `+270%` upper
bound. `CLAUDE.md § 19` now carries the reading rule; **this is the run it was written against.**

---

### THE DISTANCES CLUSTER SO TIGHTLY THAT A CUTOFF WOULD BE ALL-OR-NOTHING

Step 2 asked whether they cluster or spread. **They cluster, hard, in both queries:**

| Query | Range of distances | Spread as a share of the mean |
|---|---|---|
| 2022 | 14.718 – 15.401 | **~4.5%** |
| 2023 | 7.414 – 7.693 | **~3.7%** |

**A cutoff placed anywhere between rank 1 and rank 5 in the 2023 query would have to discriminate at
the third significant figure**, which is not a threshold anybody can defend as "similar enough". The
practical answer this measurement gives is that **a cutoff here would admit all of them or none of
them**, and the useful question is not where to put one but why five conditions across seven years
all score within 4% of each other.

**The distances are NOT comparable between the two queries**, and the ~15 against ~7.4 above is not a
finding: z-scores are computed from the site's own history up to each query's `as_of`, so the two
queries scale their axes differently. Migration 0025 says so in the column comment, and it is the
first thing a reader would otherwise get wrong from the table above.

---

### THE 2022 PASS IS A "3 OF 4" PASS BEING REPORTED AS ≥70%

**This project has already written the argument down, for the sweep**, in the Phase 6 build record:

> Five because directional consistency is a fraction of folds and the gate wants ≥70%: with four
> folds the only achievable values are 0/25/50/75/100%, so the gate would be testing "3 of 4" while
> claiming to test 70%.

**That reasoning was applied to `walkforward.MIN_FOLDS` and was never applied to `MIN_ANALOGS`.** With
`n_analogs = 4` the achievable consistencies are exactly 0/25/50/75/100%, so the 2022 pass at 3 of 4
clears a 70% threshold that **cannot be evaluated at that resolution** — the same defect, in the same
project, one phase later.

**Nothing is changed on the strength of it.** `MIN_ANALOGS = 4` is `CLAUDE.md § 7`'s number and
raising it after seeing which queries pass is selecting a method that suited the answer — the move
`CLAUDE.md § 18`'s last bullet forbids. **It is recorded as an open question for the human**, and it
is the first one to settle before anything quotes the 2022 sentence.

---

### THE DIRECTION DISAGREES WITH THE SWEEP, AND THE ESTIMATES ARE WEAK

Two things that have to be said in the same breath as "the gate passed":

1. **Both sentences say the rate ROSE. The sweep's one surviving row has a NEGATIVE statistic
   (−0.137)** — more days below p10 going with a *lower* forward return. The two are not measuring the
   same quantity (a lag-0 correlation over all weeks at horizon 7, against a 21-day forward move from
   event onset), so this is **not a contradiction on its face** — but it is exactly the check
   `CLAUDE.md § 19`'s last bullet demands, and it is not resolved. `signal_q_value = 0.0446` rides on
   both query rows so this can never be read without it.
2. **A median of +7% and +10%, across ranges of −48%→+18% and −48%→+270%, is a weak claim wearing a
   confident sentence.** Both ranges span zero. The word "rose" is carrying 3-of-4 and 4-of-5
   majorities over sets whose middle value is a single-digit percentage.

**The gate passing is a fact about the gate, not evidence for the thesis.**


---

## G. The read API

### 3. FINDING — "JOB OVERDUE" AND "DATA STALE" ARE DIFFERENT QUESTIONS, AND THEY DISAGREED

Every job reported `overdue: true` while `barge_rates` and `lock_movements` both reported
`stale: false` in the `data` block. **That is correct behaviour, and the reason is worth stating
precisely because the response looks self-contradictory at a glance:**

- **`overdue` answers "has this been RUN recently."** It is measured from `job_runs` — the most
  recent `success` row's `finished_at`, never the most recent row of any status — against that job's
  own `overdue_after` from the cadence table.
- **`stale` answers "is what is ALREADY STORED still inside its freshness window."** It is measured
  from the data — `MAX(week_ending)` on the table — against that entry's own `max_staleness`.
  `CLAUDE.md § 4`: liveness is measured from the data, never from the process.

Two clocks, two sources, two questions. **A table can be perfectly fresh while the job that fills it
has not run, and that is exactly what happened here.**

**The mechanism is the null branch, and it is not the threshold arithmetic.** `last_success` came
back **`null`** on both USDA jobs, with `age_seconds: null` beside it. `app/orchestration/
heartbeat.py:297` computes `overdue=(age is None or age > entry.overdue_after)` — **a job with no
successful run on record is overdue rather than quiet** (`CLAUDE.md § 12`), regardless of any
threshold. Meanwhile the tables hold rows a backfill CLI landed (`CLAUDE.md § 14`: a backfill is a
CLI a human runs, never a scheduled job), so `MAX(week_ending)` sits inside the 10-day window and
`stale` is correctly false. **The scheduled job has never recorded a success; the data it would have
written is present anyway.**

**This was very nearly written up with the wrong explanation, and the wrong one is worth recording
so it does not get re-derived.** The natural account — "the freshness window is longer than the gap
since the job last ran" — is a sound general principle and is **false for these two jobs**, because
their thresholds run the other way:

| | `overdue_after` | `max_staleness` |
|---|---|---|
| `usda_rates_ingest` / `barge_rates` | **14 days** (1,209,600 s) | **10 days** (864,000 s) |
| `usda_movements_ingest` / `lock_movements` | **14 days** | **10 days** |

The freshness window is the **shorter** of the two. Under that pairing a gap short enough to leave
the data fresh (< 10 days) is also short enough that the job is not overdue (< 14 days), so the
arithmetic cannot produce the combination that was observed. And `app/orchestration/cadence.py:193-199`
picked 14 days *deliberately* to make that ordering hold — two intervals rather than the three every
other entry uses, because "a three-week job threshold would let the DATA check speak twice before the
JOB check spoke once, which inverts which of the two an operator reads first." **The design intends
data-stale to fire before job-overdue on these two jobs.** An explanation resting on the reverse
would have contradicted the code while sounding entirely reasonable, and `last_success: null` is what
settled it.

**`degraded: true` in this run reflects real inactivity, not a defect in Phase 8.** No scheduler
process has been running continuously across sessions, so no ingest job has a recent success and
several have none at all. The endpoint is reporting the instance accurately. **The 200 alongside it
is the decision, not an oversight** (`CLAUDE.md § 20`): an uptime monitor that goes red on a stale
ingest job is indistinguishable from one that goes red because the API is down, and `degraded` is a
field so a monitor can alert on the field.

---

### THE ROUTE-TABLE TEST WAS VACUOUS WHEN FIRST WRITTEN, AND THAT IS THE FINDING OF THIS COMMIT

`for route in app.routes: route.methods` is the obvious way to assert "no non-GET route". On
Starlette 1.6 **it returns only `{GET, HEAD}` from `/docs` and `/openapi.json`**: `include_router`
inserts one `_IncludedRouter` object per router and the real endpoints live one level down behind
`original_router`. So the test would have asserted a property of a set containing **none of this
project's routes**, passed, and **stayed green after somebody added a POST**.

Caught by printing the walk's output rather than trusting its result. `CLAUDE.md § 2`'s theme 2, and
the same shape as the ingress test that passed because the set it constrained was empty. The walk
now recurses, returns paths as well as methods, and **the test asserts it reached all eight
documented endpoints before it asserts anything about their methods** — a walk that stops early
fails on the paths, not silently on an empty set.


---

## H. The frontend

### 1. THE TOOLCHAIN GAP — NODE WAS NEVER PINNED ANYWHERE, AND NOTHING FAILED UNTIL IT DID

**The instance ran Node 18.19.1.** Vite 8, Vitest 4 and rolldown 1.2 all require Node ≥ 20, several
of them ≥ 22. `npm ci` warned on **every package** with `EBADENGINE` and **exited zero** — the
install completed, the tree was there, and nothing in the output said the toolchain would not run.
The failure arrived one command later, from `vite build`, as an import of `styleText` from
`node:util` — a symbol that does not exist before Node 20. **Resolved by installing Node 22 via
`nvm`,** after which the build, the 37 tests and `tsc --noEmit` all behaved as they do offline.

**NODE HAS NEVER BEEN PINNED OR PROVISIONED ANYWHERE IN THIS PROJECT.** Phase 2 pinned Python and
pinned Docker by exact `pkg=version` with an `apt-mark hold` (`CLAUDE.md § 10`); every container
image is pinned by digest and resolved on the machine that runs it (`§ 5`). **Node is in none of
it** — not in `CLAUDE.md § 5`, not in a provisioning script, not in `package.json` as an `engines`
field, not in an `.nvmrc`. The frontend was built offline on a laptop that happened to have a recent
Node, and the first clean machine to try it was the instance.

This is `CLAUDE.md § 2`'s theme 1 in a new place: **a layer reported success — `npm ci` exited zero —
while the thing downstream of it could not run.** An `EBADENGINE` warning on every package is
the loudest possible form of that warning and it is still only a warning. **Whether Node becomes a
provisioned, pinned dependency the way Docker is, is now an open item at the top of `§ Up Next`.**

---

### 4. INVESTIGATION — THE SIGN DISAGREEMENT IS **EXPECTED. DIFFERENT QUANTITIES.** NOT A BUG.

**The question.** The rendered conclusion for Memphis / 2022-10-11 says the barge rate **rose** in the
four historical analogs (median `+7.4%`, range `−47.8%` to `+18.1%`). The Signals view's one surviving
row — `days_below_p10`, Memphis, horizon 7, lag 0, regime `all` — carries `statistic: −0.137`. The
frontend surfaced this as an open question rather than hiding it, which was correct.

**THE VERDICT, IN ONE PARAGRAPH.** The sweep and the engine are not two measurements of one thing that
disagree; **they are measurements of two different things, and both are correct.** The sweep asks: across
every week of the record, does a *higher counter* — more consecutive days already spent below the 10th
percentile — go with a *bigger or smaller* barge-rate move over the following week? Its answer, −0.137,
is that further into a low-water spell, the coming week's move is smaller. The engine asks a different
question entirely: on the handful of days when the river *first crossed* into low water, what did the
rate do over the next 21 days? Its answer is that it rose. **A slope that runs downhill across the
whole range says nothing about the height at the bottom end of it** — a line can fall steadily and
still sit above zero at its left edge. The sweep is reporting the slope; the engine is reporting the
value at one end.

**And "one end" is exact rather than figurative, which is what turns this from a plausible story into
a proof.** It follows from the code, with no data required:

- `events.is_entry` fires when the counter is `>= ENTRY_RUN_LENGTH_DAYS`, and
  `parameters.ENTRY_RUN_LENGTH_DAYS = 1`.
- `events.collapse` opens a new event only when the previous detection was more than
  `MIN_EVENT_SEPARATION_DAYS = 90` days earlier, so an event's `start` is a detection whose previous
  day was **not** a detection.
- `thresholds.days_below` increments the run length by **exactly one per day** and can only reach 1
  from a definite 0; from a `None` it stays `None`, and a `None` is not a detection.

**Therefore every analog `event_start` carries `days_below_p10 == 1`, with the previous day at exactly
0.** Not approximately, not usually — by construction. The four analogs behind the 2022 sentence
(`2015-10-14`, `2016-11-24`, `2017-09-25`, `2020-10-09`) are all anchored at the single lowest
non-zero point of the axis the sweep is regressing along, and `regimes.classify` labels each of them
`onset`, since 1 > 0 on contiguous days. **The engine samples one point of the sweep's predictor
range; the sweep's statistic is a slope over the whole of it.**

**Neither module has a sign-convention error, and this is checkable rather than assumed.** There is
exactly **one** implementation of a return in this project — `app/features/targets.forward_log_return`,
`ln(forward / now)`, positive when the rate rose. `app/analogs/outcomes.py` **imports that function**
rather than reimplementing it (its docstring says why, citing `CLAUDE.md § 17`), and the sweep's
targets are built from the same function. **A sign disagreement between the two could not survive a
single shared implementation**, and `statistics.pearson` applies no negation to anything. There is no
line to name as the defect because there is no defect.

**A CORRECTION TO THE HYPOTHESIS AS IT WAS PUT TO THIS SESSION, AND IT MATTERS.** The proposed
mechanism was that the sweep's sample contains many *high, falling* counter values — the recovery
condition — dragging a same-week correlation negative. **The counter almost never falls.** It is a
run-length: it climbs by one a day for the length of the spell and then resets to 0 in a single step.
Phase 5's own measured trajectory, quoted verbatim in `app/signals/regimes.py`, is the evidence:

    0, held for eleven weeks      rate drifting   335 -> 656
    2 -> 9 -> 16 -> 23            rate climbing   925 -> 1,428 -> 2,427 -> 2,812
    30 -> 37 -> 44 -> 51 -> 58    rate FALLING back from the 2,812 peak

**The counter rises monotonically 2 → 58 across the whole event.** The rate rises and then falls, but
the counter never does. So the population pulling the `all` correlation negative is **high and still
rising**, not high and falling — and `regimes.classify` calls every one of those days `onset`, the
same label it gives the analog anchor points at counter 1. **This is why Phase 6's own proposed
explanation of the negative sign could not be tested by Phase 6's own regime split**, and why that
block was right to record it as an unexplained sign rather than accept it: the split is on the
counter's *direction*, and the two populations that produce the disagreement differ in the counter's
*level* while sharing a direction. It also explains, without any new measurement, why the `recovery`
regime carries **1 to 7 observations at every horizon** — it collects only the single reset day at
the end of each spell.

**THE UI'S DISAGREEMENT WARNING IS CORRECT TO KEEP SHOWING, AND THAT IS NOT A CONCESSION.** Two true
statements about different quantities still read as a contradiction to anybody who did not derive
the above, and the sentence is the unit that gets quoted. The band names the disagreement in the
reader's own terms; nothing in this section makes it safe to remove. **Removing it is one of the
three human decisions, and this investigation does not settle any of them** — it settles only that
there is no code defect underneath the third one.

**WHAT WAS NOT MEASURED, AND IT IS CONFIRMATORY RATHER THAN DECISIVE.** The check that would put a
number on the mechanism — the distribution of `days_below_p10` across the sweep's 616 weekly
observations, and the sub-correlation among the low-counter weeks alone — **was not run.** An SSM
port-forwarding session to the instance was established and **the database answered the auth
handshake**, so the DB is up; **the API and Caddy are not running**, and querying Postgres directly
needs a credential this agent does not handle (`CLAUDE.md § 1`). The queries are owed, listed in
`§ Up Next`. **They would characterize the slope; they cannot change the verdict**, because the
verdict rests on a property of the event definition rather than on a property of the data.

---

### 5. THE 348 FIGURE IS CORRECT, AND 271 WAS NEVER AN ESTIMATE OF IT

`6,966 × 0.05 = 348.3`, so **"roughly 348 significant results on pure noise" is the right arithmetic
for this grid at α = 0.05**, and the Signals view states it correctly.

**The 271 in the Phase 6 block is not a competing estimate for a different grid size — it is a
measurement, on this same 6,966-pair grid.** 348 is what chance *predicts*; 271 is what the sweep
*observed* clearing the unadjusted threshold, before Benjamini-Hochberg took it to 1. The two sit in
the same table in that block, three lines apart, and it already says so: "a grid of 6,966 independent
tests yields ~348 significant results on pure noise; this grid yielded 271." **There is no
discrepancy and no stale number. Expected versus observed, both for the same grid.** Recorded here
so nobody re-flags it.

**One thing about how 348 is rendered, flagged and not fixed.** In `src/views/Signals.tsx` the grid
size is interpolated from the response (`count(run.grid_size)`) while **348 is a hardcoded literal
beside it**. They agree today. They stop agreeing the first time the grid changes size, and the
sentence would then read "a grid of 7,500 tests yields roughly 348" — a number that no longer traces
to the grid named in the same breath, which is `CLAUDE.md § 7`'s reproducibility rule failing at the
last inch. **Not fixed here:** the honest fix is either an API field or wording that states α and
lets the reader do the multiplication, and computing it in the component is a derived statistic
(`CLAUDE.md § 21`). It is in `§ Up Next`.


---

## I. Deployment and the verification apparatus

### 6. A REAL GAP FOUND IN `verify/preflight.py`, NOT FIXED, BECAUSE `verify/` WAS OUT OF SCOPE

**`verify/preflight.py` gate 1 checks the FIRST `image:` line in `docker-compose.yml` and nothing
else.** `read_image_reference()` is a deliberate regex rather than a YAML parse, so that
`--write-digest` can rewrite the line without a round-trip discarding every comment. That was
unambiguous when the file had one service.

**It now gates one image reference out of three**, and which one is decided by file order.
`timescaledb` is still first, so the gate still checks the database — but nothing in `verify/`
notices the caddy image, and nothing in `verify/` looks at a Dockerfile `FROM` line at all.
`--write-digest` will not write the caddy line either; **that digest is hand-edited, which is the
exact thing that has already failed twice on the line it does write.**

Stopgaps in place, and they are stopgaps: `tests/deploy/test_compose_shape.py` asserts every image
across all four services is digest-pinned and tagged, `test_dockerfiles.py` asserts the same for
every `FROM` and that a multi-stage build's stages agree on one digest, and
`test_the_compose_file_still_names_timescaledb_first` is the tripwire for a reorder silently
re-pointing gate 1. **These are offline structural checks; gate 1 is the one that asks Docker. The
right fix is a preflight that walks every image reference in the stack, and it belongs to whichever
commit is allowed to touch `verify/`.**

> **CLOSED 2026-08-17.** Gate 1 now enumerates every `image:` line in `docker-compose.yml` and
> every `FROM` line in every Dockerfile — **six references across three files** — and reports each
> by file, line and stage. `--resolve-digest` and `--write-digest` handle all of them, resolving
> each tag exactly once so two stages of one build cannot receive two different resolutions of the
> same tag. A reference carrying a tag and no digest is a failure rather than a pass, and the
> enumeration itself is asserted: a walk finding no Dockerfiles, or a compose file with no `image:`
> line, is a FAILURE rather than a clean run over an empty set.
>
> `test_the_compose_file_still_names_timescaledb_first` was **removed**, because the property it
> guarded no longer exists: service order in `docker-compose.yml` is no longer load-bearing. A test
> named for a rule the system has stopped depending on is a green check that teaches the next
> reader something untrue.
>
> **The generalization went into `CLAUDE.md § 22`:** a verification gate that checks a subset of
> what it names is worse than no gate, because it reports the whole set as verified. Any gate over
> a collection enumerates the collection.

### Multi-perspective ACME validation is a reachability measurement, not just an issuance event — 2026-08-17

Let's Encrypt validated the HTTP-01 challenge from **five distinct IPs** (23.178.112.105,
13.59.181.48, 44.247.39.78, 16.171.0.27, 13.212.174.231), each receiving a 200 from Caddy, in
**≈3.4 seconds** total. ACME account `acct/3638088191`.

**What that measures beyond "the certificate issued":** DNS resolution and port-80 reachability from
five vantage points on three continents. The operator can confirm their own vantage point with a
`curl`; they cannot confirm anybody else's. A single-perspective success is compatible with a
route that works from one network and not from the rest of the internet, and multi-perspective
validation is the only check in this project that has ever crossed that boundary.

### A DNS record fronted by a CDN must be DNS-only during ACME HTTP-01 issuance — 2026-08-17

The `bargeanalysis.com` A record was created at Cloudflare **proxied** (orange cloud). A proxied
record resolves the domain to **Cloudflare's** IPs, so the HTTP-01 challenge is answered by
Cloudflare and never reaches the origin — while `dig` returns an answer, the site appears to work,
and the only symptom is a failed validation. **Failed issuance is what Let's Encrypt rate-limits,
per domain per week.**

Switched to DNS-only before Caddy was started for the first time, so nothing was consumed.

**Proxying remains a legitimate future option** — it would partly address the missing per-IP rate
limit — but it requires either DNS-01 issuance or Cloudflare origin certificates, and it was
correctly not tangled into the one attempt whose failure costs days. Now a contract line in
`CLAUDE.md § 0`'s process notes.

### A silent drop and a connection refused look identical in a summary and mean different things — 2026-08-17

From outside the instance, `nc` to **5432** and to **8000** **hangs**. It does not report
"connection refused."

- **Refused** means a packet reached something that answered with a RST — the port is closed, but
  the host is reachable and something is deciding.
- **Hung** means the packet was dropped with no reply at all.

**The hang is the externally-visible proof of the `DOCKER-USER` terminal `-i ens5 -j DROP` rule.**
The housekeeping list has recorded since provisioning 3 that this rule *could not be observed from
outside*, because the security group blocks every port the chain would drop, and widening the
security group to prove it was out of scope. Phase 10 publishes 80 and 443 through that chain,
which is what finally made the terminal rule observable — and it was observed. **A check reporting
only "the port is not open" would have passed identically in both cases and proved nothing about
the chain.**

**Published ports across the running stack, read from `docker compose ps`:** `caddy` 80 and 443
only; `api` shows `8000/tcp` with no host binding; `timescaledb` shows `127.0.0.1:5432` **from the
out-of-repo dev override only**.


---

## J. Test apparatus and process

### 2. PROCESS NOTE — THREE THINGS STOOD BETWEEN A GREEN BUILD AND A PAGE, AND NONE WAS THE CODE

Recorded as process rather than as a technical finding. Reaching a live page required, in order:

1. **The Node upgrade above.**
2. **Distinguishing the Vite dev server from a stray `python -m http.server`** left bound to the same
   port by an earlier attempt. The port was occupied, so the new server did not bind it, and the
   page that came back was the old process serving a directory listing — a server was running, a
   page was served, and neither was the one under test.
3. **Recognizing that a port-forwarding SSM session and an interactive SSM session are different
   session types.** Several plain `aws ssm start-session` shells had accumulated over the session
   with **no forwarding session among them**, and a list of live sessions looks identical either
   way. Nothing was wrong with any of them; none of them forwarded a port.

**`CLAUDE.md § 0` now carries the contract line** — the two session types do not substitute for each
other, and `lsof -i :<port>` is checked before a freshly started server is assumed to have bound the
port anybody expects. See the note there about where it landed.

---

### Mutation confirmation — twelve rows, and the harness bug is the finding

**All twelve watched red for the NAMED test and restored, suite green afterwards.**

**The first run reported all twelve red and it was meaningless.** `--reporter=basic` was removed in
Vitest 4 and is now treated as a custom reporter *module path*, so every invocation died in the
loader with a non-zero exit and **no test names in the output at all**. Twelve rows exiting non-zero
for a reason having nothing to do with the mutations is **indistinguishable from twelve confirmed
guards** unless the harness checks *which* test failed — which is `CLAUDE.md § 0`'s "a mutation that
goes red for the wrong reason is not a confirmed guard", reached by a route that commit did not
anticipate: not a compile error in the mutated file, but a broken flag in the harness itself.

The harness names the expected test per row, greps the verbose output for it, and reports
`WRONG TEST` otherwise — which is what it did, twelve times, instead of reporting success. It also
snapshots **file contents** and restores in a `finally`, per the Phase 8 finding about stale
baselines.

---

### Mutation confirmation — 18 runs for 16 rows, and the two extra runs are the interesting part

All sixteen rows watched **red and restored**, `__pycache__` cleared between the restore and the
re-run and `PYTHONDONTWRITEBYTECODE=1` set. Two rows needed a second form, and in both cases the
pair says something the single form would have hidden.

**Row 2 — "make the refusal shape include `median_pct: null`" — is TWO different failures:**

| Form | Test 2 (key absent) | Test 3 (numeric walk) |
|---|---|---|
| `median_pct: float \| None = None` | **RED** | green |
| `median_pct: float = 0.0` | **RED** | **RED** |

A `null` is not a numeric leaf, so the recursive walk cannot see it. **Neither test subsumes the
other**: test 2 catches the key arriving empty, test 3 catches it arriving with a number in it — and
the second is the one that catches a field added three levels down that nobody thought to look at.
The brief's table lists row 2 against both; it reaches both only in the second form.

**Row 16 — "reimplement the gate threshold inside a route" — is also two failures:**

| Form | Test 8 (structural) | Test 17 (behavioural) |
|---|---|---|
| `if result.gate.n_analogs >= 4 and ... >= 0.70 * ...` | **RED** | green |
| route hardcodes the refusal `reason`, ignoring the engine | green | **RED** |

The first form is a reimplementation that happens to be **behaviourally identical** on the fixtures
— which is exactly why the structural test exists, and exactly why a behavioural test alone would
not have caught it. The second changes the answer without writing a threshold down, which is why
the grep alone would not have caught it either. **The pair is the guard; neither half is.**

**And one procedural finding, from a harness bug rather than the code:** the first mutation run
crashed mid-cycle (it tried `git checkout --` on files not yet tracked), leaving row 1's appended
POST route in place. The second run then snapshotted the **already-mutated** file as its baseline
and "restored" to it — reporting red after the restore, which reads exactly like a test that is
broken. `CLAUDE.md § 0` names the stale-bytecode version of this; **a stale BASELINE produces the
identical symptom**, and the harness now snapshots file contents rather than relying on git.

---

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

### Mutation notes — two rows needed a second pass

- **Row 1 (blanket `record.get`)** first went red with a `NameError`, because deleting the
  `optional_field` call left the `tons=` expression referencing a name that no longer existed. That
  proves only that the test runs (`CLAUDE.md § 0`). Redone as a coherent `tons=record.get(...)`
  implementation, it went red on test 3's own assertion — `DID NOT RAISE`.
- **Row 6 (combined figure)** was first applied as "stop counting nulls", which is a different
  mutation. Redone as the actual merge (`if row.tons is None or row.tons == 0`), it produced
  `('MS Locks 27', 3, 2, 0)` — the two populations added together into the single figure the decision
  forbids — and went red on the structural assertion.

---

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

---

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

---

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

### Mutation confirmation for the widened image gate — five rows, 2026-08-17

All five watched **red for the named test**, on that test's own assertion, then restored, with
`__pycache__` cleared between the restore and the re-run and the suite green afterwards. The
harness names the expected test per row and **fails the row if a different test failed** — Phase 9's
finding, applied.

| Mutation | Named test | The assertion it died on |
|---|---|---|
| Gate 1 reads only the first `image:` line | `test_gate_one_enumerates_every_image_reference_in_the_stack` | `the walk found ['timescale/timescaledb:...'], expected [...]` — one of two |
| Gate 1 skips `Dockerfile.*` `FROM` lines | `test_gate_one_covers_every_dockerfile_from_line` | `Dockerfile.api: the walk found None, expected ['python:3.12-slim@...', ...]` |
| Treat a tag without a digest as a pass | `test_a_tag_without_a_digest_is_a_failure` | `'timescale/timescaledb:2.26.2-pg16' was accepted with no digest - a floating tag passed the pin gate` |
| Collapse the all-zero message into the malformed-digest message | `test_all_zero_digest_is_reported_as_the_placeholder` | `assert 'PLACEHOLDER' in "observed: 'sha256:0000...' ... this digest is malformed..."` |
| `--write-digest` rewrites only the first reference | `test_write_digest_rewrites_every_reference_not_just_the_first` | `docker-compose.yml:6 still carries the placeholder - the rewrite reached only some of the references` |

**Two rows turned other tests red as well, and that is not a wrong-reason failure.** The
first-`image:`-line mutation also reddened the write-all test, and the Dockerfile-skip mutation also
reddened the write-all test and `test_the_repos_own_files_pass_the_image_gate`; the tag-without-digest
mutation also reddened `test_every_failure_reports_an_observed_value`. In every case the **named**
test failed on the assertion the row is about, which is what `CLAUDE.md § 0` requires. Extra reds
mean the property is guarded in more than one place, not that the guard is confused.
