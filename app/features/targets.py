"""Targets: forward log-returns of the rate the output contract names.

PURE FUNCTIONS. Nothing here opens a connection.

LOG-RETURN, NOT PERCENT CHANGE
-------------------------------
The Cairo-Memphis nearby rate went 388 -> 2,812.5 in ten weeks in 2022. As a percent change that is
+625%; the move that undid it is -86%. THE SAME MOVE IN OPPOSITE DIRECTIONS, WITH MAGNITUDES THAT
DIFFER BY A FACTOR OF SEVEN.

Anything fitted on percent changes therefore learns that asymmetry as though it were a fact about
barge freight rather than a fact about division. Every mean, threshold, and "typical move" inherits
it, and on a series that really does move 7x it is not a rounding concern.

`ln(p1/p0)` is symmetric - a doubling is +0.6931, a halving is -0.6931 - and additive across
periods, so the three 7-day returns spanning a month sum to the 21-day one. Both properties are
asserted by tests directly rather than described here and trusted.

A MISSING FORWARD RATE IS NULL AND IS NEVER CARRIED FORWARD
-----------------------------------------------------------
USDA publishes no rate for a closed river - 774 of 8,260 nearby records, mostly December-March
(migration 0017). Filling one of those weeks with the previous week's rate would produce a return of
EXACTLY ZERO, and zero is the most ordinary value this column can hold: it means the price did not
move. So the fabrication is invisible, it lands preferentially in winter, and any seasonal
comparison built on it would find winter unusually calm.

The same applies at the end of the series for a different reason: the last `horizon_days` of any
series have no forward observation yet, and that is correct rather than a gap. ANY COUNT OF VALID
TARGETS MUST BE COMPARED AGAINST THE NUMBER OF WEEKS WITH BOTH ENDPOINTS PUBLISHED, never against
the row count, or the newest weeks read as broken forever.
"""

from __future__ import annotations

import math
from datetime import timedelta

# THE TARGET SERIES. Cairo-Memphis is the segment CLAUDE.md § 7's output contract names, and
# `nearby` is the spot rate rather than a forward quote - the thing that actually moved 7.2x.
TARGET_LOCATION = "Cairo-Memphis"
TARGET_HORIZON = "nearby"
TARGET_NAME = "cairo_memphis_nearby_log_return"

# 7, 14 and 21 DAYS. The rate series is weekly, so these are one, two and three published weeks
# ahead - and they are the horizons the output contract's example claim is phrased over ("within 3
# weeks"). Days rather than weeks because days are what the date arithmetic uses; storing weeks
# would invite a multiply-by-seven in a query and it only has to be wrong once.
HORIZON_DAYS: tuple[int, ...] = (7, 14, 21)

# Distinguishes "that week is not in the series at all" from "that week is present with no
# published rate". Both produce a NULL target, but they are different facts and collapsing them in
# the code is how the second one later gets "helpfully" filled in.
_ABSENT = object()


def forward_log_return(rate_now, rate_forward) -> float | None:
    """`ln(forward / now)`, or None when either endpoint is unusable.

    A non-positive rate is refused rather than passed to `log`. `pct_of_tariff` carries a CHECK
    requiring it to be positive (migration 0017), so a zero or negative here means something
    upstream changed - and `math.log(0)` raises a ValueError that would surface as a crash in the
    build rather than as the data problem it is.
    """
    if rate_now is None or rate_forward is None:
        return None
    now = float(rate_now)
    forward = float(rate_forward)
    if now <= 0 or forward <= 0:
        return None
    return math.log(forward / now)


def forward_log_returns(weekly_rates, horizon_days: int) -> list[tuple]:
    """`(week_ending, rate)` pairs -> `(week_ending, value)` rows, one per input week.

    ONE ROW PER INPUT WEEK, INCLUDING THE ONES WITH NO TARGET. A NULL row states "this week has no
    forward observation"; a missing row leaves the series simply ending early, which nothing can
    distinguish from a build that stopped short (the same argument CLAUDE.md § 16 makes about
    ingest, one layer up).

    THE FORWARD WEEK IS LOOKED UP BY EXACT DATE, never by "the next row" or "the nearest row". The
    rate series has gaps, and `rates[i + 1]` would silently reach across one - so a week whose true
    +7-day observation is missing would take its +14-day one and be recorded under horizon 7. That
    is a wrong number in the right-looking column, which nothing downstream could detect.
    """
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be positive, got {horizon_days}")

    by_week = {week: rate for week, rate in weekly_rates}

    rows = []
    for week in sorted(by_week):
        forward = by_week.get(week + timedelta(days=horizon_days), _ABSENT)
        if forward is _ABSENT:
            rows.append((week, None))
            continue
        rows.append((week, forward_log_return(by_week[week], forward)))
    return rows


def build_targets(weekly_rates, horizons: tuple[int, ...] = HORIZON_DAYS) -> list[tuple]:
    """All horizons at once -> `(week_ending, target_name, horizon_days, value)` rows."""
    rows = []
    for horizon in horizons:
        for week, value in forward_log_returns(weekly_rates, horizon):
            rows.append((week, TARGET_NAME, horizon, value))
    return rows


def resolvable_week_count(weekly_rates, horizon_days: int) -> int:
    """How many weeks have BOTH endpoints published. The denominator any coverage check needs.

    Exists so that no caller writes `count(*) FILTER (WHERE value IS NOT NULL) = count(*)`, which
    is the check that looks right and reports the newest `horizon_days` of the series as broken on
    every single run, forever.
    """
    by_week = {week: rate for week, rate in weekly_rates}
    return sum(
        1
        for week, rate in by_week.items()
        if rate is not None and by_week.get(week + timedelta(days=horizon_days)) is not None
    )
