"""Verification harness — the live-verification procedure, made rerunnable and checked.

This package is APPARATUS, not application. Nothing here is imported by `app/`, nothing here ships
with the scheduler, and the probe jobs defined here exist only to be watched. The separation is
deliberate: a probe job living in `app/` would mean production code carrying a job whose only
purpose is verification, and the next person to read the cadence table would have to work out
which entries are real.

The three checks, in the order a human runs them:

    python3 -m verify.preflight            # gates that must hold before anything else is trusted
    python3 -m verify.restart_recovery     # stop a real process, start it, watch one prompt fire
    python3 -m verify.failure_survives     # the work rolls back, the record does not

See CLAUDE.md § 13 for the conventions every check in here obeys, and CONTEXT.md's § Up Next for
the order to run them in.
"""
