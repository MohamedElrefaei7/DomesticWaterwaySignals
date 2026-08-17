"""Typed errors. NO EXCEPTION TEXT, NO SQL, NO CONNECTION STRING, EVER, IN A RESPONSE BODY.

DATABASE_URL CARRIES THE PASSWORD, and it is the string psycopg puts into an OperationalError:

    connection failed: connection to server at "10.0.1.7", port 5432 failed: FATAL: password
    authentication failed for user "waterway_api"

That message names the host and the user; a URL-parse failure names more. `str(exc)` in a response
body is one line long, reads like helpful debugging, and is how a credential reaches a browser's
network tab, a screenshot, and a bug report. So nothing here interpolates an exception into a body
under any circumstances - not the message, not the type name, not a truncated prefix.

THE DETAIL IS NOT DISCARDED, IT IS RELOCATED. Every failure is logged server-side with a
correlation id, and the id is the only thing that crosses the boundary. A user reporting "I got
error 9f2c1a4b0e73" gives an operator an exact grep, which is the whole of what the exception text
would have bought and none of what it would have cost.

THE CODE IS THE BRANCHABLE PART. A frontend needs to tell "the database is down" from "you asked
for a 40-year span" without parsing prose, so every body carries a stable `code` from the closed
set below. Prose is for humans and may be reworded; codes are an interface.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# The closed set of error codes. A frontend branches on these; they are as much an interface as the
# response shapes are, and a new one is an additive change to this tuple rather than a new string
# invented at a raise site.
INVALID_REQUEST = "invalid_request"
SPAN_TOO_LONG = "span_too_long"
NOT_FOUND = "not_found"
DATABASE_UNAVAILABLE = "database_unavailable"
INTERNAL_ERROR = "internal_error"
# Phase 11. The application-side rate limiter (CLAUDE.md § 22's cost-based exception) refuses
# through this code, so a client branches on it rather than on the status line alone.
RATE_LIMITED = "rate_limited"

CODES: tuple[str, ...] = (
    INVALID_REQUEST,
    SPAN_TOO_LONG,
    NOT_FOUND,
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    RATE_LIMITED,
)


class ApiError(Exception):
    """A failure this layer chose to report, with a code and a message written BY US.

    The message is composed at the raise site from values this project already knows are safe -
    a limit, a span, a site id the caller supplied. It is never derived from an exception.
    """

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        if code not in CODES:
            raise ValueError(f"unknown error code {code!r}. Known: {list(CODES)}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def correlation_id() -> str:
    """A short opaque id, returned to the client and logged beside the real detail.

    Not derived from anything about the request or the failure: an id that encoded the path, the
    time, or the error class would be a channel leaking the same information this module exists to
    keep on the server side.
    """
    return uuid.uuid4().hex[:12]


def error_response(
    *, code: str, message: str, status_code: int, cid: str, fields=None
) -> JSONResponse:
    """The one error shape. Every failure in this API serializes through this function.

    `fields` carries per-parameter validation messages, and it is the one place text this layer did
    not author reaches a body. That text is pydantic's, about the REQUEST - "field required",
    "Input should be a valid date" - and it never sees a database, a query, or a connection. It is
    included because a 422 that does not say which parameter was wrong sends the client author to
    read the source.
    """
    body = {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": cid,
        }
    }
    if fields is not None:
        body["error"]["fields"] = fields
    return JSONResponse(status_code=status_code, content=body)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """A failure this layer raised deliberately. Its message is ours and is safe by construction."""
    cid = correlation_id()
    logger.warning(
        "api error [%s] %s %s: %s", cid, request.method, request.url.path, exc.message
    )
    return error_response(
        code=exc.code, message=exc.message, status_code=exc.status_code, cid=cid
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI's own 422, re-shaped into the one error body.

    Overridden rather than left at the default because the default emits `{"detail": [...]}`, which
    is a second error shape a client would have to branch on separately - and because the default
    includes an `input` echo and a `url` pointing at pydantic's documentation, neither of which
    belongs in this project's error contract.
    """
    cid = correlation_id()
    fields = [
        {
            "field": ".".join(str(part) for part in item.get("loc", ())),
            "message": item.get("msg", ""),
        }
        for item in exc.errors()
    ]
    logger.warning(
        "invalid request [%s] %s %s: %s", cid, request.method, request.url.path, fields
    )
    return error_response(
        code=INVALID_REQUEST,
        message="The request parameters are not valid.",
        status_code=422,
        cid=cid,
        fields=fields,
    )


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The database refused, disappeared, or was never configured. 503, and the body says nothing.

    `logger.exception` writes the traceback - including whatever psycopg put in the message - to
    the server log against the correlation id. The body carries the id and a fixed sentence.

    503 rather than 500 because the distinction is actionable: the API is up and its dependency is
    not, which is a different page for whoever is on call.
    """
    cid = correlation_id()
    logger.exception(
        "database unavailable [%s] %s %s", cid, request.method, request.url.path
    )
    return error_response(
        code=DATABASE_UNAVAILABLE,
        message="The database could not be reached. The detail is in the server log.",
        status_code=503,
        cid=cid,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything else. The last line, and the one that must never get chatty.

    A generic handler that includes `type(exc).__name__` looks harmless and is not: psycopg raises
    `UndefinedTable`, `InsufficientPrivilege` and `SyntaxError` by name, which tells an unauthorized
    reader what schema they are probing and how far they have got.
    """
    cid = correlation_id()
    logger.exception("unhandled error [%s] %s %s", cid, request.method, request.url.path)
    return error_response(
        code=INTERNAL_ERROR,
        message="The request could not be completed. The detail is in the server log.",
        status_code=500,
        cid=cid,
    )
