"""The feature build: bounded-window upsert, and the join that must not look ahead.

A SCHEDULED JOB AND A CLI, which is different from every backfill in this project (those are CLIs
only, because they run for hours). This one recomputes a trailing window daily and finishes in
seconds, so it has a cadence entry - `features_build` - and `--from-scratch` for the rare full
rebuild a human runs deliberately.

BOUNDED WINDOW, UPSERT, NEVER TRUNCATE-AND-REBUILD
---------------------------------------------------
`gauge_daily`, `features` and `targets` are the first tables here whose contents are DERIVED. That
makes a full rebuild feel safe - it can always be recomputed - and it is exactly why the destructive
path is not built. A truncate-and-rebuild is one bug away from emptying all three: the truncate
always succeeds, and if the rebuild raises halfway the tables are shorter than they were and
nothing upstream holds a second copy. Recomputing a window means a defect corrupts a window.

There is no DELETE and no TRUNCATE anywhere in this module, and a test asserts that by reading the
source - because "we do not delete" is a claim that decays quietly.

--from-scratch REQUIRES AN EXPLICIT START DATE
-----------------------------------------------
Not a default of "the beginning of the data". A flag that silently means "everything" is the flag
somebody types while debugging one site, and it is indistinguishable in a shell history from the
bounded run they meant. Requiring the date makes the scope of a full rebuild something the operator
stated rather than inherited. It STILL UPSERTS - `--from-scratch` widens the window, it does not
change the write mode.

THE FEATURE-TO-TARGET JOIN IS A LEAKAGE GUARD, NOT A CONVENIENCE
-----------------------------------------------------------------
Rates are published weekly on a Thursday-ending label; features are daily. So every target week has
to be matched to a feature date, and the obvious implementation - nearest date - IS LOOKAHEAD.

A feature dated the Saturday AFTER a Thursday week-ending is nearer to it than the Wednesday
before. Nearest-date matching would therefore let two days of river conditions that had not
happened yet inform that week's target. It is a leak of one or two days, it appears in no schema, it
makes the relationship look slightly better than it is, and IT IS EXACTLY THE KIND THAT SURVIVES
REVIEW - nobody reads `ORDER BY abs(date - week_ending)` as a modelling error.

`last_on_or_before` takes the most recent feature date <= the week ending. Never nearest.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.features import registry, rollup, targets as targets_module
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "features_build"

# THE DEFAULT TRAILING WINDOW, IN DAYS.
#
# 400 rather than 90 or 365, and the number comes from the longest thing being recomputed. A
# `days_below_p20` run can be hundreds of days long on a low year, and a window that started
# mid-run would recompute that run from its own left edge - producing a shorter count than the true
# one, which reads as a milder event. 400 days covers a full year plus a generous margin for a run
# that spans one, so the recomputed values agree with the originals everywhere they overlap.
#
# It is also what makes idempotence testable: rerunning over the same window must change nothing,
# and that only holds if the window is long enough to reconstruct its own left edge.
DEFAULT_WINDOW_DAYS = 400

FEATURES_UPSERT_SQL = """
INSERT INTO features (date, site_id, feature_name, value, anomaly, climatology_n_years)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (date, site_id, feature_name) DO UPDATE
    SET value               = EXCLUDED.value,
        anomaly             = EXCLUDED.anomaly,
        climatology_n_years = EXCLUDED.climatology_n_years
    WHERE (features.value, features.anomaly, features.climatology_n_years)
       IS DISTINCT FROM
          (EXCLUDED.value, EXCLUDED.anomaly, EXCLUDED.climatology_n_years)
RETURNING 1
"""

TARGETS_UPSERT_SQL = """
INSERT INTO targets (week_ending, target_name, horizon_days, value)
VALUES (%s, %s, %s, %s)
ON CONFLICT (week_ending, target_name, horizon_days) DO UPDATE
    SET value = EXCLUDED.value
    WHERE targets.value IS DISTINCT FROM EXCLUDED.value
RETURNING 1
"""

SITES_SQL = "SELECT usgs_site_id FROM gauges ORDER BY usgs_site_id"

RATES_SQL = """
SELECT week_ending, pct_of_tariff
  FROM barge_rates
 WHERE location = %(location)s AND horizon = %(horizon)s
 ORDER BY week_ending
"""


# ---------------------------------------------------------------------------------------------
# The join. A pure function, so the leakage guard is testable without a database.
# ---------------------------------------------------------------------------------------------


def last_on_or_before(feature_dates, week_ending: date) -> date | None:
    """The most recent feature date <= `week_ending`, or None if there is none.

    NOT `min(dates, key=lambda d: abs(d - week_ending))`. That is the nearest-date version, it is
    shorter, it reads as obviously correct, and it admits lookahead: a feature dated two days AFTER
    a Thursday week-ending is nearer to it than one dated three days before, so the target for that
    week would be informed by river conditions that had not happened when the rate was published.

    Returning None rather than the earliest available date is deliberate. A week that precedes
    every feature has no feature, and reaching forward to the first one would reintroduce exactly
    the leak this function exists to prevent, at the one place it would be largest.
    """
    eligible = [day for day in feature_dates if day <= week_ending]
    return max(eligible) if eligible else None


def align_features_to_weeks(feature_dates, week_endings) -> dict:
    """`{week_ending: feature_date or None}` for every requested week. Leakage-safe by construction.

    Every week appears in the result, including the ones with no eligible feature - the caller then
    sees the unmatched weeks rather than inferring them from a shorter dictionary.
    """
    return {week: last_on_or_before(feature_dates, week) for week in week_endings}


# ---------------------------------------------------------------------------------------------
# The build.
# ---------------------------------------------------------------------------------------------


def sites(conn) -> list[str]:
    return [row[0] for row in conn.execute(SITES_SQL).fetchall()]


def build_features(conn, start: date, end: date) -> int:
    """Every registered feature at every site, over [start, end]. Returns rows actually changed.

    THE LOOP ITERATES THE REGISTRY. There is no branch on the feature's name and no name assembled
    here - `feature.builder` already has its parameters bound (registry.py), so adding a sixth
    feature is a registry entry and nothing else.

    Each builder receives the site's FULL history rather than the window: a climatology needs every
    year it can get, and a percentile threshold is a property of the whole record. Only the
    resulting rows are then restricted to the window, which is what keeps a build's output
    independent of when it ran.
    """
    written = 0
    for site_id in sites(conn):
        for feature in registry.REGISTRY:
            history = rollup.observations(
                conn, site_id, feature.param_code, feature.source_column
            )
            if not history:
                continue

            for day, value, anomaly, n_years in feature.builder(history):
                if not (start <= day <= end):
                    continue
                cursor = conn.execute(
                    FEATURES_UPSERT_SQL,
                    (day, site_id, feature.name, value, anomaly, n_years),
                )
                written += len(cursor.fetchall())
    return written


def build_targets(conn, start: date, end: date) -> int:
    """The forward log-returns, over week-endings falling in [start, end].

    The whole rate series is read regardless of the window, for the same reason the features read
    the whole history: a target at week t needs week t+21, which may fall outside the window, and
    reading only the window would produce NULLs at its right edge that are artefacts of the window
    rather than facts about the series.
    """
    weekly = [
        (row[0], row[1])
        for row in conn.execute(
            RATES_SQL,
            {
                "location": targets_module.TARGET_LOCATION,
                "horizon": targets_module.TARGET_HORIZON,
            },
        ).fetchall()
    ]
    if not weekly:
        logger.warning(
            "no %s/%s rows in barge_rates; no targets built. The rates ingest has not run.",
            targets_module.TARGET_LOCATION,
            targets_module.TARGET_HORIZON,
        )
        return 0

    written = 0
    for week, name, horizon, value in targets_module.build_targets(weekly):
        if not (start <= week <= end):
            continue
        cursor = conn.execute(TARGETS_UPSERT_SQL, (week, name, horizon, value))
        written += len(cursor.fetchall())
    return written


def build(conn, start: date, end: date) -> dict:
    """Roll up, build features, build targets. One window, one transaction, all upserts."""
    if end < start:
        raise ValueError(f"build window ends before it starts ({start} to {end})")

    result = {
        "start": start,
        "end": end,
        "gauge_daily_rows": rollup.rollup(conn, start, end),
        "feature_rows": build_features(conn, start, end),
        "target_rows": build_targets(conn, start, end),
    }
    conn.commit()

    # THE REGISTRY TRIPWIRE, RUN EVERY BUILD RATHER THAN ON REQUEST. An orphaned feature name is
    # silent by nature: the rows keep answering queries with values frozen at whatever rename
    # produced them, and a stale series is harder to notice than a missing one.
    orphans = registry.unregistered_feature_names(conn)
    result["unregistered_feature_names"] = orphans
    if orphans:
        logger.error(
            "features holds row(s) under name(s) with NO REGISTRY ENTRY: %s. Either a feature was "
            "renamed and its old rows are orphans nothing will update again, or something wrote "
            "outside the registry. Neither is a row to ignore.",
            ", ".join(orphans),
        )
    return result


def window_for(
    today: date, *, from_scratch: bool = False, start: date | None = None, days: int | None = None
) -> tuple[date, date]:
    """The [start, end] the build will recompute.

    `--from-scratch` REQUIRES `start`, and this is where that is enforced rather than in the
    argument parser alone - so the rule holds for the scheduled job and for any future caller, not
    only for someone typing at a shell.
    """
    if from_scratch:
        if start is None:
            raise ValueError(
                "--from-scratch requires --start. A full rebuild with an implicit start is "
                "indistinguishable in a shell history from the bounded run somebody meant, and "
                "'everything' is not a scope an operator should inherit by default. State the "
                "date. (The rebuild still UPSERTS - this flag widens the window, it does not "
                "delete anything.)"
            )
        return start, today

    if start is not None:
        return start, today
    return today - timedelta(days=days or DEFAULT_WINDOW_DAYS), today


@job(JOB_NAME)
def features_build_job(url: str | None = None, today: date | None = None) -> int:
    """The scheduled unit. Returns rows written across all three tables.

    IT DOES NOT TRIGGER INGEST, and nothing here waits for it. The build reads what ingest has
    landed; if a source is stale the features are stale, and the freshness registry is what says so
    (`features`, 48 hours). Adding ordering logic between jobs would be building a DAG runner
    inside APScheduler, which is Phase 5's version of the streaming daemon CLAUDE.md § 6 refuses.
    """
    today = datetime.now(timezone.utc).date() if today is None else today
    start, end = window_for(today)
    with db.connection(url) as conn:
        result = build(conn, start, end)
    return result["gauge_daily_rows"] + result["feature_rows"] + result["target_rows"]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute gauge_daily, features and targets over a bounded window. Always upserts; "
            "never deletes."
        )
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="rebuild the whole series. REQUIRES --start; still upserts rather than deleting.",
    )
    parser.add_argument("--start", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"trailing window when --start is absent (default {DEFAULT_WINDOW_DAYS})",
    )
    args = parser.parse_args(argv)

    if args.from_scratch and args.start is None:
        parser.error(
            "--from-scratch requires --start. A full rebuild's scope is stated, never inherited."
        )
    return args


def main(argv=None) -> int:  # pragma: no cover - the live-verification path
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    import os

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    today = datetime.now(timezone.utc).date()
    start, end = window_for(
        today, from_scratch=args.from_scratch, start=args.start, days=args.days
    )

    started = datetime.now(timezone.utc)
    with db.connection() as conn:
        result = build(conn, start, end)
    elapsed = datetime.now(timezone.utc) - started

    print(
        f"\n  {start} to {end}: "
        f"{result['gauge_daily_rows']} gauge_daily row(s), "
        f"{result['feature_rows']} feature row(s), "
        f"{result['target_rows']} target row(s) written in {elapsed}"
    )

    if result["unregistered_feature_names"]:
        print(
            f"\n*** features holds row(s) under name(s) with NO REGISTRY ENTRY: "
            f"{', '.join(result['unregistered_feature_names'])}\n"
            f"Either a feature was renamed and left orphans, or something wrote outside the "
            f"registry (app/features/registry.py). Neither is a row to ignore."
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
