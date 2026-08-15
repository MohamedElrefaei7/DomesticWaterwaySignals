"""gauge_daily: the daily rollup, read from the view and never from the reading tables.

THE PRECEDENCE RULE HAS ONE IMPLEMENTATION AND IT IS NOT HERE
-------------------------------------------------------------
`gauge_series` (migration 0010) already decides, per site-date-parameter, whether the value comes
from the instantaneous record or the published daily one. THIS MODULE READS THAT VIEW.

Re-deriving the precedence here - a UNION with a NOT EXISTS, which is four lines and reads fine -
would be a second implementation of the same rule. Two implementations of a precedence rule diverge
SILENTLY, because each returns a plausible series and nothing compares them; that is the failure
shape CLAUDE.md § 4 names for the cadence table and § 15 names for this exact view. The view's
`source` column is carried straight through so a consumer can see the seam without re-joining.

SO WHY DOES THE SQL BELOW MENTION gauge_readings_iv AT ALL
----------------------------------------------------------
Because the view is ALREADY AGGREGATED TO ONE ROW PER DAY - it exposes the daily MEAN and nothing
else, deliberately (0010: "Daily minimum and maximum are a different series answering a different
question"). A rollup reading only the view would therefore produce value_min = value_max =
value_mean on every row in the database, and n_observations = 1 everywhere, which makes both
columns decoration.

So the sub-daily table is read for DISPERSION ONLY - min, max, and a count - and never for the
value or the source. Those two columns come from the view, always, and that is what test 4 pins.

THE JOIN CANNOT MISATTRIBUTE, AND THE REASON IS THE VIEW'S OWN NOT EXISTS
-------------------------------------------------------------------------
A dv-sourced row exists in the view only where NO instantaneous row exists for that
site-date-parameter. So the LEFT JOIN below can never attach instantaneous statistics to a
published daily mean: where it matches, the view took the iv side by construction.

Where it does not match, the row is dv-sourced and gets value_min = value_max = value_mean with
n_observations = 1. That is not a defect to be filtered out downstream, it is the honest answer -
A MINIMUM OVER ONE OBSERVATION IS THAT OBSERVATION - and `n_observations` is what lets a consumer
tell the two cases apart. Instantaneous retention is a rolling window of recent weeks at three of
the four gauges, so most of history at those sites is this case.

The `min <= mean <= max` CHECK in 0019 is the tripwire for the failure this join WOULD have if it
were wrong: attaching one day's samples to another day's mean produces three individually plausible
numbers, and only their ordering gives it away.
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

TABLE = "gauge_daily"

# THE VIEW IS NAMED HERE, ONCE, AND THE TEST GREPS FOR IT.
#
# A constant rather than an inline string so that "read the reading tables instead of the view"
# cannot be done half-way - there is one place the precedence source is named, and changing it
# changes the SQL below wholesale.
SOURCE_VIEW = "gauge_series"

ROLLUP_SQL = f"""
INSERT INTO gauge_daily
    (usgs_site_id, date, param_code, value_mean, value_min, value_max, source, n_observations)
WITH series AS (
    -- THE VALUE AND THE SOURCE. Both come from the view and neither is recomputed here.
    SELECT usgs_site_id, date, param_code, value, source
      FROM {SOURCE_VIEW}
     WHERE date >= %(start)s AND date <= %(end)s
),
iv_dispersion AS (
    -- DISPERSION ONLY. No value, no source, no precedence decision - just how far the day moved
    -- and how many samples said so. Bucketed by UTC date with the zone NAMED, exactly as the view
    -- does it, because a bare `ts::date` resolves in the session's TimeZone and would attach one
    -- day's spread to another day's mean depending on who connected.
    SELECT usgs_site_id,
           (ts AT TIME ZONE 'UTC')::date AS date,
           param_code,
           min(value)  AS value_min,
           max(value)  AS value_max,
           count(*)    AS n_observations
      FROM gauge_readings_iv
     WHERE (ts AT TIME ZONE 'UTC')::date >= %(start)s
       AND (ts AT TIME ZONE 'UTC')::date <= %(end)s
     GROUP BY 1, 2, 3
)
SELECT s.usgs_site_id,
       s.date,
       s.param_code,
       s.value,
       -- coalesce, not a CASE on s.source: the join already encodes which case this is, and a
       -- second test on `source` would be a place for the two to disagree.
       coalesce(d.value_min, s.value),
       coalesce(d.value_max, s.value),
       s.source,
       coalesce(d.n_observations, 1)
  FROM series s
  LEFT JOIN iv_dispersion d
         ON d.usgs_site_id = s.usgs_site_id
        AND d.date         = s.date
        AND d.param_code   = s.param_code
ON CONFLICT (usgs_site_id, date, param_code) DO UPDATE
    SET value_mean     = EXCLUDED.value_mean,
        value_min      = EXCLUDED.value_min,
        value_max      = EXCLUDED.value_max,
        source         = EXCLUDED.source,
        n_observations = EXCLUDED.n_observations
    -- IS DISTINCT FROM, so a rerun over an unchanged window reports 0 rather than reporting its
    -- whole input as written (CLAUDE.md § 14). This is also what makes decision 8's idempotence
    -- claim measurable rather than asserted.
    WHERE (gauge_daily.value_mean, gauge_daily.value_min, gauge_daily.value_max,
           gauge_daily.source, gauge_daily.n_observations)
       IS DISTINCT FROM
          (EXCLUDED.value_mean, EXCLUDED.value_min, EXCLUDED.value_max,
           EXCLUDED.source, EXCLUDED.n_observations)
RETURNING 1
"""


def rollup(conn, start: date, end: date) -> int:
    """Recompute gauge_daily over [start, end] inclusive. Returns rows that actually changed.

    UPSERT, NEVER TRUNCATE-AND-REBUILD (CLAUDE.md § 17). The window is bounded by the caller, so a
    defect in this SQL corrupts a window rather than emptying a table whose contents took hours to
    derive and which nothing upstream holds a second copy of.
    """
    if end < start:
        raise ValueError(
            f"rollup window ends before it starts ({start} to {end}). An inverted window selects "
            f"nothing and would report a successful rollup of zero rows."
        )

    cursor = conn.execute(ROLLUP_SQL, {"start": start, "end": end})
    written = len(cursor.fetchall())
    logger.info(
        "%s: %s to %s, %d row(s) written", TABLE, start.isoformat(), end.isoformat(), written
    )
    return written


OBSERVATIONS_SQL = """
SELECT date, {column}
  FROM gauge_daily
 WHERE usgs_site_id = %(site_id)s
   AND param_code   = %(param_code)s
 ORDER BY date
"""

# The columns a feature is allowed to read. An allowlist rather than a formatted parameter: the
# column name goes into the SQL text, so this is the boundary where an injected string would become
# an injected identifier.
READABLE_COLUMNS = frozenset({"value_mean", "value_min", "value_max"})


def observations(conn, site_id: str, param_code: str, column: str) -> list[tuple[date, float]]:
    """One site's full daily history for one parameter, in date order.

    THE FULL HISTORY, not the build window, and that is deliberate: a climatology needs every year
    it can get and a percentile threshold is a property of the site's whole record. Computing
    either from a 400-day window would make the baseline move every time the build ran.
    """
    if column not in READABLE_COLUMNS:
        raise ValueError(
            f"{column!r} is not a readable gauge_daily column. Known: {sorted(READABLE_COLUMNS)}. "
            f"This name is interpolated into SQL, so it is an allowlist rather than a parameter."
        )
    rows = conn.execute(
        OBSERVATIONS_SQL.format(column=column),
        {"site_id": site_id, "param_code": param_code},
    ).fetchall()
    return [(row[0], row[1]) for row in rows]
