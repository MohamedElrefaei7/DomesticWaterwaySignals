-- 0002 — job_runs: the record of every scheduled unit this system ever runs.
--
-- This table exists before the first ingest client on purpose. CLAUDE.md § 2's theme 1 is a layer
-- reporting success while the thing downstream gets nothing; the prior project ran for two and a
-- half months with orchestration recording "Completed" over a stack that was entirely down. The
-- cheapest defence is that the very first row of real data to land in this system lands with a
-- job_runs row already watching it.

CREATE TABLE job_runs (
    run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The join key between the cadence table and the heartbeat (CLAUDE.md § 4). Stable
    -- identifier, not a display string: renaming one orphans that job's entire history and the
    -- heartbeat then reports a brand-new job that has never succeeded.
    job_name      text NOT NULL,

    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,

    -- Constrained BY THE DATABASE, not by a Python enum and not by a docstring (CLAUDE.md § 4).
    -- A Python-side check protects only the paths that go through Python; this one also rejects
    -- the psql session where a human is repairing something at 2am.
    --
    --   running — the row the @job decorator commits BEFORE calling the wrapped function
    --   success — closed normally
    --   failed  — the wrapped function raised; error_message is populated
    --   missed  — the scheduler misfired, so the function never ran at all and the decorator
    --             never saw it. Written by a scheduler event listener instead. Without this
    --             status a missed run is indistinguishable from a job that was never scheduled.
    status        text NOT NULL,

    -- Rows written TO THE DATABASE — never rows examined, fetched, or processed (CLAUDE.md § 4).
    --
    -- NULL and 0 are different facts and both are meaningful. 0 means the job ran and wrote
    -- nothing. NULL means the job does not report a row count at all. Code that coalesces one
    -- into the other destroys the distinction that makes "wrote nothing" detectable, and
    -- "wrote nothing" is the shape of every failure in CLAUDE.md § 2's theme 1.
    rows_written  bigint,

    error_message text,

    CONSTRAINT job_runs_status_check
        CHECK (status IN ('running', 'success', 'failed', 'missed'))
);

-- The heartbeat's per-job lookup, and the ordinary "what happened last night" query.
CREATE INDEX job_runs_job_name_started_at_idx
    ON job_runs (job_name, started_at DESC);


-- ---------------------------------------------------------------------------------------------
-- Append-only, enforced by trigger.
-- ---------------------------------------------------------------------------------------------
--
-- CLAUDE.md § 4: job_runs is append-only; no code path deletes from it. "When data is lost,
-- record the loss — never synthesize a replacement," and the same principle applies to the
-- record of the loss itself.
--
-- NOTE, because "append-only" read strictly would forbid it: UPDATE REMAINS PERMITTED. The @job
-- decorator's entire design depends on it — it commits a `running` row before the work starts and
-- then comes back to close that same row as `success` or `failed`. A trigger written as
-- BEFORE DELETE OR UPDATE would be a stricter reading of the words and would break the mechanism
-- the words exist to protect. Deletion is what this forbids.
--
-- FOR EACH ROW, not FOR EACH STATEMENT: this fires on rows that would actually be removed, so it
-- raises on a real deletion and stays quiet for a no-op DELETE that matches nothing.
--
-- The contract allows a human to make a one-off correction with a stated reason. Doing so means
-- explicitly running `ALTER TABLE job_runs DISABLE TRIGGER job_runs_forbid_delete`, which is the
-- point: deleting from this table becomes a deliberate, conspicuous act instead of a stray
-- statement with a WHERE clause that was wider than intended.

CREATE FUNCTION job_runs_forbid_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'job_runs is append-only: refusing to delete run_id=% (job_name=%, status=%)',
        OLD.run_id, OLD.job_name, OLD.status
        USING HINT = 'CLAUDE.md section 4. A human making a one-off correction must first run '
                     'ALTER TABLE job_runs DISABLE TRIGGER job_runs_forbid_delete, and should '
                     'record why.';
END;
$$;

CREATE TRIGGER job_runs_forbid_delete
    BEFORE DELETE ON job_runs
    FOR EACH ROW
    EXECUTE FUNCTION job_runs_forbid_delete();
