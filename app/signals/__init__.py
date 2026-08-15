"""The lead-lag sweep: measurement, not selection.

`app/features/` computes numbers about the river and the market. THIS PACKAGE MEASURES WHETHER
THEY ARE RELATED, and its whole design is shaped by one piece of arithmetic:

    5 features × 4 sites × 3 horizons × 41 lags × 3 regimes ≈ 7,000 tests
    at α = 0.05, roughly 350 of them clear the threshold ON PURE NOISE

Every guard in these modules exists because of that number, and it is why this phase is more
dangerous than the ones before it: PREVIOUS PHASES COULD BE WRONG, THIS ONE CAN BE CONVINCINGLY
WRONG. An ingest bug produces a count that does not match the source. A sweep with no multiple-
comparisons accounting produces a table of significant-looking relationships, correctly computed,
every one of them reproducible, and mostly noise.

The modules, and what each refuses:

    pairs.py        enumerates the grid, and SKIPS features that are the same series at a site -
                    detected from the data, never from a site list.
    statistics.py   correlation, an EFFECTIVE sample size for overlapping windows, and
                    Benjamini-Hochberg. No p-value is ever produced without a q-value.
    regimes.py      the onset/recovery split, DEFINED FROM THE PREDICTOR. The classifier does not
                    take the target as an argument, so the circular version cannot be written.
    walkforward.py  splits whose training window is gapped by the target horizon, asserted at the
                    DATA level rather than in configuration.
    sweep.py        the CLI. Writes every pair it scanned including the nulls, and exposes no
                    accessor for its own best result.

NOTHING HERE SELECTS. The sweep measures and records; it does not pick a winner, does not rank into
a leaderboard anything downstream consumes, and does not tune a threshold to make a result
significant. Selection happens in Phase 7 under CLAUDE.md § 7's confidence gate - in a separate
step, never inside the procedure that generated the candidates, because a sweep that answers "which
is your best pair?" is a model-selection procedure with no held-out data.

CLAUDE.md § 18 is the contract these modules implement.
"""
