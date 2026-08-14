-- 0017 — the segment USDA actually publishes, and a rate that is legitimately absent.
--
-- ---------------------------------------------------------------------------------------------
-- THE FIRST BACKFILL FAILED ON ITS OWN TRIPWIRE. THAT IS THE SYSTEM WORKING.
-- ---------------------------------------------------------------------------------------------
--
-- 0016 seeded seven `location` values, five of them from the handoff document rather than from a
-- measurement, and said so: "live verification step 2 confirms all seven before any backfill runs.
-- WHERE THE API DISAGREES WITH THIS LIST, THE API WINS." It disagreed about one, and the run
-- stopped rather than opening a silent eighth series.
--
-- MEASURED 2026-08-14, all seven with 1,180 rows each:
--
--   Cairo-Memphis   Cincinnati   Lower Illinois   Lower Ohio   Mid-Mississippi
--   St. Louis       Twin Cities
--
-- The handoff said "Illinois"; 0016 seeded `Illinois River`; USDA publishes `Lower Illinois`. The
-- API wins, as 0016 committed in advance that it would.
--
-- ---------------------------------------------------------------------------------------------
-- 774 OF 8,260 RATE RECORDS HAVE NO `rate` FIELD, AND THE CAUSE IS PHYSICAL
-- ---------------------------------------------------------------------------------------------
--
-- NOT null-valued: the key is ABSENT FROM THE RECORD ENTIRELY. Such a record carries exactly
-- ['date', 'location', 'month', 'week', 'year'].
--
-- By month:  Jan 199  Feb 181  Mar 114  Apr 33  May 19  Jun 16
--            Jul 9    Aug 5    Sep 5    Oct 1   Nov 25  Dec 167
--
-- By location: Twin Cities 426, Mid-Mississippi 303, Lower Illinois 25, St. Louis 7,
--              Cincinnati 5, Lower Ohio 5, Cairo-Memphis 3.
--
-- 661 of 774 fall in December-March and 729 of 774 are on the two upper segments. THIS IS WINTER
-- NAVIGATION CLOSURE ON THE UPPER MISSISSIPPI. There is no rate to publish when no barges move.
--
-- SO A MISSING RATE IS A FACT ABOUT THE RIVER, NOT A GAP IN INGEST, and the column becomes
-- NULLABLE rather than the row being skipped. A skipped row makes the closure invisible - the
-- series would simply have no January, indistinguishable from an ingest that missed it. A NULL row
-- makes the closure explicit, and Phase 5's seasonal work has to see it: a seasonal baseline fitted
-- over weeks that silently do not exist learns a January that never closes.
--
-- WHAT IS NOT DONE HERE, DELIBERATELY: no constraint and no alert on the null rate. It is
-- legitimately absent 9% of the time overall and 36% of the time at Twin Cities, so any threshold
-- would either fire constantly or be set so loose it never fires. Visibility belongs in the
-- backfill's completeness report; enforcement here would be a rule nobody can satisfy.
--
-- AND THE TARGET SERIES IS EFFECTIVELY COMPLETE. Cairo-Memphis - the segment CLAUDE.md § 7's
-- output contract names - has 1,177 of 1,180 weeks. The 2022 window's 26 missing rates are an
-- ordinary winter closure and not the autumn low-water event; the 2022 rate spike is intact.
--
-- ---------------------------------------------------------------------------------------------
-- THE TABLE IS STILL EMPTY, SO THESE ARE STILL ORDINARY ALTERs
-- ---------------------------------------------------------------------------------------------
--
-- No ingest has ever succeeded: 0016's first backfill attempt aborted on the location tripwire
-- before writing a row. So `barge_rates` holds nothing, nothing is lost here, and - stated for the
-- same reason 0016 states it - THERE IS NO ARCHIVE TABLE TO GO LOOKING FOR.
--
-- Nothing in `usda_datasets` names a segment: the three rates descriptions say "by origin
-- location" without enumerating them. So there is no seed row to correct, and that is checked
-- rather than assumed.

-- ---------------------------------------------------------------------------------------------
-- 1. The corrected location vocabulary.
-- ---------------------------------------------------------------------------------------------
--
-- Dropped and recreated rather than edited in place, because a CHECK constraint has no ALTER form.
-- Still a tripwire and not a vocabulary: an eighth value remains a loud insert failure, and the fix
-- on failure is still to measure the new string and add it in a NEW migration - which is exactly
-- what this file is, arriving one step earlier in the process than expected.
--
-- ALL SEVEN ARE NOW MEASURED, each with a counted group-by returning 1,180 rows. 0016's list had
-- two measured and five from the handoff; this one has no unmeasured values in it.
ALTER TABLE barge_rates DROP CONSTRAINT barge_rates_location_known;

ALTER TABLE barge_rates ADD CONSTRAINT barge_rates_location_known
    CHECK (location IN (
        'Cairo-Memphis',
        'Cincinnati',
        'Lower Illinois',
        'Lower Ohio',
        'Mid-Mississippi',
        'St. Louis',
        'Twin Cities'
    ));

COMMENT ON COLUMN barge_rates.location IS
    'Origin location, EXACTLY as published by USDA (source field `location`). Never normalized. '
    'All seven values MEASURED 2026-08-14 at 1,180 rows each; migration 0016 had five of them from '
    'a handoff document and `Lower Illinois` was published as `Illinois River` there. The CHECK is '
    'a TRIPWIRE for an unseen eighth, not a vocabulary: on failure, measure the new string and add '
    'it in a new migration.';

-- ---------------------------------------------------------------------------------------------
-- 2. The rate becomes nullable.
-- ---------------------------------------------------------------------------------------------
--
-- NULL MEANS "USDA PUBLISHED NO RATE FOR THIS SEGMENT-WEEK", which in 661 of 774 cases means the
-- river was closed. It does not mean "not yet ingested" and it must never be read as zero: a rate
-- of zero is a claim that barge freight was free that week, which is never true and drags every
-- average over the series toward it.
ALTER TABLE barge_rates ALTER COLUMN pct_of_tariff DROP NOT NULL;

-- The positivity constraint is restated with its NULL case SPELLED OUT.
--
-- `pct_of_tariff > 0` already admits NULL - in SQL a CHECK passes when its expression is NULL
-- rather than false - so this rewrite changes no behaviour whatsoever. It is here because the
-- previous form READS as a rejection of NULL, and the next person to add a constraint beside it
-- will copy whichever form is present. An invariant that depends on knowing SQL's three-valued
-- CHECK semantics is one that gets accidentally tightened.
ALTER TABLE barge_rates DROP CONSTRAINT barge_rates_pct_positive;

ALTER TABLE barge_rates ADD CONSTRAINT barge_rates_pct_positive
    CHECK (pct_of_tariff IS NULL OR pct_of_tariff > 0);

COMMENT ON COLUMN barge_rates.pct_of_tariff IS
    'Percent of tariff, EXACTLY as published. Never divided by 100, never rounded. NULLABLE: USDA '
    'publishes no `rate` field at all for 774 of 8,260 nearby records, 661 of them in '
    'December-March and 729 on the two upper segments - WINTER NAVIGATION CLOSURE, a fact about '
    'the river rather than a gap in ingest. NULL is never coalesced to 0: a zero would claim barge '
    'freight was free that week. The row is always written (CLAUDE.md section 16).';

COMMENT ON TABLE barge_rates IS
    'Weekly barge freight rates from USDA AgTransport, as published. NOT a hypertable, '
    'deliberately - see migration 0014 for the arithmetic. The target variable of this project. A '
    'NULL pct_of_tariff is a week USDA published no rate for, usually a winter closure on the '
    'upper river - see 0017. Cairo-Memphis, the segment the output contract names, carries 1,177 '
    'of 1,180 weeks.';
