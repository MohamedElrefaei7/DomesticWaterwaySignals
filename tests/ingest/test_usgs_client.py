"""Unit tier — the USGS client. No network, no database.

Covers CLAUDE.md § 14's first three bullets: the returned (entity, parameter) set must equal the
requested set; an empty result for an AVAILABLE series is not an error while a MISSING series is;
and timestamps are stored in UTC converted from the source's own offset.

The two failure shapes these tests hold apart are the whole point of the file. They arrive over
the wire looking identical - HTTP 200, well-formed JSON - and they mean opposite things.
"""

from datetime import datetime, timezone

import pytest

from app.ingest import usgs_client

DISCHARGE = usgs_client.PARAM_DISCHARGE
GAGE_HEIGHT = usgs_client.PARAM_GAGE_HEIGHT


def test_missing_requested_pair_raises_and_names_it(iv_payload):
    """A 200 with "timeSeries": [] is a FAILURE, not zero rows.

    This is finding 1 from the 2026-08-13 measurement: a request for a series a site does not
    serve comes back 200 with an empty array, carrying no error, no flag, and nothing that
    distinguishes it from a window in which the gauge simply reported nothing.

    The message must NAME the site and the parameter. An error that says only "missing series"
    leaves the operator to work out which of four sites and which of two parameters vanished, at
    the moment they are least inclined to read carefully (CLAUDE.md § 13).
    """
    payload = iv_payload("empty_timeseries")
    requested = {("07032000", GAGE_HEIGHT)}

    with pytest.raises(usgs_client.MissingSeriesError) as excinfo:
        usgs_client.parse(payload, requested)

    message = str(excinfo.value)
    assert "07032000" in message, f"the site is not named in the error: {message}"
    assert GAGE_HEIGHT in message, f"the parameter is not named in the error: {message}"


def test_partial_response_raises_naming_only_the_absent_sites(iv_payload):
    """Four sites requested, two returned. The error separates them.

    The distinction matters operationally and the message has to carry it: ALL pairs missing is
    usually the window or the service, while SOME pairs missing means those specific sites
    stopped serving that parameter and the seed is what to correct. An error that reports only
    "2 of 4 missing" cannot tell the operator which fix to reach for.
    """
    payload = iv_payload("partial_sites")
    present = {"07010000", "07032000"}
    absent = {"07289000", "07374000"}
    requested = {(site, DISCHARGE) for site in present | absent}

    with pytest.raises(usgs_client.MissingSeriesError) as excinfo:
        usgs_client.parse(payload, requested)

    message = str(excinfo.value)

    # The message is structured as `present:` and `MISSING:` lines. Assert each site lands on the
    # correct one, rather than merely appearing somewhere in the text - which it would even if
    # the two lists were swapped.
    present_line = next(line for line in message.splitlines() if "present:" in line)
    missing_line = next(line for line in message.splitlines() if "MISSING:" in line)

    for site in present:
        assert site in present_line, f"{site} returned data but is not reported as present"
        assert site not in missing_line, f"{site} returned data but is reported as missing"

    for site in absent:
        assert site in missing_line, f"{site} was absent from the response but is not named"
        assert site not in present_line, f"{site} was absent but is reported as present"


def test_no_rows_are_returned_on_a_partial_response(iv_payload):
    """It raises BEFORE handing back a single reading.

    Raising after yielding parsed rows would let a caller write half a batch and then fail: the
    rows are already committed, they are indistinguishable from a good run, and the retry writes
    them again. Verification therefore runs when readings_from() is CALLED, not when it is first
    iterated - which is why it is a plain function returning a generator rather than a generator
    function.

    Collecting into a list and asserting it is empty is what makes the ordering observable. A
    bare `pytest.raises` would pass just as happily with the check moved to the end of the loop.
    """
    payload = iv_payload("partial_sites")
    requested = {
        (site, DISCHARGE)
        for site in ("07010000", "07032000", "07289000", "07374000")
    }

    collected = []
    with pytest.raises(usgs_client.MissingSeriesError):
        for reading in usgs_client.readings_from(payload, requested):
            collected.append(reading)

    assert collected == [], (
        f"{len(collected)} reading(s) were handed back before the failure was raised. A caller "
        f"writing as it iterates has now committed a partial batch that looks like a good run."
    )

    # And the two sites that DID return data are genuinely parseable - so the empty list above is
    # the ordering under test, not a payload that had nothing in it either way.
    assert len(usgs_client.parse(payload, {("07010000", DISCHARGE)})) == 1


def test_offsets_are_converted_to_utc(iv_payload):
    """-05:00 and -06:00 at the same wall clock land one hour apart in UTC.

    These sites observe Central and Eastern time, so the offset in the payload changes twice a
    year. Stripping it - or assuming a fixed one - shifts an hour of readings at each DST
    transition, silently, in a way that looks like the river did something interesting and that
    nothing downstream can detect.
    """
    payload = iv_payload("ok")
    requested = {("07010000", DISCHARGE), ("07374000", DISCHARGE)}

    by_site = {}
    for reading in usgs_client.parse(payload, requested):
        by_site.setdefault(reading.usgs_site_id, []).append(reading)

    # Both fixtures carry a midnight reading; St. Louis at -05:00, Baton Rouge at -06:00.
    st_louis = min(by_site["07010000"], key=lambda r: r.ts)
    baton_rouge = min(by_site["07374000"], key=lambda r: r.ts)

    assert st_louis.ts.tzinfo is not None, "timestamp is naive - the offset was dropped entirely"
    assert st_louis.ts.utcoffset() == timezone.utc.utcoffset(None)

    assert st_louis.ts == datetime(2026, 8, 1, 5, 0, tzinfo=timezone.utc)
    assert baton_rouge.ts == datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)

    delta = baton_rouge.ts - st_louis.ts
    assert delta.total_seconds() == 3600, (
        f"the same wall-clock midnight at -05:00 and -06:00 landed {delta} apart instead of one "
        f"hour. The UTC offset was not applied."
    )


def test_a_window_with_zero_readings_is_not_an_error(iv_payload):
    """A PRESENT series with an empty `values` array returns []. It does not raise.

    The counterpart to test 1, and conflating the two is the bug. Gaps are ordinary - the first
    eight St. Louis readings on 2026-08-01 skip 02:30, and a backfill window covering a sensor
    outage legitimately returns nothing. A client that treated an empty window as fatal would
    make the eighteen-year backfill unrunnable.

    Built by emptying the fixture's value blocks rather than by carrying a fourth fixture: the
    shape under test is "the real response, minus its readings", and deriving it that way keeps
    it real if the captured response shape is ever refreshed.
    """
    payload = iv_payload("ok")
    for series in payload["value"]["timeSeries"]:
        for block in series["values"]:
            block["value"] = []

    requested = {("07010000", DISCHARGE), ("07374000", DISCHARGE)}

    readings = usgs_client.parse(payload, requested)

    assert readings == [], f"expected no readings from emptied value blocks, got {len(readings)}"

    # The series are still present, which is exactly why this is not an error. If this assertion
    # ever fails the test above has stopped testing what it claims to.
    assert usgs_client.present_pairs(payload) == requested


def test_provisional_qualifier_is_preserved_not_filtered(iv_payload):
    """'P' readings come back, carrying their qualifiers.

    Provisional covers most of the recent record - the part the signal actually runs on - so
    filtering it on the way in would shrink the useful data to almost nothing while reporting
    success with a smaller row count. The provisional/approved distinction is a fact about the
    reading that downstream layers are entitled to see and gate on themselves.
    """
    payload = iv_payload("ok")
    requested = {("07010000", DISCHARGE), ("07374000", DISCHARGE)}

    readings = usgs_client.parse(payload, requested)

    provisional = [r for r in readings if "P" in r.qualifiers]
    approved = [r for r in readings if "A" in r.qualifiers]

    assert provisional, (
        "no provisional readings survived parsing. The fixture carries them; they were filtered "
        "out, which would discard most of the recent record."
    )
    assert approved, "no approved readings survived parsing either - something dropped everything"

    # Qualifiers are carried through as published, not normalized to a single flag.
    assert all(isinstance(r.qualifiers, tuple) for r in readings)
    assert any(r.qualifiers == ("P",) for r in readings)


def test_the_no_data_sentinel_is_dropped_rather_than_stored(iv_payload):
    """-999999 is USGS's declared missing marker, not a discharge.

    Not in the commit's numbered test list, and here because the fixture captured from the live
    service contains one. Stored as-is it would be a discharge of negative one million cubic feet
    per second - a value that breaks every aggregate it touches while looking, to anything
    reading the table, like data. The sentinel is read from the series' own `noDataValue` rather
    than hardcoded, because it is the series that declares it.
    """
    payload = iv_payload("ok")
    requested = {("07010000", DISCHARGE), ("07374000", DISCHARGE)}

    readings = usgs_client.parse(payload, requested)

    assert readings, "nothing parsed at all - the assertion below would be vacuous"
    assert all(r.value > 0 for r in readings), (
        f"a sentinel value survived parsing: "
        f"{[r for r in readings if r.value <= 0]}"
    )

    # The fixture's St. Louis series has three entries, one of which is the sentinel.
    st_louis = [r for r in readings if r.usgs_site_id == "07010000"]
    assert len(st_louis) == 2, (
        f"expected 2 real readings from 3 fixture entries (one is -999999), got {len(st_louis)}"
    )


def test_build_url_pins_the_format_and_serializes_utc():
    """The endpoint and WaterML version are pinned, and the window is unambiguous.

    CLAUDE.md § 6: build against a specific, named endpoint set and pin it. A bare `format=json`
    accepts whatever the service considers current - the API equivalent of an unpinned image tag.

    Bare dates are accepted by the service but interpreted in each SITE's local time, so a window
    boundary would mean a different instant at Baton Rouge than at St. Louis and the backfill's
    windows would neither tile nor be reproducible.
    """
    url = usgs_client.build_url(
        ["07010000"],
        [DISCHARGE],
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 1, tzinfo=timezone.utc),
    )

    assert url.startswith(usgs_client.IV_ENDPOINT)
    assert "format=json%2C1.1" in url, f"the WaterML version is not pinned: {url}"
    assert "2026-01-01T00%3A00%3A00Z" in url, f"startDT is not explicit UTC: {url}"
    assert "2026-04-01T00%3A00%3A00Z" in url, f"endDT is not explicit UTC: {url}"
    assert "siteStatus=all" in url

    # A naive datetime is refused rather than assumed to be UTC.
    with pytest.raises(ValueError, match="naive"):
        usgs_client.build_url(
            ["07010000"], [DISCHARGE], datetime(2026, 1, 1), datetime(2026, 4, 1)
        )
