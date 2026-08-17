"""The app. READ-ONLY, AND THE ROUTER REGISTRATION IS WHERE THAT IS EITHER TRUE OR NOT.

NO POST, NO PUT, NO PATCH, NO DELETE. No endpoint triggers a backfill, a sweep, or a build, and
`tests/api/test_contract.py::test_no_non_get_route_is_declared` walks this app's route table and
asserts it.

WHY THAT IS A HARD RULE AND NOT PHASE ORDERING
-----------------------------------------------
The sweep and the backfills are research and ingest operations whose outputs a human reads before
anything consumes them. An HTTP-triggerable sweep is a sweep that runs unattended, and a sweep that
runs unattended accumulates runs nobody reviewed - a table of q-values from experiments nobody
chose, each individually correct, none of them examined. The backfills are worse: they run for
hours, and a scheduled or triggerable copy would sit `running` in a way the heartbeat cannot
distinguish from healthy (CLAUDE.md § 14).

The database role should be read-only too, and creating it is a live step this agent cannot take -
the GRANT statements are in the live procedure for a human, and the procedure PROVES the role by
watching a DELETE fail rather than assuming it.

NOT CONTAINERIZED IN THIS COMMIT, DELIBERATELY. It runs under uvicorn from the host venv like the
scheduler does. Containerizing it here would mean this commit's live verification runs against a
different execution path than its tests, and the Compose wiring has its own failure modes worth
isolating from the API's own. The `api` and `caddy` services, TLS and the domain are Phase 10.

    uvicorn app.api.main:app --host 127.0.0.1 --port 8000

BIND TO LOOPBACK. The security group and the DOCKER-USER chain cover published container ports;
neither covers a host process bound to 0.0.0.0, and there is no TLS in front of this until Phase 10.
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app import db
from app.api import errors
from app.api.middleware.ratelimit import RateLimitMiddleware
from app.api.routes import conclusion, health, series, signals

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Inland Waterway Signals",
    description=(
        "Read-only. Historical analogs, gauge series, barge rates, lock movements, and the "
        "lead-lag sweep's verdict. A refused conclusion carries no estimate anywhere in its "
        "body - the estimate keys are ABSENT rather than null, because a client cannot default "
        "a key that does not exist."
    ),
    version="8.0",
)


# The order of registration is the order of specificity for the handlers below, not for the routes:
# Starlette dispatches an exception to the handler registered for its most derived matching class.
#
# `Exception` last, and it is the only one that can catch something this project did not anticipate.
app.add_exception_handler(errors.ApiError, errors.api_error_handler)
app.add_exception_handler(RequestValidationError, errors.validation_error_handler)

# psycopg's OperationalError covers "cannot connect", "server closed the connection", and
# authentication failures - the failures whose messages carry a host, a user, and sometimes the
# whole connection string. db.ConfigurationError is the same class of problem one step earlier:
# nobody told this process which database to talk to.
app.add_exception_handler(psycopg.OperationalError, errors.database_error_handler)
app.add_exception_handler(db.ConfigurationError, errors.database_error_handler)

app.add_exception_handler(Exception, errors.unhandled_error_handler)


# THE RATE LIMITER (CLAUDE.md § 22's Phase 11 cost-based exception). Keys on the proxy-set
# X-Real-IP; see app/api/middleware/ratelimit.py for why nothing else works behind Caddy.
#
# /api/health is exempt by exact path match, so the external monitor is never throttled into a
# false alarm.
app.add_middleware(RateLimitMiddleware)


app.include_router(health.router)
app.include_router(conclusion.router)
app.include_router(series.router)
app.include_router(signals.router)


def declared_routes(router=None) -> list[tuple[str, frozenset[str]]]:
    """Every `(path, methods)` this app declares, WALKED RECURSIVELY.

    THE OBVIOUS VERSION OF THIS FUNCTION IS VACUOUS, AND IT WAS WRITTEN FIRST HERE BEFORE BEING
    CAUGHT. `for route in app.routes: route.methods` looks complete and, on Starlette 1.6, returns
    only `{GET, HEAD}` from `/docs` and `/openapi.json`: `include_router` inserts a single
    `_IncludedRouter` object per router, and the real endpoints live one level down behind
    `original_router`. A test built on the flat version would assert "every declared method is a
    GET" over a set containing none of this project's routes, and IT WOULD STAY GREEN AFTER
    SOMEBODY ADDED A POST - which is CLAUDE.md § 2's theme 2 exactly, and the same shape as the
    ingress test that passed because the set it constrained was empty.

    So this recurses, and it returns PATHS as well as methods so the caller can prove the walk
    reached something. `test_no_non_get_route_is_declared` asserts the endpoint list it found
    contains the routes this API is documented to have; a walk that silently stopped early fails
    that assertion before it gets to the methods.
    """
    router = app.router if router is None else router

    found: list[tuple[str, frozenset[str]]] = []
    for route in getattr(router, "routes", ()):
        # A router included with `include_router` on this Starlette version. The endpoints are
        # behind `original_router`; `routes` covers Mount and older/plain routers.
        nested = getattr(route, "original_router", None) or (
            route if hasattr(route, "routes") else None
        )
        if nested is not None:
            found.extend(declared_routes(nested))
            continue

        path = getattr(route, "path", None)
        if path is None:
            continue
        found.append((path, frozenset(getattr(route, "methods", None) or ())))
    return found


def declared_methods() -> set[str]:
    """Every HTTP method this app declares. Read-only means this set is `{GET, HEAD}`.

    HEAD is in it because Starlette adds one alongside every GET; it is read-only by definition and
    is not a route this project declared.
    """
    methods: set[str] = set()
    for _, route_methods in declared_routes():
        methods.update(route_methods)
    return methods
