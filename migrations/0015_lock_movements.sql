-- 0015 — lock_movements: weekly barged grain through the locks.
--
-- The volume side of the target. Where `barge_rates` says what shippers paid, this says how much
-- actually moved - and during a low-water event the two move together in a way the analog engine
-- is built to find.
--
-- NOT A HYPERTABLE, for the reasons written out at length in 0014: a handful of locks times a few
-- grain types times two directions times ~52 weeks is thousands of rows a year, and Phase 3's own
-- compression measurement concluded that at ~290k rows Postgres alone would have been adequate.
-- Plain table, ordinary indexes.
--
-- ---------------------------------------------------------------------------------------------
-- ZERO IS A VALUE. NULL IS THE ABSENCE OF ONE. THEY ARE NEVER COLLAPSED.
-- ---------------------------------------------------------------------------------------------
--
-- THIS IS THE MOST IMPORTANT LINE IN THE FILE, because the failure it prevents destroys precisely
-- the observations this project exists to study.
--
--   `barges = 0`    the lock was surveyed and NOTHING MOVED. During the 2022 low-water event,
--                   near-zero movement is not missing data - it is THE SIGNAL. A tow that could
--                   not sail is the physical fact the whole thesis is about.
--   `barges IS NULL` not reported for that week. Nothing is known.
--
-- An ingest that skips zero rows ("no movement, nothing to write") deletes the event from the
-- record and leaves a gap indistinguishable from an unreported week. An ingest that coalesces
-- NULL to 0 invents a surveyed zero out of silence, in the same column, in the opposite
-- direction. Both are one line long, both look tidy, and tests/ingest/test_usda_movements.py
-- holds both behaviours apart.
--
-- The columns are therefore NULLABLE and the check is `>= 0`, not `> 0`.

CREATE TABLE lock_movements (
    -- The lock as published (a lock-and-dam identifier). Text, as published, for the same reason
    -- `segment` is in 0014.
    lock_id text NOT NULL,

    -- The published week-ending label, stored as the calendar date stated. No timezone
    -- arithmetic, ever - identical rule to barge_rates.week_ending and gauge_readings_daily.date.
    week_ending date NOT NULL,

    -- Corn, soybeans, wheat, and so on, as published.
    grain_type text NOT NULL,

    -- Downbound / upbound as published. IN THE KEY, not a detail: down-river grain heading for
    -- export and up-river movement are different flows, and a key without direction keeps one of
    -- the two arbitrarily. Same argument as `horizon` in 0014.
    direction text NOT NULL,

    -- NULLABLE, AND 0 IS DISTINCT FROM NULL. See the block above; this is the decision.
    barges integer,
    tons numeric,

    PRIMARY KEY (lock_id, week_ending, grain_type, direction),

    -- `>= 0`, deliberately. A zero is a legitimate reported value and must satisfy the constraint;
    -- only a negative count is impossible.
    CONSTRAINT lock_movements_barges_non_negative
        CHECK (barges IS NULL OR barges >= 0),
    CONSTRAINT lock_movements_tons_non_negative
        CHECK (tons IS NULL OR tons >= 0)
);

-- One lock's history, newest first - how a feature or an analog window reads it.
CREATE INDEX lock_movements_lock_week_idx
    ON lock_movements (lock_id, week_ending DESC);

-- One week across every lock, for the corridor-wide total.
CREATE INDEX lock_movements_week_idx ON lock_movements (week_ending DESC);

COMMENT ON TABLE lock_movements IS
    'Weekly barged grain movements through locks, from USDA AgTransport, as published. NOT a '
    'hypertable, deliberately - see migration 0014.';

COMMENT ON COLUMN lock_movements.barges IS
    'Barge count as reported. 0 MEANS REPORTED AS NONE and is a real observation - near-zero '
    'movement during a low-water event is the signal this project studies. NULL means NOT '
    'REPORTED. The two are never collapsed in either direction (CLAUDE.md section 16).';

COMMENT ON COLUMN lock_movements.week_ending IS
    'The published week-ending label, stored as the calendar date stated. No timezone arithmetic '
    'is applied at any point (CLAUDE.md section 15).';
