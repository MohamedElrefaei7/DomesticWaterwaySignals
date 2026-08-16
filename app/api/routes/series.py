"""`/api/gauges`, `/api/gauges/{site_id}/series`, `/api/rates`, `/api/movements`.

NULL SURVIVES. THAT IS THE WHOLE FILE.

Phase 4 spent three commits establishing that `barge_rates.pct_of_tariff` and
`lock_movements.tons` are nullable, that their NULLs mean DIFFERENT things, and that a zero is a
measurement rather than a synonym for silence. Every one of those commits can be undone here by a
single `COALESCE` in a SELECT or a single `= 0` in a response model:

    a NULL rate coalesced to 0     claims barge freight was free during a winter navigation
                                   closure, and drags every average over the series toward it
    a NULL tonnage coalesced to 0  claims no grain moved during a reporting gap, which says
                                   nothing about the river and looks exactly like a real zero
    a zero coalesced to NULL       deletes the 8,218 explicit zeros USDA publishes - which are
                                   precisely the observations an extreme event produces

SO THERE IS NO `COALESCE` ANYWHERE IN THIS FILE, no `IFNULL`, no `or 0` in the mapping, and no
numeric default in the models. Both directions are tested, because a single test can be satisfied
by an implementation that is wrong the other way.

MOVEMENTS ARE NOT SUMMED
-------------------------
`/api/movements` returns one row per (lock, week, commodity), as published - which is exactly the
table's primary key, so the API's row set IS the source's row set. It does not sum across
commodities, and the reason is not tidiness: `lock_movements` is SPARSE - 1,434 zeros in 2,840 rows
at MS Lock 15 - so how to aggregate a sparse series with a nullable measure is a modelling decision
(CLAUDE.md § 1), and any sum silently decides that a NULL contributes zero. That is the coalesce
this file refuses, performed one layer up where nothing downstream can see it.

THE DATE RANGE IS REQUIRED, AND THE MAXIMUM SPAN IS STATED
-----------------------------------------------------------
`gauge_readings_iv` holds 258,739 rows. An endpoint with unbounded defaults invites a client to
fetch all of it through a JSON serializer, and the cost is invisible from the client's side.
Requiring `start` and `end` puts it in the query string where whoever writes the client has to look
at it; the five-year ceiling stops the request that names the whole record anyway.

`segment` AND `location`
-------------------------
The column is `location`, because that is what USDA calls it - migration 0016 renamed it after
measuring the source, and § 16 stores source vocabularies verbatim. `segment` is accepted as a
query alias because it is the name the brief and the live procedure use, and breaking a documented
curl to win a naming argument would be a worse trade. THE RESPONSE ALWAYS SAYS `location`, so
nobody reading a body learns the wrong name for the column. Passing both with different values is
a 422 rather than a silent preference.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api import models
from app.api.dependencies import DateRange, Page, date_range, get_connection, page
from app.api.errors import INVALID_REQUEST, NOT_FOUND, ApiError

router = APIRouter(prefix="/api", tags=["series"])


# ---------------------------------------------------------------------------------------------
# Gauges.
# ---------------------------------------------------------------------------------------------
#
# THE DECLARED RECORD STARTS AND THE OBSERVED COVERAGE ARE BOTH REPORTED, AND THE PAIR IS THE
# POINT. CLAUDE.md § 15: a catalog's date range reports an envelope, not what an endpoint will
# serve, and where they disagree WHAT IS SERVED IS WHAT IS TRUE. Memphis is catalogued 1933-2026
# with 26,886 values and serves nothing between 1994 and 2014. Reporting only `record_start` would
# restate a catalog's claim as a fact about the data; reporting only the observed bounds would hide
# that a seeded assumption exists and can be wrong.
#
# The observed side is measured from `gauge_series`, the view that encodes the iv/dv precedence
# rule once. Computing coverage from either underlying table would be a consumer re-deriving that
# rule, which § 15 forbids in the sentence that created the view.

GAUGES_SQL = """
SELECT g.usgs_site_id, g.name, g.river, g.tier, g.available_params, g.native_cadence_minutes,
       g.iv_record_start, g.dv_record_start,
       c.observed_start, c.observed_end, c.observed_days
  FROM gauges g
  LEFT JOIN (
        SELECT usgs_site_id,
               min(date)              AS observed_start,
               max(date)              AS observed_end,
               count(DISTINCT date)   AS observed_days
          FROM gauge_series
         GROUP BY usgs_site_id
       ) c ON c.usgs_site_id = g.usgs_site_id
 ORDER BY g.usgs_site_id
 LIMIT %(limit)s OFFSET %(offset)s
"""

GAUGES_COUNT_SQL = "SELECT count(*) FROM gauges"


@router.get(
    "/gauges",
    response_model=models.GaugeList,
    summary="The seeded gauges, with declared record starts and observed coverage beside them.",
)
def get_gauges(conn=Depends(get_connection), bound: Page = Depends(page)) -> models.GaugeList:
    total = conn.execute(GAUGES_COUNT_SQL).fetchone()[0]
    rows = conn.execute(
        GAUGES_SQL, {"limit": bound.limit, "offset": bound.offset}
    ).fetchall()

    return models.GaugeList(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        rows=[
            models.Gauge(
                site_id=row[0],
                name=row[1],
                river=row[2],
                tier=row[3],
                available_params=list(row[4]),
                native_cadence_minutes=row[5],
                declared_iv_record_start=row[6],
                declared_dv_record_start=row[7],
                observed_start=row[8],
                observed_end=row[9],
                # A site the view has no rows for produces a NULL count through the LEFT JOIN. 0 is
                # the honest value there - we looked and there are none - while the BOUNDS stay
                # null, because there is no first or last day to name. A count and a bound are
                # different kinds of absence and this is the one place they meet.
                observed_days=row[10] if row[10] is not None else 0,
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------------------------
# One gauge's daily series.
# ---------------------------------------------------------------------------------------------
#
# Read from `gauge_series`, never from the two tables underneath it. The `source` column travels
# with every row because the two sources are NOT identical measurements - different day boundaries,
# different sampling - so a series that switches source mid-history has a SEAM, and the column is
# what keeps that seam visible instead of hidden.

SERIES_SQL = """
SELECT date, param_code, value, source
  FROM gauge_series
 WHERE usgs_site_id = %(site_id)s
   AND date BETWEEN %(start)s AND %(end)s
   AND (%(source)s::text IS NULL OR source = %(source)s)
 ORDER BY date, param_code
 LIMIT %(limit)s OFFSET %(offset)s
"""

SERIES_COUNT_SQL = """
SELECT count(*)
  FROM gauge_series
 WHERE usgs_site_id = %(site_id)s
   AND date BETWEEN %(start)s AND %(end)s
   AND (%(source)s::text IS NULL OR source = %(source)s)
"""

SITE_EXISTS_SQL = "SELECT 1 FROM gauges WHERE usgs_site_id = %(site_id)s"


@router.get(
    "/gauges/{site_id}/series",
    response_model=models.GaugeSeries,
    summary="One gauge's daily values over an explicit window.",
)
def get_gauge_series(
    site_id: str,
    conn=Depends(get_connection),
    window: DateRange = Depends(date_range),
    bound: Page = Depends(page),
    source: str | None = Query(
        None,
        description=(
            "Restrict to one source: `iv` (derived from instantaneous readings) or `dv` "
            "(published daily means). Omit for the view's own precedence, which prefers `iv` "
            "where both cover a day."
        ),
    ),
) -> models.GaugeSeries:
    # An unknown site is a 404 rather than an empty series, because the two mean different things
    # and an empty series is the answer to a legitimate question about a real gauge in a quiet
    # window. A typo'd site id returning `total: 0` reads as "this gauge has no data", which sends
    # somebody to investigate ingest.
    if conn.execute(SITE_EXISTS_SQL, {"site_id": site_id}).fetchone() is None:
        raise ApiError(
            NOT_FOUND,
            f"No gauge {site_id!r}. The site list is human-owned; see /api/gauges for the seeded "
            f"sites.",
            status_code=404,
        )

    if source is not None and source not in ("iv", "dv"):
        raise ApiError(
            INVALID_REQUEST,
            f"`source` must be 'iv' or 'dv', got {source!r}.",
            status_code=422,
        )

    params = {
        "site_id": site_id,
        "start": window.start,
        "end": window.end,
        "source": source,
        "limit": bound.limit,
        "offset": bound.offset,
    }
    total = conn.execute(SERIES_COUNT_SQL, params).fetchone()[0]
    rows = conn.execute(SERIES_SQL, params).fetchall()

    return models.GaugeSeries(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        site_id=site_id,
        start=window.start,
        end=window.end,
        rows=[
            models.GaugeReading(
                date=row[0], param_code=row[1], value=row[2], source=row[3]
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------------------------
# Barge rates.
# ---------------------------------------------------------------------------------------------
#
# `pct_of_tariff` IS SELECTED AS ITSELF. No COALESCE, no `::numeric` dance that could turn a NULL
# into something else on the way out. The cast to double precision is the same one
# `app/analogs/engine.py` does at its own boundary, and it preserves NULL - which is the property
# that matters, because a NULL here is a winter navigation closure (migration 0017) and a 0 would
# be a claim that freight was free.

RATES_SQL = """
SELECT location, week_ending, horizon, pct_of_tariff::double precision, rate_month
  FROM barge_rates
 WHERE week_ending BETWEEN %(start)s AND %(end)s
   AND (%(location)s::text IS NULL OR location = %(location)s)
   AND (%(horizon)s::text IS NULL OR horizon = %(horizon)s)
 ORDER BY week_ending, location, horizon
 LIMIT %(limit)s OFFSET %(offset)s
"""

RATES_COUNT_SQL = """
SELECT count(*)
  FROM barge_rates
 WHERE week_ending BETWEEN %(start)s AND %(end)s
   AND (%(location)s::text IS NULL OR location = %(location)s)
   AND (%(horizon)s::text IS NULL OR horizon = %(horizon)s)
"""


@router.get(
    "/rates",
    response_model=models.BargeRateList,
    summary="Published weekly barge rates. A NULL rate is a closed river, not a zero.",
)
def get_rates(
    conn=Depends(get_connection),
    window: DateRange = Depends(date_range),
    bound: Page = Depends(page),
    segment: str | None = Query(
        None,
        description=(
            "Origin location, exactly as USDA publishes it (e.g. `Cairo-Memphis`, `Twin Cities`). "
            "An alias for `location`, kept because it is the name the live procedure uses."
        ),
    ),
    location: str | None = Query(
        None, description="Origin location. The column's own name; identical to `segment`."
    ),
    horizon: str | None = Query(
        None, description="`nearby`, `1_month` or `3_month`. Three different facts, not three "
        "measurements of one."
    ),
) -> models.BargeRateList:
    # Both aliases with different values is a request that contradicts itself. Preferring one
    # silently would filter on a value the caller can see they did not choose.
    if segment is not None and location is not None and segment != location:
        raise ApiError(
            INVALID_REQUEST,
            f"`segment` ({segment!r}) and `location` ({location!r}) are aliases for the same "
            f"column and disagree. Pass one.",
            status_code=422,
        )

    params = {
        "start": window.start,
        "end": window.end,
        "location": location if location is not None else segment,
        "horizon": horizon,
        "limit": bound.limit,
        "offset": bound.offset,
    }
    total = conn.execute(RATES_COUNT_SQL, params).fetchone()[0]
    rows = conn.execute(RATES_SQL, params).fetchall()

    return models.BargeRateList(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        start=window.start,
        end=window.end,
        rows=[
            models.BargeRate(
                location=row[0],
                week_ending=row[1],
                horizon=row[2],
                pct_of_tariff=row[3],
                rate_month=row[4],
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------------------------
# Lock movements.
# ---------------------------------------------------------------------------------------------
#
# ONE ROW PER (lock, week, commodity), AS PUBLISHED. No SUM, no GROUP BY, and the primary key is
# exactly this tuple - so the API's row set is the source's row set. See the module docstring for
# why aggregating is a modelling decision this layer must not make silently.
#
# `lock` is quoted because it is a keyword-shaped identifier; Postgres accepts it bare today and
# quoting costs two characters against the day it does not.

MOVEMENTS_SQL = """
SELECT "lock", week_ending, commodity, tons::double precision
  FROM lock_movements
 WHERE week_ending BETWEEN %(start)s AND %(end)s
   AND (%(lock)s::text IS NULL OR "lock" = %(lock)s)
   AND (%(commodity)s::text IS NULL OR commodity = %(commodity)s)
 ORDER BY week_ending, "lock", commodity
 LIMIT %(limit)s OFFSET %(offset)s
"""

MOVEMENTS_COUNT_SQL = """
SELECT count(*)
  FROM lock_movements
 WHERE week_ending BETWEEN %(start)s AND %(end)s
   AND (%(lock)s::text IS NULL OR "lock" = %(lock)s)
   AND (%(commodity)s::text IS NULL OR commodity = %(commodity)s)
"""


@router.get(
    "/movements",
    response_model=models.LockMovementList,
    summary="Published weekly lock movements, per commodity. Never summed, never coalesced.",
)
def get_movements(
    conn=Depends(get_connection),
    window: DateRange = Depends(date_range),
    bound: Page = Depends(page),
    lock: str | None = Query(
        None,
        description=(
            "Lock as published, including USDA's own inconsistencies - `MS Locks 27` sits beside "
            "`MS Lock 15` and both are stored verbatim (CLAUDE.md § 16)."
        ),
    ),
    commodity: str | None = Query(None, description="Commodity as published."),
) -> models.LockMovementList:
    params = {
        "start": window.start,
        "end": window.end,
        "lock": lock,
        "commodity": commodity,
        "limit": bound.limit,
        "offset": bound.offset,
    }
    total = conn.execute(MOVEMENTS_COUNT_SQL, params).fetchone()[0]
    rows = conn.execute(MOVEMENTS_SQL, params).fetchall()

    return models.LockMovementList(
        limit=bound.limit,
        offset=bound.offset,
        total=total,
        start=window.start,
        end=window.end,
        rows=[
            models.LockMovement(
                lock=row[0],
                week_ending=row[1],
                commodity=row[2],
                tons=row[3],
            )
            for row in rows
        ],
    )
