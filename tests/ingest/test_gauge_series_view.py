"""Integration tier — the gauge_series view. Requires DATABASE_URL and a real TimescaleDB.

Covers migration 0010 and CLAUDE.md § 15's precedence bullet: where two sources cover the same
fact, precedence is encoded ONCE, in the database, and the view says which source each row came
from.

Every assertion here runs against the real view rather than against a reimplementation of its
rule in Python. A test that rebuilt the precedence logic to check the precedence logic would be
the second copy the view exists to prevent.
"""

from datetime import date, datetime, timezone

import pytest

from app.ingest import usgs_daily_ingest, usgs_ingest
from app.ingest.usgs_client import PARAM_DISCHARGE, Reading
from app.ingest.usgs_daily_client import STAT_MEAN, DailyReading

pytestmark = pytest.mark.integration

SITE = "07010000"
OTHER = "07032000"
DAY = date(2022, 10, 1)


def seed_iv(conn, day: date, values, site=SITE):
    """Instantaneous readings across `day`, one per element of `values`."""
    readings = [
        Reading(
            usgs_site_id=site,
            ts=datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc),
            param_code=PARAM_DISCHARGE,
            value=value,
            qualifiers=("P",),
        )
        for hour, value in enumerate(values)
    ]
    usgs_ingest.upsert_readings(conn, readings)
    conn.commit()


def seed_dv(conn, day: date, value, site=SITE, stat=STAT_MEAN):
    usgs_daily_ingest.upsert_daily_readings(
        conn,
        [
            DailyReading(
                usgs_site_id=site,
                date=day,
                param_code=PARAM_DISCHARGE,
                stat_cd=stat,
                value=value,
                qualifiers=("A",),
            )
        ],
    )
    conn.commit()


def series(conn, site=None):
    sql = "SELECT usgs_site_id, date, param_code, value, source FROM gauge_series"
    params = ()
    if site is not None:
        sql += " WHERE usgs_site_id = %s"
        params = (site,)
    sql += " ORDER BY usgs_site_id, date, param_code"
    cur = conn.execute(sql, params)
    columns = [d.name for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def test_view_prefers_iv_where_both_exist(migrated_db):
    """Both sources cover the day: the IV-derived mean wins and `source` reads 'iv'.

    Instantaneous is preferred because it is the finer measurement - where it covers a day, the
    daily mean comes from the sub-daily record this project actually holds and can recompute.
    The two seeded values are deliberately far apart so a view returning the wrong one cannot be
    mistaken for a rounding difference.
    """
    seed_iv(migrated_db, DAY, [100.0, 200.0, 300.0])  # mean 200
    seed_dv(migrated_db, DAY, 999999.0)               # nothing like it

    rows = series(migrated_db, SITE)

    assert len(rows) == 1, f"expected one row for one site-date-param, got {rows}"
    assert rows[0]["source"] == "iv", (
        f"source is {rows[0]['source']!r}: the view preferred the published daily value over the "
        f"finer instantaneous record"
    )
    assert rows[0]["value"] == pytest.approx(200.0), (
        f"value is {rows[0]['value']}, not the mean of the instantaneous readings"
    )


def test_view_falls_back_to_dv_where_iv_is_absent(migrated_db):
    """No instantaneous data for the day: the published daily value is used, `source` reads 'dv'.

    This is MOST OF HISTORY at three of the four sites - instantaneous retention is a rolling
    window, so for anything older than a couple of months the daily record is the only answer
    there is. A view that only exposed IV would return an empty series for 2015 Memphis and
    nothing would report a problem.
    """
    seed_dv(migrated_db, DAY, 121000.0, site=OTHER)

    rows = series(migrated_db, OTHER)

    assert len(rows) == 1
    assert rows[0]["source"] == "dv", f"source is {rows[0]['source']!r}, expected 'dv'"
    assert rows[0]["value"] == pytest.approx(121000.0)
    assert rows[0]["date"] == DAY


def test_view_exposes_exactly_one_row_per_site_date_param(migrated_db):
    """No duplicates. Counted, not eyeballed.

    THE FAILURE THIS CATCHES IS SILENT: a view that emits one row per SOURCE instead of one per
    (site, date, param) raises nothing, returns plausible data, and gives every date covered by
    both sources double weight in any average computed over the series. Nothing downstream can
    detect it - the values are all real.
    """
    # Three days at one site, all covered by BOTH sources; one day at another site covered only
    # by the daily record.
    for offset, value in enumerate((100.0, 110.0, 120.0)):
        day = date(2022, 10, 1 + offset)
        seed_iv(migrated_db, day, [value, value + 10])
        seed_dv(migrated_db, day, value + 500)
    seed_dv(migrated_db, DAY, 121000.0, site=OTHER)

    rows = series(migrated_db)

    keys = [(r["usgs_site_id"], r["date"], r["param_code"]) for r in rows]
    assert len(keys) == len(set(keys)), (
        f"the view returned {len(keys)} rows for {len(set(keys))} distinct "
        f"(site, date, param) keys - it is emitting one row per source"
    )
    assert len(rows) == 4, f"expected 4 rows (3 overlapping days + 1 dv-only), got {len(rows)}"

    # And the duplicates a broken view would produce are exactly the overlapping days, so assert
    # the overlap really exists - otherwise the check above passes vacuously.
    duplicated = migrated_db.execute(
        "SELECT count(*) FROM gauge_readings_daily d"
        " WHERE EXISTS (SELECT 1 FROM gauge_readings_iv i"
        "                WHERE i.usgs_site_id = d.usgs_site_id"
        "                  AND (i.ts AT TIME ZONE 'UTC')::date = d.date"
        "                  AND i.param_code = d.param_code)"
    ).fetchone()[0]
    assert duplicated == 3, (
        f"only {duplicated} daily rows overlap an instantaneous day; this test cannot detect a "
        f"one-row-per-source view without overlap"
    )


def test_view_source_column_is_never_null(migrated_db):
    """Every row says where it came from.

    The `source` column is the whole reason this view is honest rather than merely convenient.
    The two sources are NOT the same measurement - USGS computes its daily mean over a calendar
    day in the site's LOCAL time while the view buckets instantaneous data by UTC date, and the
    sampling differs too - so a series that switches source mid-history has a seam at the switch.

    Exposing `source` makes that seam visible to anything consuming the series. A NULL, or a
    dropped column, hides it and turns a known limitation into an invisible one.
    """
    seed_iv(migrated_db, DAY, [100.0, 200.0])
    seed_dv(migrated_db, date(2015, 6, 1), 90000.0, site=OTHER)
    seed_dv(migrated_db, DAY, 999.0)

    rows = series(migrated_db)

    assert rows, "the view returned nothing; every assertion below would be vacuous"
    assert all(r["source"] is not None for r in rows), (
        f"rows with a NULL source: {[r for r in rows if r['source'] is None]}"
    )
    assert {r["source"] for r in rows} == {"iv", "dv"}, (
        f"expected both sources to appear, got {{r['source'] for r in rows}}"
    )
    assert all(r["source"] in ("iv", "dv") for r in rows)

    # The column is genuinely part of the view's shape, not something the query above invented.
    columns = {
        row[0]
        for row in migrated_db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'gauge_series'"
        ).fetchall()
    }
    assert columns == {"usgs_site_id", "date", "param_code", "value", "source"}, (
        f"gauge_series exposes {sorted(columns)}; the source column is what makes the seam "
        f"between the two records visible (CLAUDE.md § 15)"
    )


def test_view_ignores_daily_statistics_other_than_the_mean(migrated_db):
    """A daily minimum in the table does not become a second row in the series.

    Not in the brief's numbered list, and here because `stat_cd` is in the daily table's key
    precisely so minimum and maximum can land later - at which point a view that did not filter
    would silently triple the row count per date. Written now, while it can be asserted cheaply,
    rather than discovered when the minimum series arrives.
    """
    seed_dv(migrated_db, DAY, 121000.0, site=OTHER, stat=STAT_MEAN)
    seed_dv(migrated_db, DAY, 98000.0, site=OTHER, stat="00001")

    rows = series(migrated_db, OTHER)

    assert len(rows) == 1, (
        f"the daily minimum produced a second row in the series: {rows}. gauge_series is the "
        f"MEAN series; other statistics are addressed on gauge_readings_daily directly."
    )
    assert rows[0]["value"] == pytest.approx(121000.0)
