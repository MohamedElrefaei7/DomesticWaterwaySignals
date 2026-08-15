"""The onset/recovery split, and the circularity guard that is enforced by a function signature.

Phase 5 measured the rate peaking at 23 days below p10 and then FALLING through 30, 37, 44, 51 and
58 days below. A single correlation across that event averages a strong positive against a strong
negative and reports approximately nothing - which reads as "no relationship" and would be the most
expensive wrong answer available in this phase. That is what the split is for.

WHAT IT MUST NOT BECOME is a split on the target. Calling the weeks where the rate rose "onset",
the weeks where it fell "recovery", and reporting the correlation within each would produce
beautiful numbers on a column of pure noise, because conditioning on the outcome and then measuring
association with the outcome recovers the conditioning. Test 10 is the guard, and it is asserted
against the SIGNATURE rather than against behaviour - a comment saying "do not pass the target" is
advice, and a parameter that does not exist is a fact.
"""

import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.signals import regimes


def series(start: date, values) -> list[tuple]:
    """`(date, value)` on consecutive days."""
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_onset_and_recovery_are_defined_from_the_feature_series():
    """Test 9. Rising is onset, falling is recovery, and the direction is the FEATURE's own.

    The shape below is the Phase 5 measurement: a run-length counter climbing from 0, then resetting
    when the river comes back up. The rate did something quite different across the same window -
    it peaked partway up the climb - and NONE OF THAT ENTERS HERE.
    """
    counter = series(date(2022, 8, 1), [0, 0, 2, 9, 16, 23, 30, 0, 0])
    classified = dict(regimes.classify(counter))

    assert classified[date(2022, 8, 1)] is None, "the first observation has no direction to read"
    assert classified[date(2022, 8, 2)] is None, "0 -> 0 is flat, so no regime is claimed"
    assert classified[date(2022, 8, 3)] == regimes.ONSET, "0 -> 2 is the counter rising"
    assert classified[date(2022, 8, 6)] == regimes.ONSET, "9 -> 16 is still rising"
    assert classified[date(2022, 8, 7)] == regimes.ONSET, "16 -> 23 is still rising"
    assert classified[date(2022, 8, 8)] == regimes.RECOVERY, "30 -> 0 is the counter resetting"

    onset = regimes.dates_in_regime(counter, regimes.ONSET)
    recovery = regimes.dates_in_regime(counter, regimes.RECOVERY)
    every = regimes.dates_in_regime(counter, regimes.ALL)

    assert onset and recovery
    assert not (onset & recovery), "a date cannot be in both directional regimes"
    assert (onset | recovery) < every, (
        "onset and recovery together must be a strict subset of `all` - the flat days and the "
        "first observation belong to neither, and `all` is what an unsplit correlation would use"
    )
    assert len(every) == len(counter)

    # A DECREASING SERIES IS RECOVERY THROUGHOUT, including where the values are still large. The
    # regime is the DIRECTION, not the level - Phase 5's finding is precisely that the rate fell
    # while the counter was at its highest, so a level-based split would put those days in the
    # wrong bucket.
    falling = series(date(2023, 1, 1), [58, 51, 44, 37, 30])
    assert {r for _d, r in regimes.classify(falling)[1:]} == {regimes.RECOVERY}

    # A break in the daily series ends the comparison rather than spanning it. Memphis has a
    # twenty-year hole in its record (gauge_known_gaps); "the feature rose" across it would be
    # comparing 1994 to 2014 and calling it a direction.
    across_a_gap = [(date(1994, 9, 30), 5.0), (date(2014, 9, 30), 90.0)]
    assert regimes.classify(across_a_gap)[1][1] is None

    # A NULL value is unknown, not flat, and it makes the day after it unknown too.
    with_a_null = series(date(2022, 8, 1), [1.0, None, 5.0])
    assert [r for _d, r in regimes.classify(with_a_null)] == [None, None, None]


def test_regime_is_never_derived_from_the_target():
    """Test 10. THE CIRCULARITY GUARD, ENFORCED BY SIGNATURE.

    A behavioural test cannot catch this. Splitting on the target produces a well-formed regime
    assignment that passes every assertion in test 9 - the dates would still be partitioned, the
    sets would still be disjoint, the correlations within each split would still compute. It would
    simply be measuring the conditioning instead of a relationship, and the output would look
    better rather than worse.

    So the assertion is that THERE IS NO WAY IN. `classify` takes one argument, it is the feature's
    own series, and this module cannot even see the targets module.
    """
    parameters = list(inspect.signature(regimes.classify).parameters)
    assert parameters == ["dated_values"], (
        f"regimes.classify takes {parameters}. It must take exactly one argument - the feature's "
        f"own dated values - so that a target series cannot be passed to it at all. A second "
        f"parameter is the whole failure: splitting on the outcome and reporting association "
        f"within each split is circular, and it produces a strong result on any predictor "
        f"whatsoever, including noise."
    )

    forbidden = ("target", "rate", "return", "outcome", "label", "y")
    for name in inspect.signature(regimes.classify).parameters:
        assert not any(word in name.lower() for word in forbidden), (
            f"regimes.classify has a parameter named {name!r}, which reads as the thing being "
            f"predicted"
        )

    # `dates_in_regime` is the other public entry point, and it must be equally closed.
    assert list(inspect.signature(regimes.dates_in_regime).parameters) == [
        "dated_values",
        "regime",
    ]

    # AND THE MODULE CANNOT REACH THE TARGETS AT ALL. A parameter is one way in; an import is the
    # other, and it would not change any signature.
    source = Path(regimes.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for forbidden_import in ("app.features.targets", "import targets", "from app.features"):
        assert forbidden_import not in code, (
            f"app/signals/regimes.py references {forbidden_import!r}. The regime split must be "
            f"computable from the feature series alone."
        )
    assert "targets" not in code.replace("dated_values", ""), (
        "app/signals/regimes.py mentions targets in executable code"
    )


def test_a_flat_feature_series_yields_no_onset_or_recovery_windows():
    """Test 11. Flat is NEITHER regime, not "whichever direction came last".

    This is the largest population in a run-length series and it is easy to give away by accident.
    `days_below_p10` at Memphis held 0 FOR ELEVEN CONSECUTIVE WEEKS in the Phase 5 measurement,
    while the rate drifted 335 -> 656. Carrying the previous direction through a stretch like that
    assigns hundreds of quiet days to a regime by an implementation detail - and because they are
    the majority of the series, they would dominate whichever regime received them.
    """
    flat = series(date(2022, 6, 1), [0.0] * 20)

    assert regimes.dates_in_regime(flat, regimes.ONSET) == set(), (
        "a series that never moved produced onset windows"
    )
    assert regimes.dates_in_regime(flat, regimes.RECOVERY) == set(), (
        "a series that never moved produced recovery windows"
    )

    # `all` still holds every day: an unsplit correlation over a constant series is refused by
    # statistics.pearson for having no variance, which is a different and correct refusal.
    assert len(regimes.dates_in_regime(flat, regimes.ALL)) == 20

    # AND A FLAT STRETCH DOES NOT INHERIT THE DIRECTION THAT PRECEDED IT. This is the mutation
    # shape: a classifier that carried `regime` forward across equal values would put all eight
    # flat days below into `onset`.
    rise_then_flat = series(date(2022, 6, 1), [0, 5, 5, 5, 5, 5, 5, 5, 5, 5])
    classified = regimes.classify(rise_then_flat)
    assert classified[1][1] == regimes.ONSET
    assert all(regime is None for _day, regime in classified[2:]), (
        f"a flat stretch inherited the direction before it: {classified}"
    )

    assert regimes.classify([]) == []
    with pytest.raises(ValueError, match="unknown regime"):
        regimes.dates_in_regime(flat, "sideways")

    # The three regimes are exactly what migration 0023's CHECK permits. Two copies of a closed set
    # is the risk the CHECK accepts, so this is where they are pinned together.
    assert set(regimes.REGIMES) == {"onset", "recovery", "all"}
