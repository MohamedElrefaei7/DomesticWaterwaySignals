"""The USGS daily-values client. The historical backbone's reader.

CLAUDE.md § 15. This is a sibling of usgs_client.py, not a subclass of it, and the separation is
the point rather than an accident of ordering.

WHY THIS IS NOT usgs_client.py WITH A FLAG
------------------------------------------
The two endpoints return the same JSON envelope carrying different KINDS of measurement, and the
differences are exactly where a shared code path goes wrong:

    instantaneous          daily
    -----------            -----
    2026-08-01T00:00:00.000-05:00   2022-10-01T00:00:00.000     <- offset vs NO offset
    an instant                      a calendar day
    no statistic                    stat_cd, from variable.options.option
    rolling retention at 3/4 sites  the full record

MEASURED 2026-08-14: the daily endpoint returns naive timestamps. Feeding one through the
instantaneous client's `_parse_timestamp` - which calls `.astimezone(utc)` - makes Python apply
the LOCAL MACHINE's zone to it, so a daily mean for 2022-10-01 becomes 2022-10-01T05:00Z on a
container running UTC and 2022-10-01T07:00Z on a laptop in Denver, and it silently becomes the
wrong DAY for anything east of Greenwich. Nothing downstream can detect it.

tests/ingest/test_usgs_daily_client.py::test_the_iv_utc_conversion_is_not_applied_to_daily_values
holds the two parsers apart by breaking the instantaneous one and asserting this module still
works.

THREE RESPONSE OUTCOMES, THREE PATHS, NEVER COLLAPSED
-----------------------------------------------------
Phase 3 established two of these for the instantaneous endpoint. The daily endpoint adds a third,
and it is the ORDINARY response for a window before a site's record begins:

  | response                          | meaning                             | handling          |
  |-----------------------------------|-------------------------------------|-------------------|
  | body does not parse as JSON       | window entirely outside the record  | OutsidePeriodOfRecordError |
  | 200, `timeSeries: []`             | requested series unavailable        | MissingSeriesError |
  | 200, series present, `values: []` | ordinary gap                        | not an error      |

The first is a DIFFERENT EXCEPTION TYPE from the second, deliberately, because they point the
operator at different things: an unparseable body means the seeded `dv_record_start` floor is
earlier than the site's real record, and the fix is the seed. A missing series means the site
stopped serving a parameter it is recorded as serving, and the fix is `available_params`.
Collapsing them produces one error message that is wrong half the time.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass
from datetime import date

from app.ingest.usgs_client import (
    DEFAULT_TIMEOUT_SECONDS,
    MalformedResponseError,
    MissingSeriesError,
    UsgsError,
    _urllib_fetch,
)

logger = logging.getLogger(__name__)

# Pinned, for the same reason the instantaneous endpoint is (CLAUDE.md § 6). A different path on
# the same host, and a different service with its own retention policy - which is the whole
# reason this module exists.
DV_ENDPOINT = "https://waterservices.usgs.gov/nwis/dv/"
DV_FORMAT = "json,1.1"

# USGS daily statistic codes. Mean is what this commit ingests; the others are named because
# `stat_cd` is part of the daily table's key precisely so they can land later without a rebuild.
STAT_MIN = "00001"
STAT_MAX = "00002"
STAT_MEAN = "00003"


class OutsidePeriodOfRecordError(UsgsError):
    """The response body did not parse as JSON at all.

    MEASURED: this is what the daily endpoint returns for a window entirely outside a site's
    period of record - a plain-text error page, not a JSON envelope with an empty array.

    A DISTINCT TYPE FROM MissingSeriesError, and never caught and re-raised as one. This one says
    "the seeded dv_record_start floor is earlier than this site's real record"; the other says
    "this site stopped serving a parameter it is recorded as serving". Different causes,
    different fixes, and a caller that wanted to treat them alike can catch UsgsError.
    """


@dataclass(frozen=True)
class DailyReading:
    """One published daily statistic.

    `date` is a CALENDAR DATE, not a timestamp, and carries no timezone. The API states a date;
    this stores that date. No arithmetic is applied at any point - see the module docstring for
    what applying some would cost.
    """

    usgs_site_id: str
    date: date
    param_code: str
    stat_cd: str
    value: float
    qualifiers: tuple[str, ...]


def build_url(
    site_ids,
    param_codes,
    stat_codes,
    start: date,
    end: date,
    *,
    endpoint: str = DV_ENDPOINT,
) -> str:
    """The request URL for one window.

    `start` and `end` are PLAIN CALENDAR DATES, serialized as `YYYY-MM-DD`. The instantaneous
    client sends explicit UTC instants because its window boundaries are instants; this one must
    not, because a daily value has no time of day and attaching one would ask the service a
    question about an instant it does not answer.

    Both bounds are inclusive at the service, which is what daily_backfill.py's window arithmetic
    is written against.
    """
    sites = sorted(set(site_ids))
    params = sorted(set(param_codes))
    stats = sorted(set(stat_codes))
    if not sites:
        raise ValueError("build_url called with no sites")
    if not params:
        raise ValueError("build_url called with no parameter codes")
    if not stats:
        raise ValueError("build_url called with no statistic codes")

    for label, value in (("start", start), ("end", end)):
        if not isinstance(value, date) or hasattr(value, "hour"):
            raise ValueError(
                f"{label}={value!r} is not a plain date. The daily endpoint takes calendar dates; "
                f"passing a datetime here is the first step of treating a daily mean as an "
                f"instant (CLAUDE.md § 15)."
            )

    query = urllib.parse.urlencode(
        {
            "format": DV_FORMAT,
            "sites": ",".join(sites),
            "parameterCd": ",".join(params),
            "statCd": ",".join(stats),
            "startDT": start.isoformat(),
            "endDT": end.isoformat(),
            "siteStatus": "all",
        }
    )
    return f"{endpoint}?{query}"


# ---------------------------------------------------------------------------------------------
# Parsing and verification.
# ---------------------------------------------------------------------------------------------


def _time_series(payload: dict) -> list:
    try:
        series = payload["value"]["timeSeries"]
    except (KeyError, TypeError) as exc:
        raise MalformedResponseError(
            f"daily response has no value.timeSeries key ({type(exc).__name__}: {exc}). "
            f"Top-level keys: "
            f"{sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
        ) from exc
    if not isinstance(series, list):
        raise MalformedResponseError(
            f"value.timeSeries is {type(series).__name__}, expected a list"
        )
    return series


def _statistic_code(series: dict) -> str:
    """The statistic this series reports, read from variable.options.option.

    PARSED, NEVER HARDCODED. Requesting statCd=00003 and assuming the response is the mean is the
    same class of trust that finding 1 of Phase 3 punished: the service is under no obligation to
    return what was asked for, and a daily MINIMUM stored under the mean's key is a number that
    is wrong in a direction nothing downstream can detect - it is plausible, it is the right
    order of magnitude, and it is systematically low.

    Raises when absent. A daily series with no statistic is one this project cannot key.
    """
    options = (series.get("variable", {}).get("options", {}) or {}).get("option") or []
    for option in options:
        if str(option.get("name", "")).strip().lower() == "statistic":
            code = option.get("optionCode")
            if code:
                return str(code)
            raise MalformedResponseError(
                f"series {series.get('name')!r} has a Statistic option with no optionCode: "
                f"{option!r}"
            )
    raise MalformedResponseError(
        f"series {series.get('name')!r} declares no Statistic option, so the statistic it "
        f"reports is unknown. Options present: {options!r}. This is not defaulted to the mean - "
        f"a daily minimum stored as a mean is wrong in a way nothing downstream can detect."
    )


def _series_triple(series: dict) -> tuple[str, str, str]:
    """The (site, parameter, statistic) this series is for."""
    try:
        site = series["sourceInfo"]["siteCode"][0]["value"]
        param = series["variable"]["variableCode"][0]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MalformedResponseError(
            f"a daily timeSeries entry has no identifiable siteCode/variableCode "
            f"({type(exc).__name__}: {exc}). Series name: {series.get('name', '<absent>')!r}"
        ) from exc
    return str(site), str(param), _statistic_code(series)


def present_triples(payload: dict) -> set[tuple[str, str, str]]:
    """Every (site, parameter, statistic) triple present in the response."""
    return {_series_triple(series) for series in _time_series(payload)}


def verify_triples(payload: dict, requested: set[tuple[str, str, str]]) -> None:
    """Assert the response carries every triple that was asked for. Raise naming the gap.

    Phase 3's assertion extended by one element. The statistic belongs in it for the same reason
    it belongs in the table's key: requesting the mean and receiving the minimum is a satisfied
    request by any check that only compares sites and parameters.
    """
    present = present_triples(payload)
    missing = requested - present
    if not missing:
        return

    satisfied = sorted(requested & present)
    raise MissingSeriesError(
        "USGS daily values returned HTTP 200 without every requested series. A missing series is "
        "NOT zero rows - the response carries no marker distinguishing the two (CLAUDE.md § 15).\n"
        f"  requested: {sorted(requested)}\n"
        f"  present:   {satisfied if satisfied else '(none)'}\n"
        f"  MISSING:   {sorted(missing)}\n"
        "Each triple is (site, parameter, statistic). If a site has genuinely stopped serving a "
        "parameter, correct that site's available_params in a new migration - do not relax this "
        "check. If the STATISTIC is what differs, the service answered a different question from "
        "the one asked."
    )


def parse_date(raw: str) -> date:
    """'2022-10-01T00:00:00.000' -> date(2022, 10, 1). No timezone arithmetic whatsoever.

    THE WHOLE FUNCTION IS THE DECISION. It splits on 'T' and reads the date part, and it never
    constructs a datetime - because the moment a datetime exists, something will call
    `.astimezone()` on it and the calendar date the source stated will shift by a day for half
    the world.

    An offset-bearing timestamp is REFUSED rather than silently truncated. If the daily endpoint
    ever starts returning offsets, that is a change this project must notice deliberately: it
    would mean USGS had started making a claim about the instant a daily mean belongs to, and
    quietly discarding that claim is how the two endpoints' parsing paths merge by accident
    (CLAUDE.md § 15).
    """
    if not isinstance(raw, str):
        raise MalformedResponseError(f"daily dateTime is {type(raw).__name__}, expected a string")

    day, _, time_part = raw.partition("T")

    if any(marker in time_part for marker in ("+", "Z", "z")) or (
        "-" in time_part
    ):
        raise MalformedResponseError(
            f"daily dateTime {raw!r} carries a UTC offset. Daily values are calendar dates and "
            f"are stored as stated; an offset means the service changed what it is publishing, "
            f"and truncating it silently would merge the daily and instantaneous parsing paths "
            f"(CLAUDE.md § 15)."
        )

    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise MalformedResponseError(
            f"could not parse daily date {raw!r}: {exc}"
        ) from exc


def _no_data_value(series: dict) -> float | None:
    raw = series.get("variable", {}).get("noDataValue")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "daily series %r declares a non-numeric noDataValue %r; no sentinel filtering applied",
            series.get("name"),
            raw,
        )
        return None


def _readings_in(series: dict) -> list[DailyReading]:
    site, param, stat = _series_triple(series)
    sentinel = _no_data_value(series)

    readings: list[DailyReading] = []
    for block in series.get("values", []) or []:
        for entry in block.get("value", []) or []:
            raw_value = entry.get("value")
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise MalformedResponseError(
                    f"non-numeric daily value {raw_value!r} for {site}/{param}/{stat} at "
                    f"{entry.get('dateTime')!r}: {type(exc).__name__}: {exc}"
                ) from exc

            if sentinel is not None and value == sentinel:
                continue

            readings.append(
                DailyReading(
                    usgs_site_id=site,
                    date=parse_date(entry.get("dateTime")),
                    param_code=param,
                    stat_cd=stat,
                    value=value,
                    qualifiers=tuple(entry.get("qualifiers") or ()),
                )
            )
    return readings


def readings_from(payload: dict, requested: set[tuple[str, str, str]]):
    """Verify, THEN yield. Same ordering guarantee as the instantaneous client.

    Verification runs when this is CALLED, before iteration begins, so a caller writing as it
    iterates cannot commit a partial batch and then fail.
    """
    verify_triples(payload, requested)
    return _yield_readings(payload, requested)


def _yield_readings(payload: dict, requested: set[tuple[str, str, str]]):
    for series in _time_series(payload):
        if _series_triple(series) not in requested:
            logger.info(
                "discarding unrequested daily series %r", series.get("name")
            )
            continue
        yield from _readings_in(series)


def parse(payload: dict, requested: set[tuple[str, str, str]]) -> list[DailyReading]:
    return list(readings_from(payload, requested))


def parse_body(
    body: str,
    requested: set[tuple[str, str, str]],
    *,
    site_ids=None,
    start: date | None = None,
    end: date | None = None,
) -> list[DailyReading]:
    """Decode a response body and parse it, distinguishing all three outcomes.

    THE JSON DECODE FAILURE IS HANDLED HERE AND NOWHERE ELSE, so there is exactly one place where
    "the body was not JSON" can be confused with anything else. The window and sites are
    parameters purely so the error can name them: an error that says "invalid JSON" without
    saying which site and which decade sends the operator back to reconstruct the request by hand
    (CLAUDE.md § 13).
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        window = (
            f"{start.isoformat()} to {end.isoformat()}"
            if start and end
            else "(window not supplied)"
        )
        sites = sorted(set(site_ids)) if site_ids else sorted({s for s, _p, _st in requested})
        raise OutsidePeriodOfRecordError(
            f"the USGS daily service returned a body that is not JSON for site(s) {sites} over "
            f"{window}.\n"
            f"  MEASURED 2026-08-14: this is what the service returns for a window ENTIRELY "
            f"OUTSIDE a site's period of record - a plain-text error page, not an empty JSON "
            f"envelope.\n"
            f"  This is NOT the same as a missing series. The likely cause is that the seeded "
            f"dv_record_start floor for one of these sites is earlier than its real record; the "
            f"fix is the seed, in a new numbered migration (CLAUDE.md § 1).\n"
            f"  decoder said: {exc}\n"
            f"  first 300 bytes: {body[:300]!r}"
        ) from exc

    return parse(payload, requested)


# ---------------------------------------------------------------------------------------------
# The client.
# ---------------------------------------------------------------------------------------------


class UsgsDailyClient:
    """One window of daily values, verified.

    `fetch` is injected so the whole parse-and-verify path runs offline against fixtures. The
    default reaches the real service; nothing in the test suite uses it.
    """

    def __init__(
        self,
        fetch=None,
        *,
        endpoint: str = DV_ENDPOINT,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._fetch = _urllib_fetch if fetch is None else fetch
        self._endpoint = endpoint
        self._timeout = timeout

    def fetch_window(
        self,
        site_ids,
        param_codes,
        start: date,
        end: date,
        stat_codes=(STAT_MEAN,),
    ) -> list[DailyReading]:
        """Every daily value for these sites, parameters and statistics in [start, end].

        The requested set is the cross product of sites, parameters and statistics. Callers pass
        one site at a time - availability and period of record are both per site, so a shared
        window would be wrong for at least one of them.
        """
        url = build_url(
            site_ids, param_codes, stat_codes, start, end, endpoint=self._endpoint
        )
        body = self._fetch(url, self._timeout)

        requested = {
            (site, param, stat)
            for site in site_ids
            for param in param_codes
            for stat in stat_codes
        }
        return parse_body(
            body, requested, site_ids=site_ids, start=start, end=end
        )
