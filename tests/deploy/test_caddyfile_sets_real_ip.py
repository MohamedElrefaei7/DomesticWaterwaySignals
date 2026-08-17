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


# EVERY directive the /api reverse_proxy block may contain, in order. Compared BY EQUALITY.
EXPECTED_API_PROXY_DIRECTIVES = [
    "header_up X-Real-IP {http.request.remote.host}",
    "transport http {",
    "dial_timeout 5s",
    "response_header_timeout 30s",
    "read_timeout 60s",
    "}",
]


def test_caddyfile_api_proxy_directive_set_is_exact():
    """The /api proxy carries exactly these directives and nothing else.

    NOT a substring check for `X-Real-IP`. The test above proves the header is set and that it is
    set correctly; it says nothing about what ELSE the block has acquired. A substring check passes
    for a proxy block that has also grown a directive nobody reviewed - another `header_up`
    forwarding something the application then trusts, a second `reverse_proxy` upstream, a
    `flush_interval` somebody pasted from an unrelated answer. This is the edge, and the edge is
    where CLAUDE.md § 22's whole argument lives: the previous twenty-one sections describe ways to
    be WRONG, and this one describes ways to be REACHABLE.

    WHAT THE SET USED TO BE, so the widening reads as a change of fact rather than of strictness:

        Phase 10   transport http { dial_timeout 5s, response_header_timeout 30s, read_timeout 60s }
        Phase 11   the same, PLUS `header_up X-Real-IP {http.request.remote.host}`

    The addition is the one line that makes the application's per-IP limiter work at all. Every
    test in tests/api/test_ratelimit.py fabricates that header, so all of them pass whether or not
    Caddy sets it - which is why this file exists and why the set is pinned rather than searched.

    ORDER IS ASSERTED TOO, and that is not pedantry about formatting: `header_up` before
    `transport` is how the file reads top to bottom, and a list comparison reports an insertion at
    the position it happened rather than as two set differences a reader has to reconstruct.
    """
    body = _api_proxy_block()

    directives = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert directives, (
        "the /api reverse_proxy block contains no directives at all - this test would then be "
        "asserting an exact set over an empty collection, which is green forever and watching "
        "nothing"
    )

    assert directives == EXPECTED_API_PROXY_DIRECTIVES, (
        f"the /api reverse_proxy block is not what this repo says it is.\n"
        f"  found   : {directives}\n"
        f"  expected: {EXPECTED_API_PROXY_DIRECTIVES}\n"
        f"  unexpected: {[d for d in directives if d not in EXPECTED_API_PROXY_DIRECTIVES]}\n"
        f"  missing   : {[d for d in EXPECTED_API_PROXY_DIRECTIVES if d not in directives]}\n"
        f"Everything in this block is on the path between the public internet and the "
        f"application. If a directive was added deliberately, add it to "
        f"EXPECTED_API_PROXY_DIRECTIVES with the reason it is safe."
    )
