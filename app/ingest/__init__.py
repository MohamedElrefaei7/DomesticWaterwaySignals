"""Ingest clients. See CLAUDE.md § 14 for the conventions every one of them obeys.

The single rule this package exists to enforce, restated because it is the one that gets
optimized away: an ingest client asserts that the (entity, parameter) set it RECEIVED equals the
set it REQUESTED, and hard-fails on any missing pair. A 200 with an empty payload is a failure,
not zero rows.
"""
