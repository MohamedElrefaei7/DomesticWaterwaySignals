-- 0018 — `lock_movements.tons`: a NULL is a REPORTING GAP, and that is not what a NULL rate means.
--
-- ---------------------------------------------------------------------------------------------
-- THE ANALOGY WOULD HAVE BEEN WRONG. THIS IS THE MEASUREMENT.
-- ---------------------------------------------------------------------------------------------
--
-- 0017 made `barge_rates.pct_of_tariff` nullable against a measurement and DELIBERATELY LEFT
-- `tons` ALONE, on the stated grounds that its nullability was an analogy rather than a
-- measurement (CLAUDE.md § 16, last bullet). The analogy has now been measured, and it does not
-- hold: the SHAPE of the handling is the same and the MEANING is entirely different.
--
-- MEASURED 2026-08-14 against n4pw-9ygw, all 26,144 published records.
--
-- `tons` IS ABSENT ON 108 RECORDS (0.4%), AND ON ONLY THREE OF THE SEVEN LOCKS:
--
--   AK Lock 1        71 of 4,928       IL La Grange      0 of 2,840
--   OH Olmsted       26 of 4,928       MS Lock 15        0 of 2,840
--   MS Locks 27      11 of 4,928       MS Lock 25        0 of 2,840
--                                      MS Lock 26        0 of 2,840
--
-- BY YEAR - 96 of the 108 fall in a two-year window:
--   2003 (6)  2006 (2)  2012 (2)  2014 (1)  2015 (79)  2016 (17)  2021 (1)
--
-- BY MONTH - FLAT. This is the number that decides the meaning:
--   Jan 16  Feb 9  Mar 11  Apr 6  May 3  Jun 14
--   Jul 11  Aug 1  Sep 7   Oct 9  Nov 7  Dec 14
--
-- BY COMMODITY - spread across all four: Corn 46, Other Grain 38, Wheat 19, Soybeans 5.
--
-- ---------------------------------------------------------------------------------------------
-- AND `tons = 0` APPEARS ON 8,218 RECORDS (31%). THAT IS THE FINDING THAT MATTERS.
-- ---------------------------------------------------------------------------------------------
--
-- USDA publishes explicit zeros ROUTINELY - on nearly a third of all records - so a zero is the
-- normal, published way of saying "no grain moved through this lock this week". A NULL is
-- therefore NOT THE SAME STATEMENT, and it is not a rarer spelling of the same one. The source has
-- a way to say "none moved" and it uses it 8,218 times; the 108 records that say nothing at all
-- are saying something else.
--
-- ---------------------------------------------------------------------------------------------
-- SO THE TWO NULLs MEAN DIFFERENT THINGS, AND THE COMMENT MUST NOT BE COPIED
-- ---------------------------------------------------------------------------------------------
--
--   barge_rates.pct_of_tariff   NULL is SEASONAL AND PHYSICAL. 774 records, 661 of them in
--                               December-March, 729 on the two upper segments. WINTER NAVIGATION
--                               CLOSURE - a fact about the river (0017).
--
--   lock_movements.tons         NULL is A REPORTING GAP. Flat across months, confined to three
--                               locks, 96 of 108 inside 2015-2016. IT SAYS NOTHING ABOUT THE
--                               RIVER. The winter-closure language from 0017 would, if copied
--                               here, assert something the measurement contradicts.
--
-- WHY THIS MATTERS MORE HERE THAN IT DID FOR RATES. The three affected locks are the SUMMARY
-- locks - `MS Locks 27` is the Mississippi's main southbound gate and the single most load-bearing
-- series in this dataset. Coalescing a NULL to 0 would assert "no grain moved through Lock 27 that
-- week" for eleven weeks where USDA simply did not report. That is a FABRICATED ZERO, and it is
-- exactly the failure CLAUDE.md § 7's confidence gate exists to prevent - arriving in the ingest
-- layer, where no gate is watching.
--
-- ---------------------------------------------------------------------------------------------
-- NOTHING HERE INTERPRETS THE 2015-2016 ANOMALY
-- ---------------------------------------------------------------------------------------------
--
-- The cause is unknown. It is recorded as measured - three summary locks, two years, flat across
-- months - and nothing acts on it. THERE IS NO `gauge_known_gaps`-STYLE TABLE FOR IT AND THOSE
-- WEEKS ARE NOT EXCLUDED: a 0.4% gap that falls outside both labelled events does not warrant the
-- machinery, and building the machinery would imply a conclusion about the cause that nobody has
-- reached. See CONTEXT.md, where it is logged as an open observation.
--
-- ---------------------------------------------------------------------------------------------
-- WHAT THIS FILE DOES AND DOES NOT ALTER, STATED SO THE ABSENCE IS NOT READ AS AN OMISSION
-- ---------------------------------------------------------------------------------------------
--
-- THERE IS NO `ALTER COLUMN tons DROP NOT NULL` BELOW, AND ITS ABSENCE IS DELIBERATE. 0015 created
-- `tons` without NOT NULL and 0016 never added one, so the column is ALREADY nullable and the ALTER
-- would be a no-op that reads like a change. What was missing was never the structure - it was the
-- MEANING, which until now was recorded by analogy to a column measured for a different reason.
--
-- The precondition is VERIFIED RATHER THAN ASSUMED, in the DO block below, because "the column is
-- already nullable" is exactly the kind of catalog state CLAUDE.md § 15 says to read back instead
-- of trusting. If some future migration adds a NOT NULL, this file's comment would otherwise go on
-- describing a nullability the table no longer has.
--
-- `lock_movements_tons_non_negative` IS ALSO LEFT ALONE. 0015 already wrote it as
-- `tons IS NULL OR tons >= 0` - both the NULL case and the zero case spelled out - so 0017's
-- rewrite of `barge_rates_pct_positive` has no counterpart here. Restating an identical constraint
-- would put a second CHECK of the same fact in the sequence for the sake of symmetry.

-- ---------------------------------------------------------------------------------------------
-- 1. The precondition, read from the catalog.
-- ---------------------------------------------------------------------------------------------
--
-- A hard failure rather than a corrective ALTER. If `tons` has acquired a NOT NULL, the 108
-- unreported lock-weeks cannot be written at all and the backfill would abort row by row - and the
-- right response is for a human to find out which migration added it, not for this one to strip it
-- back silently and leave no evidence the table ever disagreed with its own documentation.
DO $$
DECLARE
    is_nullable text;
BEGIN
    SELECT c.is_nullable
      INTO is_nullable
      FROM information_schema.columns c
     WHERE c.table_schema = 'public'
       AND c.table_name = 'lock_movements'
       AND c.column_name = 'tons';

    IF is_nullable IS NULL THEN
        RAISE EXCEPTION
            'lock_movements.tons does not exist. Migrations 0015 and 0016 create and rename this '
            'table; one of them has not been applied.';
    END IF;

    IF is_nullable <> 'YES' THEN
        RAISE EXCEPTION
            'lock_movements.tons is NOT NULL (is_nullable = %). USDA does not report it on 108 of '
            '26,144 records, so a NOT NULL column cannot hold this dataset - every unreported '
            'lock-week would abort its insert, and the only values available to satisfy the '
            'constraint are zero and a lie. Find the migration that added this and correct it '
            'deliberately; 0018 will not strip it silently.', is_nullable;
    END IF;
END $$;

-- ---------------------------------------------------------------------------------------------
-- 2. The measured meaning, on the column.
-- ---------------------------------------------------------------------------------------------
--
-- 0016's comment said 0 means reported-as-none and NULL means not-reported, which is TRUE and was
-- written before either population had been counted. This replaces it with the measurement and,
-- above all, with the statement that the NULL here is a reporting gap - so that nobody reads the
-- rates column's winter-closure explanation across onto this one.
COMMENT ON COLUMN lock_movements.tons IS
    'Tons as reported by USDA, exactly as published. This is the only measure the source '
    'publishes. NULL MEANS USDA DID NOT REPORT THIS LOCK-WEEK-COMMODITY; IT IS A REPORTING GAP, '
    'NOT A STATEMENT THAT NOTHING MOVED - 108 of 26,144 records, flat across months, on only three '
    'of seven locks, 96 of them in 2015-2016 (measured 2026-08-14). `0` IS THE PUBLISHED WAY OF '
    'SAYING NOTHING MOVED and appears on roughly 31% of records (8,218). The two are never '
    'collapsed in either direction: coalescing NULL to 0 fabricates a surveyed zero at the '
    'summary locks, and skipping zeros deletes the near-zero movement that IS the signal during a '
    'low-water event. NOT THE SAME MEANING AS A NULL pct_of_tariff, which is a winter closure and '
    'a fact about the river (migration 0017).';

COMMENT ON TABLE lock_movements IS
    'Weekly DOWNBOUND barged grain movements through locks, from USDA AgTransport, in tons as '
    'published. No direction column: the dataset is downbound-only by construction. No barge '
    'count: the source publishes none, and a column that would always be NULL is not created. NOT '
    'a hypertable, deliberately - see migration 0014. A NULL `tons` is a week USDA did not report '
    'for that lock and commodity - a REPORTING GAP rather than a closure, and unrelated to the '
    'seasonal NULLs in barge_rates - see 0018.';
