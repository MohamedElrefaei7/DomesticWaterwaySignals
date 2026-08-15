-- 0022 — signal_runs: one row per sweep, with the parameters and the commit it ran against.
--
-- ---------------------------------------------------------------------------------------------
-- WHY A RUN TABLE EXISTS AT ALL, WHEN `signals` COULD HAVE CARRIED A TIMESTAMP
-- ---------------------------------------------------------------------------------------------
--
-- Because a measured relationship is not a property of the world on its own - it is a property of
-- the world AND of the grid it was found in, the feature definitions in force that day, and the
-- lag range somebody chose. A SIGNAL MEASURED IN MARCH UNDER DIFFERENT FEATURE DEFINITIONS IS NOT
-- COMPARABLE TO ONE MEASURED IN JUNE, and without the commit recorded there is no way to know they
-- differ. They would sit in the same table looking like two observations of one thing.
--
-- That is not hypothetical here. `CONTEXT.md` records Phase 5 CONTRADICTING Phase 4's headline on
-- the same data: the raw-discharge relationship was substantially calendar, and deseasonalization
-- is what changed the answer. Both were "the relationship between Memphis discharge and the
-- Cairo-Memphis rate". A row from either, without its provenance, is a number nobody can place.
--
-- ---------------------------------------------------------------------------------------------
-- THIS IS NOT A JOB TABLE, AND IT IS DELIBERATELY NOT `job_runs`
-- ---------------------------------------------------------------------------------------------
--
-- `job_runs` (0002) records SCHEDULED work: did the thing that is supposed to happen every hour
-- happen. A sweep is a RESEARCH OPERATION a human starts, reads, and argues with. It has no
-- cadence entry and it registers no freshness (see app/signals/sweep.py) - a scheduled sweep would
-- accumulate runs nobody reads, and would eventually be the thing that "found" a signal at 3am
-- that nothing validated.
--
-- So the two tables answer different questions and neither can be derived from the other:
--
--     job_runs     is the system healthy - did the work that must happen keep happening
--     signal_runs  under what parameters, and against which commit, was this measurement taken
--
-- ---------------------------------------------------------------------------------------------
-- finished_at IS NULLABLE, AND A NULL IN IT IS EVIDENCE RATHER THAN A GAP
-- ---------------------------------------------------------------------------------------------
--
-- The row is written BEFORE the scan starts and committed, so a sweep that dies halfway leaves a
-- record saying it was attempted. A run row written only on success would leave a crash indistin-
-- guishable from a sweep nobody ran - and the sweep is long-running, which is precisely when
-- somebody gives up on it and later cannot remember whether it finished.
--
-- A run with `finished_at IS NULL` and no `signals` rows did not complete. That is a readable
-- state, and it is the reason this table is written in two steps rather than one.

CREATE TABLE signal_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    started_at timestamptz NOT NULL DEFAULT now(),

    -- NULL while running, and still NULL if the sweep died. See the block above: that is a state
    -- worth being able to read, not a column waiting to be filled in.
    finished_at timestamptz,

    -- How many (feature, site, target, horizon, lag, regime) combinations this run enumerated.
    -- KNOWN BEFORE THE SCAN STARTS, because the grid is built from the registry and the site list
    -- rather than discovered as the scan proceeds - so it is NOT NULL, and a row whose grid_size
    -- disagrees with its count of `signals` rows is a sweep that dropped something.
    grid_size integer NOT NULL,

    -- The ±lag range scanned, in days. Both stored, rather than a width: a run over -21..+21 and a
    -- run over 0..+42 are the same width and are completely different experiments, and only one of
    -- them can observe the target moving first.
    lag_min integer NOT NULL,
    lag_max integer NOT NULL,

    -- Arrays rather than a child table. These are PARAMETERS OF ONE RUN, read back as a unit by a
    -- human deciding whether two runs are comparable; a child table would make the common read a
    -- join and would let a run exist with no horizons at all.
    horizons integer[] NOT NULL,
    regimes text[] NOT NULL,

    -- NULL means "no filter - every feature in the registry". Stored because a partial sweep and a
    -- full one produce q-values adjusted across different grids, and a filtered run whose filter
    -- was not recorded is a set of q-values nobody can interpret.
    feature_filter text,

    -- THE COMMIT THE SWEEP RAN AGAINST. Read from the repo at runtime.
    --
    -- NOT NULL, and app/signals/sweep.py refuses to open a run it cannot determine. That is a
    -- deliberate hard failure: a sweep is cheap to re-run and a result nobody can place is
    -- expensive forever, so "I could not read the sha" is a reason to stop rather than a reason to
    -- write 'unknown' - which would look like a sha-shaped value in every listing afterwards.
    git_sha text NOT NULL,

    -- Whether the working tree had uncommitted changes when the sweep started.
    --
    -- A SEPARATE COLUMN RATHER THAN A '-dirty' SUFFIX ON git_sha, so that `git_sha` is always a
    -- value you can hand to `git show` and so a query can filter dirty runs out without string
    -- matching. A dirty run's sha names a commit whose code is NOT what ran: the results are still
    -- worth keeping and they are not reproducible, and those are two different facts about one row.
    git_dirty boolean NOT NULL,

    -- The random seed, where a run used randomness. NULL MEANS IT USED NONE, which is true of
    -- every run this migration ships with: the sweep is deterministic - a correlation, a t
    -- distribution, and a Benjamini-Hochberg sort, none of which sample anything.
    --
    -- The column exists anyway because the first thing anyone adds to a sweep like this is a
    -- permutation test or a block bootstrap, and on that day every row written before it must say
    -- plainly that it had no seed rather than being retro-fitted with a plausible one.
    seed bigint,

    CONSTRAINT signal_runs_lag_range_ordered
        CHECK (lag_max >= lag_min),

    -- A run with no horizons or no regimes enumerated an empty grid and measured nothing, which is
    -- a bug rather than an empty result - and it would otherwise be recorded as a completed sweep.
    CONSTRAINT signal_runs_horizons_not_empty
        CHECK (array_length(horizons, 1) >= 1),
    CONSTRAINT signal_runs_regimes_not_empty
        CHECK (array_length(regimes, 1) >= 1),

    CONSTRAINT signal_runs_grid_size_positive
        CHECK (grid_size > 0),

    CONSTRAINT signal_runs_finished_after_started
        CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- The newest runs first - how a human finds the run they just started.
CREATE INDEX signal_runs_started_at_idx ON signal_runs (started_at DESC);

COMMENT ON TABLE signal_runs IS
    'One row per lead-lag sweep: its parameters, its grid size, and the commit it ran against. A '
    'signal measured under one set of feature definitions is not comparable to one measured under '
    'another, and without the commit recorded there is no way to know two rows differ - Phase 5 '
    'contradicted Phase 4 on the same data by changing what the feature meant. NOT a job table: a '
    'sweep is a research operation a human starts and reads, it has no cadence entry, and it '
    'registers no freshness (its staleness is not a system-health question).';

COMMENT ON COLUMN signal_runs.git_sha IS
    'The commit the sweep ran against, read from the repo at runtime. NOT NULL: the sweep refuses '
    'to open a run whose sha it cannot determine, because a result nobody can place is expensive '
    'forever and re-running the sweep is cheap.';

COMMENT ON COLUMN signal_runs.git_dirty IS
    'Whether the working tree carried uncommitted changes. A separate column rather than a suffix '
    'on git_sha, so the sha stays something you can hand to `git show`. A dirty run''s results are '
    'worth keeping AND are not reproducible - two different facts about one row.';

COMMENT ON COLUMN signal_runs.finished_at IS
    'NULL while the sweep runs, and still NULL if it died. The row is committed before the scan '
    'starts so a crashed sweep leaves evidence: finished_at NULL with no signals rows is a run '
    'that did not complete, which is different from a sweep nobody started.';

COMMENT ON COLUMN signal_runs.seed IS
    'NULL means the run used no randomness, which is true of every run as of this migration - the '
    'sweep is deterministic. The column exists so that the first permutation test or bootstrap '
    'added here does not have to retro-fit a plausible seed onto rows that never had one.';

COMMENT ON COLUMN signal_runs.grid_size IS
    'Combinations enumerated, known before the scan starts because the grid comes from the feature '
    'registry and the site list. A run whose grid_size disagrees with its count of signals rows '
    'dropped something - which is the check that makes the multiple-comparisons denominator real.';
