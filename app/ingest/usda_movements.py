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
from app.ingest import socrata_client
from app.ingest.socrata_client import (
    MalformedResponseError,
    SocrataClient,
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

    `tons` is OPTIONAL, and None means "not reported" while 0 means "reported as none". The
    Optional is the whole point of the type: a plain Decimal would force a value for a week that
    has none, and the only values available to force are zero and a lie.

    THERE IS NO `direction` AND NO `barges` FIELD HERE, and their absence is the schema decision
    from migration 0016 made structural. Neither is published.
    """

    lock: str
    week_ending: date
    commodity: str
    tons: Decimal | None


def parse_optional_decimal(raw, *, field: str) -> Decimal | None:
    """A reported tonnage, or None when nothing was reported. 0 IS A TONNAGE.

    The ordering of these branches is the decision. `if not raw: return None` would be shorter and
    would map the string '0' and the integer 0 to None - turning every reported zero into a
    missing week, which is precisely the observation the low-water analysis needs.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            # An empty string is Socrata's "no value in this cell", not a zero.
            return None
        raw = text
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise MalformedResponseError(
            f"{field} {raw!r} is not a number: {exc}. Not defaulted to 0 - a tonnage this module "
            f"cannot read is not a tonnage of none."
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
    return LockMovement(
        lock=str(required_field(record, FIELDS["lock"], context=context)).strip(),
        week_ending=parse_period_label(
            required_field(record, FIELDS["week_ending"], context=context), field="week_ending"
        ),
        commodity=str(required_field(record, FIELDS["commodity"], context=context)).strip(),
        # `.get`, not `required_field`: an absent measure IS the unreported case, and it is the
        # one case in this module where a missing field is not an error.
        tons=parse_optional_decimal(record.get(FIELDS["tons"]), field="tons"),
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

    logger.info(
        "%s: %d record(s) received from %s, %d row(s) written (%d reported zero tons)",
        TABLE,
        len(records),
        start.isoformat(),
        written,
        sum(1 for m in movements if m.tons == 0),
    )
    return written


@job(JOB_NAME)
def usda_movements_ingest_job(
    url: str | None = None, client=None, today: date | None = None
) -> int:
    """The scheduled unit. Separate from the rates job - see usda_rates.usda_rates_ingest_job."""
    with db.connection(url) as conn:
        return ingest(conn, client=client, today=today)
