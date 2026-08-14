"""Lock movements: zero is a value, NULL is the absence of one, and the key holds three fields.

The zero rule is the reason this file exists. During the 2022 low-water event, near-zero barge
movement is not missing data - it is THE OBSERVATION. Both ways of losing it are one line long and
both look like tidying:

    if movement.tons:              drops reported zeros along with unreported weeks
    tons = record.get(...) or 0    invents a surveyed zero out of silence

The two are tested together where possible, because two separate tests can each be satisfied by
one wrong implementation (the pattern CLAUDE.md § 14 describes for empty-versus-missing series).

0015 made this argument about `barges`. The argument was right; the column was not - USDA
publishes no barge count at all, only tons, and migration 0016 moves the guard to the measure that
exists. The field-map side of that correction is in test_usda_field_maps.py.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.ingest import usda_movements
from app.ingest.socrata_client import MalformedResponseError

ZERO_WEEK = date(2026, 8, 1)


def record(
    lock="MS Locks 27",
    published_date="2026-08-08T00:00:00.000",
    commodity="Corn",
    tons="61500",
) -> dict:
    """A record in the shape USDA actually publishes (measured 2026-08-14).

    `tons=...` omits the field entirely, which models the "not reported" case - different from a
    present empty string and different again from a present zero.
    """
    built = {
        usda_movements.FIELDS["week_ending"]: published_date,
        "week": "31",
        "month": "8",
        "year": "2026",
        usda_movements.FIELDS["commodity"]: commodity,
        usda_movements.FIELDS["lock"]: lock,
    }
    if tons is not ...:
        built[usda_movements.FIELDS["tons"]] = tons
    return built


# ---------------------------------------------------------------------------------------------
# Unit tier.
# ---------------------------------------------------------------------------------------------


def test_a_zero_tonnage_week_is_stored_not_skipped():
    """A reported zero survives parsing as 0, and is not filtered anywhere.

    A zero week is a lock that was surveyed and moved nothing - the physical fact this project's
    whole thesis is about. Skipping it deletes the event from the record and leaves a hole
    indistinguishable from a week nobody reported.
    """
    parsed = usda_movements.parse_movements(
        [
            record(tons="136400"),
            record(published_date="2026-08-01T00:00:00.000", tons="0"),
        ]
    )

    assert len(parsed) == 2, (
        f"{len(parsed)} of 2 records survived parsing - a zero-tonnage week was filtered out. It "
        f"is an observation, not an absence (migration 0016)."
    )

    zero = next(m for m in parsed if m.week_ending == ZERO_WEEK)
    assert zero.tons == Decimal("0") and zero.tons is not None, (
        f"the reported zero became {zero.tons!r}. 0 means reported-as-none; None means not "
        f"reported."
    )

    # The scalar parser, directly: every falsy-but-real form maps to 0, not to None.
    assert usda_movements.parse_optional_decimal("0", field="tons") == Decimal("0")
    assert usda_movements.parse_optional_decimal(0, field="tons") == Decimal("0")


def test_an_unreported_measure_is_none_and_never_zero():
    """Absent and empty fields become None. The other direction of the same collapse.

    Coalescing NULL to 0 invents a surveyed zero out of silence, and every average over the series
    is then dragged toward zero by weeks nobody measured. Held here alongside the zero test so an
    implementation cannot satisfy one by breaking the other.
    """
    absent = usda_movements.movement_from(record(tons=...))
    assert absent.tons is None, f"an absent measure became {absent.tons!r} instead of None"

    empty = usda_movements.movement_from(record(tons="   "))
    assert empty.tons is None, (
        "an empty cell became a zero; Socrata's empty string is 'no value', not 'none moved'"
    )

    assert usda_movements.parse_optional_decimal(None, field="tons") is None

    # And a value that cannot be read is an ERROR, not a zero and not a None: a tonnage this
    # module cannot parse is not a tonnage of none.
    with pytest.raises(MalformedResponseError):
        usda_movements.parse_optional_decimal("about forty thousand", field="tons")


def test_a_missing_key_field_raises_rather_than_being_defaulted():
    """The three key fields are required; the measure is not.

    The asymmetry is deliberate: a row that cannot be keyed can never be corrected or superseded,
    while a row with no measure is an ordinary unreported week.
    """
    for field in ("lock", "week_ending", "commodity"):
        incomplete = record()
        del incomplete[usda_movements.FIELDS[field]]
        with pytest.raises(MalformedResponseError) as excinfo:
            usda_movements.movement_from(incomplete)
        assert usda_movements.FIELDS[field] in str(excinfo.value)


def test_the_captured_page_fixture_parses_including_its_zero_row(socrata_body):
    """The captured shape parses, and its zero-tonnage row survives.

    The fixture carries a real zero because a fixture with no zero in it would let a
    zero-filtering implementation pass every test in this file. The three records are in the
    measured field shape; the zero-tonnage week is constructed, since the captured sample was not
    a zero week.
    """
    import json

    movements = usda_movements.parse_movements(json.loads(socrata_body("page_1")))

    assert len(movements) == 3
    zeros = [m for m in movements if m.tons == 0]
    assert len(zeros) == 1, "the fixture's zero-tonnage row did not survive parsing"
    assert zeros[0].week_ending == date(2026, 8, 1)
    # And the plural lock string arrived from the fixture untouched.
    assert "MS Locks 27" in {m.lock for m in movements}


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_null_and_zero_are_distinguishable(migrated_db):
    """In the database, 0 and NULL are different rows and stay different.

    Asserted with SQL that can tell them apart (`IS NULL`, `= 0`) rather than by reading the
    values into Python, because the point is that the DATABASE holds the distinction - that is
    where Phase 5's features will read it from, and `NULL = 0` is NULL in SQL, so a query written
    the obvious way silently returns neither.
    """
    usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements(
            [
                record(published_date="2026-08-01T00:00:00.000", tons="0"),
                record(published_date="2026-07-25T00:00:00.000", tons=...),
            ]
        ),
    )
    migrated_db.commit()

    reported_zero = migrated_db.execute(
        "SELECT count(*) FROM lock_movements WHERE tons = 0"
    ).fetchone()[0]
    not_reported = migrated_db.execute(
        "SELECT count(*) FROM lock_movements WHERE tons IS NULL"
    ).fetchone()[0]

    assert reported_zero == 1, (
        f"{reported_zero} row(s) hold a reported zero; the surveyed-zero week is missing or was "
        f"stored as NULL"
    )
    assert not_reported == 1, (
        f"{not_reported} row(s) hold NULL; the unreported week was coalesced to 0"
    )
    assert migrated_db.execute("SELECT count(*) FROM lock_movements").fetchone()[0] == 2

    # A week going from UNREPORTED to REPORTED-ZERO is a genuine revision and must count as a
    # write. `IS DISTINCT FROM` is what makes it count: `NULL = 0` is NULL, so a plain `<>`
    # comparison would treat this as no change and the correction would never land.
    written = usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements(
            [record(published_date="2026-07-25T00:00:00.000", tons="0")]
        ),
    )
    migrated_db.commit()

    assert written == 1, (
        "a week revised from NOT REPORTED to REPORTED ZERO was not counted as a write - the "
        "upsert is comparing with = rather than IS DISTINCT FROM, so the correction was dropped"
    )
    assert (
        migrated_db.execute("SELECT count(*) FROM lock_movements WHERE tons = 0").fetchone()[0]
        == 2
    )


@pytest.mark.integration
def test_movements_key_is_lock_week_ending_commodity(migrated_db):
    """One lock, one week: corn and soybeans are two rows, and there is no direction. Test 15.

    Two commodities through one lock in one week are two different published tonnages, and a key
    missing `commodity` keeps one arbitrarily - the series silently becomes "whatever was
    published last for this lock and week".

    THE KEY DOES NOT CARRY `direction`, and that is the correction 0016 made: the dataset is
    downbound-only by construction, so there is no direction dimension to key on and a constant
    column would add nothing. This test would fail to insert at all against a schema that still
    required one.
    """
    usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements(
            [
                record(lock="MS Locks 27", commodity="Corn", tons="136400"),
                record(lock="MS Locks 27", commodity="Soybeans", tons="27000"),
                record(lock="IL La Grange", commodity="Corn", tons="41000"),
            ]
        ),
    )
    migrated_db.commit()

    rows = migrated_db.execute(
        "SELECT lock, commodity, tons FROM lock_movements WHERE week_ending = %s"
        " ORDER BY lock, commodity",
        (date(2026, 8, 8),),
    ).fetchall()

    assert rows == [
        ("IL La Grange", "Corn", Decimal("41000")),
        ("MS Locks 27", "Corn", Decimal("136400")),
        ("MS Locks 27", "Soybeans", Decimal("27000")),
    ], f"the three published rows did not survive as three rows: {rows}"

    columns = {
        row[0]
        for row in migrated_db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = "
            "'lock_movements'"
        ).fetchall()
    }
    assert columns == {"lock", "week_ending", "commodity", "tons"}, (
        f"lock_movements holds {sorted(columns)}. `direction` and `barges` are not published by "
        f"this source, and a column that would always be NULL is not created (migration 0016)."
    )


@pytest.mark.integration
def test_an_eighth_lock_value_is_rejected_by_the_check(migrated_db):
    """The seven published locks insert; an eighth is refused by the database. Test 12.

    THE CONSTRAINT IS A TRIPWIRE, NOT A VOCABULARY. An unseen lock value must be a loud insert
    failure rather than a silent new series that no query joins and nobody notices missing.

    The fix when this fires is to MEASURE the new value and add it in a new migration. Never to
    drop the constraint, and never to normalize the arriving string into one that fits.
    """
    seven = [
        "AK Lock 1",
        "IL La Grange",
        "MS Lock 15",
        "MS Lock 25",
        "MS Lock 26",
        "MS Locks 27",
        "OH Olmsted",
    ]
    written = usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements([record(lock=name, tons="1000") for name in seven]),
    )
    migrated_db.commit()
    assert written == 7, (
        f"{written} of the seven published locks were accepted; the CHECK is rejecting a value "
        f"USDA publishes, which would stop the backfill dead"
    )

    with pytest.raises(Exception) as excinfo:
        usda_movements.upsert_movements(
            migrated_db, usda_movements.parse_movements([record(lock="MS Lock 27")])
        )
    migrated_db.rollback()
    assert "lock_movements_lock_known" in str(excinfo.value), (
        f"the rejection did not come from the lock tripwire: {excinfo.value}"
    )

    # THE REJECTED VALUE IS THE NORMALIZED FORM OF A REAL ONE - `MS Lock 27`, singular, where USDA
    # publishes `MS Locks 27`. That is not a hypothetical eighth lock: it is what a well-meaning
    # normalization step would produce, and the constraint refuses it rather than opening a second
    # series for a lock that already has 4,928 rows.
    assert (
        migrated_db.execute(
            "SELECT count(*) FROM lock_movements WHERE lock = 'MS Locks 27'"
        ).fetchone()[0]
        == 1
    )


# ---------------------------------------------------------------------------------------------
# Integration tier — the wiring both USDA tables share.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_neither_usda_table_is_a_hypertable(migrated_db):
    """Read from the catalog, not from the migration text.

    "Make it a hypertable like the others" is the consistency argument to expect, and it has to
    lose to arithmetic rather than to a comment: these are weekly series of tens of thousands of
    rows (26,144 movements measured at the source), where chunking produces chunks whose metadata
    rivals their contents - and Phase 3's own measurement concluded TimescaleDB was an engineering
    choice rather than a necessity at 290k rows.

    Asserted against `timescaledb_information.hypertables` so it fails if someone converts the
    table live, not only if they edit a migration.
    """
    hypertables = {
        row[0]
        for row in migrated_db.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables"
        ).fetchall()
    }

    assert "barge_rates" not in hypertables and "lock_movements" not in hypertables, (
        f"a USDA table is registered as a hypertable: {sorted(hypertables)}. That is ceremony "
        f"with no measurement behind it at this row count (migration 0014)."
    )
    # The control: the reading tables ARE hypertables, so this test is not passing because the
    # catalog query returns nothing.
    assert {"gauge_readings_iv", "gauge_readings_daily"} <= hypertables, (
        f"the catalog query found no reading hypertables ({sorted(hypertables)}), so the "
        f"assertion above proves nothing"
    )


@pytest.mark.integration
def test_both_usda_tables_are_in_the_freshness_registry(migrated_db, database_url):
    """Both tables registered, both queryable through the real check.

    CLAUDE.md § 12: no ingest client is complete until it registers its table, and liveness is
    measured from the DATA rather than the process. Registering one of the two is the cheap
    failure - the heartbeat then reports healthy while the unregistered table receives nothing,
    which is a green light meaning "the tables I know about are fine".
    """
    from datetime import datetime, timedelta, timezone

    from app import db
    from app.orchestration import cadence as cadence_module
    from app.orchestration import heartbeat

    registered = {f.table for f in heartbeat.FRESHNESS}
    assert {"barge_rates", "lock_movements"} <= registered, (
        f"the freshness registry covers {sorted(registered)}; both USDA tables must be registered "
        f"in the commit that creates them (CLAUDE.md § 12)"
    )

    for entry in heartbeat.FRESHNESS:
        if entry.table in ("barge_rates", "lock_movements"):
            assert entry.job_name in cadence_module.BY_NAME, (
                f"{entry.table} names job {entry.job_name!r}, which has no cadence entry - it "
                f"would be reported stale forever with no way to fix it"
            )
            assert entry.timestamp_column == "week_ending"
            # Weekly publication plus a late holiday week must not alert; two consecutive missed
            # publications must. That pins the threshold between 9ish and 14 days.
            assert timedelta(days=9) < entry.max_staleness < timedelta(days=14), (
                f"{entry.table}'s staleness threshold is {entry.max_staleness}, which either "
                f"fires on ordinary weekly lateness or sleeps through two missed publications"
            )

    with db.connection(database_url) as conn:
        verdicts = {v.table: v for v in heartbeat.check_freshness(conn, now=datetime.now(timezone.utc))}

    for table in ("barge_rates", "lock_movements"):
        verdict = verdicts[table]
        assert verdict.error is None, f"{table} could not be queried: {verdict.describe()}"
        # Empty on a fresh database, and an empty ingest table is STALE, not quiet.
        assert verdict.stale and verdict.newest is None


@pytest.mark.integration
def test_rates_and_movements_are_separate_cadence_entries(migrated_db):
    """Two cadence entries, two registered jobs, one @job each.

    Fetching both SOURCES inside one @job produces ONE job_runs row whose status is the AND of two
    independent things: a movements outage marks rates failed, and the heartbeat - which joins on
    job_name - cannot say which one went quiet. CLAUDE.md § 4 requires one @job per scheduled
    unit, and this is the operational reason behind the rule.

    THE THREE RATES DATASETS ARE NOT A THIRD AND FOURTH SCHEDULED UNIT. They are one publication
    on one schedule into one table, fetched by one job - asserted below, because "one @job per
    scheduled unit" read carelessly would split them and produce three job_runs rows nobody reads
    apart.
    """
    from app.orchestration import cadence as cadence_module
    from app.orchestration import scheduler

    names = {c.job_name for c in cadence_module.CADENCES}
    assert {"usda_rates_ingest", "usda_movements_ingest"} <= names, (
        f"the cadence table holds {sorted(names)}; the two USDA sources are separate scheduled "
        f"units"
    )
    assert not {n for n in names if n.startswith("usda_rates_") and n != "usda_rates_ingest"}, (
        f"the cadence table has grown a per-horizon rates job: {sorted(names)}. The three "
        f"datasets are one scheduled unit."
    )

    for name in ("usda_rates_ingest", "usda_movements_ingest"):
        entry = cadence_module.BY_NAME[name]
        assert entry.interval.total_seconds() == 604800, (
            f"{name} is not weekly ({entry.interval}); the source publishes weekly"
        )
        # Grace derives from the interval and must land well below it, or the job can never
        # record a `missed` row (CLAUDE.md § 12). Confirmed by measurement rather than assumed.
        assert entry.misfire_grace_time == 302400, (
            f"{name}'s grace is {entry.misfire_grace_time}s; expected half the weekly interval"
        )
        assert entry.misfire_grace_time < entry.interval_seconds

        assert name in scheduler.JOB_FUNCTIONS, (
            f"{name} has a cadence entry but no registered function; build_scheduler() refuses to "
            f"start in that state, by design"
        )

    # The two callables are DISTINCT. One function registered under both names would satisfy every
    # assertion above and be exactly the single job the separation exists to prevent.
    assert (
        scheduler.JOB_FUNCTIONS["usda_rates_ingest"]
        is not scheduler.JOB_FUNCTIONS["usda_movements_ingest"]
    ), "both USDA cadence entries point at the same callable - that is one job, not two"

    # And each writes its own table: a shared implementation reading a flag would pass the
    # identity check above while still producing one job_runs row per run for both sources.
    assert usda_movements.TABLE == "lock_movements"
    from app.ingest import usda_rates

    assert usda_rates.TABLE == "barge_rates"
    assert usda_rates.JOB_NAME != usda_movements.JOB_NAME
    # The one rates job covers all three datasets.
    assert len(usda_rates.DATASET_KEYS) == 3
