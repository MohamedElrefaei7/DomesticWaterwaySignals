"""The threshold-duration family: consecutive days below a level, and NULL across a gap.

PURE FUNCTIONS. Nothing here opens a connection.

THE THRESHOLDS ARE PERCENTILES OF EACH SITE'S OWN RECORD, AND THAT IS A STAND-IN
--------------------------------------------------------------------------------
CLAUDE.md § 1 puts "threshold values that define an event" on the never-delegate list, alongside
the gauge site list and the confidence gate. What counts as "low" on the Mississippi at Memphis is
a modelling decision with operational consequences, and this agent does not pick one.

SO THIS MODULE BUILDS THE MECHANISM AND SEEDS IT WITH PERCENTILES OF THE SITE'S OWN HISTORY - the
5th, 10th and 20th. A percentile is a PROPERTY OF THE DATA rather than a judgement about the river:
"the lowest 5% of days this gauge has ever recorded" is a statement anybody can check, and it is
self-documenting in a way that `LOW_WATER_CFS = 150000` is not.

THERE IS DELIBERATELY NO ABSOLUTE cfs OR STAGE-IN-FEET CONSTANT IN THIS FILE, and a test asserts
it. An absolute threshold arriving here without a source would look exactly like a measured one six
months later, and every downstream conclusion would inherit an authority nothing gave it. When a
human supplies operational thresholds with a source, they replace the percentile seeds and that is
its own commit.

A MISSING DAY RESETS THE COUNTER TO NULL, NEVER TO ZERO
-------------------------------------------------------
This is the decision the module exists for, and it is one line either way.

    ZERO ASSERTS THE RIVER CAME BACK UP. It is a measurement: "the run of low days ended here."
    NULL SAYS WE DO NOT KNOW.       It is the absence of one.

Across the Memphis 1994-2014 hole and the Baton Rouge 2023-01-04 to 2023-08-14 gap, zeroing would
manufacture "the low-water run ended" on a day nobody observed - twenty years of them at Memphis -
and every feature built on run length would read those as recoveries that happened. These are real
ranges in this project's own `gauge_known_gaps` table, so the distinction is load-bearing rather
than theoretical, and the tests use those ranges rather than invented ones.

KNOWLEDGE COMES BACK THE MOMENT A DAY IS NOT BELOW THE THRESHOLD. After a gap the run length is
unknown while the river is still low - the run might have started before the gap - but a day AT OR
ABOVE the threshold sets it to a definite 0 regardless of what came before, because no run can span
a day that was not below. That is why the unknown state is escaped by an ordinary day rather than
by a rule.
"""

from __future__ import annotations

from datetime import date, timedelta

# THE PERCENTILE LEVELS, WHICH ARE THE SEED. Not river levels - see the module docstring.
#
# 5 / 10 / 20 rather than a single level: a run below the 20th percentile is common and long, a run
# below the 5th is rare and short, and which of the three carries signal is a Phase 6 measurement
# rather than something to guess now. Building one and adding the others later would mean the
# lead-lag sweep only ever saw the one somebody guessed.
PERCENTILES: tuple[int, ...] = (5, 10, 20)


def feature_name_for(percentile_level: int) -> str:
    """`days_below_p05`. THE ONE PLACE A THRESHOLD FEATURE NAME IS CONSTRUCTED.

    Zero-padded so `p05` sorts before `p10` in every listing a human reads, and centralised so that
    nothing builds a feature name by concatenation at write time - the registry is the vocabulary
    (migration 0020), and a name assembled somewhere else is how a row appears with no entry.
    """
    return f"days_below_p{percentile_level:02d}"


def percentile(values, level: float) -> float:
    """Linear interpolation between order statistics. `level` is 0-100.

    Written out rather than taken from `statistics.quantiles`, which returns cut points for a
    partition rather than a single requested percentile and would need index arithmetic here
    anyway - arithmetic that is the actual thing worth being able to read.
    """
    if not values:
        raise ValueError(
            "cannot take a percentile of an empty record. A site with no observations has no "
            "threshold, and defaulting one would invent the level this module refuses to invent."
        )
    if not 0 <= level <= 100:
        raise ValueError(f"percentile level {level} is outside 0-100")

    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * level / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


def thresholds_for(values, levels: tuple[int, ...] = PERCENTILES) -> dict[int, float]:
    """`{5: x, 10: y, 20: z}` from a site's own observed record.

    THE FULL RECORD, not a build window: a threshold recomputed from a trailing window would move
    every time the build ran, so "days below the 5th percentile" would mean something different
    each morning and no run length would be comparable across runs.
    """
    usable = [v for v in values if v is not None]
    return {level: percentile(usable, level) for level in levels}


def days_below(dated_values, threshold: float) -> list[tuple[date, int | None]]:
    """Consecutive days below `threshold`, ending on each row's own date.

    STRICTLY BELOW. A value exactly at the threshold does not count, and the boundary is asserted
    by a test rather than left to whichever comparison got typed. With a percentile threshold the
    exact-equality case is not hypothetical: a percentile of a record with repeated values IS one
    of the observed values, so the boundary is hit by real rows.

    Returns None for a date whose run length cannot be known - the day after a gap, while the river
    is still below - rather than 0. See the module docstring; this is the decision.
    """
    ordered = sorted(dated_values, key=lambda row: row[0])

    rows: list[tuple[date, int | None]] = []
    run: int | None = 0
    previous_day: date | None = None

    for day, value in ordered:
        if previous_day is not None and day - previous_day > timedelta(days=1):
            # A GAP. Whatever the run was before it, it cannot be continued across days nobody
            # observed - and it cannot be declared over either.
            run = None

        if value is None:
            # A present row with no value is the same statement as an absent one about run length.
            run = None
        elif value < threshold:
            # NOT `run = 1` when run is None. A low day after a gap does not start a new run: the
            # run may have begun before the gap and this count would understate it, which reads as
            # a short event rather than as an unknown one.
            run = None if run is None else run + 1
        else:
            # KNOWLEDGE RESTORED. No run of below-threshold days can span a day that was not below,
            # so this is a definite zero no matter what preceded it - including a gap.
            run = 0

        rows.append((day, run))
        previous_day = day

    return rows


def build_days_below(observations, *, level: int) -> list[tuple]:
    """`(date, value)` pairs -> `(date, run_length, anomaly, climatology_n_years)` rows.

    THE BUILDER SIGNATURE THE REGISTRY CALLS, bound to a percentile level with functools.partial.
    Plain tuples rather than a dataclass so registry.py can import this module without this module
    importing registry.py back.

    ANOMALY AND YEAR COUNT ARE BOTH None, AND THAT IS CORRECT RATHER THAN UNFINISHED. A run length
    is already a departure-from-normal measure; deseasonalizing it would be subtracting a
    day-of-year median of a count from a count, which is a number with no meaning attached to it.
    Migration 0020's CHECK allows a NULL year count beside a NULL anomaly for exactly this case.
    """
    threshold = thresholds_for([value for _day, value in observations], levels=(level,))[level]
    return [(day, run, None, None) for day, run in days_below(observations, threshold)]
