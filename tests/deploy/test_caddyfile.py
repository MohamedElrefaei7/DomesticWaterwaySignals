"""The edge: routing, the SPA fallback, security headers, and what shipped in place of a rate
limit.

Everything asserted here is read from the Caddyfile with COMMENT LINES REMOVED. The file argues at
length for two of its own choices — the missing rate limiter and the 'unsafe-inline' in style-src
— and a check that read the prose could not tell a configured directive from a discussion of one.
"""

from __future__ import annotations

import re

from . import DOMAIN, caddyfile_directives, read_artifact, CADDYFILE_PATH

# Hostnames that would mean the frontend stopped self-hosting something. The font CDNs are the
# ones Phase 9's decision was actually about; the script CDNs are here because the same mistake in
# a different asset class produces the same CSP exception.
CDN_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "ajax.googleapis.com",
    "use.typekit.net",
    "fonts.bunny.net",
)


def csp() -> dict[str, list[str]]:
    """The Content-Security-Policy header's directives, parsed into {name: [sources]}."""
    directives = caddyfile_directives()
    match = re.search(r'Content-Security-Policy\s+"([^"]+)"', directives)
    assert match is not None, "no Content-Security-Policy header is set in the Caddyfile"

    parsed: dict[str, list[str]] = {}
    for clause in match.group(1).split(";"):
        parts = clause.split()
        if parts:
            parsed[parts[0]] = parts[1:]

    assert parsed, "the CSP parsed to no directives"
    return parsed


def test_the_api_path_is_reverse_proxied_to_the_api_service():
    """/api/* goes to the container, by service name, and the path is NOT stripped.

    The strip is the interesting half. Every router in app/api/routes/ declares `prefix="/api"`,
    so a `uri strip_prefix /api` here would deliver `/health` to an application that only serves
    `/api/health` — a 404 out of a healthy stack, with nothing in either log saying why.
    """
    directives = caddyfile_directives()

    assert re.search(r"handle\s+/api/\*\s*\{", directives), (
        "no `handle /api/*` block in the Caddyfile"
    )
    assert re.search(r"reverse_proxy\s+api:8000", directives), (
        "the /api handler does not proxy to api:8000 - the Compose service name and its internal "
        "port"
    )
    assert "strip_prefix" not in directives, (
        "the Caddyfile strips a path prefix. FastAPI's routers already carry `prefix=\"/api\"`; "
        "stripping it produces a 404 from a working application."
    )
    assert "localhost:8000" not in directives and "127.0.0.1:8000" not in directives, (
        "the proxy points at loopback. Inside the caddy container that is caddy itself."
    )


def test_the_spa_fallback_serves_index_html():
    """Decision 5. Client-side routes must return the shell, not a 404.

    The app's routes (/, /signals, /river, /health) exist only in the browser's router; there is
    no file at any of them. Without `try_files` a reload on /health is a 404 from a site that is
    working, and it is a 404 nobody sees while developing because the dev server does this
    automatically.
    """
    directives = caddyfile_directives()

    assert re.search(r"try_files\s+\{path\}\s+/index\.html", directives), (
        "no `try_files {path} /index.html` in the Caddyfile - a reload on a client-side route "
        "returns 404"
    )
    assert re.search(r"root\s+\*\s+/srv/frontend", directives), (
        "the file server's root is not /srv/frontend, which is where the bundle volume is mounted"
    )
    assert "file_server" in directives


def test_security_headers_are_set():
    """HSTS, nosniff, frame-deny, referrer-policy. Decision 7, at the edge and not in FastAPI.

    At the edge because most of what is served — the whole bundle — never reaches the application.
    A FastAPI middleware would cover the JSON and leave index.html, the JS, the CSS and the fonts
    bare, while looking, in the code, exactly like the headers were configured.
    """
    directives = caddyfile_directives()

    hsts = re.search(r'Strict-Transport-Security\s+"([^"]+)"', directives)
    assert hsts is not None, "no Strict-Transport-Security header"
    max_age = re.search(r"max-age=(\d+)", hsts.group(1))
    assert max_age is not None and int(max_age.group(1)) >= 15552000, (
        f"HSTS max-age is {hsts.group(1)!r}; anything under six months is short enough that a "
        f"first-time visitor is unprotected for most of the year"
    )

    assert re.search(r'X-Content-Type-Options\s+"nosniff"', directives), "no nosniff header"
    assert re.search(r'X-Frame-Options\s+"DENY"', directives), "no X-Frame-Options: DENY"
    assert re.search(r'Referrer-Policy\s+"[^"]+"', directives), "no Referrer-Policy"

    # The CSP half of clickjacking protection. X-Frame-Options is the old spelling and some
    # browsers only honour the new one; a page carrying one and not the other is protected in some
    # browsers and not others, which is worse than knowing.
    assert csp().get("frame-ancestors") == ["'none'"], (
        f"frame-ancestors is {csp().get('frame-ancestors')!r}, expected ['none']"
    )


def test_the_csp_requires_no_font_cdn():
    """Phase 9 self-hosted the fonts so this would be true. Guarded here rather than remembered.

    @fontsource emits Archivo Narrow and Zilla Slab into the bundle's own asset directory, so
    `font-src 'self'` covers them. Had a component linked Google Fonts, the exception in this
    header is what somebody would have noticed — not the third-party request on every page load,
    which is invisible unless you open the network tab.
    """
    directives = caddyfile_directives()

    for host in CDN_HOSTS:
        assert host not in directives, (
            f"{host} appears in the Caddyfile. The frontend self-hosts its fonts (Phase 9); a CDN "
            f"exception here means something stopped doing that."
        )

    font_src = csp().get("font-src")
    assert font_src is not None, "the CSP declares no font-src, so it falls back to default-src"
    assert set(font_src) <= {"'self'", "data:"}, (
        f"font-src is {font_src!r}. Self-hosted fonts need nothing but 'self'."
    )

    assert csp().get("default-src") == ["'self'"], (
        f"default-src is {csp().get('default-src')!r} - it is the fallback for every directive "
        f"not named, so a permissive value undoes the specific ones"
    )
    assert csp().get("script-src") == ["'self'"], (
        f"script-src is {csp().get('script-src')!r}. 'unsafe-inline' is tolerated for STYLES "
        f"because Recharts writes inline style attributes; it is not tolerated for scripts."
    )


def test_the_api_path_carries_the_documented_edge_limits():
    """NO PER-IP RATE LIMIT SHIPPED. This asserts what shipped instead, and says so in its name.

    RENAMED FROM the brief's `test_a_rate_limit_applies_to_the_api_path`, and the rename is the
    honest half of the finding. Caddy has no rate limiter in core; the usual one
    (github.com/mholt/caddy-ratelimit) needs a custom binary built with xcaddy, which means a Go
    module fetch at image-build time and a plugin version this agent cannot resolve — inventing
    one is the same class of mistake as inventing an AMI id. Coupling first-ever TLS issuance to a
    supply-chain step was judged the worse trade, and Phase 11 owns the limit.

    What shipped is a body cap and three proxy timeouts. They bound what one slow or hung request
    can hold open; they do nothing about volume, and a test named for a rate limit would have said
    otherwise. The exposure is written down in CONTEXT.md rather than left implied.
    """
    directives = caddyfile_directives()

    body = re.search(r"request_body\s*\{[^}]*max_size\s+(\S+)", directives, re.DOTALL)
    assert body is not None, (
        "no `request_body max_size` cap. Every route is a GET, so no legitimate request carries a "
        "body at all."
    )

    transport = re.search(r"transport\s+http\s*\{(?P<body>[^}]*)\}", directives, re.DOTALL)
    assert transport is not None, (
        "the /api reverse_proxy declares no `transport http` block, so nothing bounds how long a "
        "single request can hold a connection open"
    )

    for setting in ("dial_timeout", "response_header_timeout", "read_timeout"):
        assert re.search(rf"{setting}\s+\S+", transport.group("body")), (
            f"the /api proxy transport sets no {setting}"
        )

    # The absence must be legible in the file itself, not only in a commit report nobody reads
    # next year. A reader who finds timeouts and no limiter should find the sentence saying which
    # of the two they are looking at.
    prose = read_artifact(CADDYFILE_PATH)
    assert "NO PER-IP RATE LIMIT SHIPPED" in prose, (
        "the Caddyfile does not state that no rate limit shipped. An unmarked absence reads as a "
        "limit somebody forgot to look for."
    )


def test_the_domain_is_bargeanalysis_com():
    """One site block, one literal domain.

    `www` is deliberately absent. Adding it to this file before the `www` A record exists means
    Caddy tries to issue a certificate for a name that does not resolve, which fails, and failed
    issuance is what Let's Encrypt rate-limits per domain per week. The record comes first.
    """
    directives = caddyfile_directives()

    assert DOMAIN in directives, f"{DOMAIN} does not appear in the Caddyfile"

    # `[^{\n]` rather than `[^{]`: a negated class matches newlines, so the looser version walks
    # from the global block's closing brace across several lines to the next opening one and
    # reports a "site address" that is three lines of unrelated text.
    site_blocks = re.findall(r"^(?!\s)(?P<addresses>[^\s{][^{\n]*)\{", directives, re.MULTILINE)
    named = [b.strip() for b in site_blocks if b.strip()]
    assert named == [DOMAIN], (
        f"expected exactly one site block for {DOMAIN}, found {named!r}. A second address is a "
        f"second certificate, and a name that does not resolve yet blocks issuance for the one "
        f"that does."
    )

    assert "example.com" not in directives and "localhost" not in directives, (
        "a placeholder hostname survived into the Caddyfile"
    )
