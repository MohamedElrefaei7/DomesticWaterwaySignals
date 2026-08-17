-- 0026 — backups: the evidentiary record of whether this database is restorable.
--
-- job_runs (0002) says whether the backup JOB ran. This says whether the ARCHIVE IT PRODUCED can
-- be restored, which is a different question and the only one that matters at 3am. A green
-- job_runs row over an archive nobody has ever restored is CLAUDE.md § 2's theme 1 in its purest
-- form: every layer reported success and the thing downstream — a working database — was never
-- checked.
--
-- ONE ROW PER VERIFIED BACKUP, AND NO ROW AT ALL FOR A FAILED ONE. A failed run's record lives in
-- job_runs, where failures belong. Never a `verified = false` placeholder row: a later query for
-- "the most recent backup" would find it and report a backup that does not exist.

CREATE TABLE backups (
    backup_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz NOT NULL DEFAULT now(),

    -- Where the archive actually is. The restore test downloads THIS, from S3, rather than
    -- reading a local staging copy - the local file passing proves nothing about what is in the
    -- bucket, and on a healthy instance the local file has already been deleted.
    s3_bucket       text   NOT NULL,
    s3_key          text   NOT NULL,

    -- NOT NULL, because a null byte size would read as "we didn't measure" and be treated as
    -- fine. It is also the input to the next run's size floor.
    byte_size       bigint NOT NULL,

    -- PER-TABLE COUNTS, NEVER A TOTAL. An object mapping qualified table name to row count,
    -- captured inside the dump's own snapshot (pg_export_snapshot + pg_dump --snapshot).
    --
    -- A total cannot distinguish "one table lost its rows and another gained some" from
    -- "identical", and per-table exactness is the entire value of the monthly restore test. The
    -- mapping includes ZERO-ROW tables: a table that vanishes between dump and restore is only
    -- detectable if its absence from the restored key set can be compared against its presence
    -- here.
    row_counts      jsonb  NOT NULL,

    -- Compression is a headline measurement for this project and is exactly the kind of thing
    -- that silently does not survive a restore.
    compressed_chunks integer NOT NULL,

    -- NOT NULL. There is no "we didn't check" state: an unverified archive does not get a row.
    verified        boolean NOT NULL,
    verified_at     timestamptz,

    -- The three columns the restore test may fill in later. Everything else is insert-once.
    restore_verified_at     timestamptz,
    restore_verified_counts jsonb,
    restore_notes           text,

    -- "Verified but we don't know when" should be unrepresentable.
    CONSTRAINT backups_verified_requires_timestamp
        CHECK (verified = false OR verified_at IS NOT NULL),

    -- row_counts is a MAPPING, not an array and not a scalar total. Enforced here because the
    -- restore test's comparison assumes it can take the key set.
    CONSTRAINT backups_row_counts_is_object
        CHECK (jsonb_typeof(row_counts) = 'object'),

    CONSTRAINT backups_restore_counts_is_object
        CHECK (restore_verified_counts IS NULL
               OR jsonb_typeof(restore_verified_counts) = 'object')
);

CREATE INDEX backups_started_at_idx ON backups (started_at DESC);

-- The size floor's lookup: the most recent VERIFIED row.
CREATE INDEX backups_verified_started_at_idx
    ON backups (started_at DESC) WHERE verified;


-- ---------------------------------------------------------------------------------------------
-- Insert-once, with exactly three updatable columns, enforced by trigger.
-- ---------------------------------------------------------------------------------------------
--
-- Convention would be a comment saying "don't update this". This project enforces structurally,
-- and this table is the evidence for whether the backups work — an audit record that anything can
-- quietly rewrite is not an audit record.
--
-- COLUMN BY COLUMN, NOT `OLD IS DISTINCT FROM NEW` ON THE WHOLE ROW. The whole-row form is one
-- line and cannot say WHICH column changed, so the error it raises sends the reader to diff two
-- rows by hand at exactly the moment they are already confused about why an update failed.
--
-- The three permitted columns are the restore test's own output. Everything describing the
-- archive itself is fixed at insert: the bytes on S3 do not change, so neither do the facts
-- about them.

CREATE FUNCTION backups_forbid_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    changed text;
BEGIN
    IF NEW.backup_id         IS DISTINCT FROM OLD.backup_id         THEN changed := 'backup_id';
    ELSIF NEW.started_at     IS DISTINCT FROM OLD.started_at        THEN changed := 'started_at';
    ELSIF NEW.finished_at    IS DISTINCT FROM OLD.finished_at       THEN changed := 'finished_at';
    ELSIF NEW.s3_bucket      IS DISTINCT FROM OLD.s3_bucket         THEN changed := 's3_bucket';
    ELSIF NEW.s3_key         IS DISTINCT FROM OLD.s3_key            THEN changed := 's3_key';
    ELSIF NEW.byte_size      IS DISTINCT FROM OLD.byte_size         THEN changed := 'byte_size';
    ELSIF NEW.row_counts     IS DISTINCT FROM OLD.row_counts        THEN changed := 'row_counts';
    ELSIF NEW.compressed_chunks IS DISTINCT FROM OLD.compressed_chunks
                                                                    THEN changed := 'compressed_chunks';
    ELSIF NEW.verified       IS DISTINCT FROM OLD.verified          THEN changed := 'verified';
    ELSIF NEW.verified_at    IS DISTINCT FROM OLD.verified_at       THEN changed := 'verified_at';
    END IF;

    IF changed IS NOT NULL THEN
        RAISE EXCEPTION
            'backups is insert-once: refusing to update column % on backup_id=%',
            changed, OLD.backup_id
            USING HINT = 'CLAUDE.md section 3. Only restore_verified_at, restore_verified_counts '
                         'and restore_notes may change after insert. A human making a one-off '
                         'correction must first run ALTER TABLE backups DISABLE TRIGGER '
                         'backups_forbid_update, and should record why.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER backups_forbid_update
    BEFORE UPDATE ON backups
    FOR EACH ROW
    EXECUTE FUNCTION backups_forbid_update();


-- ---------------------------------------------------------------------------------------------
-- No deletes, unconditionally.
-- ---------------------------------------------------------------------------------------------
--
-- Same shape as job_runs_forbid_delete (0002) and for the same reason. Silent deletability of an
-- audit table is how the audit stops being one: the rows that would be deleted first are the ones
-- recording a backup somebody would rather had not happened that way.
--
-- A human doing a genuine correction disables the trigger explicitly, which is a visible act
-- rather than a stray WHERE clause that matched more than intended.

CREATE FUNCTION backups_forbid_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'backups is append-only: refusing to delete backup_id=% (s3_key=%)',
        OLD.backup_id, OLD.s3_key
        USING HINT = 'CLAUDE.md section 3. A human making a one-off correction must first run '
                     'ALTER TABLE backups DISABLE TRIGGER backups_forbid_delete, and should '
                     'record why.';
END;
$$;

CREATE TRIGGER backups_forbid_delete
    BEFORE DELETE ON backups
    FOR EACH ROW
    EXECUTE FUNCTION backups_forbid_delete();
