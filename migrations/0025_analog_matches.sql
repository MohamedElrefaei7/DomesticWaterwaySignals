-- 0025 — analog_matches: the k nearest historical events behind one query, WITH THEIR DISTANCES.
--
-- ---------------------------------------------------------------------------------------------
-- THE DISTANCE IS THE COLUMN THIS TABLE EXISTS FOR
-- ---------------------------------------------------------------------------------------------
--
-- There is NO SIMILARITY CUTOFF in this project (app/analogs/parameters.py: SIMILARITY_CUTOFF is
-- None, deliberately). "How similar is similar enough" is a claim nobody can make before looking
-- at a distribution of distances, and it is a human's to make under CLAUDE.md § 1.
--
-- So the engine returns the k nearest whatever they are, and STORES WHAT EACH ONE COST. Without
-- the distance, a match table is a list of dates that reads as "these are comparable events" - and
-- the tenth-nearest event in a thin history is not comparable to anything, while looking identical
-- in a row to the first. The distance is what makes a bad match visibly a bad match, and step 2 of
-- the live procedure is a human reading this column before setting any cutoff.
--
-- ---------------------------------------------------------------------------------------------
-- outcome_log_return IS STORED FOR EVERY MATCH, INCLUDING ON REFUSED QUERIES
-- ---------------------------------------------------------------------------------------------
--
-- This looks like it contradicts the rule that a refused query carries no estimate. It does not,
-- and the distinction is worth stating precisely because it is one line either way:
--
--     THE TABLE IS THE RECORD.        Every analog measured, every outcome computed, refused or
--                                     not - the same argument `signals` makes for its null rows.
--     THE RETURNED RESULT IS A CLAIM. A refused query's returned structure carries NO median, no
--                                     range, no direction and no per-analog outcome, so nothing
--                                     downstream can render or aggregate one.
--
-- One is a research log a human reads deliberately; the other is what a UI or an API hands to
-- somebody who did not ask how it was made. Collapsing them would mean either fabricating a gap in
-- the record or shipping an estimate the gate refused - and app/analogs/gate.py enforces the
-- second half by never computing the aggregate at all, so there is nothing to withhold.
--
-- NULL means the window was incomplete: the rate series does not reach `outcome_window_days` past
-- that event, or an endpoint is a week USDA published no rate for (winter closure, 774 of 8,260
-- nearby records - migration 0017). Those matches are excluded from the gate's count AND KEPT AS
-- ROWS, because "the tenth analog had no measurable outcome" and "there was no tenth analog" are
-- different facts about the history.

CREATE TABLE analog_matches (
    query_id bigint NOT NULL REFERENCES analog_queries (query_id) ON DELETE CASCADE,

    -- 1 is the nearest. Part of the key rather than an ordering hint: the k nearest ARE an ordered
    -- list, and a match table without rank would need a re-sort by distance to reproduce what the
    -- engine actually returned - which silently becomes a different list wherever two distances
    -- tie.
    rank integer NOT NULL,

    -- The detection date of the historical event. THE COLLAPSED EVENT'S START, never a raw
    -- detection date - see 0024. The event's eventual depth and duration are outcomes and are
    -- deliberately not columns here: an event defined by how it turned out is an event defined
    -- using its own future, which is the lookahead this whole phase is arranged around
    -- (CLAUDE.md § 19).
    event_start date NOT NULL,

    -- Unweighted Euclidean distance over the z-scored feature vector, in the query's own units.
    -- COMPARABLE WITHIN A QUERY AND NOT ACROSS QUERIES: the z-scores are computed from the site's
    -- own history up to that query's as_of_date, so two queries scale their axes differently. The
    -- parameters_hash and as_of_date on the parent row are what say so.
    distance double precision NOT NULL,

    -- ln(rate at event_start + window / rate at event_start). NULL where the window was
    -- incomplete; see the block above. Log rather than percent for app/features/targets.py's
    -- reason: +625% and -86% are the same move in opposite directions, and anything averaging
    -- percent changes learns that asymmetry as a fact about barge freight.
    outcome_log_return double precision,

    PRIMARY KEY (query_id, rank),

    -- A distance is a norm. A negative one means the metric returned something that is not a
    -- distance, which would sort the nearest analogs to the far end of the list.
    CONSTRAINT analog_matches_distance_non_negative
        CHECK (distance >= 0.0),

    CONSTRAINT analog_matches_rank_positive
        CHECK (rank >= 1),

    -- One event cannot be two of its own analogs. A duplicate event_start within a query means the
    -- separation collapse did not run, which is the inflation 0024 describes - and it would arrive
    -- looking like extra evidence.
    CONSTRAINT analog_matches_one_row_per_event UNIQUE (query_id, event_start)
);

-- Every match for a query, in rank order: how a human reads one answer.
CREATE INDEX analog_matches_query_rank_idx ON analog_matches (query_id, rank);

-- The distribution of distances across every query, which is what a cutoff would be set from.
CREATE INDEX analog_matches_distance_idx ON analog_matches (distance);

COMMENT ON TABLE analog_matches IS
    'The k nearest historical events behind one analog query, with the distance each one cost. '
    'There is deliberately no similarity cutoff in this project - "how similar is similar enough" '
    'is a human decision under CLAUDE.md section 1, and it cannot be made before somebody has '
    'looked at a distribution of distances. This table is that distribution.';

COMMENT ON COLUMN analog_matches.distance IS
    'Unweighted Euclidean over the z-scored feature vector. COMPARABLE WITHIN A QUERY, NOT ACROSS '
    'QUERIES: z-scores come from the site''s own history up to that query''s as_of_date, so two '
    'queries scale their axes differently. Unweighted because a fitted weight vector is in-sample '
    'optimization wearing a similarity metric''s clothes, and it would be invisible in the output.';

COMMENT ON COLUMN analog_matches.outcome_log_return IS
    'Forward log-return of the Cairo-Memphis nearby rate over outcome_window_days from '
    'event_start. Stored for every match INCLUDING ON REFUSED QUERIES - the table is the record, '
    'while the engine''s returned result is a claim and carries no estimate when the gate refuses. '
    'NULL means the window was incomplete: the series does not reach that far, or an endpoint week '
    'has no published rate (winter closure).';
