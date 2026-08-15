-- 0023 — signals: EVERY combination the sweep scanned, including the ones that found nothing.
--
-- ---------------------------------------------------------------------------------------------
-- THIS TABLE IS THE MULTIPLE-COMPARISONS RECORD. THAT IS ITS PURPOSE, NOT A SIDE EFFECT.
-- ---------------------------------------------------------------------------------------------
--
-- The grid this phase scans is roughly 5 features × 4 sites × 3 horizons × 41 lags × 3 regimes.
-- AT α = 0.05, ROUGHLY ONE IN TWENTY OF THOSE CLEARS THE THRESHOLD ON PURE NOISE. That arithmetic
-- is the entire reason this table holds a row for every combination rather than for the ones that
-- looked good.
--
-- Writing only the winners is what makes a sweep dishonest, and the mechanism is worth stating
-- because it does not feel like fraud from the inside: THE DENOMINATOR DISAPPEARS. Twelve
-- surviving rows in a table of twelve read as twelve findings. The same twelve rows in a table of
-- seven thousand read as the top of a distribution, which is what they are, and the reader can see
-- that a fair coin would have produced three hundred. Nobody deleted anything; the filter happened
-- at write time and left no trace of itself.
--
-- So: a row per scanned combination, `passes_gate` computed and stored, and CONSUMERS FILTER. The
-- writer never selects. `select count(*), count(*) filter (where passes_gate) from signals where
-- run_id = ...` is the query this schema exists to make answerable, and both numbers are always
-- reported together.
--
-- ---------------------------------------------------------------------------------------------
-- NO p-VALUE WITHOUT A q-VALUE, AND THE CHECK IS BIDIRECTIONAL
-- ---------------------------------------------------------------------------------------------
--
-- A raw p-value on this table would be a number that means one thing in isolation and something
-- entirely different as one of seven thousand, and it is the number a reader's eye goes to. So the
-- schema refuses the pair: `signals_p_value_needs_its_q_value` rejects a row carrying one without
-- the other, in both directions - a q with no p is equally broken, since Benjamini-Hochberg has
-- nothing to adjust.
--
-- BENJAMINI-HOCHBERG RATHER THAN BONFERRONI, and the reason is that the tests are heavily
-- correlated: lag +7 and lag +8 of the same feature at the same site are very nearly the same
-- test. Bonferroni assumes independence, so on this grid it would divide by ~7,000 and nothing
-- whatever would survive - and a sweep that can never report anything is theatre, not rigour. BH
-- controls the FALSE DISCOVERY RATE, which is the question actually being asked here: of the pairs
-- I am calling signals, what fraction are noise?
--
-- ---------------------------------------------------------------------------------------------
-- grid_size AND n_tests_adjusted ARE ON EVERY ROW, DENORMALIZED ON PURPOSE
-- ---------------------------------------------------------------------------------------------
--
-- A q-value is MEANINGLESS without knowing how many tests it was adjusted against. A later run
-- over a narrower grid would produce smaller q-values that look directly comparable to these and
-- are not - same column, same units, different experiment.
--
-- They are two different numbers and both are needed:
--
--     grid_size          combinations ENUMERATED by the run (equals the run's signals row count)
--     n_tests_adjusted   p-values BH actually had to work with - the m in p × m / rank
--
-- They differ whenever a pair was unscannable (too few paired observations, too few folds). Storing
-- only the first would misdescribe the adjustment; storing only the second would hide that rows
-- were enumerated and not tested. Both ride on the row rather than requiring a join to
-- `signal_runs`, because the join is exactly what a reader skips.
--
-- ---------------------------------------------------------------------------------------------
-- lag_days IS SIGNED AND NEGATIVE VALUES ARE FIRST-CLASS
-- ---------------------------------------------------------------------------------------------
--
-- A negative lag means THE TARGET MOVED BEFORE THE FEATURE. There is no CHECK requiring lag_days
-- to be non-negative, and that absence is deliberate: `CONTEXT.md` records the rate peaking two to
-- three weeks BEFORE discharge bottomed in both 2022 and 2023, which is the "operators price the
-- published river forecast" case.
--
-- If the strongest relationships in this table sit at negative lags, the project's claim changes
-- from "the physical signal leads" to "the market prices the forecast." THAT IS A FINDING ABOUT
-- THE WORLD, NOT AN ARTEFACT TO FILTER OUT (CLAUDE.md § 0: when a measurement contradicts the
-- plan, the measurement wins).
--
-- ---------------------------------------------------------------------------------------------
-- WHY regime AND status CARRY CHECKs WHEN feature_name DELIBERATELY DOES NOT
-- ---------------------------------------------------------------------------------------------
--
-- Migration 0020 refuses a CHECK on `features.feature_name` because that vocabulary is this
-- project's own, is EXPECTED TO GROW, and lives in app/features/registry.py - a constraint would be
-- a second copy to migrate in lockstep. The registry's build-time tripwire is the guard instead.
--
-- `regime` and `status` are different in the way that matters: THEIR SETS ARE CLOSED BY DEFINITION
-- rather than open by design. A window's feature counter is rising, falling, or unsplit; a pair was
-- scanned or it was refused for one of two stated reasons. Neither grows when somebody adds a
-- feature, and neither has a registry whose tripwire would catch a typo - so a misspelled 'onsett'
-- would open a silent fourth regime that every `group by regime` would report as a category.
--
-- `feature_name` here carries no CHECK, for 0020's reason, and no foreign key either: a sweep is a
-- historical record and must stay readable after a feature is renamed out of the registry.

CREATE TABLE signals (
    run_id bigint NOT NULL REFERENCES signal_runs (run_id),

    -- No FK to `features`, and no CHECK. See the block above: this is a historical record of what
    -- was measured, and it must survive a rename rather than being rewritten by one.
    feature_name text NOT NULL,

    -- The FK is kept here, though, because a typo'd site would open a silent second series in
    -- exactly the way migration 0020 describes - and unlike a feature name, a site id is not
    -- something this project ever renames.
    site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- WHICH COLUMN OF `features` WAS ACTUALLY CORRELATED: 'anomaly' or 'value'.
    --
    -- Not decoration, and not derivable from feature_name. A deseasonalized feature is scanned on
    -- its anomaly; a run-length feature has no anomaly by construction (a day-of-year median of a
    -- count is a number with no meaning) and is scanned on its value. The sweep decides this per
    -- (feature, site) FROM THE DATA rather than from a hardcoded list, so it can change between
    -- runs as the climatology guard's coverage changes - which is precisely why the answer is
    -- recorded on the row instead of inferred later.
    series_column text NOT NULL,

    target_name text NOT NULL,
    horizon_days integer NOT NULL,

    -- SIGNED. Positive: the feature is dated before the target week, the physical signal leading.
    -- Negative: the target moved first. See the block above - there is deliberately no CHECK
    -- restricting this to non-negative values.
    lag_days integer NOT NULL,

    -- 'onset' | 'recovery' | 'all'. DEFINED FROM THE FEATURE SERIES, NEVER FROM THE TARGET.
    --
    -- Splitting on the thing you are predicting and then reporting an association within each
    -- split is circular, and it is the single most seductive error available in this phase. The
    -- guard lives in app/signals/regimes.py, whose classifier does not take the target as an
    -- argument at all - enforced by signature, so the circular version cannot be written by
    -- accident.
    regime text NOT NULL,

    -- 'scanned' | 'insufficient_observations' | 'insufficient_folds'.
    --
    -- A REFUSAL IS RECORDED AS A ROW, not omitted. An omitted pair is indistinguishable from a
    -- pair nobody enumerated, and the count of enumerated pairs is the denominator this whole
    -- table exists to preserve.
    status text NOT NULL,

    -- The correlation itself. NULL where status is a refusal.
    statistic double precision,

    -- Computed from n_effective, NEVER from n_observations. See n_effective below.
    p_value double precision,

    -- Benjamini-Hochberg adjusted across every p-value this run produced. Never NULL beside a
    -- non-NULL p_value, and never present without one.
    q_value double precision,

    -- See the block above. Both, on every row, denormalized deliberately.
    grid_size integer NOT NULL,
    n_tests_adjusted integer NOT NULL,

    -- Paired (feature, target) observations behind the statistic. THE RAW COUNT, recorded so the
    -- correction below is visible rather than merely applied.
    n_observations integer NOT NULL,

    -- n_observations / (horizon_days / 7), AND THE p-VALUE COMES FROM THIS ONE.
    --
    -- Targets at horizon 14 on a weekly series OVERLAP: consecutive observations share one of the
    -- two weeks in their forward window, so they are not independent draws and the naive count is
    -- roughly twice the independent one (three times at horizon 21). Feeding the raw count to a t
    -- distribution would roughly halve every p-value at horizon 14 - uniformly, invisibly, and in
    -- the flattering direction. It is what every naive implementation does.
    --
    -- Double precision rather than integer because n / 3 is not an integer and rounding it would
    -- be a second, smaller version of the same optimism.
    n_effective double precision,

    -- Walk-forward folds behind directional_consistency. NEVER stored apart from it: 4 of 5 folds
    -- and 40 of 50 are both 80% and are not equally informative.
    folds integer,

    -- The fraction of walk-forward folds whose sign matched the full-sample sign. This is the
    -- quantity Phase 7's confidence gate consumes (CLAUDE.md § 7: ≥70% directional consistency),
    -- computed here rather than in the analog engine so that the sweep's own gate and Phase 7's
    -- read the same number.
    directional_consistency double precision,

    -- COMPUTED AND STORED, so consumers filter rather than the writer selecting. A row that fails
    -- the gate is still written; that is the point of the table.
    passes_gate boolean NOT NULL,

    -- regime is in the key: a pair scanned under 'onset' and under 'recovery' is two measurements
    -- of two different things, and a key without it would keep whichever was written last - the
    -- same argument 0021 makes for horizon_days.
    PRIMARY KEY (run_id, feature_name, site_id, target_name, horizon_days, lag_days, regime),

    -- ---------------------------------------------------------------------------------------
    -- THE HONESTY CONSTRAINTS. Each rejects a row that would read as a finding and is not one.
    -- ---------------------------------------------------------------------------------------

    -- Bidirectional. A p with no q is an unadjusted number in a table of thousands; a q with no p
    -- is an adjustment of nothing.
    CONSTRAINT signals_p_value_needs_its_q_value
        CHECK ((p_value IS NULL) = (q_value IS NULL)),

    -- A p-value implies a statistic it was computed from.
    CONSTRAINT signals_p_value_needs_its_statistic
        CHECK (p_value IS NULL OR statistic IS NOT NULL),

    -- Decision 5, in the schema rather than only in the writer.
    CONSTRAINT signals_consistency_needs_its_fold_count
        CHECK (directional_consistency IS NULL OR folds IS NOT NULL),

    -- Nothing clears the gate on an unadjusted p-value. Without this, dropping the BH step would
    -- leave a table full of passing rows and no q-values, which is the exact shape of the
    -- dishonest version.
    CONSTRAINT signals_passing_rows_carry_a_q_value
        CHECK (NOT passes_gate OR q_value IS NOT NULL),

    -- The overlap correction only ever REDUCES the sample size. A stored n_effective above the raw
    -- count means the divisor was applied the wrong way round, which would look like a stronger
    -- result rather than like a bug.
    CONSTRAINT signals_effective_n_never_exceeds_raw_n
        CHECK (n_effective IS NULL OR n_effective <= n_observations),

    CONSTRAINT signals_p_value_is_a_probability
        CHECK (p_value IS NULL OR (p_value >= 0.0 AND p_value <= 1.0)),
    CONSTRAINT signals_q_value_is_a_probability
        CHECK (q_value IS NULL OR (q_value >= 0.0 AND q_value <= 1.0)),
    CONSTRAINT signals_consistency_is_a_fraction
        CHECK (directional_consistency IS NULL
               OR (directional_consistency >= 0.0 AND directional_consistency <= 1.0)),
    CONSTRAINT signals_statistic_is_a_correlation
        CHECK (statistic IS NULL OR (statistic >= -1.0 AND statistic <= 1.0)),

    CONSTRAINT signals_counts_non_negative
        CHECK (n_observations >= 0
               AND n_tests_adjusted >= 0
               AND (folds IS NULL OR folds >= 0)),
    CONSTRAINT signals_grid_size_positive
        CHECK (grid_size > 0),
    CONSTRAINT signals_horizon_days_positive
        CHECK (horizon_days > 0),

    -- Closed sets, unlike features.feature_name. See the block above for why the two differ.
    CONSTRAINT signals_regime_is_known
        CHECK (regime IN ('onset', 'recovery', 'all')),
    CONSTRAINT signals_status_is_known
        CHECK (status IN ('scanned', 'insufficient_observations', 'insufficient_folds')),
    CONSTRAINT signals_series_column_is_known
        CHECK (series_column IN ('value', 'anomaly'))
);

-- THE DENOMINATOR QUERY, and the one every reading of this table starts with:
--   select count(*) as scanned, count(*) filter (where passes_gate) as passing
--     from signals where run_id = ...;
CREATE INDEX signals_run_gate_idx ON signals (run_id, passes_gate);

-- The top of the distribution, read in q order. Nothing in the codebase consumes this ordering -
-- selection is Phase 7's job under a stated confidence gate - it is here for a human reading a
-- finished run.
CREATE INDEX signals_run_q_value_idx ON signals (run_id, q_value);

-- One pair across every lag and regime: the shape of the Phase 5 comparison, which asks whether
-- days_below_p10 at Memphis shows on the onset side what the eyeball suggested, and whether the
-- recovery side reverses.
CREATE INDEX signals_pair_lag_idx
    ON signals (feature_name, site_id, horizon_days, lag_days);

COMMENT ON TABLE signals IS
    'One row per (run, feature, site, target, horizon, lag, regime) the sweep SCANNED, including '
    'every null result. THE TABLE IS THE MULTIPLE-COMPARISONS RECORD: on a grid of ~7,000 tests at '
    'alpha 0.05, roughly 350 clear the threshold on pure noise, so writing only the survivors would '
    'destroy the denominator and make the top of a distribution read as a list of findings. '
    'passes_gate is computed and stored so consumers filter rather than the writer selecting; the '
    'sweep measures and records, and never selects (selection is Phase 7''s job, under CLAUDE.md '
    'section 7''s confidence gate).';

COMMENT ON COLUMN signals.q_value IS
    'Benjamini-Hochberg adjusted across every p-value this run produced (n_tests_adjusted). BH '
    'rather than Bonferroni because adjacent lags of one feature are very nearly the same test, so '
    'Bonferroni would be so conservative that nothing survives and the sweep becomes theatre. BH '
    'controls the false discovery rate, which is the honest question: of the pairs being called '
    'signals, what fraction are noise?';

COMMENT ON COLUMN signals.n_effective IS
    'n_observations / (horizon_days / 7). THE P-VALUE IS COMPUTED FROM THIS, NEVER FROM THE RAW '
    'COUNT. Forward windows at horizon 14 on a weekly series share a week with their neighbours, '
    'so the raw count is roughly twice the independent one - using it would halve every p-value at '
    'horizon 14, uniformly and in the flattering direction.';

COMMENT ON COLUMN signals.lag_days IS
    'Signed, deliberately unconstrained. Negative means THE TARGET MOVED BEFORE THE FEATURE - the '
    '"operators price the published forecast" case, which CONTEXT.md records as consistent with the '
    'rate peaking two to three weeks before discharge bottomed in 2022 and 2023. A negative-lag '
    'result is a finding about the world, not an artefact to filter out.';

COMMENT ON COLUMN signals.regime IS
    'onset | recovery | all, DEFINED FROM THE FEATURE SERIES AND NEVER FROM THE TARGET. Splitting '
    'on the outcome and reporting association within each split is circular. Phase 5 measured the '
    'rate peaking at 23 days below p10 and then falling through 30, 37, 44, 51 and 58 - a single '
    'correlation across both regimes averages a strong positive against a strong negative and '
    'reports approximately nothing, which would read as "no relationship".';

COMMENT ON COLUMN signals.series_column IS
    'Which column of features was correlated - anomaly for a deseasonalized feature, value for a '
    'run-length one. Decided per (feature, site) FROM THE DATA rather than from a hardcoded list, '
    'so it can change between runs as the climatology guard''s coverage changes. Recorded on the '
    'row so the seam stays visible instead of being inferred later.';

COMMENT ON COLUMN signals.status IS
    'scanned | insufficient_observations | insufficient_folds. A refusal is a ROW, not an omission: '
    'an omitted pair is indistinguishable from one nobody enumerated, and the count of enumerated '
    'pairs is the denominator this table exists to preserve.';

COMMENT ON COLUMN signals.passes_gate IS
    'Computed from the stated criteria (q <= 0.05, directional consistency >= 0.70, folds >= 5) and '
    'STORED, so consumers filter. The writer never selects: a code path where another module asks '
    'the sweep for its best pair is where a sweep quietly becomes a model-selection procedure with '
    'no held-out data.';
