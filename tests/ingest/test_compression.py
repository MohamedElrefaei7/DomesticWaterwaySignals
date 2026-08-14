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

TABLE = "gauge_readings_iv"


def test_hypertable_exists_with_the_expected_chunk_interval(migrated_db):
    """gauge_readings_iv is a hypertable partitioned on ts, in 7-day chunks.

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


# ---------------------------------------------------------------------------------------------
# Phase 3.5 — the rename, and the daily table's own schema.
# ---------------------------------------------------------------------------------------------
#
# Placement note: these live here rather than in a new file because this module is already "the
# schema read back from the catalog" suite, and the rename's whole risk is a catalog fact.

DAILY_TABLE = "gauge_readings_daily"


def test_iv_table_retains_its_hypertable_and_compression_settings_after_rename(migrated_db):
    """0007's ALTER TABLE ... RENAME carried the TimescaleDB state with it.

    TimescaleDB tracks hypertables, compression settings and policies by relation OID rather than
    by name, so a rename is EXPECTED to be transparent. EXPECTED IS NOT VERIFIED, and the failure
    mode is the worst kind: a silently dropped compression policy changes nothing observable
    until the storage bill, and a hypertable registration lost in a rename turns the table back
    into ordinary Postgres where every query still works and nothing is partitioned or prunable.

    Read from the catalog, not from the migration text.
    """
    from datetime import timedelta

    dimensions = migrated_db.execute(
        "SELECT column_name, time_interval FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = %s",
        (TABLE,),
    ).fetchall()
    assert dimensions == [("ts", timedelta(days=7))], (
        f"{TABLE} is not a 7-day hypertable on ts after the rename: {dimensions}. The rename "
        f"lost the hypertable registration."
    )

    settings = usgs_ingest.compression_settings(migrated_db, TABLE)
    assert set(settings["segmentby"]) == {"usgs_site_id", "param_code"}, (
        f"compression settings did not survive the rename: {settings}"
    )
    assert settings["orderby"] == [("ts", "DESC")]

    policies = migrated_db.execute(
        "SELECT proc_name, config FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s",
        (TABLE,),
    ).fetchall()
    assert any("compress" in (p[0] or "") for p in policies), (
        f"no compression policy on {TABLE} after the rename. Jobs: {policies}. The table is "
        f"compressible and nothing will ever compress it - invisible until the storage bill."
    )

    # And the OLD name is gone, so nothing can still be querying "the main one" by accident.
    assert (
        migrated_db.execute("SELECT to_regclass('public.gauge_readings')").fetchone()[0] is None
    ), "gauge_readings still exists; the rename left a table that reads as the complete record"


def test_daily_table_is_a_hypertable_with_the_expected_compression_settings(migrated_db):
    """gauge_readings_daily: 365-day chunks, segmented by site/param/stat, ordered date DESC.

    The chunk interval is written as `365 days` in 0008 rather than `1 year` because TimescaleDB
    stores a Postgres interval year as 360 days - so a file saying "1 year" and a test asserting
    365 would disagree, and the natural fix would be to weaken the test rather than notice the
    file said something it did not mean.
    """
    from datetime import timedelta

    dimensions = migrated_db.execute(
        "SELECT column_name, time_interval FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = %s",
        (DAILY_TABLE,),
    ).fetchall()
    assert dimensions == [("date", timedelta(days=365))], (
        f"{DAILY_TABLE} is not a 365-day hypertable on `date`: {dimensions}"
    )

    settings = usgs_ingest.compression_settings(migrated_db, DAILY_TABLE)
    assert set(settings["segmentby"]) == {"usgs_site_id", "param_code", "stat_cd"}, (
        f"segmentby is {settings['segmentby']}; expected site, param AND stat. stat_cd segments a "
        f"single value today and is correct the day the daily minimum lands - changing segmentby "
        f"later requires decompressing every chunk."
    )
    assert settings["orderby"] == [("date", "DESC")]

    policies = migrated_db.execute(
        "SELECT config FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s AND proc_name LIKE '%%compress%%'",
        (DAILY_TABLE,),
    ).fetchall()
    assert policies, f"no compression policy on {DAILY_TABLE}"
    assert "1 year" in str(policies[0][0]), (
        f"the daily compression policy is {policies[0][0]}, not the 1 year 0009 specifies. The "
        f"daily table's revision window is the same as the instantaneous one but its volume is "
        f"~25x smaller, which is why it compresses later rather than sooner."
    )


def test_daily_primary_key_includes_stat_cd(migrated_db):
    """(usgs_site_id, date, param_code, stat_cd). All four.

    Without stat_cd the table cannot hold the daily mean and the daily minimum for the same site
    and date - the second would silently overwrite the first through the upsert, which resolves
    on the primary key. This project has a specific future interest in the minimum: the
    constraint that binds a barge tow is the low point of the day, not the average of it.

    Adding a column to a primary key after the fact means rebuilding the table and its compressed
    chunks, so it costs nothing now and a great deal later.
    """
    key_columns = [
        row[0]
        for row in migrated_db.execute(
            "SELECT a.attname"
            "  FROM pg_index i"
            "  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = %s::regclass AND i.indisprimary"
            " ORDER BY a.attname",
            (DAILY_TABLE,),
        ).fetchall()
    ]

    assert key_columns == ["date", "param_code", "stat_cd", "usgs_site_id"], (
        f"the daily primary key is {key_columns}. Without stat_cd, a daily minimum upserted for a "
        f"date that already has a mean overwrites it, and nothing reports the loss."
    )


def test_gauges_carries_separate_dv_and_iv_record_starts(migrated_db):
    """Two columns, both NOT NULL, and the daily floors are not all the same value.

    One `record_start` cannot describe two endpoints whose depth differs per site: Vicksburg
    publishes daily values from 2008-2010 while its instantaneous record is a rolling window of
    recent weeks. A single column silently means whichever endpoint the reader assumed.
    """
    columns = {
        row[0]: row[1]
        for row in migrated_db.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'gauges' AND column_name LIKE '%%record_start%%'"
        ).fetchall()
    }

    assert set(columns) == {"iv_record_start", "dv_record_start"}, (
        f"gauges carries {sorted(columns)}. A single record_start column cannot describe two "
        f"endpoints with different depth per site (CLAUDE.md § 15)."
    )
    assert columns["dv_record_start"] == "NO", (
        "dv_record_start is nullable; a site with no daily floor would have the backfill either "
        "skip it silently or walk from an arbitrary default"
    )

    floors = dict(
        migrated_db.execute(
            "SELECT usgs_site_id, dv_record_start FROM gauges ORDER BY usgs_site_id"
        ).fetchall()
    )
    assert len(floors) == 4
    assert len(set(floors.values())) > 1, (
        f"every site has the same dv_record_start ({set(floors.values())}). The measured record "
        f"boundaries differ per site; a uniform set means they were filled in from an assumption."
    )
