-- 0019 — gauge_daily: derived daily statistics, and the minimum is the thesis-relevant one.
--
-- ---------------------------------------------------------------------------------------------
-- THIS IS NOT A REPLACEMENT FOR gauge_series, AND THE NAMES INVITE THAT MISTAKE
-- ---------------------------------------------------------------------------------------------
--
--   gauge_series (view, 0010)   answers SOURCE PRECEDENCE: for this site-date-param, does the
--                               value come from the instantaneous record or the published daily
--                               one, and it exposes `source` so the seam stays visible.
--
--   gauge_daily  (table, here)  answers DERIVED DAILY STATISTICS: mean, minimum, maximum, and how
--                               many observations produced them.
--
-- The rollup READS THE VIEW rather than the two reading tables, so the precedence rule has exactly
-- one implementation. Re-deriving it here would be a second copy that returns a plausible series
-- and diverges silently, which is CLAUDE.md § 15's rule about precedence living in one place.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THE MINIMUM IS STORED, AND WHY IT MATTERS MORE THAN THE MEAN
-- ---------------------------------------------------------------------------------------------
--
-- A barge's draft is constrained by THE SHALLOWEST POINT IT MUST TRANSIT, AT THE MOMENT IT
-- TRANSITS. A daily mean discharge that looks perfectly adequate can contain hours during which
-- the channel bound - and those hours are the physical mechanism this whole project is about.
--
-- The mean is the better-behaved modelling input; the minimum is closer to the mechanism. BOTH ARE
-- STORED AND PHASE 6 DECIDES. Storing only the mean would be choosing the convenient one before
-- anything had measured which is right.
--
-- ---------------------------------------------------------------------------------------------
-- n_observations IS NOT DECORATION. IT IS WHAT MAKES value_min HONEST.
-- ---------------------------------------------------------------------------------------------
--
-- A daily minimum computed from 96 instantaneous readings and a daily minimum computed from ONE
-- published daily mean are different things wearing the same column name. THE SECOND IS NOT A
-- MINIMUM AT ALL - it is the mean, because a minimum over one observation is that observation.
--
-- Instantaneous retention is a rolling window of recent weeks at three of the four gauges
-- (CLAUDE.md § 15), so MOST OF HISTORY at those sites arrives as dv rows with n_observations = 1.
-- A feature that reads value_min without reading n_observations would treat thirty years of
-- daily means as thirty years of daily minima, and every conclusion drawn from "the minimum" would
-- be a conclusion about the mean with a more alarming name.
--
-- NOT NULL, and a test asserts it is populated on every row. A nullable count is a count nobody
-- checks.
--
-- ---------------------------------------------------------------------------------------------
-- THE UTC-VERSUS-LOCAL-DAY SEAM IS INHERITED, VISIBLE, AND DELIBERATELY NOT CORRECTED HERE
-- ---------------------------------------------------------------------------------------------
--
-- 0010 buckets instantaneous readings by UTC date while USGS computes its daily values over the
-- site's LOCAL day. On the lower Mississippi that is a five-to-six-hour offset at both edges. This
-- table inherits the seam exactly as the view has it, and inherits `source` so the seam remains
-- visible rather than being averaged into invisibility.
--
-- Correcting it needs a per-gauge timezone, which the `gauges` table does not carry. THAT IS ITS
-- OWN COMMIT with its own migration, and doing it here would mean a Phase 5 commit quietly
-- changing what every Phase 3 row means.

CREATE TABLE gauge_daily (
    usgs_site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    -- The calendar date the statistics cover, as the view buckets it. No timezone arithmetic is
    -- applied here; see the seam note above for what the view's bucketing already means.
    date date NOT NULL,

    param_code text NOT NULL,

    -- The day's mean. For an iv-sourced row this is the view's average of that day's samples; for
    -- a dv-sourced row it is USGS's own published daily mean.
    value_mean double precision NOT NULL,

    -- THE DAY'S EXTREMES. For a dv-sourced row both equal value_mean and n_observations is 1 -
    -- see the block above. That is not a defect to be filtered out later, it is the honest answer,
    -- and n_observations is how a consumer tells the two cases apart.
    value_min double precision NOT NULL,
    value_max double precision NOT NULL,

    -- 'iv' | 'dv', CARRIED THROUGH FROM THE VIEW. Not recomputed, not inferred from
    -- n_observations, and not dropped: a series that switches provenance mid-history has a seam,
    -- and this column is what keeps it visible instead of hidden (0010).
    source text NOT NULL,

    -- How many observations produced the three statistics above. 1 for every dv row.
    n_observations integer NOT NULL,

    PRIMARY KEY (usgs_site_id, date, param_code),

    -- Same vocabulary as the view's own `source`. A tripwire for a third source arriving without
    -- anyone deciding what it means, not a vocabulary to extend casually.
    CONSTRAINT gauge_daily_source_known
        CHECK (source IN ('iv', 'dv')),

    -- AT LEAST ONE. A row with zero observations is a row computed from nothing, and the three
    -- value columns above would be whatever the aggregate returned for an empty set.
    CONSTRAINT gauge_daily_n_observations_positive
        CHECK (n_observations >= 1),

    -- THE ORDERING INVARIANT, ASSERTED BY THE DATABASE RATHER THAN TRUSTED.
    --
    -- min <= mean <= max is arithmetic, so this can only fire if the three columns were computed
    -- over DIFFERENT SETS OF ROWS - which is exactly the failure a join between the view and the
    -- sub-daily record can produce silently: attach one day's samples to another day's mean and
    -- every value is individually plausible. That is the shape CLAUDE.md § 2 theme 1 describes,
    -- and it is cheap to make impossible.
    CONSTRAINT gauge_daily_min_le_mean_le_max
        CHECK (value_min <= value_mean AND value_mean <= value_max)
);

-- One site's history for one parameter, newest first - how a feature window and an analog lookup
-- both read it.
CREATE INDEX gauge_daily_site_param_date_idx
    ON gauge_daily (usgs_site_id, param_code, date DESC);

-- One date across every site, for a corridor-wide view.
CREATE INDEX gauge_daily_date_idx ON gauge_daily (date DESC);

COMMENT ON TABLE gauge_daily IS
    'Derived daily statistics per site, date and parameter, computed from the gauge_series view '
    '(source precedence) plus the sub-daily record (dispersion). NOT a replacement for '
    'gauge_series: the view answers WHICH SOURCE, this table answers WHAT THE DAY LOOKED LIKE. '
    'Inherits the view''s UTC-versus-local-day seam and its `source` column unchanged - see 0019 '
    'and 0010.';

COMMENT ON COLUMN gauge_daily.value_min IS
    'The day''s minimum. CLOSER TO THE PHYSICAL MECHANISM THAN THE MEAN: a barge''s draft is bound '
    'by the shallowest point at the moment it transits, and an adequate-looking daily mean can '
    'contain hours during which the channel bound. READ n_observations BEFORE TRUSTING THIS: where '
    'it is 1 the row came from a published daily mean and this column IS that mean, not a minimum.';

COMMENT ON COLUMN gauge_daily.n_observations IS
    'How many observations produced value_mean/min/max. 1 on every dv-sourced row, and up to 96 on '
    'an iv-sourced one. NOT decoration - it is the only thing distinguishing a real daily minimum '
    'from a daily mean stored in the minimum column, and instantaneous retention is a rolling '
    'window at three of four gauges so most of history is the latter.';

COMMENT ON COLUMN gauge_daily.source IS
    'iv | dv, carried through from gauge_series unchanged. Never recomputed and never inferred '
    'from n_observations: the two reading tables are not the same measurement (different day '
    'boundaries, different sampling), so a series switching source mid-history has a seam and this '
    'column is what keeps it visible (migration 0010).';
