-- 0001 — extensions.
--
-- TimescaleDB is the reason this project runs its own Postgres rather than RDS, and the hypertable
-- that justifies it arrives in Phase 3. The extension is created here, first, because CREATE
-- EXTENSION is exactly the kind of statement that gets run by hand once on a laptop and then
-- forgotten when a database is rebuilt from a dump on a fresh machine.
--
-- IF NOT EXISTS matters: this file may be applied against a database restored from a dump that
-- already carries the extension, and a bare CREATE EXTENSION would abort the whole run.

CREATE EXTENSION IF NOT EXISTS timescaledb;
