"""The historical backfill: eighteen years of discharge, in windows, resumable.

CLAUDE.md § 14. A CLI A HUMAN RUNS, deliberately not a scheduled job. It runs for hours; a
scheduler would misfire it, and `coalesce` plus `max_instances=1` would then leave a job that is
permanently "running" rather than one that is either working or broken.

THREE DECISIONS, EACH WITH A SHORTER WRONG VERSION
--------------------------------------------------

1. CHUNKED BY DATE WINDOW, never one request for a site's whole record. The service will not
   return eighteen years in one response, and the failure mode when it declines is not a clean
   error - it is a truncated or timed-out response that looks like a short record.

2. RESUMABLE FROM `MAX(ts)` IN THE DATA, never from a checkpoint file or a progress table. A
   checkpoint is a second record of the same fact, and when the two disagree it is the checkpoint
   that gets believed. A run that crashed after writing rows but before updating its checkpoint
   re-fetches work it already did - harmless. A run that updated its checkpoint first skips work
   it never did - silent, permanent, and indistinguishable from a complete backfill.

3. AN EMPTY WINDOW ADVANCES; A MISSING (site, parameter) PAIR ABORTS. These are different facts
   that arrive looking identical (HTTP 200, well-formed JSON) and the code must never collapse
   them. Gaps are ordinary - a sensor outage, a window before the site's real record began. A
   missing series means the site has stopped serving that parameter, and continuing would walk
   the entire remaining record collecting nothing while reporting progress.

`iv_record_start` IS PER SITE, IS A FLOOR, AND IS NULL AT THREE OF THE FOUR SITES
--------------------------------------------------------------------------------
This walks from each site's own seeded value and NEVER earlier: silently walking further back
"looking for data" would turn a wrong seed into a slow sweep of empty windows that nobody
notices. Instead the first window that actually returns data is logged, so a wrong seed is
visible and correctable.

Since migration 0011 the column is NULL at Memphis, Vicksburg and Baton Rouge, because those
sites serve instantaneous values on a rolling window of recent weeks and a rolling window is not
a start date. THIS BACKFILL THEREFORE REFUSES THOSE SITES, in resume_point, naming the reason -
it already aborted at their first window on a missing series, and the refusal only moves that
abort earlier and points it at the right thing. Their historical record is the daily one.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.orchestration import session
from app.ingest import gauges as gauges_module
from app.ingest import usgs_ingest
from app.ingest.usgs_client import MissingSeriesError, UsgsClient

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Window:
    """A half-open request window, [start, end).

    HALF-OPEN is what makes the windows tile: consecutive windows share a boundary instant and
    neither claims it twice. The service's `endDT` is INCLUSIVE, so request_end() hands it a
    value one second below `end` - otherwise every boundary reading would be fetched twice, which
    the upsert would absorb silently and which would therefore never be noticed or fixed.
    """

    start: datetime
    end: datetime

    def request_end(self) -> datetime:
        return self.end - timedelta(seconds=1)


def windows(start: datetime, end: datetime, window_days: int = DEFAULT_WINDOW_DAYS) -> list[Window]:
    """Tile [start, end) into consecutive half-open windows. No gaps, no overlap.

    The last window is clipped to `end` rather than extending past it: requesting the future is
    harmless but it makes the log lie about what was asked for.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be at least 1, got {window_days}")
    if end <= start:
        return []

    span = timedelta(days=window_days)
    tiles: list[Window] = []
    cursor = start
    while cursor < end:
        tiles.append(Window(cursor, min(cursor + span, end)))
        cursor += span
    return tiles


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def resume_point(conn, gauge) -> tuple[datetime, str]:
    """Where this site's backfill starts, and why. Per site, from the data.

    Returns the newest stored reading's timestamp when the site has rows, and its own
    `iv_record_start` when it does not. NOT a global start, and NOT a checkpoint.
    """
    newest = usgs_ingest.latest_ts(conn, gauge.usgs_site_id)
    if newest is not None:
        return newest, f"resuming from MAX(ts) in gauge_readings_iv ({newest.isoformat()})"

    if gauge.iv_record_start is None:
        # A ROLLING-RETENTION SITE, AND THERE IS NOTHING TO WALK FROM.
        #
        # NULL here is not a missing value: it says this site serves instantaneous data on a
        # moving window of recent weeks and has no fixed start (migration 0011). Three of the four
        # gauges are in that state.
        #
        # This backfill already could not run for them - it aborts at the first window on a
        # missing series, which is § 14's guard working - and this refusal only moves the abort
        # earlier and gives it the right subject. Computing a start instead (today minus sixty
        # days, or the epoch) is the tempting two-line version and it is wrong in both
        # directions: one silently narrows a backfill to a window the incremental poll already
        # covers, the other walks decades of empty windows for data the service does not keep.
        raise ValueError(
            f"{gauge.usgs_site_id} has a NULL iv_record_start, which means ROLLING RETENTION: "
            f"this site serves instantaneous values for a moving window of recent weeks and has "
            f"no fixed start to backfill from. The historical record for this site is the DAILY "
            f"one - use app.ingest.daily_backfill. Do not substitute a computed start; whether "
            f"the instantaneous backfill applies to rolling-retention sites at all is an open "
            f"question for a human (see CONTEXT.md)."
        )

    return (
        _midnight_utc(gauge.iv_record_start),
        f"no rows stored; starting from this site's own iv_record_start "
        f"({gauge.iv_record_start.isoformat()})",
    )


@dataclass
class SiteResult:
    """What one site's backfill actually did. Reported, not inferred."""

    site_id: str
    windows_requested: int = 0
    empty_windows: int = 0
    readings_received: int = 0
    rows_written: int = 0
    first_window_with_data: Window | None = None

    def describe(self) -> str:
        first = (
            self.first_window_with_data.start.date().isoformat()
            if self.first_window_with_data
            else "NEVER - no window returned any data"
        )
        return (
            f"{self.site_id}: {self.windows_requested} window(s), "
            f"{self.empty_windows} empty, {self.readings_received} reading(s) received, "
            f"{self.rows_written} row(s) written. First data: {first}"
        )


def backfill_site(
    conn,
    client: UsgsClient,
    gauge,
    end: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    dry_run: bool = False,
    start_override: datetime | None = None,
) -> SiteResult:
    """Walk one site from its resume point to `end`, writing as it goes.

    Commits per window rather than once at the end. An eighteen-year backfill held in one
    transaction is one that loses everything to a disconnect in hour six, and that also holds a
    single snapshot open for the duration. Per-window commits mean an interrupted run resumes
    from where it actually got to - which is exactly what MAX(ts) reports.

    `start_override` is ONE BRANCH, not a second copy of this loop. An override that walked its
    own duplicate of the window/empty/missing-pair logic would drift from this one, and the
    drifted copy is the one the operator reaches for during an incident.
    """
    if start_override is not None:
        start = start_override
        why = (
            f"start OVERRIDDEN to {start_override.isoformat()} by --start; the resume point in "
            f"the data is being ignored"
        )
        logger.warning("%s: %s", gauge.usgs_site_id, why)
    else:
        start, why = resume_point(conn, gauge)
        logger.info("%s: %s", gauge.usgs_site_id, why)

    result = SiteResult(site_id=gauge.usgs_site_id)
    requested_pairs = gauge.requested_pairs()

    for window in windows(start, end, window_days):
        result.windows_requested += 1

        if dry_run:
            logger.info(
                "%s: [dry-run] would request %s to %s for %s",
                gauge.usgs_site_id,
                window.start.isoformat(),
                window.request_end().isoformat(),
                sorted(p for _s, p in requested_pairs),
            )
            continue

        try:
            readings = client.fetch_window(
                [gauge.usgs_site_id],
                gauge.available_params,
                window.start,
                window.request_end(),
            )
        except MissingSeriesError:
            # NOT caught-and-continued. The site has stopped serving a parameter it is recorded
            # as serving; walking the remaining fifteen years collecting nothing while logging
            # progress is precisely the failure this exception exists to prevent. The message
            # already names the site and parameter; re-raising preserves it.
            logger.error(
                "%s: aborting the backfill at window %s to %s - a requested series was absent "
                "from a 200 response. This is NOT an empty window.",
                gauge.usgs_site_id,
                window.start.isoformat(),
                window.request_end().isoformat(),
            )
            raise

        result.readings_received += len(readings)

        if not readings:
            # ORDINARY. A sensor outage, or a window before this site's record really began.
            # Advance and keep going.
            result.empty_windows += 1
            logger.info(
                "%s: %s to %s returned no readings (ordinary - advancing)",
                gauge.usgs_site_id,
                window.start.date().isoformat(),
                window.request_end().date().isoformat(),
            )
            continue

        if result.first_window_with_data is None:
            result.first_window_with_data = window
            # Decision 8: the seed's iv_record_start is unconfirmed for three of four sites, and
            # this line is how a wrong one becomes visible instead of becoming a slow sweep of
            # empty windows.
            logger.info(
                "%s: FIRST DATA in window starting %s (seeded iv_record_start is %s). A large gap "
                "between these two means the SEED is what to correct.",
                gauge.usgs_site_id,
                window.start.date().isoformat(),
                gauge.iv_record_start.isoformat(),
            )

        written = usgs_ingest.upsert_readings(conn, readings)
        conn.commit()
        result.rows_written += written

        logger.info(
            "%s: %s to %s - %d received, %d written",
            gauge.usgs_site_id,
            window.start.date().isoformat(),
            window.request_end().date().isoformat(),
            len(readings),
            written,
        )

    return result


def backfill(
    conn,
    client: UsgsClient | None = None,
    site_ids=None,
    start_override: datetime | None = None,
    end: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    dry_run: bool = False,
) -> list[SiteResult]:
    """Backfill every registered gauge, or the named ones.

    `start_override` exists for the one-site-one-year rehearsal in live verification step 3 and
    for re-fetching a known-bad range. It bypasses the resume point deliberately and says so in
    the log, because a start that came from an argument rather than from the data is exactly the
    kind of thing that gets forgotten between one command and the next.
    """
    client = UsgsClient() if client is None else client
    end = datetime.now(timezone.utc) if end is None else end

    return [
        backfill_site(
            conn,
            client,
            gauge,
            end,
            window_days,
            dry_run,
            start_override=start_override,
        )
        for gauge in gauges_module.load(conn, site_ids)
    ]


def _parse_day(text: str) -> datetime:
    """A --start/--end argument into a UTC midnight datetime."""
    try:
        return _midnight_utc(date.fromisoformat(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    parser = argparse.ArgumentParser(
        description=(
            "Backfill USGS discharge into gauge_readings_iv. Long-running - run it under tmux or "
            "nohup so an SSM disconnect does not kill it. This is a CLI a human invokes; it is "
            "deliberately not a scheduled job."
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
            "MAX(ts) per site, or that site's own iv_record_start if it has no rows."
        ),
    )
    parser.add_argument(
        "--end",
        type=_parse_day,
        help="stop here (ISO date). Default: now.",
    )
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
    print(
        "\nCompare each site's first-data date against its seeded iv_record_start above. A large "
        "discrepancy means the SEED is what to fix, in a new numbered migration."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
