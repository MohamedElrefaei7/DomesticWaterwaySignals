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
weekly series of thousands of rows and one full read costs a handful of requests, so resuming
would add a second record of progress (CLAUDE.md § 14 warns about exactly that) to save seconds.
The upsert makes a repeat run free.

IT NEVER WRITES TO usda_datasets. Discovered period bounds are REPORTED for a human to reconcile
into a new numbered migration, the same rule the daily backfill follows for dv_record_start
(CLAUDE.md § 15). A backfill that corrected its own seed would destroy the evidence it started
from the wrong place.
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

# dataset key -> the module that parses and writes it. `cost_indicators` is SEEDED IN 0013 AND
# DELIBERATELY ABSENT HERE: it has no table and no cadence entry in this commit, and asking for it
# should say so rather than fail with a KeyError from somewhere deeper.
INGESTORS = {
    usda_rates.DATASET_KEY: usda_rates,
    usda_movements.DATASET_KEY: usda_movements,
}


def backfill(conn, dataset_key: str, client: SocrataClient | None = None) -> dict:
    """Read one dataset in full and write it. Returns what happened, measured rather than assumed.

    The report includes the discovered period bounds because that is this run's deliverable
    beyond the rows: `usda_datasets.first_period` / `last_period` are NULL until a human seeds
    them from a counted full-range query, and these are what that gets reconciled against.
    """
    if dataset_key not in INGESTORS:
        raise ValueError(
            f"no ingest path for dataset key {dataset_key!r}. This commit ingests "
            f"{sorted(INGESTORS)}. 'cost_indicators' is seeded in migration 0013 for later and "
            f"has no table yet - adding it is a later commit's work, not a flag."
        )

    module = INGESTORS[dataset_key]
    client = SocrataClient() if client is None else client

    dataset = socrata_client.resolve_dataset(conn, dataset_key)
    records = client.fetch_all(dataset, order=module.ORDER_COLUMN)

    if dataset_key == usda_rates.DATASET_KEY:
        rows = module.parse_rates(records)
        written = module.upsert_rates(conn, rows)
    else:
        rows = module.parse_movements(records)
        written = module.upsert_movements(conn, rows)
    conn.commit()

    periods = sorted({row.week_ending for row in rows})
    return {
        "dataset_key": dataset_key,
        "records_received": len(records),
        "rows_written": written,
        "first_period": periods[0] if periods else None,
        "last_period": periods[-1] if periods else None,
        "seeded_first_period": dataset.first_period,
        "seeded_last_period": dataset.last_period,
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
    return (
        f"{result['dataset_key']}: {result['records_received']} record(s) received, "
        f"{result['rows_written']} row(s) written. "
        f"PERIODS RECEIVED {first} to {last} "
        f"(seeded bounds {seeded_first} / {seeded_last})"
    )


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
        help="dataset key to backfill; repeatable. Default: every ingestable dataset.",
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
    print(
        "\nThe PERIODS RECEIVED above are what usda_datasets.first_period / last_period get\n"
        "reconciled against. This backfill did NOT update them and must not: seed them from a\n"
        "COUNTED full-range query in a NEW numbered migration (CLAUDE.md § 15)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
