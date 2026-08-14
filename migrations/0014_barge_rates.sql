-- 0014 — barge_rates: the slow target the whole thesis points at.
--
-- Weekly barge freight rate as a percent of tariff, per origin segment, per horizon. This is the
-- variable the analog engine predicts the direction of; everything in Phase 3 is the leading side
-- of the same pair.
--
-- ---------------------------------------------------------------------------------------------
-- THIS IS NOT A HYPERTABLE, AND THAT IS A DECISION WITH A MEASUREMENT BEHIND IT
-- ---------------------------------------------------------------------------------------------
--
-- "Make it a hypertable like the others" is the consistency argument to expect, so here is the
-- arithmetic it has to beat. Seven segments times three horizons times ~52 weeks is roughly 1,100
-- rows a year - on the order of ten thousand rows for a decade, against 258,739 in
-- gauge_readings_iv and 30,539 in gauge_readings_daily.
--
-- AND THE MEASUREMENT FROM PHASE 3 POINTS THE OTHER WAY. Compressing the two reading tables was
-- worth doing and was reported honestly: 3.36:1 on the instantaneous table, 7.65:1 on the daily
-- one, with most of the win in INDEX bytes rather than table bytes - and the explicit conclusion
-- recorded in CONTEXT.md is that **at ~290k rows Postgres alone would have been entirely
-- adequate**, and TimescaleDB is a demonstrated engineering choice rather than a necessity the
-- data forced. At ten thousand rows there is not even a choice to demonstrate: chunking would
-- produce a handful of chunks whose per-chunk metadata rivals their contents, and compression
-- would buy kilobytes on a table small enough to fit in cache.
--
-- A plain table with ordinary indexes. Ceremony with no measurement behind it is what this
-- project spends its comments arguing against.
--
-- ---------------------------------------------------------------------------------------------
-- THE KEY INCLUDES `horizon`, AND WITHOUT IT THE TABLE SILENTLY KEEPS ONE ROW OF THREE
-- ---------------------------------------------------------------------------------------------
--
-- USDA publishes, for the same segment and the same week, a nearby rate and forward rates one and
-- three months out. THESE ARE THREE DIFFERENT FACTS, not three measurements of one. Keyed without
-- `horizon`, an upsert over one week's publication writes three rows onto the same key and keeps
-- whichever arrived last - which is not even deterministic, and which produces a series that is
-- mostly nearby rates with occasional forward ones mixed in. Every aggregate over it is wrong and
-- nothing about the series says so. This is the same reasoning that put `stat_cd` in the daily
-- table's key (0008) and it is the same failure it prevents.

CREATE TABLE barge_rates (
    -- The origin segment as published (e.g. the Cairo-Memphis reach). Text, as published: the
    -- segment vocabulary is USDA's, and normalizing it here would put a modelling decision in the
    -- ingest layer.
    segment text NOT NULL,

    -- THE PUBLISHED PERIOD LABEL, STORED AS THE CALENDAR DATE IT STATES.
    --
    -- Same rule as the daily table's `date` (0008, CLAUDE.md § 15): no timezone arithmetic is
    -- applied at any point, and this value never goes near the instantaneous timestamp converter.
    -- A week ending 2022-10-04 is that date wherever it is read from; routing it through
    -- `.astimezone()` would make the local machine's zone decide which week a rate belongs to.
    week_ending date NOT NULL,

    -- 'nearby' | '1_month' | '3_month'. Constrained rather than free text so a fourth spelling of
    -- an existing horizon lands as an error instead of as a fourth series nothing queries.
    horizon text NOT NULL,

    -- THE RATE, EXACTLY AS PUBLISHED, AS A PERCENT OF TARIFF.
    --
    -- Not divided by 100, not converted to a decimal fraction, not rounded. 112.5 is stored as
    -- 112.5. `numeric`, not double precision, so the published decimal is the stored decimal.
    --
    -- Converting the unit here would be a MODELLING DECISION IN THE INGEST LAYER, which is the
    -- error the raw-versus-hourly question avoided in Phase 3: the ingest layer stores what the
    -- source published, and every consumer can see what that was. A ratio silently stored where a
    -- percent is documented is off by two orders of magnitude in a direction that looks plausible
    -- on a chart with no axis labels.
    pct_of_tariff numeric NOT NULL,

    PRIMARY KEY (segment, week_ending, horizon),

    CONSTRAINT barge_rates_horizon_known
        CHECK (horizon IN ('nearby', '1_month', '3_month')),

    -- A percent of tariff is a positive quantity. Zero would mean a published rate of zero percent
    -- of tariff, which is not a thing that happens and is what an unparsed empty field would
    -- become if anything ever coerced one.
    CONSTRAINT barge_rates_pct_positive
        CHECK (pct_of_tariff > 0)
);

-- The read direction: one segment's series, newest first, usually for one horizon. Phase 5's
-- features and Phase 7's analog search both walk a segment's history backwards.
CREATE INDEX barge_rates_segment_horizon_week_idx
    ON barge_rates (segment, horizon, week_ending DESC);

-- The other read direction: everything published for one week, across segments. The corridor-wide
-- view a signal renders.
CREATE INDEX barge_rates_week_idx ON barge_rates (week_ending DESC);

COMMENT ON TABLE barge_rates IS
    'Weekly barge freight rates from USDA AgTransport, as published. NOT a hypertable, '
    'deliberately - see migration 0014 for the arithmetic. The target variable of this project.';

COMMENT ON COLUMN barge_rates.pct_of_tariff IS
    'Percent of tariff, EXACTLY as published. Never divided by 100, never rounded. Unit conversion '
    'is a modelling decision and does not belong in ingest (CLAUDE.md section 16).';

COMMENT ON COLUMN barge_rates.week_ending IS
    'The published week-ending label, stored as the calendar date stated. No timezone arithmetic '
    'is applied at any point (CLAUDE.md section 15).';
