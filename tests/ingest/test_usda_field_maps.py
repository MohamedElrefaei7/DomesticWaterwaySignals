"""The USDA field maps, against the records USDA actually publishes.

MEASURED 2026-08-14. Every field name Phase 4 assumed was wrong, and this file exists so the next
wrong assumption fails here rather than on the instance. The records below are VERBATIM captures,
not shapes invented to match the parser - which is the only property that makes a field-map test
worth anything at all.

Three structural decisions live here, each with a shorter wrong version that passes every obvious
test:

  * THE HORIZON COMES FROM THE DATASET, NOT THE RECORD. USDA publishes three sibling datasets, one
    per horizon, with identical fields. A parser that looked for a horizon column would find
    nothing; one that defaulted would file three different facts under one key.
  * `rate_month` IS A PUBLISHED CALENDAR MONTH, NULL ON NEARBY ROWS. Not synthesized where the
    source omits it, and not converted into a months-ahead offset.
  * THE PUBLISHED VOCABULARIES ARE STORED VERBATIM, INCLUDING `MS Locks 27`. A normalization step
    breaks the join as missing weeks rather than as an error.
"""

import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from app.ingest import usda_movements, usda_rates
from app.ingest.socrata_client import MalformedResponseError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------------------------
# THE MEASURED RECORDS, VERBATIM. Do not tidy these into the parser's shape.
# ---------------------------------------------------------------------------------------------

NEARBY_SAMPLE = {
    "date": "2026-08-11T00:00:00.000",
    "week": "32",
    "month": "8",
    "year": "2026",
    "location": "Cairo-Memphis",
    "rate": "582.1428",
}

FORWARD_SAMPLE = {
    "date": "2026-08-11T00:00:00.000",
    "week": "32",
    "month": "8",
    "year": "2026",
    "location": "Twin Cities",
    "rate_month": "9",
    "rate": "925",
}

MOVEMENT_SAMPLE = {
    "date": "2026-08-08T00:00:00.000",
    "week": "31",
    "month": "8",
    "year": "2026",
    "commodity": "Corn",
    "lock": "IL La Grange",
    "tons": "136400",
}


# ---------------------------------------------------------------------------------------------
# Rates.
# ---------------------------------------------------------------------------------------------


def test_rates_field_map_matches_the_measured_sample():
    """Every mapped field resolves against a verbatim record, and the rate is unrounded. Test 1.

    THE PUBLISHED DIGITS ARE THE FACT. `582.1428` is four real decimal places, and both ways of
    losing them are one line: rounding to the two a percent "obviously" has, and parsing through a
    float on the way to a numeric column. Both leave a smooth positive series that looks entirely
    correct on a chart.
    """
    rate = usda_rates.rate_from(NEARBY_SAMPLE, dataset_key="barge_rates_nearby")

    assert rate.location == "Cairo-Memphis"
    assert rate.week_ending == date(2026, 8, 11)
    assert rate.horizon == "nearby"
    assert rate.pct_of_tariff == Decimal("582.1428")

    # As a string, so a float round-trip cannot satisfy it and neither can a rounded Decimal.
    assert str(rate.pct_of_tariff) == "582.1428", (
        f"the published digits were not preserved: {rate.pct_of_tariff!r}. Rounding a percent to "
        f"two places in ingest is a modelling decision in the wrong layer (CLAUDE.md § 16)."
    )

    # And the map itself resolves against the record rather than against another dict: a FIELDS
    # entry naming a column USDA does not publish is the failure this whole file exists for.
    for column, published in usda_rates.FIELDS.items():
        if column == "rate_month":
            continue  # forward datasets only - asserted in test 2.
        assert published in NEARBY_SAMPLE, (
            f"FIELDS maps {column!r} to the published field {published!r}, which is not in a "
            f"measured record. Fields present: {sorted(NEARBY_SAMPLE)}"
        )


def test_future_rate_sample_yields_rate_month():
    """The forward dataset's `rate_month` parses as the published month. Test 2.

    9 AGAINST A PUBLICATION MONTH OF 8, AND IT IS STORED AS 9. Converting it to a months-ahead
    offset (9 - 8 = 1) is the tempting derivation and it is wrong here for two reasons: it is a
    modelling decision in the ingest layer, and it destroys the published value - a rate_month of
    11 quoted in August is an offset of 3 on the 3-month dataset but the arithmetic stops being
    obvious the moment a December quote crosses a year boundary.
    """
    rate = usda_rates.rate_from(FORWARD_SAMPLE, dataset_key="barge_rates_1month")

    assert rate.rate_month == 9, (
        f"rate_month is {rate.rate_month!r}. The published value is 9 - the calendar month the "
        f"quote applies to. An offset (1) means ingest derived something."
    )
    assert rate.horizon == "1_month"
    assert rate.pct_of_tariff == Decimal("925")
    assert rate.location == "Twin Cities"

    # The 3-month sibling, same shape, month 11 against the same publication month of 8. Held here
    # so an implementation that hardcoded "publication month + 1" cannot pass on the 1-month
    # dataset alone.
    three = usda_rates.rate_from(
        dict(FORWARD_SAMPLE, rate_month="11", rate="1010"), dataset_key="barge_rates_3month"
    )
    assert three.rate_month == 11 and three.horizon == "3_month"

    # A forward record that LOST its rate_month raises rather than writing a NULL. In this one
    # column NULL is a legitimate value (nearby), so a silent None here would be indistinguishable
    # from correct data.
    without = {k: v for k, v in FORWARD_SAMPLE.items() if k != "rate_month"}
    with pytest.raises(MalformedResponseError) as excinfo:
        usda_rates.rate_from(without, dataset_key="barge_rates_1month")
    assert "rate_month" in str(excinfo.value)


def test_nearby_rows_store_null_rate_month():
    """The nearby dataset has no rate_month, and none is invented. Test 3.

    NULL HERE IS CORRECT AND COMPLETE, NOT MISSING DATA. The nearby rate is a rate for now; there
    is no quoted month to publish. Synthesizing one - from the publication month, from the `month`
    field the record does carry - would put a number in the database USDA never said, in a column
    whose whole purpose is to carry what USDA did say.
    """
    rate = usda_rates.rate_from(NEARBY_SAMPLE, dataset_key="barge_rates_nearby")

    assert rate.rate_month is None, (
        f"a nearby row carries rate_month={rate.rate_month!r}. The nearby dataset publishes no "
        f"such field; a value here was synthesized from something else in the record."
    )

    # The record DOES carry `month` = "8". That is the publication month and it is not a quoted
    # month - this assertion is here because "the month is right there" is exactly the reasoning
    # that would produce the bug.
    assert NEARBY_SAMPLE["month"] == "8"
    assert rate.rate_month != 8


def test_horizon_mapping_is_total_and_injective():
    """Every dataset key has exactly one horizon and every horizon exactly one key. Test 4.

    THE GUARD AGAINST A FOURTH RATES DATASET LANDING SILENTLY. A mapping that is merely
    "sufficient" - covering the keys in use today - lets a new sibling dataset default into an
    existing horizon, which writes two different facts onto one primary key. Nothing downstream
    can see that: both are plausible weekly percent series.

    Injectivity matters as much as totality. Two keys mapping to one horizon would make the second
    dataset's rows overwrite the first's on every upsert, and `rows_written` would look busy.
    """
    mapping = usda_rates.HORIZON_BY_DATASET_KEY

    assert set(mapping) == set(usda_rates.DATASET_KEYS), (
        f"the keys the job iterates ({sorted(usda_rates.DATASET_KEYS)}) are not the keys the "
        f"mapping covers ({sorted(mapping)}) - one of the two is a second list of the same fact"
    )

    assert len(set(mapping.values())) == len(mapping), (
        f"the horizon mapping is not injective: {mapping}. Two dataset keys sharing a horizon "
        f"means one dataset's rows overwrite the other's on every run."
    )

    # Against the vocabulary the database will actually accept (migration 0014's CHECK). A horizon
    # this mapping produces that the constraint rejects is a job that fails on its first insert;
    # one the constraint accepts and the mapping never produces is a series nothing fills.
    assert set(mapping.values()) == {"nearby", "1_month", "3_month"}, (
        f"the mapping produces {sorted(set(mapping.values()))}, which is not the horizon "
        f"vocabulary barge_rates_horizon_known admits"
    )
    assert usda_rates.HORIZONS == frozenset({"nearby", "1_month", "3_month"})

    # And every key resolves through the public accessor, which is the only way the rest of the
    # module obtains a horizon.
    for key, horizon in mapping.items():
        assert usda_rates.horizon_for(key) == horizon

    # THE HORIZON IS NEVER READ OUT OF A RECORD, asserted against a record that carries a
    # contradictory horizon field of its own. USDA publishes no such field today - which is
    # exactly why "just read it from the record" is a change someone could make against a future
    # dataset that does, silently reassigning every row this project has.
    poisoned = dict(NEARBY_SAMPLE, horizon="3 Month Forward", rate_month="11")
    assert usda_rates.rate_from(poisoned, dataset_key="barge_rates_nearby").horizon == "nearby", (
        "the horizon came from the record rather than from the dataset key. It is a property of "
        "which dataset a row was read from (migration 0016, decision 1)."
    )


def test_an_unknown_rates_dataset_key_raises():
    """An unmapped dataset key is an error, never a default. Test 5.

    `.get(key, "nearby")` is the one-line version of this bug. It reads as a sensible fallback and
    it silently files a horizon nobody decided under an existing series.
    """
    with pytest.raises(usda_rates.UnknownRatesDatasetError) as excinfo:
        usda_rates.horizon_for("barge_rates_6month")

    message = str(excinfo.value)
    assert "barge_rates_6month" in message
    assert "NOT DEFAULTED" in message, (
        f"the error does not say that defaulting was refused, which is the decision a reader "
        f"needs to find here: {message}"
    )
    # It lists what it does know, so the fix is visible from the traceback.
    assert "barge_rates_nearby" in message

    # And the parser refuses too - there is no route to a BargeRate that skips the mapping.
    with pytest.raises(usda_rates.UnknownRatesDatasetError):
        usda_rates.rate_from(NEARBY_SAMPLE, dataset_key="barge_rates_6month")


def test_the_captured_rates_fixtures_parse_into_rates(socrata_body):
    """All three captured pages parse, each into its own horizon.

    Guards the fixtures as much as the parser: a fixture that drifted back toward the old assumed
    shape would make every test above pass against records the live service does not send.
    """
    seen = {}
    for name, key in (
        ("rates_nearby", "barge_rates_nearby"),
        ("rates_1month", "barge_rates_1month"),
        ("rates_3month", "barge_rates_3month"),
    ):
        records = json.loads(socrata_body(name))
        rates = usda_rates.parse_rates(records, dataset_key=key)

        assert len(rates) == len(records)
        assert {r.horizon for r in rates} == {usda_rates.horizon_for(key)}
        assert all(isinstance(r.week_ending, date) for r in rates)
        seen[key] = rates

    assert all(r.rate_month is None for r in seen["barge_rates_nearby"])
    assert all(r.rate_month == 9 for r in seen["barge_rates_1month"])
    assert all(r.rate_month == 11 for r in seen["barge_rates_3month"])


# ---------------------------------------------------------------------------------------------
# Movements.
# ---------------------------------------------------------------------------------------------


def test_movements_field_map_matches_the_measured_sample():
    """`commodity`, `lock` and `tons` all resolve against a verbatim record. Test 6."""
    movement = usda_movements.movement_from(MOVEMENT_SAMPLE)

    assert movement.commodity == "Corn"
    assert movement.lock == "IL La Grange"
    assert movement.tons == Decimal("136400")
    assert movement.week_ending == date(2026, 8, 8)

    for column, published in usda_movements.FIELDS.items():
        assert published in MOVEMENT_SAMPLE, (
            f"FIELDS maps {column!r} to {published!r}, which is not in a measured record. Fields "
            f"present: {sorted(MOVEMENT_SAMPLE)}"
        )


def test_movements_parser_has_no_direction_or_barges_concept():
    """Neither name appears anywhere in the parsed output or the field map. Test 7.

    THE SOURCE PUBLISHES NEITHER. There is no direction dimension - the dataset is downbound-only
    by construction - and no barge count at all, only tons.

    A `barges` column that is ALWAYS NULL is worse than an absent one: it looks like data, and
    every query that filters on it returns nothing forever with nothing to say why. Asserted on
    the parsed output's own keys rather than by grepping the module, because the thing that must
    not exist is a field in what this parser produces.
    """
    parsed = asdict(usda_movements.movement_from(MOVEMENT_SAMPLE))

    for forbidden in ("direction", "barges"):
        assert forbidden not in parsed, (
            f"the parsed movement carries {forbidden!r}: {sorted(parsed)}. The source publishes "
            f"no such field, so any value here was invented (migration 0016)."
        )
        assert forbidden not in usda_movements.FIELDS, (
            f"the field map still names {forbidden!r}; USDA does not publish it"
        )
        assert forbidden not in usda_movements.UPSERT_SQL, (
            f"the upsert still writes {forbidden!r}, which is not a column in lock_movements"
        )

    assert set(parsed) == {"lock", "week_ending", "commodity", "tons"}, (
        f"the parsed movement's fields are {sorted(parsed)}; the published record carries a lock, "
        f"a date, a commodity and tons"
    )


def test_lock_value_is_stored_verbatim_including_the_plural():
    """`MS Locks 27` round-trips unchanged through the parser. Test 8.

    THE PLURAL IS USDA'S AND IT IS STABLE. `MS Locks 27` sits beside `MS Lock 15`, `MS Lock 25`
    and `MS Lock 26`, all singular, and 4,928 rows - the joint-largest lock in the dataset - carry
    it.

    "Normalize the inconsistency" is the tidy this test refuses. A mapping that does not cover an
    arriving value fails as MISSING WEEKS, not as an unmapped value, and missing weeks read like a
    source problem and get investigated as one.
    """
    published = [
        "AK Lock 1",
        "IL La Grange",
        "MS Lock 15",
        "MS Lock 25",
        "MS Lock 26",
        "MS Locks 27",
        "OH Olmsted",
    ]
    parsed = usda_movements.parse_movements(
        [dict(MOVEMENT_SAMPLE, lock=name) for name in published]
    )

    assert [m.lock for m in parsed] == published, (
        f"a published lock string was altered in transit: {[m.lock for m in parsed]}. Stored "
        f"verbatim means verbatim - no singularizing, no title-casing, no internal id."
    )

    # Pointed directly at the plural, so the assertion above cannot pass by accident on a list
    # that happens to be untouched for other reasons.
    plural = usda_movements.movement_from(dict(MOVEMENT_SAMPLE, lock="MS Locks 27"))
    assert plural.lock == "MS Locks 27"
    assert plural.lock != "MS Lock 27", "the plural was normalized to the singular"

    # Surrounding whitespace is still stripped: that is transport noise, not vocabulary.
    assert usda_movements.movement_from(dict(MOVEMENT_SAMPLE, lock="  MS Locks 27 ")).lock == (
        "MS Locks 27"
    )


# ---------------------------------------------------------------------------------------------
# The date field, shared by both parsers.
# ---------------------------------------------------------------------------------------------


def test_week_ending_column_maps_from_the_source_date_field():
    """Both field maps translate the source's `date` into our `week_ending`. Test 9.

    A DELIBERATE DIVERGENCE, AND THE ONLY ONE WORTH DEFENDING TWICE. USDA calls the field `date`;
    the column stays `week_ending` because that name says what the value MEANS - the label of the
    week the figure belongs to - where `date` says only what type it is.

    "Rename the column to match the source" is a reasonable-looking tidy, which is why the
    divergence is written into the field map's comment and asserted here. A consumer reading
    `date` would have no way to know it is a week ending rather than a day of observation.
    """
    for module in (usda_rates, usda_movements):
        assert module.FIELDS["week_ending"] == "date", (
            f"{module.__name__} maps week_ending to {module.FIELDS['week_ending']!r}; the "
            f"published field is `date`"
        )
        # The column name did not follow the source name.
        assert "date" not in module.UPSERT_SQL.split("(")[1].split(")")[0], (
            f"{module.__name__} writes a `date` column; the schema column is `week_ending` "
            f"(migrations 0014/0015)"
        )
        assert "week_ending" in module.UPSERT_SQL

    # And the paging order column is the SOURCE's name, because that one goes over the wire.
    assert usda_rates.ORDER_COLUMN == "date" and usda_movements.ORDER_COLUMN == "date"
    assert usda_rates.since_clause(date(2026, 8, 1)).startswith("date >= '2026-08-01")


def test_published_date_is_stored_unchanged_under_two_timezones():
    """The published label parses to the same calendar date in Denver and in Tokyo. Test 10.

    Run in SUBPROCESSES with TZ set, because a timezone is process-global and Python caches it:
    setting os.environ inside this process would test the cache, not the behaviour. The two zones
    are chosen either side of UTC, which is what makes an `.astimezone()` on a naive value land on
    a DIFFERENT DAY in one of them rather than merely a different hour.

    Both parsers, in one test: they share `parse_period_label`, and the failure this guards
    against is one of them growing its own path (CLAUDE.md § 15).
    """
    program = (
        "import json, sys;"
        "from app.ingest.usda_rates import rate_from;"
        "from app.ingest.usda_movements import movement_from;"
        "rec = json.loads(sys.argv[1]); mov = json.loads(sys.argv[2]);"
        "r = rate_from(rec, dataset_key='barge_rates_nearby');"
        "m = movement_from(mov);"
        "print(r.week_ending.isoformat(), m.week_ending.isoformat())"
    )

    seen = {}
    for tz in ("Asia/Tokyo", "America/Denver"):
        environment = dict(os.environ, TZ=tz, PYTHONPATH=REPO_ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                json.dumps(NEARBY_SAMPLE),
                json.dumps(MOVEMENT_SAMPLE),
            ],
            capture_output=True,
            text=True,
            env=environment,
            cwd=REPO_ROOT,
        )
        assert completed.returncode == 0, completed.stderr
        seen[tz] = completed.stdout.strip()

    assert seen["Asia/Tokyo"] == seen["America/Denver"] == "2026-08-11 2026-08-08", (
        f"a published label moved with the machine's timezone: {seen}. The label is the fact; no "
        f"timezone arithmetic is applied to it at any point (CLAUDE.md § 15)."
    )

    # An offset-bearing label is REFUSED rather than truncated: it would mean USDA had started
    # making a claim about an instant, and discarding that quietly is how two parsing paths merge.
    with pytest.raises(MalformedResponseError):
        usda_rates.rate_from(
            dict(NEARBY_SAMPLE, date="2026-08-11T00:00:00.000-05:00"),
            dataset_key="barge_rates_nearby",
        )
    with pytest.raises(MalformedResponseError):
        usda_movements.movement_from(dict(MOVEMENT_SAMPLE, date="2026-08-08T00:00:00.000-05:00"))
