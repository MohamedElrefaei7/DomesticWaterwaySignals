"""The routes. Each one reads, maps, and returns; none of them computes anything.

A route in this package is allowed to: call a module beneath `app/`, run a SELECT, and rearrange
what came back into a response model. It is not allowed to evaluate a gate, pick a threshold, fit a
climatology, decide whether a table is fresh, or sum anything the database did not sum.

That is not style. A second implementation of the confidence gate would be CLAUDE.md § 4's
two-tables-of-one-fact failure arriving in the layer users actually see - the layer whose numbers
get screenshotted - and it would diverge from the first one silently, because both would keep
returning plausible answers.

`tests/api/test_contract.py::test_api_modules_contain_no_gate_logic` asserts it structurally: the
gate's thresholds may be referenced through `parameters.` and may not be written down, and nothing
here may compare an analog count against anything.
"""
