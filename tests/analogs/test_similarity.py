"""The metric is unweighted, nothing is fitted, and there is no cutoff.

Test 7 is the one that matters and it is structural rather than behavioural: the failure it guards
is somebody ADDING a fitting path that does not exist yet, and no behavioural test can be written
against a function nobody has written. Same reasoning as
`tests/signals/test_sweep.py`'s scan of the sweep's public surface for a best-pair accessor.
"""

import ast
import math
from pathlib import Path

import pytest

from app.analogs import parameters, similarity

SIMILARITY_SOURCE = Path(similarity.__file__)


def test_distance_is_unweighted_on_z_scored_features():
    """Test 6. Hand-computed, against a population whose statistics are trivial to verify.

    Not checked against the module's own output. A metric tested against itself agrees with itself
    in both directions of every mutation, which is the failure `tests/signals/conftest.py` names.
    """
    # Two dimensions. Column A: mean 3, pstdev 2 (values 1,3,5). Column B: mean 20, pstdev 10.
    population = [(1.0, 10.0), (3.0, 20.0), (5.0, 30.0)]

    scale = similarity.scale_from(population)
    assert scale.centers == (3.0, 20.0)
    assert scale.spreads == pytest.approx((math.sqrt(8 / 3), math.sqrt(200 / 3)))

    # Chosen so the z-scores are exactly +/-1 in each dimension.
    left = (3.0 + math.sqrt(8 / 3), 20.0)
    right = (3.0, 20.0 + math.sqrt(200 / 3))

    assert similarity.z_score(left, scale) == pytest.approx((1.0, 0.0))
    assert similarity.z_score(right, scale) == pytest.approx((0.0, 1.0))

    # sqrt(1^2 + 1^2). EQUAL WEIGHT ON BOTH AXES: any coefficient would move this off sqrt(2).
    assert similarity.distance(
        similarity.z_score(left, scale), similarity.z_score(right, scale)
    ) == pytest.approx(math.sqrt(2.0))


def test_a_zero_spread_dimension_contributes_nothing_rather_than_dominating():
    """A constant feature is not an axis where every difference is enormous.

    Real case, not defensive: `discharge_min` IS `discharge_mean` at Memphis and Vicksburg (Phase 5
    finding 3), so a site can carry a dimension that never varies. Dividing by an epsilon would
    make that dimension dominate every distance at exactly those sites.
    """
    population = [(1.0, 7.0), (2.0, 7.0), (3.0, 7.0)]
    scale = similarity.scale_from(population)

    assert scale.spreads[1] == 0.0
    assert similarity.z_score((99.0, 7.0), scale)[1] == 0.0
    assert similarity.z_score((2.0, -1000.0), scale) == pytest.approx((0.0, 0.0))


def test_no_weight_vector_is_fitted_anywhere():
    """Test 7. NO IDENTIFIER IN similarity.py IS WEIGHT-SHAPED OR OUTCOME-SHAPED.

    An AST walk over the whole module rather than a check of its current functions, because the
    failure guarded against is a function that does not exist yet. Fitting a weight vector so that
    near events have similar rate moves is IN-SAMPLE OPTIMIZATION WEARING A SIMILARITY METRIC'S
    CLOTHES: invisible in the output, with no held-out data anywhere in this phase to catch it, and
    it would improve every number — which is what makes it feel like progress.

    Docstrings and comments are exempt by construction (they are Constant nodes, not identifiers),
    which is why this module can describe the failure at length while being unable to commit it.
    """
    forbidden = ("weight", "outcome", "rate", "fit", "coef", "regress", "optimi")

    tree = ast.parse(SIMILARITY_SOURCE.read_text())
    offenders = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.extend(alias.name for alias in node.names)

        for name in names:
            lowered = name.lower()
            if any(token in lowered for token in forbidden):
                offenders.append(name)

    assert not offenders, (
        f"similarity.py contains {sorted(set(offenders))}. This module never sees an outcome and "
        f"carries no coefficient of any kind — a fitted weighting here would be in-sample "
        f"optimization with nothing in the output to reveal it."
    )

    # And the parameter is None, so there is no value to start reading either.
    assert parameters.SIMILARITY_WEIGHTS is None


def test_k_nearest_are_returned_with_their_distances():
    """Test 8. The distance never travels apart from the analog it belongs to.

    A list of dates separated from its distances reads as "these are comparable events", and the
    tenth-nearest condition in a thin history is not comparable to anything while looking identical
    in a row. Same argument as `walkforward.Consistency` carrying its fold count.
    """
    population = [(0.0,), (1.0,), (2.0,), (3.0,), (4.0,)]
    scale = similarity.scale_from(population)

    candidates = [("far", (4.0,)), ("near", (2.0,)), ("middle", (3.0,))]
    found = similarity.k_nearest((2.0,), candidates, 3, scale)

    assert [neighbour.key for neighbour in found] == ["near", "middle", "far"]
    assert found[0].distance == pytest.approx(0.0)
    assert found[1].distance < found[2].distance
    assert all(hasattr(neighbour, "distance") for neighbour in found)


def test_ties_break_deterministically():
    """Two equidistant analogs must not swap places between runs.

    `analog_matches` has `rank` in its primary key, so a nondeterministic order would write two
    different rank-1 rows for one query across two runs of the same question.
    """
    population = [(0.0,), (2.0,), (4.0,)]
    scale = similarity.scale_from(population)
    candidates = [("b", (0.0,)), ("a", (4.0,))]

    first = similarity.k_nearest((2.0,), candidates, 2, scale)
    second = similarity.k_nearest((2.0,), list(reversed(candidates)), 2, scale)

    assert [n.key for n in first] == [n.key for n in second] == ["a", "b"]


def test_no_similarity_cutoff_is_applied():
    """Test 9. A wildly distant analog is still returned, WITH ITS DISTANCE.

    Decision 3: "how similar is similar enough" is a claim nobody can make before looking at a
    distribution of distances, and it belongs to a human under CLAUDE.md § 1. A cutoff would also
    be the quietest way to make the confidence gate pass — drop the far analogs and what remains
    agrees with itself, while the gate reports the filtered set as the history.
    """
    population = [(0.0,), (1.0,), (2.0,)]
    scale = similarity.scale_from(population)
    candidates = [("near", (1.0,)), ("absurd", (10_000.0,))]

    found = similarity.k_nearest((1.0,), candidates, 2, scale)

    assert len(found) == 2, "a distant analog was dropped — that is a cutoff"
    assert found[1].key == "absurd"
    assert found[1].distance > 1_000

    assert parameters.SIMILARITY_CUTOFF is None

    # And there is no parameter one could arrive through.
    import inspect

    assert "cutoff" not in inspect.signature(similarity.k_nearest).parameters
    assert "threshold" not in inspect.signature(similarity.k_nearest).parameters


def test_a_vector_with_a_missing_feature_is_refused_rather_than_substituted():
    """An unobserved condition is not an average one.

    Substituting a mean for a missing dimension would turn "we have no `days_below_p20` that day"
    into "that day was typical", which is a fabricated measurement below every gate in this
    project.
    """
    scale = similarity.scale_from([(1.0, 1.0), (2.0, 2.0)])

    assert similarity.is_comparable((1.0, None)) is False
    assert similarity.is_comparable((1.0, 2.0)) is True

    with pytest.raises(ValueError, match="None"):
        similarity.z_score((1.0, None), scale)


def test_an_empty_population_refuses_rather_than_defaulting():
    """A site with no observations has no scale, and no distance may be computed against a default."""
    with pytest.raises(ValueError, match="empty population"):
        similarity.scale_from([])
