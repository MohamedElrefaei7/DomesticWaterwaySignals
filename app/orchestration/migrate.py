"""The migration runner. A CLI a human invokes — never an entrypoint, never a healthcheck.

CLAUDE.md § 3 in executable form. Five things here are decisions rather than implementation
details, and each one has the obvious-and-wrong alternative written next to it, because in every
case the wrong version is shorter:

  1. schema_migrations is bootstrapped by this runner, outside the numbered sequence. The table
     that records applied migrations cannot itself be an applied migration — there would be
     nowhere to record it. This bootstrap is the ONLY DDL this runner issues that does not come
     from a numbered file.

  2. Checksums of every ALREADY-APPLIED migration are verified before a single pending migration
     is applied. Not the pending ones — those have nothing to compare against yet.

  3. One transaction per file, with the schema_migrations INSERT inside that same transaction.

  4. `-- migrate:no-transaction` is honoured only as the literal first line.

  5. A pending migration numbered below the highest applied version is a hard failure.

Nothing in here runs automatically. `docker-compose.yml` deliberately has no `command:`, and
tests/orchestration/test_migration_ordering.py asserts it stays that way: with
`restart: unless-stopped`, a container that migrates on start turns a crash loop into a
schema-change loop.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Import as a package when installed/run normally, but stay runnable as `python
# app/orchestration/migrate.py` from a checkout, which is how a human on the instance will invoke
# it before anything is packaged.
if __package__ in (None, ""):  # pragma: no cover - exercised by the CLI, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import db
else:
    from app import db

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

FILENAME_RE = re.compile(r"^(\d+)_.+\.sql$")

NO_TRANSACTION_MARKER = "-- migrate:no-transaction"

# Bootstrap DDL. Decision 1: this is the one statement not backed by a numbered file. It is
# idempotent so that running the migrator against an already-migrated database is a no-op rather
# than an error.
BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    int PRIMARY KEY,
    filename   text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Any refusal to proceed. Always names the file(s) involved; never a bare 'migration failed'."""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: Path
    sql: str
    checksum: str

    @property
    def no_transaction(self) -> bool:
        """True only when the marker is the LITERAL FIRST LINE of the file.

        Decision 4. Matching the marker anywhere in the file would mean a comment inside a DDL
        body — or a paragraph of prose explaining why some other migration needed it — silently
        changes how this file is applied, and the change is invisible at the point of use. The
        first line is a place a human looks when they open the file.
        """
        first_line = self.sql.split("\n", 1)[0].strip()
        return first_line == NO_TRANSACTION_MARKER


def checksum_of(text: str) -> str:
    """SHA-256 of the file's exact bytes.

    Not normalized, not whitespace-insensitive: the question this answers is "is this the file
    that was applied?", and a trailing newline someone's editor added is a change to the file.
    A false alarm here costs a `git diff`; a missed alarm costs a database that disagrees with
    the repo about what it contains, with nothing detecting it.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_version(filename: str) -> int | None:
    match = FILENAME_RE.match(filename)
    return int(match.group(1)) if match else None


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every NNNN_name.sql in the directory, sorted NUMERICALLY by version.

    Numerically, not lexically. Sorted as strings, '0010' sorts before '0002' the moment the
    project passes nine migrations, and the runner would then apply them in an order nobody
    chose — silently, because every file still gets applied exactly once and the run reports
    success.
    """
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory does not exist: {migrations_dir}")

    migrations: dict[int, Migration] = {}
    for path in sorted(migrations_dir.iterdir()):
        if not path.is_file() or path.suffix != ".sql":
            continue
        version = parse_version(path.name)
        if version is None:
            raise MigrationError(
                f"{path.name} does not match the NNNN_description.sql naming convention. "
                f"Rename it or move it out of {migrations_dir}."
            )
        if version in migrations:
            raise MigrationError(
                f"duplicate migration version {version}: {migrations[version].filename} and "
                f"{path.name}. Two files claiming one version means one of them will be recorded "
                f"as applied when it was not."
            )
        sql = path.read_text(encoding="utf-8")
        migrations[version] = Migration(
            version=version,
            filename=path.name,
            path=path,
            sql=sql,
            checksum=checksum_of(sql),
        )

    return [migrations[v] for v in sorted(migrations)]


def bootstrap(conn) -> None:
    """Create schema_migrations if absent. Decision 1."""
    conn.execute(BOOTSTRAP_SQL)
    conn.commit()


def applied_migrations(conn) -> dict[int, tuple[str, str]]:
    """version -> (filename, checksum) for everything already recorded."""
    rows = conn.execute(
        "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def verify_applied_checksums(
    migrations: list[Migration], applied: dict[int, tuple[str, str]]
) -> None:
    """Recompute the checksum of every already-applied file and compare. Decision 2.

    Called BEFORE anything pending is applied, and it aborts the entire run rather than skipping
    the offending file — because by the time this fails, the database and the repo already
    disagree about what the schema is, and applying more changes on top of a disagreement you have
    just detected is how you get a schema no environment can reproduce.

    The tempting cheap version — track filenames only, or checksum the files about to run —
    catches nothing. Filename-only tracking permits editing an applied migration in place; a
    database later restored from a dump then diverges from what the repo claims it is, and no
    layer notices (CLAUDE.md § 3).
    """
    by_version = {m.version: m for m in migrations}
    problems: list[str] = []

    for version in sorted(applied):
        recorded_filename, recorded_checksum = applied[version]
        migration = by_version.get(version)

        if migration is None:
            problems.append(
                f"  version {version} ({recorded_filename}) is recorded as applied but no file "
                f"for it exists on disk. Restore it from git; do not delete the row."
            )
            continue

        if migration.checksum != recorded_checksum:
            problems.append(
                f"  {migration.filename} (version {version}) has changed since it was applied:\n"
                f"      recorded on disk when applied: {recorded_checksum}\n"
                f"      computed from the file now:    {migration.checksum}"
            )

    if problems:
        raise MigrationError(
            "refusing to apply anything: already-applied migration files have changed.\n"
            + "\n".join(problems)
            + "\n\nThe database and this repo now disagree about what the schema is. Restore the "
            "file(s) to the exact content that was applied (`git checkout -- migrations/`), or, "
            "if the change was genuinely intended, write it as a NEW numbered migration. Never "
            "edit an applied one."
        )


def check_ordering(migrations: list[Migration], applied: dict[int, tuple[str, str]]) -> None:
    """Refuse to apply a pending migration numbered below the highest applied version. Decision 5.

    This is what a merged branch produces: 0007 is applied here, someone's branch adds 0005, and
    the merge presents a pending migration that everyone else's database applied three weeks ago
    in a different position.

    Neither available shortcut is acceptable. Applying it out of order produces a schema no other
    environment can reproduce — the schema is the ordered application of the files, not the set of
    them. Skipping it produces a database that silently lacks a change the repo believes shipped,
    which is CLAUDE.md § 2's theme 1 again. So: stop, name both versions, and let a human renumber.
    """
    if not applied:
        return

    highest_applied = max(applied)
    out_of_order = [m for m in migrations if m.version not in applied and m.version < highest_applied]
    if not out_of_order:
        return

    highest_filename = applied[highest_applied][0]
    listed = "\n".join(f"  version {m.version} ({m.filename}) is pending" for m in out_of_order)
    raise MigrationError(
        f"out-of-order migration(s) detected. Version {highest_applied} ({highest_filename}) is "
        f"already applied, but:\n{listed}\n\n"
        f"Applying these out of order would produce a schema no other environment can reproduce, "
        f"and skipping them would produce one that silently lacks a change this repo believes "
        f"shipped. Renumber the pending file(s) above version {highest_applied} and re-run."
    )


def _record_sql() -> str:
    return (
        "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)"
    )


def _apply_in_transaction(conn, migration: Migration) -> None:
    """BEGIN -> apply the file -> INSERT the schema_migrations row -> COMMIT. Decision 3.

    The INSERT is INSIDE the transaction that applies the change. Both wrong alternatives are
    tidier-looking:

      A. All pending migrations in one transaction. A failure in the fourth rolls back the first
         three, which had already succeeded, and the next run reapplies them against a database
         where they may well not be idempotent.

      B. Apply the file, commit, then record it separately. The window between the two is small
         and entirely real: a crash inside it leaves a migration applied and unrecorded, and the
         next run applies it a second time.

    psycopg opens a transaction implicitly on the first statement of a non-autocommit connection,
    so the explicit boundary here is the commit/rollback pair.
    """
    try:
        conn.execute(migration.sql)
        conn.execute(_record_sql(), (migration.version, migration.filename, migration.checksum))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _apply_without_transaction(conn, migration: Migration, url: str | None) -> None:
    """Autocommit for the statement, then record it in its own transaction. Decision 4.

    Knowingly not atomic — see the comment block in 0003_job_runs_success_index.sql. A crash
    between the two leaves the migration applied and unrecorded. That is the price of
    CREATE INDEX CONCURRENTLY, which cannot run inside a transaction block at all, and it is why
    this is opt-in per file rather than the default.

    A separate connection is used because psycopg will not flip autocommit on a connection with an
    open transaction, and the caller's connection has been running queries.
    """
    with db.connection(url, autocommit=True) as autocommit_conn:
        autocommit_conn.execute(migration.sql)

    conn.execute(_record_sql(), (migration.version, migration.filename, migration.checksum))
    conn.commit()


def run(migrations_dir: Path = MIGRATIONS_DIR, url: str | None = None) -> list[Migration]:
    """Apply every pending migration in version order. Returns what it applied.

    Order of operations is itself a decision: bootstrap, read what is applied, verify those
    checksums, check ordering, and only then apply anything. Every check that can refuse the run
    runs before the first change is made.
    """
    migrations = discover(migrations_dir)

    with db.connection(url) as conn:
        bootstrap(conn)
        applied = applied_migrations(conn)

        verify_applied_checksums(migrations, applied)
        check_ordering(migrations, applied)

        pending = [m for m in migrations if m.version not in applied]
        if not pending:
            logger.info("no pending migrations; %d already applied", len(applied))
            return []

        applied_now: list[Migration] = []
        for migration in pending:
            if migration.no_transaction:
                logger.info(
                    "applying %s (no-transaction: not atomic, see the file's header)",
                    migration.filename,
                )
                _apply_without_transaction(conn, migration, url)
            else:
                logger.info("applying %s", migration.filename)
                _apply_in_transaction(conn, migration)
            applied_now.append(migration)

        return applied_now


def status(migrations_dir: Path = MIGRATIONS_DIR, url: str | None = None) -> list[tuple]:
    """(version, filename, applied?) for every file. Read-only; changes nothing."""
    migrations = discover(migrations_dir)
    with db.connection(url) as conn:
        bootstrap(conn)
        applied = applied_migrations(conn)
    return [(m.version, m.filename, m.version in applied) for m in migrations]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply pending schema migrations. Run by a human, deliberately - never on container "
            "start (CLAUDE.md section 3)."
        )
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help=f"default: {MIGRATIONS_DIR}",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="show what is applied and what is pending, and change nothing",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.status:
            for version, filename, is_applied in status(args.migrations_dir):
                print(f"{'applied' if is_applied else 'PENDING':>8}  {version:04d}  {filename}")
            return 0

        applied_now = run(args.migrations_dir)
    except MigrationError as exc:
        print(f"\nmigration aborted:\n{exc}\n", file=sys.stderr)
        return 1

    if not applied_now:
        print("nothing to apply; database is up to date")
    else:
        for migration in applied_now:
            print(f"applied {migration.version:04d}  {migration.filename}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
