"""The grid: every combination the sweep will scan, and the ones it refuses to scan twice.

PURE FUNCTIONS PLUS THREE READ-ONLY QUERIES. The queries answer questions only the database can
answer - which sites exist, which of them have a degenerate observation count, which features
carry an anomaly - and everything that decides what the grid contains is pure and hand-testable.

THE GRID SIZE IS THE DENOMINATOR, AND IT IS COMPUTED HERE
----------------------------------------------------------
`Grid.size` is the number this phase's honesty rests on. It is written onto every `signals` row
and onto the run, and the sweep asserts that it wrote exactly that many rows. A grid that quietly
shrank between enumeration and writing would produce a smaller denominator and a better-looking
passing fraction, which is the failure this whole module is arranged to make impossible: THE GRID
IS BUILT ONCE, UP FRONT, AND NOT DISCOVERED AS THE SCAN PROCEEDS.

DEBT 1b - THE DUPLICATE FEATURE PAIR, DETECTED FROM THE DATA
-------------------------------------------------------------
Phase 5's finding 3: wherever `gauge_daily.n_observations = 1`, the row came from a published daily
mean and `value_min` IS that mean (migration 0019). At those sites `discharge_min` and
`discharge_mean` are ONE SERIES UNDER TWO NAMES, and a sweep treating them as independent inputs
scans one variable twice - inflating the grid, and inflating it with a perfectly correlated pair
that will produce two nearly identical rows near the top of any q-ordered listing. Two rows saying
the same thing read as corroboration.

Phase 5 measured this at two of the four gauges entirely and at 95% of a third. THE TWO ARE NOT
NAMED IN THIS MODULE, and that is the decision rather than an omission: a hardcoded site list would
be correct today and wrong the day the instantaneous backfill fills that third site in, or the day
a fifth gauge is seeded. It would also be wrong SILENTLY - the sweep would keep skipping a pair that
had stopped being a duplicate, and the missing rows would look like a smaller grid rather than like
a bug. `bool_and(n_observations = 1)` asks the data instead, every run.

THE SKIP IS REPORTED, NOT PERFORMED QUIETLY
--------------------------------------------
A pair that vanishes from a grid with no explanation is indistinguishable from a pair nobody
thought of. Every skip carries a stated reason, the sweep prints them, and the reason names the
mechanism - so the reader can check the claim rather than take it.

WHICH OF THE TWO IS KEPT IS DELIBERATELY ARBITRARY, and it has to be: at a degenerate site THE TWO
SERIES ARE EQUAL, so there is no better one to choose. The rule is "keep the alphabetically first
name" - stable across runs, dependent on nothing about the river, and stated so that nobody later
reads a preference into it. What matters is that the variable is scanned once and that the skip is
visible; which name it is scanned under is bookkeeping, and `signals.feature_name` records it.
"""

from __future__ import annotations

import fnmatch
import functools
from dataclasses import dataclass

from app.features import registry, targets as targets_module
from app.signals import regimes as regimes_module

# The lag range this phase scans, in days, inclusive. ±21 - three weeks either side.
#
# NEGATIVE LAGS ARE HALF THE EXPERIMENT, not a symmetric flourish. `CONTEXT.md` records the rate
# peaking two to three weeks BEFORE discharge bottomed in both 2022 and 2023, which is the
# "operators price the published river forecast" case. A scan restricted to non-negative lags
# cannot observe it, and would report its absence as evidence for the physical-lead story.
DEFAULT_LAG_MIN = -21
DEFAULT_LAG_MAX = 21

# 1-day steps. The rate series is weekly, so most of these lags land on the same feature date as
# their neighbours - which is exactly why the tests are correlated, and exactly why Benjamini-
# Hochberg rather than Bonferroni (statistics.py). Stepping by 7 instead would hide that fact by
# making the grid smaller rather than by making the tests independent.
LAG_STEP_DAYS = 1


@dataclass(frozen=True)
class Pair:
    """One combination to scan. The primary key of a `signals` row, plus the column to read.

    `series_column` rides along because it is a property of the (feature, site) pair rather than of
    the feature alone, it is decided from the data, and it is recorded on the output row - so it is
    resolved once here rather than re-derived at write time where it could disagree.
    """

    feature_name: str
    site_id: str
    series_column: str
    target_name: str
    horizon_days: int
    lag_days: int
    regime: str


@dataclass(frozen=True)
class Skip:
    """A (feature, site) the grid deliberately does not contain, and why.

    The reason is a full sentence rather than a code, because its audience is a human reading a
    sweep's output and deciding whether the skip was right.
    """

    feature_name: str
    site_id: str
    reason: str


@dataclass(frozen=True)
class Grid:
    """Every pair to scan, and every (feature, site) skipped.

    `size` is the multiple-comparisons denominator. It is deliberately a property of the pair tuple
    rather than a separately-tracked integer: a count that is maintained alongside a collection is
    a count that can disagree with it.
    """

    pairs: tuple[Pair, ...]
    skipped: tuple[Skip, ...]

    @property
    def size(self) -> int:
        return len(self.pairs)


# ---------------------------------------------------------------------------------------------
# Debt 1b: which registry features collapse into one series where a site's rows are degenerate.
# ---------------------------------------------------------------------------------------------


def _builder_identity(builder) -> tuple:
    """A hashable identity for a registry builder, seeing through `functools.partial`.

    The registry binds a builder's parameters with `partial` rather than selecting one by a branch
    on the feature name (registry.py), so the three threshold features share a function and differ
    in one bound keyword. `partial` defines no `__eq__`, so two partials with identical arguments
    are unequal objects - grouping on the object itself would put every threshold feature in its
    own group and find no duplicates anywhere, which is a green test and a silently useless guard.
    """
    if isinstance(builder, functools.partial):
        return (builder.func, builder.args, tuple(sorted(builder.keywords.items())))
    return (builder, (), ())


def collapsing_groups(features=registry.REGISTRY) -> tuple[tuple, ...]:
    """Registry features that become ONE SERIES at a site whose rows all have n_observations = 1.

    Two features collapse when they compute the same thing from the same parameter and differ only
    in WHICH gauge_daily statistic they read - because at such a site `value_min`, `value_max` and
    `value_mean` are the same number by construction (migration 0019: the row came from a published
    daily mean, and a minimum over one observation is that observation).

    Grouped by `(param_code, builder identity)` and required to span more than one source column.
    That last condition is what keeps this from being vacuous in the other direction: two features
    sharing a builder, a parameter AND a column would be duplicates EVERYWHERE, which is a registry
    error rather than a site-specific collapse, and registry.py's own duplicate-name guard is where
    that is caught.

    Each group is returned sorted by feature name, which is what makes the retained feature stable.
    """
    groups: dict[tuple, list] = {}
    for feature in features:
        key = (feature.param_code, _builder_identity(feature.builder))
        groups.setdefault(key, []).append(feature)

    return tuple(
        tuple(sorted(group, key=lambda f: f.name))
        for group in groups.values()
        if len({f.source_column for f in group}) > 1
    )


def skips_for_site(
    site_id: str, degenerate_params: set, features=registry.REGISTRY
) -> dict[str, Skip]:
    """`{feature_name: Skip}` for the features this site must not be scanned twice on.

    `degenerate_params` is the set of `(site_id, param_code)` whose every `gauge_daily` row reports
    a single observation - MEASURED, by `degenerate_site_params` below. Passed in rather than
    queried here so this function is pure and the duplication rule can be tested against a
    hand-built set.
    """
    skipped: dict[str, Skip] = {}
    for group in collapsing_groups(features):
        param_code = group[0].param_code
        if (site_id, param_code) not in degenerate_params:
            continue

        kept, *duplicates = group
        for feature in duplicates:
            skipped[feature.name] = Skip(
                feature_name=feature.name,
                site_id=site_id,
                reason=(
                    f"identical to {kept.name!r} at this site and SCANNED ONCE UNDER THAT NAME. "
                    f"Every gauge_daily row here for parameter {param_code} reports "
                    f"n_observations = 1, so the row came from a published daily mean and "
                    f"{feature.source_column} IS {kept.source_column} (migration 0019) - the two "
                    f"features are one series under two names. Scanning both would test one "
                    f"variable twice and put two perfectly correlated rows near the top of any "
                    f"q-ordered listing, where they would read as corroboration. Detected from "
                    f"bool_and(n_observations = 1) on this run's data, NOT from a site list: a "
                    f"list would keep skipping this pair on the day it stopped being a duplicate."
                ),
            )
    return skipped


# ---------------------------------------------------------------------------------------------
# The grid itself. Pure.
# ---------------------------------------------------------------------------------------------


def lag_range(lag_min: int = DEFAULT_LAG_MIN, lag_max: int = DEFAULT_LAG_MAX) -> tuple[int, ...]:
    """The lags to scan, inclusive of both ends.

    Refuses an inverted range rather than returning an empty tuple, because an empty lag range
    produces an empty grid, an empty grid produces a sweep that writes nothing, and a sweep that
    writes nothing exits zero and looks like a sweep that found nothing.
    """
    if lag_max < lag_min:
        raise ValueError(
            f"lag range ends before it starts ({lag_min} to {lag_max}). An inverted range scans "
            f"nothing and would report a completed sweep over an empty grid."
        )
    return tuple(range(lag_min, lag_max + 1, LAG_STEP_DAYS))


def matches_filter(feature_name: str, feature_filter: str | None) -> bool:
    """Glob match, or True when no filter is set.

    A glob rather than an exact name because the instruction this phase starts from is "point the
    scan at `days_below_*` first" - a family, not a feature. `fnmatchcase` rather than `fnmatch` so
    the match does not silently depend on the platform's filesystem case rules.
    """
    if feature_filter is None:
        return True
    return fnmatch.fnmatchcase(feature_name, feature_filter)


def build_grid(
    *,
    sites,
    degenerate_params,
    series_columns,
    lags,
    horizons=targets_module.HORIZON_DAYS,
    regimes=regimes_module.REGIMES,
    target_name: str = targets_module.TARGET_NAME,
    feature_filter: str | None = None,
    features=registry.REGISTRY,
) -> Grid:
    """Enumerate the grid. PURE - every input is a value, so the denominator is hand-checkable.

    `series_columns` maps `(feature_name, site_id)` to the `features` column to correlate, measured
    by `series_columns_by_pair` below. A pair absent from it reads `value`: a feature with no rows
    at that site has no anomaly to read, and the pair will be recorded as
    `insufficient_observations` rather than silently dropped.

    THE ITERATION ORDER IS FIXED - registry order, then sorted sites, then horizons, lags, regimes -
    so two runs over the same grid enumerate it identically. Not for aesthetics: Benjamini-Hochberg
    sorts by p-value and breaks ties by position, so an unstable enumeration order would make
    q-values differ between two runs that scanned exactly the same thing.
    """
    pairs: list[Pair] = []
    skips: list[Skip] = []

    for site_id in sorted(sites):
        site_skips = skips_for_site(site_id, degenerate_params, features)
        for feature in features:
            if not matches_filter(feature.name, feature_filter):
                continue
            if feature.name in site_skips:
                skips.append(site_skips[feature.name])
                continue

            column = series_columns.get((feature.name, site_id), "value")
            for horizon in horizons:
                for lag in lags:
                    for regime in regimes:
                        pairs.append(
                            Pair(
                                feature_name=feature.name,
                                site_id=site_id,
                                series_column=column,
                                target_name=target_name,
                                horizon_days=horizon,
                                lag_days=lag,
                                regime=regime,
                            )
                        )

    return Grid(pairs=tuple(pairs), skipped=tuple(skips))


# ---------------------------------------------------------------------------------------------
# The three read-only queries. Everything the database knows that the grid needs.
# ---------------------------------------------------------------------------------------------

SITES_SQL = "SELECT usgs_site_id FROM gauges ORDER BY usgs_site_id"

# THE DUPLICATION TEST, AND IT IS A MEASUREMENT RATHER THAN A LOOKUP.
#
# `bool_and(n_observations = 1)` is true only where EVERY row for that site and parameter came from
# a published daily mean. One genuine sub-daily day anywhere in the record makes it false, and the
# pair is scanned as two features again - which is the correct answer, because from that day on the
# two series differ.
#
# Deliberately NOT `bool_and(value_min = value_mean)`: that is the same condition measured on the
# values rather than on the evidence, and it would also be true of a site whose river happened to
# be flat. `n_observations` is the column that exists to make this distinction visible (0019), and
# reading it is what makes the skip's stated reason checkable.
DEGENERATE_PARAMS_SQL = """
SELECT usgs_site_id, param_code
  FROM gauge_daily
 GROUP BY usgs_site_id, param_code
HAVING bool_and(n_observations = 1)
"""

# Which column carries this (feature, site)'s series.
#
# A deseasonalized feature is scanned on its ANOMALY - the calendar removed is the whole point, and
# Phase 5's finding 1 is that the raw level relationship was substantially calendar. A run-length
# feature has no anomaly by construction (thresholds.py: a day-of-year median of a count is a
# number with no meaning attached), so it is scanned on its VALUE.
#
# ASKED OF THE DATA RATHER THAN MAPPED FROM THE REGISTRY, because a mapping would be a second copy
# of a fact the builders already express, and CLAUDE.md § 17 is explicit that the registry is the
# single source of truth for what a feature is. `bool_or` rather than `every`: the climatology
# guard legitimately refuses some days, and a feature with any anomaly at all is an anomaly
# feature.
SERIES_COLUMNS_SQL = """
SELECT feature_name, site_id, bool_or(anomaly IS NOT NULL) AS has_anomaly
  FROM features
 GROUP BY feature_name, site_id
"""


def sites(conn) -> list[str]:
    """Every seeded gauge, in id order.

    From `gauges` rather than from `features`, so a site with no feature rows is ENUMERATED AND
    REFUSED rather than silently absent. A missing site and a site that had nothing to say are
    different facts, and only one of them is a bug.
    """
    return [row[0] for row in conn.execute(SITES_SQL).fetchall()]


def degenerate_site_params(conn) -> set[tuple[str, str]]:
    """`{(site_id, param_code)}` whose every gauge_daily row reports a single observation."""
    return {(row[0], row[1]) for row in conn.execute(DEGENERATE_PARAMS_SQL).fetchall()}


def series_columns_by_pair(conn) -> dict[tuple[str, str], str]:
    """`{(feature_name, site_id): 'anomaly' | 'value'}`."""
    return {
        (row[0], row[1]): ("anomaly" if row[2] else "value")
        for row in conn.execute(SERIES_COLUMNS_SQL).fetchall()
    }


def grid(
    conn,
    *,
    lag_min: int = DEFAULT_LAG_MIN,
    lag_max: int = DEFAULT_LAG_MAX,
    horizons=targets_module.HORIZON_DAYS,
    regimes=regimes_module.REGIMES,
    target_name: str = targets_module.TARGET_NAME,
    feature_filter: str | None = None,
) -> Grid:
    """The grid for this database. The three queries, then `build_grid`."""
    return build_grid(
        sites=sites(conn),
        degenerate_params=degenerate_site_params(conn),
        series_columns=series_columns_by_pair(conn),
        lags=lag_range(lag_min, lag_max),
        horizons=horizons,
        regimes=regimes,
        target_name=target_name,
        feature_filter=feature_filter,
    )
