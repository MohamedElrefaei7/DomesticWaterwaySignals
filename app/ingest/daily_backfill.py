"""The daily-values backfill: 35 years of discharge where the record allows it.

CLAUDE.md § 15. A CLI a human runs, never a scheduled job - same reasoning as the instantaneous
backfill, and the same shape, so the two read alike.

WHAT THIS ONE DOES DIFFERENTLY, AND WHY
---------------------------------------
It walks CALENDAR DATES, not instants. Nothing in this module constructs a datetime, and that is
deliberate rather than incidental: the moment a datetime exists in the daily path, something
later calls `.astimezone()` on it and the published date shifts by a day for half the world
(see usgs_daily_client.parse_date).

An out-of-record window ABORTS rather than advancing. This is the reverse of the instantaneous
backfill's tolerance for empty windows, and the two are not in conflict - they are the three
outcomes of § 15 applied in order:

    non-JSON body           -> the window is entirely outside the record.  ABORT, name the seed.
    200, series missing     -> the site stopped serving the parameter.     ABORT, name the site.
    200, series, no values  -> an ordinary gap inside the record.          ADVANCE.

The first is what a `dv_record_start` floor set too early produces on the very first window, and
it must be loud: a backfill that treated it as an empty window would walk the entire configured
range collecting nothing, logging steady progress, and finish reporting success over an empty
table. That is CLAUDE.md § 2's theme 1 with a progress bar.

EXPECTED EMPTY WINDOWS AND UNEXPLAINED ONES ARE REPORTED DIFFERENTLY, AND NEITHER IS FATAL
------------------------------------------------------------------------------------------
Two ranges in this corridor return nothing however they are requested: twenty years at Memphis
and most of 2023 at Baton Rouge, both measured 2026-08-14 and seeded as rows in
`gauge_known_gaps` (migration 0012). Walking them produces dozens of empty windows that are
entirely expected, and a run in which every empty window looks the same is a run where the one
empty window that means something is a line in the middle of forty identical ones.

So an empty window inside a known gap logs at INFO, and one that is not logs at WARNING. THE
CLASSIFICATION CHANGES NOTHING ELSE. Both advance, both are counted, neither raises - an empty
window has never been fatal here and must not become fatal now (CLAUDE.md § 14). The fatal case
is a missing SERIES, it is unchanged, and it lives in the client.

The gaps are NOT used to decide what to request. Every window inside a known gap is still asked
for, and tests/ingest/test_known_gaps.py asserts it: skipping ahead would let a human-maintained
table decide what never to ask for, where a wrong end date silently skips real data and leaves no
request, no empty response, and no evidence behind. Asking and receiving nothing is cheap and
self-correcting.

IT NEVER WRITES TO `gauges`
---------------------------
The backfill reports the first date that actually returned data per site, and stops. It does not
update `dv_record_start`, and tests/ingest/test_daily_backfill.py asserts the `gauges` table is
untouched by a full run.

A backfill that silently corrected its own starting assumption could never be caught having
started from the wrong place - the evidence would be overwritten by the run that produced it.
The floors are a human's claim about the data (CLAUDE.md § 1); reconciling them is live
verification step 6, in a new numbered migration.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.orchestration import session
from app.ingest import gauges as gauges_module
from app.ingest import usgs_daily_ingest
from app.ingest.usgs_client import MissingSeriesError
from app.ingest.usgs_daily_client import (
    STAT_MEAN,
    OutsidePeriodOfRecordError,
    UsgsDailyClient,
)

logger = logging.getLogger(__name__)

# Five years per request. A daily series is one value per day, so this is ~1,825 values - a
# comfortable response, and eight requests for a 35-year site rather than one enormous one.
DEFAULT_WINDOW_DAYS = 1825


@dataclass(frozen=True)
class DateWindow:
    """An INCLUSIVE calendar-date window, [start, end].

    Inclusive on both ends because that is what the daily service takes, and because half-open
    arithmetic on dates invites an off-by-one that silently drops one day per window - 6 days
    across a 35-year backfill, invisible in any row count anyone would check.

    Consecutive windows therefore meet at `end + 1 day`, not at a shared boundary.
    """

    start: date
    end: date


def windows(start: date, end: date, window_days: int = DEFAULT_WINDOW_DAYS) -> list[DateWindow]:
    """Tile [start, end] into consecutive inclusive windows. No gaps, no overlap."""
    if window_days < 1:
        raise ValueError(f"window_days must be at least 1, got {window_days}")
    if end < start:
        return []

    tiles: list[DateWindow] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=window_days - 1), end)
        tiles.append(DateWindow(cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tiles


@dataclass(frozen=True)
class ResumePoint:
    """Where this site's walk starts, why, and WHETHER IT STARTED FROM THE SEED.

    `from_seed` exists because of a real misreading on the instance, 2026-08-14: a resumed run
    reported `FIRST DATA 2020-01-01` for Memphis against a correct seed of 2014-10-01, under a log
    line saying that value is what the seed gets reconciled against. It is not - it is the first
    date of THAT RUN, and the run began at MAX(date) in a table that already held the record back
    to 2014. Acting on the line as written would mean "correcting" a seed that was right.

    Only a walk that actually began at the floor produces a first-data date the seed can be
    reconciled against, and only such a walk should invite it.
    """

    start: date
    why: str
    from_seed: bool


def resume_point(conn, gauge) -> ResumePoint:
    """Where this site's daily backfill starts, and why. Per site, from the data.

    MAX(date) when the site has rows, its own `dv_record_start` floor when it does not. Never a
    checkpoint, never a global start.
    """
    newest = usgs_daily_ingest.latest_date(conn, gauge.usgs_site_id)
    if newest is not None:
        return ResumePoint(
            newest,
            f"resuming from MAX(date) in gauge_readings_daily ({newest.isoformat()})",
            from_seed=False,
        )
    return ResumePoint(
        gauge.dv_record_start,
        f"no daily rows stored; starting from this site's own dv_record_start floor "
        f"({gauge.dv_record_start.isoformat()})",
        from_seed=True,
    )


@dataclass
class DailySiteResult:
    """What one site's daily backfill actually did. Reported, not inferred."""

    site_id: str
    seeded_floor: date | None = None
    windows_requested: int = 0
    empty_windows: int = 0

    # Of those, the ones NO known gap accounts for. Reported separately rather than as a
    # proportion: "40 empty" reads as fine at a site with a twenty-year hole and as an outage at
    # one without, and the summary should not need the reader to remember which is which.
    unexplained_empty_windows: int = 0
    readings_received: int = 0
    rows_written: int = 0
    first_data_date: date | None = None

    # Whether this run's walk began at the seeded floor. False when it resumed from stored data,
    # and then `first_data_date` is the first date THIS RUN saw - not the start of the record.
    walked_from_seed: bool = False

    def describe(self) -> str:
        first = (
            self.first_data_date.isoformat()
            if self.first_data_date
            else "NEVER - no window returned any data"
        )
        floor = self.seeded_floor.isoformat() if self.seeded_floor else "(none)"

        # TWO DIFFERENT FACTS, AND THE WORDING SAYS WHICH ONE THIS IS.
        #
        # A resumed run's earliest date is a property of where it started, not of the record, and
        # reporting it under "reconcile the seed against this" is how a correct seed gets
        # "corrected". Observed on the instance 2026-08-14: Memphis reported 2020-01-01 against a
        # correct floor of 2014-10-01.
        if self.walked_from_seed:
            provenance = (
                f"FIRST DATA IN THE RECORD {first} (walked from the seeded dv_record_start floor "
                f"{floor} - THIS is what the seed reconciles against)"
            )
        else:
            provenance = (
                f"first date in THIS RUN {first} (resumed from stored data, so this is NOT the "
                f"start of the record and the seed - {floor} - is not reconciled against it)"
            )

        return (
            f"{self.site_id}: {self.windows_requested} window(s), "
            f"{self.empty_windows} empty "
            f"({self.unexplained_empty_windows} UNEXPLAINED), "
            f"{self.readings_received} value(s) received, "
            f"{self.rows_written} row(s) written. "
            f"{provenance}"
        )


def backfill_site(
    conn,
    client: UsgsDailyClient,
    gauge,
    end: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    dry_run: bool = False,
    start_override: date | None = None,
    known_gaps=(),
) -> DailySiteResult:
    """Walk one site's daily record from its resume point to `end`, writing as it goes.

    Commits per window. A 35-year backfill held in one transaction loses everything to a
    disconnect near the end and holds one snapshot open throughout; per-window commits mean an
    interrupted run resumes from where it actually got to, which is what MAX(date) reports.

    `known_gaps` only decides how an empty window is LOGGED. It defaults to none, so a caller that
    forgets to pass them gets every empty window at WARNING - noisy, and the safe direction to be
    wrong in. The opposite default would quietly reclassify a real outage as expected.
    """
    if start_override is not None:
        start = start_override
        # An overridden start is never a walk from the seed, even when the two dates coincide:
        # what makes a first-data date reconcilable is that nothing earlier was skipped, and
        # --start is the flag that skips.
        from_seed = False
        logger.warning(
            "%s: start OVERRIDDEN to %s by --start; the resume point in the data is being ignored",
            gauge.usgs_site_id,
            start_override.isoformat(),
        )
    else:
        resume = resume_point(conn, gauge)
        start, from_seed = resume.start, resume.from_seed
        logger.info("%s: %s", gauge.usgs_site_id, resume.why)

    result = DailySiteResult(
        site_id=gauge.usgs_site_id,
        seeded_floor=gauge.dv_record_start,
        walked_from_seed=from_seed,
    )

    for window in windows(start, end, window_days):
        result.windows_requested += 1

        if dry_run:
            logger.info(
                "%s: [dry-run] would request %s to %s for %s (stat %s)",
                gauge.usgs_site_id,
                window.start.isoformat(),
                window.end.isoformat(),
                sorted(gauge.available_params),
                STAT_MEAN,
            )
            continue

        try:
            readings = client.fetch_window(
                [gauge.usgs_site_id],
                gauge.available_params,
                window.start,
                window.end,
                stat_codes=(STAT_MEAN,),
            )
        except OutsidePeriodOfRecordError:
            # THE SEED IS WRONG, AND THIS IS THE ONLY MOMENT THAT SAYS SO.
            #
            # Not caught-and-advanced. A window entirely outside the period of record is the
            # ordinary response to a dv_record_start floor set earlier than the site's real
            # record - so treating it as an empty window would walk the whole configured range
            # collecting nothing, logging progress, and finish reporting success over an empty
            # table.
            logger.error(
                "%s: ABORTING at window %s to %s - the service returned a non-JSON body, which "
                "means this window is entirely outside the site's period of record. The seeded "
                "dv_record_start floor (%s) is EARLIER than this site's real daily record. Fix "
                "the SEED in a new numbered migration; do not widen the backfill's tolerance.",
                gauge.usgs_site_id,
                window.start.isoformat(),
                window.end.isoformat(),
                gauge.dv_record_start.isoformat(),
            )
            raise
        except MissingSeriesError:
            # A different failure with a different fix: the site stopped serving a parameter it
            # is recorded as serving. Also fatal, also not an empty window.
            logger.error(
                "%s: ABORTING at window %s to %s - a requested series was absent from a 200 "
                "response. This is NOT an empty window and NOT an out-of-record window; the fix "
                "is this site's available_params.",
                gauge.usgs_site_id,
                window.start.isoformat(),
                window.end.isoformat(),
            )
            raise

        result.readings_received += len(readings)

        if not readings:
            # ORDINARY: a gap inside the period of record. Advance - whichever way it classifies.
            #
            # The two branches below differ ONLY in log level and wording. Neither raises, neither
            # stops the walk, and neither is reachable by a path the other is not: an empty window
            # is not an error (CLAUDE.md § 14), and making the unexplained one fatal would make
            # every sensor outage a backfill that cannot be run to completion.
            result.empty_windows += 1
            explanation = gauges_module.explain_empty_window(
                gauge.usgs_site_id,
                window.start,
                window.end,
                known_gaps,
            )

            if explanation is not None:
                logger.info(
                    "%s: %s to %s returned no daily values - EXPECTED, inside the known gap "
                    "%s to %s (%s). Advancing.",
                    gauge.usgs_site_id,
                    window.start.isoformat(),
                    window.end.isoformat(),
                    explanation.gap_start.isoformat(),
                    explanation.gap_end.isoformat(),
                    explanation.note,
                )
            else:
                result.unexplained_empty_windows += 1
                logger.warning(
                    "%s: %s to %s returned no daily values - UNEXPLAINED. No row in "
                    "gauge_known_gaps covers this window. Not an error and not fatal; advancing. "
                    "If this range is genuinely not served, MEASURE it and seed the gap in a new "
                    "numbered migration rather than widening an existing row to cover it.",
                    gauge.usgs_site_id,
                    window.start.isoformat(),
                    window.end.isoformat(),
                )
            continue

        earliest = min(r.date for r in readings)
        if result.first_data_date is None or earliest < result.first_data_date:
            result.first_data_date = earliest
            if from_seed:
                logger.info(
                    "%s: FIRST DATA IN THE RECORD at %s. This walk began at the seeded "
                    "dv_record_start floor (%s), so nothing earlier was skipped and THIS is what "
                    "the seed gets reconciled against - the backfill does NOT update it "
                    "(CLAUDE.md § 15).",
                    gauge.usgs_site_id,
                    earliest.isoformat(),
                    gauge.dv_record_start.isoformat(),
                )
            else:
                logger.info(
                    "%s: first date in THIS RUN is %s. The walk resumed from stored data rather "
                    "than from the seeded floor (%s), so this is NOT the start of the record and "
                    "the seed is NOT reconciled against it - re-read min(date) from "
                    "gauge_readings_daily for that.",
                    gauge.usgs_site_id,
                    earliest.isoformat(),
                    gauge.dv_record_start.isoformat(),
                )

        written = usgs_daily_ingest.upsert_daily_readings(conn, readings)
        conn.commit()
        result.rows_written += written

        logger.info(
            "%s: %s to %s - %d received, %d written",
            gauge.usgs_site_id,
            window.start.isoformat(),
            window.end.isoformat(),
            len(readings),
            written,
        )

    return result


def backfill(
    conn,
    client: UsgsDailyClient | None = None,
    site_ids=None,
    start_override: date | None = None,
    end: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    dry_run: bool = False,
) -> list[DailySiteResult]:
    """Backfill daily values for every registered gauge, or the named ones."""
    client = UsgsDailyClient() if client is None else client
    end = datetime.now(timezone.utc).date() if end is None else end

    # Read ONCE for the whole run, not per window: a lookup that hits the database dozens of times
    # per site to answer a logging question is a lookup someone removes later for the wrong reason.
    # Only 'dv' gaps - a hole in the instantaneous service says nothing about the daily one.
    known_gaps = gauges_module.load_known_gaps(conn, source=gauges_module.SOURCE_DAILY)

    return [
        backfill_site(
            conn,
            client,
            gauge,
            end,
            window_days,
            dry_run,
            start_override=start_override,
            known_gaps=known_gaps,
        )
        for gauge in gauges_module.load(conn, site_ids)
    ]


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    parser = argparse.ArgumentParser(
        description=(
            "Backfill USGS DAILY values into gauge_readings_daily - the historical backbone. "
            "Long-running; run under tmux or nohup so an SSM disconnect does not kill it. This "
            "is a CLI a human invokes; it is deliberately not a scheduled job, and it never "
            "writes to the gauges table."
        )
    )
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        help="site id to backfill; repeatable. Default: every gauge in the registry.",
    )
    parser.add_argument(
        "--start",
        type=_parse_day,
        help=(
            "override the resume point (ISO date). Without this the backfill resumes from "
            "MAX(date) per site, or that site's own dv_record_start floor if it has no rows."
        ),
    )
    parser.add_argument("--end", type=_parse_day, help="stop here (ISO date). Default: today.")
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"request window size in days (default: {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the windows that would be requested and write nothing",
    )
    args = parser.parse_args(argv)

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

    started = datetime.now(timezone.utc)
    with session.writing() as conn:
        results = backfill(
            conn,
            site_ids=args.sites,
            start_override=args.start,
            end=args.end,
            window_days=args.window_days,
            dry_run=args.dry_run,
        )
    elapsed = datetime.now(timezone.utc) - started

    print()
    for result in results:
        print(f"  {result.describe()}")
    print(
        f"\ntotal: {sum(r.rows_written for r in results)} row(s) written across "
        f"{len(results)} site(s) in {elapsed}"
    )
    unexplained = sum(r.unexplained_empty_windows for r in results)
    if unexplained:
        print(
            f"\n{unexplained} empty window(s) were UNEXPLAINED - no gauge_known_gaps row covers\n"
            f"them (grep the log for UNEXPLAINED). Not a failure: it is the list of ranges to\n"
            f"MEASURE and seed as gaps before Phase 5's features interpolate across one."
        )

    reconcilable = [r for r in results if r.walked_from_seed]
    if reconcilable:
        print(
            "\nThe sites reporting FIRST DATA IN THE RECORD above walked from their seeded\n"
            "dv_record_start, so those dates are what the seed reconciles against. This backfill\n"
            "did NOT update the seed and must not: correct it deliberately, in a NEW numbered\n"
            "migration (CLAUDE.md § 15)."
        )
    if len(reconcilable) < len(results):
        print(
            "\nThe remaining sites RESUMED FROM STORED DATA. Their earliest date above is a\n"
            "property of this run, not of the record, and reconciling a seed against it would\n"
            "'correct' a seed that is already right. Use:\n"
            "  select usgs_site_id, min(date) from gauge_readings_daily group by 1 order by 1;"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
