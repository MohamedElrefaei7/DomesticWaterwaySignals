"""Unit tier — the USGS daily-values client. No network, no database.

Covers CLAUDE.md § 15: three response outcomes never collapsed, offset-free timestamps stored as
the calendar date the source stated, and statistic codes parsed rather than hardcoded.

The heart of this file is that the daily and instantaneous parsing paths are SEPARATE. Two tests
exist only to hold them apart, because the shared-path version of this client is shorter, reads
better, and silently shifts every daily mean by a day for anything east of Greenwich.
"""

import time
from datetime import date

import pytest

from app.ingest import usgs_client, usgs_daily_client
from app.ingest.usgs_client import MissingSeriesError

DISCHARGE = usgs_client.PARAM_DISCHARGE
MEAN = usgs_daily_client.STAT_MEAN
MINIMUM = usgs_daily_client.STAT_MIN

MEMPHIS = "07032000"
VICKSBURG = "07289000"


def test_non_json_body_raises_its_own_error_naming_the_window(dv_raw_body):
    """A body that is not JSON is its own failure, distinct from a missing series.

    MEASURED 2026-08-14: this is what the daily service returns for a window ENTIRELY OUTSIDE a
    site's period of record. It is the ORDINARY response for a backfill floor set earlier than
    the real record, which is why it cannot be folded into the missing-series case: that error
    tells the operator to check `available_params`, and this one tells them to check
    `dv_record_start`. One message cannot say both and be right.
    """
    body = dv_raw_body("non_json")
    requested = {(VICKSBURG, DISCHARGE, MEAN)}

    with pytest.raises(usgs_daily_client.OutsidePeriodOfRecordError) as excinfo:
        usgs_daily_client.parse_body(
            body,
            requested,
            site_ids=[VICKSBURG],
            start=date(1990, 1, 1),
            end=date(1990, 12, 31),
        )

    message = str(excinfo.value)
    assert VICKSBURG in message, f"the site is not named: {message}"
    assert "1990-01-01" in message and "1990-12-31" in message, (
        f"the window is not named: {message}"
    )
    assert "period of record" in message.lower()
    assert "dv_record_start" in message, (
        "the message does not point at the seed, which is the thing to fix"
    )

    # A DIFFERENT TYPE, not a subclass relationship that would let one `except` swallow both.
    assert not issubclass(
        usgs_daily_client.OutsidePeriodOfRecordError, MissingSeriesError
    )
    assert not issubclass(
        MissingSeriesError, usgs_daily_client.OutsidePeriodOfRecordError
    )


def test_missing_triple_raises_and_names_site_param_and_stat(dv_payload):
    """The requested (site, parameter, statistic) set must equal what came back.

    Phase 3's assertion extended by one element. The statistic belongs in it because requesting
    the mean and receiving the minimum is a SATISFIED request to any check that compares only
    sites and parameters - and a daily minimum stored under the mean's key is systematically low,
    plausible, and undetectable downstream.
    """
    payload = dv_payload("ok")
    # The fixture carries Memphis and Vicksburg. Ask for a third site as well.
    requested = {
        (MEMPHIS, DISCHARGE, MEAN),
        (VICKSBURG, DISCHARGE, MEAN),
        ("07010000", DISCHARGE, MEAN),
    }

    with pytest.raises(MissingSeriesError) as excinfo:
        usgs_daily_client.parse(payload, requested)

    message = str(excinfo.value)
    missing_line = next(line for line in message.splitlines() if "MISSING:" in line)
    present_line = next(line for line in message.splitlines() if "present:" in line)

    assert "07010000" in missing_line
    assert MEAN in missing_line, "the statistic code is not named in the failure"
    assert MEMPHIS in present_line and VICKSBURG in present_line

    # And the statistic genuinely participates: asking for the MINIMUM of a series that returned
    # the MEAN is a missing triple, even though the site and parameter both arrived.
    with pytest.raises(MissingSeriesError) as excinfo:
        usgs_daily_client.parse(payload, {(MEMPHIS, DISCHARGE, MINIMUM)})
    assert MINIMUM in str(excinfo.value)


def test_present_series_with_empty_values_is_not_an_error(dv_payload):
    """The ordinary gap. A present series with no values returns [], and does not raise.

    The third of the three outcomes, and the one that must stay cheap: treating it as an error
    would make a 35-year backfill unrunnable across any period a gauge was out of service.
    """
    payload = dv_payload("ok")
    for series in payload["value"]["timeSeries"]:
        for block in series["values"]:
            block["value"] = []

    requested = {(MEMPHIS, DISCHARGE, MEAN), (VICKSBURG, DISCHARGE, MEAN)}

    readings = usgs_daily_client.parse(payload, requested)

    assert readings == [], f"expected no readings from emptied value blocks, got {len(readings)}"
    # The series are still present, which is exactly why this is not an error.
    assert usgs_daily_client.present_triples(payload) == requested


def test_stat_cd_is_parsed_from_options_not_hardcoded(dv_payload):
    """A series declaring 00001 parses as 00001, not as the 00003 that was requested.

    Hardcoding the statistic - or defaulting it to the mean when the option is absent - makes the
    set-equality assertion above vacuous: every response would report the statistic that was
    asked for, by construction, whatever actually arrived.
    """
    payload = dv_payload("ok")

    # As captured: both series report the mean.
    assert usgs_daily_client.present_triples(payload) == {
        (MEMPHIS, DISCHARGE, MEAN),
        (VICKSBURG, DISCHARGE, MEAN),
    }

    # Now make Memphis report the daily MINIMUM instead, exactly as the service would.
    memphis = next(
        s
        for s in payload["value"]["timeSeries"]
        if s["sourceInfo"]["siteCode"][0]["value"] == MEMPHIS
    )
    memphis["variable"]["options"]["option"] = [
        {"value": "Minimum", "name": "Statistic", "optionCode": MINIMUM}
    ]

    triples = usgs_daily_client.present_triples(payload)
    assert (MEMPHIS, DISCHARGE, MINIMUM) in triples, (
        f"the statistic was not read from the response: {triples}"
    )
    assert (MEMPHIS, DISCHARGE, MEAN) not in triples, (
        "the requested statistic was reported back regardless of what the series declared"
    )

    readings = usgs_daily_client.parse(payload, {(MEMPHIS, DISCHARGE, MINIMUM)})
    assert readings, "nothing parsed"
    assert {r.stat_cd for r in readings} == {MINIMUM}

    # A series with NO Statistic option is refused rather than defaulted to the mean.
    del memphis["variable"]["options"]["option"]
    with pytest.raises(usgs_client.MalformedResponseError, match="Statistic"):
        usgs_daily_client.present_triples(payload)


@pytest.mark.parametrize("tz", ["UTC", "America/Denver", "Asia/Tokyo"])
def test_naive_timestamps_are_stored_as_the_stated_calendar_date(dv_payload, monkeypatch, tz):
    """`2022-10-01T00:00:00.000` is 2022-10-01. In every timezone. With no arithmetic.

    THE BUG THIS PREVENTS: the daily endpoint returns naive timestamps and the instantaneous one
    returns offsets. Parsing a naive timestamp into a datetime and calling `.astimezone(utc)` on
    it makes Python apply the LOCAL MACHINE's zone - so a daily mean for 2022-10-01 becomes
    05:00Z on a UTC container and 07:00Z on a laptop in Denver, and in Tokyo it becomes the
    PREVIOUS DAY. The value is plausible everywhere and wrong somewhere.

    Asia/Tokyo is in the parameter list specifically because it is east of Greenwich: a UTC-only
    test passes against an implementation that shifts the date backwards for exactly the zones
    nobody ran it in.
    """
    monkeypatch.setenv("TZ", tz)
    time.tzset()

    payload = dv_payload("ok")
    readings = usgs_daily_client.parse(payload, {(MEMPHIS, DISCHARGE, MEAN)})

    assert readings, "nothing parsed"
    dates = sorted(r.date for r in readings)

    assert dates == [date(2022, 10, 1), date(2022, 10, 2)], (
        f"under TZ={tz} the fixture's dates parsed as {dates}, not the dates the API stated. "
        f"Timezone arithmetic was applied to a value that carries no timezone."
    )
    # Dates, not datetimes. A datetime is the thing something later calls .astimezone() on.
    for reading in readings:
        assert type(reading.date) is date, (
            f"date is {type(reading.date).__name__}; a datetime here is one refactor away from "
            f"being localized"
        )

    # And a timestamp that DOES carry an offset is refused rather than truncated - that would
    # mean the service changed what it publishes, which this project must notice deliberately.
    with pytest.raises(usgs_client.MalformedResponseError, match="offset"):
        usgs_daily_client.parse_date("2022-10-01T00:00:00.000-05:00")


def test_the_iv_utc_conversion_is_not_applied_to_daily_values(dv_payload, monkeypatch):
    """The daily path does not go through the instantaneous client's timestamp converter.

    Asserted by BREAKING the instantaneous converter and confirming the daily client still works.
    That is stronger than inspecting imports: it fails for any route to `_parse_timestamp`,
    including one added three refactors from now by someone consolidating "duplicate" parsing
    code - which is exactly the tidying this guard exists to catch.
    """
    def explode(raw):
        raise AssertionError(
            f"usgs_client._parse_timestamp was called with {raw!r} from the DAILY path. It "
            f"applies .astimezone(UTC) to a value that carries no timezone, which silently "
            f"shifts the calendar date (CLAUDE.md § 15)."
        )

    monkeypatch.setattr(usgs_client, "_parse_timestamp", explode)

    payload = dv_payload("ok")
    readings = usgs_daily_client.parse(
        payload, {(MEMPHIS, DISCHARGE, MEAN), (VICKSBURG, DISCHARGE, MEAN)}
    )

    assert readings, "nothing parsed at all - the assertion above would be vacuous"
    assert {r.date for r in readings} == {date(2022, 10, 1), date(2022, 10, 2)}

    # The converse, so this test cannot pass because the sabotage did not take: the instantaneous
    # client really is broken right now.
    with pytest.raises(AssertionError, match="DAILY path"):
        usgs_client._parse_timestamp("2026-08-01T00:00:00.000-05:00")


def test_the_no_data_sentinel_is_dropped(dv_payload):
    """-999999 is USGS's declared missing marker, not a discharge.

    The daily fixture carries one, as the captured response did. Same reasoning as the
    instantaneous client: stored as-is it is a number that breaks every aggregate it touches
    while looking like data.
    """
    payload = dv_payload("ok")
    readings = usgs_daily_client.parse(payload, {(MEMPHIS, DISCHARGE, MEAN)})

    assert len(readings) == 2, (
        f"expected 2 real readings from 3 fixture entries (one is -999999), got {len(readings)}"
    )
    assert all(r.value > 0 for r in readings)


def test_provisional_daily_values_are_preserved(dv_payload):
    """'P' daily values come back with their qualifiers, same as the instantaneous path.

    Daily values are revised after publication - which is why the scheduled poll re-requests the
    last seven days - so the provisional/approved distinction is a fact consumers are entitled to
    see rather than one the ingest quietly resolves.
    """
    payload = dv_payload("ok")
    readings = usgs_daily_client.parse(payload, {(VICKSBURG, DISCHARGE, MEAN)})

    assert any("P" in r.qualifiers for r in readings), "provisional daily values were filtered out"
    assert any("A" in r.qualifiers for r in readings)


def test_build_url_pins_the_format_and_sends_plain_dates():
    """Calendar dates, not instants, and the WaterML version is pinned.

    The instantaneous client sends explicit UTC instants because its boundaries ARE instants.
    Sending one here would ask the daily service a question about a time of day it does not
    answer, and would be the first step of treating a daily mean as an instant.
    """
    url = usgs_daily_client.build_url(
        [MEMPHIS], [DISCHARGE], [MEAN], date(2022, 1, 1), date(2022, 12, 31)
    )

    assert url.startswith(usgs_daily_client.DV_ENDPOINT)
    assert "/nwis/dv/" in url, "the daily endpoint is not being used"
    assert "format=json%2C1.1" in url, f"the WaterML version is not pinned: {url}"
    assert "statCd=00003" in url, f"the statistic is not requested explicitly: {url}"
    assert "startDT=2022-01-01" in url and "endDT=2022-12-31" in url
    assert "T00%3A00" not in url, "a time of day was sent to the daily endpoint"

    # A datetime is refused: it is how an instant gets in.
    from datetime import datetime

    with pytest.raises(ValueError, match="plain date"):
        usgs_daily_client.build_url(
            [MEMPHIS], [DISCHARGE], [MEAN], datetime(2022, 1, 1), date(2022, 12, 31)
        )
