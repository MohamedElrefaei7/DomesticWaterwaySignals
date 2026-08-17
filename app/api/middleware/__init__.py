"""ASGI middleware for the read API.

One module so far: `ratelimit`. See CLAUDE.md § 22's Phase 11 exception for why a rate limiter
lives in the application at all, and what it explicitly does not cover.
"""
