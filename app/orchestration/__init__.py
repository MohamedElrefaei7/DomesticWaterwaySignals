"""Orchestration: the layer that observes every job this system will ever run.

Built before the first ingest client exists, so the first data that ever lands is already
observed. See CLAUDE.md § 12 for the contract this package implements.
"""
