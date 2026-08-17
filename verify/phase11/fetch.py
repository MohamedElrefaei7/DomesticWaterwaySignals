"""HTTP reads, on the standard library, with the one header that makes D7 a separate step.

WHY `urllib` AND NOT A CLIENT LIBRARY. Measured, not assumed: `requirements.txt` carries psycopg,
APScheduler, SQLAlchemy, fastapi, uvicorn and boto3, and NO HTTP client. `httpx2` is in
`requirements-dev.txt` and exists for `fastapi.testclient`. These verifiers run on the instance from
the runtime venv, where a dev dependency is not installed, and an ImportError at that moment reads
as a broken verifier rather than as a missing package. So: `urllib.request`, and nothing added to
either requirements file.

`Accept-Encoding: identity` IS THE POINT OF THIS MODULE.

Route53's HTTPS_STR_MATCH check matches a literal against the FIRST 5,120 BYTES OF THE BODY AS SENT.
Caddy sits between the application and the monitor, and Caddy compresses. Every app-side test stays
green while the monitor goes permanently blind, because the application's body is correct and the
bytes on the wire are gzip. That is the whole reason D7 exists as a step separate from D6: D6 asks
whether the API says `"degraded":false`, and D7 asks whether those bytes leave the edge.

`urllib` does not send `Accept-Encoding` by default and does not decompress, so a naive fetch
happens to pass. That is worse than failing: it passes for a reason nobody chose, and it would stop
passing the day somebody adds a client library with sensible defaults. The header is set
EXPLICITLY, and `test_d_post_fails_when_body_is_gzipped_and_header_not_suppressed` drives a gzipped
body through the check to prove the assertion is about the bytes rather than about the parse.

THE BODY IS BYTES AND STAYS BYTES. Decoding to `str` before searching would let a codec normalise
away exactly the difference being looked for.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from verify.phase11.result import Precondition

DEFAULT_TIMEOUT_SECONDS = 15

# Not a browser string. A monitor identifying itself is one an operator can find in an access log
# when they are working out where a burst of 429s came from.
USER_AGENT = "dws-verify-phase11/1.0"


@dataclass(frozen=True)
class Response:
    """What came back. `body` is BYTES; see the module docstring."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        """Case-insensitive lookup. HTTP header names are case-insensitive and servers vary."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


def get(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    data: bytes | None = None,
) -> Response:
    """One request. A non-2xx is RETURNED, never raised - the status is frequently the assertion.

    Stage J asserts that 429s appear; Stage I waits for a health check to report Failure. A helper
    that raised on 4xx would make the interesting case the exception path.
    """
    request = urllib.request.Request(url, method=method, data=data)
    request.add_header("User-Agent", USER_AGENT)
    # EXPLICIT, not a default anyone can change. See the module docstring.
    request.add_header("Accept-Encoding", "identity")
    for name, value in (headers or {}).items():
        request.add_header(name, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(
                status=response.status,
                body=response.read(),
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as error:
        # A 429 or a 500 is a response, and its body and headers are what Stage J reads.
        return Response(
            status=error.code,
            body=error.read(),
            headers=dict(error.headers.items()) if error.headers else {},
        )
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        raise Precondition(
            f"{url} could not be reached: {error}. This is a precondition, not a failed check - "
            f"nothing was verified either way."
        ) from error


# ---------------------------------------------------------------------------------------------
# IMDS
# ---------------------------------------------------------------------------------------------

IMDS_BASE = "http://169.254.169.254/latest"
# SHORT. On a laptop this address is unroutable and the request hangs until something gives up;
# from the instance it answers in milliseconds. The timeout is the entire cost of the guard on the
# machine where the guard is unnecessary.
IMDS_TIMEOUT_SECONDS = 2


def imds_instance_id(getter=get) -> str | None:
    """The instance id, or None when this is not an EC2 instance.

    IMDSv2: a token is requested with PUT first. `compute.tf` sets
    `http_put_response_hop_limit = 2` (so containers can reach it) and IMDSv1 may be disabled, in
    which case a bare GET returns 401 and the naive guard concludes "not an instance" ON THE
    INSTANCE - which is the one place the guard has to work.

    Every failure is None rather than an exception: this is a "where am I" question, and the answer
    on a laptop is an unroutable address timing out.
    """
    try:
        token = getter(
            f"{IMDS_BASE}/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=IMDS_TIMEOUT_SECONDS,
        )
    except Precondition:
        return None
    if token.status != 200 or not token.body:
        return None

    try:
        identity = getter(
            f"{IMDS_BASE}/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token.body.decode("ascii", "replace")},
            timeout=IMDS_TIMEOUT_SECONDS,
        )
    except Precondition:
        return None
    if identity.status != 200 or not identity.body:
        return None
    return identity.body.decode("ascii", "replace").strip() or None
