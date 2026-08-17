"""Part 3 — the external health check, its alarm, and the token it matches.

THE TWO RENDERING TESTS ARE THE POINT OF THIS MODULE. `/api/health` returns 200 while degraded by
contract (CLAUDE.md § 20), so a status-code monitor on it is a check that cannot fail — theme 2 in
Terraform. What makes the string match trustworthy is not that the literal is spelled correctly,
but that it is spelled the same way the ACTUAL RESPONSE BYTES spell it. A literal-to-literal
comparison across two files catches a typo and misses a change of response class or of JSON
separators, either of which leaves this monitor permanently, silently green.

So the search string is asserted against a body rendered through the real FastAPI app.
"""

from datetime import date, timedelta

from conftest import unwrap

from app.orchestration import cadence as cadence_module
from tests.api.conftest import FakeConn, make_client, utc

NOW = utc(2026, 8, 16, 12, 0, 0)

ALL_JOBS_RECENT = {entry.job_name: NOW - timedelta(minutes=5) for entry in cadence_module.CADENCES}
FRESH_TABLES = {
    "gauge_readings_iv": NOW - timedelta(hours=1),
    "gauge_readings_daily": date(2026, 8, 15),
    "barge_rates": date(2026, 8, 15),
    "lock_movements": date(2026, 8, 15),
    "features": date(2026, 8, 15),
}


def hcl_string(value):
    """Unwrap a python-hcl2 string literal AND undo HCL's backslash escaping.

    `search_string = "\\"degraded\\":false"` parses to the Python string
    `'"\\\\"degraded\\\\":false"'`. Stripping the outer quotes is not enough: the escapes have to
    come off too, or the comparison below runs against a string containing backslashes that never
    appear in any response body.
    """
    return unwrap(value).replace('\\"', '"').replace("\\\\", "\\")


def _health_check(tf):
    checks = tf.resources_of_type("aws_route53_health_check")
    assert checks, "no aws_route53_health_check defined - nothing is watching from outside"
    assert len(checks) == 1, f"expected exactly one health check, found {sorted(checks)}"
    return next(iter(checks.items()))


def _alarm(tf):
    alarms = tf.resources_of_type("aws_cloudwatch_metric_alarm")
    assert alarms, "no aws_cloudwatch_metric_alarm defined - the health check pages nobody"
    assert len(alarms) == 1, f"expected exactly one alarm, found {sorted(alarms)}"
    return next(iter(alarms.items()))


def _body(conn):
    """The REAL rendered bytes of /api/health, through the real app and its real response class."""
    response = make_client(conn=conn, now=NOW).get("/api/health")
    assert response.status_code == 200, (
        f"/api/health returned {response.status_code}; the contract is 200 even when degraded"
    )
    return response.content


def test_health_check_is_str_match_not_status_only(tf):
    """`/api/health` returns 200 when degraded, so a status check on it CANNOT FAIL."""
    name, attrs = _health_check(tf)

    assert unwrap(attrs.get("type")) == "HTTPS_STR_MATCH", (
        f"aws_route53_health_check.{name}.type is {unwrap(attrs.get('type'))!r}. A plain HTTPS "
        f"check on this endpoint is green whenever anything answers, including a fully degraded "
        f"system - CLAUDE.md § 20 makes the 200 deliberate."
    )
    assert unwrap(attrs.get("resource_path")) == "/api/health", (
        f"{name} probes {unwrap(attrs.get('resource_path'))!r}"
    )
    assert attrs.get("enable_sni") is True, f"{name} does not enable SNI"
    assert attrs.get("measure_latency") is True, f"{name} does not measure latency"


def test_health_check_search_string_matches_rendered_body(tf):
    """The Terraform literal must appear in the bytes Route53 will actually receive.

    Rendered through the app rather than compared to another literal. Starlette's JSONResponse
    emits compact separators; a switch to a response class that emits `": "` turns
    `"degraded":false` into a string that never appears in any body, while both files still read
    exactly as intended.
    """
    name, attrs = _health_check(tf)
    search_string = hcl_string(attrs["search_string"])

    body = _body(FakeConn(last_success=ALL_JOBS_RECENT, newest=FRESH_TABLES))

    assert search_string.encode() in body, (
        f"aws_route53_health_check.{name}.search_string is {search_string!r}, which does not "
        f"appear in a healthy rendered body. The monitor would report failure permanently.\n"
        f"first 200 bytes: {body[:200]!r}"
    )

    # Route53 inspects only the first 5,120 bytes of the response.
    assert body.index(search_string.encode()) < 5120, (
        f"the token appears at byte {body.index(search_string.encode())}, past the 5,120-byte "
        f"window Route53 inspects"
    )


def test_rendered_degraded_body_does_not_contain_search_string(tf):
    """A degraded body must not contain the token anywhere, including in a nested field.

    The nested models use `overdue` and `stale`, so there is no field that CAN serialise as
    `degraded` inside a job or table entry — but assert it rather than reason about it. The
    assertion survives somebody renaming a nested field; the reasoning does not.

    Constructed with at least one HEALTHY job present, because the interesting failure is a
    monitor reading a degraded system as healthy off the back of one component that is fine.
    """
    name, attrs = _health_check(tf)
    search_string = hcl_string(attrs["search_string"])

    stale_job, *healthy_jobs = sorted(entry.job_name for entry in cadence_module.CADENCES)
    assert healthy_jobs, "the cadence table has only one job; this test needs a healthy one too"

    last_success = dict(ALL_JOBS_RECENT)
    last_success[stale_job] = NOW - timedelta(days=400)

    body = _body(FakeConn(last_success=last_success, newest=FRESH_TABLES))

    assert b'"degraded":true' in body, (
        f"the constructed state did not actually degrade - this test would pass vacuously.\n"
        f"first 200 bytes: {body[:200]!r}"
    )
    assert search_string.encode() not in body, (
        f"the search string {search_string!r} appears in a DEGRADED body, so the external monitor "
        f"reads a degraded system as healthy. This is the single most important assertion in "
        f"aws_route53_health_check.{name}."
    )


def test_alarm_has_insufficient_data_action(tf):
    """An alarm stuck in INSUFFICIENT_DATA looks exactly like a healthy one on a dashboard."""
    name, attrs = _alarm(tf)

    actions = attrs.get("insufficient_data_actions")
    assert actions, (
        f"aws_cloudwatch_metric_alarm.{name} has no insufficient_data_actions. If the health "
        f"check is deleted or stops reporting, the alarm sits grey forever and nothing is said - "
        f"which is how a monitor dies quietly."
    )


def test_alarm_evaluation_periods_greater_than_one(tf):
    """One bad thirty seconds at one edge location is not an incident.

    An alarm that pages for a single blip gets muted, and the next real outage is silent.
    """
    name, attrs = _alarm(tf)

    periods = attrs.get("evaluation_periods")
    assert isinstance(periods, int), f"{name}.evaluation_periods is {periods!r}"
    assert periods > 1, (
        f"aws_cloudwatch_metric_alarm.{name}.evaluation_periods is {periods}, so a single "
        f"edge-location blip pages"
    )

    assert unwrap(attrs.get("metric_name")) == "HealthCheckStatus", (
        f"{name} alarms on {unwrap(attrs.get('metric_name'))!r}"
    )
    assert unwrap(attrs.get("statistic")) == "Minimum", (
        f"{name}.statistic is {unwrap(attrs.get('statistic'))!r}. Minimum is what catches ONE "
        f"checker region failing; Average lets a majority of healthy regions hide it."
    )


def test_sns_email_variable_has_no_default(tf):
    """An alert destination nobody chose is an alert nobody reads."""
    variable = tf.variables.get("alert_email")
    assert variable is not None, "no alert_email variable is declared"
    assert "default" not in variable, (
        f"alert_email has a default ({variable.get('default')!r}). A placeholder default means "
        f"apply succeeds, the subscription is created against an address nobody owns, and the "
        f"alarm reports as configured while delivering nothing."
    )


def test_alarm_and_health_check_are_pinned_to_us_east_1(tf):
    """Route53 health-check metrics exist only in us-east-1, whatever region the stack moves to.

    Pinned to an explicit aliased provider rather than left to var.aws_region: the day somebody
    adds a second region and moves the default, an alarm on the regional provider watches a metric
    namespace with nothing in it, and reports OK forever.
    """
    _, alarm = _alarm(tf)
    _, check = _health_check(tf)

    for label, attrs in (("alarm", alarm), ("health check", check)):
        provider = attrs.get("provider")
        assert provider is not None, (
            f"the {label} does not name a provider, so it inherits var.aws_region and silently "
            f"follows the stack to a region where Route53 metrics do not exist"
        )
        assert "us_east_1" in str(provider), f"the {label} uses provider {provider!r}"
