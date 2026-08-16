-- 0024 — analog_queries: one row per question asked of the analog engine, INCLUDING THE REFUSALS.
--
-- ---------------------------------------------------------------------------------------------
-- A REFUSAL IS A ROW. THIS IS THE SAME ARGUMENT 0023 MAKES, AND IT MATTERS MORE HERE.
-- ---------------------------------------------------------------------------------------------
--
-- Phase 6 scanned 6,966 pairs and ONE passed - `days_below_p10` at Memphis, horizon 7, LAG 0,
-- q 0.0446. Zero pairs passed at any non-zero lag in either direction. So the analog engine is
-- being pointed at a dataset in which no predictive relationship has been measured, and the
-- expected output of most queries is "insufficient history".
--
-- THAT IS THE DELIVERABLE, NOT A DEGRADED MODE. Which is why the refusal is written down with the
-- counts that produced it: a table holding only the queries that produced an estimate would make
-- an engine that refuses ninety-nine times out of a hundred look like an engine that answers.
-- Same disappearing denominator as the sweep, one layer up and with no q-value to catch it.
--
-- ---------------------------------------------------------------------------------------------
-- BOTH DETECTION COUNTS ARE STORED, AND THE GATE CONSUMES THE COLLAPSED ONE
-- ---------------------------------------------------------------------------------------------
--
-- A sustained low-water period produces a detection every day it continues. The 2022 event ran
-- from August into November; counted raw, it alone would contribute dozens of "analogs" and would
-- satisfy CLAUDE.md § 7's ">= 4 analogs" four times over FROM A SINGLE EVENT. That is the exact
-- shape of the failure the gate exists to prevent - conviction manufactured from one coincidence -
-- and it would be invisible in the output, because four analogs is four analogs.
--
-- So detections within `MIN_EVENT_SEPARATION_DAYS` collapse into one event, and BOTH numbers are
-- stored:
--
--     n_raw_detections      days on which the entry condition held
--     n_collapsed_events    distinct events after the separation rule
--
-- A row where the first is large and the second is 1 or 2 is the whole story of this dataset, and
-- it is only readable if both are kept. (The brief's column list named neither; they are added
-- here, and the deviation is recorded in CONTEXT.md.)
--
-- ---------------------------------------------------------------------------------------------
-- parameters_hash AND git_sha: TWO OUTPUTS UNDER DIFFERENT SETTINGS ARE NOT TWO OBSERVATIONS
-- ---------------------------------------------------------------------------------------------
--
-- 0022 makes this argument for the sweep and it is not weaker here. A similarity metric over three
-- features and one over five are different instruments; a k of 10 and a k of 4 answer different
-- questions. Without the hash, two rows differing in every one of those sit in the same table
-- looking comparable. `git_sha` covers the code, `parameters_hash` covers the values the code was
-- pointed at, and NEITHER SUBSTITUTES FOR THE OTHER - app/analogs/parameters.py can change without
-- a commit, and the engine can change without a parameter moving.
--
-- ---------------------------------------------------------------------------------------------
-- WHAT THE SWEEP SAID, RECORDED ON THE QUERY
-- ---------------------------------------------------------------------------------------------
--
-- `signal_q_value` and `signal_run_id` carry Phase 6's verdict on the relationship this query
-- assumes. NOT because the engine needs a passing signal to run - the sweep asks "is there a
-- relationship across all history" and the engine asks "what happened the last few times
-- conditions looked like this", and a weak sweep does not invalidate the second question.
--
-- They are here so an output CANNOT BE READ WITHOUT THE SWEEP'S VERDICT BESIDE IT. An analog
-- engine reporting confident analogs where the sweep found no relationship has a bug, and this is
-- the column pair that makes that contradiction visible in the data rather than in an argument.
-- NULL means the sweep never scanned this feature-site pair at all, which is a third state and is
-- distinguishable from "scanned and found nothing".

CREATE TABLE analog_queries (
    query_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The date the question was asked AS OF. Every observation the engine used is dated on or
    -- before this, including the z-score population the distances were computed against - see
    -- app/analogs/similarity.py. It is not a timestamp: the engine answers a question about a
    -- calendar day of river conditions, and `created_at` records when somebody asked it.
    as_of_date date NOT NULL,

    site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- The features the distance was computed over, in order. An array rather than a child table
    -- for 0022's reason: these are parameters of one query, read back as a unit.
    feature_vector text[] NOT NULL,

    k integer NOT NULL,
    outcome_window_days integer NOT NULL,

    -- 'passed' | 'no_current_event' | 'insufficient_analogs' | 'inconsistent_direction'
    -- | 'incomplete_outcomes'.
    --
    -- The refusal reason is part of the record, not a log line. "Insufficient history" with no
    -- reason cannot be acted on: too few events is a fact about the dataset that more ingest
    -- fixes, and inconsistent direction is a fact about the relationship that it does not.
    gate_result text NOT NULL,

    -- Detections before and after the separation rule. See the block above.
    n_raw_detections integer NOT NULL,
    n_collapsed_events integer NOT NULL,

    -- Analogs actually carried into the gate: collapsed events that were eligible (outcome window
    -- entirely in the past, not overlapping the query's own event) AND had a complete outcome.
    n_analogs integer NOT NULL,

    -- How many of those moved the rate in the majority direction. NEVER STORED WITHOUT n_analogs,
    -- for CLAUDE.md § 18's reason about directional consistency: 4 of 5 and 40 of 50 are both 80%
    -- and are not equally informative, so the fraction is not stored at all - it is derivable from
    -- the pair, and a stored fraction is a number that can drift from its own evidence.
    n_consistent integer NOT NULL,

    -- Phase 6's verdict on the relationship this query assumes. NULL means unscanned. See above.
    signal_run_id bigint REFERENCES signal_runs (run_id),
    signal_q_value double precision,

    git_sha text NOT NULL,
    git_dirty boolean NOT NULL,

    -- sha256 over app/analogs/parameters.py's values. See the block above.
    parameters_hash text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT analog_queries_gate_result_is_known
        CHECK (gate_result IN ('passed', 'no_current_event', 'insufficient_analogs',
                               'inconsistent_direction', 'incomplete_outcomes')),

    -- THE GATE'S OWN ARITHMETIC, IN THE SCHEMA. A row claiming to have passed on three analogs is
    -- the mutation this phase's table lists ("lower min_analogs to 3"), and a CHECK is what makes
    -- it impossible to write one by hand, from a script, or from a future module - not just
    -- impossible to produce through gate.py. Same argument 0023 makes about passes_gate needing a
    -- q-value.
    CONSTRAINT analog_queries_passing_needs_enough_analogs
        CHECK (gate_result <> 'passed' OR n_analogs >= 4),
    CONSTRAINT analog_queries_passing_needs_directional_consistency
        CHECK (gate_result <> 'passed'
               OR (n_analogs > 0 AND n_consistent::double precision / n_analogs >= 0.70)),

    -- Consistency is counted among the analogs, so it can never exceed them.
    CONSTRAINT analog_queries_consistent_within_analogs
        CHECK (n_consistent >= 0 AND n_consistent <= n_analogs),

    -- Collapsing only ever reduces. A collapsed count above the raw one means the separation rule
    -- ran the wrong way round, which would look like more evidence rather than like a bug - the
    -- same shape as 0023's n_effective constraint.
    CONSTRAINT analog_queries_collapsed_never_exceeds_raw
        CHECK (n_collapsed_events <= n_raw_detections),

    -- Analogs are drawn from collapsed events, never from raw detections. This is the constraint
    -- that makes the paragraph above enforceable rather than merely documented.
    CONSTRAINT analog_queries_analogs_within_collapsed_events
        CHECK (n_analogs <= n_collapsed_events),

    CONSTRAINT analog_queries_counts_non_negative
        CHECK (n_raw_detections >= 0 AND n_collapsed_events >= 0 AND n_analogs >= 0),

    CONSTRAINT analog_queries_k_positive
        CHECK (k > 0),
    CONSTRAINT analog_queries_outcome_window_positive
        CHECK (outcome_window_days > 0),

    CONSTRAINT analog_queries_feature_vector_not_empty
        CHECK (array_length(feature_vector, 1) >= 1),

    -- Bidirectional, in 0023's style: a q-value with no run is a number from nowhere, and a run
    -- with no q-value is a reference to a scan whose verdict was not recorded.
    CONSTRAINT analog_queries_signal_q_needs_its_run
        CHECK ((signal_run_id IS NULL) = (signal_q_value IS NULL)),
    CONSTRAINT analog_queries_signal_q_is_a_probability
        CHECK (signal_q_value IS NULL
               OR (signal_q_value >= 0.0 AND signal_q_value <= 1.0))
);

-- How a human finds the query they just ran, and how the labelled-event tests find theirs.
CREATE INDEX analog_queries_as_of_idx ON analog_queries (site_id, as_of_date DESC);

-- THE DENOMINATOR QUERY FOR THIS TABLE:
--   select gate_result, count(*) from analog_queries group by 1 order by 2 desc;
-- Refusals are expected to dominate it. That is the finding, not a fault in the engine.
CREATE INDEX analog_queries_gate_result_idx ON analog_queries (gate_result);

COMMENT ON TABLE analog_queries IS
    'One row per question asked of the analog engine, INCLUDING EVERY REFUSAL and the counts that '
    'produced it. Phase 6 scanned 6,966 pairs and one passed, at lag 0, so most queries against '
    'this dataset are expected to return "insufficient history" - that is the deliverable rather '
    'than a degraded mode, and a table holding only the queries that produced an estimate would '
    'make an engine that refuses look like an engine that answers.';

COMMENT ON COLUMN analog_queries.n_raw_detections IS
    'Days on which the entry condition held, BEFORE the separation rule. Stored beside '
    'n_collapsed_events because a sustained low-water period produces a detection every day it '
    'continues: counted raw, the 2022 event alone would satisfy the >=4-analog gate four times '
    'over from one event, which is exactly the manufactured conviction the gate exists to stop.';

COMMENT ON COLUMN analog_queries.n_consistent IS
    'Analogs whose rate move matched the majority direction. The FRACTION is deliberately not '
    'stored: it is derivable from this and n_analogs, and a stored fraction is a number that can '
    'drift from its own evidence (CLAUDE.md section 18 - 4 of 5 and 40 of 50 are both 80%).';

COMMENT ON COLUMN analog_queries.signal_q_value IS
    'What the Phase 6 sweep said about the feature-site relationship this query assumes. Recorded '
    'so an output can never be read without the sweep''s verdict beside it. NULL means the pair '
    'was never scanned, which is a third state and distinguishable from "scanned and found '
    'nothing". AN ENGINE REPORTING CONFIDENT ANALOGS WHERE THE SWEEP FOUND NO RELATIONSHIP HAS A '
    'BUG, and this column is what makes that visible in the data rather than in an argument.';

COMMENT ON COLUMN analog_queries.parameters_hash IS
    'sha256 over app/analogs/parameters.py''s human-owned values. A similarity metric over three '
    'features and one over five are different instruments; without this, two such rows sit in the '
    'same table looking like two observations of one thing. git_sha covers the code and does not '
    'substitute: parameters.py can change without a commit, and the engine can change without a '
    'parameter moving.';
