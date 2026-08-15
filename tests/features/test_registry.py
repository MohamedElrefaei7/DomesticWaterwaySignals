"""The registry: one source of truth for what features exist, and the orphan tripwire.

Migration 0020 puts no CHECK on `features.feature_name` on purpose - the vocabulary is this
project's own, so a constraint would be a second copy of the registry that has to be migrated in
lockstep. These tests are what replaces it.
"""

from datetime import date, timedelta

import pytest

from app.features import registry, rollup, thresholds


def test_every_registered_feature_has_a_builder():
    """Test 20. A declared feature with no builder appears in every listing and in no row.

    That is worse than an absent feature: it looks present. Anything enumerating the registry -
    a coverage report, the API surface in Phase 8, a human reading a listing - sees it and assumes
    the table holds it, and the query that finds nothing reads as "no data yet".
    """
    for feature in registry.REGISTRY:
        assert callable(feature.builder), (
            f"feature {feature.name!r} has builder {feature.builder!r}, which is not callable. It "
            f"would appear in every listing of what this project computes and produce no rows."
        )

    # AND EVERY BUILDER ACTUALLY RUNS AND RETURNS THE FOUR-TUPLE THE BUILD LOOP WRITES. `callable`
    # alone is satisfied by any object with __call__, including one that raises immediately - which
    # would fail at build time, not here, halfway through a four-hour rebuild.
    sample = [(date(2022, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(10)]
    for feature in registry.REGISTRY:
        rows = feature.builder(sample)
        assert len(rows) == len(sample), (
            f"{feature.name}'s builder returned {len(rows)} rows for {len(sample)} observations"
        )
        for row in rows:
            assert len(row) == 4, (
                f"{feature.name}'s builder returned {row!r}; the build loop unpacks "
                f"(date, value, anomaly, climatology_n_years)"
            )


def test_feature_names_are_unique():
    """Test 21. `feature_name` is part of the primary key.

    Two entries sharing a name would silently write over each other's rows - and only the later
    builder's values would survive, for a feature whose description says it is the earlier one.
    Enforced at import as well, so this can never be reached in a broken state; asserted here so
    the reason is written down where a reader of the registry will find it.
    """
    names = [feature.name for feature in registry.REGISTRY]
    assert len(names) == len(set(names)), f"duplicate feature name in the registry: {names}"
    assert set(registry.BY_NAME) == set(names)
    assert len(registry.REGISTRY) == len(registry.BY_NAME)


def test_every_feature_reads_a_column_gauge_daily_actually_has():
    """A typo'd source column must fail at import, not partway through a rebuild.

    By build time the features earlier in the registry have already been written, so the failure
    arrives with the table in a half-updated state and a stack trace pointing at the rollup rather
    than at the registry entry that is wrong.
    """
    for feature in registry.REGISTRY:
        assert feature.source_column in rollup.READABLE_COLUMNS, (
            f"{feature.name} reads {feature.source_column!r}, which gauge_daily does not offer"
        )


def test_the_threshold_features_are_named_by_the_module_that_defines_them():
    """No feature name is assembled by concatenation at write time.

    The registry is the vocabulary (migration 0020). A name built at write time exists in the table
    before it exists in the registry, which is precisely the orphan case the build reports - and it
    would report it about a row it had just written itself.
    """
    expected = {thresholds.feature_name_for(level) for level in thresholds.PERCENTILES}
    assert expected <= set(registry.BY_NAME), (
        f"the registry does not carry {sorted(expected - set(registry.BY_NAME))}"
    )
    # Zero-padded so p05 sorts before p10 in every listing a human reads.
    assert "days_below_p05" in registry.BY_NAME


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_a_feature_row_with_no_registry_entry_is_reported(migrated_db):
    """Test 22. An orphaned name is an ERROR, not a row to ignore.

    It means one of two things and both need a human: a feature was RENAMED and its old rows are
    orphans nothing will update again - they keep answering queries with values frozen at the
    rename, and a stale series is harder to notice than a missing one - or SOMETHING WROTE OUTSIDE
    THE REGISTRY, in which case the registry is not describing the table and every count taken
    from it is short.
    """
    from tests.features.conftest import ST_LOUIS

    assert registry.unregistered_feature_names(migrated_db) == [], (
        "a fresh database already holds unregistered feature names"
    )

    # A registered name lands quietly...
    migrated_db.execute(
        "INSERT INTO features (date, site_id, feature_name, value) VALUES (%s, %s, %s, %s)",
        (date(2022, 10, 4), ST_LOUIS, "discharge_mean", 1.0),
    )
    # ...and one that was renamed away does not.
    migrated_db.execute(
        "INSERT INTO features (date, site_id, feature_name, value) VALUES (%s, %s, %s, %s)",
        (date(2022, 10, 4), ST_LOUIS, "discharge_mean_OLD", 1.0),
    )
    migrated_db.commit()

    orphans = registry.unregistered_feature_names(migrated_db)
    assert orphans == ["discharge_mean_OLD"], (
        f"the orphan check returned {orphans}; it must name the unregistered feature and must not "
        f"name the registered one"
    )
