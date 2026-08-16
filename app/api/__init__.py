"""The read layer. EVERY HONESTY GUARANTEE BENEATH THIS PACKAGE HAS TO SURVIVE SERIALIZATION.

Seven phases built guards that live in Python objects and in database constraints. This is the
first package where they have to hold on the far side of a JSON encoder, and a serializer is
exactly where they stop holding quietly:

    a refusal with `median_pct: null`      one frontend default away from rendering `0%`
    a q_value without its grid_size        the multiple-comparisons problem, re-created at the API
    a sentence without its denominators    a claim that gets quoted with the evidence left behind
    a NULL rate coalesced to 0             "freight was free", below every gate built to catch it
    a bare {"status": "ok"}                CLAUDE.md § 2's theme 1, in the layer users actually see

NOTHING HERE COMPUTES ANYTHING. No gate, no threshold, no climatology, no freshness verdict. Every
number this package emits was produced by a module beneath it, and
`tests/api/test_contract.py::test_api_modules_contain_no_gate_logic` asserts that structurally.

READ-ONLY, AND IT IS TWO PROPERTIES RATHER THAN ONE. No non-GET route is declared (asserted by
test), AND the analog engine is called with `persist=False` so the one code path under here that
COULD write does not. The HTTP verbs are the visible half; the engine's own default is the half
that would have written a row on every request.
"""
