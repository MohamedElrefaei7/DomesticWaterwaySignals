"""The Socrata reader: paged, ordered, and refusing to guess.

CLAUDE.md § 16. USDA AgTransport publishes through Socrata, which is a paged API, and paged APIs
fail differently from the USGS windows this project already reads. Three of those differences are
what this module is arranged around.

PAGING TERMINATES ON AN EMPTY PAGE, NEVER ON A SHORT ONE
--------------------------------------------------------
The obvious loop is `while len(page) == limit`. It reads naturally, it is one line shorter, and it
SILENTLY TRUNCATES A DATASET: Socrata does not guarantee that a page shorter than `$limit` is the
last page - a filtered query, a server-side row cap, or a slow backend can return a short page
mid-sequence. The job then reports success having collected a prefix, and nothing distinguishes
that from a dataset that really is that small. CLAUDE.md § 2 theme 1, with a row count that looks
plausible.

So the loop runs until a page returns ZERO rows, and a page cap RAISES rather than returning what
it has. Returning the prefix at the cap would reintroduce the same silent truncation through the
safety valve meant to prevent it.

EVERY QUERY CARRIES AN EXPLICIT `$order`
----------------------------------------
Socrata does not guarantee stable ordering across pages without one. Without `$order`, paging can
repeat rows and omit others - and the symptom is not "paging is broken", it is duplicate-key noise
on the upsert and a few missing weeks, which reads like a source problem and gets investigated as
one. `build_url` refuses to build a query without it.

DATASET IDENTIFIERS ARE RESOLVED BY A HUMAN
-------------------------------------------
A Socrata id is a four-four token (`abcd-1234`). This project does not guess one (CLAUDE.md § 1):
an invented id produces a 404 that reads like a network fault. They live in `usda_datasets`
(migration 0013), NULL until resolved, and every call path raises a named error naming the key
BEFORE any request is issued - never a URL built around the string "None", which would 404 exactly
like a network fault and send the operator to the wrong place.

TRANSPORT
---------
Injected callable, defaulting to a small urllib one, exactly as the USGS clients do. NO TEST IN
THIS REPO MAKES A LIVE HTTP REQUEST.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120

# Socrata's own default page size is 1,000 and its documented maximum for the JSON endpoint is
# 50,000. A thousand is a comfortable response and makes the page count legible in a log; these
# datasets are thousands of rows, so this is a handful of requests either way.
DEFAULT_PAGE_LIMIT = 1000

# THE CAP EXISTS TO TURN A RUNAWAY INTO A FAILURE, NOT INTO A SHORTER ANSWER.
#
# 500 pages at the default limit is 500,000 rows - two orders of magnitude above anything these
# weekly datasets can hold, so hitting it means a paging bug (an offset that stopped advancing, a
# server ignoring `$offset`) rather than a large dataset. That is precisely when returning what
# was collected would be worst: it would be a plausible-looking prefix of a broken read.
DEFAULT_PAGE_CAP = 500


class SocrataError(RuntimeError):
    """Base for every refusal in this module. Always names the dataset involved."""


class DatasetNotResolvedError(SocrataError):
    """The dataset id is NULL in `usda_datasets`: a human has not resolved it yet.

    Raised BEFORE any request. A URL built from a NULL id would be requested, would 404, and would
    be indistinguishable from the network being down - so the one thing this error must do is
    happen earlier than that, and name the key and the fix.
    """


class PageCapExceededError(SocrataError):
    """Paging hit its bound. Raised rather than returning the rows collected so far."""


class SocrataResponseError(SocrataError):
    """The service returned an error document rather than a page of rows.

    A DISTINCT OUTCOME FROM AN EMPTY PAGE, and never collapsed into one. Socrata reports a bad
    SoQL query as a JSON object with `error: true` - which is a truthy body, parses cleanly, and
    has a length. Treated as "a page with no rows" it would terminate the loop and report a
    successful read of nothing at all, which is the same shape of failure as a 200 carrying an
    empty `timeSeries` in the USGS client (CLAUDE.md § 14).
    """


class MalformedResponseError(SocrataError):
    """The body parsed as JSON but is not a page of rows and not an error document."""


@dataclass(frozen=True)
class Dataset:
    """One row of `usda_datasets`: what a key means, and whether it is resolved yet."""

    dataset_key: str
    dataset_id: str | None
    domain: str
    description: str
    first_period: date | None = None
    last_period: date | None = None

    @property
    def resolved(self) -> bool:
        return self.dataset_id is not None


DATASET_COLUMNS = (
    "dataset_key",
    "dataset_id",
    "domain",
    "description",
    "first_period",
    "last_period",
)


def load_datasets(conn) -> dict[str, Dataset]:
    """Every seeded dataset, by key. Includes the unresolved ones, which is the point.

    An unresolved dataset is not filtered out here. A loader that silently dropped rows with a
    NULL id would make "the key is not seeded" and "the id is not resolved" produce the same
    KeyError, and those have different fixes - one is a migration adding a key, the other is a
    human at the catalog.
    """
    rows = conn.execute(
        f"SELECT {', '.join(DATASET_COLUMNS)} FROM usda_datasets ORDER BY dataset_key"
    ).fetchall()

    if not rows:
        raise SocrataError(
            "the usda_datasets table is empty. Migration 0013 seeds three keys; either it has "
            "not been applied or the rows were removed. Run `python3 -m app.orchestration.migrate`."
        )

    return {
        row[0]: Dataset(
            dataset_key=row[0],
            dataset_id=row[1],
            domain=row[2],
            description=row[3],
            first_period=row[4],
            last_period=row[5],
        )
        for row in rows
    }


def resolve_dataset(conn, dataset_key: str) -> Dataset:
    """The dataset for this key, or raise. NEVER returns one with a NULL id.

    The single place the NULL check lives, so no caller can reach a request path with an
    unresolved dataset by taking a different route to it.
    """
    datasets = load_datasets(conn)

    if dataset_key not in datasets:
        raise SocrataError(
            f"no dataset seeded under the key {dataset_key!r}. Known keys: "
            f"{sorted(datasets)}. Dataset keys are human-owned (CLAUDE.md § 1) - add one by "
            f"writing a new numbered migration, not by passing it on the command line."
        )

    dataset = datasets[dataset_key]
    if not dataset.resolved:
        raise DatasetNotResolvedError(
            f"dataset id not yet resolved for key {dataset_key!r}; see CONTEXT.md § Up Next.\n"
            f"  Migration 0013 seeds this key with a NULL id because the agent that wrote it "
            f"could not reach the USDA catalog and does not guess identifiers (CLAUDE.md § 1).\n"
            f"  A human resolves the four-four id at https://{dataset.domain} and lands it - with "
            f"first_period/last_period from a COUNTED FULL-RANGE query, never a sampled window "
            f"(CLAUDE.md § 15) - in a NEW numbered migration.\n"
            f"  Nothing was requested: a URL built from a NULL id 404s and reads like a network "
            f"fault."
        )
    return dataset


# ---------------------------------------------------------------------------------------------
# The query.
# ---------------------------------------------------------------------------------------------


def build_url(
    domain: str,
    dataset_id: str | None,
    *,
    order: str,
    select: str | None = None,
    where: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> str:
    """One page's URL.

    `order` is REQUIRED and is asserted here rather than defaulted. A default would be a global
    guess about a column name that varies per dataset; an assertion makes the caller state the
    column it is paging by, which is the caller that knows.
    """
    if dataset_id is None:
        raise DatasetNotResolvedError(
            "build_url called with a NULL dataset id. Resolve the dataset through "
            "resolve_dataset(), which raises before any request is built - a URL containing the "
            "word 'None' would 404 and read like a network fault."
        )
    if not str(order).strip():
        raise ValueError(
            "build_url called without an $order clause. Socrata does not guarantee stable "
            "ordering across pages without one, so paging can repeat and omit rows - and the "
            "symptom is duplicate-key noise plus a few missing periods, which reads like a source "
            "problem rather than a paging bug (CLAUDE.md § 16)."
        )
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must not be negative, got {offset}")

    query = {"$order": order, "$limit": limit, "$offset": offset}
    if select is not None:
        query["$select"] = select
    if where is not None:
        query["$where"] = where

    return (
        f"https://{domain}/resource/{dataset_id}.json?"
        f"{urllib.parse.urlencode(query)}"
    )


def _urllib_fetch(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """The default transport. Returns the body as text, or raises naming the URL.

    Deliberately thin and deliberately not retrying, for the same reason the USGS one is not: the
    caller knows whether this is a long backfill or a weekly poll, and a retry policy buried here
    would apply to both.

    An HTTP error body is READ AND INCLUDED, truncated. Socrata puts the reason a query was
    rejected in the body - a column name that does not exist, a malformed `$where` - and an error
    that reports only "HTTP 400" sends the operator to reconstruct the query by hand
    (CLAUDE.md § 13).
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SocrataError(f"Socrata returned HTTP {exc.code} for {url}\n  body: {body}") from exc
    except urllib.error.URLError as exc:
        raise SocrataError(f"could not reach Socrata at {url}: {exc.reason}") from exc


def parse_page(body: str, *, url: str = "(url not supplied)") -> list[dict]:
    """One response body into a list of records, distinguishing all three outcomes.

    A page of rows, an EMPTY page, and an ERROR DOCUMENT arrive looking similar - all three are
    valid JSON with a length - and they mean different things:

        [ {...}, {...} ]                     rows. Keep paging.
        [ ]                                  the end of the dataset. Stop.
        { "error": true, "message": ... }    the query was rejected. RAISE.

    The third is the one that must not be collapsed. As "a page with no rows" it would end the
    loop and report a successful read of nothing, which is exactly the USGS empty-`timeSeries`
    failure in a different costume (CLAUDE.md § 14).
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError(
            f"Socrata returned a body that is not JSON for {url}\n"
            f"  decoder said: {exc}\n"
            f"  first 300 bytes: {body[:300]!r}"
        ) from exc

    if isinstance(payload, dict):
        if payload.get("error"):
            raise SocrataResponseError(
                f"Socrata REJECTED the query for {url}\n"
                f"  code:    {payload.get('code', '(none)')}\n"
                f"  message: {payload.get('message', '(none)')}\n"
                f"  This is an error document, NOT an empty page. The usual causes are a column "
                f"name that does not exist in this dataset (check $order and $where against the "
                f"resolved dataset) and a malformed SoQL clause."
            )
        raise MalformedResponseError(
            f"Socrata returned a JSON object rather than a list of records for {url}, and it "
            f"carries no `error` key. Top-level keys: {sorted(payload)}"
        )

    if not isinstance(payload, list):
        raise MalformedResponseError(
            f"Socrata returned {type(payload).__name__} rather than a list of records for {url}"
        )

    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise MalformedResponseError(
                f"record {index} in the page from {url} is {type(record).__name__}, expected an "
                f"object"
            )
    return payload


def parse_period_label(raw, *, field: str = "period") -> date:
    """'2022-10-04T00:00:00.000' -> date(2022, 10, 4). NO TIMEZONE ARITHMETIC WHATSOEVER.

    The same decision as the USGS daily parser, and the same reasoning (CLAUDE.md § 15): the
    function splits on 'T' and reads the date part, and it NEVER CONSTRUCTS A DATETIME - because
    the moment a datetime exists, something calls `.astimezone()` on it and the published week
    label shifts by a day for half the world. A rate published for the week ending 2022-10-04
    belongs to 2022-10-04 in Denver and in Tokyo.

    Socrata's "floating timestamp" type is exactly this: a calendar instant with no zone. An
    offset-BEARING value is refused rather than truncated, because it would mean the publisher had
    started making a claim about an instant, and silently discarding that claim is how two
    parsing paths merge by accident.
    """
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise MalformedResponseError(
            f"{field} is {raw!r} ({type(raw).__name__}), expected a date string. A record with no "
            f"period cannot be keyed and is not defaulted to anything."
        )

    day, _, time_part = raw.partition("T")

    if any(marker in time_part for marker in ("+", "Z", "z")) or "-" in time_part:
        raise MalformedResponseError(
            f"{field} {raw!r} carries a UTC offset. Published period labels are calendar dates and "
            f"are stored as stated; an offset means the publisher changed what it is publishing, "
            f"and truncating it silently would route a weekly label through timezone arithmetic "
            f"(CLAUDE.md § 15)."
        )

    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise MalformedResponseError(f"could not parse {field} {raw!r}: {exc}") from exc


class _Absent:
    """The type of ABSENT. A class so the sentinel has a legible repr in a traceback."""

    def __repr__(self) -> str:  # pragma: no cover - debugging affordance
        return "<no value published>"

    def __bool__(self) -> bool:
        return False


# "THE SOURCE PUBLISHED NO VALUE HERE", distinct from None.
#
# Distinct because None is a value a parser can legitimately return, and `raw is ABSENT` is a
# question with one answer where `raw is None` is a question about two different things. The
# distinction costs one object and removes the branch where "absent" and "parsed to nothing" are
# told apart by whoever is reading the code at the time.
ABSENT = _Absent()


def required_field(record: dict, key: str, *, context: str) -> object:
    """One field that MUST be present, or raise naming what the record actually carries.

    NEVER `.get(key)`. A missing field returning None becomes a NULL column or a zero, and the
    first ingest client this project wrote had exactly that bug - a required field hardcoded to
    None while the layer reported success (CLAUDE.md § 2, theme 1).

    THIS IS FOR FIELDS WHOSE ABSENCE IS A MAPPING ERROR - the ones that key a row. A field the
    source legitimately omits goes through `optional_field` instead, and the difference is a
    measurement rather than a matter of taste: `rate` is absent from 774 of 8,260 published rate
    records because the river was closed (migration 0017), while a record with no `date` or no
    `location` cannot be keyed and is not a fact about anything.
    """
    if key not in record:
        raise MalformedResponseError(
            f"{context}: record has no field {key!r}. Fields present: {sorted(record)}.\n"
            f"  This field KEYS THE ROW, so its absence is a mapping error rather than an "
            f"unpublished value: a record that cannot be keyed can never be corrected or "
            f"superseded. If the published name has changed, correct the mapping in the module "
            f"that raised this.\n"
            f"  DO NOT reach for `optional_field` to make this go away. That is the right tool "
            f"only where the source is KNOWN AND MEASURED to omit a value - it records the "
            f"absence as NULL, and doing that to a key field would write unkeyable rows and "
            f"report success."
        )
    return record[key]


def optional_field(record: dict, key: str, *, context: str) -> object:
    """One field the source may legitimately omit: its value, or ABSENT.

    THREE CONDITIONS, AND THEY ARE NOT THE SAME (CLAUDE.md § 16):

        key absent from the record      the source published no value. ABSENT.
        key present, value null         the source published an explicit nothing. ABSENT.
        key present, value anything else returned as-is, FOR THE CALLER'S PARSER TO ACCEPT OR
                                        REJECT. An unparseable value raises there, naming itself.

    The two ABSENT cases are deliberately collapsed because they mean the same thing about the
    world - USDA expresses "no rate this week" by omitting the key, and an explicit null would be
    the same statement spelled differently. The third case is never collapsed into them, and that
    is the whole reason this function exists rather than a `record.get(key)`:

        rate = record.get("rate")

    reads identically, is one line shorter, and turns a CORRUPT value into a winter closure. The
    row then says "the river was shut" about a week the river was open, in a column where that is
    a completely ordinary thing to say - so nothing downstream can tell, and 774 legitimate NULLs
    are exactly the camouflage a 775th would hide in.
    """
    if key not in record:
        return ABSENT
    value = record[key]
    if value is None:
        return ABSENT
    return value


# ---------------------------------------------------------------------------------------------
# The client.
# ---------------------------------------------------------------------------------------------


class SocrataClient:
    """Paged reads of one Socrata dataset, verified.

    `fetch` is injected so the whole paging, parsing and refusal path runs offline against
    fixtures. The default reaches the real service; nothing in the test suite uses it.
    """

    def __init__(
        self,
        fetch=None,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        page_cap: int = DEFAULT_PAGE_CAP,
    ):
        self._fetch = _urllib_fetch if fetch is None else fetch
        self._timeout = timeout
        self._page_limit = page_limit
        self._page_cap = page_cap

    def fetch_all(
        self,
        dataset: Dataset,
        *,
        order: str,
        select: str | None = None,
        where: str | None = None,
    ) -> list[dict]:
        """Every record matching this query, across as many pages as it takes.

        TERMINATES ON AN EMPTY PAGE. A short page is not the end - see the module docstring - so a
        short page is collected and paging continues from the next offset.

        The offset advances by the REQUESTED limit rather than by the number of rows received.
        Advancing by the received count is the change that pairs with short-page termination: it
        looks like it handles short pages, and it makes the next page overlap the previous one by
        the shortfall, which the upsert then absorbs invisibly.
        """
        if not dataset.resolved:
            # Defence in depth. resolve_dataset() is the gate; this is here so a caller that
            # constructed a Dataset by hand cannot route around it, and so the request log is
            # provably empty when it fires.
            raise DatasetNotResolvedError(
                f"dataset id not yet resolved for key {dataset.dataset_key!r}; see CONTEXT.md "
                f"§ Up Next. Nothing was requested."
            )

        records: list[dict] = []
        offset = 0

        for page_number in range(1, self._page_cap + 1):
            url = build_url(
                dataset.domain,
                dataset.dataset_id,
                order=order,
                select=select,
                where=where,
                limit=self._page_limit,
                offset=offset,
            )
            page = parse_page(self._fetch(url, self._timeout), url=url)

            if not page:
                logger.info(
                    "%s: %d record(s) over %d page(s); page %d was empty, which is the end",
                    dataset.dataset_key,
                    len(records),
                    page_number - 1,
                    page_number,
                )
                return records

            records.extend(page)
            if len(page) < self._page_limit:
                # NOT A STOPPING CONDITION. Logged because it is worth seeing, and because the
                # next reader of this loop deserves to know the short page was noticed and
                # deliberately not acted on.
                logger.info(
                    "%s: page %d returned %d of %d requested rows - SHORT, not necessarily last; "
                    "paging continues until a page is empty",
                    dataset.dataset_key,
                    page_number,
                    len(page),
                    self._page_limit,
                )
            offset += self._page_limit

        raise PageCapExceededError(
            f"paging {dataset.dataset_key!r} hit the cap of {self._page_cap} pages at "
            f"{self._page_limit} rows each ({len(records)} records collected) without an empty "
            f"page.\n"
            f"  RAISED RATHER THAN RETURNING WHAT WAS COLLECTED: {len(records)} records is a "
            f"plausible-looking prefix, and returning it would report a successful read of a "
            f"truncated dataset.\n"
            f"  At this size the likely cause is a paging fault - an offset that stopped "
            f"advancing, or a service ignoring $offset - rather than a genuinely large dataset. "
            f"These are weekly series of thousands of rows."
        )
