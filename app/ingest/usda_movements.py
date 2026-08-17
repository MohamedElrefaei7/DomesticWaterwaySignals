"""Downbound barged grain movements through the locks: parsing, writing, and the weekly poll.

The volume side of the target. Sibling of usda_rates.py and deliberately the same shape.

WHAT THIS DATASET DOES NOT PUBLISH
----------------------------------
Measured 2026-08-14 against n4pw-9ygw, "Downbound Barge Grain Movements (Tons)":

  NO DIRECTION.   The dataset is downbound-only by construction - it is what the title says. There
                  is no direction dimension to key on, and a column holding 'Down' on all 26,144
                  rows would add nothing a reader of the table name does not already have.

  NO BARGE COUNT. Tons only. Phase 4 carried a `barges` column on the strength of the handoff's
                  wording; migration 0016 drops it. A column that is ALWAYS NULL is worse than an
                  absent one - it looks like data, and `WHERE barges IS NOT NULL` returns nothing
                  forever with nothing to say why. A barge count, if it is ever wanted, comes from
                  a DIFFERENT dataset and is a separate commit with its own measurement.

The published fields are `date`, `week`, `month`, `year`, `commodity`, `lock`, `tons`.

ZERO IS A VALUE. NULL IS THE ABSENCE OF ONE.
--------------------------------------------
0015 argued this about `barges`; the argument was right and now belongs to `tons`, which is the
measure that exists. Both directions of collapsing them are one line long:

    skipping rows where tons == 0    deletes the event this project studies. During the 2022
                                     low-water event, near-zero movement IS the signal - a tow that
                                     could not sail is the physical fact behind the thesis. The gap
                                     it leaves is indistinguishable from a week nobody reported.

    coalescing NULL to 0             invents a surveyed zero out of silence, in the same column, in
                                     the opposite direction. Every average over the series is then
                                     dragged toward zero by weeks nobody measured.

AND THE MEASUREMENT SAYS 0 IS THE COMMON CASE, NOT THE EDGE CASE
-----------------------------------------------------------------
Measured 2026-08-14 across all 26,144 records (migration 0018):

    tons = 0        8,218 records (31%)   USDA's PUBLISHED way of saying nothing moved.
    tons absent       108 records (0.4%)  Three locks only - AK Lock 1 (71), OH Olmsted (26),
                                          MS Locks 27 (11). 96 of them in 2015-2016. FLAT ACROSS
                                          MONTHS: 16 9 11 6 3 14 11 1 7 9 7 14.

Because the source says "none moved" explicitly 8,218 times, THE 108 SILENT RECORDS ARE SAYING
SOMETHING ELSE. Flat across months and confined to three locks in a two-year window, they are a
REPORTING GAP - they say nothing whatsoever about the river.

DO NOT REUSE usda_rates.py's LANGUAGE FOR THIS COLUMN. There, a NULL is winter navigation closure:
seasonal, physical, and a fact about the river. Here it is not. The handling is the same shape and
the meaning is different, and a comment copied across would assert something the measurement
contradicts (CLAUDE.md § 16).

The three locks that carry the gap are the SUMMARY locks. `MS Locks 27` is the Mississippi's main
southbound gate, and coalescing its eleven silent weeks to 0 would state that no grain moved
through it - a fabricated zero in the most load-bearing series this project has, in a layer with no
confidence gate watching.

THE LOCK STRING IS STORED VERBATIM
----------------------------------
The published vocabulary contains `MS Locks 27` - plural - beside `MS Lock 15`, `MS Lock 25` and
`MS Lock 26`, singular. That inconsistency is USDA's, it is stable, and NOTHING IN THIS MODULE
NORMALIZES IT. A normalization step is where the join silently breaks the week USDA publishes a
value the mapping does not cover, and the symptom is missing weeks rather than an unmapped value.
0016's CHECK is the tripwire for an eighth lock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app import db
from app.orchestration import session
from app.ingest import socrata_client
from app.ingest.socrata_client import (
    ABSENT,
    MalformedResponseError,
    SocrataClient,
    optional_field,
    parse_period_label,
    required_field,
)
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "usda_movements_ingest"
TABLE = "lock_movements"
DATASET_KEY = "lock_movements"

# THE SOCRATA FIELD NAMES, MEASURED 2026-08-14 AGAINST THE LIVE API.
#
# A verbatim record from n4pw-9ygw:
#
#   {"date":"2026-08-08T00:00:00.000","week":"31","month":"8","year":"2026",
#    "commodity":"Corn","lock":"IL La Grange","tons":"136400"}
#
# EVERY NAME PHASE 4 ASSUMED WAS WRONG - `lock_id`, `week_ending`, `grain_type`, `direction`,
# `barges`. Two of those five name things the source does not publish at all, which is why this
# correction is a migration and not just a dict.
#
# `date` MAPS TO `week_ending` DELIBERATELY, and the reasoning is written out once in
# usda_rates.FIELDS rather than twice: the source name says what type the value is, ours says what
# it means, and "rename the column to match the source" is the tidy that would lose that.
#
# `week`, `month` and `year` are deliberately not mapped - all three are derivable from `date`, and
# a stored copy of a derivable fact is a second record that can disagree with the first.
FIELDS = {
    "lock": "lock",
    "week_ending": "date",
    "commodity": "commodity",
    "tons": "tons",
}

# See the note in usda_rates: `date` is also a SoQL type name, and a rejection would arrive as an
# error document raising SocrataResponseError, never as an empty page.
ORDER_COLUMN = FIELDS["week_ending"]

OVERLAP_WEEKS = 8
COLD_START_WEEKS = 12
BATCH_SIZE = 500


@dataclass(frozen=True)
class LockMovement:
    """One published week of downbound movement through one lock, for one commodity.

    `tons` is OPTIONAL, and None means "USDA DID NOT REPORT THIS LOCK-WEEK" while 0 means
    "reported, and nothing moved". The Optional is the whole point of the type: a plain Decimal
    would force a value for a week that has none, and the only values available to force are zero
    and a lie.

    None here is a REPORTING GAP, not a closure. 108 of 26,144 records, on three locks, flat
    across months (0018). The rates module's None means the opposite kind of thing.

    THERE IS NO `direction` AND NO `barges` FIELD HERE, and their absence is the schema decision
    from migration 0016 made structural. Neither is published.
    """

    lock: str
    week_ending: date
    commodity: str
    tons: Decimal | None


def parse_tons(raw, *, field: str = "tons") -> Decimal:
    """A PUBLISHED tonnage, exactly as published. NEVER CALLED FOR AN ABSENT ONE. 0 IS A TONNAGE.

    Shaped like usda_rates.parse_rate and for the same structural reason: absence is decided
    BEFORE the call, by `optional_field`, so everything arriving here is supposed to be a number
    and anything that will not parse is a DATA ERROR that raises. That split is what stops a
    corrupt value being filed as an unreported week.

    THE FIRST BRANCH IS THE DECISION. `if not raw: return None` would be shorter and would map the
    string '0' and the integer 0 onto the unreported case - turning 8,218 published zeros into
    missing weeks, which is precisely the observation the low-water analysis is built on.

    A BLANK VALUE RAISES RATHER THAN BECOMING NULL, and this is the one condition here that is
    argued rather than measured. USDA expresses "no tonnage" by omitting the key (108 records), and
    it expresses "none moved" with an explicit 0 (8,218 records); a present-but-empty cell is a
    THIRD spelling that nothing has measured. Storing it as NULL would hide it among the 108
    legitimate reporting gaps - camouflage that matters more here than for rates, because these
    gaps sit on the summary locks. If this fires on a live backfill, MEASURE what those records
    look like before changing anything.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise MalformedResponseError(
            f"{field} is {raw!r} - present but blank. NOT read as an unreported week: this source "
            f"omits the `tons` key entirely when it reports nothing (108 of 26,144 records) and "
            f"publishes an explicit 0 when nothing moved (8,218 records), so a blank is a third "
            f"and unmeasured condition. Storing it as NULL would hide it among the legitimate "
            f"reporting gaps at the summary locks (migration 0018)."
        )
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise MalformedResponseError(
            f"{field} {raw!r} is not a number: {exc}. Not defaulted to 0 and not stored as NULL - "
            f"a tonnage this module cannot read is neither a tonnage of none nor an unreported "
            f"week."
        ) from exc


def movement_from(record: dict) -> LockMovement:
    """One Socrata record into a LockMovement, or raise naming what the record carries.

    The three key fields are REQUIRED and raise when absent; the measure is OPTIONAL and becomes
    None. That asymmetry is deliberate: a row that cannot be keyed is a row nothing can ever
    correct or supersede, while a row with no measure is an ordinary unreported week.

    `lock` IS PASSED THROUGH WITH ONLY WHITESPACE STRIPPED. No title-casing, no singularizing of
    `MS Locks 27`, no mapping to an internal id.
    """
    context = "lock movement record"

    # THREE CONDITIONS, KEPT APART (CLAUDE.md § 16). `optional_field` collapses an absent key and
    # an explicit null into ABSENT - both are USDA not reporting this lock-week - and hands
    # everything else to parse_tons, which raises on a value it cannot read.
    #
    # `record.get(FIELDS["tons"])` is the one-line version, it is one call shorter, and it is
    # FORBIDDEN: it collapses the unparseable case into the absent one, so a corrupt value would be
    # filed as a reporting gap. There are already 108 legitimate gaps for it to hide among, and
    # they sit on the three summary locks.
    published_tons = optional_field(record, FIELDS["tons"], context=context)

    return LockMovement(
        lock=str(required_field(record, FIELDS["lock"], context=context)).strip(),
        week_ending=parse_period_label(
            required_field(record, FIELDS["week_ending"], context=context), field="week_ending"
        ),
        commodity=str(required_field(record, FIELDS["commodity"], context=context)).strip(),
        # NOT `required_field`: an absent measure IS the unreported case, and it is the one field
        # in this module whose absence is not an error. The three key fields stay required - a row
        # that cannot be keyed can never be corrected or superseded, while a row with no measure
        # is an ordinary unreported week.
        tons=(None if published_tons is ABSENT else parse_tons(published_tons)),
    )


def parse_movements(records) -> list[LockMovement]:
    """Every record, INCLUDING the zero-tonnage weeks.

    There is no filter in this function and there must never be one. A `if movement.tons:`
    anywhere on this path drops both the unreported weeks and the reported-zero weeks, and the
    second kind is the signal.
    """
    return [movement_from(record) for record in records]


UPSERT_SQL = """
INSERT INTO lock_movements (lock, week_ending, commodity, tons)
VALUES {placeholders}
ON CONFLICT (lock, week_ending, commodity) DO UPDATE
    SET tons = EXCLUDED.tons
    WHERE lock_movements.tons IS DISTINCT FROM EXCLUDED.tons
RETURNING 1
"""


def _deduplicate(movements):
    by_key = {}
    for movement in movements:
        by_key[(movement.lock, movement.week_ending, movement.commodity)] = movement
    return list(by_key.values())


def upsert_movements(conn, movements) -> int:
    """Write movements, returning the number that ACTUALLY changed the database.

    `IS DISTINCT FROM`, which is also what makes a revision from NULL to 0 count as a change:
    `NULL = 0` is NULL and would compare as "no change", so a week going from unreported to
    reported-zero would be written and reported as nothing. That is the same distinction this
    whole module is about, appearing one more time in the SQL.
    """
    deduplicated = _deduplicate(movements)
    if not deduplicated:
        return 0

    written = 0
    for start in range(0, len(deduplicated), BATCH_SIZE):
        batch = deduplicated[start : start + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(batch))
        params: list = []
        for movement in batch:
            params.extend(
                [
                    movement.lock,
                    movement.week_ending,
                    movement.commodity,
                    movement.tons,
                ]
            )
        cursor = conn.execute(UPSERT_SQL.format(placeholders=placeholders), params)
        written += len(cursor.fetchall())

    return written


def latest_week(conn) -> date | None:
    """MAX(week_ending), or None when the table is empty. From the data, never a checkpoint."""
    row = conn.execute(f"SELECT max(week_ending) FROM {TABLE}").fetchone()
    return row[0] if row else None


def since_clause(start: date) -> str:
    """A SoQL `$where` restricting to periods at or after `start`. No timezone, as published."""
    return f"{FIELDS['week_ending']} >= '{start.isoformat()}T00:00:00.000'"


def ingest(conn, client: SocrataClient | None = None, today: date | None = None) -> int:
    """Fetch and write recent movements. Returns rows actually written."""
    client = SocrataClient() if client is None else client
    today = datetime.now(timezone.utc).date() if today is None else today

    dataset = socrata_client.resolve_dataset(conn, DATASET_KEY)

    newest = latest_week(conn)
    if newest is None:
        start = today - timedelta(weeks=COLD_START_WEEKS)
        logger.warning(
            "%s is EMPTY; polling only the last %d weeks. This job does not backfill - run "
            "`python3 -m app.ingest.usda_backfill --dataset %s` for history.",
            TABLE,
            COLD_START_WEEKS,
            DATASET_KEY,
        )
    else:
        start = newest - timedelta(weeks=OVERLAP_WEEKS)

    records = client.fetch_all(dataset, order=ORDER_COLUMN, where=since_clause(start))
    movements = parse_movements(records)
    written = upsert_movements(conn, movements)
    conn.commit()

    # THE TWO POPULATIONS ARE LOGGED SEPARATELY, never as one "no data" figure. A reported zero is
    # a measurement and an unreported week is not, and a combined count would hide the one
    # distinction this module is arranged around in the line an operator actually reads.
    logger.info(
        "%s: %d record(s) received from %s, %d row(s) written "
        "(%d reported zero tons, %d not reported)",
        TABLE,
        len(records),
        start.isoformat(),
        written,
        sum(1 for m in movements if m.tons == 0),
        sum(1 for m in movements if m.tons is None),
    )
    return written


@job(JOB_NAME)
def usda_movements_ingest_job(
    url: str | None = None, client=None, today: date | None = None
) -> int:
    """The scheduled unit. Separate from the rates job - see usda_rates.usda_rates_ingest_job."""
    with session.writing(url) as conn:
        return ingest(conn, client=client, today=today)
