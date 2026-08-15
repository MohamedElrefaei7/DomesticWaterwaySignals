-- 0021 — targets: forward log-returns of the rate the output contract names.
--
-- ---------------------------------------------------------------------------------------------
-- LOG-RETURN, NOT PERCENT CHANGE, AND THE 2022 EVENT IS THE ARGUMENT
-- ---------------------------------------------------------------------------------------------
--
-- The Cairo-Memphis nearby rate went from 388 to 2,812.5 in ten weeks - a 7.2x rise, +625%. The
-- reverse move, back down, is -86%. THOSE ARE THE SAME MOVE IN OPPOSITE DIRECTIONS AND PERCENT
-- CHANGE GIVES THEM WILDLY DIFFERENT MAGNITUDES.
--
-- Anything fitted on percent changes therefore learns that asymmetry as if it were a fact about
-- barge freight: rises look enormous, falls look bounded at -100%, and every summary statistic -
-- a mean, a threshold, a "typical move" - inherits the distortion. On a series that moves 7x it is
-- not a subtlety.
--
-- ln(p1/p0) is SYMMETRIC (a doubling is +0.693, a halving is -0.693) and ADDITIVE across periods
-- (the 7-day returns over three weeks sum to the 21-day return). Both properties are asserted
-- directly by tests rather than left as a comment.
--
-- ---------------------------------------------------------------------------------------------
-- horizon_days IS IN THE ROW AND IN THE KEY
-- ---------------------------------------------------------------------------------------------
--
-- Three horizons - 7, 14 and 21 days - are three different questions about the same week, and a
-- table holding one of them with the horizon recorded only in a module constant is a table nobody
-- can read six months later. It is in the primary key for the same reason `horizon` is in
-- barge_rates' (0014): two horizons for one week are two facts, and a key without it keeps
-- whichever was written last.
--
-- ---------------------------------------------------------------------------------------------
-- A NULL TARGET IS THE HONEST ANSWER IN TWO DIFFERENT SITUATIONS
-- ---------------------------------------------------------------------------------------------
--
--   1. THE FORWARD WEEK HAS NO PUBLISHED RATE. A winter NULL is a real closure (0017). Carrying
--      the previous week's rate through it would manufacture a return of ZERO that never happened,
--      in a series where zero means "the price did not move" - a completely ordinary thing for
--      this column to say, so nothing downstream could tell. That is the same camouflage argument
--      0017 and 0018 make about the ingest layer, one layer up.
--
--   2. THE FORWARD WEEK IS PAST THE END OF THE SERIES. The last horizon_days of any series have no
--      target and THAT IS CORRECT, not a gap to fill. Any check on this table must compare against
--      the number of weeks with BOTH endpoints published, never against the row count - the latter
--      would report the newest weeks as broken forever.
--
-- Both are NULL rather than absent rows, for the reason CLAUDE.md § 16 gives about ingest: a NULL
-- row states the absence, a missing row hides it.

CREATE TABLE targets (
    -- The week the target is measured FROM. Matches barge_rates.week_ending exactly - the
    -- published week-ending label, stored as the calendar date stated, no timezone arithmetic.
    week_ending date NOT NULL,

    -- Which series this is a target for. Text rather than a CHECK, for the same reason
    -- features.feature_name is: this vocabulary is this project's own and lives in
    -- app/features/targets.py, not in a constraint that has to be migrated in lockstep.
    target_name text NOT NULL,

    -- How far forward, in DAYS rather than weeks. The rate series is weekly on a Thursday-ending
    -- convention, so 7/14/21 are exact multiples - but days are what the arithmetic actually uses
    -- and storing weeks would invite someone to multiply by 7 in a query and get it wrong once.
    horizon_days integer NOT NULL,

    -- ln(rate[t + horizon_days] / rate[t]). NULL where either endpoint is unpublished or the
    -- forward week is past the end of the series - see the block above. NEVER imputed, never
    -- carried forward, never defaulted to 0.
    value double precision,

    PRIMARY KEY (week_ending, target_name, horizon_days),

    CONSTRAINT targets_horizon_days_positive
        CHECK (horizon_days > 0)
);

-- One target series in date order - how a walk-forward split reads it.
CREATE INDEX targets_name_horizon_week_idx
    ON targets (target_name, horizon_days, week_ending DESC);

COMMENT ON TABLE targets IS
    'Forward log-returns of the barge rate, one row per (week_ending, target_name, horizon_days). '
    'LOG-RETURNS, not percent changes: the 2022 event moved this series 7.2x, and percent change '
    'is wildly asymmetric between a rise and the fall that undoes it, so anything fitted on it '
    'learns the asymmetry as a fact about freight. A NULL value means either endpoint was '
    'unpublished or the forward week is past the end of the series; it is never imputed and never '
    'carried forward (CLAUDE.md section 17).';

COMMENT ON COLUMN targets.value IS
    'ln(rate[t + horizon_days] / rate[t]). Symmetric - a doubling is +0.693 and a halving is '
    '-0.693 - and additive across periods. NULL is a REFUSAL, not a missing computation: carrying '
    'the previous rate through an unpublished week would manufacture a return of zero that never '
    'happened, and zero is an entirely ordinary value here.';

COMMENT ON COLUMN targets.horizon_days IS
    'Forward window in DAYS. In the primary key because two horizons for one week are two '
    'different facts, and a key without it would keep whichever was written last.';
