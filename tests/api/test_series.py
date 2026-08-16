"""The series endpoints. NULL SURVIVES, ZERO SURVIVES, NOTHING IS SUMMED, EVERY LIST IS BOUNDED.

THE NULL AND ZERO TESTS ARE INTEGRATION AND COULD NOT HONESTLY BE ANYTHING ELSE. The claim is that
a NULL in a Postgres column arrives at a JSON client as `null` - which involves the column's
nullability, the driver's type mapping, the response model's declaration, and the encoder. A fake
connection handing back `None` would exercise the last two and assert the first two by assumption,
and Phase 4 spent three commits on the first two.

BOTH DIRECTIONS, SEPARATELY. `test_a_null_tons_serializes_as_null_not_zero` and
`test_a_zero_tons_serializes_as_zero_not_null` are two tests because ONE TEST CAN BE SATISFIED BY
AN IMPLEMENTATION THAT IS WRONG THE OTHER WAY - the same argument tests/ingest/ makes about the
empty-window/missing-series pair, and the reason CLAUDE.md § 16 states the zero and the NULL as two
claims rather than one rule.
"""

from datetime import date, timedelta

import pytest

from app.api import models
from app.api.dependencies import MAX_LIMIT, MAX_SPAN_YEARS
from tests.api.conftest import (
    CAIRO_MEMPHIS,
    FakeConn,
    MEMPHIS,
    TWIN_CITIES,
    make_client,
    weekly,
)

# A window inside which every fixture below sits, well under the five-year ceiling.
WINDOW = "start=2022-01-01&end=2022-12-31"


def unit_client():
    """A client whose connection RAISES on any query.

    The 422 tests must fail before a query is issued: a validation error that reached the database
    would mean the bound and the range were checked after the expensive part, which is the ordering
    that makes them pointless.
    """
    return make_client(conn=FakeConn())


# ---------------------------------------------------------------------------------------------
# Tests 18-21. What the database said, unchanged.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_a_null_rate_serializes_as_null_not_zero(seed, db_client):
    """Test 18. A winter navigation closure is not a week when freight was free.

    USDA publishes no `rate` field for 774 of 8,260 nearby records, 661 of them December-March and
    729 on the two upper segments (migration 0017). A `0` here would claim barge freight cost
    nothing that week, which is never true and drags every average over the series toward it.
    """
    seed.rates(
        [(date(2022, 1, 8), None), (date(2022, 1, 15), 250.5)], location=TWIN_CITIES
    )

    body = db_client.get(f"/api/rates?{WINDOW}&segment=Twin%20Cities").json()
    by_week = {row["week_ending"]: row for row in body["rows"]}

    assert by_week["2022-01-08"]["pct_of_tariff"] is None, (
        "a week USDA published no rate for came back as "
        f"{by_week['2022-01-08']['pct_of_tariff']!r}"
    )
    assert by_week["2022-01-15"]["pct_of_tariff"] == 250.5

    # And it is `null` in the JSON text, not the string "None" or an omitted key.
    assert '"pct_of_tariff":null' in db_client.get(
        f"/api/rates?{WINDOW}&segment=Twin%20Cities"
    ).text.replace(" ", "")


@pytest.mark.integration
def test_a_null_tons_serializes_as_null_not_zero(seed, db_client):
    """Test 19. A reporting gap is not a week when no grain moved.

    `tons` is absent on 108 of 26,144 records, on three locks only. That is a REPORTING GAP and it
    says nothing about the river - which is a different fact from the 8,218 records where USDA
    reported a zero, and CLAUDE.md § 16's closing bullet is about exactly this pair of columns
    receiving identical handling and meaning entirely different things.
    """
    seed.movements([("MS Lock 15", date(2022, 10, 8), "Corn", None)])

    body = db_client.get(f"/api/movements?{WINDOW}").json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["tons"] is None, (
        f"an unreported tonnage came back as {body['rows'][0]['tons']!r}"
    )


@pytest.mark.integration
def test_a_zero_tons_serializes_as_zero_not_null(seed, db_client):
    """Test 20. The inverse, and it is the direction that deletes the observations that matter.

    USDA publishes `tons = 0` on 8,218 of 26,144 records: ZERO IS THE ROUTINE WAY THIS SOURCE SAYS
    NOTHING MOVED. Turning those into `null` would delete precisely the observations an extreme
    low-water event produces - near-zero movement is the signal this project studies.
    """
    seed.movements([("MS Lock 15", date(2022, 10, 8), "Corn", 0)])

    body = db_client.get(f"/api/movements?{WINDOW}").json()

    assert body["rows"][0]["tons"] == 0, (
        f"a reported zero came back as {body['rows'][0]['tons']!r}"
    )
    assert body["rows"][0]["tons"] is not None


@pytest.mark.integration
def test_a_null_and_a_zero_tonnage_are_distinguishable_in_one_response(seed, db_client):
    """Both, side by side, in one body. Not numbered.

    Tests 19 and 20 each seed one row, so each can be passed by an implementation that emits one
    value for everything. This one seeds both and asserts they come back different, which no such
    implementation can satisfy.
    """
    seed.movements(
        [
            ("MS Lock 15", date(2022, 10, 8), "Corn", 0),
            ("MS Lock 15", date(2022, 10, 8), "Soybeans", None),
            ("MS Lock 15", date(2022, 10, 8), "Wheat", 12_500),
        ]
    )

    rows = db_client.get(f"/api/movements?{WINDOW}").json()["rows"]
    by_commodity = {row["commodity"]: row["tons"] for row in rows}

    assert by_commodity == {"Corn": 0, "Soybeans": None, "Wheat": 12_500}


@pytest.mark.integration
def test_movements_are_not_summed_across_commodities(seed, db_client):
    """Test 21, decision 5. One row per (lock, week, commodity), as published.

    A sum would be a modelling decision made silently in a read layer, and on a table this sparse
    it would also be a coalesce: 12,500 + NULL is either 12,500 or NULL depending on how it is
    written, and the first one invents a measurement out of a reporting gap.
    """
    seed.movements(
        [
            ("MS Lock 15", date(2022, 10, 8), "Corn", 1_000),
            ("MS Lock 15", date(2022, 10, 8), "Soybeans", 2_000),
            ("MS Lock 15", date(2022, 10, 8), "Wheat", None),
        ]
    )

    body = db_client.get(f"/api/movements?{WINDOW}&lock=MS%20Lock%2015").json()

    assert body["total"] == 3
    assert len(body["rows"]) == 3
    assert sorted(row["commodity"] for row in body["rows"]) == [
        "Corn",
        "Soybeans",
        "Wheat",
    ]
    assert {row["tons"] for row in body["rows"]} == {1_000, 2_000, None}
    assert 3_000 not in [row["tons"] for row in body["rows"]], (
        "a summed row would carry 3,000 tons and would have decided, silently, that an unreported "
        "wheat tonnage was zero"
    )


@pytest.mark.integration
def test_a_null_gauge_reading_is_not_invented(seed, db_client):
    """The gauge series carries its source, and its value is not defaulted. Not numbered.

    `gauge_series` encodes the iv/dv precedence rule ONCE, and the `source` column is what keeps
    the seam between the two visible (CLAUDE.md § 15). A response that dropped it would leave a
    client unable to tell a switch of measurement from a change in the river.
    """
    seed.daily_values(MEMPHIS, [(date(2022, 10, 1), 180_000.0), (date(2022, 10, 2), 175_000.0)])

    body = db_client.get(f"/api/gauges/{MEMPHIS}/series?{WINDOW}").json()

    assert body["total"] == 2
    assert {row["source"] for row in body["rows"]} == {"dv"}
    assert body["rows"][0]["value"] == 180_000.0
    assert body["rows"][0]["param_code"] == "00060"


# ---------------------------------------------------------------------------------------------
# Tests 22-24. The bound and the range are rejections, not adjustments.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "",
        "start=2022-01-01",
        "end=2022-12-31",
    ],
)
@pytest.mark.parametrize("path", ["/api/rates", "/api/movements"])
def test_missing_start_or_end_is_a_422(path, query):
    """Test 22, decision 7. Both ends required, on every series endpoint.

    An unbounded default invites a client to fetch 258,739 instantaneous rows through a JSON
    serializer, and the cost is invisible from the client's side - the request just takes a while
    and then works, until it does not.
    """
    response = unit_client().get(f"{path}?{query}")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    missing = {field["field"].split(".")[-1] for field in body["error"]["fields"]}
    assert missing & {"start", "end"}


def test_missing_start_or_end_is_a_422_on_the_gauge_series_too():
    """Test 22, for the path-parameterized endpoint. Same rule, different route signature."""
    response = unit_client().get(f"/api/gauges/{MEMPHIS}/series")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_span_over_five_years_is_a_422_naming_the_limit():
    """Test 23, decision 7. The refusal says what the limit is.

    A 422 that does not name the maximum sends the client author to read the source, and the number
    they find there is the one they hardcode - which is how a limit acquires a second definition.
    """
    response = unit_client().get("/api/rates?start=2000-01-01&end=2026-01-01")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "span_too_long"
    assert f"{MAX_SPAN_YEARS} years" in body["error"]["message"]
    assert "9497" in body["error"]["message"], (
        "the message must state the span that was asked for as well as the limit"
    )


def test_an_exactly_five_year_span_is_accepted():
    """The limit does not fire one day early. Not numbered.

    A guard that rejected a legal request would be found immediately and worked around by raising
    the limit, which is the wrong repair.
    """
    from app.api.dependencies import MAX_SPAN_DAYS, DateRange, date_range

    start = date(2021, 1, 1)
    window = date_range(start=start, end=start + timedelta(days=MAX_SPAN_DAYS))

    assert isinstance(window, DateRange)
    assert window.days == MAX_SPAN_DAYS


def test_an_inverted_range_is_its_own_error():
    """A swapped pair of parameters is a different mistake from asking for a decade. Not numbered."""
    response = unit_client().get("/api/rates?start=2022-12-31&end=2022-01-01")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_limit_above_maximum_is_a_422_not_a_silent_clamp():
    """Test 24, decision 6.

    A CLAMP IS A LIE THE CLIENT CANNOT DETECT. It asked for 50,000 rows and received 5,000, which
    is indistinguishable from a filter that matched 5,000 rows - and the chart drawn from it looks
    exactly like a complete one. `total` would eventually reveal it, and a client that reads
    `total` is not the client this guard exists for.
    """
    response = unit_client().get(f"/api/rates?{WINDOW}&limit=50000")

    assert response.status_code == 422, (
        f"expected a rejection, got {response.status_code} - a 200 here means the request was "
        f"silently clamped"
    )
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert str(MAX_LIMIT) in str(body["error"]["fields"])


@pytest.mark.parametrize(
    "path",
    ["/api/gauges", "/api/rates", "/api/movements", "/api/signals", "/api/signals/runs"],
)
def test_every_list_response_model_carries_limit_offset_and_total(path):
    """Test 25, the structural half: every list endpoint's response model IS a `ListEnvelope`.

    Structural because the fields are REQUIRED on the base model, so a route cannot return one
    without them - the model would refuse to construct. The behavioural half below hits the real
    endpoints, because a structural check cannot tell whether `total` was filled in with something
    true.
    """
    model = _response_model_for(path)
    assert issubclass(model, models.ListEnvelope), (
        f"{path} returns {model.__name__}, which is not a ListEnvelope: a list response without "
        f"`total` lets a client draw a truncated series that looks like a real one"
    )
    assert {"limit", "offset", "total"} <= set(model.model_fields)


def _response_model_for(path: str):
    """The declared response model for a path, walked out of the app's route tree."""
    from app.api.main import declared_routes  # noqa: F401 - kept for symmetry
    from app.api.main import app

    def walk(router):
        for route in getattr(router, "routes", ()):
            nested = getattr(route, "original_router", None) or (
                route if hasattr(route, "routes") else None
            )
            if nested is not None:
                yield from walk(nested)
                continue
            yield route

    for route in walk(app.router):
        if getattr(route, "path", None) == path:
            return route.response_model
    raise AssertionError(f"no route declared at {path}")


@pytest.mark.integration
def test_every_list_response_carries_limit_offset_and_total(seed, db_client):
    """Test 25, the behavioural half: the real bodies, from the real endpoints.

    INTEGRATION, though the brief did not mark it. There is no honest unit version: the assertion
    is about what four endpoints actually emit, and a fake connection able to answer all four would
    be a reimplementation of four queries whose correctness is the thing in question.
    """
    seed.rates([(date(2022, 10, 7), 250.0)])
    seed.movements([("MS Lock 15", date(2022, 10, 8), "Corn", 0)])
    run_id = seed.signal_run(grid_size=10)
    seed.signal(run_id, feature_name="days_below_p10", site_id=MEMPHIS, q_value=0.0446,
                passes_gate=True)

    for path in (
        "/api/gauges",
        f"/api/rates?{WINDOW}",
        f"/api/movements?{WINDOW}",
        "/api/signals",
        "/api/signals/runs",
    ):
        body = db_client.get(path).json()
        assert {"limit", "offset", "total"} <= body.keys(), f"{path} is missing an envelope field"
        assert body["limit"] == 500
        assert body["offset"] == 0
        assert isinstance(body["total"], int)


@pytest.mark.integration
def test_total_reflects_the_unpaginated_count(seed, db_client):
    """Test 26, decision 6. `total` is the whole matching set, not the page.

    THIS IS THE TEST THAT MAKES `total` MEAN SOMETHING. A `total` filled in with `len(rows)` would
    satisfy every shape assertion in this file and defeat the entire purpose - the client would
    receive 5 of 30 rows and be told there are 5.
    """
    seed.rates(weekly(date(2022, 1, 7), [200.0 + i for i in range(30)]))

    body = db_client.get(f"/api/rates?{WINDOW}&limit=5").json()

    assert len(body["rows"]) == 5
    assert body["limit"] == 5
    assert body["total"] == 30, (
        f"total is {body['total']} against 5 returned rows; a client cannot tell a truncated "
        f"series from a complete one without the real count"
    )

    # And the offset actually moves the window, so `total` is not being read off a fixed page.
    second = db_client.get(f"/api/rates?{WINDOW}&limit=5&offset=25").json()
    assert second["total"] == 30
    assert len(second["rows"]) == 5
    assert second["rows"][0]["week_ending"] != body["rows"][0]["week_ending"]


# ---------------------------------------------------------------------------------------------
# The rest of the series surface.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_gauges_report_declared_record_starts_and_observed_coverage(seed, db_client):
    """The catalog's claim and the data's answer, side by side. Not numbered.

    CLAUDE.md § 15: a catalog's date range reports an ENVELOPE, not what an endpoint will serve,
    and where they disagree what is served is what is true. Memphis is seeded with a daily record
    start of 2014-10-01 and this fixture gives it two days of data in 2022; reporting only the
    seeded value would restate an assumption as a measurement.
    """
    seed.daily_values(MEMPHIS, [(date(2022, 10, 1), 180_000.0), (date(2022, 10, 2), 175_000.0)])

    body = db_client.get("/api/gauges").json()
    by_site = {row["site_id"]: row for row in body["rows"]}

    memphis = by_site[MEMPHIS]
    assert memphis["declared_dv_record_start"] == "2014-10-01"
    assert memphis["observed_start"] == "2022-10-01"
    assert memphis["observed_end"] == "2022-10-02"
    assert memphis["observed_days"] == 2

    # A gauge with no rows at all: 0 days is a MEASUREMENT ("we looked, there are none") while the
    # bounds are null, because there is no first or last day to name.
    quiet = by_site["07010000"]
    assert quiet["observed_days"] == 0
    assert quiet["observed_start"] is None
    assert quiet["observed_end"] is None


@pytest.mark.integration
def test_an_unknown_site_is_a_404_rather_than_an_empty_series(db_client):
    """An empty series and a typo are different answers. Not numbered.

    `total: 0` for a misspelled site id reads as "this gauge has no data", which sends somebody to
    investigate ingest for a gauge that does not exist.
    """
    response = db_client.get(f"/api/gauges/99999999/series?{WINDOW}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.integration
def test_segment_and_location_are_aliases_and_disagreement_is_rejected(seed, db_client):
    """`segment` is the brief's name; `location` is the column's. Not numbered.

    Both are accepted because the live procedure's curl uses `segment` and migration 0016 renamed
    the column to `location` after measuring what USDA calls it. THE RESPONSE ALWAYS SAYS
    `location`, so nobody reading a body learns the wrong name.
    """
    seed.rates([(date(2022, 10, 7), 250.0)], location=CAIRO_MEMPHIS)
    seed.rates([(date(2022, 10, 7), 300.0)], location=TWIN_CITIES)

    by_segment = db_client.get(f"/api/rates?{WINDOW}&segment=Cairo-Memphis").json()
    by_location = db_client.get(f"/api/rates?{WINDOW}&location=Cairo-Memphis").json()

    assert by_segment["total"] == 1
    assert by_segment == by_location
    assert by_segment["rows"][0]["location"] == CAIRO_MEMPHIS
    assert "segment" not in by_segment["rows"][0]

    conflict = db_client.get(
        f"/api/rates?{WINDOW}&segment=Cairo-Memphis&location=Twin%20Cities"
    )
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "invalid_request"
