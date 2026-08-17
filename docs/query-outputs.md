# Query outputs

The verbatim output of queries this project has run, kept **as it came back**. Split out of
`CONTEXT.md` on 2026-08-17.

**Why verbatim matters here specifically.** `CLAUDE.md § 7` requires every number in the README, the
UI or the résumé to be reproducible from a query. This file is where the reproduction is checked
against. A paraphrase of a result set is a number nobody can check; anchor points without the rows
between them are what this project has already refused to interpolate twice (`CLAUDE.md § 4`: when
data is lost, record the loss — never synthesize a replacement).

**DEBT 1a IS STILL OPEN and it is the reason this file is thinner than it should be.** See the
section at the bottom.

## Contents

- [Phase 4 — the USDA catalog, measured](#phase-4--the-usda-catalog-measured)
- [Phase 6 — the sweep tables](#phase-6--the-sweep-tables)
- [Phase 7 — the two gate results, verbatim](#phase-7--the-two-gate-results-verbatim)
- [Phase 8 — the six live API responses](#phase-8--the-six-live-api-responses)
- [Phase 10 — the stack on the public internet](#phase-10--the-stack-on-the-public-internet)
- [Owed queries](#owed-queries)
- [DEBT 1a — the four thesis CSVs, still not pasted in](#debt-1a--the-four-thesis-csvs-still-not-pasted-in)


---

## Phase 4 — the USDA catalog, measured

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

---

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


---

## Phase 6 — the sweep tables

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

---

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


---

## Phase 7 — the two gate results, verbatim

### The two gate results, verbatim

**2022 —** `python -m app.analogs.engine --as-of 2022-10-11 --site 07032000 --explain`

```
parameters 45600c6d05c0   k=10 window=21d
detections 77 raw -> 5 collapsed events
gate: passed  analogs=4 consistent=3 incomplete=0
sweep: best q = 0.0446 (run 1)

Mississippi discharge at Memphis has fallen 16,000 cfs in 14 days and is now 83,967 cfs
below the 12-year seasonal median. The last 4 times discharge moved like this, the
Cairo-Memphis barge rate rose, -48% to +18% within 3 weeks - median +7%, 3 of 4
directionally consistent.

rank  event_start   distance
   1  2020-10-09     14.718
   2  2016-11-24     15.006
   3  2017-09-25     15.084
   4  2015-10-14     15.401
```

**2023 —** `python -m app.analogs.engine --as-of 2023-09-19 --site 07032000 --explain`

```
parameters 45600c6d05c0   k=10 window=21d
detections 161 raw -> 6 collapsed events
gate: passed  analogs=5 consistent=4 incomplete=0
sweep: best q = 0.0446 (run 1)

Mississippi discharge at Memphis has fallen 42,000 cfs in 14 days and is now 120,333 cfs
below the 11-year seasonal median. The last 5 times discharge moved like this, the
Cairo-Memphis barge rate rose, -48% to +270% within 3 weeks - median +10%, 4 of 5
directionally consistent.

rank  event_start   distance
   1  2022-09-16      7.414
   2  2020-10-09      7.501
   3  2016-11-24      7.563
   4  2017-09-25      7.599
   5  2015-10-14      7.693
```

**The 2023 `analog_matches` breakdown, in full:**

```
 event_start | outcome_log_return  | pct
-------------+---------------------+-----
 2022-09-16  |   1.307554871280256 | 270
 2020-10-09  | 0.09679505602470496 |  10
 2016-11-24  | 0.04512043528046964 |   5
 2017-09-25  | 0.16644820763766438 |  18
 2015-10-14  | -0.6509147176885973 | -48
```


---

## Phase 8 — the six live API responses

### 2. THE SIX LIVE RESPONSES

Key evidence only. The full bodies are in the session transcript; what is recorded here is the field
or two in each that carries the property.

| Request | Came back | The evidence |
|---|---|---|
| `/api/health` | `degraded: true` | every job `overdue: true`; `barge_rates` and `lock_movements` `stale: false` — see § 3 |
| `/api/conclusion?site_id=07032000&as_of=2022-10-11` | `gate: "passed"` | `analogs: 4`, `consistent: 3`, `median_pct: 7.35`, **and `sweep.passing_pairs: 1` / `sweep.scanned_pairs: 6966` in the same body** |
| `/api/conclusion?site_id=07032000&as_of=2022-09-06` | `gate: "no_current_event"` | `median_pct`, `range_pct`, `matches` **absent** — not null |
| `/api/rates?segment=Twin Cities&horizon=nearby&start=2022-01-01&end=2022-03-31` | 11 rows read | 5 of them `pct_of_tariff: null`, then `850.0` on `2022-03-22` |
| `/api/gauges/07032000/series?start=2022-09-01&end=2022-11-01` | `total: 62`, 62 rows | no truncation; the 2022 event's own discharge trace |
| `/api/rates?start=2000-01-01&end=2026-01-01` | **422** | the five-year span limit, enforced over HTTP |

**The +7% is never returned without its sweep context.** `median_pct: 7.35` and `passing_pairs: 1`
of `scanned_pairs: 6966` arrived in one body, on the passing shape, at the first request that ever
stated Phase 6's denominator through the API. 1 reads as a finding; 1 of 6,966 reads as the top of a
distribution, and a reader who screenshots the median gets the denominator in the same rectangle.

**The refusal was read in full by eye, and it contains no key that could be misread as an
estimate.** Not `median_pct: null`, not a zero three levels down, not a debug block — the keys are
absent. The recursive-walk test asserts this offline against fixtures; this is the same property
confirmed against real data on a real date, which is a different check and the one `CLAUDE.md § 20`
asks for. `no_current_event` came back as its own shape rather than as `refused`: a quiet river is
not a coverage problem.

**The nulls survived serialization.** Five of the first eleven Twin Cities rows carry
`pct_of_tariff: null` — winter closure, matching Phase 4's measurement that 426 of that segment's
records have no rate and that 661 of the 774 absent nearby rates fall in December–March — and then
`850.0` lands on `2022-03-22` as ice-out resumes. **Not one of the five arrived as `0`.** A zero
there would have been a week when barge freight was free, well formed, correctly typed, and
indistinguishable from a real reading on any chart.

**The series window happens to show the event the analog engine detected on the same dates.** 62
days, `total: 62`, no truncation: discharge from ~250,000 down to a trough of ~147,000–149,000
around 10-17 to 10-21, then recovery. The conclusion request above ran `as_of=2022-10-11` and found
4 analogs; this is the trace underneath that answer, served by a different endpoint, and the two
agree about when the river was low. Nothing was tuned to make them agree — the same rows fed both.

**The span limit is enforced over HTTP, not only in the route-level unit test.** `MAX_SPAN_YEARS = 5`
(`app/api/dependencies.py:48`) rejected a 26-year request with a 422 rather than serving it or
clamping it. A clamp is the failure mode that matters here: a client asking for 26 years and
receiving 5 has no way to tell that from a filter that matched 5.


---

## Phase 10 — the stack on the public internet

*Taken 2026-08-17, from a laptop rather than from the instance. That distinction is the whole point
of this section: everything Phase 8 and Phase 9 recorded came back through an SSM tunnel from one
IP, and nothing before this had ever been answered to a stranger.*

### Certificate issuance, first attempt, 02:41 UTC

Let's Encrypt validated the HTTP-01 challenge from five distinct IPs, each receiving a 200 from
Caddy. Whole exchange ≈3.4 seconds. ACME account `acct/3638088191`.

```
23.178.112.105    200
13.59.181.48      200
44.247.39.78      200
16.171.0.27       200
13.212.174.231    200
```

### `curl -sI https://bargeanalysis.com`

```
HTTP/2 200
content-security-policy: ... font-src 'self' ...      <- no CDN exception
referrer-policy: no-referrer
alt-svc: h3=":443"; ma=2592000
```

The absence in that header set is the evidence: **`font-src 'self'` with no font-CDN host**, which
is Phase 9's self-hosted `@fontsource` decision arriving at the edge exactly as intended.

### `curl -sI http://bargeanalysis.com`

```
HTTP/1.1 308 Permanent Redirect
location: https://bargeanalysis.com/
```

### `curl -s https://bargeanalysis.com/api/health`

The same body Phase 8 verified through a tunnel, now over TLS: `degraded: true`, every job
`overdue: true` with `last_success: null`, and `barge_rates` / `lock_movements` `stale: false`. See
`findings.md § G` for why those two answers disagree correctly.

### What is NOT reachable — and the shape of the refusal is the finding

```
nc -zv bargeanalysis.com 5432     ->  hangs
nc -zv bargeanalysis.com 8000     ->  hangs
curl -s https://bargeanalysis.com/api/health   ->  200, while both of the above hang
```

**Hangs, not "connection refused."** A silent drop rather than a RST, which is what a correctly
scoped firewall looks like from outside and is the externally-visible proof of the `DOCKER-USER`
terminal `-i ens5 -j DROP` rule. `findings.md § I` has the distinction in full.

### Published ports across the running stack

```
caddy          0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
api            8000/tcp                      <- no host binding
timescaledb    127.0.0.1:5432->5432/tcp      <- from the out-of-repo dev override ONLY
frontend-build (exited 0)
```

### Reboot survival

`dws-stack.service` enabled, `sudo reboot`, and the stack came back unattended: unit
`active (exited)`, `docker compose up -d` executed at **02:56:26** (≈30 s after boot), all three
long-running containers up, and `https://bargeanalysis.com` answering 200 **before the operator
reconnected**.


---

## Owed queries

*Not outputs — queries whose output this project owes itself. They live here rather than in `../CONTEXT.md` so the SQL is beside the outputs it will join.*

   ```sql
   -- The counter's distribution across the weeks the surviving row was measured over.
   select width_bucket(f.value, 0, 60, 12) as bucket,
          count(*), min(f.value), max(f.value), avg(t.value) as mean_fwd_return
     from features f
     join targets  t on t.week_ending = f.date
    where f.feature_name = 'days_below_p10' and f.site_id = '07032000'
      and t.target_name  = 'cairo_memphis_nearby_log_return' and t.horizon_days = 7
      and f.value is not null and t.value is not null
    group by 1 order by 1;

   -- The low-counter weeks alone, which is the population the engine's anchors sit in.
   select count(*), corr(f.value, t.value)
     from features f
     join targets  t on t.week_ending = f.date
    where f.feature_name = 'days_below_p10' and f.site_id = '07032000'
      and t.target_name  = 'cairo_memphis_nearby_log_return' and t.horizon_days = 7
      and f.value between 1 and 7;
   ```

   **These join on an exact date and the sweep does not** — `sweep.align_lagged` takes the last
   feature date on or before the anchor — so they characterize the sample without reproducing
   `−0.137` exactly, and a small difference between the first query's full-sample correlation and
   the stored statistic is expected rather than a discrepancy to chase.

   **A positive or near-zero correlation in the second query beside the full sample's −0.137 is the
   measured form of the whole argument.** It is not needed to reach the verdict and must not be run
   as a search for one — if it comes out otherwise, that is a finding to record, not a result to
   re-cut. `python -m app.signals.sweep` is not re-run for this.


---

## DEBT 1a — the four thesis CSVs, still not pasted in

**Open since Phase 4. Carried through Phases 5, 6, 7, 8, 9 and 10.**

`scripts/capture_thesis_queries.py --out <dir>` runs all four owed queries and writes CSV:

```
2022_raw_discharge.csv
2023_raw_discharge.csv
2022_deseasonalized.csv
2023_deseasonalized.csv
```

**The script has been run — on 2026-08-15 — and the four files exist.** The paste has not happened,
so **the debt has not closed**, and it is recorded as open rather than as "captured": a file in
`/tmp` that nobody read is the same amount of review as no file at all.

**The script deliberately does not write to any document.** That is the design, not an omission: a
document that edits itself is a document nobody reviews, and the paste is the step where somebody
actually reads the numbers.

**What the four blocks check.** `findings.md § D` records the 2022 and 2023 thesis observations as
*anchor points* — discharge ~368,000 → 153,143 cfs, rate 388 → 2,812.5, peaks on 10-11 and 09-26 —
because the session that wrote them received the endpoints and the peaks and **not** the row-by-row
output, and did not invent the intervening weeks (`CLAUDE.md § 4`: when data is lost, record the
loss, never synthesize a replacement). Interpolating ~26 plausible weekly rows between measured
endpoints produces a table that reads as measured and is not. **These four blocks are what make
every number in that section checkable.**

**When they land, they go here as fenced blocks**, beside the anchor points in `findings.md § D`
they check.
