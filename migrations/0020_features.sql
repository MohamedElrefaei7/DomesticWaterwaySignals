-- 0020 — features: long format, one row per (date, site, feature name).
--
-- ---------------------------------------------------------------------------------------------
-- LONG FORMAT, AND THE WIDE ALTERNATIVE IS REJECTED RATHER THAN OVERLOOKED
-- ---------------------------------------------------------------------------------------------
--
-- The obvious shape is a wide table - one column per feature, one row per site-date. It reads
-- better in a SELECT and it is what most people draw first.
--
-- IT IS REJECTED BECAUSE EVERY NEW FEATURE WOULD BECOME A MIGRATION, and the feature set is
-- expected to grow through Phases 6 and 7 - that is the entire point of a lead-lag sweep, which
-- discovers which constructions deserve to exist. A schema that makes the expected activity
-- expensive is a schema that gets worked around, and the workaround is a second table.
--
-- Long format costs a WHERE clause and buys a stable schema. That is the trade, stated once.
--
-- ---------------------------------------------------------------------------------------------
-- THE REGISTRY IS THE SOURCE OF TRUTH FOR WHAT FEATURES EXIST. THIS TABLE IS NOT.
-- ---------------------------------------------------------------------------------------------
--
-- `feature_name` is text and there is DELIBERATELY NO CHECK CONSTRAINT enumerating the names -
-- which is the opposite of what 0016 does for locks and locations, so the difference needs
-- stating. Those vocabularies are a SOURCE's, published by someone else, where an unseen value is
-- news and the constraint is the tripwire that reports it. This vocabulary is THIS PROJECT'S OWN,
-- declared in app/features/registry.py, and a CHECK here would be a second copy of it that has to
-- be migrated in lockstep forever.
--
-- The equivalent tripwire lives in the build instead: A FEATURE ROW WHOSE NAME HAS NO REGISTRY
-- ENTRY IS AN ERROR THE BUILD REPORTS. It means either a rename left orphans behind or something
-- wrote outside the registry, and both are things to fix rather than rows to ignore. Guarded by
-- tests/features/test_registry.py.
--
-- ---------------------------------------------------------------------------------------------
-- WHY value, anomaly AND climatology_n_years ARE THREE COLUMNS AND NOT THREE FEATURES
-- ---------------------------------------------------------------------------------------------
--
-- The anomaly is the same measurement with the calendar removed, not a different measurement.
-- Splitting them into two feature names would double the row count, make every consumer join the
-- table to itself to compare them, and let the pair drift apart by one date.
--
-- `climatology_n_years` rides along because THE EIGHT-YEAR GUARD HAS TO BE AUDITABLE AFTER THE
-- FACT. A NULL anomaly with no count beside it is indistinguishable from a bug, and the first
-- thing anyone would do about it is remove the guard.

CREATE TABLE features (
    -- The date the feature describes. Daily, from gauge_daily.
    date date NOT NULL,

    -- NAMED site_id RATHER THAN usgs_site_id, and it still references the gauges table. Features
    -- are this project's own layer and a feature is conceptually "at a site"; the FK keeps a
    -- typo'd site from opening a silent second series, which is the only thing the longer name
    -- was buying.
    site_id text NOT NULL REFERENCES gauges (usgs_site_id),

    feature_name text NOT NULL,

    -- The feature's own value. NULLABLE: a run-length feature is NULL across a data gap, where a
    -- zero would assert the condition ended (CLAUDE.md § 17).
    value double precision,

    -- value minus the day-of-year climatology. NULLABLE, and a NULL here is a DELIBERATE REFUSAL
    -- rather than a missing computation: below the eight-year guard there is not enough history to
    -- say what normal looks like, and a climatology fitted on three years is a number with a false
    -- air of authority.
    anomaly double precision,

    -- How many distinct calendar years backed the climatology this anomaly was taken against.
    -- NULL where no anomaly is defined for this feature at all. See 0020's block above: this is
    -- what makes the guard checkable instead of merely claimed.
    climatology_n_years integer,

    PRIMARY KEY (date, site_id, feature_name),

    -- An anomaly must never exist without the count that justifies it. The reverse is allowed -
    -- the count is recorded even when the guard refused, which is precisely the case somebody
    -- investigating a NULL anomaly needs to see.
    CONSTRAINT features_anomaly_needs_its_year_count
        CHECK (anomaly IS NULL OR climatology_n_years IS NOT NULL),

    CONSTRAINT features_climatology_n_years_non_negative
        CHECK (climatology_n_years IS NULL OR climatology_n_years >= 0)
);

-- One feature's history at one site - the shape every window read and every analog lookup takes.
CREATE INDEX features_name_site_date_idx
    ON features (feature_name, site_id, date DESC);

-- One date across everything, for the join onto a target week.
CREATE INDEX features_date_idx ON features (date DESC);

COMMENT ON TABLE features IS
    'Derived per-site daily features, LONG FORMAT: one row per (date, site, feature_name). Wide '
    'format was rejected because every new feature would become a migration and the feature set is '
    'expected to grow through Phases 6 and 7. There is deliberately NO CHECK enumerating '
    'feature_name: the vocabulary is this project''s own and lives in app/features/registry.py, '
    'which is the single source of truth - a row whose name has no registry entry is an ERROR the '
    'build reports, not an orphan to ignore.';

COMMENT ON COLUMN features.anomaly IS
    'value minus the day-of-year climatology (median across years, 15-day centred smoothing). NULL '
    'where fewer than the required minimum of years back that day-of-year - a deliberate refusal, '
    'not a missing computation. Never imputed (CLAUDE.md section 17).';

COMMENT ON COLUMN features.climatology_n_years IS
    'Distinct calendar years contributing to the smoothing window behind this day''s climatology. '
    'Stored on every row that has a climatology concept, INCLUDING the rows where the guard '
    'refused - a NULL anomaly with no count beside it is indistinguishable from a bug, and the '
    'first response to that is to remove the guard.';

COMMENT ON COLUMN features.value IS
    'The feature''s value. NULLABLE: a run-length feature is NULL across a data gap, where 0 would '
    'assert the condition ended and NULL says it is unknown (CLAUDE.md section 17).';
