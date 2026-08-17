"""How near one river condition is to another. UNWEIGHTED, AND DELIBERATELY CRUDE.

PURE FUNCTIONS. Nothing here opens a connection, and nothing here has ever seen a rate.

THE METRIC IS A PLACEHOLDER, AND SAYING SO IS THE POINT
-------------------------------------------------------
Unweighted Euclidean distance over z-scored features. It is not good. It is SELF-DOCUMENTING, which
is the property that matters while nobody has grounds to choose anything better:

    an unweighted metric makes no claim about which feature matters

and after a sweep that scanned 6,966 pairs and found one contemporaneous relationship at lag 0
(docs/phase-log.md, Phase 6), "no claim about which feature matters" is the only honest claim
available. Anything better-behaved would be asserting something this project has not measured.

WHY NO FITTED WEIGHTING EXISTS HERE, AND WHY THAT IS ENFORCED STRUCTURALLY
--------------------------------------------------------------------------
Fitting a weight vector so that near events have similar rate moves IS IN-SAMPLE OPTIMIZATION
WEARING A SIMILARITY METRIC'S CLOTHES. It would be the single most damaging thing that could be
added to this file, because:

  * it is invisible in the output - the sentence says "the last 5 times conditions looked like
    this", and nothing in it reveals that "looked like this" was defined by the answer;
  * it has no held-out data anywhere in this phase to catch it;
  * and it would improve every number, which is what makes it feel like progress.

It is the same error as splitting a regime on the target (`app/signals/regimes.py`) and as a sweep
that answers "which was your best pair?" (`app/signals/sweep.py`), one layer further out.

So this module never sees an outcome. NOTHING HERE TAKES THE RATE SERIES, and
`tests/analogs/test_similarity.py::test_no_weight_vector_is_fitted_anywhere` walks this file's AST
asserting that no identifier in it is weight-shaped or rate-shaped - a guard on the module's whole
surface rather than on its current functions, because the failure is somebody adding a function
that does not exist yet. `parameters.SIMILARITY_WEIGHTS` is None and there is deliberately no code
path here that would read it.

THERE IS NO CUTOFF, AND THE DISTANCES ARE RETURNED SO A HUMAN CAN SET ONE
-------------------------------------------------------------------------
`k_nearest` returns k neighbours whatever they cost. "How similar is similar enough" is a claim
about the world that nobody can make before looking at a distribution of distances, and CLAUDE.md
§ 1 puts it on the human's side of the line. Every returned neighbour carries its distance, every
one is stored (migration 0025), and step 2 of the live procedure is somebody reading them.

A cutoff added here would ALSO be the quietest way to make the confidence gate pass: drop the far
analogs, and what remains agrees with itself. The gate would then be counting a filtered set while
reporting it as the history.

Z-SCORES COME FROM A STATED POPULATION, AND THE ENGINE PASSES ONE ENDING AT `as_of`
-----------------------------------------------------------------------------------
`scale_from` takes the population explicitly rather than deriving it from the candidates, because
the candidates are the analog set - standardizing against them would make the scale depend on which
events were selected, and the scale would then move whenever the selection did.

The engine passes the site's own series through `events.observations_through(as_of)`. Standardizing
against the FULL record would leak the future into the distances: a 2015 condition would be scored
against a spread that includes 2022, in a number nobody reads as a prediction.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Scale:
    """Per-dimension centers and spreads. What z-scoring is done against, stated as data.

    A value object rather than two loose sequences so a caller cannot pair the centers of one
    population with the spreads of another - which would produce finite, plausible, wrong distances
    with nothing in the output to show for it.
    """

    centers: tuple[float, ...]
    spreads: tuple[float, ...]

    def __post_init__(self):
        if len(self.centers) != len(self.spreads):
            raise ValueError(
                f"{len(self.centers)} centers against {len(self.spreads)} spreads - a Scale built "
                f"from two different populations."
            )


def scale_from(population) -> Scale:
    """Per-dimension mean and standard deviation over `population`, a sequence of equal-length rows.

    A DIMENSION WITH NO SPREAD GETS A SPREAD OF ZERO AND CONTRIBUTES NOTHING, rather than a small
    epsilon. Dividing by an epsilon turns a constant column into an axis where every difference is
    enormous, so a feature that never varies at a site would dominate every distance there - and
    `discharge_min` IS constant-with-`discharge_mean` at two of the four gauges (Phase 5 finding 3),
    so this is a real case rather than a defensive one. See `z_score`.

    Population statistics (`pstdev`), not sample: this is the site's whole observed record, not a
    draw from something larger.
    """
    rows = [tuple(row) for row in population]
    if not rows:
        raise ValueError(
            "cannot build a Scale from an empty population. The caller has no observations to "
            "standardize against, which is a refusal to make explicitly rather than a distance to "
            "compute against a default."
        )

    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise ValueError(f"ragged population: rows of widths {sorted(widths)}")

    columns = list(zip(*rows))
    centers = tuple(statistics.fmean(column) for column in columns)
    spreads = tuple(
        statistics.pstdev(column) if len(column) > 1 else 0.0 for column in columns
    )
    return Scale(centers=centers, spreads=spreads)


def z_score(vector, scale: Scale) -> tuple[float, ...]:
    """`vector` standardized against `scale`. A zero-spread dimension becomes 0.0.

    Zero rather than an error: a constant dimension carries no information about similarity, so the
    honest contribution is none. Erroring instead would make a site unqueryable because one of its
    five features never moved, which is a fact about that feature rather than about the query.
    """
    values = tuple(vector)
    if len(values) != len(scale.centers):
        raise ValueError(
            f"vector of width {len(values)} against a Scale of width {len(scale.centers)}"
        )
    if any(value is None for value in values):
        raise ValueError(
            "a vector carrying None cannot be standardized. A missing feature is a condition this "
            "engine has not observed, and the caller filters it out and counts it rather than "
            "letting a substituted value become an axis - see `is_comparable`."
        )

    return tuple(
        0.0 if spread == 0.0 else (value - center) / spread
        for value, center, spread in zip(values, scale.centers, scale.spreads)
    )


def is_comparable(vector) -> bool:
    """Can this vector enter a distance at all? False when any dimension is missing.

    Exists so the engine can COUNT what it dropped instead of silently shortening a list. A
    condition with no `days_below_p20` on that date is not a distant analog, it is an unobserved
    one, and the two are different facts about the history.
    """
    return all(value is not None for value in vector)


def distance(left, right) -> float:
    """Euclidean distance between two standardized vectors. Every dimension counts the same.

    No coefficient of any kind, and no parameter through which one could arrive. See the module
    docstring: this signature is a guard, and the test that reads this file's AST is what keeps it
    one.
    """
    left_values = tuple(left)
    right_values = tuple(right)
    if len(left_values) != len(right_values):
        raise ValueError(
            f"distance between vectors of width {len(left_values)} and {len(right_values)}"
        )
    return math.sqrt(
        math.fsum((a - b) ** 2 for a, b in zip(left_values, right_values))
    )


@dataclass(frozen=True)
class Neighbour:
    """One candidate and what it cost. The distance never travels apart from the key.

    Same argument as `walkforward.Consistency` carrying its fold count: a list of dates that has
    been separated from its distances reads as "these are comparable events", and the tenth-nearest
    condition in a thin history is not comparable to anything while looking identical in a row.
    """

    key: object
    distance: float


def k_nearest(query_vector, candidates, k: int, scale: Scale) -> list[Neighbour]:
    """The `k` nearest candidates to `query_vector`, nearest first, EACH WITH ITS DISTANCE.

    `candidates` is a sequence of `(key, vector)`. NO CUTOFF IS APPLIED and none can be passed -
    there is no parameter for one. A caller wanting fewer analogs asks for a smaller k, which is a
    statement about how much evidence to gather rather than a claim about what counts as similar.

    Ties break on the key so the returned order is deterministic. Two equidistant analogs in a
    different order between runs would produce two different rank-1 rows in `analog_matches` for
    one query, and rank is part of that table's primary key.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    standardized_query = z_score(query_vector, scale)
    scored = [
        Neighbour(key=key, distance=distance(standardized_query, z_score(vector, scale)))
        for key, vector in candidates
    ]
    scored.sort(key=lambda neighbour: (neighbour.distance, str(neighbour.key)))
    return scored[:k]
