"""The USDA backfill: the full published history of one dataset, in one paged read.

A CLI A HUMAN RUNS, never a scheduled job - same rule as the two USGS backfills (CLAUDE.md § 14).
It is far shorter than they are, and the reason is worth stating rather than leaving as an
apparent inconsistency:

  * The USGS backfills CHUNK BY WINDOW because the service will not return decades in one
    response and its failure mode when it declines is a truncated body rather than an error.
  * Socrata pages the response itself. The chunking is the API's, and this project's job is to
    page it correctly - terminate on an empty page, never a short one, and raise at the cap
    rather than returning a prefix (CLAUDE.md § 16, socrata_client).

So there is no window arithmetic here. There is also NO RESUME LOGIC, deliberately: these are
weekly series of tens of thousands of rows and one full read costs a handful of requests, so
resuming would add a second record of progress (CLAUDE.md § 14 warns about exactly that) to save
seconds. The upsert makes a repeat run free.

FOUR INGESTABLE DATASETS, NOT TWO. The rates series is published as three sibling datasets, one
per horizon (migration 0016), so `--dataset barge_rates_nearby` is one of four choices and the
default reads all four.

IT NEVER WRITES TO usda_datasets. Discovered period bounds are REPORTED for a human to reconcile
into a new numbered migration, the same rule the daily backfill follows for dv_record_start
(CLAUDE.md § 15). A backfill that corrected its own seed would destroy the evidence it started
from the wrong place.

WHAT IT DOES DO IS COMPARE ITSELF AGAINST THE SEED. `usda_datasets.source_row_count` holds the row
count the source reported when its id was resolved, and this run reports how many records the
pager actually returned beside it. LANDING FEWER ROWS THAN THE SOURCE REPORTED IS A TRUNCATION
SIGNAL - the exact failure CLAUDE.md § 16's first bullet describes, which otherwise reports success
with a plausible-looking count. The seeded number goes stale in the safe direction: these datasets
only grow, so it is a floor.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.ingest import socrata_client, usda_movements, usda_rates
from app.ingest.socrata_client import SocrataClient

logger = logging.getLogger(__name__)

# dataset key -> the module that parses and writes it.
#
# The three rates keys all map to usda_rates, which is the point of the horizon mapping: one
# parser, one table, three sources, and the horizon supplied by which key was asked for.
#
# `cost_indicators` is SEEDED IN 0016 WITH A REAL ID AND DELIBERATELY ABSENT HERE. It has no table
# and no cadence entry, and asking for it should say so rather than fail with a KeyError from
# somewhere deeper. Its id being resolved is not a promise that it is loaded.
INGESTORS = {
    **{key: usda_rates for key in usda_rates.DATASET_KEYS},
    usda_movements.DATASET_KEY: usda_movements,
}


def completeness_by_location(rows) -> list[tuple[str, int, int, float]]:
    """Per location: rows landed, rows with NO PUBLISHED RATE, and the percentage.

    REPORTED, NEVER ENFORCED. USDA omits the `rate` field entirely when the river is closed - 774
    of 8,260 nearby records, 36% of them at Twin Cities alone - so this is a legitimate 9% overall
    and any constraint or alert on it would either fire constantly or be set so loose it never
    fires. What a threshold cannot do, a printed number can: if USDA's publication behaviour
    changes, the shape of this table changes with it and somebody reading a backfill log sees it.

    Sorted by location so two runs are diffable. Returns an empty list for a dataset whose rows
    carry no rate concept - the movements backfill prints nothing rather than a table of zeros.
    """
    if not rows or not hasattr(rows[0], "pct_of_tariff"):
        return []

    landed: dict[str, int] = {}
    absent: dict[str, int] = {}
    for row in rows:
        landed[row.location] = landed.get(row.location, 0) + 1
        if row.pct_of_tariff is None:
            absent[row.location] = absent.get(row.location, 0) + 1

    return [
        (
            location,
            landed[location],
            absent.get(location, 0),
            100.0 * absent.get(location, 0) / landed[location],
        )
        for location in sorted(landed)
    ]


def seeded_row_count(conn, dataset_key: str) -> int | None:
    """`usda_datasets.source_row_count` for this key, or None if never measured.

    Read here rather than carried on socrata_client.Dataset: the count is this CLI's business
    only - nothing on the request path consults it - and the request-path model has no reason to
    grow a reporting field.
    """
    row = conn.execute(
        "SELECT source_row_count FROM usda_datasets WHERE dataset_key = %s", (dataset_key,)
    ).fetchone()
    return row[0] if row else None


def backfill(conn, dataset_key: str, client: SocrataClient | None = None) -> dict:
    """Read one dataset in full and write it. Returns what happened, measured rather than assumed.

    The report includes the discovered period bounds and the truncation comparison because those
    are this run's deliverable beyond the rows.
    """
    if dataset_key not in INGESTORS:
        raise ValueError(
            f"no ingest path for dataset key {dataset_key!r}. This commit ingests "
            f"{sorted(INGESTORS)}. 'cost_indicators' is seeded in migration 0016 with a real id "
            f"but has no table yet - adding it is a later commit's work, not a flag."
        )

    module = INGESTORS[dataset_key]
    client = SocrataClient() if client is None else client

    dataset = socrata_client.resolve_dataset(conn, dataset_key)
    records = client.fetch_all(dataset, order=module.ORDER_COLUMN)

    if module is usda_rates:
        # The horizon comes from the KEY, never from the records (migration 0016, decision 1).
        rows = module.parse_rates(records, dataset_key=dataset_key)
        written = module.upsert_rates(conn, rows)
        horizon = usda_rates.horizon_for(dataset_key)
    else:
        rows = module.parse_movements(records)
        written = module.upsert_movements(conn, rows)
        horizon = None
    conn.commit()

    periods = sorted({row.week_ending for row in rows})
    seeded_count = seeded_row_count(conn, dataset_key)
    return {
        "dataset_key": dataset_key,
        "horizon": horizon,
        "records_received": len(records),
        "rows_written": written,
        "first_period": periods[0] if periods else None,
        "last_period": periods[-1] if periods else None,
        "seeded_first_period": dataset.first_period,
        "seeded_last_period": dataset.last_period,
        "seeded_row_count": seeded_count,
        # Per-location completeness, for the log. Decision 4: visibility, not enforcement.
        "completeness": completeness_by_location(rows),
        # Compared against RECORDS RECEIVED rather than rows written: `rows_written` counts only
        # rows that changed the database, so a second run legitimately writes 0 and would look
        # like total truncation. What the seed is a floor for is what the pager returned.
        "short_of_seeded_count": (
            seeded_count is not None and len(records) < seeded_count
        ),
    }


def describe(result: dict) -> str:
    first = result["first_period"].isoformat() if result["first_period"] else "(none)"
    last = result["last_period"].isoformat() if result["last_period"] else "(none)"
    seeded_first = (
        result["seeded_first_period"].isoformat()
        if result["seeded_first_period"]
        else "NULL - not yet measured"
    )
    seeded_last = (
        result["seeded_last_period"].isoformat()
        if result["seeded_last_period"]
        else "NULL - not yet measured"
    )

    seeded_count = result["seeded_row_count"]
    if seeded_count is None:
        count_line = "seeded row count NULL - never measured, so no truncation check is possible"
    elif result["short_of_seeded_count"]:
        count_line = (
            f"*** SHORT: {result['records_received']} record(s) received against a seeded "
            f"{seeded_count}. THE PAGER TRUNCATED, or the source shrank. Do not treat this run as "
            f"a complete backfill (CLAUDE.md section 16)."
        )
    else:
        count_line = (
            f"{result['records_received']} record(s) received against a seeded floor of "
            f"{seeded_count} - not truncated"
        )

    horizon = f" [{result['horizon']}]" if result["horizon"] else ""

    lines = [
        f"{result['dataset_key']}{horizon}: {result['records_received']} record(s) received, "
        f"{result['rows_written']} row(s) written.",
        f"      PERIODS RECEIVED {first} to {last} "
        f"(seeded bounds {seeded_first} / {seeded_last})",
        f"      {count_line}",
    ]

    completeness = result.get("completeness") or []
    if completeness:
        total_absent = sum(absent for _loc, _rows, absent, _pct in completeness)
        lines.append(
            f"      NO RATE PUBLISHED for {total_absent} of {result['records_received']} "
            f"record(s) - a week USDA published no rate for is stored as a NULL row, usually a "
            f"winter closure (migration 0017). NOT an ingest gap, and not alerted on:"
        )
        for location, landed, absent, pct in completeness:
            lines.append(
                f"        {location:<18} {landed:>6} row(s), {absent:>5} with no rate "
                f"({pct:5.1f}%)"
            )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the live-verification path
    parser = argparse.ArgumentParser(
        description=(
            "Backfill a USDA AgTransport dataset in full. A CLI a human invokes; deliberately "
            "not a scheduled job, and it never writes to the usda_datasets seed."
        )
    )
    parser.add_argument(
        "--dataset",
        action="append",
        dest="datasets",
        choices=sorted(INGESTORS),
        help=(
            "dataset key to backfill; repeatable. Default: every ingestable dataset. The rates "
            "series is THREE datasets, one per horizon."
        ),
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

    keys = args.datasets or sorted(INGESTORS)
    started = datetime.now(timezone.utc)
    results = []
    with db.connection() as conn:
        for key in keys:
            results.append(backfill(conn, key))
    elapsed = datetime.now(timezone.utc) - started

    print()
    for result in results:
        print(f"  {describe(result)}")
    print(
        f"\ntotal: {sum(r['rows_written'] for r in results)} row(s) written across "
        f"{len(results)} dataset(s) in {elapsed}"
    )

    short = [r["dataset_key"] for r in results if r["short_of_seeded_count"]]
    if short:
        print(
            f"\n*** {len(short)} dataset(s) returned FEWER records than the seed measured: "
            f"{', '.join(short)}.\n"
            f"That is the truncation signal source_row_count exists to give (CLAUDE.md § 16).\n"
            f"Investigate the paging before trusting this table."
        )

    print(
        "\nThe PERIODS RECEIVED above are what usda_datasets.first_period / last_period get\n"
        "reconciled against. This backfill did NOT update them and must not: seed them from a\n"
        "COUNTED full-range query in a NEW numbered migration (CLAUDE.md § 15)."
    )
    return 1 if short else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
