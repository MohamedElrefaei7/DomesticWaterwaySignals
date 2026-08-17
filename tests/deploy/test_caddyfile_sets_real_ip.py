"""THE ONLY TEST CONNECTING THE CADDYFILE TO THE RATE LIMITER'S BEHAVIOUR.

Every test in tests/api/test_ratelimit.py fabricates `X-Real-IP`, so all of them pass identically
whether or not Caddy sets it. Without this file the limiter could ship completely un-keyed in
production - bucketing the entire internet into one client, keyed on Caddy's own container
address - with a fully green suite above it. That is CLAUDE.md § 2's theme 2, and it is why the
live verification for Part 4 bursts the public URL rather than the container.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "Caddyfile"


def _api_proxy_block() -> str:
    """The body of the `reverse_proxy api:8000 { ... }` block, by brace matching.

    Extracted rather than searched for across the whole file, because `header_up X-Real-IP`
    anywhere in the Caddyfile would satisfy a naive `in` check while sitting in the block that
    serves the static bundle - where it does nothing for the API at all.
    """
    text = CADDYFILE.read_text(encoding="utf-8")
    assert text.strip(), f"{CADDYFILE} is empty - every assertion below would pass over nothing"

    match = re.search(r"reverse_proxy\s+api:8000\s*\{", text)
    assert match is not None, (
        f"no `reverse_proxy api:8000 {{` block in {CADDYFILE}. Either the API is no longer "
        f"proxied or this test is reading the wrong file; both need a human."
    )

    depth = 0
    start = match.end() - 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    raise AssertionError("unbalanced braces in the reverse_proxy block")


def test_caddyfile_sets_real_ip_on_api_proxy():
    """`header_up X-Real-IP {http.request.remote.host}` on the /api proxy.

    header_up OVERWRITES. That is the property that matters: a client sending its own X-Real-IP
    cannot influence what the application reads, which is exactly what makes this header usable as
    a limiter key when X-Forwarded-For is not.
    """
    body = _api_proxy_block()

    directives = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    header_up = [line for line in directives if line.startswith("header_up")]

    assert header_up, (
        "the /api reverse_proxy block sets no header_up directive. The application sees "
        "request.client.host, which is Caddy's container address, so the per-IP rate limiter "
        "buckets the entire internet into one client - and every middleware test still passes."
    )

    real_ip = [line for line in header_up if "X-Real-IP" in line]
    assert real_ip, f"header_up directives present but none sets X-Real-IP: {header_up}"

    (directive,) = real_ip
    assert "{http.request.remote.host}" in directive, (
        f"X-Real-IP is set to {directive!r} rather than to Caddy's own view of the remote host. "
        f"Anything derived from a client-supplied header is forgeable, and a forgeable limiter key "
        f"is no limiter at all."
    )


def test_caddyfile_states_the_absent_edge_rate_limit():
    """§ 22: where an edge control is wanted and unavailable, its absence is stated in the config.

    An unmarked absence reads as a control somebody forgot to look for. The Phase 11 decision was
    to put the cost-based limit in the application and leave static assets unlimited - that is a
    decision, and it has to be findable by someone reading only this file.
    """
    body = _api_proxy_block()

    assert "xcaddy" in body, (
        "the /api proxy block does not say why there is no edge rate limiter. The residual "
        "exposure (static assets are unlimited) becomes invisible to the next reader."
    )
    assert re.search(r"rate.limit", body, flags=re.IGNORECASE), (
        "nothing in the block names the rate limit at all"
    )
