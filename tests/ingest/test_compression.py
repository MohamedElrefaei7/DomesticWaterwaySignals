"""Integration tier — the hypertable and its compression. Requires DATABASE_URL.

Covers migrations 0005 and 0006, and the query live verification step 6 runs to take the
compression ratio.

Everything here is READ BACK FROM THE SERVER rather than asserted about the migration text.
`ALTER TABLE ... SET (timescaledb.compress_segmentby = ...)` accepts a column list without
complaint, and `create_hypertable` accepts a chunk interval the same way; whether either took
effect is a property of the database. CLAUDE.md § 13 is about checking the thing rather than the
statement that was supposed to configure it - a test that grepped 0006 for the string
'param_code' would pass against a server where the setting never applied.
"""

import pytest

from app.ingest import usgs_ingest

pytestmark = pytest.mark.integration

TABLE = "gauge_readings"


def test_hypertable_exists_with_the_expected_chunk_interval(migrated_db):
    """gauge_readings is a hypertable partitioned on ts, in 7-day chunks.

    If create_hypertable never ran, this is an ordinary Postgres table: every query still works,
    every insert still succeeds, and nothing anywhere reports a problem - it is simply not
    partitioned, not compressible, and not prunable. That is a failure with no symptom until the
    table is large enough that the fix is expensive, which is why it is asserted here rather than
    assumed from the migration having applied cleanly.
    """
    row = migrated_db.execute(
        "SELECT column_name, time_interval FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = %s",
        (TABLE,),
    ).fetchall()

    assert row, (
        f"{TABLE} has no dimensions in timescaledb_information.dimensions, which means it is not "
        f"a hypertable at all - 0005's create_hypertable did not take effect."
    )
    assert len(row) == 1, f"expected exactly one partitioning dimension, got {row}"

    column_name, time_interval = row[0]
    assert column_name == "ts", f"partitioned on {column_name!r}, not on ts"

    from datetime import timedelta

    assert time_interval == timedelta(days=7), (
        f"chunk interval is {time_interval}, not 7 days. Smaller chunks compress and prune "
        f"better than the default at this data volume (0005)."
    )


def test_compression_settings_segment_by_site_and_param(migrated_db):
    """segmentby is (usgs_site_id, param_code) and orderby is ts DESC.

    param_code LOOKS REDUNDANT and is the assertion that matters most here. This commit ingests
    one parameter, so segmenting by it buys nothing today - which is exactly why a future session
    would remove it as dead configuration. It is correct the day a second parameter lands, and
    segmentby cannot be changed on a populated table without decompressing every chunk first. The
    cost of keeping it is zero; the cost of re-adding it later is the whole table.
    """
    settings = usgs_ingest.compression_settings(migrated_db, TABLE)

    assert settings["segmentby"], (
        f"{TABLE} has no compression settings at all (read from {settings['view']}) - 0006's "
        f"ALTER TABLE did not take effect, and the compression policy has nothing to act on."
    )

    assert set(settings["segmentby"]) == {"usgs_site_id", "param_code"}, (
        f"segmentby is {settings['segmentby']}; expected usgs_site_id and param_code. Dropping "
        f"param_code cannot be undone cheaply - changing segmentby on a populated hypertable "
        f"requires decompressing every chunk in it."
    )

    assert settings["orderby"] == [("ts", "DESC")], (
        f"orderby is {settings['orderby']}, expected [('ts', 'DESC')]. Ordering is what makes "
        f"the timestamp and value encodings work; an unordered batch compresses like noise."
    )


def test_a_compression_policy_is_registered(migrated_db):
    """The policy exists, so chunks compress without anyone remembering to run compress_chunk.

    Not in the commit brief's numbered list, and here because 0006's two statements fail
    independently: the ALTER TABLE can succeed while add_compression_policy does not, leaving a
    table that is compressible and never compressed. Nothing would report that - the settings all
    read correctly, which is CLAUDE.md § 2's theme 2.
    """
    jobs = migrated_db.execute(
        "SELECT proc_name, config FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s",
        (TABLE,),
    ).fetchall()

    policies = [j for j in jobs if "compress" in (j[0] or "")]
    assert policies, (
        f"no compression policy is registered for {TABLE}. Jobs found: {jobs}. The table is "
        f"compressible and nothing will ever compress it."
    )


def test_compression_stats_query_returns_both_sizes(migrated_db):
    """The measurement query works and reports both byte counts.

    THE POINT OF THIS TEST IS THAT THE MEASUREMENT STEP CANNOT FAIL AT REPORTING TIME. Live
    verification step 6 takes the compression ratio after an eighteen-year backfill; discovering
    then that the stats function is named something else on this server, or that the column names
    changed, means re-deriving it by hand at the worst moment (CLAUDE.md § 13).

    It asserts the query RUNS and returns the fields, not that the ratio is any particular value.
    The ratio is a measurement, and a test that expected a number would be a placeholder ratio
    wearing a test's clothes - which 0006 explicitly forbids.
    """
    stats = usgs_ingest.compression_stats(migrated_db, TABLE)

    assert stats["function"] in (
        "hypertable_compression_stats",
        "hypertable_columnstore_stats",
    ), f"unexpected stats function {stats['function']!r}"

    # The keys the reporting path reads must all be present, whatever their values are on an
    # empty table. This is what stops step 6 failing on a KeyError after a six-hour backfill.
    for key in ("before_bytes", "after_bytes", "compressed_chunks", "ratio"):
        assert key in stats, f"compression_stats() did not return {key!r}: {stats}"

    # On a freshly migrated, empty table nothing is compressed yet. Reported as None rather than
    # 0: a table whose chunks are all uncompressed has no compressed size, and calling that zero
    # bytes would make the ratio look infinite.
    assert stats["compressed_chunks"] == 0
    assert stats["ratio"] is None, (
        f"a ratio of {stats['ratio']} was reported for a table with no compressed chunks. The "
        f"only ratio this project publishes is one measured on real data (CLAUDE.md § 7)."
    )
