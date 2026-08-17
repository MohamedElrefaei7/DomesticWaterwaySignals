"""Barge freight rates: parsing, writing, and the weekly poll. THE TARGET.

Everything in Phase 3 is the leading side of a pair whose lagging side is this table. The output
contract (CLAUDE.md § 7) is a statement about what happened to barge rates after a stage move, so
this is the series those claims are checked against.

Shaped like usgs_daily_ingest.py on purpose - parse, upsert, resume from the data, one @job - so a
reader who has found their way around one ingest client finds this one where they expect it.

THE HORIZON IS A PROPERTY OF THE DATASET, NOT OF THE RECORD
-----------------------------------------------------------
USDA publishes THREE datasets, one per horizon, with identical field lists. It does not publish
one dataset with a horizon column - which is what Phase 4 assumed and what migration 0016
corrected against a measurement.

So `horizon` is assigned by WHICH DATASET a row came from, through HORIZON_BY_DATASET_KEY below,
and is never read out of a record. The mapping is total in both directions and a test asserts it,
because the failure mode of an incomplete one is a fourth rates dataset quietly defaulting to
`nearby` and mixing two different facts under one key - a series that is wrong in a way no
aggregate reveals.

AN ABSENT RATE IS A FACT ABOUT THE RIVER
----------------------------------------
MEASURED: 774 of 8,260 nearby records carry NO `rate` FIELD AT ALL - not a null value, the key is
simply not there. 661 of those fall in December-March and 729 are on the two upper segments. It is
WINTER NAVIGATION CLOSURE: there is no rate to publish when no barges move.

So the row is written with a NULL rate, always. Skipping it would make the closure invisible - the
series would just have no January, which is indistinguishable from an ingest that missed it - and
Phase 5's seasonal baseline would learn a January that never closes.

The three conditions are kept apart (`socrata_client.optional_field`): an absent key and an
explicit null both mean "no rate published"; a value that will not parse RAISES. Collapsing the
third into the first with `record.get("rate")` would file a corrupt value as a winter closure,
which is a completely ordinary thing for this column to say and therefore invisible.

THREE THINGS THIS MODULE REFUSES TO DO
--------------------------------------
1. CONVERT THE UNIT. `pct_of_tariff` is stored exactly as published: `582.1428` stays 582.1428,
   never 5.821428 and never 582.14. Dividing by 100 or rounding in ingest is a modelling decision
   in the wrong layer, and its symptom is a chart that looks fine and a threshold two orders of
   magnitude out.

2. COALESCE A MISSING RATE TO ZERO, OR DROP THE ROW. A zero would claim barge freight was free
   that week, in a column every average reads. NULL says what is true: nothing was published.

3. DERIVE `rate_month` INTO AN OFFSET. The forward datasets publish the calendar month the quoted
   rate applies to. It is stored as that month. Turning it into "months ahead" is a derivation,
   and derivations belong downstream of ingest.
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
    SocrataError,
    optional_field,
    parse_period_label,
    required_field,
)
from app.orchestration.job import job

logger = logging.getLogger(__name__)

JOB_NAME = "usda_rates_ingest"
TABLE = "barge_rates"

# ---------------------------------------------------------------------------------------------
# THE ONE PLACE A HORIZON IS ASSIGNED.
# ---------------------------------------------------------------------------------------------
#
# Measured 2026-08-14: three datasets, identical field lists, ~8,260 rows each spanning
# 2004-01-07 to 2026-08-11.
#
#   deqi-uken  barge_rates_nearby   nearby
#   svms-9yya  barge_rates_1month   1_month
#   uuhv-5etw  barge_rates_3month   3_month
#
# The ids themselves live in `usda_datasets` (migration 0016), not here: an id is a human-resolved
# fact about the world, and this is a mapping between two of this project's own names.
#
# ONE PLACE, and tests/ingest/test_usda_field_maps.py asserts the mapping is TOTAL AND INJECTIVE
# against both the dataset keys and 0014's horizon CHECK. A second copy of this - a per-call
# default, a `.get(key, "nearby")`, a branch in the backfill - is how a fourth rates dataset would
# land silently in an existing series.
HORIZON_BY_DATASET_KEY: dict[str, str] = {
    "barge_rates_nearby": "nearby",
    "barge_rates_1month": "1_month",
    "barge_rates_3month": "3_month",
}

# The order the weekly job fetches them in. A tuple rather than a set so the log reads the same way
# every run.
DATASET_KEYS: tuple[str, ...] = tuple(HORIZON_BY_DATASET_KEY)

# The horizon vocabulary, taken FROM the mapping rather than restated beside it. Restating it is
# how the two drift, and the drift is invisible: both lists look right in isolation.
HORIZONS: frozenset[str] = frozenset(HORIZON_BY_DATASET_KEY.values())

# The two datasets that publish `rate_month`. Derived from the mapping, for the same reason.
FORWARD_DATASET_KEYS: frozenset[str] = frozenset(
    key for key, horizon in HORIZON_BY_DATASET_KEY.items() if horizon != "nearby"
)


class UnknownRatesDatasetError(SocrataError):
    """A dataset key with no horizon. Raised rather than defaulted.

    Its own class because the fix is specific: measure the new dataset, decide its horizon
    deliberately, and extend both HORIZON_BY_DATASET_KEY and 0014's CHECK in a migration. A
    ValueError here would read like a bad argument rather than like a missing decision.
    """


# ---------------------------------------------------------------------------------------------
# THE SOCRATA FIELD NAMES, MEASURED 2026-08-14 AGAINST THE LIVE API.
# ---------------------------------------------------------------------------------------------
#
# A verbatim record from deqi-uken:
#
#   {"date":"2026-08-11T00:00:00.000","week":"32","month":"8","year":"2026",
#    "location":"Cairo-Memphis","rate":"582.1428"}
#
# and from svms-9yya, which adds one field:
#
#   {"date":"2026-08-11T00:00:00.000","week":"32","month":"8","year":"2026",
#    "location":"Twin Cities","rate_month":"9","rate":"925"}
#
# EVERY NAME PHASE 4 ASSUMED WAS WRONG. `segment`, `week_ending`, `horizon` and
# `rate_pct_of_tariff` were all built from the shape the fixtures were written to, which Phase 4
# disclosed in writing. Every read went through `required_field`, so the wrongness arrived as an
# exception naming the fields a record actually carries.
#
# TWO COLUMN NAMES DELIBERATELY DIVERGE FROM THE SOURCE, and both are arguments, not oversights:
#
#   date -> week_ending    The source calls it `date`. It is the week-ending LABEL, and
#                          `week_ending` says what the value MEANS where `date` says only what
#                          type it is. "Rename the column to match the source" is the
#                          reasonable-looking tidy this comment exists to answer: it would trade a
#                          name that carries a fact for one that carries none, and every consumer
#                          would then have to know that `date` means the end of the week rather
#                          than a day of observation.
#
#   rate -> pct_of_tariff  Same argument. The published unit is percent of tariff (0014), the
#                          value is stored unconverted and unrounded, and `rate` alone would let a
#                          reader assume dollars.
#
# WHAT IS DELIBERATELY NOT MAPPED: `week`, `month` and `year`. All three are derivable from `date`,
# and storing them would be three more copies of one fact that can disagree with it after a
# revision. The project has a rule about two records of the same thing; this is that rule at
# column scale.
FIELDS = {
    "location": "location",
    "week_ending": "date",
    "pct_of_tariff": "rate",
    "rate_month": "rate_month",
}

# The column paging is ordered by. Every Socrata query in this project carries one (§ 16).
#
# NOTE FOR LIVE VERIFICATION: `date` is also a SoQL type name. If the service rejects it as a bare
# identifier in `$order` or `$where`, the rejection arrives as an ERROR DOCUMENT and
# socrata_client.parse_page raises SocrataResponseError carrying Socrata's own message - loudly,
# not as an empty page. The fix would be to quote it (`date`) in one place, here and in
# since_clause. Left unquoted because the measured queries against these datasets used bare
# identifiers, and this project does not guess syntax it has not seen.
ORDER_COLUMN = FIELDS["week_ending"]

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


def horizon_for(dataset_key: str) -> str:
    """The horizon this dataset publishes, or raise. THE ONLY WAY A HORIZON IS OBTAINED."""
    horizon = HORIZON_BY_DATASET_KEY.get(dataset_key)
    if horizon is None:
        raise UnknownRatesDatasetError(
            f"no horizon mapped for rates dataset key {dataset_key!r}. Known keys: "
            f"{sorted(HORIZON_BY_DATASET_KEY)}.\n"
            f"  NOT DEFAULTED TO 'nearby'. USDA publishes one dataset per horizon, so an "
            f"unmapped key is a dataset nobody has decided the meaning of - and filing its rows "
            f"under an existing horizon mixes two different facts under one primary key, which "
            f"produces a series that is wrong in a way no aggregate reveals.\n"
            f"  A fourth rates dataset needs its horizon decided deliberately here AND admitted "
            f"by barge_rates_horizon_known in a new migration."
        )
    return horizon


@dataclass(frozen=True)
class BargeRate:
    """One published weekly rate.

    `week_ending` is a CALENDAR DATE carrying no timezone, `pct_of_tariff` is a Decimal carrying
    the published digits, and `rate_month` is an int or None. Every type is chosen to make the
    refusals structural: there is no timezone to apply, no float to round, and no offset to
    compute.
    """

    location: str
    week_ending: date
    horizon: str
    # NONE WHEN USDA PUBLISHED NO RATE - a winter closure week, 774 of 8,260 nearby records.
    # The row still exists, and that is the point: a skipped row makes the closure invisible while
    # a NULL one states it. Never 0, which would claim the freight was free.
    pct_of_tariff: Decimal | None
    # NONE ON NEARBY ROWS, AND THAT NONE IS CORRECT RATHER THAN MISSING. The nearby dataset
    # publishes no such field; synthesizing one from the publication date would invent a quoted
    # month USDA never quoted (migration 0016's rate_month/horizon CHECK is the same guard at the
    # database).
    rate_month: int | None


def parse_rate(raw) -> Decimal:
    """A PUBLISHED percent-of-tariff, EXACTLY as published. Never called for an absent one.

    Decimal via str, never float: `float('112.5')` is fine but `float('582.1428')` is not exactly
    582.1428, and a numeric column fed a float inherits the binary artefact. The published digits
    are the fact - and the measured data really does carry four decimal places, so this is not a
    theoretical precision argument.

    EVERYTHING THIS FUNCTION SEES IS SUPPOSED TO BE A NUMBER. Absence is handled before the call,
    by `optional_field`, so anything arriving here that will not parse is a DATA ERROR and raises.
    That includes the empty string: USDA expresses "no rate" by omitting the key, measured, and
    accepting a blank cell as a second spelling of absence would let a genuinely corrupt value
    pass as a winter closure - the one failure this split exists to prevent.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise MalformedResponseError(
            f"pct_of_tariff is {raw!r} - present but blank. NOT read as an unpublished rate: this "
            f"source omits the `rate` key entirely when it publishes none (774 of 8,260 records, "
            f"migration 0017), so a blank value is a different condition and a suspect one. "
            f"Storing it as NULL would hide it among the legitimate closure weeks."
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


def parse_rate_month(raw) -> int:
    """The published calendar month, 1-12. NOT an offset from anything.

    The samples show `rate_month` 9 and 11 against a publication month of 8 - so this is not
    "months ahead" wearing a month's clothes, and subtracting the publication month here would
    bake a derivation into ingest that the feature layer is the right place to make.
    """
    try:
        month = int(Decimal(str(raw).strip()))
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise MalformedResponseError(
            f"rate_month {raw!r} is not an integer: {exc}. Not defaulted and not dropped - a "
            f"forward rate whose quoted month cannot be read is not a nearby rate."
        ) from exc

    if not 1 <= month <= 12:
        raise MalformedResponseError(
            f"rate_month {raw!r} is not a calendar month (1-12). If USDA has started publishing "
            f"an OFFSET in this field rather than a month, that is a measurement to take and a "
            f"deliberate change - not a value to coerce."
        )
    return month


def rate_from(record: dict, *, dataset_key: str) -> BargeRate:
    """One Socrata record into a BargeRate, or raise naming what the record carries.

    `dataset_key` is REQUIRED and keyword-only. That is the horizon's only source: there is no
    signature of this function that lets a caller obtain a rate without stating which dataset it
    came from, which is what keeps decision 1 structural rather than conventional.
    """
    context = f"barge rate record ({dataset_key})"
    horizon = horizon_for(dataset_key)

    if dataset_key in FORWARD_DATASET_KEYS:
        # REQUIRED, not `.get`. A forward dataset that stopped publishing `rate_month` - or that
        # renamed it - would otherwise write NULLs indistinguishable from nearby's legitimate
        # ones, in the one column where NULL is a normal value. That is exactly the shape of
        # failure `required_field` exists for.
        rate_month = parse_rate_month(
            required_field(record, FIELDS["rate_month"], context=context)
        )
    else:
        # NOT SYNTHESIZED. The nearby dataset publishes no quoted month, and None is the complete
        # and correct answer.
        rate_month = None

    published_rate = optional_field(record, FIELDS["pct_of_tariff"], context=context)

    return BargeRate(
        # `location` and `week_ending` stay REQUIRED. They key the row, and a record carrying
        # neither a rate nor a location is not a closure week - it is unkeyable.
        location=str(required_field(record, FIELDS["location"], context=context)).strip(),
        week_ending=parse_period_label(
            required_field(record, FIELDS["week_ending"], context=context), field="week_ending"
        ),
        horizon=horizon,
        # `optional_field`, NOT `required_field`: this source legitimately omits the key.
        # ABSENT (missing key or explicit null) becomes NULL; anything else goes to parse_rate,
        # which raises on a value it cannot read rather than filing it as a closure week.
        pct_of_tariff=(None if published_rate is ABSENT else parse_rate(published_rate)),
        rate_month=rate_month,
    )


def parse_rates(records, *, dataset_key: str) -> list[BargeRate]:
    return [rate_from(record, dataset_key=dataset_key) for record in records]


UPSERT_SQL = """
INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff, rate_month)
VALUES {placeholders}
ON CONFLICT (location, week_ending, horizon) DO UPDATE
    SET pct_of_tariff = EXCLUDED.pct_of_tariff,
        rate_month = EXCLUDED.rate_month
    WHERE (barge_rates.pct_of_tariff, barge_rates.rate_month)
       IS DISTINCT FROM (EXCLUDED.pct_of_tariff, EXCLUDED.rate_month)
RETURNING 1
"""


def _deduplicate(rates):
    """Collapse repeated natural keys within one batch, keeping the last occurrence.

    Correctness, not tidiness: Postgres rejects the whole statement with "ON CONFLICT DO UPDATE
    command cannot affect row a second time" when one INSERT carries a conflict key twice.
    """
    by_key = {}
    for rate in rates:
        by_key[(rate.location, rate.week_ending, rate.horizon)] = rate
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
        placeholders = ", ".join(["(%s, %s, %s, %s, %s)"] * len(batch))
        params: list = []
        for rate in batch:
            params.extend(
                [
                    rate.location,
                    rate.week_ending,
                    rate.horizon,
                    rate.pct_of_tariff,
                    rate.rate_month,
                ]
            )
        cursor = conn.execute(UPSERT_SQL.format(placeholders=placeholders), params)
        written += len(cursor.fetchall())

    return written


def latest_week(conn, horizon: str | None = None) -> date | None:
    """MAX(week_ending), or None when there is nothing. The resume point, from the data.

    PER HORIZON when one is given, because the three datasets are three independent publications:
    a corridor-wide MAX would let the freshest of the three decide where the other two resume,
    and a dataset that fell a month behind would never be asked for the weeks it is missing.
    """
    if horizon is None:
        row = conn.execute(f"SELECT max(week_ending) FROM {TABLE}").fetchone()
    else:
        row = conn.execute(
            f"SELECT max(week_ending) FROM {TABLE} WHERE horizon = %s", (horizon,)
        ).fetchone()
    return row[0] if row else None


def since_clause(start: date) -> str:
    """A SoQL `$where` restricting to periods at or after `start`.

    A floating-timestamp comparison, written with the same explicit midnight the published labels
    carry. No timezone: Socrata's floating timestamps have none, and introducing one here would
    ask a different question than the one the labels answer.
    """
    return f"{FIELDS['week_ending']} >= '{start.isoformat()}T00:00:00.000'"


def ingest_dataset(
    conn, dataset_key: str, client: SocrataClient, today: date
) -> int:
    """Fetch and write recent rates for ONE of the three horizon datasets."""
    horizon = horizon_for(dataset_key)
    dataset = socrata_client.resolve_dataset(conn, dataset_key)

    newest = latest_week(conn, horizon)
    if newest is None:
        start = today - timedelta(weeks=COLD_START_WEEKS)
        logger.warning(
            "%s holds no %s rows; polling only the last %d weeks. This job does not backfill - "
            "run `python3 -m app.ingest.usda_backfill --dataset %s` for history.",
            TABLE,
            horizon,
            COLD_START_WEEKS,
            dataset_key,
        )
    else:
        start = newest - timedelta(weeks=OVERLAP_WEEKS)

    records = client.fetch_all(dataset, order=ORDER_COLUMN, where=since_clause(start))
    rates = parse_rates(records, dataset_key=dataset_key)
    written = upsert_rates(conn, rates)

    logger.info(
        "%s/%s: %d record(s) received from %s, %d row(s) written",
        TABLE,
        horizon,
        len(records),
        start.isoformat(),
        written,
    )
    return written


def ingest(conn, client: SocrataClient | None = None, today: date | None = None) -> int:
    """Fetch and write recent rates from ALL THREE datasets. Returns rows actually written.

    Resumes from MAX(week_ending) per horizon in the data minus the revision overlap - never a
    checkpoint (CLAUDE.md § 14).

    THREE DATASETS IN ONE SCHEDULED UNIT, AND THAT IS NOT THE THING CLAUDE.md § 4 FORBIDS. The
    rule is one @job per scheduled unit, never nested; this is one scheduled unit. The three
    horizons are one logical publication on one weekly schedule, `rows_written` is meaningful
    summed across them, and a reader asking "did the rates land this week" is asking one question.
    Splitting them into three jobs would produce three job_runs rows nobody reads separately and
    three heartbeat entries for one table.

    That is the opposite case from rates-versus-movements, which ARE separate jobs: those are two
    independent sources, and one job over both would produce a status that is the AND of two
    things with a heartbeat unable to say which went quiet.
    """
    client = SocrataClient() if client is None else client
    today = datetime.now(timezone.utc).date() if today is None else today

    written = sum(
        ingest_dataset(conn, dataset_key, client, today) for dataset_key in DATASET_KEYS
    )
    conn.commit()

    logger.info("%s: %d row(s) written across %d dataset(s)", TABLE, written, len(DATASET_KEYS))
    return written


@job(JOB_NAME)
def usda_rates_ingest_job(url: str | None = None, client=None, today: date | None = None) -> int:
    """The scheduled unit. Returns rows written, which @job records as rows_written.

    SEPARATE FROM THE MOVEMENTS JOB, not one job fetching both sources. CLAUDE.md § 4 requires one
    @job per scheduled unit, and the operational reason is sharper than the rule: a failure
    fetching movements must not mark rates as failed. Two SOURCES in one job produce one job_runs
    row whose status is the AND of two independent things, and the heartbeat then cannot say which
    one went quiet. The three rates datasets are not two sources - see ingest().
    """
    with session.writing(url) as conn:
        return ingest(conn, client=client, today=today)
