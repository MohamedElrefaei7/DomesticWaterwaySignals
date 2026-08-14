-- 0016 — the real USDA identifiers, and the two schema corrections they force.
--
-- ---------------------------------------------------------------------------------------------
-- WHAT WAS MEASURED, AND WHEN
-- ---------------------------------------------------------------------------------------------
--
-- A human queried the AgTransport catalog and each dataset directly on 2026-08-14. Every id,
-- bound, and row count below is from that measurement. Nothing here is inferred from a dataset's
-- web page or its description, which is CLAUDE.md § 15's rule about periods of record applied to
-- the identifiers themselves.
--
--   key                  id           rows     range
--   barge_rates_nearby   deqi-uken     8,260   2004-01-07 -> 2026-08-11
--   barge_rates_1month   svms-9yya     8,260   2004-01-07 -> 2026-08-11
--   barge_rates_3month   uuhv-5etw     8,260   2004-01-07 -> 2026-08-11
--   lock_movements       n4pw-9ygw    26,144   2003-01-04 -> 2026-08-08
--   cost_indicators      8uye-ieij    NOT MEASURED - seeded, not fetched
--
-- EVERY FIELD NAME 0013-0015 WAS BUILT AROUND WAS WRONG. The published fields are `date`, `week`,
-- `month`, `year`, `location`, `rate` (plus `rate_month` on the two forward datasets) for rates,
-- and `date`, `week`, `month`, `year`, `commodity`, `lock`, `tons` for movements. Phase 4 said so
-- in writing - the names came from the shape the fixtures were written to - and routed every read
-- through `required_field`, which is why this correction is a migration and a field map rather
-- than an investigation.
--
-- ---------------------------------------------------------------------------------------------
-- THE THREE HORIZONS ARE THREE DATASETS, NOT A COLUMN
-- ---------------------------------------------------------------------------------------------
--
-- 0014 assumed USDA published one rates dataset carrying a horizon column. It publishes three
-- datasets, one per horizon, each with the identical field list. `horizon` stays in the primary
-- key exactly as 0014 designed it - the reasoning there was right - but its value is assigned by
-- WHICH DATASET A ROW CAME FROM and is never read out of a record. The mapping lives in one place
-- (app/ingest/usda_rates.py, HORIZON_BY_DATASET_KEY) and a test asserts it is total both ways, so
-- a fourth rates dataset fails loudly instead of defaulting.
--
-- ---------------------------------------------------------------------------------------------
-- THESE ARE ORDINARY ALTERs. THE TABLES HAVE NEVER HELD A ROW.
-- ---------------------------------------------------------------------------------------------
--
-- 0014 and 0015 are applied and EMPTY. No ingest has ever run against them, because until this
-- file there was no resolved dataset id to run one against - every client path raised before
-- issuing a request. So the renames and drops below move no data and lose none, and they are NOT
-- CLAUDE.md § 3 archival operations.
--
-- STATED HERE SO A FUTURE READER DOES NOT GO LOOKING FOR AN ARCHIVE TABLE: there is no
-- `lock_movements_archived_20260814`, and its absence is not an omission. § 3's rule protects
-- data; there was none.
--
-- 0014 and 0015 THEMSELVES ARE NOT EDITED. The checksum guard would refuse, but that is not the
-- reason: a migration records what was believed when it was written, and this correction is
-- itself a fact worth having in the sequence (the same argument 0011 makes about 0008).

-- ---------------------------------------------------------------------------------------------
-- 1. usda_datasets: the real ids, the measured bounds, and the count they were measured with.
-- ---------------------------------------------------------------------------------------------

-- WHY STORE A COUNT THAT WILL GO STALE.
--
-- After a backfill, comparing rows landed against rows the source reported at seed time is the
-- cheapest check that the pager did not silently truncate - which is the exact failure
-- CLAUDE.md § 16's first bullet is about, and the one that reports success with a plausible row
-- count. Staleness does not weaken it: the dataset only grows, so this is a FLOOR. Landing fewer
-- rows than this is a truncation signal; landing more is ordinary publication.
ALTER TABLE usda_datasets ADD COLUMN source_row_count integer;

ALTER TABLE usda_datasets ADD CONSTRAINT usda_datasets_row_count_non_negative
    CHECK (source_row_count IS NULL OR source_row_count >= 0);

COMMENT ON COLUMN usda_datasets.source_row_count IS
    'Rows the source reported for this dataset when its id was resolved, MEASURED 2026-08-14. A '
    'floor, not a current value: a backfill landing FEWER rows than this truncated (CLAUDE.md '
    'section 16). NULL means never measured - which is the case for cost_indicators, seeded but '
    'not fetched.';

-- The `barge_rates` key becomes `barge_rates_nearby` by UPDATE rather than by DELETE + INSERT.
--
-- Two reasons, and the second is the load-bearing one. This project does not delete rows
-- (CLAUDE.md § 1) - a rename is non-destructive where a delete-and-replace is a small archival
-- question nobody wants to answer at seed level. And the nearby dataset IS the direct successor
-- of what `barge_rates` meant: the row keeps its identity rather than being replaced by three
-- strangers.
UPDATE usda_datasets
   SET dataset_key = 'barge_rates_nearby',
       dataset_id = 'deqi-uken',
       description = 'Weekly barge freight rate as percent of tariff, NEARBY horizon, by origin location. One of three sibling datasets, one per horizon.',
       first_period = DATE '2004-01-07',
       last_period = DATE '2026-08-11',
       source_row_count = 8260
 WHERE dataset_key = 'barge_rates';

INSERT INTO usda_datasets
    (dataset_key, dataset_id, domain, description, first_period, last_period, source_row_count)
VALUES
    ('barge_rates_1month', 'svms-9yya', 'agtransport.usda.gov', 'Weekly barge freight rate as percent of tariff, 1-MONTH FORWARD horizon, by origin location. Carries rate_month: the calendar month the quoted rate applies to.', DATE '2004-01-07', DATE '2026-08-11', 8260),
    ('barge_rates_3month', 'uuhv-5etw', 'agtransport.usda.gov', 'Weekly barge freight rate as percent of tariff, 3-MONTH FORWARD horizon, by origin location. Carries rate_month: the calendar month the quoted rate applies to.', DATE '2004-01-07', DATE '2026-08-11', 8260);

-- Downbound only, tons only. Both facts are in the description because both changed the schema
-- below, and a description that still said "by lock, grain type and direction" would be the one
-- place the old shape survived.
UPDATE usda_datasets
   SET dataset_id = 'n4pw-9ygw',
       description = 'Weekly DOWNBOUND barged grain movements through locks, by lock and commodity, in TONS. No barge count and no direction are published: the dataset is downbound-only by construction.',
       first_period = DATE '2003-01-04',
       last_period = DATE '2026-08-08',
       source_row_count = 26144
 WHERE dataset_key = 'lock_movements';

-- Resolved in the same catalog query, and DELIBERATELY NOT MEASURED BEYOND ITS ID. The bounds and
-- the count stay NULL because nothing counted them, and seeding an unmeasured bound is precisely
-- what CLAUDE.md § 15 forbids - Phase 3 did it from sampled windows and was wrong at three of four
-- sites. Its presence here is a record of what exists, not a promise that it is loaded: there is
-- no table, no cadence entry, and no ingest path for it.
UPDATE usda_datasets
   SET dataset_id = '8uye-ieij'
 WHERE dataset_key = 'cost_indicators';

-- ---------------------------------------------------------------------------------------------
-- 2. barge_rates: `segment` -> `location`, and `rate_month` arrives.
-- ---------------------------------------------------------------------------------------------

-- USDA calls it `location`. 0014 called it `segment` on the strength of the handoff's wording, and
-- the column name is now the source's own - unlike `week_ending` below, where the divergence is
-- deliberate and argued.
ALTER TABLE barge_rates RENAME COLUMN segment TO location;

ALTER INDEX barge_rates_segment_horizon_week_idx RENAME TO barge_rates_location_horizon_week_idx;

-- THE CALENDAR MONTH THE QUOTED RATE APPLIES TO, AS PUBLISHED. NOT AN OFFSET.
--
-- The forward datasets publish `rate_month` = 9 or 11 against a publication month of 8. It is
-- stored as that integer and NOT converted to a months-ahead offset: an offset is a DERIVED
-- quantity, and deriving it here would put a modelling decision in the ingest layer - the same
-- error as dividing a percent by 100, which 0014 spends a paragraph refusing. The month is what
-- USDA published; whoever wants a horizon distance can subtract two columns.
--
-- NULLABLE, because the nearby dataset has no such field. A NULL here means "this horizon has no
-- quoted month", which is correct and complete - not missing data.
ALTER TABLE barge_rates ADD COLUMN rate_month integer;

ALTER TABLE barge_rates ADD CONSTRAINT barge_rates_rate_month_is_a_month
    CHECK (rate_month IS NULL OR rate_month BETWEEN 1 AND 12);

-- A TRIPWIRE ON THE PAIRING, not a modelling rule.
--
-- Exactly the nearby rows carry no rate_month and exactly the forward rows carry one. Both
-- failures this catches are silent and one line long: SYNTHESIZING a rate_month for nearby rows
-- (inventing a quoted month out of a publication date), and a forward row LOSING its rate_month
-- to a renamed field, which would write a NULL indistinguishable from nearby's legitimate one.
--
-- If USDA starts publishing a quoted month on the nearby dataset, this fires on the first insert.
-- The fix is then to measure what it means and change this constraint in a new migration.
ALTER TABLE barge_rates ADD CONSTRAINT barge_rates_rate_month_matches_horizon
    CHECK ((horizon = 'nearby') = (rate_month IS NULL));

-- THE SEVEN PUBLISHED LOCATIONS, AS A TRIPWIRE - NOT A VOCABULARY, AND NOT A NORMALIZATION.
--
-- The strings are stored exactly as USDA publishes them and are never mapped, title-cased, or
-- reduced to an internal id. A normalization step is where the join silently breaks the week USDA
-- publishes a value the mapping does not cover, and the symptom is MISSING WEEKS rather than an
-- unmapped value - a shape nothing downstream can attribute.
--
-- So the constraint's whole job is to make an eighth value a LOUD INSERT FAILURE. On failure the
-- fix is to measure the new string and add it in a NEW migration. NEVER to drop this constraint,
-- and never to bend the arriving value to fit it.
--
-- TWO OF THESE SEVEN ARE MEASURED. `Cairo-Memphis` and `Twin Cities` appear verbatim in captured
-- records. THE OTHER FIVE COME FROM THE HANDOFF AND THEIR EXACT SPELLING IS UNMEASURED - live
-- verification step 2 groups the dataset by location and confirms all seven before any backfill
-- runs. WHERE THE API DISAGREES WITH THIS LIST, THE API WINS and the correction lands in 0017.
ALTER TABLE barge_rates ADD CONSTRAINT barge_rates_location_known
    CHECK (location IN (
        'Twin Cities',
        'Mid-Mississippi',
        'Illinois River',
        'St. Louis',
        'Cincinnati',
        'Lower Ohio',
        'Cairo-Memphis'
    ));

COMMENT ON COLUMN barge_rates.location IS
    'Origin location, EXACTLY as published by USDA (source field `location`). Never normalized. '
    'The CHECK listing seven values is a TRIPWIRE for an unseen eighth, not a vocabulary: on '
    'failure, measure the new string and add it in a new migration.';

COMMENT ON COLUMN barge_rates.rate_month IS
    'The calendar month the quoted rate applies to, as published (source field `rate_month`, '
    'forward datasets only). NOT a months-ahead offset - that is derived, and deriving it in '
    'ingest is a modelling decision in the wrong layer. NULL on nearby rows, where the source '
    'publishes no such field, and that NULL is correct rather than missing.';

COMMENT ON COLUMN barge_rates.horizon IS
    'nearby | 1_month | 3_month, assigned by WHICH DATASET the row came from - USDA publishes '
    'three sibling datasets, not one dataset with a horizon column. Never inferred from record '
    'content (app/ingest/usda_rates.py, HORIZON_BY_DATASET_KEY).';

-- ---------------------------------------------------------------------------------------------
-- 3. lock_movements: `direction` and `barges` leave entirely.
-- ---------------------------------------------------------------------------------------------
--
-- THE DATASET IS DOWNBOUND-ONLY BY CONSTRUCTION. It is titled "Downbound Barge Grain Movements
-- (Tons)" and publishes no direction field. There is no direction dimension to key on, and a
-- column holding the constant 'Down' on every row adds nothing a reader of the table name does
-- not already know.
--
-- THERE IS NO BARGE COUNT PUBLISHED. Only tons. 0015 spends its longest comment on the zero-versus
-- -NULL distinction in `barges`, and that reasoning was right - it just belongs to `tons` now,
-- which is the measure that exists. A `barges` column that is ALWAYS NULL is worse than no column:
-- it looks like data, invites `SELECT ... WHERE barges IS NOT NULL` to return nothing forever, and
-- records an absence as if it were an unreported value.
--
-- IF A BARGE COUNT IS WANTED LATER IT COMES FROM A DIFFERENT DATASET, and that is a separate
-- commit with its own measurement, its own id, and its own table or column. It is recorded in
-- CONTEXT.md as such rather than held open here as a NULL column pretending to be a placeholder.

ALTER TABLE lock_movements DROP CONSTRAINT lock_movements_pkey;

-- Explicit, though DROP COLUMN would take them with it. A constraint disappearing as a side
-- effect of a column drop is the kind of catalog change § 15 says to read back rather than assume.
ALTER TABLE lock_movements DROP CONSTRAINT lock_movements_barges_non_negative;

ALTER TABLE lock_movements DROP COLUMN direction;
ALTER TABLE lock_movements DROP COLUMN barges;

-- USDA's own names. `lock` is a non-reserved keyword in Postgres and is legal unquoted; verified
-- against the pinned image rather than assumed.
ALTER TABLE lock_movements RENAME COLUMN lock_id TO lock;
ALTER TABLE lock_movements RENAME COLUMN grain_type TO commodity;

-- The new key. One lock, one week, one commodity is one published tonnage.
ALTER TABLE lock_movements ADD PRIMARY KEY (lock, week_ending, commodity);

-- THE SEVEN PUBLISHED LOCKS, VERBATIM, WITH THEIR MEASURED ROW COUNTS:
--
--   AK Lock 1      4,928      IL La Grange   2,840      MS Lock 15     2,840
--   MS Lock 25     2,840      MS Lock 26     2,840      MS Locks 27    4,928
--   OH Olmsted     4,928
--
-- NOTE `MS Locks 27` - PLURAL - BESIDE `MS Lock 15`, `MS Lock 25` AND `MS Lock 26`, SINGULAR.
-- That inconsistency is USDA's, it is stable, and it is stored exactly as it arrives. Normalizing
-- the plural is the tidy that breaks the join: every downstream query written against the tidied
-- form silently returns nothing for that lock, and 4,928 rows - the largest lock in the dataset -
-- go missing as absent weeks rather than as an error.
--
-- Unlike the location list, ALL SEVEN OF THESE ARE MEASURED, each with a counted group-by. The
-- constraint is still a tripwire and not a vocabulary: an eighth lock is a loud insert failure,
-- and the fix is a new migration, never a dropped constraint.
ALTER TABLE lock_movements ADD CONSTRAINT lock_movements_lock_known
    CHECK (lock IN (
        'AK Lock 1',
        'IL La Grange',
        'MS Lock 15',
        'MS Lock 25',
        'MS Lock 26',
        'MS Locks 27',
        'OH Olmsted'
    ));

COMMENT ON TABLE lock_movements IS
    'Weekly DOWNBOUND barged grain movements through locks, from USDA AgTransport, in tons as '
    'published. No direction column: the dataset is downbound-only by construction. No barge '
    'count: the source publishes none, and a column that would always be NULL is not created. NOT '
    'a hypertable, deliberately - see migration 0014.';

COMMENT ON COLUMN lock_movements.lock IS
    'The lock EXACTLY as published, including `MS Locks 27` (plural) beside `MS Lock 15` '
    '(singular). Never normalized: a mapping that does not cover an arriving value fails as '
    'missing weeks rather than as an error. The CHECK is a TRIPWIRE for an eighth lock - on '
    'failure, measure it and add it in a new migration.';

COMMENT ON COLUMN lock_movements.tons IS
    'Tons as reported. 0 MEANS REPORTED AS NONE and is a real observation - near-zero movement '
    'during a low-water event is the signal this project studies. NULL means NOT REPORTED. The '
    'two are never collapsed in either direction (CLAUDE.md section 16). This is the only measure '
    'the source publishes.';
