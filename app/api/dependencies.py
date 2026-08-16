"""What every route is handed: a connection, a clock, a bound, and a date range.

THE BOUND AND THE RANGE ARE DEPENDENCIES RATHER THAN PER-ROUTE ARGUMENTS ON PURPOSE. A limit
written out three times is a limit that is 5000 in two places and 50000 in the third, and the
third one is discovered when somebody asks a series endpoint for 258,739 instantaneous rows
through a JSON serializer. One definition, injected.

REQUIRING THE DATE RANGE IS THE POINT, NOT A CONVENIENCE. An endpoint with unbounded defaults
invites a client author to fetch everything, and the cost is invisible from the client's side -
the request just takes a while and then works, until the day it does not. Making `start` and `end`
required puts the decision in the query string, where whoever writes the client has to look at it.

THE CONNECTION SHOULD BE A READ-ONLY ROLE, AND THAT IS AN OPERATIONAL FACT THIS FILE CANNOT
ENFORCE. `API_DATABASE_URL` names a role created by a human with GRANT SELECT and nothing else
(the live procedure creates it, and then PROVES it by watching a DELETE fail - a read-only role
that has never been observed refusing a write is not known to be read-only). This file prefers that
variable and falls back to `DATABASE_URL` with a warning, because the alternative - refusing to
start without it - would break every developer and every test run for a property that is enforced
in three other places anyway: no non-GET route is declared, no route calls anything that writes,
and the engine is called with `persist=False`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import Query

from app import db
from app.api.errors import INVALID_REQUEST, SPAN_TOO_LONG, ApiError

logger = logging.getLogger(__name__)

# The role the API connects as, when a human has created one. See the module docstring.
API_DATABASE_URL_VAR = "API_DATABASE_URL"

# Decision 6. 500 is a page a chart can draw; 5000 is the ceiling above which a client should be
# paging rather than asking. Neither is clamped: see `page` below.
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

# Decision 7. Five years, counted generously in days so a leap year cannot turn an exactly-five-year
# request into a rejection - the limit is a guard against a client fetching a decade by accident,
# not an arithmetic exercise.
MAX_SPAN_YEARS = 5
MAX_SPAN_DAYS = MAX_SPAN_YEARS * 366


def api_database_url() -> str:
    """`API_DATABASE_URL` if a read-only role exists, otherwise `DATABASE_URL` with a warning.

    The warning is at WARNING rather than INFO deliberately: it says the API is connecting as a
    role that may be able to write, which is a real deviation from the deployed configuration and
    should be visible in a log somebody scans rather than buried in one they grep.

    NEITHER VALUE IS EVER LOGGED OR RETURNED IN A RESPONSE. Both carry a password.
    """
    url = os.environ.get(API_DATABASE_URL_VAR, "").strip()
    if url:
        return url
    logger.warning(
        "%s is not set; falling back to %s. The API is connecting as whatever role that names, "
        "which may be able to write. The deployed configuration uses a role granted SELECT only.",
        API_DATABASE_URL_VAR,
        db.DATABASE_URL_VAR,
    )
    return db.database_url()


def get_connection():
    """One connection per request, closed on the way out, NEVER COMMITTED.

    `db.connection` deliberately does not commit on a clean exit (see app/db.py), so this layer
    cannot write by accident even if a query somewhere acquired the ability to: the transaction is
    rolled back when the connection closes. That is a third belt beside the two braces - no non-GET
    route, and `persist=False` into the engine.

    A pool would be the obvious improvement and is deliberately not here: pooling is a lifecycle
    concern that belongs with the containerization work in Phase 10, and adding it in the same
    commit as the API's own logic would mean debugging two new failure modes at once.
    """
    with db.connection(api_database_url()) as conn:
        yield conn


def now() -> datetime:
    """The clock, injected so a test can freeze it.

    A route that calls `datetime.now()` directly is a route whose freshness arithmetic can only be
    tested by waiting, and a test that waits is a test that gets a `sleep` in it.
    """
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Page:
    """The bound this request asked for. Echoed back in the response envelope."""

    limit: int
    offset: int


def page(
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=(
            f"Rows to return, at most {MAX_LIMIT}. A larger value is REJECTED with 422 rather "
            f"than clamped: a clamped request returns fewer rows than it asked for with nothing "
            f"in the response to say so, which is the same silence a truncated page produces."
        ),
    ),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> Page:
    """`limit` and `offset`, validated by the framework so an over-maximum value is a 422.

    `le=MAX_LIMIT` rather than `min(limit, MAX_LIMIT)` IS THE DECISION. The clamp is one character
    shorter and it lies: the client asked for 50,000 rows, received 5,000, and has no way to tell
    that apart from a filter that matched 5,000 rows. `total` would eventually reveal it; a client
    that reads `total` is not the client this guard is for.
    """
    return Page(limit=limit, offset=offset)


@dataclass(frozen=True)
class DateRange:
    """An explicit, bounded window. Both ends required.

    `days` is inclusive of neither end particularly - it is the span, used only to compare against
    the maximum. The SQL uses `BETWEEN start AND end`, inclusive at both ends, which is what a
    reader of a date range expects and what migration 0012 states for gap boundaries.
    """

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def date_range(
    start: date = Query(
        ...,
        description=(
            "First day of the window, inclusive. REQUIRED - there is no default, because an "
            "unbounded default invites a client to fetch the whole record through a JSON "
            "serializer and makes that cost invisible to whoever writes the client."
        ),
    ),
    end: date = Query(..., description="Last day of the window, inclusive. REQUIRED."),
) -> DateRange:
    """The window, with its two failure modes named separately.

    An inverted range and an over-long one are different mistakes - a swapped pair of parameters
    against a request for a decade - and they get different codes so a client can tell a bug in its
    own code from a limit it has to page around.
    """
    if end < start:
        raise ApiError(
            INVALID_REQUEST,
            f"`end` ({end}) is before `start` ({start}).",
            status_code=422,
        )

    window = DateRange(start=start, end=end)
    if window.days > MAX_SPAN_DAYS:
        raise ApiError(
            SPAN_TOO_LONG,
            f"The requested window spans {window.days} days. Series endpoints accept at most "
            f"{MAX_SPAN_YEARS} years ({MAX_SPAN_DAYS} days) per request; page with `start` and "
            f"`end` instead.",
            status_code=422,
        )
    return window


# ---------------------------------------------------------------------------------------------
# Shared reads. Not FastAPI dependencies - shared SQL, kept in one place for one reason.
# ---------------------------------------------------------------------------------------------
#
# `run_summary` is read by BOTH the conclusion route (which embeds the sweep's verdict beside every
# answer) and the signals route (which reports the run a page of rows came from). Two copies of
# this query would be two definitions of "how many pairs passed", and the moment they disagree the
# conclusion endpoint and the signals endpoint would report different denominators for the same
# run - with nothing comparing them.
#
# `passes_gate` is READ, never recomputed. The sweep computes and stores it precisely so consumers
# filter rather than the writer selecting (migration 0023).

RUN_SUMMARY_SQL = """
SELECT r.run_id, r.started_at, r.finished_at, r.grid_size, r.lag_min, r.lag_max, r.horizons,
       r.regimes, r.feature_filter, r.git_sha, r.git_dirty, r.seed,
       count(s.run_id)                                        AS scanned_pairs,
       count(*) FILTER (WHERE s.passes_gate)                   AS passing_pairs
  FROM signal_runs r
  LEFT JOIN signals s ON s.run_id = r.run_id
 WHERE r.run_id = %(run_id)s
 GROUP BY r.run_id
"""

LATEST_RUN_SQL = "SELECT max(run_id) FROM signal_runs"


def run_summary(conn, run_id: int | None):
    """One run's parameters and its two counts, or None when there is no such run.

    A LEFT JOIN rather than an inner one, so a run that enumerated a grid and wrote no rows yet -
    a sweep that died, whose `finished_at` is still NULL - reports `scanned_pairs = 0` rather than
    vanishing. A run that disappears from a listing because it produced nothing is the disappearing
    denominator one level up from the one `signals` exists to preserve.
    """
    if run_id is None:
        return None
    row = conn.execute(RUN_SUMMARY_SQL, {"run_id": run_id}).fetchone()
    return row


def latest_run_id(conn) -> int | None:
    """The most recent run, and MOST RECENT rather than best.

    Taking the run with the smallest q-values anywhere would be selecting the friendliest
    experiment - the model-selection failure `app/signals/` is arranged to prevent, performed by
    the consumer instead of the writer. `app/analogs/engine.py` reads the latest run for the same
    reason and says so at length. One run is one experiment; the latest one is the current answer.
    """
    row = conn.execute(LATEST_RUN_SQL).fetchone()
    return row[0] if row else None
