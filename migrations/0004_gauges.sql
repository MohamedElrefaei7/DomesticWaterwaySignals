-- 0004 — gauges: the site registry, and the record of what each site actually serves.
--
-- The four rows at the bottom of this file are a HUMAN'S MODELLING DECISION (CLAUDE.md § 1). The
-- gauge site list is on the never-invent list, and tests/ingest/test_gauge_seed.py asserts exact
-- set equality against these four IDs so that adding a fifth is a test failure rather than a
-- diff nobody reads. A Cairo, IL site number was investigated for this commit and NOT confirmed;
-- it is deliberately absent rather than guessed.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS TABLE CARRIES PER-SITE PARAMETERS AND CADENCE RATHER THAN A BARE SITE LIST
-- ---------------------------------------------------------------------------------------------
--
-- Measured against the live USGS instantaneous-values service on 2026-08-13, and it contradicts
-- what the project plan assumed:
--
--   * A request for a series a site does not serve returns HTTP 200 with "timeSeries": [].
--     No error, no flag, no marker. When several sites are requested in one call, the missing
--     ones are simply absent from the array while the others return normally.
--   * Parameter availability is PER SITE. Stage (00065) is absent at Memphis and Vicksburg —
--     USGS states their gage height is furnished by the USACE Memphis District.
--   * Native cadence is PER SITE: 15, 30, and 60 minutes across these four. None is uniformly
--     15-minute.
--   * Period of record is PER SITE. Vicksburg's IV record appears to begin 2008-01-01, not the
--     2007-10-01 the plan assumed for everything.
--
-- The first of those is CLAUDE.md § 2's theme 1 in its purest form: a layer reporting success
-- while the thing downstream gets nothing. The defence is that the ingest client asserts the
-- returned (site, parameter) set equals the REQUESTED set — and it cannot do that unless
-- something records, per site, what there was to ask for. That is this table. A bare site list
-- with parameters assumed uniform is precisely what makes a permanently-vanished series
-- indistinguishable from a satisfied request.

CREATE TABLE gauges (
    -- USGS site number, as a string. Not an integer: these are zero-padded identifiers
    -- ('07010000'), and the first thing an integer column does is eat the leading zero.
    usgs_site_id text PRIMARY KEY,

    name  text NOT NULL,
    river text NOT NULL,

    -- NULL where unknown, never estimated. An interpolated river mile is a fabricated number
    -- that looks plausible and that nothing downstream can check.
    river_mile numeric,

    -- NULLABLE AND SEEDED NULL, deliberately. See the note above the seed rows: this commit's
    -- agent had no way to verify coordinates, and a gauge plotted at confidently wrong
    -- coordinates is a map that lies with no layer able to notice. They are populated from the
    -- USGS site service by a human; until then the column is honestly empty.
    lat numeric,
    lon numeric,

    -- Corridor priority. All four seeded sites are tier 1; the column exists so a later, wider
    -- site list can be ingested at a lower priority without a schema change.
    tier int NOT NULL,

    -- The parameter codes THIS SITE ACTUALLY SERVES, as measured. '00060' is discharge in cubic
    -- feet per second; '00065' is gage height in feet.
    --
    -- This is the set the ingest client builds its requested (site, parameter) pairs from, and
    -- therefore the set it asserts the response against. Adding a code here that the site does
    -- not serve does not produce partial data — it produces a hard failure on every run, which
    -- is the intended direction to fail in.
    available_params text[] NOT NULL,

    -- DOCUMENTATION OF WHAT WAS OBSERVED. NOT A FILTER, AND NOT A CONTRACT.
    --
    -- The ingest client stores whatever timestamps arrive and never consults this column. Gaps
    -- are ordinary — the first eight St. Louis readings on 2026-08-01 skip 02:30 — so a client
    -- that used this value to decide which readings were "expected" would either discard real
    -- readings or manufacture missing ones. It is recorded so a human reading the data knows
    -- why St. Louis has half as many rows per day as Baton Rouge.
    native_cadence_minutes int NOT NULL,

    -- The start of this site's instantaneous-values record, per site (see the note above).
    -- The backfill walks from here and NEVER earlier: silently walking back further "looking for
    -- data" would turn a wrong seed value into a slow, invisible sweep of empty windows.
    record_start date NOT NULL,

    -- An empty array would pass a NOT NULL check and then produce a request for nothing, which
    -- the client's set-equality assertion would find trivially satisfied. Vacuous success is the
    -- failure mode this whole table exists to prevent, so it is rejected by the database.
    CONSTRAINT gauges_available_params_non_empty
        CHECK (cardinality(available_params) > 0),

    CONSTRAINT gauges_native_cadence_positive
        CHECK (native_cadence_minutes > 0),

    -- Eight digits, as a string. Catches the integer-typed site number that lost its leading
    -- zero somewhere upstream, which would otherwise be inserted happily as '7010000'.
    CONSTRAINT gauges_site_id_shape
        CHECK (usgs_site_id ~ '^[0-9]{8}$')
);


-- ---------------------------------------------------------------------------------------------
-- The seed. Four rows, human-owned (CLAUDE.md § 1).
-- ---------------------------------------------------------------------------------------------
--
-- ONE ROW PER LINE, and the layout is load-bearing: app/ingest/gauges.py parses this statement
-- so that the unit tier — which has no database — can assert what is seeded. The alternative
-- was a second copy of these four rows in Python, which is two tables of the same fact and
-- diverges silently (CLAUDE.md § 4).
--
-- lat/lon ARE NULL AND MUST BE FILLED BY A HUMAN. They are not in this file because this
-- commit's agent could not verify them and coordinates typed from recollection are exactly the
-- plausible-looking fabrication CLAUDE.md § 1 forbids. Obtain them from the USGS site service
-- and apply them as a NEW numbered migration (never by editing this applied one):
--
--   curl 'https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=07010000,07032000,07289000,07374000'
--
-- record_start values are the plan's assumption, corrected only where measurement contradicted
-- it (Vicksburg). They are UNCONFIRMED for the other three, and live verification step 5
-- compares each min(ts) against them. A large discrepancy means THIS SEED is what to fix.

INSERT INTO gauges (usgs_site_id, name, river, river_mile, lat, lon, tier, available_params, native_cadence_minutes, record_start) VALUES
    ('07010000', 'Mississippi River at St. Louis, MO', 'Mississippi', NULL, NULL, NULL, 1, ARRAY['00060'], 30, DATE '2007-10-01'),
    ('07032000', 'Mississippi River at Memphis, TN', 'Mississippi', NULL, NULL, NULL, 1, ARRAY['00060'], 60, DATE '2007-10-01'),
    ('07289000', 'Mississippi River at Vicksburg, MS', 'Mississippi', NULL, NULL, NULL, 1, ARRAY['00060'], 60, DATE '2008-01-01'),
    ('07374000', 'Mississippi River at Baton Rouge, LA', 'Mississippi', NULL, NULL, NULL, 1, ARRAY['00060'], 15, DATE '2007-10-01');


-- Every site seeded above serves discharge and nothing else, and that is a deliberate scope
-- decision rather than an oversight — see CLAUDE.md § 14 and CONTEXT.md. Stage is absent at two
-- of the four sites, and DERIVING it from discharge through a USGS rating curve is rejected:
-- USGS publishes ratings as provisional and shifting with channel features, so applying a
-- current rating to 2008 discharge yields a stage that gauge never read. That is a fabricated
-- number that looks plausible, in a layer that has no confidence gate to catch it.
