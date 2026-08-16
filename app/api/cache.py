"""An in-process TTL cache for the two expensive reads. THE KEY IS THE WHOLE QUERY STRING.

WHAT A CACHE KEYED ON THE PATH ALONE DOES, AND WHY IT IS WORSE THAN NO CACHE
----------------------------------------------------------------------------
`/api/conclusion?site_id=07032000&as_of=2022-10-11` and the same path with `as_of=2023-09-19` are
two different questions with IDENTICALLY SHAPED ANSWERS. Key on the path and the second caller
receives the first caller's conclusion: a real sentence, a real median, a real set of analog dates,
computed from a date nobody asked about. Nothing in the response is malformed, nothing raises, and
the only way to notice is to already know what the answer should have been.

So the key is built from the request's ACTUAL query string rather than from a tuple the route
assembles by hand. A hand-assembled key is a key somebody forgets to extend when a parameter is
added, and the symptom of that omission is the same silent cross-serving. Building it from
`request.query_params` makes the key total by construction: a parameter that reaches the route
reached the key first.

WHY 60 SECONDS
---------------
Long enough that a dashboard polling every few seconds, or a page mounting several components that
each ask, costs one computation. Short enough that a human running the ingest, rebuilding features
and refreshing does not sit looking at a stale answer wondering whether the build worked.

WHAT IS NOT CACHED, AND IT IS THE IMPORTANT HALF
-------------------------------------------------
`/api/health` NEVER. A cached health check reports the state of the world up to a minute ago, and
health is the one endpoint where that is unacceptable - it is read precisely when somebody suspects
something is wrong, and a minute of staleness is a minute of "it says it is fine" during an
incident. `tests/api/test_health.py::test_health_is_never_cached` changes the database between two
calls and asserts the second answer moved.

EVERY CACHED RESPONSE CARRIES `computed_at`
--------------------------------------------
The time the VALUE was computed, not the time it was served - so a cache hit returns an older
timestamp than "now", which is exactly the information a reader needs. A `computed_at` set at serve
time would be a field that says nothing while looking like provenance.

IN-PROCESS, SO IT IS PER-WORKER. Two uvicorn workers hold two caches and may serve answers computed
seconds apart. That is fine for a 60-second TTL over a read-only database and it is stated here
rather than discovered: a shared cache is a second piece of infrastructure with its own failure
modes, and this project does not have a second piece of infrastructure yet.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Decision 8.
DEFAULT_TTL_SECONDS = 60


@dataclass(frozen=True)
class Cached:
    """A value and WHEN IT WAS COMPUTED. The pair is the unit; neither is stored alone."""

    value: Any
    computed_at: datetime


def key_from_request(request) -> tuple:
    """`(path, every query parameter, sorted)`. Decision 8.

    `multi_items()` rather than `dict(query_params)`: a repeated parameter is preserved, so
    `?horizon=nearby&horizon=1_month` cannot collide with `?horizon=1_month`. Sorted so parameter
    ORDER does not fragment the cache into one entry per permutation.

    The path is included because one cache instance serves one route today and there is no reason
    to make that assumption load-bearing.
    """
    return (request.url.path, tuple(sorted(request.query_params.multi_items())))


class TTLCache:
    """A tiny bounded-lifetime cache. Nothing evicts on size; entries expire on read.

    A LOCK, BECAUSE UVICORN RUNS SYNC ROUTES ON A THREAD POOL. Without one, two simultaneous first
    requests race on the dict and both compute - harmless here - but a partially-written entry read
    by a third is not, and "harmless today" is how a data structure acquires a heisenbug.

    `clock` is injectable so the expiry can be tested without sleeping. A test that sleeps for the
    TTL is a test that adds a minute to the suite and gets deleted.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, clock=time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[tuple, tuple[float, Cached]] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def get_or_compute(self, key: tuple, compute) -> Cached:
        """Return the cached value, or compute one and remember it with the time it was computed.

        `compute` IS CALLED WITH THE TIMESTAMP rather than being asked to produce one. That is what
        makes `computed_at` single-sourced: the response model carries the field, so somebody has
        to fill it in, and if the route filled it in from its own clock there would be two answers
        to "when was this computed" - one in the cache and one in the body - which would agree on
        a miss and diverge on every hit. Diverge in the flattering direction, too: the body would
        always say "just now".

        `compute` runs OUTSIDE the lock. Holding a lock across a database round trip would
        serialize every request for a different key behind the slowest one, which turns a cache
        into a queue.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > self._clock():
                return entry[1]

        computed_at = datetime.now(timezone.utc)
        cached = Cached(value=compute(computed_at), computed_at=computed_at)

        with self._lock:
            self._entries[key] = (self._clock() + self._ttl, cached)
        return cached

    def clear(self) -> None:
        """Drop everything. For tests, and for nothing else - there is no invalidation endpoint.

        An HTTP route that cleared this cache would be a non-GET route, which decision 1 forbids,
        and a GET with a side effect, which is worse.
        """
        with self._lock:
            self._entries.clear()


# The two cached endpoints share nothing but this module. Separate instances so clearing one in a
# test cannot silently clear the other and make a stale-cache bug invisible.
CONCLUSION_CACHE = TTLCache()
SIGNALS_CACHE = TTLCache()
