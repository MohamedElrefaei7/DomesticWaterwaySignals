-- 0013 — usda_datasets: which Socrata dataset each key means, resolved by a human.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THE IDs ARE NULL IN THIS FILE
-- ---------------------------------------------------------------------------------------------
--
-- A Socrata dataset identifier is a four-four alphanumeric token (`abcd-1234`). THE AGENT THAT
-- WROTE THIS MIGRATION COULD NOT REACH THE USDA CATALOG AND DID NOT GUESS ONE. That is the same
-- rule that governed the AMI id, the container image digest, and the gauge site numbers
-- (CLAUDE.md § 1): an invented identifier does not fail as a wrong answer, it fails as a 404 that
-- reads like a network problem, and the hours go into the network rather than into the value.
--
-- So the keys are seeded and the ids are NULL. A human resolves them from the AgTransport catalog
-- and lands them - with the period bounds from a counted full-range query - in a NEW numbered
-- migration. Until then every client method raises a named error naming the key, BEFORE issuing a
-- request, rather than building a URL around the word "None".
--
-- ---------------------------------------------------------------------------------------------
-- WHY first_period / last_period ARE HERE AND WHY THEY ARE NOT SEEDED EITHER
-- ---------------------------------------------------------------------------------------------
--
-- Phase 3 seeded a period of record from sampled windows and was wrong for three of four sites
-- (see CONTEXT.md and CLAUDE.md § 15). The contract that came out of it binds here on first
-- contact: **coverage is established by a full-range query with a count**, not by a sampled
-- window, not by a dataset's web page, and not by its description. `$select=count(*)` plus min and
-- max of the date column, per dataset, and those values seed these columns.
--
-- They are NULL now for the same reason the ids are: nothing has measured them.

CREATE TABLE usda_datasets (
    -- The name THIS PROJECT uses. Stable across a dataset being re-published under a new id,
    -- which is what makes it the thing code refers to.
    dataset_key text PRIMARY KEY,

    -- The Socrata four-four identifier. NULL until a human resolves it.
    dataset_id text,

    -- The Socrata domain the dataset lives on. Held per row rather than as a constant because a
    -- dataset moving between domains is exactly the kind of change that should be one UPDATE.
    domain text NOT NULL,

    description text NOT NULL,

    -- Coverage, from a counted full-range query. See above.
    first_period date,
    last_period date,

    -- NULL IS PERMITTED AND MEANS "NOT YET RESOLVED". A shape check applies only when a value is
    -- present, so an id typed as `abcd1234` or as a dataset's title fails here rather than at the
    -- first request - and, more importantly, rather than succeeding against some other dataset.
    CONSTRAINT usda_datasets_id_shape
        CHECK (dataset_id IS NULL OR dataset_id ~ '^[a-z0-9]{4}-[a-z0-9]{4}$'),

    -- Bounds are either both unknown or a real ordered range. One end alone is a half-measurement,
    -- and the query that establishes them returns both or neither.
    CONSTRAINT usda_datasets_period_bounds
        CHECK (
            (first_period IS NULL AND last_period IS NULL)
            OR (first_period IS NOT NULL AND last_period IS NOT NULL
                AND last_period >= first_period)
        )
);

COMMENT ON COLUMN usda_datasets.dataset_id IS
    'Socrata four-four identifier, resolved by a HUMAN from the AgTransport catalog and landed in '
    'a new numbered migration. NULL means not yet resolved: every client method raises a named '
    'error before issuing any request rather than building a URL from a NULL (CLAUDE.md '
    'sections 1 and 16).';

COMMENT ON COLUMN usda_datasets.first_period IS
    'Earliest period in the dataset, from a COUNTED FULL-RANGE query - never a sampled window and '
    'never the dataset description (CLAUDE.md section 15).';


-- ---------------------------------------------------------------------------------------------
-- The three keys. Human-owned, like the gauge list.
-- ---------------------------------------------------------------------------------------------
--
-- ONE ROW PER LINE; the layout is load-bearing in the same way 0004's and 0012's are.
--
-- `cost_indicators` IS SEEDED AND DELIBERATELY NOT INGESTED IN THIS COMMIT. It is here because
-- resolving three ids in one sitting at the catalog costs the human nothing extra, and because a
-- key with no consumer is visibly a stub while a missing key is invisible. Its absence from the
-- ingest path is a scope decision, not an oversight - there is no `cost_indicators` cadence entry
-- and no table for it, and adding one is a later commit's work.

INSERT INTO usda_datasets (dataset_key, dataset_id, domain, description) VALUES
    ('barge_rates', NULL, 'agtransport.usda.gov', 'Weekly barge freight rate as percent of tariff, by origin segment and horizon (nearby, 1-month, 3-month forward).'),
    ('lock_movements', NULL, 'agtransport.usda.gov', 'Weekly barged grain movements through locks, by lock, grain type and direction.'),
    ('cost_indicators', NULL, 'agtransport.usda.gov', 'Weekly modal transportation cost indicators. SEEDED FOR LATER - not ingested by this commit.');
