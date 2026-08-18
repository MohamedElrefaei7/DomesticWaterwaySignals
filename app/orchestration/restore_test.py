"""The monthly restore test: download from S3, restore into a throwaway, compare exactly.

A backup nobody has restored is a backup nobody knows they have. Part 6's job verifies that an
archive can be READ - `pg_restore -f /dev/null` walks every block and reconstructs every object in
memory. This one verifies that it can be RESTORED INTO A DATABASE, which is a different claim:
extension state, hypertable metadata, roles and grants all live outside the block stream.

The decisions that carry the most weight, in order:

  - THE ARCHIVE IS DOWNLOADED FROM S3, never read from local staging. The local file passing proves
    nothing about what is in the bucket, and on a healthy instance the local file was deleted the
    moment its upload was verified. This is also the only thing that exercises the IAM READ path,
    which is otherwise untested until the day it matters.
  - THE RESTORE IS WRAPPED IN timescaledb_pre_restore() / timescaledb_post_restore(), AND THE
    EXTENSION IS CREATED BEFORE EITHER. Without the wrapper the restore APPEARS TO SUCCEED while
    hypertable and chunk metadata is wrong - CLAUDE.md § 2's theme 1 exactly, surfacing much later
    as queries that return plausible partial results. The CREATE EXTENSION is new in Phase 12 and
    is not optional: measured against 2.26.2, `timescaledb_pre_restore()` DOES NOT EXIST in a
    database created from template0. It was invisible until now because the timescaledb image's
    own init scripts create the extension in POSTGRES_DB, so the throwaway CONTAINER always had it.

  - THE THROWAWAY IS A DATABASE ON THE EXISTING SERVER, NOT A CONTAINER, AND THAT IS A TRADE.
    Spawning a container from inside the scheduler container requires the host's Docker socket,
    which is root-equivalent on the host (CLAUDE.md § 22). Two things are lost and neither is
    closed: roles are CLUSTER-wide, so create_roles becomes a no-op in production runs and its
    production path is untested; and the fresh-cluster property is gone, so a dump depending on
    some cluster-level object would restore cleanly here and fail on a real rebuild. This job now
    answers "does this archive restore into THIS server" rather than "into a new one".
  - ROLES ARE CREATED, NOT STRIPPED. `--no-owner --no-privileges` makes any restore succeed, at the
    cost of never exercising the grants - and the read-only `waterway_api` role is a proven
    invariant of this system (§ 20). After restoring, that role is made to attempt a DELETE and
    must be refused. That assertion is the only thing proving the security property is IN THE
    BACKUP rather than only in production.
  - COUNTS MUST MATCH EXACTLY, IN BOTH KEY DIRECTIONS, WITH NO TOLERANCE. Comparing only the
    intersection hides both a dropped table and an unexpected new one. A tolerance of any size is
    a tolerance for exactly the loss this job exists to detect.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from psycopg import sql

from app import db
from app.orchestration import backup, session
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "restore_test_monthly"

SCRATCH_DIR = Path("/mnt/data/restore-test")

# The read-only role whose refusal to write is a proven invariant of this system (CLAUDE.md § 20).
READ_ONLY_ROLE = "waterway_api"

# The one table whose restored count legitimately differs from the recorded one, because Part 6
# excluded its DATA while keeping its DDL.
EXPECTED_EMPTY_TABLE = f"{backup.COUNTED_SCHEMA}.{backup.EXCLUDED_DATA_TABLE}"

# THE PREFIX IS THE FIRST HALF OF THE NAME GUARD AND IT IS NOT ALLOWED TO BE EMPTY.
# A prefix check against "" matches every string, so the guard would fail OPEN - which is why the
# second condition below is an inequality against the connected database's own name rather than a
# more careful prefix.
THROWAWAY_PREFIX = "dws_restore_test_"

# The client binaries, from the scheduler image. Same constants the backup job uses, so there is
# one place that says how postgres is invoked.
PG_RESTORE = backup.PG_RESTORE


class RestoreTestError(RuntimeError):
    """The restore test did not prove the backup restorable. No verification mark is written."""


@dataclass(frozen=True)
class Throwaway:
    """A disposable DATABASE on the production server: unique name, dropped when done.

    IT WAS A CONTAINER UNTIL PHASE 12. Spawning one from inside the scheduler container requires
    the host's Docker socket, which is root-equivalent on the host. See the module docstring for
    what that trade costs.
    """

    name: str
    url: str
    production_database: str


# ---------------------------------------------------------------------------------------------
# Selecting and fetching the archive
# ---------------------------------------------------------------------------------------------


def most_recent_verified(conn) -> dict:
    """The newest `backups` row with `verified = true`.

    Only verified rows exist by construction - Part 6 writes no row for a failed run and never a
    `verified = false` placeholder - but the predicate is written anyway, because a query that
    relies on an invariant holding elsewhere breaks silently when that invariant is relaxed.
    """
    row = conn.execute(
        "SELECT backup_id, s3_bucket, s3_key, byte_size, row_counts, compressed_chunks "
        "FROM backups WHERE verified ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RestoreTestError(
            "no verified backup on record. There is nothing to restore, which is itself the "
            "finding: either the nightly job has never succeeded or its rows were removed."
        )
    return {
        "backup_id": row[0],
        "s3_bucket": row[1],
        "s3_key": row[2],
        "byte_size": row[3],
        "row_counts": row[4] if isinstance(row[4], dict) else json.loads(row[4]),
        "compressed_chunks": row[5],
    }


def download_archive(s3, bucket: str, key: str, destination: Path) -> Path:
    """FROM S3. Never from a local staging copy.

    The local file passing proves nothing about what is in the bucket - and on a healthy instance
    it does not exist, because Part 6 deletes it once the upload is verified. Downloading is also
    the only exercise the IAM read path gets before the day somebody needs it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(destination))
    if not destination.exists() or destination.stat().st_size == 0:
        raise RestoreTestError(
            f"download of s3://{bucket}/{key} produced no bytes at {destination}"
        )
    return destination


def check_free_space(scratch: Path, byte_size: int, usage=shutil.disk_usage) -> int:
    """The archive AND the restored database both land here, alongside production data."""
    free = usage(str(scratch)).free
    required = int(byte_size * 3)
    if free < required:
        raise RestoreTestError(
            f"refusing to start: {free} bytes free on {scratch}, need {required} (3x the "
            f"{byte_size}-byte archive: the download plus the restored database). Filling this "
            f"volume would take production down with it."
        )
    return free


# ---------------------------------------------------------------------------------------------
# The throwaway database
# ---------------------------------------------------------------------------------------------


def throwaway_name() -> str:
    """A random suffix, so two runs cannot collide and neither can collide with anything real."""
    return f"{THROWAWAY_PREFIX}{secrets.token_hex(6)}"


def throwaway_url(url: str, name: str) -> str:
    """The same DSN, pointed at a different database. Nothing else about it changes."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def assert_safe_to_drop(name: str, production_database: str) -> None:
    """TWO INDEPENDENT CONDITIONS, and this is called TWICE - at creation and before the DROP.

    CLAUDE.md § 3 permits exactly one `DROP` in this system and bounds it with this function.

    THE SECOND CALL IS THE ONE THAT MATTERS AND THE ONE THAT LOOKS REDUNDANT. Checking at creation
    guards against a bad name. Checking again immediately before the drop guards against the
    variable being reassigned, shadowed, or read from a different scope in between - which is the
    only way this could ever go wrong, because by then the name has travelled through a restore, a
    comparison, and a `finally`.

    TWO CONDITIONS, NOT ONE, BECAUSE A PREFIX CHECK ALONE FAILS OPEN. If THROWAWAY_PREFIX were
    ever "" - a refactor, a config lookup that returned nothing - `startswith("")` is true of every
    string including the production database's name. The inequality does not depend on the prefix
    being anything in particular.

    The production name is the database this job is CONNECTED TO, taken from the DSN rather than
    from a POSTGRES_DB environment variable. Same fact, one fewer copy, and it cannot disagree with
    the connection the drop is issued on.
    """
    if not THROWAWAY_PREFIX:
        raise RestoreTestError(
            "THROWAWAY_PREFIX is empty, so the prefix half of the name guard matches every "
            "database name in the cluster. Refusing to drop anything."
        )
    if not name.startswith(THROWAWAY_PREFIX):
        raise RestoreTestError(
            f"refusing to touch database {name!r}: it does not start with {THROWAWAY_PREFIX!r}. "
            f"This job creates and drops exactly one database and its name is generated here; a "
            f"name that does not match came from somewhere else."
        )
    if name == production_database:
        raise RestoreTestError(
            f"refusing to touch database {name!r}: it IS the production database this job is "
            f"connected to. The prefix check passed, which means the prefix is not protecting "
            f"anything - this is the condition that does not depend on it."
        )


def create_throwaway(admin_conn, url: str, production_database: str) -> Throwaway:
    """`CREATE DATABASE ... TEMPLATE template0`, with the name guard asserted first.

    TEMPLATE template0, NEVER template1. template1 is the default and may carry local additions -
    extensions, tables, anything a previous operator installed into it - which would land in the
    throwaway and show up as tables the recorded snapshot does not have. template0 is the pristine
    baseline and is what makes the restore reproducible.

    The connection must be in autocommit: CREATE DATABASE cannot run inside a transaction block.
    """
    name = throwaway_name()
    assert_safe_to_drop(name, production_database)

    admin_conn.execute(
        sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
    )
    logger.info("created throwaway database %s", name)
    return Throwaway(
        name=name, url=throwaway_url(url, name), production_database=production_database
    )


def terminate_backends(admin_conn, name: str) -> int:
    """Kill every backend attached to that database, and NOTHING else.

    Without this the DROP fails on an open connection and the throwaway LEAKS - a database's worth
    of disk on the same volume as production, under a name nobody will recognise in a month.
    Measured 2026-08-17: with one idle session attached, `DROP DATABASE` returns
    `ERROR: database "..." is being accessed by other users`.

    SCOPED TO THAT datname, and excluding this connection's own pid. An unscoped
    pg_terminate_backend sweep is how a maintenance job takes production offline.
    """
    return int(
        admin_conn.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        ).fetchone()[0]
    )


def drop_throwaway(admin_conn, throwaway: Throwaway) -> None:
    """The one DROP this system performs. Guarded again, immediately above the statement.

    The second assertion is deliberately adjacent to the DROP rather than at the top of the
    function: what it defends against is the name changing between the check and the use, so any
    distance between them is the window it exists to close.
    """
    assert_safe_to_drop(throwaway.name, throwaway.production_database)
    killed = terminate_backends(admin_conn, throwaway.name)
    admin_conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(throwaway.name)))
    logger.info(
        "dropped throwaway database %s (terminated %d backend(s) first)", throwaway.name, killed
    )


# ---------------------------------------------------------------------------------------------
# The restore
# ---------------------------------------------------------------------------------------------


# A role name as pg_restore renders it: either a quoted identifier (with "" escaping an embedded
# quote) or a bare one. BOTH FORMS MATTER - see roles_in_archive.
_ROLE = r'"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*'

_OWNER_TO_RE = re.compile(rf"\bOWNER\s+TO\s+({_ROLE})", re.IGNORECASE)
_GRANT_TO_RE = re.compile(rf"\bGRANT\b[^;]*?\bTO\s+((?:{_ROLE})(?:\s*,\s*(?:{_ROLE}))*)",
                          re.IGNORECASE | re.DOTALL)
_REVOKE_FROM_RE = re.compile(rf"\bREVOKE\b[^;]*?\bFROM\s+((?:{_ROLE})(?:\s*,\s*(?:{_ROLE}))*)",
                             re.IGNORECASE | re.DOTALL)
_ROLE_TOKEN_RE = re.compile(_ROLE)

# Names that are not roles to create. PUBLIC is a PSEUDO-ROLE and `CREATE ROLE PUBLIC` is an error,
# so the first `GRANT ... TO PUBLIC` in an archive would abort the whole restore test before the
# restore began. Measured: this project's own archive contains both `GRANT SELECT ON TABLE
# public.probe_tbl TO PUBLIC` and `REVOKE USAGE ON SCHEMA public FROM PUBLIC`.
#
# The session keywords are here for the same reason - they parse as role names and name no role.
_NOT_A_ROLE = {"public", "current_user", "session_user", "current_role", "none"}


def _unquote_role(token: str) -> str:
    """`"Mixed-Case"` -> `Mixed-Case`, `waterway_api` -> `waterway_api`.

    THE CASE IS PRESERVED EXACTLY, and that is the whole point of parsing rendered SQL rather than
    the table of contents. Measured 2026-08-17 against a real archive:

        rendered SQL  ALTER TABLE public.probe_tbl OWNER TO "Mixed-Case_Owner";
        TOC (-l)      300; 1259 1770508 TABLE public probe_tbl Mixed-Case_Owner

    The TOC gives the name UNQUOTED. Creating `Mixed-Case_Owner` from that without quoting produces
    the role `mixed-case_owner` - a DIFFERENT role - after which the restore fails on an owner that
    exists under a name nobody created, and the error names a role that looks correct.
    """
    if token.startswith('"'):
        return token[1:-1].replace('""', '"')
    return token


def roles_in_archive(archive_path: Path, run=subprocess.run) -> list[str]:
    """Every role THE ARCHIVE references, read from the archive's own rendered SQL.

    FROM THE ARCHIVE, NOT FROM THE LIVE SOURCE DATABASE. Reading the source gives this job a
    dependency on production being reachable, and - worse - it describes the wrong thing. A role
    dropped from production after the dump would never be created, and one added after the dump
    would be created needlessly. Either way the throwaway diverges from the artifact under test,
    and the artifact is what is being verified. The archive is a fixed object; the database it came
    from has moved on.

    RENDERED SQL (`pg_restore -f -`) RATHER THAN THE TABLE OF CONTENTS (`pg_restore -l`), for two
    measured reasons:

      1. THE TOC HAS NO GRANTEES. Its ACL entries carry the object's OWNER, not who was granted
         anything. `waterway_api` owns nothing at all - it holds GRANTs - so it does not appear in
         the TOC under any object, and it is the one role whose restoration this job asserts.
      2. THE TOC UNQUOTES NAMES, which silently changes mixed-case ones. See _unquote_role.

    This is also the one place `pg_restore -l` would have been the cheaper call, and CLAUDE.md § 3
    already says why cheap reads of the table of contents are not to be trusted for anything that
    matters: the archive that was one third its correct size passed `--list` cleanly.
    """
    completed = run(
        [PG_RESTORE, "--file", "-", str(archive_path)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RestoreTestError(
            f"could not render {archive_path} in order to discover its roles: pg_restore exited "
            f"{completed.returncode}: {(completed.stderr or '').strip() or '(no stderr)'}"
        )

    sql_text = completed.stdout or ""
    if not sql_text.strip():
        raise RestoreTestError(
            f"rendering {archive_path} produced no SQL at all. An empty render means the roles "
            f"below would be an empty set, and the restore would then run with nothing created - "
            f"failing on the first OWNER TO with a message about a role rather than about this."
        )

    tokens = []
    for match in _OWNER_TO_RE.finditer(sql_text):
        tokens.append(match.group(1))
    for pattern in (_GRANT_TO_RE, _REVOKE_FROM_RE):
        for match in pattern.finditer(sql_text):
            tokens.extend(_ROLE_TOKEN_RE.findall(match.group(1)))

    roles = set()
    for token in tokens:
        name = _unquote_role(token)
        # Bare or quoted, `public` is never a role to create. A quoted "public" cannot exist as a
        # real role either, because CREATE ROLE public is rejected outright.
        if name.lower() in _NOT_A_ROLE:
            continue
        roles.add(name)

    # The read-only role, unconditionally. It may hold no grants in a given archive - an early
    # archive predating the GRANTs, say - and its restoration is the assertion this whole job is
    # built around, so it is never left to discovery.
    roles.add(READ_ONLY_ROLE)
    return sorted(roles)


def create_roles(conn, roles=(READ_ONLY_ROLE,)) -> None:
    """Create every role the archive references BEFORE restoring. NOLOGIN, and no passwords.

    The alternative is `--no-owner --no-privileges`, which makes the restore succeed by discarding
    exactly the thing worth checking. A backup whose grants were never restored is a backup that
    cannot be used to rebuild this system's security posture, and nothing would say so.

    NOLOGIN AND NO PASSWORD, DELIBERATELY. The throwaway needs these roles as targets for ownership
    and grants, never as connection identities - the read-only assertion is made with SET ROLE from
    the superuser session. Creating them LOGIN with a password would put a credential in the test
    path for no benefit at all.

    IDENTIFIERS ARE COMPOSED, NOT INTERPOLATED. These names now come from parsing an archive rather
    than from a constant, so building the statement with an f-string would put archive content into
    executed SQL. `sql.Identifier` also quotes correctly, which is what preserves a mixed-case name
    through creation - the same property _unquote_role exists to protect on the way in.

    Idempotent: `postgres` and the object owner frequently already exist in the throwaway.
    """
    for role in roles:
        conn.execute(
            sql.SQL(
                "DO $do$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {name}) "
                "THEN CREATE ROLE {ident} NOLOGIN; "
                "END IF; END $do$"
            ).format(name=sql.Literal(role), ident=sql.Identifier(role))
        )


def restore_command(*, archive_path: Path, host: str, port: int, database: str, user: str) -> list[str]:
    """pg_restore into the throwaway database. NO --no-owner, NO --no-privileges.

    A direct invocation, like the dump's: the scheduler container has no Docker socket, so the
    client is the one installed in the image (CLAUDE.md § 22).
    """
    return [
        PG_RESTORE,
        "--host", host,
        "--port", str(port),
        "--username", user,
        "--dbname", database,
        # Exit non-zero if anything at all failed, rather than restoring what it can and reporting
        # success - which is the whole failure mode this job exists to detect.
        "--exit-on-error",
        # NEVER PROMPT, and this is the DURABLE half of the 2026-08-18 pgpass fix rather than a
        # tidy-up beside it. libpq prompts when no pgpass line matches, so this job's first run
        # printed a bare `Password: ` and then, on the instance's TTY, `password authentication
        # failed for user "waterway"` - a message that sends the reader to check the credential
        # when the credential was right and one FIELD of the pgpass entry was wrong.
        # `--no-password` makes libpq fail immediately naming the missing password instead, which
        # converts this whole class from "looks like a wrong password" into "says no password was
        # supplied" - and makes it say that identically on a TTY and off one. Without it the next
        # mismatch - a changed port, a renamed user - is diagnosed from scratch. See
        # `backup.write_pgpass` for the matching rules and both measured spellings.
        "--no-password",
        str(archive_path),
    ]


def restore(
    throwaway: Throwaway, archive_path: Path, *, parts: dict, pgpass_path: Path,
    run=subprocess.run, roles=(READ_ONLY_ROLE,),
) -> None:
    """CREATE EXTENSION -> pre_restore -> pg_restore -> post_restore, reconnecting between.

    THE CREATE EXTENSION IS NEW IN PHASE 12 AND IT IS NOT OPTIONAL. Measured against TimescaleDB
    2.26.2 on 2026-08-17, in a database created `TEMPLATE template0`:

        SELECT timescaledb_pre_restore();
        ERROR:  function timescaledb_pre_restore() does not exist

    The function is owned by the extension, so a pristine database does not have it. This was
    invisible while the throwaway was a CONTAINER, because the timescale/timescaledb image's own
    init scripts run `CREATE EXTENSION` in POSTGRES_DB - so the container's database always had it
    and this code never had to.

    Pre-creating does not collide with the archive: measured on this project's own dump, pg_dump
    emits `CREATE EXTENSION IF NOT EXISTS timescaledb WITH SCHEMA public`, which is a no-op against
    an extension that is already there. `--exit-on-error` would have caught a collision loudly.

    WITHOUT THE pre/post WRAPPER the restore appears to succeed while hypertable and chunk metadata
    is wrong. The symptom arrives much later, as queries returning plausible partial results over
    chunks the catalog no longer knows about. Both functions are per-DATABASE settings, so they
    apply to the throwaway and touch nothing else in the cluster.
    """
    with db.connection(throwaway.url, autocommit=True) as conn:
        create_roles(conn, roles)
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        conn.execute("SELECT timescaledb_pre_restore()")

    completed = run(
        restore_command(
            archive_path=archive_path,
            host=parts["host"], port=parts["port"],
            database=throwaway.name, user=parts["user"],
        ),
        capture_output=True, text=True,
        env=backup.pgpass_environment(pgpass_path),
    )

    # post_restore runs on a NEW connection whatever happened, because pre_restore leaves the
    # extension in a state the database must not be abandoned in - and unlike a container, this
    # database survives the failure on purpose, so abandoning it mid-restore is a real outcome.
    with db.connection(throwaway.url, autocommit=True) as conn:
        conn.execute("SELECT timescaledb_post_restore()")

    if completed.returncode != 0:
        raise RestoreTestError(
            f"pg_restore exited {completed.returncode}:\n{(completed.stderr or '').strip()[:2000]}"
        )


def analyze(conn) -> None:
    """A restored database has NO PLANNER STATISTICS. ANALYZE is part of restoring (§ 3)."""
    conn.execute("ANALYZE")


def assert_statistics_exist(conn) -> str:
    """Assert ANALYZE's EFFECT, not its invocation.

    A step that runs ANALYZE and never checks it ran is a step that quietly stops running - the
    call gets moved, or wrapped in a condition, or the connection it ran on turns out to have been
    rolled back, and nothing anywhere says the planner is flying blind.
    """
    row = conn.execute(
        "SELECT relname, last_analyze IS NOT NULL OR last_autoanalyze IS NOT NULL "
        "FROM pg_stat_user_tables ORDER BY n_live_tup DESC NULLS LAST LIMIT 1"
    ).fetchone()

    if row is None:
        raise RestoreTestError(
            "no user tables in pg_stat_user_tables after restore - ANALYZE had nothing to do, "
            "which means the restore landed nothing."
        )
    if not row[1]:
        raise RestoreTestError(
            f"ANALYZE left no statistics on {row[0]!r}: pg_stat_user_tables reports neither "
            f"last_analyze nor last_autoanalyze. The restored database's planner has no "
            f"statistics, and every query against it will choose plans from defaults."
        )
    return row[0]


# ---------------------------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------------------------


def restored_counts(conn) -> dict[str, int]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (backup.COUNTED_SCHEMA,),
        ).fetchall()
    ]
    counts = {}
    for table in tables:
        counts[f"{backup.COUNTED_SCHEMA}.{table}"] = int(
            conn.execute(
                f'SELECT count(*) FROM "{backup.COUNTED_SCHEMA}"."{table}"'
            ).fetchone()[0]
        )
    return counts


def compare_counts(recorded: dict[str, int], restored: dict[str, int]) -> None:
    """Exact, both directions, every mismatch reported.

    NO TOLERANCE. A tolerance of any size is a tolerance for exactly the loss this job detects, and
    "±1%" over a large table is thousands of rows.

    BOTH DIRECTIONS. Comparing only the intersection hides a dropped table (recorded, not restored)
    AND an unexpected one (restored, not recorded) - and the second matters because it means the
    archive contains something production does not.

    EVERY MISMATCH, not the first. Stopping at the first turns one investigation into as many
    round trips as there are broken tables.
    """
    missing = sorted(set(recorded) - set(restored))
    unexpected = sorted(set(restored) - set(recorded))

    problems = []
    if missing:
        problems.append(f"tables recorded but NOT RESTORED: {missing}")
    if unexpected:
        problems.append(f"tables restored but NOT RECORDED: {unexpected}")

    mismatched = []
    for name in sorted(set(recorded) & set(restored)):
        expected = recorded[name]
        # The one legitimate difference, asserted explicitly rather than skipped. Part 6 excluded
        # this table's DATA while keeping its DDL, so it must come back EMPTY - and asserting the
        # expected difference is what proves the exclusion worked rather than assuming it.
        if name == EXPECTED_EMPTY_TABLE:
            if restored[name] != 0:
                mismatched.append(
                    f"{name}: expected 0 rows (its data is excluded from every dump), "
                    f"restored {restored[name]}"
                )
            continue
        if restored[name] != expected:
            mismatched.append(f"{name}: recorded {expected}, restored {restored[name]}")

    if mismatched:
        problems.append("row counts differ:\n    " + "\n    ".join(mismatched))

    if problems:
        raise RestoreTestError(
            "the restored database does not match the recorded snapshot.\n  "
            + "\n  ".join(problems)
        )


def compare_compressed_chunks(recorded: int, conn) -> int:
    """Compression surviving a dump/restore cycle is not something to assume."""
    restored = int(
        conn.execute(
            "SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed"
        ).fetchone()[0]
    )
    if restored != recorded:
        raise RestoreTestError(
            f"compressed chunk count differs: recorded {recorded}, restored {restored}. "
            f"Compression is a headline measurement for this project and is exactly the kind of "
            f"thing that silently does not survive a restore."
        )
    return restored


def assert_read_only_role_cannot_delete(conn, table: str, role: str = READ_ONLY_ROLE) -> None:
    """THE ONLY ASSERTION THAT PROVES THE SECURITY PROPERTY IS IN THE BACKUP.

    A restore with `--no-owner --no-privileges` succeeds and leaves this untestable. Making the
    restored role actually attempt a write is the difference between "the grants restored" and
    "the grants were never exercised".

    `SET ROLE`, NOT `SET LOCAL ROLE`, AND THE SWITCH'S EFFECT IS READ BACK BEFORE THE DELETE.

    This was `SET LOCAL ROLE` and it did not work. `SET LOCAL` is scoped to the enclosing
    transaction, and this connection is autocommit, so there IS no enclosing transaction and the
    setting is discarded at the end of the statement that set it. Measured 2026-08-17 against a
    real server: `current_user` after `SET LOCAL ROLE` was still the OWNER, and the DELETE that
    followed ran as the owner and SUCCEEDED.

    The direction that failure takes is what makes it worth this much prose: it does not silently
    pass, it raises the message below - so the monthly restore test would have failed every time,
    accusing the BACKUP'S GRANTS, while the actual cause was a session-scoping rule one layer away.
    A false failure pointing at the wrong layer is the expensive kind.

    So the effect is asserted rather than the invocation, which is the same discipline
    `assert_statistics_exist` applies to ANALYZE a few functions up (CLAUDE.md § 13): a check that
    would report correct about the exact thing it is failing to do is theme 2.
    """
    try:
        conn.execute(f"SET ROLE {role}")

        observed = conn.execute("SELECT current_user").fetchone()[0]
        if observed != role:
            raise RestoreTestError(
                f"SET ROLE {role} did not take: current_user is {observed!r}. The DELETE below "
                f"would run as {observed!r} and prove nothing about {role!r} - and because a "
                f"successful DELETE is reported as the read-only property being ABSENT FROM THE "
                f"BACKUP, this would surface as a false accusation against the archive. "
                f"(`SET LOCAL ROLE` on an autocommit connection does exactly this.)"
            )

        try:
            conn.execute(f"DELETE FROM {table} WHERE false")
        except Exception:
            return
    finally:
        conn.execute("RESET ROLE")

    raise RestoreTestError(
        f"the restored {role!r} role was permitted to DELETE from {table}. The read-only property "
        f"is a proven invariant of this system in production (CLAUDE.md § 20); a backup that does "
        f"not carry it cannot be used to rebuild it."
    )


def mark_verified(conn, backup_id: int, counts: dict[str, int], notes: str) -> None:
    """UPDATES EXACTLY THE THREE COLUMNS THE 0026 TRIGGER PERMITS.

    Touching any other column in the same statement raises in the database, which is the point of
    enforcing insert-once structurally rather than by convention.
    """
    conn.execute(
        "UPDATE backups SET restore_verified_at = %s, restore_verified_counts = %s, "
        "restore_notes = %s WHERE backup_id = %s",
        (datetime.now(timezone.utc), json.dumps(counts), notes, backup_id),
    )


# ---------------------------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------------------------


@job(JOB_NAME)
def restore_test_monthly_job(
    url: str | None = None,
    *,
    scratch_dir: Path = SCRATCH_DIR,
    s3=None,
    run=subprocess.run,
) -> None:
    """Returns None, so `rows_written` is NULL. Same reasoning as the backup job."""
    if s3 is None:  # pragma: no cover - exercised on the instance, injected in tests
        import boto3

        s3 = boto3.client("s3")

    parts = backup.connection_parts(url)
    production_database = parts["database"]

    with db.connection(url) as conn:
        record = most_recent_verified(conn)

    scratch_dir.mkdir(parents=True, exist_ok=True)
    backup.assert_staging_writable(scratch_dir)
    # BEFORE CREATING ANYTHING. The archive AND the restored database both land on /mnt/data,
    # beside production's own data files.
    check_free_space(scratch_dir, record["byte_size"])

    archive_path = scratch_dir / Path(record["s3_key"]).name
    download_archive(s3, record["s3_bucket"], record["s3_key"], archive_path)

    # ROLES COME FROM THE ARCHIVE, AFTER IT IS DOWNLOADED - never from the live source database.
    # The source has moved on since the dump: a role dropped from it would never be created and one
    # added to it would be created needlessly, and either way the throwaway stops matching the
    # artifact under test.
    #
    # PHASE 12 MADE THIS A NO-OP IN PRODUCTION AND IT IS KEPT ANYWAY. Roles are CLUSTER-wide, so
    # every role the archive references already exists in a database on this server. The code and
    # its tests stay because the idempotent guard makes the no-op correct and because the archive's
    # role set is still worth reading - but its production path is no longer exercised end to end,
    # which is one of the two coverage losses this change accepts (see the module docstring).
    roles = roles_in_archive(archive_path, run=run)

    # The pgpass file for pg_restore, written beside the archive at 0600, removed in the finally.
    # Same shape as the dump's, and never PGPASSWORD (CLAUDE.md § 3).
    #
    # `database="*"`, AND THE WILDCARD IS THE FIX RATHER THAN THE SLOPPY VERSION OF ONE.
    #
    # This read `database=production_database` and that is what broke the job's first ever run.
    # libpq matches a pgpass line on ALL FIVE FIELDS, and pg_restore connects to the THROWAWAY -
    # `dws_restore_test_<suffix>` - so the entry matched nothing. libpq does not error on a
    # non-matching file; it falls through to PROMPTING, so the observed failure was a bare
    # `Password: ` on stdout and then `FATAL: password authentication failed for user
    # "waterway"`. That reads as a wrong password and it was a wrong DATABASE FIELD.
    #
    # NOT the throwaway's name, which is the other obvious repair: the throwaway does not exist
    # yet. It is created below, inside the `try`, and moving this write in after it would change
    # the shape of the `finally` that unlinks the file - opening a window where a failure between
    # the two leaves a 0600 credential behind. The wildcard needs no reordering.
    #
    # The widening is not meaningful. The file is 0600 and still pinned to one host, one port and
    # one user, so it says "this password, for this user, on this server" - which is exactly true,
    # and is what this job needs, because it authenticates to the PRODUCTION database (to CREATE
    # and DROP) and to the THROWAWAY (to restore) with the same credential.
    #
    # THE BACKUP JOB'S OWN ENTRY STAYS NARROW at `parts["database"]` and must not be "unified"
    # with this one: the dump connects only to production, so its specific entry is correct and
    # strictly narrower. Narrower is right wherever it is achievable; the wildcard is a concession
    # this path needs and that one does not. A test asserts each caller separately, in opposite
    # directions, so unifying them goes red.
    pgpass_path = scratch_dir / ".pgpass-restore-test"
    backup.write_pgpass(
        pgpass_path,
        host=parts["host"], port=parts["port"], database="*",
        user=parts["user"], password=parts["password"],
    )

    throwaway = None
    succeeded = False
    try:
        # CREATE DATABASE CANNOT RUN INSIDE A TRANSACTION, hence autocommit. This connection is to
        # the PRODUCTION database and it is the one the DROP is issued on too - which is why the
        # name guard's second condition compares against the database this connection is on.
        with db.connection(url, autocommit=True) as admin:
            throwaway = create_throwaway(admin, url or db.database_url(), production_database)

        restore(
            throwaway, archive_path, parts=parts, pgpass_path=pgpass_path, run=run, roles=roles
        )

        with db.connection(throwaway.url, autocommit=True) as restored:
            analyze(restored)
            largest = assert_statistics_exist(restored)

            counts = restored_counts(restored)
            compare_counts(record["row_counts"], counts)
            chunks = compare_compressed_chunks(record["compressed_chunks"], restored)
            assert_read_only_role_cannot_delete(
                restored, f'"{backup.COUNTED_SCHEMA}"."{largest}"'
            )

        # ONLY AFTER EVERY ASSERTION. Marking before them would leave a verification mark on a
        # backup whose restore then failed, which is worse than no mark at all.
        with session.writing(url) as conn:
            mark_verified(
                conn,
                record["backup_id"],
                counts,
                f"{len(counts)} tables compared, {chunks} compressed chunks, "
                f"restored from s3://{record['s3_bucket']}/{record['s3_key']}",
            )

        succeeded = True
        logger.info(
            "restore test passed for backup %d: %d tables compared, %d compressed chunks "
            "on both sides",
            record["backup_id"], len(counts), chunks,
        )
    finally:
        pgpass_path.unlink(missing_ok=True)

        # RUNS ON EVERY EXIT PATH, INCLUDING KeyboardInterrupt - but ONLY ON SUCCESS.
        #
        # ON FAILURE THE THROWAWAY IS KEPT AND NAMED. Evidence at the moment it becomes useful is
        # worth more than a clean server: a restore that failed halfway is the one thing that can
        # say WHY, and dropping it destroys the only copy of that state. The cost is a database
        # holding disk on the same volume as production, so the error says exactly what to run.
        #
        # This inverts the container version, which always tore down and captured logs first. A
        # container's logs are its whole state; a database's state IS the database.
        if throwaway is not None and succeeded:
            with db.connection(url, autocommit=True) as admin:
                drop_throwaway(admin, throwaway)
            archive_path.unlink(missing_ok=True)
        elif throwaway is not None:
            logger.error(
                "restore test FAILED. EVIDENCE KEPT, NOTHING DROPPED:\n"
                "  throwaway database: %s\n"
                "  archive:            %s\n"
                "Inspect it, then remove it by hand when you are done:\n"
                "  SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '%s';\n"
                "  DROP DATABASE %s;",
                throwaway.name, archive_path, throwaway.name, throwaway.name,
            )

    return None
