"""The barge-rate target: its key, its unit, and its period label.

Three decisions, each with a shorter wrong version that passes every obvious test:

  * `horizon` IS PART OF THE KEY. Without it the same week's nearby and forward rates overwrite
    each other and the survivor is whichever arrived last.
  * `pct_of_tariff` IS STORED AS PUBLISHED. Dividing by 100 in ingest is a modelling decision two
    layers from where such decisions belong, and its symptom is a plausible-looking chart.
  * `week_ending` IS THE PUBLISHED LABEL. Route it through a timezone and the same rate belongs to
    a different week in Tokyo than in Denver.
"""

import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal

import pytest

from app.ingest import usda_rates
from app.ingest.socrata_client import MalformedResponseError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def record(
    segment="Cairo-Memphis",
    week_ending="2022-10-04T00:00:00.000",
    horizon="Nearby",
    rate="112.5",
) -> dict:
    return {
        usda_rates.FIELDS["segment"]: segment,
        usda_rates.FIELDS["week_ending"]: week_ending,
        usda_rates.FIELDS["horizon"]: horizon,
        usda_rates.FIELDS["pct_of_tariff"]: rate,
    }


# ---------------------------------------------------------------------------------------------
# Unit tier.
# ---------------------------------------------------------------------------------------------


def test_pct_of_tariff_is_stored_exactly_as_published():
    """112.5 parses as 112.5. Not 1.125, not 113, not 112.49999999999999. Test 7.

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
    assert usda_rates.rate_from(record(rate="112.5")).pct_of_tariff == Decimal("112.5")

    # An empty or non-numeric rate raises rather than becoming 0 or NULL. A rate of zero percent
    # of tariff is not a thing that happens; it is what an empty field becomes when coerced.
    for bad in ("", "  ", None, "n/a", "0"):
        with pytest.raises(MalformedResponseError):
            usda_rates.parse_rate(bad)


def test_week_ending_is_stored_as_the_published_label_under_two_timezones():
    """The published label parses to the same calendar date in Denver and in Tokyo. Test 8.

    Run in SUBPROCESSES with TZ set, because a timezone is process-global and Python caches it:
    setting os.environ inside this process would test the cache, not the behaviour. The two zones
    are chosen either side of UTC, which is what makes an `.astimezone()` on a naive value land on
    a DIFFERENT DAY in one of them rather than merely a different hour.

    This is the same guard the daily-values parser carries (CLAUDE.md § 15), applied at the point
    the same mistake would be made again in a new module.
    """
    program = (
        "import json;"
        "from app.ingest.usda_rates import rate_from;"
        "r = rate_from(json.loads(__import__('sys').argv[1]));"
        "print(r.week_ending.isoformat())"
    )
    payload = json.dumps(record(week_ending="2022-10-04T00:00:00.000"))

    seen = {}
    for tz in ("Asia/Tokyo", "America/Denver"):
        environment = dict(os.environ, TZ=tz, PYTHONPATH=REPO_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", program, payload],
            capture_output=True,
            text=True,
            env=environment,
            cwd=REPO_ROOT,
        )
        assert completed.returncode == 0, completed.stderr
        seen[tz] = completed.stdout.strip()

    assert seen["Asia/Tokyo"] == seen["America/Denver"] == "2022-10-04", (
        f"the published week label moved with the machine's timezone: {seen}. The label is the "
        f"fact; no timezone arithmetic is applied to it at any point (CLAUDE.md § 15)."
    )

    # An offset-bearing label is REFUSED rather than truncated: it would mean USDA had started
    # making a claim about an instant, and discarding that quietly is how two parsing paths merge.
    with pytest.raises(MalformedResponseError):
        usda_rates.rate_from(record(week_ending="2022-10-04T00:00:00.000-05:00"))


def test_an_unrecognised_horizon_raises_rather_than_defaulting():
    """A label this module does not know is an error, never a `nearby`.

    Not in the brief's numbered list, and here because the mapping from published label to key
    vocabulary is the place a silent default would do the damage test 6 guards against - filing a
    three-month forward rate under `nearby` produces a series that is wrong in a way no aggregate
    reveals, and the key constraint cannot see it because the value is valid.
    """
    assert usda_rates.parse_horizon("Nearby") == "nearby"
    assert usda_rates.parse_horizon("3 Month Forward") == "3_month"

    with pytest.raises(MalformedResponseError) as excinfo:
        usda_rates.parse_horizon("6 Month Forward")
    assert "nearby" in str(excinfo.value), "the error does not list the labels it does know"


def test_a_missing_field_raises_naming_what_the_record_carries():
    """A record without the rate field is an error, not a NULL row.

    The USDA field names in this repo are PROVISIONAL until the datasets are resolved (migration
    0013). A mapping that silently produced NULLs would be a client reporting success over an
    empty table - CLAUDE.md § 2's theme 1, which is exactly how the first ingest client this
    project ever wrote failed.
    """
    incomplete = record()
    del incomplete[usda_rates.FIELDS["pct_of_tariff"]]

    with pytest.raises(MalformedResponseError) as excinfo:
        usda_rates.rate_from(incomplete)

    message = str(excinfo.value)
    assert usda_rates.FIELDS["pct_of_tariff"] in message
    assert "Fields present" in message, (
        f"the error does not say what the record does carry, which is what makes a wrong field "
        f"mapping a two-minute fix: {message}"
    )
    assert "PROVISIONAL" in message


def test_the_captured_fixture_parses_into_rates(socrata_body):
    """The captured page shape parses end to end, including all three horizons."""
    records = json.loads(socrata_body("rates_ok"))
    rates = usda_rates.parse_rates(records)

    assert len(rates) == len(records)
    assert {r.horizon for r in rates} == {"nearby", "1_month", "3_month"}
    assert {r.segment for r in rates} == {"Cairo-Memphis", "St. Louis"}
    assert all(isinstance(r.week_ending, date) for r in rates)


# ---------------------------------------------------------------------------------------------
# Integration tier.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_horizon_is_part_of_the_key_so_three_horizons_coexist(migrated_db):
    """One segment, one week, three horizons: three rows, not one. Test 6.

    THE FAILURE THIS CATCHES IS SILENT AND TOTAL. Keyed without `horizon`, an upsert over one
    week's publication writes three rows onto one key and keeps whichever arrived last - which is
    not deterministic. The resulting series is mostly nearby rates with occasional forward ones
    mixed in, every aggregate over it is wrong, and nothing about it says so.
    """
    week = date(2022, 10, 4)
    written = usda_rates.upsert_rates(
        migrated_db,
        usda_rates.parse_rates(
            [
                record(horizon="Nearby", rate="1420"),
                record(horizon="1 Month Forward", rate="1050"),
                record(horizon="3 Month Forward", rate="780.25"),
            ]
        ),
    )
    migrated_db.commit()

    assert written == 3, f"{written} row(s) written for three distinct horizons"

    rows = migrated_db.execute(
        "SELECT horizon, pct_of_tariff FROM barge_rates WHERE segment = %s AND week_ending = %s"
        " ORDER BY horizon",
        ("Cairo-Memphis", week),
    ).fetchall()

    assert dict(rows) == {
        "1_month": Decimal("1050"),
        "3_month": Decimal("780.25"),
        "nearby": Decimal("1420"),
    }, f"the three horizons did not survive as three rows: {rows}"

    # And the stored value is the published one, read back from the database rather than from the
    # dataclass - the round trip is where a float or a rounding cast would show.
    stored = migrated_db.execute(
        "SELECT pct_of_tariff::text FROM barge_rates WHERE horizon = '3_month'"
    ).fetchone()[0]
    assert stored == "780.25", f"the database holds {stored!r}, not the published 780.25"


@pytest.mark.integration
def test_a_revised_week_overwrites(migrated_db):
    """USDA republishes a week; the new number wins and is counted once. Test 9.

    `DO NOTHING` is the trap: it makes reruns safe, passes every duplicate test, and freezes the
    first-published value permanently and silently (CLAUDE.md § 14). USDA revises as a matter of
    routine, so the frozen value would be a target series that quietly disagrees with the source.
    """
    first = usda_rates.upsert_rates(migrated_db, usda_rates.parse_rates([record(rate="1420")]))
    migrated_db.commit()
    assert first == 1

    # The same week, revised.
    revised = usda_rates.upsert_rates(migrated_db, usda_rates.parse_rates([record(rate="1455.5")]))
    migrated_db.commit()
    assert revised == 1, "a genuine revision was not counted as a write"

    value = migrated_db.execute(
        "SELECT pct_of_tariff::text FROM barge_rates WHERE segment = %s AND week_ending = %s"
        " AND horizon = 'nearby'",
        ("Cairo-Memphis", date(2022, 10, 4)),
    ).fetchone()[0]
    assert value == "1455.5", (
        f"the revision did not land - the table holds {value!r}. DO NOTHING freezes the "
        f"first-published value forever, silently."
    )

    # A rerun over UNCHANGED data writes nothing and reports 0. `rows_written` means rows that
    # actually changed the database (CLAUDE.md § 4), and a plain DO UPDATE would report 1 here on
    # every run - a number large enough to look reassuring and meaning nothing.
    unchanged = usda_rates.upsert_rates(
        migrated_db, usda_rates.parse_rates([record(rate="1455.5")])
    )
    migrated_db.commit()
    assert unchanged == 0, (
        f"a rerun over unchanged data reported {unchanged} row(s) written; rows_written must "
        f"count rows that actually changed"
    )

    assert (
        migrated_db.execute("SELECT count(*) FROM barge_rates").fetchone()[0] == 1
    ), "the revision inserted a second row instead of replacing the first"
