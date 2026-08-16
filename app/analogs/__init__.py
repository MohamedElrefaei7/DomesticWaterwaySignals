"""The analog engine: the user-facing output, and the thing that says "insufficient history".

`app/features/` computes numbers about the river and the market. `app/signals/` measured whether
they are related, and the answer was essentially no: 6,966 pairs scanned, ONE passed, at LAG 0,
with zero passing rows at any non-zero lag in either direction (CONTEXT.md, PHASE 6 - VERIFIED).

THIS PACKAGE IS BUILT ON TOP OF THAT, AND THE FACT IS NOT SOFTENED ANYWHERE IN IT. The engine will
refuse most or all queries against this dataset, and:

    THAT IS THE CORRECT OUTPUT AND IT IS THE DELIVERABLE.
    AN ENGINE THAT FINDS CONFIDENT ANALOGS WHERE THE SWEEP FOUND NO RELATIONSHIP HAS A BUG.

Every module here is arranged so the honest answer is the easy answer:

    parameters.py   EVERY human-owned number in one file, with its provenance. Four of them are
                    None on purpose - weights, a similarity cutoff, a seasonal window - and each
                    None is a stated position rather than a gap.
    events.py       detection using ONLY observations up to the detection date. An event's depth
                    and duration are outcomes, never part of its definition, because a period
                    defined by how it turned out is defined using its own future.
    similarity.py   unweighted Euclidean over z-scored features. NO FITTED WEIGHTING, enforced by
                    a test that walks this module's AST rather than its current functions.
    outcomes.py     ONE forward window, fixed before any outcome is looked at. Reporting the
                    strongest of several windows is the sweep's multiple-comparisons problem
                    relocated somewhere with no q-values to catch it.
    gate.py         CLAUDE.md § 7's >=4 analogs and >=70% consistency. Refusal is the default path
                    and it is not an error. THE GATE RUNS BEFORE THE ESTIMATE EXISTS.
    render.py       the sentence, carrying its own K, D and window - because the sentence is the
                    unit that gets quoted, and a claim that does not carry its denominator will be
                    quoted without one.
    engine.py       the CLI and the function Phase 8's API will call. No cadence entry.

THE ARITHMETIC THIS PACKAGE IS ARRANGED AROUND is not the sweep's ~350-on-noise. It is smaller and
sharper: A SUSTAINED LOW-WATER PERIOD PRODUCES A DETECTION EVERY DAY IT CONTINUES. The 2022 event
alone would contribute dozens, and ">= 4 analogs" would then be satisfied four times over BY ONE
EVENT - conviction manufactured from a single coincidence, arriving in the exact form the gate
cannot see, because four analogs is four analogs. `events.collapse` is the whole answer to that,
and both counts are stored so its effect is readable rather than assumed.

CLAUDE.md § 19 is the contract these modules implement.
"""
