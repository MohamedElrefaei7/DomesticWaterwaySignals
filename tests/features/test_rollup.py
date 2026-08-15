"""gauge_daily: the daily statistics, and the view they must come from.

The two decisions guarded here pull in opposite directions and that is why they are tested
together. The rollup must read `gauge_series` for the value and the source, so the precedence rule
has exactly one implementation - and it must read the sub-daily table for min/max/count, because
the view is already aggregated to a mean and a rollup reading only it would produce
value_min = value_max = value_mean on every row in the database.

`n_observations` is what keeps that honest. A minimum over one observation IS the mean, and
instantaneous retention is a rolling window at three of the four gauges, so most of history is that
case.
"""

from datetime import date, datetime, timezone

import pytest

from app.features import rollup
from tests.features.conftest import DISCHARGE, MEMPHIS, ST_LOUIS


def utc(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------
# Unit tier — the SQL's own text.
# ---------------------------------------------------------------------------------------------


def test_rollup_reads_the_view_not_the_reading_tables():
    """Test 4. The value and the source come from `gauge_series`, always.

    Read from the SQL text rather than from its output, because the failure this guards is a
    CORRECT-LOOKING ONE: a rollup that re-derives the precedence with its own UNION/NOT EXISTS
    returns a plausible series that agrees with the view on every row it was tested against, and
    diverges the day one of them changes. Nothing compares them, so nothing reports it
    (CLAUDE.md § 15).
    """
    assert rollup.SOURCE_VIEW == "gauge_series"
    assert "gauge_series" in rollup.ROLLUP_SQL, (
        "the rollup no longer names the view. The precedence rule then has a second "
        "implementation, and two implementations of a precedence rule diverge silently."
    )

    # The sub-daily table IS read, and that is not the same thing - it supplies dispersion only.
    # Asserted positively so this test does not pass by the rollup having stopped computing a real
    # minimum, which is the other way to satisfy "does not read the reading tables".
    assert "gauge_readings_iv" in rollup.ROLLUP_SQL, (
        "nothing reads the sub-daily record, so value_min/value_max can only be copies of the "
        "mean and n_observations can only be 1 - both columns become decoration (migration 0019)"
    )

    # The published-daily table is NOT read here: taking it directly would be re-deriving the half
    # of the precedence rule the view's NOT EXISTS decides.
    assert "gauge_readings_daily" not in rollup.ROLLUP_SQL, (
        "the rollup reads the published daily table directly, which is the precedence rule being "
        "re-derived - the view already decided which source wins for each date"
    )


def test_an_inverted_window_is_refused():
    """A window that ends before it starts selects nothing and would report a successful rollup."""

    class Unreachable:
        def execute(self, *_args, **_kwargs):  # pragma: no cover - must never be called
            raise AssertionError("the rollup issued SQL for an inverted window")

    with pytest.raises(ValueError) as excinfo:
        rollup.rollup(Unreachable(), date(2022, 10, 1), date(2022, 9, 1))
    assert "2022-10-01" in str(excinfo.value)


def test_the_observation_column_is_an_allowlist():
    """The column name is interpolated into SQL, so it is checked rather than parameterised."""

    class Unreachable:
        def execute(self, *_args, **_kwargs):  # pragma: no cover - must never be called
            raise AssertionError("a rejected column reached the database")

    with pytest.raises(ValueError):
        rollup.observations(Unreachable(), ST_LOUIS, DISCHARGE, "value_mean; DROP TABLE features")
    assert rollup.READABLE_COLUMNS == {"value_mean", "value_min", "value_max"}


# ---------------------------------------------------------------------------------------------
# Integration tier — the real SQL over a hand-built day.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_daily_min_mean_max_from_a_hand_built_day(migrated_db, seed_readings):
    """Test 1. Four readings, three hand-computed statistics.

    Hand-computed rather than compared against another query: `min`, `avg` and `max` over the same
    group is what the SQL is FOR, so a test that recomputed them in SQL would assert the database
    agrees with itself.

        200000, 100000, 150000, 250000  ->  min 100000, mean 175000, max 250000
    """
    seed_readings.instantaneous(
        ST_LOUIS,
        [
            (utc(2022, 10, 4, 0), 200000.0),
            (utc(2022, 10, 4, 6), 100000.0),
            (utc(2022, 10, 4, 12), 150000.0),
            (utc(2022, 10, 4, 18), 250000.0),
        ],
    )

    rollup.rollup(migrated_db, date(2022, 10, 4), date(2022, 10, 4))
    migrated_db.commit()

    row = migrated_db.execute(
        "SELECT value_mean, value_min, value_max, n_observations, source"
        "  FROM gauge_daily WHERE usgs_site_id = %s AND date = %s",
        (ST_LOUIS, date(2022, 10, 4)),
    ).fetchone()

    assert row is not None, "the hand-built day produced no gauge_daily row"
    value_mean, value_min, value_max, n_observations, source = row

    assert value_mean == pytest.approx(175000.0), f"mean is {value_mean}, hand-computed 175000"
    assert value_min == pytest.approx(100000.0), (
        f"minimum is {value_min}, hand-computed 100000. If it equals the mean, the rollup is "
        f"reading only the view - which is already aggregated to a mean (migration 0019)."
    )
    assert value_max == pytest.approx(250000.0), f"maximum is {value_max}, hand-computed 250000"
    assert n_observations == 4
    assert source == "iv"


@pytest.mark.integration
def test_n_observations_is_populated_and_equals_the_input_count(migrated_db, seed_readings):
    """Test 2. Populated on EVERY row, and equal to what produced the statistics.

    Without it, `value_min` is a column whose meaning changes silently partway through every site's
    history - a real minimum over 96 samples for recent weeks, a published daily mean for the
    decades before. A feature reading it would draw conclusions about "the minimum" that are
    conclusions about the mean with a more alarming name.
    """
    seed_readings.instantaneous(
        ST_LOUIS,
        [(utc(2022, 10, 5, hour), 150000.0 + hour) for hour in range(6)],
    )
    seed_readings.daily(MEMPHIS, [(date(2022, 10, 5), 300000.0)])

    rollup.rollup(migrated_db, date(2022, 10, 5), date(2022, 10, 5))
    migrated_db.commit()

    rows = dict(
        migrated_db.execute(
            "SELECT usgs_site_id, n_observations FROM gauge_daily WHERE date = %s",
            (date(2022, 10, 5),),
        ).fetchall()
    )

    assert rows == {ST_LOUIS: 6, MEMPHIS: 1}, (
        f"n_observations is {rows}; expected 6 from the six instantaneous samples and 1 from the "
        f"single published daily mean"
    )

    # NOT NULL is in the schema; asserted here too because the schema is what a mutation would
    # remove and this is the assertion that would notice.
    nulls = migrated_db.execute(
        "SELECT count(*) FROM gauge_daily WHERE n_observations IS NULL"
    ).fetchone()[0]
    assert nulls == 0


@pytest.mark.integration
def test_a_single_observation_day_yields_min_equal_to_mean(migrated_db, seed_readings):
    """Test 3. Decision 1's caveat, made visible rather than left in a comment.

    A published daily mean is ONE observation, so its minimum is itself. This is not a defect to be
    filtered out downstream - it is the honest answer - and the only reason it is safe is that
    `n_observations` says so on the same row.
    """
    seed_readings.daily(MEMPHIS, [(date(2015, 6, 6), 412000.0)])

    rollup.rollup(migrated_db, date(2015, 6, 6), date(2015, 6, 6))
    migrated_db.commit()

    value_mean, value_min, value_max, n_observations = migrated_db.execute(
        "SELECT value_mean, value_min, value_max, n_observations FROM gauge_daily"
        " WHERE usgs_site_id = %s AND date = %s",
        (MEMPHIS, date(2015, 6, 6)),
    ).fetchone()

    assert (value_min, value_mean, value_max) == (412000.0, 412000.0, 412000.0)
    assert n_observations == 1, (
        "n_observations is not 1, so nothing on this row says its 'minimum' is really a published "
        "mean - which is what makes the equality above safe rather than misleading"
    )


@pytest.mark.integration
def test_source_is_carried_through_from_the_view(migrated_db, seed_readings):
    """Test 5. `source` comes from the view unchanged, so the seam stays visible.

    The two reading tables are NOT the same measurement - different day boundaries (UTC here,
    site-local at USGS) and different sampling - so a series that switches source mid-history has a
    seam. Dropping this column, or recomputing it from `n_observations`, would average the seam
    into invisibility exactly where three of the four gauges cross it.
    """
    # Two sites on one date: one covered instantaneously, one only by a published daily value.
    seed_readings.instantaneous(ST_LOUIS, [(utc(2022, 10, 6, 0), 190000.0)])
    seed_readings.daily(MEMPHIS, [(date(2022, 10, 6), 210000.0)])
    # And a date where St. Louis has BOTH - the view's precedence must pick iv, and gauge_daily
    # must say so.
    seed_readings.instantaneous(ST_LOUIS, [(utc(2022, 10, 7, 0), 191000.0)])
    seed_readings.daily(ST_LOUIS, [(date(2022, 10, 7), 999999.0)])

    rollup.rollup(migrated_db, date(2022, 10, 6), date(2022, 10, 7))
    migrated_db.commit()

    rows = {
        (site, day): (source, value)
        for site, day, source, value in migrated_db.execute(
            "SELECT usgs_site_id, date, source, value_mean FROM gauge_daily ORDER BY 1, 2"
        ).fetchall()
    }

    assert rows[(ST_LOUIS, date(2022, 10, 6))][0] == "iv"
    assert rows[(MEMPHIS, date(2022, 10, 6))][0] == "dv"

    source, value = rows[(ST_LOUIS, date(2022, 10, 7))]
    assert source == "iv", (
        f"the day covered by both sources is marked {source!r}; the view prefers instantaneous "
        f"and gauge_daily must carry that decision rather than making its own"
    )
    assert value == pytest.approx(191000.0), (
        "the published daily value won on a date the instantaneous record covers - the precedence "
        "rule has been re-derived here and it disagrees with the view"
    )
