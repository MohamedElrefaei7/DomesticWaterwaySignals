"""Per-client-IP rate limiting for the read API.

WHY THIS IS IN THE APPLICATION AT ALL. CLAUDE.md § 22 says rate limiting lives at the edge, and it
is right about static assets. The Phase 11 exception is narrow and stated there: `/api/conclusion`
accepts distinct `(site_id, as_of)` pairs, each of which misses the conclusion cache and runs an
analog query. The expensive request and the cheap one are the same shape, the same size and the
same path, and differ only in a query parameter whose cost only this layer knows. An edge limiter
cannot see that. The bundle, the CSS and the fonts remain unlimited, which is the residual exposure
§ 22 names rather than hides.

THE KEY IS `X-Real-IP`, AND THAT DECISION IS THE WHOLE THING.

  - `request.client.host` is Caddy's container address. A per-IP limiter keyed on it buckets the
    ENTIRE INTERNET into one client: the first hundred requests from anyone exhaust the quota for
    everyone, and the limiter reads as working the whole time.
  - `X-Forwarded-For[0]` is written by the client and never verified. An attacker rotates it per
    request and the limiter does nothing at all, while every unit test passes.
  - Caddy's `header_up X-Real-IP {http.request.remote.host}` OVERWRITES the header with the true
    remote host. The client cannot influence it.

This is CLAUDE.md § 2's theme 2 in its natural habitat: every test in tests/api/test_ratelimit.py
fabricates the header, so all of them pass identically whether or not Caddy is configured to set
it. `tests/deploy/test_caddyfile_sets_real_ip.py` is the only test that connects the configuration
to the behaviour, and live verification hits the public URL rather than the container for the same
reason.

CONFIGURATION IS MODULE CONSTANTS, matching `app/api/dependencies.py`'s `DEFAULT_LIMIT` /
`MAX_LIMIT` / `MAX_SPAN_YEARS`. There is no settings module in this project and this commit does
not introduce one. Nothing here reads `os.environ` at request time - a limit that can change
between two requests is a limit nobody can reason about from the code.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api import errors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------------------------
#
# Token buckets: `capacity` is the burst a client may spend at once, `refill_per_second` the
# sustained rate it drains back at. A bucket rather than a fixed window because a fixed window lets
# a client spend the whole quota in the last instant of one window and the whole of it again in the
# first instant of the next - twice the intended rate, at the moment it costs most.

# The whole /api surface. Generous: this is the backstop, not the interesting limit.
GENERAL_CAPACITY = 120
GENERAL_REFILL_PER_SECOND = 2.0

# /api/conclusion, which is where the money is. 20 uncached analog queries is already a burst; the
# sustained rate of one per five seconds is well above any human browsing and well below what makes
# the instance unhappy.
CONCLUSION_CAPACITY = 20
CONCLUSION_REFILL_PER_SECOND = 0.2

# THE STORE'S BOUND IS A HARD CAP, NOT A TARGET. An unbounded dict keyed by client IP IS the denial
# of service - an attacker with address diversity exhausts memory faster than they exhaust quota,
# and the tempting `defaultdict(list)` of request timestamps is four lines and reads correctly.
STORE_CAPACITY = 10_000

# A bucket nobody has touched for this long is full anyway, so dropping it loses nothing.
IDLE_EVICTION_SECONDS = 3600

# EXACT PATH MATCH, NEVER A PREFIX. The external monitor must never be throttled into a false
# alarm, and health is cheap and hits no aggregate. A prefix exemption on "/api/health" would also
# exempt a future "/api/health-expensive-debug", which is precisely the endpoint somebody would
# want limited.
EXEMPT_PATHS = frozenset({"/api/health"})

CONCLUSION_PATH = "/api/conclusion"
API_PREFIX = "/api/"

REAL_IP_HEADER = "x-real-ip"


@dataclass
class Bucket:
    tokens: float
    last_refill: float
    last_seen: float


def client_key(real_ip: str | None, client_host: str | None) -> tuple[str, bool]:
    """(bucket key, whether the fallback was used).

    IPv6 BUCKETS BY /64, IPv4 BY FULL ADDRESS. A single residential IPv6 allocation is a /64 or
    larger, so per-address bucketing hands one household an effectively unlimited quota: it simply
    uses a new address per request out of its own prefix, which costs it nothing.
    """
    used_fallback = False
    raw = (real_ip or "").strip()
    if not raw:
        raw = (client_host or "").strip()
        used_fallback = True

    if not raw:
        # Neither a header nor a peer address. One shared bucket is the safe reading: it cannot
        # under-limit, and the alternative is an unkeyed request that skips limiting entirely.
        return "unknown", used_fallback

    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        # Not an address. Key on the raw string rather than skipping - an unparseable value must
        # not become a free pass.
        return f"raw:{raw}", used_fallback

    if address.version == 6:
        network = ipaddress.ip_network(f"{address}/64", strict=False)
        return f"v6:{network.network_address}/64", used_fallback

    return f"v4:{address}", used_fallback


class BucketStore:
    """A bounded, LRU-evicting, lazily-expiring map of client key -> bucket."""

    def __init__(self, capacity: int = STORE_CAPACITY, idle_seconds: float = IDLE_EVICTION_SECONDS):
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self._buckets: OrderedDict[str, Bucket] = OrderedDict()
        self.evictions = 0

    def __len__(self) -> int:
        return len(self._buckets)

    def get(self, key: str, now: float, capacity: float) -> Bucket:
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._buckets.move_to_end(key)
            bucket.last_seen = now
            return bucket

        self._expire_idle(now)

        # EVICT THE LEAST RECENTLY SEEN, and count it. Under address-diversity pressure the counter
        # is the only signal that the limiter is being pushed rather than merely used - the request
        # rate alone looks identical.
        while len(self._buckets) >= self.capacity:
            self._buckets.popitem(last=False)
            self.evictions += 1

        bucket = Bucket(tokens=float(capacity), last_refill=now, last_seen=now)
        self._buckets[key] = bucket
        return bucket

    def _expire_idle(self, now: float) -> None:
        """Drop buckets nobody has touched. Cheap: they are ordered, so it stops at the first live
        one."""
        cutoff = now - self.idle_seconds
        while self._buckets:
            key, bucket = next(iter(self._buckets.items()))
            if bucket.last_seen > cutoff:
                return
            self._buckets.popitem(last=False)

    def clear(self) -> None:
        self._buckets.clear()
        self.evictions = 0


class RateLimiter:
    """Two buckets per client: a general one across /api, a tighter one on /api/conclusion.

    TWO, NOT ONE, because the Phase 10 exposure is specifically the uncached `(site_id, as_of)`
    pairs. A single limit set loose enough for ordinary browsing does nothing about the endpoint
    that costs something, and one set tight enough for that endpoint breaks the series views.
    """

    def __init__(self):
        self.general = BucketStore()
        self.conclusion = BucketStore()
        self._fallback_warned = False

    def reset(self) -> None:
        """Drop all state. For tests, like `app.api.cache`'s `.clear()`."""
        self.general.clear()
        self.conclusion.clear()
        self._fallback_warned = False

    def note_fallback(self) -> None:
        """WARN ONCE PER PROCESS, not once per request.

        Failing closed when the header is absent would make the app unusable in dev and in any
        direct-to-container request; failing silently would hide a Caddyfile regression until
        somebody wondered why one client could exhaust everyone's quota. Once-per-process is the
        middle: per-request logging turns a misconfiguration into a log-volume incident, which is
        its own outage.
        """
        if self._fallback_warned:
            return
        self._fallback_warned = True
        logger.warning(
            "rate limiter fell back to request.client.host: no X-Real-IP header on the request. "
            "Behind Caddy this means `header_up X-Real-IP` is missing from the /api reverse proxy, "
            "and every client is sharing one bucket keyed on Caddy's own container address. "
            "Expected in tests and for direct-to-container requests. Logged once per process."
        )

    def check(self, path: str, key: str, now: float) -> float | None:
        """None if allowed; otherwise the seconds to wait, as a float.

        A conclusion request consumes from BOTH buckets, so the tighter one trips first and the
        general one still backstops a client spreading load across every endpoint.
        """
        applicable = [(self.general, GENERAL_CAPACITY, GENERAL_REFILL_PER_SECOND)]
        if path == CONCLUSION_PATH:
            applicable.append((self.conclusion, CONCLUSION_CAPACITY, CONCLUSION_REFILL_PER_SECOND))

        # Check every bucket before spending any, or a request refused by the second bucket has
        # already cost a token in the first.
        waits = []
        for store, capacity, refill in applicable:
            bucket = store.get(key, now, capacity)
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(float(capacity), bucket.tokens + elapsed * refill)
            bucket.last_refill = now
            if bucket.tokens < 1.0:
                waits.append((1.0 - bucket.tokens) / refill)

        if waits:
            return max(waits)

        for store, capacity, refill in applicable:
            store.get(key, now, capacity).tokens -= 1.0
        return None


LIMITER = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter | None = None):
        super().__init__(app)
        self.limiter = limiter if limiter is not None else LIMITER

    async def dispatch(self, request, call_next) -> Response:
        path = request.url.path

        # Only /api is limited. The bundle is served by Caddy and never reaches this process at
        # all; /docs and /openapi.json are static and cheap.
        if not path.startswith(API_PREFIX) or path in EXEMPT_PATHS:
            return await call_next(request)

        real_ip = request.headers.get(REAL_IP_HEADER)
        client_host = request.client.host if request.client else None
        key, used_fallback = client_key(real_ip, client_host)
        if used_fallback:
            self.limiter.note_fallback()

        wait = self.limiter.check(path, key, time.monotonic())
        if wait is None:
            return await call_next(request)

        # THE REFUSAL SHAPE IS THE ONE EVERY OTHER FAILURE USES (§ 20). Its estimate keys are not
        # absent by accident - there are none to omit, because `error_response` builds the body
        # from a closed set of fields. A 429 carrying `"median_pct": null` would be one frontend
        # default away from rendering a refusal as "nothing changed".
        # int() + 1 rather than math.ceil(): tests/api/test_contract.py forbids app/api/ from
        # importing a computation module, and that guard exists to stop this layer reimplementing
        # the analog gate. Rounding a wait up is not worth an exception to it. Over-waiting by up
        # to one second is harmless; under-waiting invites an immediate retry.
        retry_after = max(1, int(wait) + 1)
        response = errors.error_response(
            code=errors.RATE_LIMITED,
            message=(
                "Too many requests. This endpoint runs an uncached analog query per distinct "
                f"(site_id, as_of) pair. Retry in {retry_after} second(s)."
            ),
            status_code=429,
            cid=errors.correlation_id(),
        )
        # Integer seconds. RFC 9110 permits a date, but a client parsing two formats is a client
        # that gets one of them wrong.
        response.headers["Retry-After"] = str(retry_after)
        return response
