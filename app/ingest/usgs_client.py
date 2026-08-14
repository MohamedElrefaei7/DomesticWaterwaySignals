"""The USGS instantaneous-values client. Parses, verifies, and refuses.

CLAUDE.md § 14. Everything in this file is arranged around one assertion, and it is the single
most important line in Phase 3:

    THE SET OF (site, parameter) PAIRS IN THE RESPONSE MUST EQUAL THE SET THAT WAS REQUESTED.

MEASURED AGAINST THE LIVE SERVICE ON 2026-08-13, and it is the reason this file is not the
obvious loop: A REQUEST FOR A SERIES A SITE DOES NOT SERVE RETURNS HTTP 200 WITH
`"timeSeries": []`. No error. No flag. No marker anywhere in the payload. When several sites are
requested together, the missing ones are simply absent from the array while the others return
normally, at the correct row counts.

The obvious implementation — iterate `timeSeries`, write whatever is in it — is shorter, reads
better, and never raises. It is also indistinguishable from correct on every single run. A site
that drops out of the feed permanently would produce a job that reports success, forever, with a
row count nobody notices is smaller than last month's. That is CLAUDE.md § 2's theme 1 exactly:
a layer reporting success while the thing downstream gets nothing.

THE DISTINCTION THIS FILE MUST NEVER COLLAPSE
---------------------------------------------
  * A requested series that is ABSENT from the response  -> MissingSeriesError. Hard failure.
  * A present series whose `values` are EMPTY            -> zero readings. Not an error.

Gaps are ordinary: the first eight St. Louis readings on 2026-08-01 skip 02:30, and a backfill
window covering a sensor outage legitimately returns nothing. Treating an empty window as fatal
would make the backfill unrunnable; treating a missing series as an empty window would restore
the exact blindness the assertion exists to remove. tests/ingest/test_backfill_chunking.py's
`test_an_empty_window_advances_and_a_missing_pair_aborts` holds both behaviours in one test so
they cannot be collapsed into each other without a failure.

TRANSPORT
---------
The HTTP call is an injected callable (`fetch`), defaulting to a small urllib-based one. Same
pattern as infra/provision's explicitly-injected command runner, and for the same reason: tests
drive the real parsing and verification code with no network and no monkeypatching of a global.
NO TEST IN THIS REPO MAKES A LIVE HTTP REQUEST.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# The endpoint, PINNED.
# ---------------------------------------------------------------------------------------------
#
# CLAUDE.md § 6: build against a specific, named endpoint set and pin it; never a moving default.
# USGS is migrating to a modernized OGC API, and the legacy waterservices host will eventually
# stop being the default anyone means by "the USGS API".
#
# `format=json,1.1` pins the WaterML version the parser below is written against. A bare
# `format=json` accepts whatever the service considers current, which is the API equivalent of an
# unpinned image tag — the payload shape changes under you and the parser starts finding nothing
# where it used to find everything.
IV_ENDPOINT = "https://waterservices.usgs.gov/nwis/iv/"
IV_FORMAT = "json,1.1"

# USGS documents 00060 as discharge in cubic feet per second and 00065 as gage height in feet.
# 00065 is named here for the error messages and the seed guard, NOT because anything requests
# it: this commit ingests discharge only (CLAUDE.md § 14, CONTEXT.md).
PARAM_DISCHARGE = "00060"
PARAM_GAGE_HEIGHT = "00065"

DEFAULT_TIMEOUT_SECONDS = 120


class UsgsError(RuntimeError):
    """Base for every refusal in this module. Always names the sites and parameters involved."""


class MissingSeriesError(UsgsError):
    """A requested (site, parameter) pair was absent from a 200 response.

    THE ONE THIS FILE EXISTS FOR. Never caught and turned into an empty result anywhere in this
    project; a caller that swallows it has reintroduced the blindness by hand.
    """


class MalformedResponseError(UsgsError):
    """The payload did not have the shape the pinned format promises.

    Raised rather than skipped: a response we cannot parse is not a response with no data in it,
    and quietly returning zero readings for one would be the same failure in a different costume.
    """


@dataclass(frozen=True)
class Reading:
    """One instantaneous value, as published.

    ts is TIMEZONE-AWARE AND IN UTC. Never naive: the API returns local offsets, these sites span
    Central and Eastern observance, and a naive datetime is a number that has silently lost the
    fact that it means something different in November than it did in June.
    """

    usgs_site_id: str
    ts: datetime
    param_code: str
    value: float
    qualifiers: tuple[str, ...]


def _urllib_fetch(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """The default transport. Returns the response body as text, or raises.

    Deliberately thin, and deliberately not retrying. A retry policy that lives here would retry
    the backfill's 90-day windows silently, and the caller — which knows whether it is a
    long-running backfill or an hourly poll — is the layer that should decide. Errors carry the
    URL because a failing request whose message does not say what was requested sends the
    operator off to reconstruct it by hand (CLAUDE.md § 13).
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise UsgsError(
            f"USGS returned HTTP {exc.code} for {url}\n  body: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise UsgsError(f"could not reach USGS at {url}: {exc.reason}") from exc


def build_url(
    site_ids,
    param_codes,
    start: datetime,
    end: datetime,
    *,
    endpoint: str = IV_ENDPOINT,
) -> str:
    """The request URL for one window.

    start and end are TZ-AWARE and serialized in explicit UTC ('2026-08-01T00:00:00Z'). Bare
    dates are the more commonly seen form and the service accepts them, but it interprets them in
    each site's own local time — so a window boundary means a different instant at Baton Rouge
    than at St. Louis, and the backfill's windows would neither tile nor be reproducible. The
    explicit offset removes the ambiguity entirely.

    `end` is INCLUSIVE at the service, which is why backfill.py hands this function a value one
    second below the next window's start. Getting that wrong duplicates a boundary reading, which
    the upsert would absorb silently — a bug that costs nothing and teaches nothing.
    """
    sites = sorted(set(site_ids))
    params = sorted(set(param_codes))
    if not sites:
        raise ValueError("build_url called with no sites")
    if not params:
        raise ValueError("build_url called with no parameter codes")

    query = urllib.parse.urlencode(
        {
            "format": IV_FORMAT,
            "sites": ",".join(sites),
            "parameterCd": ",".join(params),
            "startDT": _iso_utc(start),
            "endDT": _iso_utc(end),
            # Include sites the service considers inactive. Without it a gauge that was
            # discontinued mid-record silently returns nothing for its later windows — which,
            # being a 200 with an absent series, the assertion below would correctly reject, but
            # the operator would be debugging the wrong thing.
            "siteStatus": "all",
        }
    )
    return f"{endpoint}?{query}"


def _iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError(
            f"naive datetime {moment!r} passed to the USGS client. Every timestamp crossing this "
            f"boundary is tz-aware; a naive one here would be interpreted in whatever the "
            f"server's local zone happens to be."
        )
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# Parsing and verification.
# ---------------------------------------------------------------------------------------------


def _series_pair(series: dict) -> tuple[str, str]:
    """The (site, parameter) this series is for.

    Raises rather than skipping an unrecognizable series. A series we cannot identify cannot be
    matched against the requested set, so silently dropping it would let a genuinely missing pair
    hide behind a parse failure.
    """
    try:
        site = series["sourceInfo"]["siteCode"][0]["value"]
        param = series["variable"]["variableCode"][0]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MalformedResponseError(
            f"a timeSeries entry has no identifiable siteCode/variableCode pair "
            f"({type(exc).__name__}: {exc}). Series name: {series.get('name', '<absent>')!r}"
        ) from exc
    return str(site), str(param)


def present_pairs(payload: dict) -> set[tuple[str, str]]:
    """Every (site, parameter) pair actually present in the response."""
    return {_series_pair(series) for series in _time_series(payload)}


def _time_series(payload: dict) -> list:
    try:
        series = payload["value"]["timeSeries"]
    except (KeyError, TypeError) as exc:
        raise MalformedResponseError(
            f"response has no value.timeSeries key ({type(exc).__name__}: {exc}). Top-level keys "
            f"present: {sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
        ) from exc
    if not isinstance(series, list):
        raise MalformedResponseError(
            f"value.timeSeries is {type(series).__name__}, expected a list"
        )
    return series


def verify_pairs(payload: dict, requested: set[tuple[str, str]]) -> None:
    """Assert the response carries every pair that was asked for. Raise naming the gap.

    THE ASSERTION. Set equality against what was REQUESTED, not a sanity check on what arrived.

    The message names both halves — what was requested and satisfied, and what was requested and
    is missing — because the two failures behind this look identical from the outside and need
    different responses (CLAUDE.md § 13, a check reports the observed value on failure):

      * ALL pairs missing -> usually the window, the endpoint, or the whole service.
      * SOME pairs missing -> that site genuinely stopped serving that parameter, and the fix is
        to correct `gauges.available_params` deliberately, not to make the client tolerant.

    Note what is NOT checked: extra pairs in the response that were not requested. The service
    does not volunteer series, and a stray one would be discarded by the caller anyway. Failing on
    it would turn a harmless upstream addition into an outage.
    """
    present = present_pairs(payload)
    missing = requested - present
    if not missing:
        return

    satisfied = sorted(requested & present)
    raise MissingSeriesError(
        "USGS returned HTTP 200 without every requested series. A missing series is NOT zero "
        "rows - the response carries no marker distinguishing the two, which is why this is "
        "checked rather than trusted (CLAUDE.md § 14).\n"
        f"  requested: {sorted(requested)}\n"
        f"  present:   {satisfied if satisfied else '(none)'}\n"
        f"  MISSING:   {sorted(missing)}\n"
        "If a site has genuinely stopped serving a parameter, correct that site's "
        "available_params in a new migration - do not relax this check."
    )


def _parse_timestamp(raw: str) -> datetime:
    """'2026-08-01T00:00:00.000-05:00' -> an aware UTC datetime.

    The offset is PARSED AND APPLIED, never stripped and never assumed. These sites observe
    Central and Eastern time, so the offset in the payload changes twice a year; a client that
    dropped it would shift an hour of readings at each DST transition, in a way that looks like
    the river did something and that nothing downstream can detect.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise MalformedResponseError(
            f"could not parse dateTime {raw!r}: {type(exc).__name__}: {exc}"
        ) from exc
    if parsed.tzinfo is None:
        raise MalformedResponseError(
            f"dateTime {raw!r} carries no UTC offset. Every reading this project stores is "
            f"anchored to a real instant; a local wall clock with no offset is not one."
        )
    return parsed.astimezone(timezone.utc)


def _no_data_value(series: dict) -> float | None:
    """The sentinel this series uses for a missing reading, if it declares one.

    USGS publishes -999999 as `variable.noDataValue` and then emits it as an ordinary value.
    Stored as-is it would be a discharge of negative one million cubic feet per second: a number
    that breaks every aggregate it touches and that looks, to anything reading the table, like
    real data. Read from the payload rather than hardcoded, because it is the series that
    declares it.
    """
    raw = series.get("variable", {}).get("noDataValue")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "series %r declares a non-numeric noDataValue %r; no sentinel filtering applied",
            series.get("name"),
            raw,
        )
        return None


def _readings_in(series: dict) -> list[Reading]:
    site, param = _series_pair(series)
    sentinel = _no_data_value(series)

    readings: list[Reading] = []
    # `values` is a LIST of blocks, one per measurement method. Iterating only values[0] - which
    # is what every example does, because there is almost always exactly one - silently drops a
    # whole method's readings at a site that has two.
    for block in series.get("values", []) or []:
        for entry in block.get("value", []) or []:
            raw_value = entry.get("value")
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise MalformedResponseError(
                    f"non-numeric value {raw_value!r} for {site}/{param} at "
                    f"{entry.get('dateTime')!r}: {type(exc).__name__}: {exc}"
                ) from exc

            if sentinel is not None and value == sentinel:
                # Dropped, and counted as never having been published. Distinct from a gap only
                # in that USGS chose to transmit a row saying so.
                continue

            readings.append(
                Reading(
                    usgs_site_id=site,
                    ts=_parse_timestamp(entry.get("dateTime")),
                    param_code=param,
                    value=value,
                    # PRESERVED, NOT FILTERED. 'P' (provisional) covers most of the recent
                    # record - the part the signal runs on - and dropping it would shrink the
                    # useful data to nothing while reporting success with a smaller row count.
                    qualifiers=tuple(entry.get("qualifiers") or ()),
                )
            )
    return readings


def readings_from(payload: dict, requested: set[tuple[str, str]]):
    """Verify, THEN yield. Returns a generator; raises before it is ever iterated.

    THE ORDER IS THE POINT, and it is why this is a plain function returning a generator rather
    than a generator function. Verification runs when this is CALLED. A generator function would
    defer the whole body — including the check — until the first `next()`, so a caller writing
    rows as it iterated would have already written a partial batch by the time the failure
    surfaced. Half a batch written and an exception raised is worse than either outcome alone:
    the rows are indistinguishable from a good run, and the retry writes them again.

    tests/ingest/test_usgs_client.py::test_no_rows_are_returned_on_a_partial_response holds this
    down by collecting into a list and asserting the list is empty after the raise.
    """
    verify_pairs(payload, requested)
    return _yield_readings(payload, requested)


def _yield_readings(payload: dict, requested: set[tuple[str, str]]):
    """Yield readings for the REQUESTED pairs only.

    Filtering happens after verification, never instead of it. The two are easy to confuse and do
    opposite jobs: verification refuses a response that is missing something asked for; this
    discards a series that was not asked for. Returning only what was requested is what lets a
    caller treat this function's output as the answer to its own question - a writer that
    received extra series would otherwise persist rows nobody decided to collect.

    In practice the service volunteers nothing, so this filters nothing. It is here so that the
    day it does, the extra data is dropped rather than written.
    """
    for series in _time_series(payload):
        if _series_pair(series) not in requested:
            logger.info(
                "discarding unrequested series %r: it was not in the requested set",
                series.get("name"),
            )
            continue
        yield from _readings_in(series)


def parse(payload: dict, requested: set[tuple[str, str]]) -> list[Reading]:
    """readings_from(), realized into a list. The form most callers want."""
    return list(readings_from(payload, requested))


# ---------------------------------------------------------------------------------------------
# The client.
# ---------------------------------------------------------------------------------------------


class UsgsClient:
    """One window of instantaneous values, verified.

    `fetch` is injected so the whole parse-and-verify path is exercised offline by fixtures. The
    default reaches the real service; nothing in the test suite uses it.
    """

    def __init__(
        self,
        fetch=None,
        *,
        endpoint: str = IV_ENDPOINT,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._fetch = _urllib_fetch if fetch is None else fetch
        self._endpoint = endpoint
        self._timeout = timeout

    def fetch_window(
        self,
        site_ids,
        param_codes,
        start: datetime,
        end: datetime,
    ) -> list[Reading]:
        """Every reading for these sites and parameters in [start, end], verified.

        The requested set is the CROSS PRODUCT of the sites and parameters given. Callers that
        need per-site parameter sets - which is every real caller, because availability is per
        site - pass one site at a time, which is also the unit the backfill works in.
        """
        url = build_url(site_ids, param_codes, start, end, endpoint=self._endpoint)
        body = self._fetch(url, self._timeout)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MalformedResponseError(
                f"USGS response was not JSON ({exc}). url: {url}\n"
                f"  first 500 bytes: {body[:500]!r}"
            ) from exc

        requested = {(site, param) for site in site_ids for param in param_codes}
        return parse(payload, requested)
