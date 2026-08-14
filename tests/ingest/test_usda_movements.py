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

WHAT MIGRATION 0018 MEASURED, AND WHY IT MAKES THESE TESTS LOAD-BEARING
-----------------------------------------------------------------------
Until 0018 the tests below guarded a HYPOTHETICAL: nobody had counted either population, and the
zero-versus-NULL argument was inherited from 0015's reasoning about a column that turned out not
to exist. Both populations are now measured across all 26,144 records:

    tons = 0        8,218 records (31%)   USDA's PUBLISHED way of saying nothing moved
    tons absent       108 records (0.4%)  three locks only, 96 of them in 2015-2016, FLAT across
                                          months - a REPORTING GAP, not a closure

So a zero is the ordinary case and a NULL is the rare one, and the NULLs sit on the SUMMARY locks -
`MS Locks 27` is the Mississippi's main southbound gate. Coalescing eleven of its weeks to 0 would
state that no grain moved through it. Every assertion here is now guarding a real population
against another real population.

AND THE MEANING IS NOT THE RATES MODULE'S MEANING. There, a NULL is winter navigation closure - a
fact about the river. Here it says nothing about the river at all. The handling is the same shape;
the meaning is different, and 0018 exists to keep the two apart.
"""

import json
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


def test_a_record_with_no_tons_key_yields_a_row_with_null_tons():
    """Test 1. An absent measure becomes NULL, and THE ROW IS STILL PRODUCED.

    Both halves matter and they fail differently. If the measure became 0, the row would state
    that nothing moved through a lock nobody surveyed. If the row vanished, the absence would
    become invisible to everything downstream - the series simply has no June 2015, which nothing
    can distinguish from an ingest that failed to fetch it (CLAUDE.md § 16).

    An explicit null is asserted alongside the absent key, because `optional_field` collapses the
    two deliberately: both are USDA declining to report this lock-week, spelled differently. The
    measurement counted 108 such records without distinguishing which spelling they use, so the
    parser must accept either.
    """
    absent = usda_movements.movement_from(record(tons=...))
    assert absent.tons is None, (
        f"an absent measure became {absent.tons!r} instead of None. A reporting gap at a summary "
        f"lock is not a tonnage of zero (migration 0018)."
    )
    # The row exists, fully keyed, and is the same row it would have been with a tonnage on it.
    assert (absent.lock, absent.week_ending, absent.commodity) == (
        "MS Locks 27",
        date(2026, 8, 8),
        "Corn",
    )

    explicit_null = usda_movements.movement_from(record(tons=None))
    assert explicit_null.tons is None, (
        f"an explicitly null measure became {explicit_null.tons!r}; an absent key and a null value "
        f"are the same statement about the world, spelled two ways"
    )

    # And neither is filtered out of a batch on the way past.
    parsed = usda_movements.parse_movements([record(tons=...), record(tons=None)])
    assert len(parsed) == 2, (
        f"{len(parsed)} of 2 unreported weeks survived parsing. A NULL row states the absence; a "
        f"missing row hides it."
    )


def test_a_record_with_explicit_zero_tons_yields_zero_not_null():
    """Test 2. The INVERSE direction of test 1, and 0018's decision 2.

    Only one direction of this was ever covered. A published 0 must survive as 0 - not as None,
    and not filtered out - because USDA publishes explicit zeros on 8,218 of 26,144 records. It is
    the normal way of saying nothing moved, and during a low-water event near-zero movement is not
    missing data, it is THE OBSERVATION.

    Both falsy-but-real forms are asserted, because `if not raw` is the one-line tidy that maps
    the string '0' and the integer 0 onto the unreported case.
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
    assert zero.tons is not None, (
        "the published zero became None - 8,218 records say 'none moved' this way, and reading "
        "them as 'not reported' erases the whole low-water signal (migration 0018)"
    )
    assert zero.tons == Decimal("0"), f"the reported zero became {zero.tons!r}"

    # The scalar parser, directly: every falsy-but-real form maps to 0, and none of them raises.
    assert usda_movements.parse_tons("0") == Decimal("0")
    assert usda_movements.parse_tons(0) == Decimal("0")
    assert usda_movements.parse_tons("0.0") == Decimal("0")


def test_a_record_with_unparseable_tons_raises_naming_the_value():
    """Test 3. The THIRD condition, never collapsed into the first (CLAUDE.md § 16).

    A blanket `record.get("tons")` is one call shorter and turns a corrupt value into whatever the
    legitimate NULL means. Here that meaning is "USDA did not report this lock-week" - an entirely
    ordinary thing for this column to say, with 108 records already saying it on the three summary
    locks, which is exactly the camouflage a 109th would hide in.

    THE ERROR NAMES THE VALUE, because an exception that says only "bad tonnage" sends the operator
    back to the API to find out which record it was (CLAUDE.md § 13).
    """
    with pytest.raises(MalformedResponseError) as excinfo:
        usda_movements.movement_from(record(tons="about forty thousand"))
    assert "about forty thousand" in str(excinfo.value), (
        f"the refusal does not carry the value it refused: {excinfo.value}"
    )

    # The scalar parser refuses it too, so no caller can route around movement_from and get a
    # silent None.
    with pytest.raises(MalformedResponseError):
        usda_movements.parse_tons("about forty thousand")

    # A PRESENT BUT BLANK CELL IS A THIRD SPELLING NOTHING HAS MEASURED, and it raises rather than
    # joining the 108 legitimate gaps. This source omits the key when it reports nothing and
    # publishes an explicit 0 when nothing moved; a blank is neither, and it is the one condition
    # in this module argued rather than measured. If it fires live, measure those records first.
    for blank in ("", "   "):
        with pytest.raises(MalformedResponseError) as excinfo:
            usda_movements.movement_from(record(tons=blank))
        assert "blank" in str(excinfo.value).lower(), (
            f"a blank cell was refused for the wrong reason: {excinfo.value}"
        )


def test_null_tons_is_not_coalesced_to_zero():
    """Test 4. The NULL survives all the way to the parameters handed to the driver.

    Asserted at the WRITE boundary rather than only at the parser, because that is where the
    coalesce would do its damage and it is a different line of code: `movement.tons or 0` in the
    parameter list passes every parser test in this file while writing a fabricated zero to the
    database. CLAUDE.md § 2 theme 2 - check across the boundary where the bug would live.

    The zero and the NULL go in together, so an implementation cannot satisfy this by mapping
    everything to one of them.
    """
    captured = []

    class RecordingConn:
        def execute(self, sql, params=None):
            captured.append((sql, params))
            return self

        def fetchall(self):
            return [(1,)]

    usda_movements.upsert_movements(
        RecordingConn(),
        usda_movements.parse_movements(
            [
                record(published_date="2026-08-01T00:00:00.000", tons="0"),
                record(published_date="2026-07-25T00:00:00.000", tons=...),
            ]
        ),
    )

    assert len(captured) == 1, f"expected one batched statement, got {len(captured)}"
    sql, params = captured[0]
    tonnages = params[3::4]
    assert tonnages == [Decimal("0"), None], (
        f"the parameters carry {tonnages!r}. A NULL coalesced to 0 asserts that nothing moved "
        f"through a lock nobody surveyed; a 0 coalesced to NULL erases a published measurement "
        f"(migration 0018)."
    )

    # And the change detection has to tell them apart too: `NULL = 0` is NULL in SQL, so a plain
    # comparison would read a correction from unreported to reported-zero as "no change" and drop
    # it silently. Verified behaviourally against a real database in the integration tier.
    assert "IS DISTINCT FROM" in sql


def test_lock_commodity_and_week_ending_still_use_required_field():
    """Test 5. The three key fields are required; only the measure is optional.

    The asymmetry is deliberate and is the thing 0018 must not erode: a row that cannot be keyed
    can never be corrected or superseded, while a row with no measure is an ordinary unreported
    week. Making `lock` optional alongside `tons` is the plausible-looking symmetry that would
    write unkeyable rows and report success.
    """
    for field in ("lock", "week_ending", "commodity"):
        incomplete = record()
        del incomplete[usda_movements.FIELDS[field]]
        with pytest.raises(MalformedResponseError) as excinfo:
            usda_movements.movement_from(incomplete)
        message = str(excinfo.value)
        assert usda_movements.FIELDS[field] in message, (
            f"the refusal for a missing {field} does not name the source field: {message}"
        )
        assert "KEYS THE ROW" in message, (
            f"a missing {field} was refused by something other than required_field, so the key "
            f"field may have been made optional: {message}"
        )

    # The measure is the ONLY optional field, asserted positively so this test fails if the
    # asymmetry is removed from the other direction too.
    assert usda_movements.movement_from(record(tons=...)).tons is None


def test_the_movements_fixture_carries_one_absent_and_one_zero_record(socrata_body):
    """Test 9. The fixture guard, so neither case can quietly drift out.

    Every parser test above is only as good as the shape it runs against: a fixture that lost its
    zero row would let a zero-filtering implementation pass this whole file, and a fixture that
    lost its tons-absent row would do the same for a coalescing one. Both are asserted in the RAW
    JSON as well as after parsing, because the raw shape is the claim about what USDA sends.

    The absent record is modelled on the measurement: `AK Lock 1` (71 of the 108), June 2015 (96
    of 108 fall in 2015-2016, and the gap is flat across months rather than seasonal).
    """
    raw = json.loads(socrata_body("movements"))

    no_key = [r for r in raw if "tons" not in r]
    assert len(no_key) == 1, (
        f"the fixture carries {len(no_key)} record(s) with no `tons` key; exactly one is required, "
        f"or a coalescing implementation passes every test in this file"
    )
    assert (no_key[0]["lock"], no_key[0]["year"]) == ("AK Lock 1", "2015")

    zeros = [r for r in raw if r.get("tons") == "0"]
    assert len(zeros) == 1, (
        f"the fixture carries {len(zeros)} record(s) with an explicit zero tonnage; exactly one is "
        f"required, or a zero-filtering implementation passes every test in this file"
    )

    # And both survive the parser, distinguishable, out of the same page.
    movements = usda_movements.parse_movements(raw)
    assert len(movements) == 4
    assert [m for m in movements if m.tons is None][0].lock == "AK Lock 1"
    assert [m for m in movements if m.tons == 0][0].week_ending == ZERO_WEEK
    # And the plural lock string arrived from the fixture untouched.
    assert "MS Locks 27" in {m.lock for m in movements}


def test_movements_completeness_report_counts_zero_and_null_separately(socrata_body):
    """Test 8. Three counts per lock, never two (0018 decision 3).

    ROWS LANDED, ROWS REPORTED ZERO, AND ROWS NOT REPORTED. Collapsing the last two into one "no
    data" figure would hide precisely the distinction this commit exists to preserve, in the one
    output a human actually reads - and it would hide it while looking like a tidier report.

    The figures are asserted in the RENDERED TEXT as well as in the structure, because a summary
    that computes three numbers and prints two is the same as computing two: nobody reads a dict
    from a CLI run.
    """
    from app.ingest import usda_backfill

    rows = usda_movements.parse_movements(
        [
            record(lock="MS Locks 27", published_date="2026-08-08T00:00:00.000", tons="136400"),
            record(lock="MS Locks 27", published_date="2026-08-01T00:00:00.000", tons="0"),
            record(lock="MS Locks 27", published_date="2026-07-25T00:00:00.000", tons=...),
            record(lock="IL La Grange", published_date="2026-08-08T00:00:00.000", tons="0"),
        ]
    )

    summary = usda_backfill.completeness_by_lock(rows)
    assert summary == [
        ("IL La Grange", 1, 1, 0),
        ("MS Locks 27", 3, 1, 1),
    ], (
        f"the movements completeness summary is {summary}. It must carry rows landed, rows "
        f"REPORTED ZERO, and rows NOT REPORTED as three separate counts."
    )

    rendered = usda_backfill.describe(
        {
            "dataset_key": "lock_movements",
            "horizon": None,
            "records_received": 4,
            "rows_written": 4,
            "first_period": date(2026, 7, 25),
            "last_period": date(2026, 8, 8),
            "seeded_first_period": None,
            "seeded_last_period": None,
            "seeded_row_count": None,
            "completeness": [],
            "lock_completeness": summary,
            "short_of_seeded_count": False,
        }
    )

    assert "MS Locks 27" in rendered and "IL La Grange" in rendered
    # The two populations must appear as two numbers on the lock that has one of each. A combined
    # figure would print `2` here for MS Locks 27 and read as perfectly reasonable.
    lock_line = next(line for line in rendered.splitlines() if "MS Locks 27" in line)
    assert "1 reported zero" in lock_line and "1 not reported" in lock_line, (
        f"the report does not separate the two populations for the lock that carries one of "
        f"each - a combined figure would print `2` here and read as perfectly reasonable:\n"
        f"{rendered}"
    )

    # And a rates backfill, whose rows have no lock concept, prints no table of zeros.
    assert usda_backfill.completeness_by_lock([]) == []

    # The real fixture, end to end: one zero and one gap, counted apart.
    from_fixture = usda_backfill.completeness_by_lock(
        usda_movements.parse_movements(json.loads(socrata_body("movements")))
    )
    assert dict((lock, (zeros, nulls)) for lock, _landed, zeros, nulls in from_fixture) == {
        "AK Lock 1": (0, 1),
        "IL La Grange": (0, 0),
        "MS Locks 27": (1, 0),
    }


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_rows_with_null_tons_are_still_written(migrated_db):
    """Test 6. A row whose measure is legitimately absent still lands (CLAUDE.md § 16).

    THE ROW IS THE STATEMENT. Skipping it makes the absence invisible to everything downstream:
    the series simply has no June 2015 at AK Lock 1, and nothing can distinguish that from an
    ingest that failed to fetch it. A NULL row states the gap; a missing row hides it.

    This is asserted against the database rather than the parser because a skip is as easily
    written in the upsert (`if movement.tons is None: continue`) as in the parse, and only the
    landed row proves neither happened.
    """
    written = usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements(
            [
                record(lock="AK Lock 1", published_date="2015-06-06T00:00:00.000", tons=...),
                record(lock="OH Olmsted", published_date="2015-06-06T00:00:00.000", tons=None),
                record(lock="MS Locks 27", published_date="2015-06-06T00:00:00.000", tons="61500"),
            ]
        ),
    )
    migrated_db.commit()

    assert written == 3, (
        f"{written} of 3 rows were written; the two unreported weeks were skipped rather than "
        f"stored as NULL rows"
    )

    landed = migrated_db.execute(
        "SELECT lock FROM lock_movements WHERE tons IS NULL ORDER BY lock"
    ).fetchall()
    assert landed == [("AK Lock 1",), ("OH Olmsted",)], (
        f"the unreported lock-weeks did not land as NULL rows: {landed}. Both spellings of "
        f"'not reported' - an absent key and an explicit null - must produce a row."
    )


@pytest.mark.integration
def test_zero_and_null_tons_are_distinguishable_after_a_round_trip(migrated_db):
    """Test 7. One of each written, both read back, and they differ.

    THE DATABASE IS WHERE THE DISTINCTION HAS TO SURVIVE, because that is where Phase 5's features
    read it from. A parser that keeps them apart and an upsert that flattens them on the way in
    look identical from Python.

    Both directions are asserted from the values themselves rather than from `count(*)` filters,
    so this fails whichever way the collapse runs: 0 stored as NULL, or NULL stored as 0.
    """
    usda_movements.upsert_movements(
        migrated_db,
        usda_movements.parse_movements(
            [
                record(lock="MS Locks 27", published_date="2026-08-01T00:00:00.000", tons="0"),
                record(lock="MS Locks 27", published_date="2026-07-25T00:00:00.000", tons=...),
            ]
        ),
    )
    migrated_db.commit()

    rows = dict(
        migrated_db.execute(
            "SELECT week_ending, tons FROM lock_movements WHERE lock = 'MS Locks 27'"
            " ORDER BY week_ending"
        ).fetchall()
    )

    reported_zero = rows[date(2026, 8, 1)]
    not_reported = rows[date(2026, 7, 25)]

    assert reported_zero == Decimal("0"), (
        f"the published zero came back as {reported_zero!r}. 8,218 records say 'none moved' this "
        f"way; storing it as NULL erases a measurement (migration 0018)."
    )
    assert not_reported is None, (
        f"the unreported week came back as {not_reported!r}. Coalescing it to 0 asserts that no "
        f"grain moved through the Mississippi's main southbound gate that week - a fabricated "
        f"zero in the most load-bearing series in the dataset."
    )
    assert reported_zero != not_reported, (
        "the two came back identical, so nothing downstream can tell a surveyed zero from a "
        "reporting gap"
    )


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
