"""The per-client-IP rate limiter (CLAUDE.md § 22's Phase 11 cost-based exception).

EVERY TEST IN THIS FILE FABRICATES `X-Real-IP`, so every one of them passes identically whether or
not Caddy is configured to set it. That is theme 2, acknowledged rather than hidden:
`tests/deploy/test_caddyfile_sets_real_ip.py` is the only test connecting the configuration to the
behaviour, and live verification bursts the PUBLIC URL rather than the container.

The limiter's arithmetic is exercised directly against `RateLimiter` with an injected clock, not
through eighty HTTP round trips. A test that had to make 120 real requests to prove a limit of 120
would be slow enough to get marked integration and then skipped.
"""

import logging

from app.api import errors
from app.api.middleware import ratelimit
from tests.api.conftest import FakeConn, make_client, utc

NOW = utc(2026, 8, 16, 12, 0, 0)


# The limiter is reset between tests by the autouse fixture in tests/api/conftest.py, alongside
# the caches - it is the same kind of module-level singleton and has the same failure mode.


def _limiter():
    return ratelimit.RateLimiter()


def _drain(limiter, path, key, now=0.0, count=None):
    """Spend `count` requests (default: exactly the path's capacity) and return the refusals."""
    if count is None:
        count = (
            ratelimit.CONCLUSION_CAPACITY
            if path == ratelimit.CONCLUSION_PATH
            else ratelimit.GENERAL_CAPACITY
        )
    return [limiter.check(path, key, now) for _ in range(count)]


# ---------------------------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------------------------


def test_ratelimit_uses_x_real_ip_not_client_host():
    """Two requests from the SAME client.host and DIFFERENT X-Real-IP get independent buckets.

    Behind Caddy every request has the same `request.client.host` - the proxy's container address.
    A limiter keyed on it buckets the entire internet into one client, so the hundredth visitor is
    refused because of the first ninety-nine, and the limiter reads as working the whole time.
    """
    proxy_host = "172.18.0.5"

    first, _ = ratelimit.client_key("203.0.113.7", proxy_host)
    second, _ = ratelimit.client_key("198.51.100.9", proxy_host)

    assert first != second, (
        f"two different real clients behind the same proxy share the bucket {first!r} - the "
        f"limiter is keyed on the proxy, not on the client"
    )

    # And behaviourally: draining one client must not refuse the other.
    limiter = _limiter()
    assert all(w is None for w in _drain(limiter, ratelimit.CONCLUSION_PATH, first))
    assert limiter.check(ratelimit.CONCLUSION_PATH, first, 0.0) is not None, (
        "the first client was not actually limited, so the assertion below proves nothing"
    )
    assert limiter.check(ratelimit.CONCLUSION_PATH, second, 0.0) is None, (
        "a second client was refused because the first exhausted its quota"
    )


def test_ratelimit_ignores_x_forwarded_for():
    """Varying X-Forwarded-For with a constant X-Real-IP shares ONE bucket.

    XFF's first element is written by the client and never verified. If the limiter read it, an
    attacker would rotate a header per request and the limiter would do nothing - while every
    other test in this file still passed.

    DRIVEN THROUGH THE MIDDLEWARE, not through `client_key`. `client_key` does not take an XFF
    argument at all, so a test written against it passes whatever `dispatch` reads - measured,
    when the "key on X-Forwarded-For[0]" mutation left the direct-call version of this test green.
    The header selection lives in `dispatch`, so the test has to go through `dispatch`.
    """
    client = make_client(conn=FakeConn(), now=NOW)
    real_ip = "203.0.113.7"

    statuses = []
    for index in range(ratelimit.CONCLUSION_CAPACITY + 1):
        response = client.get(
            "/api/conclusion?site_id=07032000&as_of=2024-01-15",
            headers={
                "X-Real-IP": real_ip,
                # A different forged value every time. If the limiter read this, each request
                # would land in its own fresh bucket and none of them would ever be refused.
                "X-Forwarded-For": f"198.51.100.{index % 200 + 1}",
            },
        )
        statuses.append(response.status_code)

    assert statuses[-1] == 429, (
        f"{len(statuses)} requests with ONE X-Real-IP and a rotating X-Forwarded-For were never "
        f"limited (statuses {sorted(set(statuses))}). The limiter is keyed on a header the client "
        f"writes, so rotating it defeats the limiter entirely."
    )


def test_ratelimit_falls_back_to_client_host_when_header_absent(caplog):
    """No proxy: limiting still happens, and the fallback is announced."""
    key, used_fallback = ratelimit.client_key(None, "203.0.113.7")

    assert used_fallback is True
    assert key == "v4:203.0.113.7"

    limiter = _limiter()
    with caplog.at_level(logging.WARNING, logger=ratelimit.logger.name):
        limiter.note_fallback()

    assert any("X-Real-IP" in record.message for record in caplog.records), (
        f"no warning naming X-Real-IP was emitted: {[r.message for r in caplog.records]}"
    )
    assert all(record.levelno == logging.WARNING for record in caplog.records)

    # Limiting still occurs on the fallback key - failing open here would make the header's
    # absence a free pass, which is worse than the bucketing it is meant to fix.
    assert all(w is None for w in _drain(limiter, ratelimit.CONCLUSION_PATH, key))
    assert limiter.check(ratelimit.CONCLUSION_PATH, key, 0.0) is not None


def test_ratelimit_fallback_warning_logged_once_per_process(caplog):
    """A second fallback logs nothing further.

    Per-request logging turns one missing Caddyfile line into a log-volume incident, which is its
    own outage. Once is enough to find it; twice a second is a second problem.
    """
    limiter = _limiter()
    with caplog.at_level(logging.WARNING, logger=ratelimit.logger.name):
        limiter.note_fallback()
        first = len(caplog.records)
        for _ in range(50):
            limiter.note_fallback()
        after = len(caplog.records)

    assert first == 1, f"the first fallback logged {first} records, expected 1"
    assert after == 1, f"50 further fallbacks logged {after - first} more records, expected 0"


def test_ratelimit_ipv6_buckets_by_64():
    """Two addresses in one /64 share a bucket; a third in another /64 does not.

    A residential IPv6 allocation is a /64 or larger. Per-address bucketing gives one household an
    unlimited quota for free - it just uses a new address per request out of its own prefix.
    """
    same_a, _ = ratelimit.client_key("2001:db8:1234:5678::1", None)
    same_b, _ = ratelimit.client_key("2001:db8:1234:5678:aaaa:bbbb:cccc:dddd", None)
    other, _ = ratelimit.client_key("2001:db8:1234:9999::1", None)

    assert same_a == same_b, (
        f"two addresses in one /64 got separate buckets ({same_a!r}, {same_b!r}) - one allocation "
        f"has effectively unlimited quota"
    )
    assert other != same_a, f"two different /64s collapsed into one bucket ({other!r})"

    # IPv4 still buckets by full address; the /64 rule must not leak across families.
    v4_a, _ = ratelimit.client_key("203.0.113.7", None)
    v4_b, _ = ratelimit.client_key("203.0.113.8", None)
    assert v4_a != v4_b


# ---------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------


def test_ratelimit_store_is_bounded():
    """capacity+N distinct clients must not grow the store past capacity.

    An unbounded dict keyed by client IP IS the denial of service: an attacker with address
    diversity exhausts memory faster than they exhaust quota, and `defaultdict(list)` of request
    timestamps is four lines and reads correctly.
    """
    store = ratelimit.BucketStore(capacity=64, idle_seconds=3600)

    for index in range(200):
        store.get(f"v4:198.51.100.{index}", now=float(index), capacity=10)
        assert len(store) <= 64, f"the store grew to {len(store)} with a capacity of 64"

    assert len(store) == 64
    assert store.evictions == 200 - 64, (
        f"evicted {store.evictions}, expected {200 - 64}. Without the counter, address-diversity "
        f"pressure looks identical to ordinary use."
    )


def test_ratelimit_store_evicts_least_recently_seen():
    """Under pressure the survivor is the client still making requests, not the oldest arrival."""
    store = ratelimit.BucketStore(capacity=3, idle_seconds=3600)

    for key in ("a", "b", "c"):
        store.get(key, now=0.0, capacity=10)
    store.get("a", now=1.0, capacity=10)  # `a` is now the most recently seen
    store.get("d", now=2.0, capacity=10)  # forces one eviction

    assert store.evictions == 1
    assert "a" in store._buckets, "the most recently seen bucket was evicted"
    assert "b" not in store._buckets, "the least recently seen bucket survived"


# ---------------------------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------------------------


def test_ratelimit_returns_429_with_integer_retry_after():
    limiter = _limiter()
    key = "v4:203.0.113.7"
    _drain(limiter, ratelimit.CONCLUSION_PATH, key)

    wait = limiter.check(ratelimit.CONCLUSION_PATH, key, 0.0)
    assert wait is not None and wait > 0

    retry_after = max(1, int(wait) + 1)
    assert retry_after == int(retry_after)
    assert retry_after >= 1, "a Retry-After of 0 invites an immediate retry"

    # And the wire shape, through the real app.
    client = make_client(conn=FakeConn(), now=NOW)
    headers = {"X-Real-IP": "203.0.113.77"}
    responses = [
        client.get("/api/conclusion?site_id=07032000&as_of=2024-01-15", headers=headers)
        for _ in range(ratelimit.CONCLUSION_CAPACITY + 5)
    ]
    limited = [r for r in responses if r.status_code == 429]
    assert limited, (
        f"no request was limited in {len(responses)} attempts; statuses were "
        f"{sorted({r.status_code for r in responses})}"
    )
    assert "retry-after" in {k.lower() for k in limited[0].headers}
    assert limited[0].headers["retry-after"].isdigit(), (
        f"Retry-After is {limited[0].headers['retry-after']!r}, not integer seconds"
    )


def test_ratelimit_429_body_omits_estimate_keys():
    """The refusal shape: estimate keys ABSENT, not null (CLAUDE.md § 20).

    A 429 carrying `"median_pct": null` is one `?? 0` away from rendering a refusal as "nothing
    changed" - and the whole reason § 20 forbids null estimates is that a client cannot default a
    key that does not exist.
    """
    client = make_client(conn=FakeConn(), now=NOW)
    headers = {"X-Real-IP": "203.0.113.88"}

    limited = None
    for _ in range(ratelimit.CONCLUSION_CAPACITY + 5):
        response = client.get(
            "/api/conclusion?site_id=07032000&as_of=2024-01-15", headers=headers
        )
        if response.status_code == 429:
            limited = response
            break
    assert limited is not None, "nothing was limited; this test would pass vacuously"

    body = limited.json()
    assert body["error"]["code"] == errors.RATE_LIMITED

    # By name, and then by a recursive walk. Neither subsumes the other (§ 20): naming keys
    # catches one added with a null; the walk catches one added three levels down with a NUMBER.
    forbidden = {"median_pct", "range_pct", "matches", "estimate", "analogs"}

    def walk(node, path="body"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, f"{path}.{key} is an estimate key in a refusal"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(body)


def test_ratelimit_conclusion_limit_is_tighter_than_general():
    """The conclusion bucket trips first under a mixed burst.

    The Phase 10 exposure is specifically the uncached (site_id, as_of) pairs, so the general limit
    is not where the cost lives. If the general bucket tripped first, the tighter one would never
    be reached and would be decorative.
    """
    assert ratelimit.CONCLUSION_CAPACITY < ratelimit.GENERAL_CAPACITY

    limiter = _limiter()
    key = "v4:203.0.113.7"

    refusals = _drain(
        limiter, ratelimit.CONCLUSION_PATH, key, count=ratelimit.CONCLUSION_CAPACITY + 1
    )
    assert refusals[-1] is not None, "the conclusion bucket did not trip at its own capacity"
    assert all(w is None for w in refusals[:-1])

    # The general bucket still has room, which is what makes "tighter" mean anything.
    assert limiter.check("/api/signals", key, 0.0) is None, (
        "the general bucket was exhausted by the conclusion burst, so the two limits are one"
    )


def test_ratelimit_exempts_health_exact_path_only():
    """/api/health never 429s; a fabricated /api/health-other does.

    Exact match, not prefix. The external monitor must never be throttled into a false alarm - and
    a prefix exemption would also exempt a future /api/health-expensive-debug, which is precisely
    the endpoint somebody would want limited.
    """
    assert "/api/health" in ratelimit.EXEMPT_PATHS
    assert "/api/health-other" not in ratelimit.EXEMPT_PATHS

    client = make_client(conn=FakeConn(), now=NOW)
    headers = {"X-Real-IP": "203.0.113.99"}

    statuses = {
        client.get("/api/health", headers=headers).status_code
        for _ in range(ratelimit.GENERAL_CAPACITY + 20)
    }
    assert 429 not in statuses, (
        f"/api/health was rate limited (statuses {sorted(statuses)}). The external monitor would "
        f"read a throttled probe as an outage."
    )

    # THE SIBLING PATH, THROUGH THE MIDDLEWARE. Calling `RateLimiter.check` directly would skip
    # `dispatch`, which is where the exemption actually lives - measured, when the prefix-match
    # mutation left the direct-call version of this assertion green.
    #
    # /api/health-other has no route, so an allowed request 404s. That is the point: a 404 proves
    # the limiter let it through to the router, and a 429 proves it did not.
    sibling = [
        client.get("/api/health-other", headers=headers).status_code
        for _ in range(ratelimit.GENERAL_CAPACITY + 1)
    ]
    assert 429 in sibling, (
        f"/api/health-other was never limited in {len(sibling)} requests (statuses "
        f"{sorted(set(sibling))}) - the exemption is matching by PREFIX, so a future "
        f"/api/health-expensive-debug would be exempt too"
    )


def test_ratelimit_bucket_refills_over_time():
    """A refused client is allowed again once the bucket has refilled, without restarting."""
    limiter = _limiter()
    key = "v4:203.0.113.7"
    _drain(limiter, ratelimit.CONCLUSION_PATH, key, now=0.0)

    assert limiter.check(ratelimit.CONCLUSION_PATH, key, 0.0) is not None

    later = 1.0 / ratelimit.CONCLUSION_REFILL_PER_SECOND + 0.1
    assert limiter.check(ratelimit.CONCLUSION_PATH, key, later) is None, (
        "the bucket never refilled - a limited client would be refused forever"
    )
