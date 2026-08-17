"""The database connection every instance verifier uses, as the read-only role, with no fallback.

THE VERIFIER CONNECTS AS `waterway_api`, NOT AS THE OWNER, AND THAT IS WHAT MAKES IT READ-ONLY.

Everything else in this package is read-only by an allow-list, which is a property of the code.
Here the guarantee is stronger and cheaper: the database refuses. A mutation in a verifier fails at
Postgres rather than at review, and a future edit that adds an UPDATE "just to fix this one row"
raises `InsufficientPrivilege` instead of succeeding quietly on production data.

`app/api/dependencies.py` FALLS BACK TO `DATABASE_URL` WITH A WARNING, AND THIS DELIBERATELY DOES
NOT. The API's fallback is correct for the API: refusing to start because a human has not yet run
the GRANTs would be worse than starting as the owner and saying so. A verifier has the opposite
trade - it exists to establish a fact, and a verifier that quietly connected as the owner would be
reporting "read-only" while holding write privileges. So an absent `API_DATABASE_URL` is a
PRECONDITION (exit 2), and a missing SELECT names the table and the GRANT and stops.

CLAUDE.md § 20: "a read-only role that has never been observed refusing a write is not known to be
read-only." That observation is the human's step, and it stays the human's: this module never
issues the DELETE that must fail, because issuing it would be a write.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Sequence

from verify.phase11.result import Precondition

API_DATABASE_URL_VAR = "API_DATABASE_URL"

# Every table the instance stages read. Probed for SELECT before any stage runs, so a missing grant
# is one clear message at the start rather than an exception halfway through Stage G.
#
# `backups` IS THE ONE MOST LIKELY TO BE MISSING, and the reason is worth writing down: the role was
# granted `SELECT ON ALL TABLES IN SCHEMA public` in Phase 8, which covers the tables that existed
# THEN. `backups` arrives with migration 0026. The `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON
# TABLES` issued alongside it should cover new tables - but only those created by the same role
# that issued it, and only if it was issued. That is exactly the kind of thing that is true until
# it is not, so it is probed rather than assumed.
REQUIRED_TABLES: tuple[str, ...] = (
    "job_runs",
    "backups",
    "schema_migrations",
)


def api_database_url(env: dict | None = None) -> str:
    environ = os.environ if env is None else env
    url = (environ.get(API_DATABASE_URL_VAR) or "").strip()
    if not url:
        raise Precondition(
            f"{API_DATABASE_URL_VAR} is not set. The instance verifiers connect as the read-only "
            f"role so that read-onlyness is enforced by Postgres rather than by review, and there "
            f"is deliberately NO fallback to DATABASE_URL - a verifier that quietly connected as "
            f"the owner would be reporting a guarantee it was not holding. "
            f"Load the environment first: `set -a; . ./.env; set +a`."
        )
    return url


@contextmanager
def connection(url: str | None = None):
    """A psycopg connection as the read-only role. Never committed; nothing here writes."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - psycopg is in requirements.txt
        raise Precondition(f"psycopg is not importable: {exc}") from exc

    resolved = url if url is not None else api_database_url()
    try:
        conn = psycopg.connect(resolved)
    except Exception as exc:
        # The URL carries the password (§ 20), so the exception text never reaches the message.
        raise Precondition(
            f"could not connect as the read-only role: {type(exc).__name__}. The connection string "
            f"is not reported, because it contains the password."
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def query(conn, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def assert_select_granted(conn, tables: Sequence[str] = REQUIRED_TABLES) -> dict[str, bool]:
    """Probe SELECT on every table these stages read. Stop and report rather than falling back.

    `has_table_privilege` is used rather than an actual `SELECT 1 FROM t`, because a table that
    exists and is empty and a table the role cannot read are different facts and only one of them
    is about privileges. A table that does not exist yet is reported as such, separately - Stage F
    is the stage that establishes `backups` exists at all.
    """
    missing: list[str] = []
    absent: list[str] = []
    granted: dict[str, bool] = {}

    for table in tables:
        rows = query(
            conn,
            "SELECT to_regclass(%s) IS NOT NULL, "
            "       coalesce(has_table_privilege(current_user, %s, 'SELECT'), false)",
            [f"public.{table}", f"public.{table}"],
        )
        exists, can_select = rows[0]
        granted[table] = bool(exists and can_select)
        if not exists:
            absent.append(table)
        elif not can_select:
            missing.append(table)

    if missing:
        grants = "\n".join(
            f"    GRANT SELECT ON TABLE public.{table} TO waterway_api;" for table in missing
        )
        raise Precondition(
            f"the read-only role lacks SELECT on: {missing}. "
            f"THIS VERIFIER DOES NOT FALL BACK TO THE OWNER CONNECTION - the fallback is the "
            f"tempting move and it silently discards the guarantee the whole approach is for. "
            f"`backups` is the likely one: the role was granted SELECT on all tables in Phase 8, "
            f"before migration 0026 created it. Granting it is a small migration and is a human's "
            f"decision:\n{grants}"
        )
    if absent:
        raise Precondition(
            f"these tables do not exist: {absent}. Stage F is what establishes that migration 0026 "
            f"has been applied; run it first, or run `python3 -m migrations.run`."
        )
    return granted
