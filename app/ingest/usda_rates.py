"""Barge freight rates: parsing, writing, and the weekly poll. THE TARGET.

Everything in Phase 3 is the leading side of a pair whose lagging side is this table. The output
contract (CLAUDE.md § 7) is a statement about what happened to barge rates after a stage move, so
this is the series those claims are checked against.

Shaped like usgs_daily_ingest.py on purpose - parse, upsert, resume from the data, one @job - so a
reader who has found their way around one ingest client finds this one where they expect it.

THREE THINGS THIS MODULE REFUSES TO DO
--------------------------------------
1. CONVERT THE UNIT. `pct_of_tariff` is stored exactly as published: 112.5 stays 112.5, never
   1.125 and never 113. Dividing by 100 in ingest is a modelling decision in the wrong layer, and
   its symptom is a chart that looks fine and a threshold two orders of magnitude out.

2. TREAT A MISSING FIELD AS AN ABSENT VALUE. A record without the rate field raises naming the
   fields it does carry. The field names below are PROVISIONAL - the datasets are unresolved
   (migration 0013) - and a mapping that silently produced NULLs would be a client reporting
   success over an empty table.

3. GUESS A HORIZON. A published label this module does not recognise raises rather than being
   filed under `nearby`. Nearby and three-month-forward rates move differently, and mixing them
   under one key produces a series that is wrong in a way no aggregate reveals.
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

JOB_NAME = "usda_rates_ingest"
TABLE = "barge_rates"
DATASET_KEY = "barge_rates"

# THE SOCRATA COLUMN NAMES, AND THEY ARE PROVISIONAL.
#
# The dataset is unresolved (migration 0013 seeds a NULL id), so these names come from the shape
# the fixtures were written to and NOT from the live catalog. They are collected here, in one
# dict, precisely so that confirming them at live verification step 3 is one edit rather than a
# search - and every read of them goes through `required_field`, which raises naming the fields a
# record actually has. A wrong name here fails loudly on the first record; it never writes NULLs.
FIELDS = {
    "segment": "segment",
    "week_ending": "week_ending",
    "horizon": "horizon",
    "pct_of_tariff": "rate_pct_of_tariff",
}

# The column paging is ordered by. Every Socrata query in this project carries one (§ 16).
ORDER_COLUMN = FIELDS["week_ending"]

# PUBLISHED LABEL -> THE KEY VOCABULARY 0014's CHECK CONSTRAINT ENFORCES.
#
# Normalizing here is not the unit conversion refused above: the horizon is part of the primary
# key, and a key vocabulary has to be closed for the constraint to mean anything. What matters is
# that the mapping is EXPLICIT and CLOSED - an unrecognised label raises. Defaulting to 'nearby'
# would be a silent misfiling of the exact distinction the key exists to preserve.
HORIZON_LABELS = {
    "nearby": "nearby",
    "1 month forward": "1_month",
    "1-month forward": "1_month",
    "3 month forward": "3_month",
    "3-month forward": "3_month",
}

# How far back the weekly poll reaches beyond the newest week it already holds.
#
# EIGHT WEEKS, because USDA REVISES published weeks. The upsert makes the overlap free - a
# re-fetched unchanged row writes nothing and counts nothing (CLAUDE.md § 14) - so the cost is one
# slightly larger query per week and the benefit is that a correction issued a month later lands.
OVERLAP_WEEKS = 8

# What the poll asks for when the table is empty. Not the dataset's first period: that would have
# a weekly job attempt a full backfill every week, and max_instances=1 would leave it permanently
# running rather than either working or broken. The backfill is a separate CLI.
COLD_START_WEEKS = 12

BATCH_SIZE = 500


@dataclass(frozen=True)
class BargeRate:
    """One published weekly rate.

    `week_ending` is a CALENDAR DATE carrying no timezone, and `pct_of_tariff` is a Decimal
    carrying the published digits. Both types are chosen to make the two refusals above
    structural: there is no timezone to apply and no float to round.
    """

    segment: str
    week_ending: date
    horizon: str
    pct_of_tariff: Decimal


def parse_horizon(raw) -> str:
    """A published horizon label into the key vocabulary, or raise."""
    if not isinstance(raw, str) or not raw.strip():
        raise MalformedResponseError(
            f"horizon is {raw!r}, expected a published label. A rate with no horizon cannot be "
            f"keyed - the same week carries a nearby and two forward rates, and they are "
            f"different facts (migration 0014)."
        )

    horizon = HORIZON_LABELS.get(raw.strip().lower())
    if horizon is None:
        raise MalformedResponseError(
            f"unrecognised horizon label {raw!r}. Known labels: {sorted(HORIZON_LABELS)}.\n"
            f"  NOT defaulted to 'nearby': nearby and forward rates move differently, and filing "
            f"one under the other produces a series that is wrong in a way no aggregate reveals.\n"
            f"  If USDA publishes a label this project has not seen, add it here deliberately - "
            f"and if it is a genuinely new horizon, it needs a migration extending 0014's CHECK."
        )
    return horizon


def parse_rate(raw) -> Decimal:
    """The published percent-of-tariff, EXACTLY as published.

    Decimal via str, never float: `float('112.5')` is fine but `float('1050.10')` is not exactly
    1050.10, and a numeric column fed a float inherits the binary artefact. The published digits
    are the fact.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise MalformedResponseError(
            "pct_of_tariff is empty. An unpublished rate is not a zero and is not a NULL in this "
            "table - the row simply should not exist (migration 0014 makes the column NOT NULL)."
        )
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise MalformedResponseError(
            f"pct_of_tariff {raw!r} is not a number: {exc}"
        ) from exc

    if value <= 0:
        raise MalformedResponseError(
            f"pct_of_tariff {raw!r} is not positive. A percent of tariff is a positive quantity; "
            f"a zero here is what an empty field becomes when something coerces one, and 0014's "
            f"CHECK would reject it at the database anyway."
        )
    return value


def rate_from(record: dict) -> BargeRate:
    """One Socrata record into a BargeRate, or raise naming what the record carries."""
    context = "barge rate record"
    return BargeRate(
        segment=str(required_field(record, FIELDS["segment"], context=context)).strip(),
        week_ending=parse_period_label(
            required_field(record, FIELDS["week_ending"], context=context), field="week_ending"
        ),
        horizon=parse_horizon(required_field(record, FIELDS["horizon"], context=context)),
        pct_of_tariff=parse_rate(
            required_field(record, FIELDS["pct_of_tariff"], context=context)
        ),
    )


def parse_rates(records) -> list[BargeRate]:
    return [rate_from(record) for record in records]


UPSERT_SQL = """
INSERT INTO barge_rates (segment, week_ending, horizon, pct_of_tariff)
VALUES {placeholders}
ON CONFLICT (segment, week_ending, horizon) DO UPDATE
    SET pct_of_tariff = EXCLUDED.pct_of_tariff
    WHERE barge_rates.pct_of_tariff IS DISTINCT FROM EXCLUDED.pct_of_tariff
RETURNING 1
"""


def _deduplicate(rates):
    """Collapse repeated natural keys within one batch, keeping the last occurrence.

    Correctness, not tidiness: Postgres rejects the whole statement with "ON CONFLICT DO UPDATE
    command cannot affect row a second time" when one INSERT carries a conflict key twice.
    """
    by_key = {}
    for rate in rates:
        by_key[(rate.segment, rate.week_ending, rate.horizon)] = rate
    return list(by_key.values())


def upsert_rates(conn, rates) -> int:
    """Write rates, returning the number that ACTUALLY changed the database.

    `DO UPDATE ... WHERE ... IS DISTINCT FROM`, counted from RETURNING (CLAUDE.md § 14). The
    eight-week revision overlap therefore reports genuine revisions and nothing else, and a rerun
    over unchanged weeks reports 0.

    `DO NOTHING` is the trap here, as it was on the reading tables: USDA republishes weeks with
    corrections, and DO NOTHING would freeze the first-published number permanently and silently.
    """
    deduplicated = _deduplicate(rates)
    if not deduplicated:
        return 0

    written = 0
    for start in range(0, len(deduplicated), BATCH_SIZE):
        batch = deduplicated[start : start + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(batch))
        params: list = []
        for rate in batch:
            params.extend([rate.segment, rate.week_ending, rate.horizon, rate.pct_of_tariff])
        cursor = conn.execute(UPSERT_SQL.format(placeholders=placeholders), params)
        written += len(cursor.fetchall())

    return written


def latest_week(conn) -> date | None:
    """MAX(week_ending), or None when the table is empty. The resume point, from the data."""
    row = conn.execute(f"SELECT max(week_ending) FROM {TABLE}").fetchone()
    return row[0] if row else None


def since_clause(start: date) -> str:
    """A SoQL `$where` restricting to periods at or after `start`.

    A floating-timestamp comparison, written with the same explicit midnight the published labels
    carry. No timezone: Socrata's floating timestamps have none, and introducing one here would
    ask a different question than the one the labels answer.
    """
    return f"{FIELDS['week_ending']} >= '{start.isoformat()}T00:00:00.000'"


def ingest(conn, client: SocrataClient | None = None, today: date | None = None) -> int:
    """Fetch and write recent rates. Returns rows actually written.

    Resumes from MAX(week_ending) in the data minus the revision overlap - never a checkpoint
    (CLAUDE.md § 14).
    """
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
    rates = parse_rates(records)
    written = upsert_rates(conn, rates)
    conn.commit()

    logger.info(
        "%s: %d record(s) received from %s, %d row(s) written",
        TABLE,
        len(records),
        start.isoformat(),
        written,
    )
    return written


@job(JOB_NAME)
def usda_rates_ingest_job(url: str | None = None, client=None, today: date | None = None) -> int:
    """The scheduled unit. Returns rows written, which @job records as rows_written.

    SEPARATE FROM THE MOVEMENTS JOB, not one job fetching both datasets. CLAUDE.md § 4 requires
    one @job per scheduled unit, and the operational reason is sharper than the rule: a failure
    fetching movements must not mark rates as failed. Two datasets in one job produce one
    job_runs row whose status is the AND of two independent things, and the heartbeat then cannot
    say which source went quiet.
    """
    with db.connection(url) as conn:
        return ingest(conn, client=client, today=today)
