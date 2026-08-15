"""The grid, and the duplicate feature pair the sweep must not scan twice. DEBT 1b.

Every test here is a unit test against hand-built inputs, because `pairs.build_grid` is pure and
the grid size it produces IS the multiple-comparisons denominator. A test that read the denominator
back out of a sweep's own output would be asserting that the code counts what the code counted.
"""

import re
from pathlib import Path

import pytest

from app.features import registry
from app.signals import pairs, regimes

from tests.signals.conftest import BATON_ROUGE, DISCHARGE, MEMPHIS, ST_LOUIS, VICKSBURG

SITES = [ST_LOUIS, MEMPHIS, VICKSBURG, BATON_ROUGE]
LAGS = pairs.lag_range(-2, 2)
HORIZONS = (7, 14, 21)


def test_duplicate_feature_pairs_are_skipped_with_a_stated_reason():
    """Test 1, DEBT 1b. One series under two names is scanned ONCE, and the skip says why.

    Phase 5's finding 3: wherever `gauge_daily.n_observations = 1`, the row came from a published
    daily mean and `value_min` IS that mean (migration 0019). `discharge_min` and `discharge_mean`
    are then the same series, and scanning both would test one variable twice.

    THE COST OF NOT SKIPPING IS NOT MERELY A BIGGER GRID. The duplicate is PERFECTLY correlated with
    its twin, so it produces a near-identical q-value and lands beside it in any q-ordered listing -
    where two rows saying one thing read as two independent lines of evidence. That is the failure
    this skip exists to prevent, and it is worse than the wasted computation.
    """
    degenerate = {(MEMPHIS, DISCHARGE)}

    grid = pairs.build_grid(
        sites=[MEMPHIS, ST_LOUIS],
        degenerate_params=degenerate,
        series_columns={},
        lags=LAGS,
        horizons=HORIZONS,
    )

    scanned_at_memphis = {p.feature_name for p in grid.pairs if p.site_id == MEMPHIS}
    scanned_at_st_louis = {p.feature_name for p in grid.pairs if p.site_id == ST_LOUIS}

    assert "discharge_min" not in scanned_at_memphis, (
        "discharge_min was scanned at a site where every gauge_daily row reports one observation, "
        "so it is the same series as discharge_mean and the sweep tested one variable twice"
    )
    assert "discharge_mean" in scanned_at_memphis, (
        "the duplicate pair was collapsed to nothing rather than to one - the variable is not "
        "scanned at all at this site now, which is worse than scanning it twice"
    )

    # AND THE OTHER SITE IS UNTOUCHED. A skip that applied everywhere would be a hardcoded rule
    # wearing a measurement's clothes.
    assert {"discharge_min", "discharge_mean"} <= scanned_at_st_louis, (
        f"a non-degenerate site lost a feature: {sorted(scanned_at_st_louis)}"
    )

    # THE REASON IS STATED, AND IT NAMES THE MECHANISM rather than merely announcing the skip. A
    # pair that vanishes with no explanation is indistinguishable from one nobody thought of.
    skips = [s for s in grid.skipped if s.site_id == MEMPHIS]
    assert len(skips) == 1, f"expected exactly one skip at the degenerate site, got {skips}"
    reason = skips[0].reason
    assert skips[0].feature_name == "discharge_min"
    assert "n_observations = 1" in reason, f"the reason does not name the evidence: {reason}"
    assert "discharge_mean" in reason, f"the reason does not name what it duplicates: {reason}"
    assert not any(s.site_id == ST_LOUIS for s in grid.skipped)


def test_duplication_is_detected_from_n_observations_not_a_site_list():
    """Test 2. The detection reads the data. A hardcoded site list would be wrong, silently.

    Phase 5 measured the duplication at two of the four gauges entirely and at 95% of a third. THE
    TEMPTING IMPLEMENTATION IS TO WRITE THOSE TWO DOWN. It would be correct today and wrong the day
    the instantaneous backfill fills the third site in, or the day a fifth gauge is seeded - and
    wrong in the direction nothing reports: the sweep would keep skipping a pair that had stopped
    being a duplicate, and the missing rows would read as a smaller grid rather than as a bug.

    Asserted by reading the module, because that is the only form of this guard that catches the
    list being added back later. A behavioural test cannot distinguish "measured the data" from
    "looked up the right answer" on a database whose sites match the list.
    """
    source = Path(pairs.__file__).read_text(encoding="utf-8")

    for site_id in SITES:
        assert site_id not in source, (
            f"app/signals/pairs.py contains the site id {site_id!r} as a literal. The duplication "
            f"rule must be measured from n_observations, not looked up from a list of sites that "
            f"happened to be degenerate when somebody last checked."
        )

    # And no OTHER eight-digit USGS site id either - the four above are today's gauges, and a fifth
    # hardcoded one would pass the loop above while being exactly the same mistake.
    stray = re.findall(r"(?<![\d.])\d{8}(?![\d.])", source)
    assert not stray, f"app/signals/pairs.py contains site-id-shaped literal(s): {stray}"

    # THE POSITIVE HALF: the detection really is the aggregate over n_observations. Without this,
    # deleting the query and returning an empty set would pass everything above.
    assert "bool_and(n_observations = 1)" in pairs.DEGENERATE_PARAMS_SQL, (
        f"the degeneracy query no longer aggregates over n_observations:\n"
        f"{pairs.DEGENERATE_PARAMS_SQL}"
    )
    assert "GROUP BY" in pairs.DEGENERATE_PARAMS_SQL

    # And the rule is genuinely a function of the passed-in set: the same site is skipped or not
    # depending only on what was measured.
    assert pairs.skips_for_site(MEMPHIS, set()) == {}
    assert set(pairs.skips_for_site(MEMPHIS, {(MEMPHIS, DISCHARGE)})) == {"discharge_min"}
    assert pairs.skips_for_site(VICKSBURG, {(MEMPHIS, DISCHARGE)}) == {}


def test_the_grid_size_equals_the_number_of_pairs_scanned():
    """Test 3. The denominator is the pair count, and it is arithmetic a reader can check.

    `Grid.size` is written onto every `signals` row and onto the run, and the sweep refuses to
    finish if it wrote a different number of rows. So this is the one place the expected count is
    computed by hand rather than by the code under test.
    """
    grid = pairs.build_grid(
        sites=SITES,
        degenerate_params=set(),
        series_columns={},
        lags=LAGS,
        horizons=HORIZONS,
        regimes=regimes.REGIMES,
    )

    expected = (
        len(registry.REGISTRY) * len(SITES) * len(HORIZONS) * len(LAGS) * len(regimes.REGIMES)
    )
    assert grid.size == expected, (
        f"the grid holds {grid.size} pairs; {len(registry.REGISTRY)} features x {len(SITES)} sites "
        f"x {len(HORIZONS)} horizons x {len(LAGS)} lags x {len(regimes.REGIMES)} regimes is "
        f"{expected}"
    )
    assert grid.size == len(grid.pairs), (
        "Grid.size disagrees with the pairs it reports - a separately-tracked count is a count "
        "that can drift from the collection it describes"
    )

    # EVERY PAIR IS DISTINCT. A duplicate would inflate the denominator without adding a test, and
    # would collide on the signals primary key at write time - which is a crash rather than a wrong
    # number, but only after the whole scan has run.
    keys = {
        (p.feature_name, p.site_id, p.target_name, p.horizon_days, p.lag_days, p.regime)
        for p in grid.pairs
    }
    assert len(keys) == grid.size, f"{grid.size - len(keys)} duplicate pair(s) in the grid"

    # And a skip reduces it by exactly the pairs it removes: one feature at one site, across every
    # horizon, lag and regime.
    with_skip = pairs.build_grid(
        sites=SITES,
        degenerate_params={(MEMPHIS, DISCHARGE)},
        series_columns={},
        lags=LAGS,
        horizons=HORIZONS,
        regimes=regimes.REGIMES,
    )
    per_feature_site = len(HORIZONS) * len(LAGS) * len(regimes.REGIMES)
    assert with_skip.size == expected - per_feature_site


def test_negative_and_positive_lags_are_enumerated_symmetrically():
    """Not in the brief's list, and it is here because the lag range is where a scan gets narrowed.

    `lag_range` is three lines and it is the only thing standing between this project and a sweep
    that cannot observe the case CONTEXT.md says it saw in both 2022 and 2023.
    """
    lags = pairs.lag_range(-21, 21)
    assert len(lags) == 43
    assert min(lags) == -21 and max(lags) == 21
    assert sum(1 for lag in lags if lag < 0) == sum(1 for lag in lags if lag > 0) == 21
    assert 0 in lags

    with pytest.raises(ValueError, match="ends before it starts"):
        pairs.lag_range(21, -21)


def test_the_feature_filter_is_a_glob_and_is_recorded_verbatim():
    """Also not in the brief's list. CONTEXT.md's instruction for this phase is "point the scan at
    `days_below_*` first", so the filter is a family rather than a name, and a filtered run's
    q-values are adjusted across a smaller grid - which is why `signal_runs.feature_filter` stores
    it and why a filtered run is not comparable to a full one.
    """
    grid = pairs.build_grid(
        sites=[ST_LOUIS],
        degenerate_params=set(),
        series_columns={},
        lags=(0,),
        horizons=(7,),
        regimes=("all",),
        feature_filter="days_below_*",
    )
    names = {p.feature_name for p in grid.pairs}
    assert names == {"days_below_p05", "days_below_p10", "days_below_p20"}, names

    assert pairs.matches_filter("discharge_mean", None) is True
    assert pairs.matches_filter("discharge_mean", "days_below_*") is False


def test_the_series_column_is_carried_onto_the_pair_from_the_measurement():
    """The column each pair is scanned on rides on the pair, decided from data, defaulting to value.

    Not in the brief's list. It is here because `series_column` is the one field of a `signals` row
    that describes WHAT WAS CORRELATED rather than what was correlated against - a row measured on
    a raw seasonal level and a row measured on a deseasonalized anomaly are different measurements,
    and Phase 5's finding 1 is precisely that the difference changes the answer.
    """
    measured = {("discharge_mean", ST_LOUIS): "anomaly"}
    grid = pairs.build_grid(
        sites=[ST_LOUIS],
        degenerate_params=set(),
        series_columns=measured,
        lags=(0,),
        horizons=(7,),
        regimes=("all",),
    )
    by_name = {p.feature_name: p.series_column for p in grid.pairs}

    assert by_name["discharge_mean"] == "anomaly"
    # A run-length feature has no anomaly by construction, and an unmeasured pair falls back to the
    # raw value rather than to an anomaly column that holds nothing.
    assert by_name["days_below_p10"] == "value"
