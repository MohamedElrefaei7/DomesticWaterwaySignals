-- migrate:no-transaction
-- 0003 — the index behind "last success".
--
-- The marker on line 1 above is load-bearing, and it is only honoured on line 1. CREATE INDEX
-- CONCURRENTLY cannot run inside a transaction block, so the runner applies this file with
-- autocommit and then records it in schema_migrations in a separate transaction immediately
-- afterwards.
--
-- THE HONEST COST: that sequence is not atomic. A crash between the CREATE INDEX below and the
-- schema_migrations INSERT leaves this migration applied and unrecorded, and the next run would
-- try it again. IF NOT EXISTS makes that particular replay harmless, but the general hazard is
-- real and unavoidable — which is exactly why this path is opt-in per file rather than the
-- runner's default. Every other migration gets one transaction covering both the change and the
-- record of it.
--
-- CONCURRENTLY is not ceremony on a table the whole system writes to on every job: the ordinary
-- form takes a lock that blocks every INSERT for the duration, which on this table means blocking
-- the @job decorator, which means blocking every job in the system.
--
-- The index is partial, on status = 'success', because that is the only status the query it
-- serves looks at. CLAUDE.md § 4: "last success" is the most recent SUCCESS row's finished_at,
-- never the most recent row of any status — a job failing every night has recent activity and no
-- recent success, and a MAX(finished_at) across all statuses reports it healthy.

CREATE INDEX CONCURRENTLY IF NOT EXISTS job_runs_last_success_idx
    ON job_runs (job_name, finished_at DESC)
    WHERE status = 'success';
