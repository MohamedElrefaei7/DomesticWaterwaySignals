"""Stage F — migration 0026 applied, `backups` present, and both triggers installed.

    python3 -m verify.phase11 f

THE TRIGGERS' EXISTENCE IS ASSERTED; THEIR BEHAVIOUR IS NOT, AND THAT SPLIT IS DELIBERATE.

Proving `backups_forbid_update` works means attempting an UPDATE and watching it be refused. That
is a WRITE, and this verifier connects as the read-only role precisely so it cannot make one. The
behavioural proof belongs to the human's F3 step, where the refusal is observed against a row a
human inserted - and CONTEXT.md already records what that refusal looks like, which is the part
that is easy to misread: the trigger's `RAISE EXCEPTION` surfaces as `psycopg.errors.RaiseException`
rather than as a constraint violation, and the message names the column
(`refusing to update column byte_size on backup_id=N`).

So this checks `pg_trigger`, which is a real and different fact: a trigger that was never created,
or one dropped by a hand-edited migration, is invisible to any amount of reading the `.sql` file -
the file says what SHOULD have been applied, and `pg_trigger` says what IS.

`tgenabled` IS READ, NOT JUST `tgname`. § 3 permits a human to disable the delete trigger for a
genuine correction - "which is a visible act" - and the visible part only works if something looks.
A trigger that exists and is disabled is exactly as protective as one that does not exist, and it
reads as present in every query that only counts rows.
"""

from __future__ import annotations

from typing import Any, Sequence

from verify.phase11 import readonly
from verify.phase11.result import Check, CheckResult, failed, passed

EXPECTED_MIGRATIONS = 27

# migrations/0026_backups_table.sql
EXPECTED_TRIGGERS = ("backups_forbid_delete", "backups_forbid_update")

# `pg_trigger.tgenabled`: 'O' means enabled in the default replication role. 'D' is disabled.
ENABLED = "O"


def check_migration_count(applied: int) -> CheckResult:
    name = "schema_migrations is at 26"
    expected = f"{EXPECTED_MIGRATIONS} applied migrations"
    if applied != EXPECTED_MIGRATIONS:
        return failed(
            name,
            expected,
            f"{applied} applied. Below this means 0026 has not run "
            f"(`python3 -m migrations.run`); above it means something landed that this verifier "
            f"does not know about.",
        )
    return passed(name, expected, f"{applied} applied")


def check_backups_table_exists(regclass: str | None) -> CheckResult:
    name = "public.backups exists"
    expected = "to_regclass('public.backups') is not null"
    if not regclass:
        return failed(
            name,
            expected,
            "null. Migration 0026 creates it; a migration count of 26 with no table means "
            "schema_migrations and the schema disagree, which is worse than either alone.",
        )
    return passed(name, expected, regclass)


def check_triggers_exist_and_are_enabled(rows: Sequence[tuple[str, str]]) -> CheckResult:
    """Both triggers present AND enabled, by exact set equality.

    Containment would pass while one of the two is missing, and § 3 needs both: the BEFORE UPDATE
    trigger is what makes `backups` insert-once except for three columns, and the BEFORE DELETE
    trigger is what makes removing a row a visible act.
    """
    observed = {name: state for name, state in rows}
    name = "both backups triggers exist and are enabled"
    expected = f"exactly {list(EXPECTED_TRIGGERS)}, each tgenabled='{ENABLED}'"

    missing = sorted(set(EXPECTED_TRIGGERS) - set(observed))
    unexpected = sorted(set(observed) - set(EXPECTED_TRIGGERS))
    disabled = sorted(
        f"{trigger} (tgenabled={observed[trigger]!r})"
        for trigger in observed
        if observed[trigger] != ENABLED
    )

    if missing or unexpected or disabled:
        parts = []
        if missing:
            parts.append(f"missing: {missing}")
        if unexpected:
            parts.append(f"unexpected: {unexpected}")
        if disabled:
            parts.append(
                f"present but DISABLED: {disabled}. A disabled trigger is exactly as protective as "
                f"an absent one and reads as present in any query that only counts rows."
            )
        return failed(name, expected, "; ".join(parts))
    return passed(name, expected, f"{sorted(observed)}, all enabled")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def read(conn) -> dict[str, Any]:
    """Every read this stage makes. No statement here writes; the role could not anyway."""
    readonly.assert_select_granted(conn)

    applied = readonly.query(conn, "SELECT count(*) FROM schema_migrations")[0][0]
    regclass = readonly.query(conn, "SELECT to_regclass('public.backups')::text")[0][0]
    triggers = readonly.query(
        conn,
        """
        SELECT t.tgname, t.tgenabled::text
        FROM pg_trigger AS t
        JOIN pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'backups' AND NOT t.tgisinternal
        ORDER BY t.tgname
        """,
    )
    return {"applied": applied, "regclass": regclass, "triggers": triggers}


def checks() -> Sequence[Check]:
    with readonly.connection() as conn:
        state = read(conn)

    return [
        lambda: check_migration_count(state["applied"]),
        lambda: check_backups_table_exists(state["regclass"]),
        lambda: check_triggers_exist_and_are_enabled(state["triggers"]),
    ]
