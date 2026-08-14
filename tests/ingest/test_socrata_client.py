"""Unit tier — the Socrata reader's paging, ordering, and refusals. No database, no network.

CLAUDE.md § 16. Every test here drives the real client with an injected transport over captured
fixtures; nothing in this file reaches the network.

The four failures these guard are the ones that do not look like failures:

  * a short page ending the walk       -> a truncated dataset reported as a successful read
  * a page cap returning what it has   -> the same truncation, through the safety valve
  * a query without $order             -> repeated and omitted rows, read as source noise
  * an error document read as a page   -> a rejected query reported as an empty dataset
"""

import json
from datetime import date

import pytest

from app.ingest import socrata_client
from app.ingest.socrata_client import (
    Dataset,
    DatasetNotResolvedError,
    MalformedResponseError,
    PageCapExceededError,
    SocrataClient,
    SocrataResponseError,
)

RESOLVED = Dataset(
    dataset_key="barge_rates",
    dataset_id="abcd-1234",
    domain="agtransport.usda.gov",
    description="test dataset",
)

UNRESOLVED = Dataset(
    dataset_key="barge_rates",
    dataset_id=None,
    domain="agtransport.usda.gov",
    description="test dataset, id not yet resolved by a human",
)


def page(count: int, start: int = 0) -> str:
    """A body carrying `count` records. The rows' content is irrelevant to paging."""
    return json.dumps(
        [{"week_ending": "2022-01-01T00:00:00.000", "n": start + i} for i in range(count)]
    )


def test_paging_stops_on_an_empty_page_not_a_short_one(recording_bodies):
    """A SHORT page mid-sequence does not end the walk. Only an empty page does. Test 1.

    THE WRONG VERSION IS `while len(page) == limit` AND IT READS BETTER THAN THIS ONE. Socrata
    does not guarantee that a short page is the last: a filtered query or a server-side row cap
    can return one mid-sequence. The loop then stops early, the job reports success, and the row
    count is plausible - there is nothing to distinguish a truncated read from a small dataset.

    Driven with short-then-full-then-empty, which is the ordering the wrong implementation cannot
    survive: it would return 2 of 7 records and raise nothing.
    """
    fetch, calls = recording_bodies([page(2), page(3, start=2), page(2, start=5), "[]"])
    client = SocrataClient(fetch, page_limit=3)

    records = client.fetch_all(RESOLVED, order="week_ending")

    assert len(records) == 7, (
        f"collected {len(records)} record(s) of 7. A short page ended the walk - see the module "
        f"docstring; only an EMPTY page is the end."
    )
    assert [r["n"] for r in records] == list(range(7)), "records were dropped or reordered"
    assert len(calls) == 4, f"expected four requests (three pages plus the empty one), got {calls}"

    # The offset advances by the REQUESTED limit, not by the number of rows received. Advancing by
    # the received count looks like it handles short pages and instead overlaps the next page by
    # the shortfall - which the upsert would absorb invisibly.
    offsets = [int(url.split("%24offset=")[1].split("&")[0]) for url in calls]
    assert offsets == [0, 3, 6, 9], f"offsets advanced by rows received rather than by limit: {offsets}"


def test_page_cap_raises_rather_than_returning_a_prefix(recording_bodies):
    """Hitting the cap is an error, not a shorter answer. Test 2.

    Returning what was collected would reintroduce, through the safety valve, exactly the silent
    truncation the empty-page rule exists to prevent - and it would do it in the case where
    something is already known to be wrong.
    """
    fetch, calls = recording_bodies([page(2) for _ in range(10)])
    client = SocrataClient(fetch, page_limit=2, page_cap=3)

    with pytest.raises(PageCapExceededError) as excinfo:
        client.fetch_all(RESOLVED, order="week_ending")

    message = str(excinfo.value)
    assert "3 pages" in message and "barge_rates" in message, (
        f"the error does not name the cap and the dataset: {message}"
    )
    assert "6 records collected" in message, (
        f"the error does not report how much was collected, which is the evidence an operator "
        f"needs to tell a paging bug from a large dataset: {message}"
    )
    assert len(calls) == 3, f"paging continued past the cap: {len(calls)} requests"


def test_no_request_is_issued_without_an_order_clause(recording_bodies):
    """Every query carries an explicit `$order`, asserted in the builder. Test 3.

    Socrata does not guarantee stable ordering across pages without one, and the symptom of
    leaving it out is not "paging is broken" - it is duplicate-key noise on the upsert and a few
    missing weeks, which reads like a source problem and gets investigated as one.
    """
    for missing in ("", "   "):
        with pytest.raises(ValueError) as excinfo:
            socrata_client.build_url(
                RESOLVED.domain, RESOLVED.dataset_id, order=missing, limit=10, offset=0
            )
        assert "$order" in str(excinfo.value)

    # And the real client's URLs carry it, so the assertion above is not guarding a path nothing
    # takes.
    fetch, calls = recording_bodies(["[]"])
    SocrataClient(fetch).fetch_all(RESOLVED, order="week_ending")
    assert calls and "%24order=week_ending" in calls[0], (
        f"the client issued a query without $order: {calls}"
    )


def test_a_null_dataset_id_raises_a_named_error_before_any_request(recording_bodies):
    """An unresolved dataset raises, and THE REQUEST LOG IS EMPTY. Test 4.

    "Before any request" is the whole claim. A URL built from a NULL id would be requested, would
    404, and would be indistinguishable from the network being down - sending the operator to
    investigate connectivity for a problem whose fix is a human at the USDA catalog.
    """
    fetch, calls = recording_bodies([page(1)])
    client = SocrataClient(fetch)

    with pytest.raises(DatasetNotResolvedError) as excinfo:
        client.fetch_all(UNRESOLVED, order="week_ending")

    assert calls == [], f"a request was issued for an unresolved dataset: {calls}"
    assert "barge_rates" in str(excinfo.value), (
        f"the error does not name the key a human has to resolve: {excinfo.value}"
    )

    # The builder refuses too, so no caller can route around resolve_dataset() by constructing a
    # URL directly.
    with pytest.raises(DatasetNotResolvedError):
        socrata_client.build_url(UNRESOLVED.domain, None, order="week_ending")


def test_an_error_body_is_distinguished_from_an_empty_page(socrata_body, recording_bodies):
    """An error document raises; an empty page ends the walk quietly. Test 5.

    BOTH ARE VALID JSON WITH A LENGTH, which is what makes collapsing them so easy. Socrata
    reports a rejected query - a column name that does not exist, a malformed $where - as an
    object with `error: true`. Treated as "a page with no rows" it would end the loop and report a
    successful read of an empty dataset, which is the USGS empty-`timeSeries` failure in a
    different costume (CLAUDE.md § 14).

    Both halves are asserted in one test, because two separate tests can each be satisfied by one
    wrong implementation.
    """
    fetch, calls = recording_bodies([socrata_body("error_body")])
    with pytest.raises(SocrataResponseError) as excinfo:
        SocrataClient(fetch).fetch_all(RESOLVED, order="week_ending")

    message = str(excinfo.value)
    assert "week_endng" in message, (
        f"the error does not carry Socrata's own message, which names the bad column: {message}"
    )
    assert "NOT an empty page" in message
    assert len(calls) == 1

    # The other half: an empty page is NOT an error. It is how a complete read ends.
    fetch, calls = recording_bodies([socrata_body("page_1"), socrata_body("page_2_empty")])
    records = SocrataClient(fetch, page_limit=3).fetch_all(RESOLVED, order="week_ending")
    assert len(records) == 3, "the empty page was treated as an error or the first page was lost"
    assert len(calls) == 2

    # And a body that is not JSON at all is a third outcome, distinct from both.
    fetch, _calls = recording_bodies(["<html>502 Bad Gateway</html>"])
    with pytest.raises(MalformedResponseError):
        SocrataClient(fetch).fetch_all(RESOLVED, order="week_ending")


def test_the_rates_fixture_is_a_page_of_records(socrata_body):
    """The captured shape parses as a page. Guards the fixture, not the client.

    A fixture that silently stopped being a list of objects would make every test above pass
    against a shape the live service does not send.
    """
    records = socrata_client.parse_page(socrata_body("rates_nearby"))
    assert isinstance(records, list) and records
    assert all(isinstance(r, dict) for r in records)


# ---------------------------------------------------------------------------------------------
# Integration tier — the dataset seed.
# ---------------------------------------------------------------------------------------------


@pytest.mark.integration
def test_usda_datasets_seed_carries_five_keys_with_real_ids(migrated_db):
    """Five keys, five human-resolved ids, and the measured bounds beside them. Test 11.

    RESOLVED BY A HUMAN AGAINST THE LIVE CATALOG ON 2026-08-14, which is what migration 0016
    lands. Until then every id was NULL and every client path raised before issuing a request -
    this project does not guess a four-four Socrata token (CLAUDE.md § 1), and an invented one
    fails as a 404 that reads like a network fault rather than as a wrong answer.

    Exact set equality on keys AND ids, because the failure worth catching is not a missing key -
    it is an id quietly changed to one nobody measured, which reads identically to a correct one
    until the rows arrive.

    `cost_indicators` HAS AN ID AND NO BOUNDS, deliberately: it was found in the same catalog
    query and nothing has counted it. A seeded bound that nobody measured is precisely what
    CLAUDE.md § 15 forbids, and its presence here is a record of what exists rather than a promise
    that it is loaded.
    """
    datasets = socrata_client.load_datasets(migrated_db)

    assert {key: d.dataset_id for key, d in datasets.items()} == {
        "barge_rates_nearby": "deqi-uken",
        "barge_rates_1month": "svms-9yya",
        "barge_rates_3month": "uuhv-5etw",
        "lock_movements": "n4pw-9ygw",
        "cost_indicators": "8uye-ieij",
    }, (
        f"the seeded keys and ids are {{k: d.dataset_id for k, d in datasets.items()}}. These are "
        f"human-measured facts about the world; changing one is a new migration, not an edit."
    )

    for key, dataset in datasets.items():
        assert dataset.domain and dataset.description

    # The three rates datasets carry the SAME measured coverage - they are three publications of
    # one series - and each carries the row count its truncation check compares against.
    for key in ("barge_rates_nearby", "barge_rates_1month", "barge_rates_3month"):
        dataset = datasets[key]
        assert dataset.first_period == date(2004, 1, 7)
        assert dataset.last_period == date(2026, 8, 11)

    assert datasets["lock_movements"].first_period == date(2003, 1, 4)
    assert datasets["lock_movements"].last_period == date(2026, 8, 8)

    assert (
        datasets["cost_indicators"].first_period is None
        and datasets["cost_indicators"].last_period is None
    ), (
        "cost_indicators carries period bounds. Nothing measured them - it is seeded, not "
        "fetched - and a bound that was not measured is the Phase 3 failure repeated "
        "(CLAUDE.md § 15)."
    )

    counts = {
        row[0]: row[1]
        for row in migrated_db.execute(
            "SELECT dataset_key, source_row_count FROM usda_datasets"
        ).fetchall()
    }
    assert counts == {
        "barge_rates_nearby": 8260,
        "barge_rates_1month": 8260,
        "barge_rates_3month": 8260,
        "lock_movements": 26144,
        "cost_indicators": None,
    }, (
        f"the seeded row counts are {counts}. They are the floor a backfill is checked against; "
        f"landing fewer rows than these means the pager truncated (CLAUDE.md § 16)."
    )

    # An unknown key is a DIFFERENT error from an unresolved one: one is fixed by a migration
    # adding a key, the other by a human at the catalog. Both paths still exist, and the
    # unresolved one is now only reachable by a future key - so it is exercised against a row
    # constructed here rather than against the seed.
    with pytest.raises(socrata_client.SocrataError) as excinfo:
        socrata_client.resolve_dataset(migrated_db, "no_such_key")
    assert not isinstance(excinfo.value, DatasetNotResolvedError)

    migrated_db.execute(
        "INSERT INTO usda_datasets (dataset_key, dataset_id, domain, description)"
        " VALUES ('unresolved_probe', NULL, 'agtransport.usda.gov', 'probe')"
    )
    with pytest.raises(DatasetNotResolvedError) as excinfo:
        socrata_client.resolve_dataset(migrated_db, "unresolved_probe")
    assert "not yet resolved" in str(excinfo.value)
    assert "CONTEXT.md" in str(excinfo.value), (
        "the error does not point at where the resolution procedure is written down"
    )
    migrated_db.rollback()

    # The database rejects a malformed id, so a title or an unhyphenated token cannot land as one.
    with pytest.raises(Exception):
        migrated_db.execute(
            "UPDATE usda_datasets SET dataset_id = 'abcd1234' WHERE dataset_key = "
            "'barge_rates_nearby'"
        )
    migrated_db.rollback()
