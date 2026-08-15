"""Capture the four thesis queries `CONTEXT.md` was promised and never received. DEBT 1a.

`CONTEXT.md` records ANCHOR POINTS - endpoints, peaks, troughs - in two places where it promised
full week-by-week result sets, and says so in writing both times:

    "THE FULL WEEK-BY-WEEK RESULT SETS ARE NOT REPRODUCED HERE, AND THE REASON IS THE POINT."
    "The two Phase 4 thesis tables are STILL owed their verbatim output, and now so are the two
     2022 deseasonalized tables."

Those sessions had the endpoints and not the rows, and did not invent the ~26 intervening weeks
(CLAUDE.md § 4: when data is lost, record the loss - never synthesize a replacement). This script
is the other half of that discipline: the database holds the rows, they are cheap to re-read, and
every figure in both write-ups is checkable against them.

THIS SCRIPT NEVER WRITES TO CONTEXT.md, AND THAT IS THE DESIGN RATHER THAN AN OVERSIGHT
----------------------------------------------------------------------------------------
It writes four CSVs to a directory the operator states, and the human pastes them in as fenced
blocks. A DOCUMENT THAT EDITS ITSELF IS A DOCUMENT NOBODY REVIEWS - the paste is the review step,
and it is the moment somebody looks at the numbers rather than at the fact that a job exited zero.
That is CLAUDE.md § 2's theme 1 in the one place this project has repeatedly failed it: the log.

There is exactly one function here that opens a file for writing, it writes into the stated `--out`
directory only, and the four filenames are fixed constants. `CONTEXT.md` appears nowhere in this
module except in this docstring.

THE FOUR QUERIES ARE THE ONES THE LOG NAMES, UNCHANGED
------------------------------------------------------
Not improved, not widened, not given extra columns. The point of the exercise is that the anchor
points already recorded in `CONTEXT.md` can be checked against these tables - so the tables have to
be the output of the queries those anchor points came from. A better query would produce a better
table that answers a different question, and the debt would still be open.

  1. 2022 raw discharge          `CONTEXT.md § Phase 4 live verification, step 9`, 2022-07-01..12-31
  2. 2023 raw discharge          the same query over the second labelled event
  3. 2022 deseasonalized         `CONTEXT.md § Phase 5's live verification`: the same shape against
  4. 2023 deseasonalized         `features` where `feature_name = 'discharge_min'`, reading
                                 `anomaly` in place of `avg(g.value)`

A NOTE ON QUERIES 3 AND 4, so the table is not over-read. Phase 5's finding 3 measured that
`discharge_min` IS `discharge_mean` wherever `gauge_daily.n_observations = 1`, which at Memphis is
EVERY ROW. So the deseasonalized tables below are equally `discharge_mean` tables at this site, and
nothing about them is evidence that the daily minimum carries information the mean does not.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db

# The Memphis gauge (migration 0004). A literal here rather than a lookup, because these are four
# SPECIFIC historical queries reproducing anchor points already written down against this site -
# the site is part of the query's identity, not a parameter of it. (Contrast app/signals/pairs.py,
# where a site literal would be a bug and a test asserts its absence: there the site set is
# whatever the database holds.)
MEMPHIS = "07032000"

# The segment CLAUDE.md § 7's output contract names, and the spot rate rather than a forward quote.
TARGET_LOCATION = "Cairo-Memphis"
TARGET_HORIZON = "nearby"

# The deseasonalized feature the Phase 5 live-verification note names.
DESEASONALIZED_FEATURE = "discharge_min"

# The trailing window each rate week is matched against: the six days before the week-ending label
# plus the label itself. This is the join Phase 4 used, and caution 3 in `CONTEXT.md` names it as a
# limitation - a weekly mean smooths away the sub-weekly timing that would distinguish leading from
# lagging. It is REPRODUCED rather than fixed, because fixing it here would produce a table the
# recorded anchor points could no longer be checked against. The ±lag sweep is where timing is
# measured properly.
WEEK_TRAILING_DAYS = 6


RAW_DISCHARGE_SQL = """
SELECT r.week_ending,
       r.pct_of_tariff              AS cairo_memphis_nearby,
       round(avg(g.value))          AS memphis_discharge_cfs
  FROM barge_rates r
  LEFT JOIN gauge_series g
         ON g.usgs_site_id = %(site_id)s
        AND g.date BETWEEN r.week_ending - %(trailing_days)s * interval '1 day' AND r.week_ending
 WHERE r.location    = %(location)s
   AND r.horizon     = %(horizon)s
   AND r.week_ending BETWEEN %(start)s AND %(end)s
 GROUP BY r.week_ending, r.pct_of_tariff
 ORDER BY r.week_ending
"""

DESEASONALIZED_SQL = """
SELECT r.week_ending,
       r.pct_of_tariff              AS cairo_memphis_nearby,
       round(avg(f.anomaly))        AS memphis_discharge_min_anomaly
  FROM barge_rates r
  LEFT JOIN features f
         ON f.site_id      = %(site_id)s
        AND f.feature_name = %(feature_name)s
        AND f.date BETWEEN r.week_ending - %(trailing_days)s * interval '1 day' AND r.week_ending
 WHERE r.location    = %(location)s
   AND r.horizon     = %(horizon)s
   AND r.week_ending BETWEEN %(start)s AND %(end)s
 GROUP BY r.week_ending, r.pct_of_tariff
 ORDER BY r.week_ending
"""


class Capture:
    """One query, its parameters, and the file it lands in.

    A plain class rather than a dataclass so the SQL and its bound parameters stay adjacent to the
    filename in one readable block - the thing a reviewer checks is that the file called
    `2022_deseasonalized.csv` really does hold the deseasonalized 2022 query.
    """

    def __init__(self, filename: str, sql: str, params: dict, description: str):
        self.filename = filename
        self.sql = sql
        self.params = params
        self.description = description


def captures() -> tuple[Capture, ...]:
    """The four. Built by a function rather than a module constant so the shared parameters below
    are written once and the two years differ only in their bounds - which is the property that
    makes the 2022 and 2023 tables comparable."""
    common = {
        "site_id": MEMPHIS,
        "location": TARGET_LOCATION,
        "horizon": TARGET_HORIZON,
        "trailing_days": WEEK_TRAILING_DAYS,
    }
    years = {"2022": ("2022-07-01", "2022-12-31"), "2023": ("2023-07-01", "2023-12-31")}

    result = []
    for year, (start, end) in years.items():
        result.append(
            Capture(
                filename=f"{year}_raw_discharge.csv",
                sql=RAW_DISCHARGE_SQL,
                params={**common, "start": start, "end": end},
                description=(
                    f"{year} Cairo-Memphis nearby rate against RAW Memphis discharge, weekly. "
                    f"Owed to CONTEXT.md's PHASE 4 - VERIFIED section."
                ),
            )
        )
    for year, (start, end) in years.items():
        result.append(
            Capture(
                filename=f"{year}_deseasonalized.csv",
                sql=DESEASONALIZED_SQL,
                params={
                    **common,
                    "feature_name": DESEASONALIZED_FEATURE,
                    "start": start,
                    "end": end,
                },
                description=(
                    f"{year} Cairo-Memphis nearby rate against the DESEASONALIZED Memphis "
                    f"{DESEASONALIZED_FEATURE} anomaly, weekly. Owed to CONTEXT.md's PHASE 5 - "
                    f"VERIFIED section. At Memphis this feature is identical to discharge_mean "
                    f"(Phase 5 finding 3)."
                ),
            )
        )
    return tuple(result)


def run_capture(conn, capture: Capture, out_dir: Path) -> tuple[Path, int]:
    """Run one query and write its CSV. Returns the path and the row count.

    THE HEADER ROW COMES FROM THE CURSOR, not from a hand-written list. A hand-written header can
    disagree with the SELECT after an edit, and a CSV whose columns are mislabelled is the most
    convincing wrong table there is - it reads as measured, because it was.
    """
    cursor = conn.execute(capture.sql, capture.params)
    columns = [d.name for d in cursor.description]
    rows = cursor.fetchall()

    path = out_dir / capture.filename
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])
    return path, len(rows)


def capture_all(conn, out_dir: Path) -> list[tuple[Capture, Path, int]]:
    """All four, into `out_dir`. The directory is created if it does not exist."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return [(c, *run_capture(conn, c, out_dir)) for c in captures()]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Write the four thesis queries CONTEXT.md is owed to CSV. Reads only; writes nothing "
            "to the database and nothing to CONTEXT.md - paste the CSVs in by hand, because a "
            "document that edits itself is a document nobody reviews."
        )
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help=(
            "directory to write the four CSVs into. REQUIRED: an output path defaulted to the "
            "working directory is a path nobody stated, and these files are meant to be found "
            "again minutes later by a human who is pasting them somewhere."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:  # pragma: no cover - the live-verification path
    args = parse_args(argv)

    import os

    if not os.environ.get(db.DATABASE_URL_VAR):
        print(
            f"{db.DATABASE_URL_VAR} is not set. Copy .env.example to .env, fill it in, and "
            f"`set -a; . ./.env; set +a` before running this.",
            file=sys.stderr,
        )
        return 2

    with db.connection() as conn:
        results = capture_all(conn, args.out)

    print(f"\n  Wrote {len(results)} file(s) to {args.out}:\n")
    empty = []
    for capture, path, count in results:
        print(f"    {path}  -  {count} row(s)")
        print(f"      {capture.description}")
        if count == 0:
            empty.append(path.name)

    print(
        f"\n  Paste these into CONTEXT.md as fenced blocks, replacing the notes that say the "
        f"output is still owed. THIS SCRIPT DOES NOT DO THAT FOR YOU: the paste is the step where "
        f"somebody reads the numbers.\n"
    )

    if empty:
        # A COUNT OF ZERO IS NOT AN EMPTY RESULT UNTIL SOMEBODY CHECKS WHICH TABLE WAS EMPTY.
        # CLAUDE.md § 2's theme 1: it far more often means the query measured something narrower
        # than its name suggests - a location string that no longer matches, a feature renamed, a
        # build that never ran over these weeks.
        print(
            f"*** {len(empty)} of {len(results)} queries returned NO ROWS: {', '.join(empty)}\n"
            f"    The weeks queried are ones CONTEXT.md records measured figures for, so an empty "
            f"table means the query is now measuring something narrower than its name - not that "
            f"there is nothing there. Check barge_rates for {TARGET_LOCATION}/{TARGET_HORIZON} "
            f"over these dates, and features for {DESEASONALIZED_FEATURE} at {MEMPHIS}, before "
            f"pasting anything.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
