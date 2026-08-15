"""The feature registry: the single source of truth for what features exist.

Migration 0020 deliberately puts NO CHECK constraint on `features.feature_name`, and this module is
why. The lock and location vocabularies in 0016 belong to USDA - an unseen value there is news, and
a CHECK is the tripwire that reports it. THIS VOCABULARY IS THIS PROJECT'S OWN, so a CHECK would be
a second copy of the list below that has to be migrated in lockstep forever, and the two would
disagree the first time somebody was in a hurry.

The tripwire lives in the build instead: A FEATURE ROW WHOSE NAME HAS NO REGISTRY ENTRY IS AN
ERROR, reported rather than ignored. It means one of two things, and both need a human:

    a feature was RENAMED     and its old rows are orphans nothing will ever update again. They
                              keep answering queries with values frozen at the rename, which is
                              worse than their absence - a stale series looks like a live one.
    something WROTE OUTSIDE   a script, a manual INSERT, an older branch. The registry is then not
    the registry             describing the table, and every count taken from it is short.

NOTHING CONSTRUCTS A FEATURE NAME BY CONCATENATION AT WRITE TIME. The build loop iterates this
tuple; the threshold names are assembled by `thresholds.feature_name_for` and registered here, so
the assembled name exists in the registry before any row carrying it exists in the table.

THE BUILDERS ARE BOUND WITH functools.partial, NOT SELECTED BY A BRANCH
-----------------------------------------------------------------------
Every builder has the same signature - `builder(observations) -> [(date, value, anomaly, n_years)]`
- and its parameters are bound here rather than passed at call time. So the build loop has no
`if feature.name.startswith("days_below")` in it, which is where a sixth feature would land
silently in the wrong branch.

Plain tuples rather than a dataclass for the builder return, so that seasonal.py and thresholds.py
can be imported by this module without importing it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

from app.features import seasonal, thresholds

# The parameter this phase's features are built on.
#
# DISCHARGE (00060) AND NOT STAGE (00065), because stage is absent at two of the four seeded gauges
# and this project refuses to derive it from discharge through a USGS rating curve (CLAUDE.md
# § 14): ratings are provisional and shift with channel features, so a current rating applied to
# 2008 discharge yields a stage that gauge never read.
DISCHARGE = "00060"


@dataclass(frozen=True)
class Feature:
    """One declared feature: its name, what it reads, and what computes it.

    name          The value written to `features.feature_name`. Unique across the registry.
    builder       `builder(observations) -> [(date, value, anomaly, climatology_n_years)]`, with
                  every parameter already bound. Required, and a test asserts it is callable -
                  a registry entry with no builder is a feature that exists in every listing and
                  in no row of the table.
    param_code    The USGS parameter this reads from gauge_daily.
    source_column Which gauge_daily statistic it reads. Checked against rollup.READABLE_COLUMNS at
                  import, below.
    description   For a human reading a listing. Not decoration: `days_below_p05` says nothing
                  about which statistic it thresholds, and that is the difference between a
                  daily-mean run and a daily-minimum one.
    """

    name: str
    builder: Callable
    param_code: str
    source_column: str
    description: str


REGISTRY: tuple[Feature, ...] = (
    Feature(
        name="discharge_mean",
        builder=seasonal.build_anomalies,
        param_code=DISCHARGE,
        source_column="value_mean",
        description=(
            "Daily mean discharge, with a day-of-year climatology subtracted. The better-behaved "
            "of the two discharge features; see discharge_min for the one closer to the mechanism."
        ),
    ),
    Feature(
        name="discharge_min",
        builder=seasonal.build_anomalies,
        param_code=DISCHARGE,
        source_column="value_min",
        description=(
            "Daily MINIMUM discharge, deseasonalized. Closer to the physical constraint than the "
            "mean - a barge is bound by the shallowest moment it transits - but READ "
            "gauge_daily.n_observations before trusting it: where that is 1 the row came from a "
            "published daily mean and this is that mean (migration 0019)."
        ),
    ),
    *(
        Feature(
            name=thresholds.feature_name_for(level),
            # partial, not a lambda: a lambda closing over `level` in a comprehension is the
            # classic late-binding bug, and all three features would threshold at 20.
            builder=partial(thresholds.build_days_below, level=level),
            param_code=DISCHARGE,
            # THRESHOLDED ON THE MINIMUM, not the mean. A day whose mean is above the threshold can
            # still contain hours that bound a tow, and the run-length family exists to count
            # constrained days rather than statistically low ones.
            source_column="value_min",
            description=(
                f"Consecutive days whose daily minimum discharge is strictly below the {level}th "
                f"percentile of this site's own record. The percentile is a STAND-IN for an "
                f"operational threshold, which is a human decision awaiting a source "
                f"(CLAUDE.md section 1). NULL across a data gap, never 0."
            ),
        )
        for level in thresholds.PERCENTILES
    ),
)


BY_NAME: dict[str, Feature] = {feature.name: feature for feature in REGISTRY}

if len(BY_NAME) != len(REGISTRY):  # pragma: no cover - a duplicate is caught at import
    raise ValueError(
        "duplicate feature name in REGISTRY. `feature_name` is part of the features primary key, "
        "so two entries sharing one would silently write over each other's rows and only the "
        "later builder's values would survive."
    )


def _validate_source_columns() -> None:
    """Every declared source column must be one gauge_daily actually offers.

    Checked at import rather than at build time. A typo here would otherwise surface as a ValueError
    from rollup.observations partway through a four-hour from-scratch build, after the features
    before it in the registry had already been written.
    """
    from app.features import rollup

    unknown = sorted(
        {f.source_column for f in REGISTRY if f.source_column not in rollup.READABLE_COLUMNS}
    )
    if unknown:
        raise ValueError(
            f"registry declares gauge_daily column(s) that do not exist: {unknown}. "
            f"Known: {sorted(rollup.READABLE_COLUMNS)}."
        )


_validate_source_columns()


UNREGISTERED_SQL = """
SELECT DISTINCT feature_name
  FROM features
 ORDER BY feature_name
"""


def unregistered_feature_names(conn) -> list[str]:
    """Feature names present in the table with no registry entry. AN ERROR, NOT AN ORPHAN LIST.

    Read as `SELECT DISTINCT` and filtered in Python rather than as a `NOT IN (%s, %s, ...)`,
    because the interesting failure is the registry being SHORTER than the table and a query
    parameterised by the registry would still return the right answer while being harder to read
    at the moment somebody is trying to work out what happened.
    """
    present = {row[0] for row in conn.execute(UNREGISTERED_SQL).fetchall()}
    return sorted(present - set(BY_NAME))
