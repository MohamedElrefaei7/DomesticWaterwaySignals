"""The onset/recovery split, DEFINED FROM THE PREDICTOR AND NEVER FROM THE TARGET.

PURE FUNCTIONS. Nothing here opens a connection, and NOTHING HERE ACCEPTS THE TARGET SERIES - that
second property is the module's reason for existing and it is enforced by the signature rather than
by a comment.

WHY THE SPLIT EXISTS AT ALL
----------------------------
Phase 5 measured `days_below_p10` at Memphis against the Cairo-Memphis nearby rate:

    0, held for eleven weeks      rate drifting   335 -> 656
    2 -> 9 -> 16 -> 23            rate climbing   925 -> 1,428 -> 2,427 -> 2,812
    30 -> 37 -> 44 -> 51 -> 58    rate FALLING back from the 2,812 peak

The rate peaked at 23 days below and then fell through 30, 37, 44, 51 and 58. A SINGLE CORRELATION
ACROSS THAT WHOLE EVENT AVERAGES A STRONG POSITIVE AGAINST A STRONG NEGATIVE AND REPORTS
APPROXIMATELY NOTHING - which would be read as "no relationship", and would be the most expensive
wrong answer available in this phase. Part of why the level relationship looked weak in Phase 5 is
very likely this.

WHY THE SPLIT IS DEFINED FROM THE FEATURE, AND WHY THAT IS NOT A DETAIL
------------------------------------------------------------------------
The tempting version is to split on the target: call the weeks where the rate rose "onset" and the
weeks where it fell "recovery", then report the correlation within each. IT WOULD PRODUCE
BEAUTIFUL NUMBERS AND THEY WOULD MEAN NOTHING. Conditioning on the outcome and then measuring
association with the outcome recovers the conditioning, not a relationship - you would find a
strong positive correlation in the subset selected for going up, on any predictor whatsoever,
including a column of noise.

It is seductive because it does not look like cheating from the inside. Every step is defensible in
isolation: "the market behaves differently when rates are rising" is true; "so measure the two
regimes separately" is reasonable; and the resulting split is described in the write-up as a regime
analysis. The circularity is invisible in the output and lives entirely in how the subsets were
chosen.

So `classify` TAKES ONE ARGUMENT - the feature's own dated values - and there is no parameter it
could be handed a target through. A test asserts that by reading the signature, which is the only
form of this guard that cannot be worn away: a comment saying "do not pass the target" is advice,
and a parameter that does not exist is a compile-time fact.

THE DIRECTION IS THE FEATURE'S OWN, DAY OVER DAY
-------------------------------------------------
    rising    -> onset       the constraint is tightening
    falling   -> recovery    the constraint is easing, or the counter has reset
    flat      -> neither     no direction was observed, so no regime is claimed
    unknown   -> neither     a NULL value, or a break in the daily series

FLAT IS NEITHER, NOT "WHATEVER IT WAS LAST TIME". A run-length feature sits at 0 for months at a
time (eleven weeks in the measurement above), and carrying the previous direction through that
would assign hundreds of quiet days to whichever regime happened to precede them - which is the
single largest population in the series, silently assigned by an implementation detail.

A BREAK IN THE DAILY SERIES ENDS THE COMPARISON, for the reason thresholds.py gives about run
lengths: Memphis has a twenty-year hole in its daily record (`gauge_known_gaps`), and "the feature
rose" across it would be comparing 1994 to 2014 and calling it a direction.
"""

from __future__ import annotations

from datetime import date, timedelta

# The three regimes. `all` is the UNSPLIT series and is scanned alongside the other two rather than
# instead of them: it is what a single correlation over everything would have produced, so keeping
# it in the table is what lets a reader see the averaging effect described above rather than being
# told about it.
ONSET = "onset"
RECOVERY = "recovery"
ALL = "all"

REGIMES: tuple[str, ...] = (ONSET, RECOVERY, ALL)

# The largest step between two observations that can still be read as a direction, in days.
#
# One. `features` carries a row per day the site was observed, so a larger step is a gap in the
# record - and a gap is where knowledge ends rather than where a slow move happened. Same argument
# as thresholds.days_below, one layer up.
MAX_STEP_DAYS = 1


def classify(dated_values) -> list[tuple[date, str | None]]:
    """`(date, value)` pairs -> `(date, regime or None)`, one row per input date.

    ONE PARAMETER, AND IT IS THE FEATURE'S OWN SERIES. There is deliberately no way to pass a
    target here; see the module docstring. `tests/signals/test_regimes.py` asserts this by reading
    the signature, so adding one would go red before it could be used.

    `None` means no regime was observed - the first date, a flat day, a NULL value, or the far side
    of a break in the daily series. Those dates are still returned rather than dropped: a caller
    that wants the unsplit series needs every date, and a caller that wants a regime filters.
    """
    ordered = sorted(dated_values, key=lambda row: row[0])

    rows: list[tuple[date, str | None]] = []
    previous_day: date | None = None
    previous_value = None

    for day, value in ordered:
        regime: str | None = None

        contiguous = (
            previous_day is not None and day - previous_day <= timedelta(days=MAX_STEP_DAYS)
        )
        if contiguous and value is not None and previous_value is not None:
            if value > previous_value:
                regime = ONSET
            elif value < previous_value:
                regime = RECOVERY
            # Equal: no direction was observed, so none is claimed. See the module docstring - this
            # branch is the largest population in a run-length series and carrying the previous
            # regime through it would assign all of it by accident.

        rows.append((day, regime))
        previous_day = day
        previous_value = value

    return rows


def dates_in_regime(dated_values, regime: str) -> set[date]:
    """The dates belonging to `regime`. For `all`, every date carrying a value.

    `all` IS NOT "every date": a date with a NULL value has nothing to correlate, so including it
    would only change the denominator. It IS every date with a value regardless of direction -
    including the flat days and the first observation, which the two directional regimes exclude.
    That is what makes `all` a fair reconstruction of the single un-split correlation rather than a
    third, differently-filtered thing.
    """
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}. Known: {list(REGIMES)}")

    if regime == ALL:
        return {day for day, value in dated_values if value is not None}

    return {day for day, found in classify(dated_values) if found == regime}
