"""Deseasonalization: day-of-year climatology, and the refusal to compute one from too few years.

PURE FUNCTIONS. Nothing here opens a connection, and that is what lets every number below be
tested against a hand-computed expectation rather than against the database's own output.

THE MEDIAN, NOT THE MEAN, AND THE 2022 EVENT IS THE ARGUMENT
------------------------------------------------------------
The whole purpose of this module is to make an extreme low-water autumn stand out against a normal
one. The 2022 and 2023 events ARE IN THE HISTORY the climatology is fitted on.

With a MEAN, those two autumns pull the October baseline down - so the anomaly they produce is
measured against a baseline they themselves depressed, and the events partly erase their own
signal. The effect is largest exactly where the data matters most, and it is invisible: the
resulting series is smooth, plausible, and quietly understated.

A median is unmoved by two extreme years out of twenty. The event you are trying to detect must not
be allowed to move the baseline you detect it against, and this is the one line where that is
decided.

THE EIGHT-YEAR GUARD, AND WHY NULL BEATS A NUMBER
--------------------------------------------------
Memphis's daily record starts 2014-10-01 (migration 0011), so its early-October days have barely a
decade behind them and its late-September days have less. A climatology computed from three years
is a number with a false air of authority: it is a median of three observations, it will be wrong
by a wide margin, and NOTHING DOWNSTREAM CAN TELL - an anomaly of +40,000 cfs looks identical
whether it came from twenty years or three.

So below MIN_YEARS the anomaly is NULL. `climatology_n_years` is stored beside every row - including
the refused ones - because a NULL with no count next to it is indistinguishable from a bug, and the
first thing anyone does about an unexplained NULL is remove the check that produced it.

FEBRUARY 29 IS FOLDED INTO DAY 59, NOT GIVEN ITS OWN BUCKET
------------------------------------------------------------
A leap day gets one observation every four years. Its own bucket would therefore hold a QUARTER of
the observations of every neighbouring day - so its climatology would be noisier than the rest of
the series by construction, and its `climatology_n_years` would fail the guard for the first
thirty-two years of any record while February 28 and March 1 passed it comfortably. A single day
that behaves differently from its neighbours for a calendar reason is the kind of artefact that
gets discovered as a modelling result.

Folding also keeps every date in a leap year AFTER February aligned with the same calendar date in
a common year - March 1 is day 60 in both - which is the property the whole day-of-year comparison
depends on.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from statistics import median

# The day-of-year of February 29 in a leap year, and the day it folds onto (February 28).
LEAP_DAY = 60
FOLDED_LEAP_DAY = 59

# Days in the folded year. Every date maps into 1..365; there is no 366.
DAYS_IN_YEAR = 365

# THE GUARD. Below this many distinct years behind a day-of-year, the anomaly is NULL.
#
# Eight rather than five or ten: five leaves a median of five observations deciding what "normal
# October" means, which the 2022/2023 pair alone could shift; ten would refuse Memphis's entire
# useful early record, since its daily series starts 2014-10-01 and the interesting events are 2022
# and 2023. Eight admits Memphis's events with a baseline that has seen a full range of autumns.
MIN_YEARS = 8

# The centred smoothing window, in days. Odd so the window is symmetric about its own day.
#
# Fifteen days is about half a month: long enough to absorb the noise of a single day-of-year whose
# handful of observations happened to fall in wet years, short enough not to smear the seasonal
# shape it is supposed to describe. Rivers move on a scale of weeks.
SMOOTHING_DAYS = 15


def day_of_year(day: date) -> int:
    """1..365, with February 29 folded onto day 59 and later leap-year days shifted back.

    NOT `day.timetuple().tm_yday`. In a leap year that returns 61 for March 1 and 60 in a common
    year, so the naive version compares March 1 against February 28's climatology in three years out
    of four - a one-day seasonal shift that is far too small to notice and perfectly systematic.
    """
    doy = day.timetuple().tm_yday
    if calendar.isleap(day.year) and doy >= LEAP_DAY:
        # February 29 (60) folds onto 59; everything after it shifts back one to realign with the
        # common-year calendar.
        return FOLDED_LEAP_DAY if doy == LEAP_DAY else doy - 1
    return doy


@dataclass(frozen=True)
class DayClimatology:
    """One day-of-year's baseline, and the evidence behind it.

    `value` is None when `n_years` is below the guard. The two travel together on purpose: a
    caller cannot obtain the baseline without also obtaining what backs it, so there is no code
    path where the guard is skipped by reading a different field.
    """

    value: float | None
    n_years: int


def _window_days(doy: int, span: int) -> list[int]:
    """The `span`-day centred window around `doy`, wrapping across the year boundary.

    Wrapping matters: without it, late-December and early-January climatologies would be computed
    from half a window each, so the seasonal curve would develop a discontinuity at New Year that
    is an artefact of arithmetic rather than of the river.
    """
    half = span // 2
    return [((doy - 1 + offset) % DAYS_IN_YEAR) + 1 for offset in range(-half, half + 1)]


def climatology(
    observations,
    *,
    min_years: int = MIN_YEARS,
    smoothing_days: int = SMOOTHING_DAYS,
) -> dict[int, DayClimatology]:
    """Day-of-year baselines from `(date, value)` pairs. MEDIAN across years, then smoothed.

    Two steps, in this order, and the order is what the mean/median decision applies to:

      1. For each day-of-year, the MEDIAN of every value ever observed on it. This is where the
         extreme years are refused entry, and it is the step the mutation table targets.
      2. Those per-day medians are averaged over a centred window. Smoothing a set of medians is
         not the same as taking a mean of the raw values - the extreme observations have already
         been excluded by step 1 and cannot re-enter here.

    `n_years` counts DISTINCT CALENDAR YEARS CONTRIBUTING TO THE WINDOW, not to the day alone,
    because the window is what actually backs the smoothed number. Counting the single day would
    understate the evidence by a factor of about fifteen and refuse almost everything.
    """
    values_by_doy: dict[int, list[float]] = {}
    years_by_doy: dict[int, set[int]] = {}
    for day, value in observations:
        if value is None:
            # A missing value contributes neither a number nor a year. Counting the year would let
            # a run of NULLs satisfy the guard with no observations behind it.
            continue
        doy = day_of_year(day)
        values_by_doy.setdefault(doy, []).append(value)
        years_by_doy.setdefault(doy, set()).add(day.year)

    if not values_by_doy:
        return {}

    per_day_median = {doy: median(values) for doy, values in values_by_doy.items()}

    result: dict[int, DayClimatology] = {}
    for doy in range(1, DAYS_IN_YEAR + 1):
        window = _window_days(doy, smoothing_days)
        present = [per_day_median[w] for w in window if w in per_day_median]
        if not present:
            result[doy] = DayClimatology(value=None, n_years=0)
            continue

        years: set[int] = set()
        for w in window:
            years |= years_by_doy.get(w, set())

        smoothed = sum(present) / len(present)
        # THE GUARD, APPLIED ONCE, HERE. Below it the value is withheld rather than the row being
        # dropped - the caller still learns how many years there were, which is the whole point of
        # storing the count.
        result[doy] = DayClimatology(
            value=smoothed if len(years) >= min_years else None,
            n_years=len(years),
        )
    return result


def build_anomalies(observations, *, min_years: int = MIN_YEARS) -> list[tuple]:
    """`(date, value)` pairs -> `(date, value, anomaly, climatology_n_years)` rows.

    THE BUILDER SIGNATURE THE REGISTRY CALLS. Plain tuples rather than a dataclass so that
    registry.py can import this module without this module importing registry.py back.

    An anomaly is `value - climatology`, and it is None wherever the climatology is - which is the
    guard - or wherever the value itself is. The year count is returned EITHER WAY.
    """
    climo = climatology(observations, min_years=min_years)

    rows = []
    for day, value in observations:
        entry = climo.get(day_of_year(day))
        n_years = entry.n_years if entry is not None else 0
        if value is None or entry is None or entry.value is None:
            rows.append((day, value, None, n_years))
        else:
            rows.append((day, value, value - entry.value, n_years))
    return rows
