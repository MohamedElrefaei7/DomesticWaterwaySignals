"""EVERY HUMAN-OWNED NUMBER IN THE ANALOG ENGINE, IN ONE FILE, WITH ITS PROVENANCE.

CLAUDE.md § 1 puts "analog-matching logic and confidence-gating logic" on the never-delegate list,
beside "threshold values that define an event". This commit builds the MECHANISM; every value the
mechanism is pointed at lives here, and each one carries a comment saying where it came from.

THE TEST OF THIS FILE IS WHETHER A HUMAN CAN CHANGE ONE NUMBER AND UNDERSTAND WHAT MOVED. So:

  * no value is computed from another value here,
  * no value is read from the database,
  * nothing outside this module hardcodes any of them, and
  * `parameters_hash()` covers all of them, so two outputs produced under different settings are
    never mistaken for two observations of one thing.

FOUR OF THESE ARE DELIBERATELY UNSET, AND `None` IS THE HONEST SEED
-------------------------------------------------------------------
`SIMILARITY_WEIGHTS`, `SIMILARITY_CUTOFF` and `SEASON_MATCH_WINDOW_DAYS` are `None`, and that is a
stated position rather than a gap waiting to be filled:

    weights     a fitted weight vector is in-sample optimization wearing a similarity metric's
                clothes, and it would be invisible in the output. Unweighted claims nothing about
                which feature matters, which is the only honest default after a sweep that found
                no feature that predicts anything (CONTEXT.md, PHASE 6 - VERIFIED).
    cutoff      "how similar is similar enough" is a claim nobody can make before looking at a
                distribution of distances. The engine returns the k nearest AND THEIR DISTANCES so
                that a human can look. Step 2 of the live procedure is that look.
    season      CLAUDE.md § 7's example sentence says "during harvest season". THIS ENGINE APPLIES
                NO SEASONAL RESTRICTION, so it does not say so - see render.py. A rendered sentence
                claiming a seasonal match that was never applied is a false claim in the one
                artifact a reader quotes.

WHAT WAS SEEDED FROM WHERE
---------------------------
    MIN_ANALOGS                    CLAUDE.md § 7, fixed: >= 4 analogs
    MIN_DIRECTIONAL_CONSISTENCY    CLAUDE.md § 7, fixed: >= 70%
    ENTRY_FEATURE / ENTRY_RUN_LENGTH_DAYS
                                   app/features/thresholds.py's percentile stand-ins. NO NEW
                                   THRESHOLD IS INVENTED HERE - see the constant's own comment.
    everything else                seeded at the value the Phase 7 brief states, and stated as a
                                   seed rather than as a finding.
"""

from __future__ import annotations

import hashlib
import json

# ---------------------------------------------------------------------------------------------
# What counts as an event.
# ---------------------------------------------------------------------------------------------

# The feature whose crossing opens an event.
#
# `days_below_p10` because it is the one feature-site pair Phase 6 found anything at all for
# (Memphis, horizon 7, lag 0, q 0.0446) - AND THAT IS A REASON TO POINT THE ENGINE HERE, NOT
# EVIDENCE THAT IT WILL FIND ANYTHING. The relationship it survives on is CONTEMPORANEOUS: lag 0,
# with zero passing rows at any non-zero lag in either direction. An engine built on it is being
# built on top of a sweep that found essentially nothing, and it should be expected to refuse.
ENTRY_FEATURE = "days_below_p10"

# The run length at which an event is considered to have begun, in days.
#
# ONE, AND ONE IS CHOSEN BECAUSE IT ADDS NO SECOND THRESHOLD ON TOP OF THE PERCENTILE. The feature
# already encodes a human-owned stand-in - "consecutive days whose daily minimum discharge is below
# this site's 10th percentile" - and a value of 1 says an event begins on the first such day.
#
# Any larger value is a NEW threshold this agent has no source for, and it is exactly the kind of
# number that reads as measured six months later (app/features/thresholds.py says the same thing
# about the percentiles themselves). RAISING IT IS A HUMAN DECISION, and raising it after seeing
# how many events each value produces is choosing a method that suited the answer.
ENTRY_RUN_LENGTH_DAYS = 1

# Days after a detection during which no new event may begin.
#
# THIS IS THE LARGEST INFLATION RISK IN THE PHASE, and it is why the gate consumes collapsed events
# rather than raw detections. A sustained low-water period produces a detection every day it
# continues: the 2022 event alone ran long enough to contribute dozens, and "4 analogs" would then
# be met by ONE event four times over - a confidence gate satisfied by a single coincidence, which
# is precisely what CLAUDE.md § 7 exists to prevent.
#
# 90 days is the brief's seed. It is roughly a season, which is the timescale of the events in
# CONTEXT.md (2022: August onset, November recovery). It is not measured.
MIN_EVENT_SEPARATION_DAYS = 90

# ---------------------------------------------------------------------------------------------
# What counts as similar.
# ---------------------------------------------------------------------------------------------

# The feature vector the distance is computed over.
#
# ALL FIVE REGISTERED FEATURES, UNWEIGHTED, WHICH IS A REFUSAL TO CHOOSE RATHER THAN A CHOICE. Two
# known consequences are recorded rather than corrected, because correcting either one is a
# weighting decision:
#
#   * `discharge_min` IS `discharge_mean` at Memphis and Vicksburg (Phase 5 finding 3), so at those
#     sites this vector counts one variable twice.
#   * `days_below_p05`, `p10` and `p20` are three thresholds on one series and move together, so
#     the run-length direction carries three of the five dimensions.
#
# Both make the metric lean where the data is duplicated. THAT IS WHAT AN UNWEIGHTED METRIC DOES,
# and it is visible here rather than hidden in a weight vector nobody can audit.
SIMILARITY_FEATURES: tuple[str, ...] = (
    "discharge_mean",
    "discharge_min",
    "days_below_p05",
    "days_below_p10",
    "days_below_p20",
)

# UNSET, AND NOT A GAP. See the module docstring. `similarity.py` has no code path that would read
# a weight vector, and tests/analogs/test_similarity.py asserts by AST walk that no identifier in
# that module is weight- or outcome-shaped - so setting this alone would not silently take effect.
SIMILARITY_WEIGHTS = None

# UNSET, AND THE ENGINE REPORTS DISTANCES SO A HUMAN CAN SET IT. See the module docstring, and step
# 2 of the live procedure: look at the distribution of distances BEFORE deciding what counts as
# near. A cutoff seeded now would be a claim about similarity made before anybody had seen one.
SIMILARITY_CUTOFF = None

# How many nearest analogs are returned.
#
# Ten, from the brief. Note what it interacts with: the gate needs >= 4 analogs WITH COMPLETE
# OUTCOMES, so k is an upper bound on the evidence and never a floor under it. Raising k does not
# make a refusal into a pass; it adds more distant analogs, which is why the distance rides on
# every one of them in the output.
K_NEAREST = 10

# UNSET. CLAUDE.md § 7's example sentence says "during harvest season"; THIS ENGINE APPLIES NO
# SEASONAL RESTRICTION, and render.py therefore omits that clause rather than asserting it. When a
# human sets a window here, the clause appears and means something.
SEASON_MATCH_WINDOW_DAYS = None

# ---------------------------------------------------------------------------------------------
# What is measured afterwards.
# ---------------------------------------------------------------------------------------------

# The forward window over which each analog's rate move is measured, in days.
#
# FIXED BEFORE THE OUTCOMES ARE LOOKED AT, AND SINGULAR ON PURPOSE. Computing the outcome at 7, 14
# and 21 days and reporting whichever is strongest is the sweep's multiple-comparisons problem
# moved somewhere with no q-values to catch it - and unlike the sweep, nothing here would record
# the two windows that were discarded. `outcomes.py` takes ONE window and has no plural parameter;
# a test asserts that by signature.
#
# 21 days is the brief's seed, and it is the horizon CLAUDE.md § 7's example claim is phrased over
# ("within 3 weeks").
OUTCOME_WINDOW_DAYS = 21

# How far back the rendered condition looks when describing the current move.
#
# 14 days, from CLAUDE.md § 7's example ("has fallen 4.2 ft in 14 days"). It describes the query
# condition in the sentence and DOES NOT ENTER THE SIMILARITY METRIC - changing it changes what the
# sentence says, not which analogs were found.
CONDITION_LOOKBACK_DAYS = 14

# ---------------------------------------------------------------------------------------------
# The confidence gate. CLAUDE.md § 7 fixes both of these; they are not this agent's to move.
# ---------------------------------------------------------------------------------------------

# ">= 4 analogs", verbatim from the contract. Manufacturing conviction from three coincidences is
# the failure the gate exists to prevent, and the mutation table has "lower this to 3" in it
# precisely because that is the shape the pressure takes when the gate refuses.
MIN_ANALOGS = 4

# ">= 70% directional consistency", verbatim from the contract.
MIN_DIRECTIONAL_CONSISTENCY = 0.70


# ---------------------------------------------------------------------------------------------
# The hash.
# ---------------------------------------------------------------------------------------------

# The names covered by `parameters_hash`. AN EXPLICIT LIST RATHER THAN A MODULE SCAN: a scan would
# silently start covering any constant somebody adds for an unrelated reason, and would silently
# stop covering one that gets renamed - so the hash would change for reasons nobody could trace,
# which is worse than a hash that has to be maintained. Adding a parameter means adding it here,
# and `test_parameters_hash_changes_when_a_parameter_changes` is what notices if you forget.
HASHED_PARAMETERS: tuple[str, ...] = (
    "ENTRY_FEATURE",
    "ENTRY_RUN_LENGTH_DAYS",
    "MIN_EVENT_SEPARATION_DAYS",
    "SIMILARITY_FEATURES",
    "SIMILARITY_WEIGHTS",
    "SIMILARITY_CUTOFF",
    "K_NEAREST",
    "SEASON_MATCH_WINDOW_DAYS",
    "OUTCOME_WINDOW_DAYS",
    "CONDITION_LOOKBACK_DAYS",
    "MIN_ANALOGS",
    "MIN_DIRECTIONAL_CONSISTENCY",
)


def current_values(overrides: dict | None = None) -> dict:
    """Every hashed parameter and its value. `overrides` exists so a test can hash a changed set
    without mutating the module, which would leak into every test that ran afterwards."""
    values = {name: globals()[name] for name in HASHED_PARAMETERS}
    if overrides:
        unknown = set(overrides) - set(values)
        if unknown:
            raise ValueError(
                f"not hashed parameters: {sorted(unknown)}. Add them to HASHED_PARAMETERS if they "
                f"belong in the hash - an override for a name the hash does not cover would "
                f"produce two different results carrying the same parameters_hash."
            )
        values.update(overrides)
    return values


def parameters_hash(overrides: dict | None = None) -> str:
    """A stable hash of every human-owned value above.

    STORED ON EVERY QUERY, for migration 0022's reason one layer up: two outputs produced under
    different similarity settings are not comparable, and without the hash there is no way to know
    they differ - they sit in the same table looking like two observations of one thing. This
    project has already done exactly that once, when Phase 5 contradicted Phase 4's headline on the
    same data by changing what a feature meant.

    `sort_keys` and a JSON encoding rather than `hash()` or `repr()`: the first is salted per
    process and would differ between runs of identical parameters, and the second would change if a
    tuple became a list without any value changing.
    """
    encoded = json.dumps(current_values(overrides), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
