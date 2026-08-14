"""The barge-rate target: its key, its unit, and its location vocabulary.

The field-map decisions are in test_usda_field_maps.py; this file is about what the TABLE does
with what the parser produced.

  * `horizon` IS PART OF THE KEY, and its value comes from which of the three sibling datasets a
    row was read from. Without it the same week's nearby and forward rates overwrite each other
    and the survivor is whichever arrived last.
  * `pct_of_tariff` IS STORED AS PUBLISHED. Dividing by 100 in ingest is a modelling decision two
    layers from where such decisions belong, and its symptom is a plausible-looking chart.
  * `location` IS STORED VERBATIM AND GUARDED BY A TRIPWIRE. An eighth value is a loud insert
    failure, never a silent new series.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.ingest import usda_rates
from app.ingest.socrata_client import MalformedResponseError

NEARBY = "barge_rates_nearby"
ONE_MONTH = "barge_rates_1month"
THREE_MONTH = "barge_rates_3month"


def record(
    location="Cairo-Memphis",
    published_date="2026-08-11T00:00:00.000",
    rate="112.5",
    rate_month=None,
) -> dict:
    """A record in the shape USDA actually publishes (measured 2026-08-14).

    `rate_month` is present only when asked for, which is how the two forward datasets differ from
    the nearby one - the field is absent rather than empty.
    """
    built = {
        usda_rates.FIELDS["week_ending"]: published_date,
        "week": "32",
        "month": "8",
        "year": "2026",
        usda_rates.FIELDS["location"]: location,
    }
    # `rate=...` omits the key entirely, which is how USDA publishes a closure week - 774 of
    # 8,260 nearby records carry no `rate` field at all (migration 0017).
    if rate is not ...:
        built[usda_rates.FIELDS["pct_of_tariff"]] = rate
    if rate_month is not None:
        built[usda_rates.FIELDS["rate_month"]] = rate_month
    return built


# ---------------------------------------------------------------------------------------------
# Unit tier.
# ---------------------------------------------------------------------------------------------


def test_pct_of_tariff_is_stored_exactly_as_published():
    """112.5 parses as 112.5. Not 1.125, not 113, not 112.49999999999999.

    THE PUBLISHED UNIT IS THE FACT. A percent of tariff divided by 100 in ingest is a ratio stored
    where the schema and every consumer document a percent - two orders of magnitude out, in a
    direction that looks entirely reasonable on an unlabelled chart, and impossible to detect
    downstream because both versions are smooth positive series.

    Decimal rather than float for the same reason the column is `numeric`: 1050.10 is not exactly
    representable in binary, and a numeric column fed a float inherits the artefact.
    """
    assert usda_rates.parse_rate("112.5") == Decimal("112.5")
    assert usda_rates.parse_rate("112.5") != Decimal("1.125"), "the rate was divided by 100"
    assert usda_rates.parse_rate("1050") == Decimal("1050")

    # Exactness, stated as a string comparison so a float round-trip cannot pass it.
    assert str(usda_rates.parse_rate("1050.10")) == "1050.10", (
        "the published digits were not preserved; a float somewhere on this path is rounding them"
    )
    assert usda_rates.rate_from(
        record(rate="112.5"), dataset_key=NEARBY
    ).pct_of_tariff == Decimal("112.5")

    # An empty or non-numeric rate raises rather than becoming 0 or NULL. A rate of zero percent
    # of tariff is not a thing that happens; it is what an empty field becomes when coerced.
    for bad in ("", "  ", None, "n/a", "0"):
        with pytest.raises(MalformedResponseError):
            usda_rates.parse_rate(bad)


def test_required_field_still_raises_for_date_and_location():
    """The tripwire is NARROWED to the key fields, not removed. 0017 test 5.

    THE TRIPWIRE IS WHAT MADE THE 0016 CORRECTION CHEAP. Every field name Phase 4 assumed was
    wrong, and because every read went through `required_field`, that arrived as an exception
    naming the fields a record actually carries rather than as a table of NULLs - CLAUDE.md § 2's
    theme 1, caught at the first record instead of after a backfill.

    0017 moves `rate` off it, because absence there is measured and legitimate. THE RISK IS THAT
    THE NARROWING KEEPS GOING: `date` and `location` KEY the row, so their absence can never be a
    fact about the river - a record with no location is not a closure week, it is a record nothing
    can ever correct or supersede.
    """
    for field in ("week_ending", "location"):
        incomplete = record()
        del incomplete[usda_rates.FIELDS[field]]

        with pytest.raises(MalformedResponseError) as excinfo:
            usda_rates.rate_from(incomplete, dataset_key=NEARBY)

        message = str(excinfo.value)
        assert usda_rates.FIELDS[field] in message
        assert "Fields present" in message, (
            f"the error does not say what the record does carry, which is what makes a wrong "
            f"field mapping a two-minute fix: {message}"
        )

    # And the same for a forward dataset's rate_month, which is required on the datasets that
    # publish it - a forward row silently losing it would write a NULL indistinguishable from
    # nearby's legitimate one.
    forward = record(rate_month="9")
    del forward[usda_rates.FIELDS["rate_month"]]
    with pytest.raises(MalformedResponseError):
        usda_rates.rate_from(forward, dataset_key=ONE_MONTH)


def test_the_resume_point_is_read_per_horizon():
    """`latest_week` filters by horizon when given one.

    THE THREE DATASETS ARE THREE INDEPENDENT PUBLICATIONS. A corridor-wide MAX(week_ending) would
    let the freshest of the three decide where the other two resume, and a dataset that fell a
    month behind would never be asked for the weeks it is missing - the poll would report success
    every week over a series with a permanent hole in it.

    Asserted on the SQL the function issues, at the unit tier, because the behaviour is a WHERE
    clause and its absence is invisible in any result that happens to be uniform.
    """

    class RecordingConn:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append((sql, params))
            return self

        def fetchone(self):
            return (date(2026, 8, 11),)

    conn = RecordingConn()
    usda_rates.latest_week(conn, "1_month")
    sql, params = conn.statements[-1]
    assert "WHERE horizon" in sql and params == ("1_month",), (
        f"latest_week did not restrict to the horizon: {sql!r} {params!r}"
    )

    conn = RecordingConn()
    usda_rates.latest_week(conn)
    assert "WHERE horizon" not in conn.statements[-1][0]


def test_completeness_report_counts_null_rates_per_location():
    """Rows landed, rows with no published rate, and the percentage, per location. 0017 test 10.

    VISIBILITY, NOT ENFORCEMENT (migration 0017, decision 4). The rate is legitimately absent 9%
    of the time overall and 36% of the time at Twin Cities, so a constraint or an alert on it would
    either fire constantly or be loosened until it never fired. A printed number does what neither
    can: if USDA's publication behaviour changes, the shape of this table changes with it and the
    next person to run a backfill sees it.

    The count is asserted in the RENDERED REPORT as well as in the structure, because a summary
    that computes the figure and does not print it is the same as not computing it - nobody reads
    a dict from a CLI run.
    """
    from app.ingest import usda_backfill

    rows = usda_rates.parse_rates(
        [
            record(location="Twin Cities", published_date="2026-01-05T00:00:00.000", rate=...),
            record(location="Twin Cities", published_date="2026-01-12T00:00:00.000", rate=...),
            record(location="Twin Cities", published_date="2026-08-11T00:00:00.000", rate="925"),
            record(location="Cairo-Memphis", published_date="2026-01-05T00:00:00.000", rate="600"),
        ],
        dataset_key=NEARBY,
    )

    summary = usda_backfill.completeness_by_location(rows)

    assert summary == [
        ("Cairo-Memphis", 1, 0, 0.0),
        ("Twin Cities", 3, 2, pytest.approx(66.666, abs=0.01)),
    ], f"the completeness summary is {summary}"

    rendered = usda_backfill.describe(
        {
            "dataset_key": "barge_rates_nearby",
            "horizon": "nearby",
            "records_received": 4,
            "rows_written": 4,
            "first_period": date(2026, 1, 5),
            "last_period": date(2026, 8, 11),
            "seeded_first_period": None,
            "seeded_last_period": None,
            "seeded_row_count": None,
            "completeness": summary,
            "short_of_seeded_count": False,
        }
    )

    assert "Twin Cities" in rendered and "Cairo-Memphis" in rendered
    assert "2 with no rate" in rendered, (
        f"the report does not print the per-location null count, so the one thing this table is "
        f"for is invisible in a run:\n{rendered}"
    )
    assert "66.7" in rendered, f"the percentage is not rendered:\n{rendered}"
    # And a movements backfill, whose rows have no rate concept, prints no table of zeros.
    assert usda_backfill.completeness_by_location([]) == []


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_rates_key_is_location_week_ending_horizon(migrated_db):
    """One location, one week, three horizons: three rows, not one. Test 14.

    THE FAILURE THIS CATCHES IS SILENT AND TOTAL. Keyed without `horizon`, an upsert over one
    week's publication writes three rows onto one key and keeps whichever arrived last - which is
    not deterministic. The resulting series is mostly nearby rates with occasional forward ones
    mixed in, every aggregate over it is wrong, and nothing about it says so.

    Built from three DATASET KEYS rather than three labels, because that is now where a horizon
    comes from: this test would not compile against a parser that read the horizon from a record.
    """
    week = date(2026, 8, 11)
    written = usda_rates.upsert_rates(
        migrated_db,
        [
            *usda_rates.parse_rates([record(rate="1420")], dataset_key=NEARBY),
            *usda_rates.parse_rates(
                [record(rate="1050", rate_month="9")], dataset_key=ONE_MONTH
            ),
            *usda_rates.parse_rates(
                [record(rate="780.25", rate_month="11")], dataset_key=THREE_MONTH
            ),
        ],
    )
    migrated_db.commit()

    assert written == 3, f"{written} row(s) written for three distinct horizons"

    rows = migrated_db.execute(
        "SELECT horizon, pct_of_tariff, rate_month FROM barge_rates"
        " WHERE location = %s AND week_ending = %s ORDER BY horizon",
        ("Cairo-Memphis", week),
    ).fetchall()

    assert rows == [
        ("1_month", Decimal("1050"), 9),
        ("3_month", Decimal("780.25"), 11),
        ("nearby", Decimal("1420"), None),
    ], f"the three horizons did not survive as three rows: {rows}"

    # And the stored value is the published one, read back from the database rather than from the
    # dataclass - the round trip is where a float or a rounding cast would show.
    stored = migrated_db.execute(
        "SELECT pct_of_tariff::text FROM barge_rates WHERE horizon = '3_month'"
    ).fetchone()[0]
    assert stored == "780.25", f"the database holds {stored!r}, not the published 780.25"


@pytest.mark.integration
def test_a_nearby_rate_month_is_rejected_by_the_database(migrated_db):
    """The rate_month/horizon pairing is enforced by the database, not only by the parser.

    Migration 0016's CHECK, from both sides: a synthesized rate_month on a nearby row and a
    forward row that lost its own. Held here because the parser guard and the constraint guard the
    same decision at different layers, and the layer that survives a hand-written INSERT is this
    one.
    """
    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff, rate_month)"
            " VALUES ('Twin Cities', DATE '2026-08-11', 'nearby', 925, 9)"
        )
    migrated_db.rollback()

    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff, rate_month)"
            " VALUES ('Twin Cities', DATE '2026-08-11', '1_month', 925, NULL)"
        )
    migrated_db.rollback()


# THE SEVEN PUBLISHED LOCATIONS, MEASURED 2026-08-14 at 1,180 rows each.
#
# `Lower Illinois`, NOT `Illinois River`. The handoff document said "Illinois", migration 0016
# seeded `Illinois River` on the strength of it and said in writing that five of its seven values
# were unmeasured and that the API would win. It did. 0017 is that promise being kept.
MEASURED_LOCATIONS = {
    "Cairo-Memphis",
    "Cincinnati",
    "Lower Illinois",
    "Lower Ohio",
    "Mid-Mississippi",
    "St. Louis",
    "Twin Cities",
}


@pytest.mark.integration
def test_the_seven_locations_are_exactly_the_measured_set(migrated_db):
    """The CHECK admits exactly the seven strings USDA publishes. 0017 test 8.

    EXACT SET EQUALITY, READ OUT OF THE CONSTRAINT ITSELF rather than probed by inserting. A test
    that only inserted the seven would pass just as happily against a constraint that also admits
    `Illinois River` - and a stale value left in the list is not harmless: it is a spelling that
    can never arrive, sitting in the one place a reader goes to learn what the vocabulary is.

    The first backfill attempt died here, which is the tripwire working exactly as designed: it
    refused to open an eighth series for a segment that already had one under another name.
    """
    definition = migrated_db.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'barge_rates_location_known'"
    ).fetchone()[0]

    admitted = set(re.findall(r"'([^']*)'", definition))
    assert admitted == MEASURED_LOCATIONS, (
        f"the location CHECK admits {sorted(admitted)}.\n"
        f"  Expected exactly the seven measured strings. `Illinois River` was the handoff's name "
        f"and USDA publishes `Lower Illinois` (migration 0017); where the two disagree the API "
        f"wins."
    )

    # And all seven really do insert - the constraint says what it means, and a list that was
    # right on paper but rejected a published value would stop the backfill dead.
    written = usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates(
            [record(location=name, rate="500") for name in sorted(MEASURED_LOCATIONS)],
            dataset_key=NEARBY,
        ),
    )
    migrated_db.commit()
    assert written == 7, (
        f"{written} of the seven published locations were accepted; the CHECK is rejecting a "
        f"value USDA publishes"
    )


@pytest.mark.integration
def test_an_unmeasured_location_is_still_rejected(migrated_db):
    """The tripwire survives the correction. 0017 test 9.

    THE CONSTRAINT IS A TRIPWIRE, NOT A VOCABULARY. Correcting one of its values is exactly the
    moment someone concludes the constraint is more trouble than it is worth - it has, after all,
    just cost a backfill run. It bought that run: without it the rows would have landed under a
    name nothing queries, and the corridor-wide views would have silently omitted a segment.

    The fix on failure is to measure the new string and add it in a NEW migration - which is what
    0017 is. Never to drop the constraint, and never to bend the arriving value to fit the list.
    """
    with pytest.raises(Exception) as excinfo:
        usda_rates.upsert_rates(
            migrated_db,
            usda_rates.parse_rates([record(location="Illinois River")], dataset_key=NEARBY),
        )
    migrated_db.rollback()
    assert "barge_rates_location_known" in str(excinfo.value), (
        f"the rejection did not come from the location tripwire: {excinfo.value}"
    )

    # A ONE-CHARACTER near-miss is refused too: an en dash where USDA publishes a hyphen. That is
    # the realistic shape of this failure - not an obviously new location, but a plausible-looking
    # variant that would otherwise open a second series for a segment that already has one.
    with pytest.raises(Exception):
        usda_rates.upsert_rates(
            migrated_db,
            usda_rates.parse_rates([record(location="Cairo–Memphis")], dataset_key=NEARBY),
        )
    migrated_db.rollback()
    assert "Cairo–Memphis" != "Cairo-Memphis"


@pytest.mark.integration
def test_rows_with_null_rates_are_still_written(migrated_db):
    """Three published records, one of them a closure week: THREE rows. 0017 test 6.

    THE ROW IS THE OBSERVATION. USDA publishes a segment-week with no `rate` key when the river is
    closed - 774 of 8,260 nearby records, 661 of them December to March. Skipping those rows would
    leave a series with no January at all, which is indistinguishable from an ingest that failed
    to fetch January, and Phase 5's seasonal baseline would fit a winter that never closes.

    The NULL is also read back FROM THE DATABASE here, not from the dataclass: `IS NULL` and
    `= 0` are what tell the two apart, and that is where Phase 5 will be reading from.
    """
    written = usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates(
            [
                record(published_date="2026-01-06T00:00:00.000", rate=...),
                record(published_date="2026-08-04T00:00:00.000", rate="601"),
                record(published_date="2026-08-11T00:00:00.000", rate="582.1428"),
            ],
            dataset_key=NEARBY,
        ),
    )
    migrated_db.commit()

    assert written == 3, (
        f"{written} of 3 rows written - the closure week was skipped. A week USDA published no "
        f"rate for is an observation about the river, not an absence of one (migration 0017)."
    )

    nulls = migrated_db.execute(
        "SELECT count(*) FROM barge_rates WHERE pct_of_tariff IS NULL"
    ).fetchone()[0]
    zeros = migrated_db.execute(
        "SELECT count(*) FROM barge_rates WHERE pct_of_tariff = 0"
    ).fetchone()[0]
    assert nulls == 1, f"{nulls} row(s) hold a NULL rate; the closure week was stored as something"
    assert zeros == 0, (
        "a NULL rate was coalesced to 0 in the database. Zero claims the freight was free that "
        "week, and every average over the series would be dragged toward it."
    )

    # A closure week that later gets a published rate is a genuine revision and must count as a
    # write. `IS DISTINCT FROM` is what makes it count: `NULL <> 582` is NULL, so a plain
    # comparison would treat this as no change and the rate would never land.
    revised = usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates(
            [record(published_date="2026-01-06T00:00:00.000", rate="1200")], dataset_key=NEARBY
        ),
    )
    migrated_db.commit()
    assert revised == 1, (
        "a week revised from NO PUBLISHED RATE to a published one was not counted as a write"
    )


@pytest.mark.integration
def test_freshness_uses_max_week_ending_over_all_rows(migrated_db, database_url):
    """The newest row counts even when its rate is NULL. 0017 test 7.

    "ONLY COUNT ROWS THAT HAVE DATA" IS THE NATURAL-SOUNDING CHANGE THAT BREAKS THIS. In January
    both upper segments are closed, so the newest rows for those weeks carry NULL rates - and a
    freshness check reading `MAX(week_ending) WHERE rate IS NOT NULL` would report the table stale
    while ingest was perfectly correct, every week, all winter.

    That failure is worse than a false alarm: it fires for a whole season, gets muted, and the
    check is then not watching in the spring either.
    """
    from datetime import datetime, timezone

    from app import db
    from app.orchestration import heartbeat

    usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates(
            [
                # The older week has a rate; the NEWER one is a closure week.
                record(published_date="2025-12-29T00:00:00.000", rate="900"),
                record(
                    location="Twin Cities",
                    published_date="2026-01-05T00:00:00.000",
                    rate=...,
                ),
            ],
            dataset_key=NEARBY,
        ),
    )
    migrated_db.commit()

    entry = next(f for f in heartbeat.FRESHNESS if f.table == "barge_rates")
    with db.connection(database_url) as conn:
        newest = heartbeat.newest_row(conn, entry)

    assert newest == datetime(2026, 1, 5, tzinfo=timezone.utc), (
        f"freshness read {newest}, not the 2026-01-05 closure week. The check is filtering on a "
        f"non-null rate, so a legitimately closed river reads as staleness."
    )


@pytest.mark.integration
def test_the_backfill_reads_three_rates_datasets_and_checks_itself_against_the_seed(migrated_db):
    """One backfill per horizon, each comparing what it received against the seeded floor.

    NOT IN THE BRIEF'S TEST LIST, and here because live verification step 3 runs this code and
    nothing else did. The three-dataset wiring, the horizon coming from the key, and the
    truncation comparison are all new in this commit; shipping them unexercised would mean
    discovering a typo on the instance, in the one path a human runs by hand.

    THE TRUNCATION CHECK IS THE POINT (CLAUDE.md § 16). A pager that stops early reports success
    with a plausible row count, and comparing against the count the source reported at seed time
    is the cheapest thing that can tell the difference. It compares RECORDS RECEIVED rather than
    rows written, because a second run legitimately writes nothing.
    """
    import json

    from app.ingest import usda_backfill
    from app.ingest.socrata_client import SocrataClient

    assert set(usda_backfill.INGESTORS) == {
        NEARBY,
        ONE_MONTH,
        THREE_MONTH,
        "lock_movements",
    }, (
        f"the backfill offers {sorted(usda_backfill.INGESTORS)}; the rates series is three "
        f"datasets and `cost_indicators` has no ingest path"
    )

    def client_for(records):
        pages = [json.dumps(records), "[]"]
        return SocrataClient(lambda url, timeout=None: pages.pop(0))

    results = {}
    for key, rate_month in ((NEARBY, None), (ONE_MONTH, "9"), (THREE_MONTH, "11")):
        results[key] = usda_backfill.backfill(
            migrated_db,
            key,
            client=client_for([record(rate="500", rate_month=rate_month)]),
        )

    assert [results[k]["horizon"] for k in (NEARBY, ONE_MONTH, THREE_MONTH)] == [
        "nearby",
        "1_month",
        "3_month",
    ], "the backfill did not take each dataset's horizon from its key"

    stored = migrated_db.execute(
        "SELECT horizon, rate_month FROM barge_rates ORDER BY horizon"
    ).fetchall()
    assert stored == [("1_month", 9), ("3_month", 11), ("nearby", None)]

    # One record against a seeded floor of 8,260 is truncation, and the report says so rather
    # than reporting a successful read of one row.
    nearby = results[NEARBY]
    assert nearby["seeded_row_count"] == 8260
    assert nearby["short_of_seeded_count"] is True
    assert "SHORT" in usda_backfill.describe(nearby), (
        f"a run that returned 1 of 8,260 records did not report truncation: "
        f"{usda_backfill.describe(nearby)}"
    )

    # And the seed is untouched: a backfill that corrected its own starting assumption would
    # destroy the evidence it started from the wrong place (CLAUDE.md § 15).
    assert (
        migrated_db.execute(
            "SELECT first_period, last_period, source_row_count FROM usda_datasets"
            " WHERE dataset_key = %s",
            (NEARBY,),
        ).fetchone()
        == (date(2004, 1, 7), date(2026, 8, 11), 8260)
    )


@pytest.mark.integration
def test_a_revised_week_overwrites(migrated_db):
    """USDA republishes a week; the new number wins and is counted once.

    `DO NOTHING` is the trap: it makes reruns safe, passes every duplicate test, and freezes the
    first-published value permanently and silently (CLAUDE.md § 14). USDA revises as a matter of
    routine, so the frozen value would be a target series that quietly disagrees with the source.
    """
    first = usda_rates.upsert_rates(
        migrated_db, usda_rates.parse_rates([record(rate="1420")], dataset_key=NEARBY)
    )
    migrated_db.commit()
    assert first == 1

    # The same week, revised.
    revised = usda_rates.upsert_rates(
        migrated_db, usda_rates.parse_rates([record(rate="1455.5")], dataset_key=NEARBY)
    )
    migrated_db.commit()
    assert revised == 1, "a genuine revision was not counted as a write"

    value = migrated_db.execute(
        "SELECT pct_of_tariff::text FROM barge_rates WHERE location = %s AND week_ending = %s"
        " AND horizon = 'nearby'",
        ("Cairo-Memphis", date(2026, 8, 11)),
    ).fetchone()[0]
    assert value == "1455.5", (
        f"the revision did not land - the table holds {value!r}. DO NOTHING freezes the "
        f"first-published value forever, silently."
    )

    # A rerun over UNCHANGED data writes nothing and reports 0. `rows_written` means rows that
    # actually changed the database (CLAUDE.md § 4), and a plain DO UPDATE would report 1 here on
    # every run - a number large enough to look reassuring and meaning nothing.
    unchanged = usda_rates.upsert_rates(
        migrated_db, usda_rates.parse_rates([record(rate="1455.5")], dataset_key=NEARBY)
    )
    migrated_db.commit()
    assert unchanged == 0, (
        f"a rerun over unchanged data reported {unchanged} row(s) written; rows_written must "
        f"count rows that actually changed"
    )

    assert (
        migrated_db.execute("SELECT count(*) FROM barge_rates").fetchone()[0] == 1
    ), "the revision inserted a second row instead of replacing the first"

    # A REVISION TO rate_month ALONE also counts, on the forward datasets. The upsert compares the
    # pair, so a corrected quoted month with an unchanged rate is a genuine write - and comparing
    # only the rate would drop it silently.
    usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates([record(rate="900", rate_month="9")], dataset_key=ONE_MONTH),
    )
    migrated_db.commit()
    month_revised = usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates([record(rate="900", rate_month="10")], dataset_key=ONE_MONTH),
    )
    migrated_db.commit()
    assert month_revised == 1, (
        "a revision that changed only rate_month was not counted; the upsert is comparing the "
        "rate alone"
    )
