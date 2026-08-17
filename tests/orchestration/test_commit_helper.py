"""`session.writing` — its behaviour, and the structural guard that it is what write paths use.

ON THE LEGITIMACY OF A SOURCE-TEXT TEST, BECAUSE THIS FILE CONTAINS ONE AND PHASE 11 PROVED THREE
OTHERS WORTHLESS.

Three of Phase 11's own tests were rewritten after a mutation escaped them. They grepped their
module's docstrings for a phrase and asserted it was present — source text standing in as a PROXY
for a behaviour, which is illegitimate for a reason that has nothing to do with grep: the behaviour
could change freely while the sentence describing it stayed put, so the test was pinned to the
comment rather than to the code. A docstring is evidence about intent, never about what runs.

`test_write_paths_use_the_commit_helper` below is a source-text test and is NOT that. The
difference is the subject. Here the forbidden call site IS the invariant — the property being
asserted is "no write path opens a raw connection", which is a statement about what the source says
and about nothing else. There is no behaviour it stands in for, because a bare `db.connection` that
is never executed is exactly as much of a violation as one that is: it is a path waiting to lose a
commit. When the source text is the subject, reading the source is direct evidence; when it is a
proxy for behaviour, reading it is a comment wearing a test's clothes.

The operational test of which kind you have: ask what a mutation would do. Mutating the BEHAVIOUR
under an illegitimate source test leaves it green — that is the failure. Mutating the SOURCE under
this one turns it red, because the source is the thing it constrains.

AN AST WALK RATHER THAN A REGEX, and that is not a stylistic preference. This module's own
docstrings contain the string `db.connection` several times, including in the sentences explaining
why write paths must not call it. A regex-based guard would match its own explanation and fail
permanently, and the fix somebody reaches for is to weaken the pattern. `ast` sees code, so a
mention in a comment or a docstring is invisible to it — which mutation 3 confirms deliberately, by
adding a bare call inside a comment and requiring this test to STAY GREEN. A guard that is merely
strict is not the same as a guard that is precise, and only the precise one survives contact with a
codebase that documents itself.
"""

import ast
import pathlib

import psycopg
import pytest

from app import db
from app.orchestration import session

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# Every call site permitted to open a connection WITHOUT the committing helper, as
# {(module path relative to app/, enclosing function): how many}. Compared BY EXACT EQUALITY, the
# same discipline as the security-group ingress set (CLAUDE.md § 8), the ufw port set (§ 11) and
# the published-port set (§ 22): a new bare call fails until somebody writes down which kind it is,
# and a REMOVED one fails too, so this list cannot rot into describing a codebase that has moved on.
#
# The counts matter. `backup_nightly_job` legitimately opens two and `restore_test_monthly_job` two;
# without a count a third could appear inside either and nothing would notice.
ALLOWED = {
    # ---- read-only paths -----------------------------------------------------------------
    # The API is read-only by contract and connects as a role that cannot write (§ 20). Routing it
    # through a helper called `writing` would obscure the single most important property it has.
    ("api/dependencies.py", "get_connection"): 1,
    # Reports two byte counts and a ratio. Writes nothing.
    ("ingest/usgs_ingest.py", "_print_compression_stats"): 1,
    # Reads job_runs and MAX(ts) per registered table. Writes nothing.
    ("orchestration/heartbeat.py", "heartbeat_job"): 1,
    # Reads the last verified backup and asserts the excluded table exists, before any dump.
    ("orchestration/backup.py", "backup_nightly_job"): 2,
    # Reads the most recent verified row and the source's roles; the second is the throwaway.
    ("orchestration/restore_test.py", "restore_test_monthly_job"): 2,
    # A readiness probe against the throwaway container.
    ("orchestration/restore_test.py", "wait_until_ready"): 1,

    # ---- the migration runner, whose transaction boundaries ARE its correctness argument ----
    # One transaction per file with the schema_migrations insert inside it (§ 3, § 12), and a
    # knowingly non-atomic `-- migrate:no-transaction` path. A helper that commits on exit would be
    # wrong here in a way that is close to undetectable afterwards.
    ("orchestration/migrate.py", "run"): 1,
    ("orchestration/migrate.py", "_apply_without_transaction"): 1,
    ("orchestration/migrate.py", "status"): 1,

    # ---- connections whose transaction is itself the point ---------------------------------
    # autocommit against a throwaway container that is about to be destroyed.
    ("orchestration/restore_test.py", "restore"): 2,

    # ---- the helper itself -----------------------------------------------------------------
    ("orchestration/session.py", "writing"): 1,
}


def _enclosing_function(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best.name if best else "<module>"


def _raw_connection_sites():
    """Every `db.connection(...)` and `db.connect(...)` call under app/, by (module, function).

    `db.connect` is included because it is the obvious way around the guard: it is the lower-level
    function `db.connection` itself wraps, and a write path calling it directly would be invisible
    to a check that only knew about the context manager.
    """
    files = sorted(APP.rglob("*.py"))

    # § 21: A STATIC ASSERTION MUST PROVE IT RESOLVED THE SOURCE TREE FIRST. A scanner pointed at a
    # directory that does not exist finds no violations and reports success forever.
    assert len(files) >= 30, (
        f"the scan found {len(files)} python files under {APP}; the application has far more than "
        f"that, so this walk is not seeing the source tree and would pass over anything"
    )

    found = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in ("connection", "connect"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "db"):
                continue
            if path.name == "db.py":
                continue  # where they are defined
            key = (str(path.relative_to(APP)), _enclosing_function(tree, node.lineno))
            found[key] = found.get(key, 0) + 1
    return found


def test_write_paths_use_the_commit_helper():
    """No call site opens a raw connection except the ones written down above.

    THE INVARIANT IS THE CALL SITE, which is why reading the source is direct evidence here rather
    than a proxy for behaviour — see this module's docstring for the distinction, and for why that
    makes this test legitimate where three of Phase 11's docstring-grepping tests were not.
    """
    found = _raw_connection_sites()

    # The walk must have found SOMETHING, or an `ast` change or a renamed module would silently
    # turn this into a test that constrains the empty set — § 22's "a gate that passes over an
    # empty collection is green forever and watching nothing".
    assert found, (
        "the walk found no db.connection/db.connect call sites at all under app/. The allow-list "
        "below is non-empty, so this means the scanner stopped working, not that the codebase "
        "became clean."
    )

    unexpected = {k: v for k, v in found.items() if k not in ALLOWED}
    assert not unexpected, (
        "these call sites open a connection without `session.writing`, and are not on the "
        "allow-list:\n"
        + "\n".join(f"  app/{mod}::{fn}  ({n} call site(s))" for (mod, fn), n in sorted(unexpected.items()))
        + "\n\nIf the path WRITES, use `session.writing(url)`: it commits on a clean exit and rolls "
          "back on an exception, so the boundary is structural rather than a line each call site "
          "has to remember. Eight of ten write paths could lose their commit with the suite green "
          "before that helper existed.\n"
        "If it does not write, add it to ALLOWED with the reason, which is a visible act."
    )

    miscounted = {k: (found[k], ALLOWED[k]) for k in found if k in ALLOWED and found[k] != ALLOWED[k]}
    assert not miscounted, (
        "these call sites are allowed but the NUMBER of them changed:\n"
        + "\n".join(
            f"  app/{mod}::{fn}  found {got}, allow-list says {want}"
            for (mod, fn), (got, want) in sorted(miscounted.items())
        )
        + "\n\nA new raw connection inside an already-allowed function is exactly what a "
          "function-name allow-list without counts would miss."
    )

    vanished = {k: v for k, v in ALLOWED.items() if k not in found}
    assert not vanished, (
        "these are on the allow-list and no longer exist:\n"
        + "\n".join(f"  app/{mod}::{fn}" for (mod, fn) in sorted(vanished))
        + "\n\nRemove them. An allow-list that outlives what it permits stops describing this "
          "codebase, and the next reader trusts it anyway."
    )


# ---------------------------------------------------------------------------------------------
# The helper's behaviour. Integration tier: the point is what a SECOND connection can see.
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def scratch_table(migrated_db, database_url):
    """A table outside the migrations, so these tests cannot disturb anything that matters."""
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS commit_helper_probe")
        conn.execute("CREATE TABLE commit_helper_probe (note text)")
    yield "commit_helper_probe"
    with db.connection(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS commit_helper_probe")


def _notes(database_url, table):
    """Read on a connection opened after the writer closed."""
    with db.connection(database_url) as conn:
        return [r[0] for r in conn.execute(f"SELECT note FROM {table} ORDER BY note").fetchall()]


@pytest.mark.integration
def test_commit_helper_commits_on_success(database_url, scratch_table):
    """A clean exit leaves the row visible to a connection that never saw the transaction."""
    with session.writing(database_url) as conn:
        conn.execute(f"INSERT INTO {scratch_table} (note) VALUES ('committed')")

    assert _notes(database_url, scratch_table) == ["committed"], (
        "session.writing exited cleanly and a new connection sees no row. The helper is not "
        "committing, which is the entire reason it exists."
    )


@pytest.mark.integration
def test_commit_helper_rolls_back_on_exception(database_url, scratch_table):
    """An exception discards the work — asserted by the ABSENCE of the write, not by the raise.

    Asserting only that the exception propagated would pass on a helper that committed first and
    then re-raised, which is the failure worth catching: it is the one that leaves half a
    transaction behind under a stack trace that says the operation failed.
    """
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with session.writing(database_url) as conn:
            conn.execute(f"INSERT INTO {scratch_table} (note) VALUES ('rolled-back')")
            raise Boom("the work failed after writing")

    assert _notes(database_url, scratch_table) == [], (
        "a row written before the exception survived. session.writing committed work that the "
        "caller's failure should have discarded."
    )


@pytest.mark.integration
def test_commit_helper_reraises(database_url, scratch_table):
    """It never swallows. The @job contract depends on a failing job failing LOUDLY.

    A context manager returning True from `__exit__` suppresses the exception, which here would
    mean a job that raised being recorded as a success by the decorator above it and reported as
    healthy by the scheduler — CLAUDE.md § 4's decorator "re-raises; it never swallows" defeated one
    layer down, for every path routed through this helper at once.
    """
    class Boom(RuntimeError):
        pass

    raised = None
    try:
        with session.writing(database_url) as conn:
            conn.execute(f"INSERT INTO {scratch_table} (note) VALUES ('x')")
            raise Boom("must reach the caller")
    except Boom as exc:
        raised = exc

    assert raised is not None, (
        "session.writing swallowed the exception. Every job routed through it would then report "
        "success while its work had been rolled back — the Phase 11 defect with a stack trace "
        "deleted as well as the row."
    )
    assert str(raised) == "must reach the caller", "the original exception was replaced"


@pytest.mark.integration
def test_commit_helper_rolls_back_on_keyboard_interrupt(database_url, scratch_table):
    """BaseException, not Exception. A Ctrl-C mid-write must not leave work to be committed later.

    Separate from the Exception test because `except Exception` passes that one and fails this one,
    and the backfills this helper now wraps are precisely the long-running things a human
    interrupts.
    """
    with pytest.raises(KeyboardInterrupt):
        with session.writing(database_url) as conn:
            conn.execute(f"INSERT INTO {scratch_table} (note) VALUES ('interrupted')")
            raise KeyboardInterrupt

    assert _notes(database_url, scratch_table) == [], (
        "a row written before a KeyboardInterrupt survived; the helper is catching Exception "
        "rather than BaseException"
    )
