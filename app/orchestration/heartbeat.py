"""The heartbeat: the job that reports on the other jobs, and on the data they produce.

CLAUDE.md § 12 decisions 13, 16, 17, and 18.

TWO CHECKS, AND THE SECOND IS THE ONE THAT CATCHES THE HARD FAILURE
-------------------------------------------------------------------
JOB OVERDUE-NESS. For each entry in the cadence table, how long since that job's most recent
SUCCESSFUL run, measured against that entry's own overdue_after.

DATA FRESHNESS. For each entry in the freshness registry below, how long since the newest row in
the ingested table, measured against that entry's own max_staleness.

The second exists because the first cannot see the failure that matters most. CLAUDE.md § 4:
liveness is measured from the data, never from the process — a source that accepts your
connection and delivers nothing is indistinguishable from a healthy one at every layer except
the data. That is not hypothetical for this ingest: MEASURED ON 2026-08-13, a USGS request for a
series a site does not serve returns HTTP 200 WITH AN EMPTY ARRAY. The client hard-fails on that
(app/ingest/usgs_client.py), but the general shape — a source that answers cheerfully and sends
nothing — is exactly what a job-status check reports as healthy. Successful job_runs rows, recent
activity, no errors, and a table whose newest row is four days old.

tests/orchestration/test_heartbeat.py::test_a_job_with_recent_runs_but_stale_data_is_still_flagged
is that scenario written down.

Phase 2 deliberately shipped NO registry rather than an empty one: a check that iterates over
nothing, finds nothing wrong, and reports healthy is a monitor whose green light means "I looked
at zero things." The registry is built here, in the commit that has something to put in it, which
is what CLAUDE.md § 12 requires of every ingest client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app import db
from app.orchestration import cadence as cadence_module
from app.orchestration.job import job

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------------------------
# The freshness registry.
# ---------------------------------------------------------------------------------------------
#
# WHY THIS IS NOT IN cadence.py, given how firmly § 12 says the cadence table is the single
# source of truth: it is a different fact about a different thing. The cadence table answers "how
# often should this job fire, and how long may it go without succeeding" — a statement about a
# PROCESS. This answers "how old may this table's newest row be" — a statement about DATA. They
# are related but not derivable from one another: a job can succeed on schedule while its source
# sends nothing, which is the entire reason this registry exists.
#
# What IS enforced is that the two cannot drift apart in the way that matters: every entry here
# must name a job that the cadence table schedules, checked at import below. A freshness entry
# for a job nothing runs would report a stale table forever with no way to fix it.


# ---------------------------------------------------------------------------------------------
# THE THRESHOLDS ARE DERIVED FROM MEASURED PUBLICATION BEHAVIOUR, NOT FROM EXPECTATION.
# ---------------------------------------------------------------------------------------------
#
# Every threshold below was originally set from what its source was expected to do. Measured on
# 2026-08-18, on real data, three of the five sat AT OR INSIDE their own boundary during entirely
# normal operation — `gauge_readings_daily` and `lock_movements` were reporting stale with nothing
# wrong anywhere, and `barge_rates` was 7d21h into a 10-day window for the same structural reason
# and days from doing the same.
#
# THAT IS THE FAILURE, AND IT IS NOT A TUNING PROBLEM. A check that cannot distinguish "the source
# is publishing normally" from "the source stopped" is not answering the only question it exists
# to answer, and an alarm that fires during correct operation is one that gets muted — after which
# nobody is watching the case it was built for. This project has already recorded that shape once
# (`d-pre`): a guard that goes red on the correct state trains its own removal.
#
# THE DERIVATION, applied identically to all five and stated per entry:
#
#     max_staleness  >=  cycle  +  observed_lag  +  one missed publication (a second cycle)
#
#   cycle         how often the source publishes a new period.
#   observed_lag  how long after a period closes before its value appears. MEASURED, with a date.
#   + one cycle   headroom for a single missed publication, so one late week is not an alarm and
#                 two consecutive ones are.
#
# THE ARITHMETIC IS COMMITTED, NOT JUST THE NUMBER. `cycle` and `observed_lag` are fields, and
# each entry carries a `# DERIVATION:` line that
# tests/orchestration/test_heartbeat.py::test_each_threshold_records_its_derivation PARSES and
# checks against those fields and against `max_staleness`. A bare `timedelta(days=4)` is a number
# somebody reducing alert latency will tighten because they cannot see what it was for; the
# arithmetic beside it is the only thing that makes the tightening visibly wrong.
#
# A THRESHOLD THAT STARTS TRIPPING IS A SIGNAL TO RE-MEASURE, NOT TO RETUNE. `observed_lag` is a
# measurement with a date, not a constant: a source that changes its publication schedule
# invalidates it, and `lag_measured_on` is what tells a later reader the measurement is old.
# Widening or tightening by feel puts the check back where it started.
#
# CONTENT AGE AND INGESTION AGE ARE DIFFERENT QUESTIONS, AND ONLY ONE OF THEM IS THIS ONE.
#
# None of these tables carries an ingestion timestamp, and that is deliberate rather than missing.
# `gauge_readings_daily` is (usgs_site_id, date, param_code, stat_cd, value, qualifiers);
# `lock_movements` and `barge_rates` are keyed on `week_ending`. So this check can only measure
# how old the newest CONTENT is — which is the question "is the source still publishing".
#
# "Is the pipeline running" is ALREADY ANSWERED, by `job_runs`, through the cadence table's
# `overdue_after`. Both statements are true at once and both are useful: measured 2026-08-18, the
# heartbeat reported `usgs_daily_ingest: ok` with a success minutes old while this table's newest
# content was two days behind.
#
# THE OBVIOUS "FIX" FOR A PERMANENTLY-STALE TABLE IS TO ADD AN `ingested_at` COLUMN AND MEASURE
# THAT INSTEAD. It would make every entry here read green forever, because it would silently
# convert this check into a second copy of the one `job_runs` already performs — leaving nobody at
# all watching the source. CLAUDE.md § 4: liveness is measured from the DATA, never from the
# process.


@dataclass(frozen=True)
class Freshness:
    """One ingested table's liveness contract.

    job_name       The job that writes this table. Must exist in the cadence table.
    table          The ingested table.
    timestamp_column  The column carrying the SOURCE's timestamp — not an inserted_at. An
                   inserted_at column measures that we wrote something, which is the process
                   measurement this check exists to replace.
    cycle          How often the SOURCE publishes a new period. Not how often the job polls: a
                   daily poll of a weekly series still only produces a new row once a week.
    observed_lag   Measured delay between a period closing and its value appearing.
    lag_measured_on  When `observed_lag` was measured, or None where it is an assumption carried
                   from a sibling source rather than a measurement of this one. A date here is a
                   claim that somebody looked; None is the honest absence of one.
    max_staleness  How old the newest row may be before this is stale. Never below
                   `derived_minimum`, and the entry's `# DERIVATION:` comment records why.
    """

    job_name: str
    table: str
    timestamp_column: str
    cycle: timedelta
    observed_lag: timedelta
    lag_measured_on: date | None
    max_staleness: timedelta

    @property
    def derived_minimum(self) -> timedelta:
        """`cycle + observed_lag + one missed publication`, the floor for `max_staleness`.

        A FLOOR, NOT AN EQUALITY (CLAUDE.md § 24). Equality would make a well-reasoned increase
        fail the guard, and the guard exists to stop TIGHTENING - which is the direction that
        reintroduces permanent staleness.
        """
        return self.cycle + self.observed_lag + self.cycle


FRESHNESS: tuple[Freshness, ...] = (
    Freshness(
        job_name="usgs_ingest",
        table="gauge_readings_iv",
        timestamp_column="ts",
        # CYCLE = 1 HOUR, and it is the POLL's interval rather than the gauges' own.
        #
        # Native recording cadence is per site and is 15, 30 or 60 minutes across the four seeded
        # gauges (measured 2026-08-13, docs/findings.md § A) - but USGS TRANSMITS hourly rather
        # than continuously, and this job polls hourly, so a new row cannot appear here more often
        # than once an hour whatever the gauge is doing.
        cycle=timedelta(hours=1),
        # 2 HOURS. Carried unchanged from the Phase 3 registry comment, which recorded a MAX(ts)
        # one to two hours behind wall clock as the normal steady state.
        #
        # `lag_measured_on` IS None BECAUSE THIS WAS NEVER MEASURED WITH A QUERY. It is the
        # value this project has been running on and it has not fired, which is evidence of a
        # kind but is not a measurement; recording a date beside it would assert that somebody
        # looked (CLAUDE.md § 16: a comment copied from a sibling reads as measured and is worse
        # than an absent one).
        observed_lag=timedelta(hours=2),
        lag_measured_on=None,
        # DERIVATION: cycle 1h + observed lag 2h + one missed publication 1h = 4h
        #
        # SIX HOURS IS UNCHANGED, AND IT ALREADY SATISFIES THE DERIVATION with two hours to
        # spare. Only the reasoning is new. Six hours is roughly four consecutive failed polls or
        # a genuine upstream outage before this speaks, and the extra margin over the 4-hour floor
        # is what keeps it from firing on ordinary USGS lateness.
        #
        # THIS IS THE ONE ENTRY WHOSE COLUMN IS A REAL timestamptz, so it is also the only one
        # that does not pay the midnight-anchoring cost the four date-keyed entries below do.
        max_staleness=timedelta(hours=6),
    ),
    Freshness(
        job_name="usgs_daily_ingest",
        table="gauge_readings_daily",
        # `date`, not a timestamp. A daily mean is a calendar date and is stored as one; see
        # newest_row() for how an age is computed from it without inventing a time of day.
        timestamp_column="date",
        # CYCLE = 1 DAY. One published daily mean per site per calendar day.
        cycle=timedelta(days=1),
        # 2 DAYS, MEASURED 2026-08-18 ON THE INSTANCE: 4 rows/day, every day, no gaps, and the
        # newest date was 2026-08-16. USGS finalises a daily value roughly two days after the day
        # it describes, consistently.
        observed_lag=timedelta(days=2),
        lag_measured_on=date(2026, 8, 18),
        # DERIVATION: cycle 1d + observed lag 2d + one missed publication 1d = 4d
        #
        # WAS 48 HOURS, WHICH WAS THE DEFECT. The normal state of this table is two days behind,
        # so a 2-day threshold sat permanently AT its own boundary - and because a date column is
        # anchored at midnight UTC by _as_utc_datetime (up to 24 hours of extra apparent age), it
        # spent most of every day over the line. It reported stale on 2026-08-18 with the source
        # publishing perfectly and the job succeeding minutes earlier.
        #
        # The old comment's arithmetic - "the newest date is normally yesterday, ~24 hours old" -
        # was the error, and it was an expectation rather than a measurement. The newest date is
        # normally the day BEFORE yesterday.
        max_staleness=timedelta(days=4),
    ),
    Freshness(
        job_name="usda_rates_ingest",
        table="barge_rates",
        # The published week label. A calendar date, like the daily table's - see newest_row()
        # for how an age is taken from one without inventing a time of day.
        timestamp_column="week_ending",
        # CYCLE = 7 DAYS. A weekly series labelled by week-ending date.
        cycle=timedelta(days=7),
        # 3 DAYS, AND THIS ONE IS AN ASSUMPTION RATHER THAN A MEASUREMENT - hence
        # `lag_measured_on=None`.
        #
        # It is carried from `lock_movements` below, which WAS measured. Both come from USDA
        # AgTransport, both are labelled by `week_ending`, and both are polled weekly, so the
        # analogy is a reasonable one - but CLAUDE.md § 16 is explicit that a property established
        # by analogy to a measured sibling is a guess that later reads as verified, and this
        # project has already been wrong assuming two USGS endpoints shared a period of record
        # (§ 15). The honest record is the missing date.
        #
        # TO MEASURE IT: `SELECT max(week_ending), now()::date - max(week_ending) FROM
        # barge_rates;` run a few days apart, and note which weekday the labels fall on. If it
        # differs from 3 days, change `observed_lag` and this comment - the DERIVATION line and
        # the test move with it.
        observed_lag=timedelta(days=3),
        lag_measured_on=None,
        # DERIVATION: cycle 7d + observed lag 3d + one missed publication 7d = 17d
        #
        # WAS 10 DAYS - THE SAME DEFECT AS lock_movements, AND IT WAS NOT YET FIRING. On
        # 2026-08-18 this table read fresh at 7d21h purely because it sat earlier in its own
        # cycle; 7 days of normal gap plus a 3-day lag already reaches 10 and it would have
        # tripped within days. FIXING THE TWO TABLES THAT WERE RED AND LEAVING THIS ONE IS HOW
        # THE WHOLE THING RECURS, so the derivation is applied to all five rather than to the
        # ones that complained.
        #
        # FRESHNESS COUNTS ROWS, NOT RATES, AND THAT IS LOAD-BEARING FOR THIS TABLE.
        #
        # `pct_of_tariff` IS NULLABLE and legitimately NULL for 774 of 8,260 nearby records:
        # USDA publishes no rate when the river is closed, 661 of those weeks in December-March
        # and 36% of Twin Cities' entire history (migration 0017). newest_row() takes
        # MAX(week_ending) over ALL rows, which is what makes a closure week count as the fresh
        # data it is.
        #
        # "Only count rows that have data" - MAX(week_ending) WHERE pct_of_tariff IS NOT NULL -
        # is the natural-sounding change that breaks it. In January the upper segments are shut,
        # so their newest rows carry NULL rates, and this check would report the table stale
        # while ingest was perfectly correct. The damage is not the false alarm: it is that the
        # alarm fires all winter, gets muted, and the check is then not watching in the spring
        # either. Guarded by
        # tests/ingest/test_usda_rates.py::test_freshness_uses_max_week_ending_over_all_rows.
        max_staleness=timedelta(days=17),
    ),
    Freshness(
        job_name="usda_movements_ingest",
        table="lock_movements",
        timestamp_column="week_ending",
        # CYCLE = 7 DAYS, MEASURED 2026-08-18: week_ending values every 7 days with no gaps -
        # 2026-08-08, 08-01, 07-25, 07-18 - all Saturdays.
        cycle=timedelta(days=7),
        # >= 3 DAYS, MEASURED 2026-08-18: week_ending 2026-08-15 had not been published, three
        # days after that week closed. A LOWER BOUND rather than a point estimate, because the
        # measurement can only say the value had not appeared yet - it cannot say when it did.
        #
        # The bound is used as though it were the value, which is the direction that errs towards
        # a WIDER threshold, and that is the safe direction here: too wide reports stale late,
        # too narrow reports stale always and gets muted.
        observed_lag=timedelta(days=3),
        lag_measured_on=date(2026, 8, 18),
        # DERIVATION: cycle 7d + observed lag 3d + one missed publication 7d = 17d
        #
        # WAS 10 DAYS, AND IT WAS RED ON MEASUREMENT DAY: newest week_ending 2026-08-08 on
        # 2026-08-18 is 10 days plus the midnight anchoring, with USDA publishing exactly on
        # schedule. The old comment reasoned "up to 7 days old before anything has gone wrong,
        # add a holiday week, 8 or 9 days is still healthy" - which never counted the publication
        # lag at all, so it was one whole term short.
        #
        # NOT UNIFIED WITH THE DAILY TABLES' THRESHOLD, and not to be. Two values differing
        # because their sources differ is a contract to assert in opposite directions, not an
        # inconsistency to remove (CLAUDE.md § 25).
        #
        # BOTH USDA TABLES ARE REGISTERED, not one. A single registration covering "the USDA
        # ingest" would report healthy while the other table received nothing - the heartbeat's
        # green light would mean "the one table I know about is fine", which is CLAUDE.md § 2's
        # theme 2 exactly. They have separate jobs, so they have separate entries.
        max_staleness=timedelta(days=17),
    ),
    Freshness(
        job_name="features_build",
        # THE FIRST DERIVED TABLE IN THIS REGISTRY, and it is registered for a reason none of the
        # ingest entries have.
        #
        # An ingest table goes stale when a SOURCE goes quiet. `features` goes stale when THE BUILD
        # stops - and the build is the one job whose failure is otherwise invisible from the data,
        # because every table it reads stays perfectly fresh while it does nothing. A green
        # heartbeat covering only the four ingest tables would mean "everything we collect is
        # arriving", which is true, and says nothing about whether anything is being computed from
        # it. That is CLAUDE.md § 2's theme 2 with an entire layer inside the blind spot.
        table="features",
        # `date`, the day the feature describes - never an inserted_at. An inserted_at would
        # measure that the build wrote something, which is the process measurement this check
        # exists to replace.
        timestamp_column="date",
        # CYCLE = 1 DAY. The build runs daily and writes one row per site per feature per day.
        cycle=timedelta(days=1),
        # 2 DAYS, AND THE OLD COMMENT HAD THIS WRONG IN A WAY WORTH RECORDING.
        #
        # It said the newest feature date "comes from the daily-values job". IT DOES NOT.
        # `features` is built from `gauge_daily`, which app/features/rollup.py reads from the
        # `gauge_series` VIEW - and 0010's precedence rule takes the INSTANTANEOUS row wherever
        # one exists, falling back to the published daily mean only where none does. So the
        # newest feature date tracks `gauge_readings_iv`, which is current to within hours.
        #
        # That is why `features` carried a row dated 2026-08-18 on 2026-08-18 while
        # `gauge_readings_daily` ended at 2026-08-16 - an observation that looks impossible if
        # you believe the old comment, and is ordinary once you follow the view.
        #
        # SO WHY 2 DAYS AND NOT 0. Instantaneous retention is a rolling window of recent weeks at
        # three of the four gauges (§ 15), and whenever the iv side is briefly absent
        # `gauge_series` falls back to the dv side, putting the newest feature date at
        # `gauge_readings_daily`'s own 2-day lag. THAT IS NORMAL OPERATION, not a fault. Deriving
        # this from the FASTEST input would put the threshold back on its boundary the moment the
        # fastest input hiccuped - the exact defect this commit exists to remove - so the lag is
        # the SLOWEST healthy input's.
        observed_lag=timedelta(days=2),
        lag_measured_on=date(2026, 8, 18),
        # DERIVATION: cycle 1d + observed lag 2d + one missed publication 1d = 4d
        #
        # WAS 48 HOURS. That satisfied the arithmetic only under the fallback-free reading, and
        # exactly - 2 days against a 2-day floor is a threshold sitting on its own boundary.
        #
        # THE DIVISION OF LABOUR WITH THE JOB CHECK IS CLEAN AT THIS VALUE, and it is worth
        # stating because it is what makes four days defensible rather than merely generous:
        # `features_build`'s cadence entry has `overdue_after=3 days`, so A BUILD THAT STOPS IS
        # CAUGHT BY THE JOB CHECK FIRST, at three days. What this entry catches is the case the
        # job check cannot see - the build running, succeeding, and producing nothing new.
        max_staleness=timedelta(days=4),
    ),
)


_unscheduled = [f.job_name for f in FRESHNESS if f.job_name not in cadence_module.BY_NAME]
if _unscheduled:  # pragma: no cover - a drift would be caught at import
    raise ValueError(
        f"freshness registry names job(s) with no cadence entry: {_unscheduled}. Nothing would "
        f"ever write those tables, so they would be reported stale forever with no way to fix it."
    )


@dataclass(frozen=True)
class Verdict:
    """One job's state, as of one heartbeat run."""

    job_name: str
    last_success: datetime | None
    age: timedelta | None
    overdue_after: timedelta
    overdue: bool

    def describe(self) -> str:
        if self.last_success is None:
            return (
                f"{self.job_name}: NO SUCCESSFUL RUN ON RECORD "
                f"(threshold {self.overdue_after})"
            )
        state = "OVERDUE" if self.overdue else "ok"
        return (
            f"{self.job_name}: {state} - last success {self.last_success.isoformat()} "
            f"({self.age} ago, threshold {self.overdue_after})"
        )


def log_sink(message: str) -> None:
    """The default alert sink: stdout via the logger.

    Slack is a webhook URL, which is a secret (CLAUDE.md § 1) and a later commit. Every sink has
    this signature, so swapping this one for a real transport changes one argument and nothing
    else.
    """
    logger.warning("HEARTBEAT ALERT: %s", message)


def _emit(sink, message: str) -> None:
    """Call the sink, and swallow anything it raises. Decision 18.

    THIS IS THE ONE PLACE IN THIS COMMIT WHERE SWALLOWING AN EXCEPTION IS CORRECT, and it
    contradicts the @job decorator three files away, which never swallows and says so at length.
    The comment is here because the contradiction is real and someone will otherwise "fix" one of
    the two to match the other.

    The difference is what the exception means. In @job, an exception means the work failed, and
    the scheduler and the operator both need to know. Here, an exception means the REPORT ABOUT
    the work could not be delivered — Slack is down, the network is out, the webhook rotated. The
    jobs it was reporting on are unaffected. A monitoring job that fails because it could not
    report is a monitoring job that stops monitoring, and it stops precisely during the kind of
    broad outage where monitoring matters most.

    The failure is logged, so it is not invisible; it just is not fatal.
    """
    try:
        sink(message)
    except Exception:
        logger.exception(
            "alert sink raised; the heartbeat is continuing deliberately (CLAUDE.md § 12). "
            "THE ALERT BELOW WAS NOT DELIVERED: %s",
            message,
        )


def last_success(conn, job_name: str) -> datetime | None:
    """The most recent SUCCESS row's finished_at. Decision 16.

    `status = 'success'` is the entire content of this function. Dropping it — taking
    MAX(finished_at) across all statuses — turns this into "last activity", and a job that has
    failed every night for a week has plenty of recent activity and no recent success. The query
    would report it healthy (CLAUDE.md § 4).

    Backed by the partial index in 0003_job_runs_success_index.sql, which is partial on exactly
    this predicate.
    """
    row = conn.execute(
        "SELECT max(finished_at) FROM job_runs WHERE job_name = %s AND status = 'success'",
        (job_name,),
    ).fetchone()
    return row[0] if row else None


def check(conn, now: datetime | None = None, cadences=None) -> list[Verdict]:
    """One verdict per cadence entry.

    `cadences` defaults to the live cadence table, READ AT CALL TIME rather than captured at
    import. That is what lets the test for decision 13 mutate an entry and observe this function's
    verdict follow it — a module-level `from cadence import CADENCES` binding would freeze the
    value at import and the guard would be untestable, which in practice means unguarded.
    """
    now = now or datetime.now(timezone.utc)
    cadences = cadence_module.CADENCES if cadences is None else cadences

    verdicts = []
    for entry in cadences:
        succeeded_at = last_success(conn, entry.job_name)
        age = None if succeeded_at is None else now - succeeded_at
        verdicts.append(
            Verdict(
                job_name=entry.job_name,
                last_success=succeeded_at,
                age=age,
                # The threshold comes from the cadence entry and from nowhere else. Decision 13.
                overdue_after=entry.overdue_after,
                # No successful run on record counts as overdue. A job that has never succeeded is
                # not a job that is fine; it is the most alarming state in the table, and treating
                # a NULL as "nothing to report" is how a job that never once worked stays quiet.
                overdue=(age is None or age > entry.overdue_after),
            )
        )
    return verdicts


@dataclass(frozen=True)
class FreshnessVerdict:
    """One ingested table's state, as of one heartbeat run."""

    job_name: str
    table: str
    newest: datetime | None
    age: timedelta | None
    max_staleness: timedelta
    stale: bool
    error: str | None = None

    def describe(self) -> str:
        if self.error is not None:
            return f"{self.table}: CANNOT BE CHECKED - {self.error}"
        if self.newest is None:
            return (
                f"{self.table}: EMPTY - no rows at all "
                f"(threshold {self.max_staleness}, written by {self.job_name})"
            )
        state = "STALE" if self.stale else "fresh"
        return (
            f"{self.table}: {state} - newest row {self.newest.isoformat()} "
            f"({self.age} old, threshold {self.max_staleness})"
        )


def newest_row(conn, entry: Freshness) -> datetime | None:
    """MAX(ts) on the ingested table. The measurement this whole check is about.

    The table and column are interpolated rather than parameterized because they are identifiers,
    which SQL parameters cannot carry. They come from the frozen registry above — module
    constants, never user input — and the identifier check below is a belt-and-braces guard so
    that a future registry built from configuration cannot turn this into an injection point.
    """
    if not entry.table.isidentifier() or not entry.timestamp_column.isidentifier():
        raise ValueError(
            f"freshness entry {entry.job_name!r} has a non-identifier table/column "
            f"({entry.table!r}, {entry.timestamp_column!r})"
        )
    row = conn.execute(
        f"SELECT max({entry.timestamp_column}) FROM {entry.table}"
    ).fetchone()
    newest = row[0] if row else None
    return _as_utc_datetime(newest)


def _as_utc_datetime(value):
    """Normalize a MAX() result to an aware UTC datetime so an age can be computed.

    A DATE COLUMN NEEDS THIS AND A timestamptz COLUMN DOES NOT, and the difference is the whole
    reason it exists: `gauge_readings_daily.date` is a calendar date with no time of day, and
    `now - date` is not a thing Python will do.

    A date is anchored at MIDNIGHT UTC, which is the conservative direction. It makes a daily
    value look up to 24 hours OLDER than an alternative anchoring would - so the check errs
    towards reporting stale rather than towards reporting healthy, and the freshness threshold
    for that table is set with this already priced in (48 hours, see the registry above).

    Anchoring is done here, at the boundary, rather than by casting in SQL: `date::timestamptz`
    resolves in the SESSION's TimeZone setting, so the same table would produce a different age
    depending on who connected.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # A naive timestamp from a `timestamp` (no tz) column would otherwise blow up on
        # subtraction against an aware `now`. There is no such column today; this makes adding
        # one a non-event rather than a 3am traceback inside the monitor.
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def check_freshness(conn, now: datetime | None = None, registry=None) -> list[FreshnessVerdict]:
    """One verdict per freshness entry. Liveness measured from the DATA.

    `registry` defaults to the live one READ AT CALL TIME, for the same reason check() reads the
    cadence table at call time: it is what lets a test mutate an entry and observe the verdict
    follow it. A module-level binding would freeze the value at import and the guard would be
    untestable, which in practice means unguarded.

    A TABLE WITH NO ROWS AT ALL IS STALE, NOT QUIET. Same reasoning as a job with no successful
    run being overdue rather than silent: an ingest table that has never received a row is the
    most alarming state it can be in, and treating a NULL MAX(ts) as "nothing to report" is how a
    source that never once delivered stays quiet forever. This DOES mean the heartbeat alerts
    about gauge_readings_iv from the moment 0005 is applied until the backfill puts rows in it —
    expected, once, exactly like the heartbeat's own first run alerting about itself.
    """
    now = now or datetime.now(timezone.utc)
    registry = FRESHNESS if registry is None else registry

    verdicts = []
    for entry in registry:
        error = None
        newest = None
        try:
            newest = newest_row(conn, entry)
        except Exception as exc:
            # A registered table that cannot be queried — it does not exist, the migration was
            # never applied, a permission changed — is reported as a FAILED CHECK and alerts. It
            # is not skipped: CLAUDE.md § 13, a skipped check must never read as green. Rolled
            # back first because psycopg leaves the transaction unusable after an error, and the
            # remaining entries still need to be checked.
            conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("freshness check failed for table %s", entry.table)

        age = None if newest is None else now - newest
        verdicts.append(
            FreshnessVerdict(
                job_name=entry.job_name,
                table=entry.table,
                newest=newest,
                age=age,
                max_staleness=entry.max_staleness,
                # Unqueryable or empty both count as stale. Neither is a healthy table, and
                # neither should need a second kind of alert to be noticed.
                stale=(error is not None or age is None or age > entry.max_staleness),
                error=error,
            )
        )
    return verdicts


@job("heartbeat")
def heartbeat_job(sink=None, url: str | None = None, now: datetime | None = None):
    """The scheduled unit. Returns None: it writes no rows, so it reports no row count.

    None rather than 0 is the point of decision 9 applied to itself. 0 would claim this job wrote
    zero rows to the database; None says it is not a job that counts rows. The bookkeeping row
    @job writes is not this job's output.
    """
    sink = log_sink if sink is None else sink

    with db.connection(url) as conn:
        verdicts = check(conn, now=now)
        freshness = check_freshness(conn, now=now)

    overdue = [v for v in verdicts if v.overdue]
    stale = [v for v in freshness if v.stale]

    for verdict in verdicts:
        logger.info("%s", verdict.describe())
    for verdict in freshness:
        logger.info("%s", verdict.describe())

    # ONE ALERT, not one per category. Two sinks' worth of messages for one heartbeat run is two
    # notifications for one event, and a broad outage — which trips both checks at once — would
    # produce exactly that, at the moment the reader can least afford noise.
    problems = []
    if overdue:
        problems.append(
            f"{len(overdue)} of {len(verdicts)} job(s) overdue:\n"
            + "\n".join(f"  {v.describe()}" for v in overdue)
        )
    if stale:
        problems.append(
            f"{len(stale)} of {len(freshness)} ingested table(s) stale:\n"
            + "\n".join(f"  {v.describe()}" for v in stale)
        )

    if problems:
        _emit(sink, "\n".join(problems))

    return None
