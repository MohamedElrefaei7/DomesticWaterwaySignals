"""Stage J — the rate limiter, from a laptop, against distinct `(site_id, as_of)` pairs.

    python3 -m verify.phase11 j

STAGE J REFUSES TO RUN ON THE INSTANCE, AND THAT REFUSAL IS THE FIRST CHECK.

From the instance the request reaches Caddy over the Docker network, so `X-Real-IP` carries the
Docker gateway address rather than a client's. The limiter then buckets the run against an address
no external client shares. Nothing errors: the burst returns plausible status codes, some of them
even 429s, and the run reports a working per-IP limiter while having measured nothing about per-IP
anything. That is CLAUDE.md § 2's theme 2 - a check that verifies the exact thing responsible for a
failure and reports it correct.

Detected by asking IMDS. On a laptop `169.254.169.254` is unroutable and the request times out,
which is the pass condition; on the instance it answers with an instance id and this exits 2.

THE BURST USES DISTINCT PAIRS, AND THAT IS THE WHOLE EXPOSURE BEING MEASURED. CLAUDE.md § 22's
Phase 11 amendment: `/api/conclusion` is limited in the application rather than at the edge
precisely because each distinct `(site_id, as_of)` misses the conclusion cache and runs an analog
query, so the expensive request and the cheap one are the same shape and differ only in a query
parameter. A burst of IDENTICAL requests is served from `CONCLUSION_CACHE` after the first - it
measures the cache, returns 200s, and concludes the limiter does not work.

THREE THINGS ARE ASSERTED SEPARATELY because the third is a contract assertion the first two cannot
reach: that 429s appear at all, that `Retry-After` is present and parses as an integer, and that the
429 body's estimate keys are ABSENT rather than null. A limiter can be perfectly correct and still
serialize a body one frontend default away from rendering a refusal as "nothing changed" (§ 20).

AND `/api/health` MUST NEVER 429. It is `EXEMPT_PATHS` in the middleware, and the reason is that
throttling the endpoint Route53 watches turns a load spike into a page. The site root is asserted
too - not because it is protected, but because § 22 records its exposure as ACCEPTED, and seeing an
accepted exposure confirmed is worth more than reading that somebody accepted it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Sequence

from verify.phase11 import fetch
from verify.phase11.result import Check, CheckResult, Precondition, failed, passed

# app/api/middleware/ratelimit.py: CONCLUSION_CAPACITY = 20. The burst has to exceed it, and a
# comfortable margin costs nothing because every request past the limit is refused cheaply.
BURST_REQUESTS = 30

# Memphis. The site list is human-owned (migration 0004, CLAUDE.md § 1); this verifier does not
# invent one, it uses the site the analog engine was validated against.
DEFAULT_SITE_ID = "07032000"

# A date far enough back that the engine has history to refuse on, and fixed rather than "today" so
# two runs of this verifier ask the same questions.
BURST_ANCHOR = date(2022, 10, 1)

# Keys that must never appear in a 429 body. § 20: a refusal's estimate keys are ABSENT, not null,
# because a client cannot default a key that does not exist.
ESTIMATE_KEYS = frozenset({"median_pct", "range_pct", "matches", "n_analogs_used"})

# Numeric leaves a refusal may legitimately carry: a count, a stated threshold, or a sweep
# statistic (§ 20). A 429 from `errors.error_response` carries none at all, so anything numeric in
# one is a new field somebody must classify.
PERMITTED_NUMERIC_KEYS = frozenset({"status_code", "retry_after"})


def burst_pairs(count: int = BURST_REQUESTS, site_id: str = DEFAULT_SITE_ID) -> list[tuple[str, str]]:
    """`count` DISTINCT `(site_id, as_of)` pairs.

    Distinct dates rather than distinct sites: the site list is human-owned and inventing site ids
    would be inventing gauge sites (§ 1). Dates are ours to choose and each one is a cache miss,
    which is the property the burst needs.
    """
    return [
        (site_id, (BURST_ANCHOR - timedelta(days=offset)).isoformat())
        for offset in range(count)
    ]


def numeric_leaves(value: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every numeric leaf in a decoded body, with its path.

    A RECURSIVE WALK, not a check of named fields. § 20 measures that the two catch different
    failures: naming the keys catches one added with a `null` value, and walking every numeric leaf
    catches one added with a NUMBER three levels down that nobody thought to look at.
    """
    found: list[tuple[str, Any]] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        return [(path or "<root>", value)]
    if isinstance(value, dict):
        for key, item in value.items():
            found += numeric_leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += numeric_leaves(item, f"{path}[{index}]")
    return found


def key_paths(value: Any, path: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            paths.append(here)
            paths += key_paths(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths += key_paths(item, f"{path}[{index}]")
    return paths


# ---------------------------------------------------------------------------------------------
# Checks, over an already-collected burst
# ---------------------------------------------------------------------------------------------


def check_some_requests_were_refused(responses: Sequence[fetch.Response]) -> CheckResult:
    statuses = [response.status for response in responses]
    refused = [status for status in statuses if status == 429]

    name = "the conclusion limiter refuses a burst"
    expected = f">= 1 response with status 429 out of {len(responses)} distinct pairs"
    if not refused:
        return failed(
            name,
            expected,
            f"0 of {len(statuses)}; statuses observed: {sorted(set(statuses))}. If these are all "
            f"200 and the pairs were distinct, the limiter is not running - check that the API has "
            f"exactly one uvicorn worker (§ 22: in-process state is per worker).",
        )
    return passed(name, expected, f"{len(refused)} of {len(statuses)} refused with 429")


def check_retry_after_is_an_integer(responses: Sequence[fetch.Response]) -> CheckResult:
    refused = [response for response in responses if response.status == 429]

    name = "every 429 carries an integer Retry-After"
    expected = "Retry-After present on every 429 and parsing as an integer"
    if not refused:
        return failed(name, expected, "no 429 was observed, so this asserted nothing")

    missing = [r for r in refused if r.header("Retry-After") is None]
    if missing:
        return failed(name, expected, f"{len(missing)} of {len(refused)} 429s had no Retry-After")

    unparseable = [
        r.header("Retry-After") for r in refused if not _is_integer(r.header("Retry-After"))
    ]
    if unparseable:
        return failed(
            name,
            expected,
            f"{len(unparseable)} non-integer value(s): {unparseable}. RFC 9110 permits a date, but "
            f"a client parsing two formats is a client that gets one of them wrong.",
        )
    values = sorted({int(r.header("Retry-After")) for r in refused})
    return passed(name, expected, f"{len(refused)} 429s, Retry-After values {values}")


def _is_integer(value: str | None) -> bool:
    if value is None:
        return False
    try:
        int(value)
    except ValueError:
        return False
    return True


def check_refusal_body_has_no_estimate_keys(responses: Sequence[fetch.Response]) -> CheckResult:
    """The 429 body carries no estimate key, at any depth, with any value.

    Asserted twice over, for the reason § 20 measures: a named-key check catches `median_pct: null`
    and a numeric-leaf walk catches `median_pct: 0.0` three levels down. Neither subsumes the other.
    """
    refused = [response for response in responses if response.status == 429]
    name = "the 429 body has no estimate keys and no unexplained numbers"
    expected = "no key in ESTIMATE_KEYS at any depth; every numeric leaf on the allow-list"

    if not refused:
        return failed(name, expected, "no 429 was observed, so this asserted nothing")

    problems: list[str] = []
    for response in refused[:5]:
        try:
            body = json.loads(response.body)
        except (ValueError, UnicodeDecodeError) as exc:
            problems.append(f"body is not JSON: {exc}")
            continue

        leaked = [
            path for path in key_paths(body) if path.rsplit(".", 1)[-1] in ESTIMATE_KEYS
        ]
        if leaked:
            problems.append(f"estimate keys present: {leaked}")

        numbers = [
            (path, value)
            for path, value in numeric_leaves(body)
            if path.rsplit(".", 1)[-1] not in PERMITTED_NUMERIC_KEYS
        ]
        if numbers:
            problems.append(f"numeric leaves outside the allow-list: {numbers}")

    if problems:
        return failed(name, expected, "; ".join(problems))
    return passed(name, expected, f"{len(refused)} 429 bodies, no estimate key, no stray number")


def check_path_is_never_refused(
    responses: Sequence[fetch.Response], *, path: str, why: str
) -> CheckResult:
    statuses = [response.status for response in responses]
    refused = [status for status in statuses if status == 429]

    name = f"{path} is never rate limited"
    expected = f"0 responses with status 429 out of {len(responses)}"
    if refused:
        return failed(name, expected, f"{len(refused)} of {len(statuses)} were 429. {why}")
    return passed(name, expected, f"{len(statuses)} requests, statuses {sorted(set(statuses))}")


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------


def refuse_to_run_on_the_instance(getter=fetch.get) -> None:
    instance_id = fetch.imds_instance_id(getter)
    if instance_id is not None:
        raise Precondition(
            f"Stage J must run from a LAPTOP, not from the instance. IMDS answered with "
            f"{instance_id}. From here the source address is the Docker network's, so the "
            f"limiter buckets this run against an address no client shares - and the burst still "
            f"returns plausible status codes, so nothing would look wrong."
        )


def collect(base_url: str, getter=fetch.get) -> dict[str, list[fetch.Response]]:
    """Issue the burst and the two control sets. The only place this stage touches the network."""
    conclusion = [
        getter(f"{base_url}/api/conclusion?site_id={site_id}&as_of={as_of}")
        for site_id, as_of in burst_pairs()
    ]
    health = [getter(f"{base_url}/api/health") for _ in range(BURST_REQUESTS)]
    root = [getter(f"{base_url}/") for _ in range(BURST_REQUESTS)]
    return {"conclusion": conclusion, "health": health, "root": root}


def checks(base_url: str = "https://bargeanalysis.com", getter=fetch.get) -> Sequence[Check]:
    refuse_to_run_on_the_instance(getter)
    collected = collect(base_url, getter)

    return [
        lambda: check_some_requests_were_refused(collected["conclusion"]),
        lambda: check_retry_after_is_an_integer(collected["conclusion"]),
        lambda: check_refusal_body_has_no_estimate_keys(collected["conclusion"]),
        lambda: check_path_is_never_refused(
            collected["health"],
            path="/api/health",
            why="It is in EXEMPT_PATHS; throttling the endpoint Route53 watches turns a load "
            "spike into a page.",
        ),
        lambda: check_path_is_never_refused(
            collected["root"],
            path="/",
            why="CLAUDE.md § 22 records the bundle, CSS and fonts as unlimited at the edge - an "
            "ACCEPTED residual exposure. A 429 here means something changed and the recorded "
            "decision is now wrong.",
        ),
    ]
