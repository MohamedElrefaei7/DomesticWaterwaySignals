"""Barged grain movements through the locks: parsing, writing, and the weekly poll.

The volume side of the target. Sibling of usda_rates.py and deliberately the same shape.

ZERO IS A VALUE. NULL IS THE ABSENCE OF ONE.
--------------------------------------------
This module exists to keep those two apart, and both directions of collapsing them are one line
long:

    skipping rows where barges == 0    deletes the event this project studies. During the 2022
                                       low-water event, near-zero movement IS the signal - a tow
                                       that could not sail is the physical fact behind the thesis.
                                       The gap it leaves is indistinguishable from a week nobody
                                       reported.

    coalescing NULL to 0               invents a surveyed zero out of silence, in the same column,
                                       in the opposite direction. Every average over the series is
                                       then dragged toward zero by weeks nobody measured.

`barges` and `tons` are nullable in 0015 for exactly this reason, and the CHECK is `>= 0` rather
than `> 0` so a real reported zero satisfies it. tests/ingest/test_usda_movements.py holds both
behaviours at once, because two separate tests can each be satisfied by one wrong implementation.
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

# PROVISIONAL Socrata column names - see the identical note in usda_rates.py. Confirmed at live
# verification step 3, when the dataset is resolved. Every read goes through `required_field`, so
# a wrong name here fails loudly on the first record rather than writing NULLs.
FIELDS = {
    "lock_id": "lock_id",
    "week_ending": "week_ending",
    "grain_type": "grain_type",
    "direction": "direction",
    "barges": "barges",
    "tons": "tons",
}

ORDER_COLUMN = FIELDS["week_ending"]

OVERLAP_WEEKS = 8
COLD_START_WEEKS = 12
BATCH_SIZE = 500


@dataclass(frozen=True)
class LockMovement:
    """One published weekly movement through one lock.

    `barges` and `tons` are OPTIONAL, and None means "not reported" while 0 means "reported as
    none". The Optional is the whole point of the type: a plain int would force a value for a week
    that has none, and the only values available to force are zero and a lie.
    """

    lock_id: str
    week_ending: date
    grain_type: str
    direction: str
    barges: int | None
    tons: Decimal | None


def parse_optional_int(raw, *, field: str) -> int | None:
    """A reported count, or None when nothing was reported. 0 IS A COUNT.

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
        return int(Decimal(str(raw)))
    except (InvalidOperation, ValueError) as exc:
        raise MalformedResponseError(
            f"{field} {raw!r} is not an integer: {exc}. Not defaulted to 0 - a count this module "
            f"cannot read is not a count of none."
        ) from exc


def parse_optional_decimal(raw, *, field: str) -> Decimal | None:
    """A reported tonnage, or None. Same reasoning as parse_optional_int, same trap."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        raw = text
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise MalformedResponseError(
            f"{field} {raw!r} is not a number: {exc}. Not defaulted to 0."
        ) from exc


def movement_from(record: dict) -> LockMovement:
    """One Socrata record into a LockMovement, or raise naming what the record carries.

    The four key fields are REQUIRED and raise when absent; the two measures are OPTIONAL and
    become None. That asymmetry is deliberate: a row that cannot be keyed is a row nothing can
    ever correct or supersede, while a row with no measure is an ordinary unreported week.
    """
    context = "lock movement record"
    return LockMovement(
        lock_id=str(required_field(record, FIELDS["lock_id"], context=context)).strip(),
        week_ending=parse_period_label(
            required_field(record, FIELDS["week_ending"], context=context), field="week_ending"
        ),
        grain_type=str(required_field(record, FIELDS["grain_type"], context=context)).strip(),
        direction=str(required_field(record, FIELDS["direction"], context=context)).strip(),
        # `.get`, not `required_field`: an absent measure IS the unreported case, and it is the
        # one case in this module where a missing field is not an error.
        barges=parse_optional_int(record.get(FIELDS["barges"]), field="barges"),
        tons=parse_optional_decimal(record.get(FIELDS["tons"]), field="tons"),
    )


def parse_movements(records) -> list[LockMovement]:
    """Every record, INCLUDING the zero-barge weeks.

    There is no filter in this function and there must never be one. A `if movement.barges:`
    anywhere on this path drops both the unreported weeks and the reported-zero weeks, and the
    second kind is the signal.
    """
    return [movement_from(record) for record in records]


UPSERT_SQL = """
INSERT INTO lock_movements (lock_id, week_ending, grain_type, direction, barges, tons)
VALUES {placeholders}
ON CONFLICT (lock_id, week_ending, grain_type, direction) DO UPDATE
    SET barges = EXCLUDED.barges,
        tons = EXCLUDED.tons
    WHERE (lock_movements.barges, lock_movements.tons)
       IS DISTINCT FROM (EXCLUDED.barges, EXCLUDED.tons)
RETURNING 1
"""


def _deduplicate(movements):
    by_key = {}
    for movement in movements:
        by_key[
            (movement.lock_id, movement.week_ending, movement.grain_type, movement.direction)
        ] = movement
    return list(by_key.values())


def upsert_movements(conn, movements) -> int:
    """Write movements, returning the number that ACTUALLY changed the database.

    `IS DISTINCT FROM` over the pair, which is also what makes a revision from NULL to 0 count as
    a change: `NULL = 0` is NULL and would compare as "no change", so a week going from
    unreported to reported-zero would be written and reported as nothing. That is the same
    distinction this whole module is about, appearing one more time in the SQL.
    """
    deduplicated = _deduplicate(movements)
    if not deduplicated:
        return 0

    written = 0
    for start in range(0, len(deduplicated), BATCH_SIZE):
        batch = deduplicated[start : start + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(batch))
        params: list = []
        for movement in batch:
            params.extend(
                [
                    movement.lock_id,
                    movement.week_ending,
                    movement.grain_type,
                    movement.direction,
                    movement.barges,
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
        "%s: %d record(s) received from %s, %d row(s) written (%d reported zero barges)",
        TABLE,
        len(records),
        start.isoformat(),
        written,
        sum(1 for m in movements if m.barges == 0),
    )
    return written


@job(JOB_NAME)
def usda_movements_ingest_job(
    url: str | None = None, client=None, today: date | None = None
) -> int:
    """The scheduled unit. Separate from the rates job - see usda_rates.usda_rates_ingest_job."""
    with db.connection(url) as conn:
        return ingest(conn, client=client, today=today)
