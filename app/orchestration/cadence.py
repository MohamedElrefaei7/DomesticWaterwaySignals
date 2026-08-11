"""The cadence table: the single source of truth for trigger timing AND overdue thresholds.

CLAUDE.md § 4 and § 12. One frozen record per scheduled job. The scheduler builds its triggers
from this; the heartbeat imports this same object and defines no threshold of its own.

That last clause is the whole point, and it is the part that erodes first. The natural way to
write a heartbeat is `if age > timedelta(hours=2)` right where the comparison happens — it reads
fine, it is local, and it is a second table of the same fact. Two tables of the same fact diverge
silently, and the divergence produces confident wrong answers: someone changes an interval here,
the heartbeat keeps its own stale threshold, and a job that is now genuinely overdue is reported
healthy. That is a monitor that verifies the exact thing responsible for a failure and reports it
correct — CLAUDE.md § 2's theme 2.

tests/orchestration/test_heartbeat.py guards this BEHAVIOURALLY: it mutates an entry here and
asserts the heartbeat's verdict flips. A test that grepped heartbeat.py for numeric literals would
pass on the day someone reintroduces a threshold as a constant defined in a third file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

# Below this, APScheduler's misfire grace is short enough that a slow start, a long GC pause, or a
# database that took a moment to accept a connection reads as a missed run.
MINIMUM_MISFIRE_GRACE_SECONDS = 60


@dataclass(frozen=True)
class Cadence:
    """One scheduled job's timing. Frozen: this is a table, not a settings object.

    job_name      Stable identifier, and the join key into job_runs (CLAUDE.md § 4). Renaming one
                  orphans that job's history, and the heartbeat then sees a brand-new job that has
                  never succeeded.
    interval      How often the job fires.
    overdue_after How long without a SUCCESSFUL run before the heartbeat calls it overdue. Always
                  longer than `interval` — equal would mean a single late run alerts, and an alert
                  that fires routinely is an alert everyone mutes.
    """

    job_name: str
    interval: timedelta
    overdue_after: timedelta

    def __post_init__(self):
        if self.overdue_after <= self.interval:
            raise ValueError(
                f"cadence for {self.job_name!r}: overdue_after ({self.overdue_after}) must be "
                f"longer than interval ({self.interval}), or a single late run alerts and the "
                f"heartbeat trains its readers to ignore it."
            )

    @property
    def interval_seconds(self) -> int:
        return int(self.interval.total_seconds())

    @property
    def misfire_grace_time(self) -> int:
        """Derived from the interval. NEVER the library default.

        APScheduler's default misfire_grace_time is ONE SECOND. A job whose fire time passed while
        the process was down by more than a second is silently dropped — which is every job, after
        every outage, which is precisely the case the persistent job store exists to handle. The
        prior project's ten green scheduler tests asserted the settings that were supposed to
        guarantee restart recovery while recovery did not work (CLAUDE.md § 2, theme 2).

        Half the interval: long enough that a restart inside the window still fires the run that
        was due, short enough that a run does not fire so late it overlaps the next one. Floored at
        MINIMUM_MISFIRE_GRACE_SECONDS so a short-interval job does not inherit a grace measured in
        seconds.

        A CONSEQUENCE WORTH KNOWING BEFORE READING job_runs, confirmed by reading APScheduler's
        executor rather than assumed: with coalesce=True, only the LAST missed fire time is
        evaluated against the grace window, and that one is never more than `interval` old. So a
        job whose grace is >= its interval can never produce a `missed` row — it always catches up
        instead. That is the case for any job hitting the 60-second floor.

        For the heartbeat (900s interval, 450s grace) both outcomes are reachable: an outage that
        ends within 450s of the last due slot catches up, and one that ends later records a
        `missed` row. An absence of `missed` rows is therefore not by itself evidence that nothing
        was ever missed.
        """
        return max(MINIMUM_MISFIRE_GRACE_SECONDS, self.interval_seconds // 2)


# ---------------------------------------------------------------------------------------------
# The table.
# ---------------------------------------------------------------------------------------------
#
# Phase 2 has exactly one job, because Phase 2 builds the observation layer and nothing that
# produces data. The USGS and USDA ingest jobs join this table in Phase 3, and each one is
# incomplete until it also registers its table in the heartbeat's freshness registry (CLAUDE.md
# § 12) — liveness is measured from the data, never from the process.
#
# The heartbeat monitors itself along with everything else. That is not circular: a heartbeat that
# stops running stops writing `success` rows, so the next time it does run it reports its own gap,
# and the gap is visible in job_runs to anything else that looks. What it cannot do is report its
# own permanent death — nothing in-process can, which is why live verification step 7 stops the
# process and confirms recovery externally rather than trusting any test in this repo.

CADENCES: tuple[Cadence, ...] = (
    Cadence(
        job_name="heartbeat",
        interval=timedelta(minutes=15),
        # Three intervals. Two consecutive misses are a blip; three is a pattern.
        overdue_after=timedelta(minutes=45),
    ),
)

BY_NAME: dict[str, Cadence] = {c.job_name: c for c in CADENCES}

if len(BY_NAME) != len(CADENCES):  # pragma: no cover - a duplicate would be caught at import
    raise ValueError("duplicate job_name in CADENCES: job names are the join key into job_runs")
