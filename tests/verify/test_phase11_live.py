"""Unit tier — the live-endpoint verifiers (d-post, i, j), against fabricated responses.

NOTHING HERE TOUCHES THE NETWORK. Every check takes an already-collected response, and the two
stages that must issue requests take their fetcher as a parameter, so a test hands them a function
and the real `urllib` path is never entered.

The one thing that cannot be faked is the reason `Accept-Encoding: identity` matters, so
`test_d_post_fails_when_body_is_gzipped_and_header_not_suppressed` builds a REAL gzip body with the
standard library and drives it through the real check.
"""

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from verify.phase11 import fetch, stage_d, stage_i, stage_j  # noqa: E402
from verify.phase11.result import FAIL, PASS, Precondition  # noqa: E402

# The literal monitoring.tf configures. Used here as DATA handed to the check, never imported from
# the verifier - the whole property under test is that the verifier does not know it.
REAL_SEARCH_STRING = '"degraded":false'

HEALTHY_BODY = json.dumps(
    {"degraded": False, "jobs": [{"name": "backup_nightly", "overdue": False}], "data": []},
    separators=(",", ":"),
).encode("utf-8")


def response(status=200, body=HEALTHY_BODY, headers=None):
    return fetch.Response(status=status, body=body, headers=headers or {})


# ---------------------------------------------------------------------------------------------
# d-post: D6 and D7
# ---------------------------------------------------------------------------------------------


def test_d_post_fails_when_search_string_absent_from_body():
    degraded = json.dumps({"degraded": True}, separators=(",", ":")).encode("utf-8")
    result = stage_d.check_search_string_is_in_the_body(REAL_SEARCH_STRING, response(body=degraded))

    assert result.status == FAIL
    assert "absent" in result.observed
    # CLAUDE.md § 13: the observed value, so the operator does not go and fetch it by hand.
    assert "degraded" in result.observed


def test_d_post_fails_when_body_is_gzipped_and_header_not_suppressed():
    """D7, and the reason it is a separate step from D6.

    Route53 matches the literal against the first 5,120 bytes AS SENT. The application's body is
    correct here - it is the same bytes, compressed - so every app-side test stays green while the
    monitor never matches again. The assertion has to be about the bytes on the wire.
    """
    compressed = gzip.compress(HEALTHY_BODY)
    assert REAL_SEARCH_STRING.encode() not in compressed, "the fixture must really be compressed"

    result = stage_d.check_search_string_is_in_the_body(
        REAL_SEARCH_STRING, response(body=compressed, headers={"Content-Encoding": "gzip"})
    )
    assert result.status == FAIL
    assert "Content-Encoding='gzip'" in result.observed

    # The uncompressed form of the same body passes, so the failure above is about the encoding
    # rather than about the content.
    assert (
        stage_d.check_search_string_is_in_the_body(REAL_SEARCH_STRING, response()).status == PASS
    )


def test_fetch_sends_accept_encoding_identity(monkeypatch):
    """The REQUEST carries the header. Nothing else in this file can see that.

    The gzip test above drives a compressed body through the check, which proves the ASSERTION is
    about bytes - but it passes identically whether or not the request ever asked for identity.
    urllib sends no `Accept-Encoding` by default, so a fetch with the header deleted happens to
    work, and that is worse than failing: it works for a reason nobody chose, and it stops working
    the day somebody swaps in a client library with sensible defaults. This is the only test that
    goes red when the header is removed.
    """
    captured = {}

    class FakeResponse:
        status = 200
        headers = {}

        def read(self):
            return HEALTHY_BODY

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    fetch.get("https://example.invalid/api/health")

    lowered = {name.lower(): value for name, value in captured["headers"].items()}
    assert lowered.get("Accept-encoding".lower()) == "identity", captured["headers"]


def test_d_post_fails_when_the_edge_compresses_despite_the_identity_request():
    """The string is present AND the encoding is wrong - still a failure.

    A body that happens to contain the bytes while being served compressed means what Route53
    receives is not what was just measured. Checking only for presence would pass.
    """
    result = stage_d.check_search_string_is_in_the_body(
        REAL_SEARCH_STRING, response(headers={"Content-Encoding": "br"})
    )
    assert result.status == FAIL
    assert "compressed anyway" in result.observed


def test_d_post_reads_search_string_from_state_not_a_constant():
    """The verifier must follow whatever Route53 is CONFIGURED to search for.

    Behavioural rather than a grep for the literal, because the grep passes over a verifier that
    hardcodes the string with different JSON separators. Here a DIFFERENT search string is handed
    in, and the verdict has to follow it: a body containing the real token but not the configured
    one must FAIL, and a body containing the configured one must PASS.
    """
    configured = '"degraded":true'
    other_body = json.dumps({"degraded": True}, separators=(",", ":")).encode("utf-8")

    # The real token is present; the CONFIGURED token is not. A hardcoded verifier passes this.
    assert stage_d.check_search_string_is_in_the_body(configured, response()).status == FAIL
    # The configured token is present. A hardcoded verifier fails this.
    assert (
        stage_d.check_search_string_is_in_the_body(configured, response(body=other_body)).status
        == PASS
    )


def test_d_post_fails_on_a_non_200():
    result = stage_d.check_search_string_is_in_the_body(REAL_SEARCH_STRING, response(status=502))
    assert result.status == FAIL
    assert "502" in result.observed


def test_the_search_string_is_read_out_of_the_route53_configuration(monkeypatch):
    """`search_string_from_route53` reads `HealthCheckConfig.SearchString` and nothing else."""

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {"HealthCheck": {"HealthCheckConfig": {"Type": "HTTPS_STR_MATCH",
                                                   "SearchString": '"degraded":false'}}}
        )
        stderr = ""

    monkeypatch.setattr("verify.phase11.shell.run", lambda *a, **k: Completed())
    assert stage_d.search_string_from_route53("abc123") == '"degraded":false'


def test_a_health_check_with_no_search_string_is_a_precondition(monkeypatch):
    """An HTTPS check without a SearchString watches only for a status code.

    `/api/health` returns 200 while degraded BY CONTRACT (§ 20), so a status-code-only check on
    this endpoint is green during exactly the outage it exists to report.
    """

    class Completed:
        returncode = 0
        stdout = json.dumps({"HealthCheck": {"HealthCheckConfig": {"Type": "HTTPS"}}})
        stderr = ""

    monkeypatch.setattr("verify.phase11.shell.run", lambda *a, **k: Completed())
    with pytest.raises(Precondition) as excinfo:
        stage_d.search_string_from_route53("abc123")
    assert "no SearchString" in str(excinfo.value)
    assert "'HTTPS'" in str(excinfo.value)


# ---------------------------------------------------------------------------------------------
# d-post: D8
# ---------------------------------------------------------------------------------------------


def test_d_post_fails_on_pending_confirmation_subscription():
    """The literal string IS the failure, not a state to wait out.

    Nothing is delivered until somebody clicks the link. Every AWS call succeeds meanwhile - the
    topic exists, the alarm is attached to it - so the first anybody knows is the outage nobody
    was emailed about.
    """
    result = stage_d.check_subscription_is_confirmed(
        [{"SubscriptionArn": "PendingConfirmation", "Endpoint": "someone@example.com"}]
    )
    assert result.status == FAIL
    assert "PendingConfirmation" in result.observed

    confirmed = stage_d.check_subscription_is_confirmed(
        [{"SubscriptionArn": "arn:aws:sns:us-east-1:0:alerts:uuid", "Endpoint": "s@example.com"}]
    )
    assert confirmed.status == PASS


def test_d_post_fails_when_one_of_several_subscriptions_is_pending():
    """A confirmed one beside a pending one is still a subscription that delivers nothing."""
    result = stage_d.check_subscription_is_confirmed(
        [
            {"SubscriptionArn": "arn:aws:sns:us-east-1:0:alerts:uuid", "Endpoint": "a@x"},
            {"SubscriptionArn": "PendingConfirmation", "Endpoint": "b@x"},
        ]
    )
    assert result.status == FAIL
    assert "b@x" in result.observed


def test_d_post_fails_when_there_are_no_subscriptions_at_all():
    result = stage_d.check_subscription_is_confirmed([])
    assert result.status == FAIL
    assert "0 subscriptions" in result.observed


def test_d_post_checks_the_budget_against_the_configured_limit():
    """The limit is READ from variables.tf, never restated in the verifier."""
    assert stage_d._budget_limit_from_variables() == "25"
    assert stage_d._budget_name_from_variables() == "domestic-waterway-signals-monthly"

    good = stage_d.check_budget_exists(
        {"BudgetName": "domestic-waterway-signals-monthly", "BudgetType": "COST",
         "BudgetLimit": {"Amount": "25.0", "Unit": "USD"}},
        "25",
    )
    assert good.status == PASS

    wrong = stage_d.check_budget_exists(
        {"BudgetName": "x", "BudgetType": "COST", "BudgetLimit": {"Amount": "2500"}}, "25"
    )
    assert wrong.status == FAIL
    assert "2500" in wrong.observed

    assert stage_d.check_budget_exists({}, "25").status == FAIL


# ---------------------------------------------------------------------------------------------
# Stage J
# ---------------------------------------------------------------------------------------------


def _imds_getter(instance_id="i-0aa81133da97fd2bf"):
    def getter(url, **kwargs):
        if url.endswith("/api/token"):
            return fetch.Response(status=200, body=b"token-abc")
        if url.endswith("/meta-data/instance-id"):
            return fetch.Response(status=200, body=instance_id.encode())
        raise AssertionError(f"unexpected url {url}")

    return getter


def _unroutable_getter(url, **kwargs):
    raise Precondition(f"{url} could not be reached: timed out")


def test_j_exits_2_when_imds_responds():
    """From the instance the burst measures nothing, and it does not look like it.

    The source address is the Docker network's, so the limiter buckets the whole run against an
    address no client shares. The requests still return plausible status codes - including 429s -
    so a run from the wrong host reports a working per-IP limiter having measured nothing about
    per-IP anything.
    """
    with pytest.raises(Precondition) as excinfo:
        stage_j.refuse_to_run_on_the_instance(_imds_getter())

    message = str(excinfo.value)
    assert "must run from a LAPTOP" in message
    assert "i-0aa81133da97fd2bf" in message

    # And from a laptop the guard is silent.
    assert stage_j.refuse_to_run_on_the_instance(_unroutable_getter) is None


def test_j_imds_guard_survives_imdsv1_being_disabled():
    """IMDSv2: the token PUT comes first.

    A bare GET against a v2-only instance returns 401, and a guard that read that as "not an
    instance" would fail open on the one host where it has to work.
    """
    calls = []

    def getter(url, **kwargs):
        calls.append((url, kwargs.get("method", "GET")))
        if url.endswith("/api/token"):
            return fetch.Response(status=200, body=b"tok")
        return fetch.Response(status=200, body=b"i-123")

    assert fetch.imds_instance_id(getter) == "i-123"
    assert calls[0][1] == "PUT", calls
    assert calls[0][0].endswith("/api/token")


def test_j_uses_distinct_pairs_in_the_burst():
    """A burst of IDENTICAL requests measures the conclusion cache, not the limiter.

    § 22's amendment: the exposure is that each distinct `(site_id, as_of)` misses the cache and
    runs an analog query. Identical requests are served from `CONCLUSION_CACHE` after the first,
    return 200s, and the run concludes the limiter is not working.
    """
    pairs = stage_j.burst_pairs()

    assert len(pairs) == stage_j.BURST_REQUESTS
    assert len(set(pairs)) == len(pairs), "the pairs must be distinct"
    # And distinct in the DATE, not by inventing site ids - the site list is human-owned (§ 1).
    assert len({site for site, _ in pairs}) == 1
    assert len({as_of for _, as_of in pairs}) == len(pairs)
    assert all(date.fromisoformat(as_of) for _, as_of in pairs)


def test_j_burst_exceeds_the_conclusion_capacity():
    """A burst at or below capacity cannot produce a 429, so it would assert nothing.

    app/api/middleware/ratelimit.py sets CONCLUSION_CAPACITY = 20; this reads that number rather
    than restating it, so the two cannot drift into a burst that is quietly too small.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from app.api.middleware import ratelimit

    assert stage_j.BURST_REQUESTS > ratelimit.CONCLUSION_CAPACITY, (
        f"burst of {stage_j.BURST_REQUESTS} against a capacity of "
        f"{ratelimit.CONCLUSION_CAPACITY} cannot refuse anything"
    )


def test_j_fails_when_no_429_observed():
    result = stage_j.check_some_requests_were_refused([response(status=200) for _ in range(30)])

    assert result.status == FAIL
    assert "0 of 30" in result.observed
    assert "one uvicorn worker" in result.observed


def test_j_fails_when_retry_after_missing_or_non_integer():
    missing = stage_j.check_retry_after_is_an_integer(
        [fetch.Response(status=429, body=b"{}", headers={})]
    )
    assert missing.status == FAIL
    assert "no Retry-After" in missing.observed

    dated = stage_j.check_retry_after_is_an_integer(
        [fetch.Response(status=429, body=b"{}", headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})]
    )
    assert dated.status == FAIL
    assert "non-integer" in dated.observed

    good = stage_j.check_retry_after_is_an_integer(
        [fetch.Response(status=429, body=b"{}", headers={"Retry-After": "5"})]
    )
    assert good.status == PASS


def test_j_retry_after_check_fails_when_there_was_no_429_to_inspect():
    """A check that had nothing to look at must not report green (CLAUDE.md § 13, § 22)."""
    result = stage_j.check_retry_after_is_an_integer([response(status=200)])
    assert result.status == FAIL
    assert "asserted nothing" in result.observed


def test_j_fails_when_429_body_has_null_estimate_keys():
    """A contract assertion the first two checks cannot reach.

    A limiter can be perfectly correct and still serialize `"median_pct": null`, which is one
    `median_pct ?? 0` on the client from rendering a refusal as "nothing changed" (§ 20, § 21).
    """
    leaky = json.dumps(
        {"error": {"code": "rate_limited", "message": "slow down", "correlation_id": "abc"},
         "median_pct": None}
    ).encode()
    result = stage_j.check_refusal_body_has_no_estimate_keys(
        [fetch.Response(status=429, body=leaky, headers={"Retry-After": "5"})]
    )
    assert result.status == FAIL
    assert "median_pct" in result.observed


def test_j_fails_when_429_body_has_a_buried_numeric_estimate():
    """The recursive walk, which the named-key check does not subsume.

    § 20 measures that the two catch different failures: naming the keys catches a key added with a
    null, and walking numeric leaves catches one added with a NUMBER three levels down.
    """
    buried = json.dumps(
        {"error": {"code": "rate_limited", "message": "slow down", "correlation_id": "abc",
                   "debug": {"engine": {"median": 29.4}}}}
    ).encode()
    result = stage_j.check_refusal_body_has_no_estimate_keys(
        [fetch.Response(status=429, body=buried, headers={"Retry-After": "5"})]
    )
    assert result.status == FAIL
    assert "29.4" in result.observed
    assert "error.debug.engine.median" in result.observed


def test_j_passes_on_the_real_error_body_shape():
    """`errors.error_response` builds from a closed set of fields and carries no number at all."""
    body = json.dumps(
        {"error": {"code": "rate_limited",
                   "message": "Too many requests. Retry in 5 second(s).",
                   "correlation_id": "0f8c"}}
    ).encode()
    result = stage_j.check_refusal_body_has_no_estimate_keys(
        [fetch.Response(status=429, body=body, headers={"Retry-After": "5"})]
    )
    assert result.status == PASS


def test_j_fails_when_health_returns_429():
    result = stage_j.check_path_is_never_refused(
        [response(status=200), fetch.Response(status=429, body=b"{}")],
        path="/api/health",
        why="It is in EXEMPT_PATHS.",
    )
    assert result.status == FAIL
    assert "1 of 2" in result.observed


def test_j_asserts_the_site_root_is_never_refused():
    """§ 22 records the bundle as an ACCEPTED residual exposure. Seeing it confirmed is the point."""
    assert (
        stage_j.check_path_is_never_refused(
            [response(status=200)] * 30, path="/", why="accepted exposure"
        ).status
        == PASS
    )


def test_numeric_leaf_walk_ignores_booleans():
    """`True` is an `int` in Python, and `"degraded": false` is not an estimate leaking out."""
    assert stage_j.numeric_leaves({"degraded": False, "ok": True}) == []
    assert stage_j.numeric_leaves({"n": 0}) == [("n", 0)]


# ---------------------------------------------------------------------------------------------
# Stage I
# ---------------------------------------------------------------------------------------------


def _status_payload(*statuses):
    return {
        "HealthCheckObservations": [
            {"Region": f"r{index}", "StatusReport": {"Status": status}}
            for index, status in enumerate(statuses)
        ]
    }


def test_i_exits_2_on_timeout_not_1():
    """"I did not see it in the window" is not "I saw the wrong thing".

    Only one of those is evidence about the monitor. Reporting a timeout as exit 1 sends somebody
    to investigate a health check that may be about to flip.
    """
    slept = []
    ticks = iter([0, 30, 60, 90, 120, 150])

    with pytest.raises(Precondition) as excinfo:
        stage_i.poll(
            "abc123",
            stage_i.FAILURE,
            timeout_seconds=90,
            poll_seconds=30,
            reader=lambda _: _status_payload("Success: HTTP Status Code 200, OK"),
            sleeper=slept.append,
            clock=lambda: next(ticks),
        )

    message = str(excinfo.value)
    assert "was not reported within 90s" in message
    assert "exit 2" in message
    # Everything observed on the way is carried, so a human can see whether it was moving.
    assert "Success: HTTP Status Code 200, OK" in message
    assert slept, "it must actually have waited between polls"


def test_i_returns_when_every_region_agrees():
    ticks = iter([0, 30, 60])
    answers = iter(
        [
            _status_payload("Success: HTTP Status Code 200, OK", "Failure: Connection timed out"),
            _status_payload("Failure: Connection timed out", "Failure: Connection timed out"),
        ]
    )

    statuses, elapsed = stage_i.poll(
        "abc123",
        stage_i.FAILURE,
        timeout_seconds=600,
        poll_seconds=30,
        reader=lambda _: next(answers),
        sleeper=lambda _: None,
        clock=lambda: next(ticks),
    )
    assert all(status.startswith("Failure") for status in statuses)
    result = stage_i.check_verdict_matches(stage_i.FAILURE, statuses, elapsed)
    assert result.status == PASS
    assert "2 region(s) agreed" in result.observed


def test_i_does_not_call_a_verdict_while_regions_disagree():
    """A split is a real state during the ~90s Route53 takes, and it is neither answer.

    Taking the majority would let this declare a verdict early, and a human then writes down a
    time-to-detect shorter than the real one.
    """
    assert stage_i.verdict(["Failure: x", "Success: y"]) is None
    assert stage_i.verdict([]) is None
    assert stage_i.verdict(["Failure: x", "Failure: y"]) == stage_i.FAILURE
    assert stage_i.verdict(["Success: x"]) == stage_i.SUCCESS


def test_i_rejects_an_expect_value_it_cannot_wait_for():
    with pytest.raises(Precondition) as excinfo:
        stage_i.checks(expect="Degraded")
    assert "--expect must be" in str(excinfo.value)


def test_i_reports_the_per_region_statuses_on_a_wrong_verdict():
    result = stage_i.check_verdict_matches(
        stage_i.FAILURE, ["Success: HTTP Status Code 200, OK"], 120.0
    )
    assert result.status == FAIL
    assert "Success: HTTP Status Code 200, OK" in result.observed
    assert "120s" in result.observed
