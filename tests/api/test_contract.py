"""The honesty properties, asserted at the serialization boundary.

EVERY GUARD THIS PROJECT BUILT IN SEVEN PHASES LIVES IN PYTHON OR IN THE DATABASE. This file is
where they have to survive being turned into JSON, and each test below corresponds to a way they
quietly stop holding on the far side of an encoder.

If the API can be made to emit a number the engine refused to produce, this commit has failed
regardless of what else passes.
"""

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from app.analogs import gate as gate_module, parameters
from app.api import models
from app.api.main import declared_methods, declared_routes
from tests.api.conftest import ExplodingConn, MEMPHIS, make_client, numeric_leaves
from tests.api.test_conclusion import (
    RUN_ROW,
    FakeConn,
    get,
    passing_result,
    quiet_result,
    refused_result,
    stub_engine,
)

API_ROOT = Path(__file__).resolve().parents[2] / "app" / "api"

# Every endpoint this API is documented to have. Listed so the route walk cannot pass over an empty
# or truncated set - CLAUDE.md § 2's theme 2, and the exact shape of the ingress test that passed
# vacuously because the set it constrained was empty.
DOCUMENTED_ENDPOINTS = {
    "/api/health",
    "/api/conclusion",
    "/api/gauges",
    "/api/gauges/{site_id}/series",
    "/api/rates",
    "/api/movements",
    "/api/signals",
    "/api/signals/runs",
}


# ---------------------------------------------------------------------------------------------
# 1. Read-only.
# ---------------------------------------------------------------------------------------------


def test_no_non_get_route_is_declared():
    """Test 1, decision 1. No POST, no PUT, no PATCH, no DELETE, anywhere in the app.

    An HTTP-triggerable sweep is a sweep that runs unattended and accumulates runs nobody reviewed;
    an HTTP-triggerable backfill runs for hours and sits `running` in a way the heartbeat cannot
    distinguish from healthy. Neither is a phase-ordering convenience.

    THE WALK IS ASSERTED BEFORE THE METHODS ARE. On Starlette 1.6 a flat pass over `app.routes`
    returns only `/docs` and `/openapi.json` - the real endpoints sit behind `_IncludedRouter` -
    so this test would have been green over a set containing none of this project's routes, and it
    would have stayed green after somebody added a POST.
    """
    found = dict(declared_routes())

    assert DOCUMENTED_ENDPOINTS <= found.keys(), (
        f"the route walk did not reach {DOCUMENTED_ENDPOINTS - found.keys()}; every assertion "
        f"below it would be vacuous"
    )

    non_get = {
        path: sorted(methods)
        for path, methods in found.items()
        if not methods <= {"GET", "HEAD"}
    }
    assert non_get == {}, f"non-GET routes declared: {non_get}"
    assert declared_methods() <= {"GET", "HEAD"}


# ---------------------------------------------------------------------------------------------
# 2-4. The refusal contract, and the sweep's verdict.
# ---------------------------------------------------------------------------------------------

ESTIMATE_KEYS = ("median_pct", "range_pct", "matches")


@pytest.mark.parametrize(
    "reason",
    [
        gate_module.INSUFFICIENT_ANALOGS,
        gate_module.INCONSISTENT_DIRECTION,
        gate_module.INCOMPLETE_OUTCOMES,
    ],
)
def test_a_refused_conclusion_has_no_estimate_key_at_all(monkeypatch, reason):
    """Test 2, decision 3. The keys are ABSENT, not null.

    `{"median_pct": null}` is one frontend default away from rendering `0%`. `median_pct ?? 0`,
    `Number(x) || 0`, a chart library's `defaultValue` - each is a reasonable line of client code
    and each converts a refusal into a confident claim that nothing changed. A CLIENT CANNOT
    DEFAULT A KEY THAT DOES NOT EXIST.

    Parameterized over all three refusal reasons because the shape must hold for each: they are
    different news and must not become different shapes.
    """
    stub_engine(monkeypatch, lambda as_of: refused_result(reason=reason, as_of=as_of))

    body = get().json()

    assert body["gate"] == "refused"
    assert body["reason"] == reason

    for key in ESTIMATE_KEYS:
        assert key not in body, (
            f"a refused conclusion carries {key!r} = {body[key]!r}. Absent, not null: a null is a "
            f"key a client can default, and the default renders as a number the engine refused to "
            f"compute."
        )


def test_a_refused_conclusion_contains_no_number_readable_as_an_estimate(monkeypatch):
    """Test 3, decision 3. A RECURSIVE WALK over every numeric leaf, not a check of named fields.

    The failure this catches is a number somewhere nobody thought to look - in a nested block, in a
    sibling object, three levels down, added by a later commit for a good reason. `app/analogs/
    gate.py` makes the same argument for the same reason and its own test walks the same way.

    Every number that survives must be a COUNT, a REQUIRED THRESHOLD, or a SWEEP STATISTIC. The
    allow-list is by key name and is deliberately short: a new numeric field in a refusal body
    fails this test until somebody writes down which of the three it is.
    """
    allowed = {
        # Counts. What the detector saw, what the gate consumed, what was unmeasurable.
        "analogs",
        "incomplete",
        "raw",
        "collapsed",
        # The threshold the refusal fell short of. A fact about the gate, not about the market.
        "required",
        # The sweep's verdict, which rides on every conclusion. A q-value is a statement about the
        # relationship's significance, never about the size of a move.
        "best_q",
        "run_id",
        "grid_size",
        "passing_pairs",
        "scanned_pairs",
    }

    stub_engine(monkeypatch, lambda as_of: refused_result(as_of=as_of))
    body = get().json()

    leaves = list(numeric_leaves(body))
    assert leaves, "the walk found no numbers at all, which means it is not walking anything"

    offenders = [(path, value) for path, value in leaves if path[-1] not in allowed]
    assert offenders == [], (
        f"a refused conclusion carries numbers that are not counts, thresholds or sweep "
        f"statistics: {offenders}. Every number in a refusal must be one of the three - anything "
        f"else is an estimate the gate refused to produce, arriving through a field nobody read as "
        f"one."
    )

    # And the specific one this project cares most about, spelled out: no percentage anywhere.
    assert not any("pct" in part for path, _ in leaves for part in path)


@pytest.mark.parametrize(
    "result_for, expected_gate",
    [
        (passing_result, "passed"),
        (refused_result, "refused"),
        (quiet_result, "no_current_event"),
    ],
)
def test_every_conclusion_response_embeds_the_sweep_verdict(
    monkeypatch, result_for, expected_gate
):
    """Test 4, decision 4. All three shapes, with the denominator beside the passing count.

    Phase 7 decision 8: an analog output must never be readable without the sweep's verdict beside
    it. Serialization is where that coupling is most likely to be dropped, because the block looks
    like metadata a frontend does not need.

    Phase 6 scanned 6,966 pairs and ONE passed, at lag 0, with zero passing rows at any non-zero
    lag in either direction. An engine finding confident analogs where the sweep found no
    relationship has a bug, not a discovery - and that check is unavailable to anyone reading a
    body that dropped this block.
    """
    stub_engine(monkeypatch, lambda as_of: result_for(as_of=as_of))

    body = get().json()

    assert body["gate"] == expected_gate
    assert "sweep" in body, f"the {expected_gate} shape has no sweep block"

    sweep = body["sweep"]
    assert sweep.keys() == {
        "best_q",
        "run_id",
        "grid_size",
        "passing_pairs",
        "scanned_pairs",
    }
    assert sweep["best_q"] == pytest.approx(0.0446)
    assert sweep["run_id"] == 1
    assert sweep["passing_pairs"] == 1
    assert sweep["scanned_pairs"] == 6966, (
        "a passing count without its denominator is the dishonest form: 1 reads as a finding, "
        "1 of 6,966 reads as the top of a distribution"
    )


# ---------------------------------------------------------------------------------------------
# 6. No numeric defaults.
# ---------------------------------------------------------------------------------------------


def _response_models():
    """Every BaseModel defined in `app.api.models`."""
    for name, obj in vars(models).items():
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj.__module__ == models.__name__
        ):
            yield name, obj


def test_no_response_model_declares_a_numeric_default():
    """Test 6, decision 5. A field is either required or a discriminator literal. Never defaulted.

    `pct_of_tariff: float = 0` silently converts a winter navigation closure into a week when
    barge freight was free. `tons: float = 0` converts a reporting gap into "no grain moved" -
    and since USDA publishes an explicit `tons = 0` on 8,218 of 26,144 records, the fabricated
    zeros would be indistinguishable from the reported ones.

    `= None` FAILS THIS TEST TOO, and that is deliberate. A nullable field with a None default is a
    field a route can forget to fill in, and the symptom is a `null` that looks exactly like a
    faithfully preserved one. Required-and-nullable is the shape: the route must state the value,
    and the value may be null.
    """
    offenders = []
    for model_name, model in _response_models():
        assert model.model_fields, f"{model_name} declares no fields"
        for field_name, field in model.model_fields.items():
            if field.default_factory is not None:
                offenders.append(f"{model_name}.{field_name} (default_factory)")
                continue
            default = field.default
            if default is PydanticUndefined:
                continue
            # The only permitted default is the discriminator's own literal, which is a string and
            # is what makes the union dispatch on `gate` rather than on field presence.
            if isinstance(default, str):
                continue
            offenders.append(f"{model_name}.{field_name} = {default!r}")

    assert offenders == [], (
        f"response model fields carrying a default: {offenders}. A nullable field must be "
        f"REQUIRED and nullable, so a NULL from the database reaches the client as `null` and a "
        f"route that failed to read the column fails loudly."
    )


def test_the_models_that_matter_declare_their_nullable_fields_nullable():
    """The other half of decision 5: the NULL-bearing columns are typed to admit NULL. Not numbered.

    A `float` where the column is nullable would make a preserved NULL a 500 rather than a `null`,
    which is a different bug from a coalesced zero and would be found in production rather than
    here.
    """
    import typing

    for model, field_name in (
        (models.BargeRate, "pct_of_tariff"),
        (models.LockMovement, "tons"),
        (models.GaugeReading, "value"),
        (models.Signal, "q_value"),
        (models.Signal, "statistic"),
        (models.JobHealth, "last_success"),
        (models.TableFreshness, "newest"),
    ):
        annotation = model.model_fields[field_name].annotation
        assert type(None) in typing.get_args(annotation), (
            f"{model.__name__}.{field_name} is not nullable, and its column is"
        )


# ---------------------------------------------------------------------------------------------
# 7. Errors say nothing.
# ---------------------------------------------------------------------------------------------

# Substrings that must never appear in a response body. SQL fragments, the schema, and the shape of
# a connection string with a password in it.
FORBIDDEN = (
    "SELECT",
    "FROM ",
    "INSERT",
    "relation ",
    "postgresql://",
    "hunter2",
    "5432",
    "barge_rates",
    "Traceback",
)


def _assert_body_says_nothing(response, *, expected_status, expected_code):
    body = response.json()
    text = response.text

    assert response.status_code == expected_status, text
    assert body["error"]["code"] == expected_code
    assert body["error"]["correlation_id"], "a failure with no id cannot be traced in the log"

    for fragment in FORBIDDEN:
        assert fragment not in text, (
            f"the error body contains {fragment!r}:\n{text}\n"
            f"DATABASE_URL carries the password, and psycopg puts the host, the user and the "
            f"statement into its own messages."
        )


def test_no_error_body_contains_sql_or_a_connection_string(monkeypatch, client):
    """Test 7, decision 9. Three failure modes, one shape, nothing leaked.

    `str(exc)` in a response body is one line long, reads like helpful debugging, and is how a
    credential reaches a browser's network tab, a screenshot and a bug report.
    """
    # 1. A query that raises with SQL AND a connection string in its message.
    exploding = make_client(conn=ExplodingConn())
    _assert_body_says_nothing(
        exploding.get("/api/rates?start=2022-01-01&end=2022-12-31"),
        expected_status=500,
        expected_code="internal_error",
    )

    # 2. Nobody told this process which database to talk to. The REAL dependency has to run for
    # this one, so the override installed above is removed first - without that, this mode would
    # silently re-test mode 1 and report a 500 as if it were the connection failure.
    from app.api.main import app

    app.dependency_overrides.clear()
    monkeypatch.delenv("API_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _assert_body_says_nothing(
        client.get("/api/health"),
        expected_status=503,
        expected_code="database_unavailable",
    )

    # 3. The engine itself raises, from inside the conclusion route, past the cache.
    from app.analogs import engine

    def exploding_query(conn, *, as_of, site_id, persist=True):
        raise RuntimeError(ExplodingConn.MESSAGE)

    monkeypatch.setattr(engine, "query", exploding_query)
    _assert_body_says_nothing(
        make_client(conn=FakeConn(run_summary_row=RUN_ROW)).get(
            f"/api/conclusion?site_id={MEMPHIS}&as_of=2022-10-11"
        ),
        expected_status=500,
        expected_code="internal_error",
    )


def test_a_validation_error_names_the_field_and_nothing_else():
    """A 422 is useful without being chatty. Not numbered.

    The field messages are pydantic's, about the REQUEST, and never see a database. Included
    because a 422 that does not say which parameter was wrong sends the client author to read the
    source.
    """
    response = make_client(conn=FakeConn()).get("/api/rates")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["correlation_id"]
    for fragment in FORBIDDEN:
        assert fragment not in response.text


# ---------------------------------------------------------------------------------------------
# 8. No gate logic under app/api/.
# ---------------------------------------------------------------------------------------------

# The gate's own numbers. Written down here, in the test, so that finding either of them written
# down under `app/api/` is a failure.
FORBIDDEN_LITERALS = {parameters.MIN_ANALOGS, parameters.MIN_DIRECTIONAL_CONSISTENCY}

# Names that may be REFERENCED through the parameters module and never re-bound or re-derived.
GATE_PARAMETER_NAMES = {"MIN_ANALOGS", "MIN_DIRECTIONAL_CONSISTENCY"}

# Quantities the API must never compare. Reading `result.gate.n_analogs` and putting it in a field
# is mapping; comparing it against anything is evaluating the gate a second time.
UNCOMPARABLE = {
    "n_analogs",
    "n_consistent",
    "n_incomplete",
    "analogs",
    "consistent",
    "consistency",
    "directional_consistency",
}

# Functions that COMPUTE an estimate. The API may read a summary the engine produced; it may not
# produce one.
FORBIDDEN_CALLS = {"summarize", "median", "evaluate"}

FORBIDDEN_IMPORTS = {"statistics", "math"}


def _api_modules():
    paths = sorted(API_ROOT.rglob("*.py"))
    assert len(paths) >= 8, f"only found {len(paths)} modules under app/api/; the walk is wrong"
    for path in paths:
        yield path, ast.parse(path.read_text())


def test_api_modules_contain_no_gate_logic():
    """Test 8, decision 10. The structural half of the no-second-implementation guard.

    A second implementation of the confidence gate would be CLAUDE.md § 4's two-tables-of-one-fact
    failure arriving in the layer users actually see - and it would diverge silently, because both
    copies would keep returning plausible answers. `test_conclusion.py::
    test_conclusion_calls_the_engine_and_does_not_reimplement_it` is the behavioural half; neither
    alone is enough.

    FOUR RULES, and each one closes a different way of writing the gate down again:

      the literals    `>= 4` or `>= 0.70` anywhere under app/api/
      the names       MIN_ANALOGS bound to something local rather than read from `parameters`
      the comparisons an analog count compared against anything at all
      the calls       summarize/median/evaluate, and the modules that make them available
    """
    literal_offenders = []
    name_offenders = []
    compare_offenders = []
    call_offenders = []
    import_offenders = []

    for path, tree in _api_modules():
        where = path.relative_to(API_ROOT.parent.parent)

        # A row index is not a threshold. `row[4]` is how a cursor tuple is unpacked, and flagging
        # it would make this rule fire on ordinary mapping code - which is how a guard gets
        # loosened until it stops guarding. Every OTHER position a constant can occupy stays
        # covered, including `THRESHOLD = 4` and `>= 4`.
        indices = {
            id(inner)
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            for inner in ast.walk(node.slice)
            if isinstance(inner, ast.Constant)
        }

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and id(node) not in indices
            ):
                if not isinstance(node.value, bool) and node.value in FORBIDDEN_LITERALS:
                    literal_offenders.append(f"{where}:{node.lineno} -> {node.value!r}")

            # A gate parameter name is legal ONLY as `parameters.<NAME>`. A bare Name node means
            # somebody imported it directly, at which point the route holds its own copy of the
            # binding and a later edit to `parameters.py` stops reaching it.
            if isinstance(node, ast.Name) and node.id in GATE_PARAMETER_NAMES:
                name_offenders.append(f"{where}:{node.lineno} -> {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in GATE_PARAMETER_NAMES:
                base = node.value
                if not (isinstance(base, ast.Name) and base.id == "parameters"):
                    name_offenders.append(f"{where}:{node.lineno} -> {node.attr}")

            if isinstance(node, ast.Compare):
                for inner in ast.walk(node):
                    named = getattr(inner, "id", None) or getattr(inner, "attr", None)
                    if named in UNCOMPARABLE:
                        compare_offenders.append(f"{where}:{node.lineno} -> {named}")

            if isinstance(node, ast.Call):
                called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if called in FORBIDDEN_CALLS:
                    call_offenders.append(f"{where}:{node.lineno} -> {called}")

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        import_offenders.append(f"{where}:{node.lineno} -> {alias.name}")
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                    import_offenders.append(f"{where}:{node.lineno} -> {node.module}")

    assert literal_offenders == [], (
        f"the gate's own thresholds are written down under app/api/: {literal_offenders}. They "
        f"live in app/analogs/parameters.py with their provenance beside them, and a copy here is "
        f"a second definition that will not be changed when that one is."
    )
    assert name_offenders == [], (
        f"a gate parameter is bound locally rather than read through `parameters`: {name_offenders}"
    )
    assert compare_offenders == [], (
        f"an analog count is compared under app/api/: {compare_offenders}. Reading a count and "
        f"putting it in a field is mapping; comparing it is evaluating the gate a second time."
    )
    assert call_offenders == [], (
        f"an estimate is computed under app/api/: {call_offenders}. A refused query has no "
        f"summary because `outcomes.summarize` was never called - a call here would give it one."
    )
    assert import_offenders == [], (
        f"app/api/ imports a computation module: {import_offenders}"
    )


def test_the_gate_parameters_are_reachable_from_the_api_only_through_the_parameters_module():
    """The rule above is non-vacuous: the API DOES use both parameters, through `parameters`.

    Not numbered, and it is the guard on the guard. If `app/api/` referenced neither threshold,
    every assertion in the test above would pass over an empty set - and the day somebody wrote one
    down, the test would still pass if they had also removed the legitimate reference. Asserting
    the legitimate uses exist pins what "only through `parameters`" is measured against.
    """
    conclusion = (API_ROOT / "routes" / "conclusion.py").read_text()

    assert "parameters.MIN_ANALOGS" in conclusion, (
        "the refusal shape's `required` field must come from the parameters module"
    )
    assert "parameters.OUTCOME_WINDOW_DAYS" in conclusion, (
        "the passing shape's `window_days` must come from the parameters module"
    )


WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT")


def _sql_literals():
    """Every SQL string this package holds: `*_SQL` constants and inline `.execute(...)` arguments.

    NOT a grep over the file text, and the first version of this test was exactly that - it flagged
    `cache.py` for the word "Drop" in a docstring and `main.py` for the sentence "No POST, no PUT,
    no PATCH, no DELETE". A prose-matching rule that fires on its own documentation is a rule that
    gets deleted, and then nothing is checking the SQL.

    So this reads the syntax tree and collects only strings that ARE queries: assigned to a
    `*_SQL` name, or passed positionally to `.execute()`.
    """
    found = []
    for path, tree in _api_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id.endswith("_SQL")
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found.append((path.name, target.id, node.value.value))
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.append((path.name, f"execute@{node.lineno}", node.args[0].value))
    return found


def test_no_api_module_writes_to_the_database():
    """Read-only, asserted over the SQL this package actually issues. Not numbered.

    The route table shows no non-GET method and the conclusion route passes `persist=False`; this
    is the third check, and it is the one that would catch a write hidden inside a SELECT-shaped
    helper. The deployed role cannot write either - but a role that has never been observed
    refusing a write is not known to be read-only, which is why the live procedure proves it with
    a DELETE that must fail.
    """
    literals = _sql_literals()
    assert len(literals) >= 8, (
        f"only {len(literals)} SQL literals found under app/api/; this walk is not reaching the "
        f"queries and every assertion below it is vacuous"
    )

    offenders = []
    for module, name, sql in literals:
        for line in sql.splitlines():
            first = line.strip().split(" ")[0].upper()
            if first in WRITE_VERBS:
                offenders.append(f"{module}:{name} -> {first}")

    assert offenders == [], f"app/api/ issues a writing statement: {offenders}"

    # And every one of them is a SELECT. A query that is neither a read nor a recognized write is
    # something this rule has not thought about, which is worth failing on rather than passing.
    for module, name, sql in literals:
        assert "SELECT" in sql.upper(), f"{module}:{name} is not a SELECT: {sql[:60]!r}"
