"""scripts/capture_thesis_queries.py — DEBT 1a's other half.

`CONTEXT.md` is owed four verbatim result sets and has been owed them across two phases. The script
re-reads them; this asserts it produces four CSVs against a real database, with the columns the
write-ups are checkable against, and that it writes nowhere else.

A SIXTH TEST FILE THE BRIEF DID NOT LIST, and it is recorded as a deviation. Part 1's done-condition
is "the script runs against a fixture database", which is a test whether or not it is written as
one - and an unasserted claim that a script works is the kind this project has already had to come
back and correct.

WHY THE INTEGRATION TIER. The script is four SQL statements and a `csv.writer`. Everything
interesting about it is whether the SQL still selects what its column names claim, against the real
schema - which is precisely what a fixture-free test cannot ask.
"""

import csv
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.signals.conftest import MEMPHIS

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capture_thesis_queries.py"


def load_script():
    """Import the script by path.

    `scripts/` is deliberately not a package - it holds operator entry points, not importable
    modules, and adding an `__init__.py` so a test could import it would make it one.
    """
    spec = importlib.util.spec_from_file_location("capture_thesis_queries", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_writes_only_the_four_csvs_and_never_the_log_it_serves():
    """The paste is the review step, and a document that edits itself is one nobody reviews.

    Unit, because it is a property of the source rather than of a run. Both phases that owed these
    tables had the anchor points and not the rows; the fix is a human putting the rows in front of
    their own eyes, which an automatic writeback would remove.

    ASSERTED AS "ONE WRITE PATH, FOUR CSV FILENAMES" rather than as "the string CONTEXT.md does not
    appear". The script names the log repeatedly in prose - it says which section each table is owed
    to, which is the useful part of its output - so a string search would either fail on that prose
    or be weakened until it caught nothing. What actually matters is that there is exactly one
    place a file is opened for writing and that every filename it can produce ends in `.csv`.
    """
    import re

    source = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]  # everything after the module docstring

    write_opens = re.findall(r"open\(\s*[^)]*[\"'][wax]", body)
    assert len(write_opens) == 1, (
        f"scripts/capture_thesis_queries.py has {len(write_opens)} write-mode open() call(s): "
        f"{write_opens}. It must have exactly one, writing a CSV into the stated --out directory."
    )

    module = load_script()
    filenames = [capture.filename for capture in module.captures()]
    assert len(filenames) == len(set(filenames)) == 4
    assert all(name.endswith(".csv") for name in filenames), (
        f"the script can write {filenames}; every output must be a CSV in the --out directory, "
        f"never a document. The paste into CONTEXT.md is a human's step."
    )

    # And it does not write to the database either. These four queries are a read of history.
    for statement in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "DROP "):
        assert statement not in body.upper(), (
            f"the capture script contains {statement.strip()}; it must only read"
        )


@pytest.mark.integration
def test_the_four_queries_run_and_write_four_csvs(migrated_db, seed_signals, tmp_path):
    """Part 1's done-condition: the script runs against a fixture database.

    The fixture puts rows inside the 2022 window and none inside the 2023 one, so the test covers
    both a populated table and an empty one - and asserts that the empty one still produces a FILE
    WITH ITS HEADER. A missing file and an empty table are different facts, and the script's caller
    is a human about to paste something into a log.
    """
    module = load_script()

    weeks = [date(2022, 8, 4) + timedelta(days=7 * i) for i in range(10)]

    # SEEDED INTO `gauge_readings_daily`, NOT `gauge_daily`. The raw-discharge query joins
    # `gauge_series` - the Phase 3.5 view that decides source precedence per site-date-parameter -
    # because that is the query CONTEXT.md's anchor points came from. `gauge_daily` is the Phase 5
    # rollup computed FROM that view and is a different table answering a different question; the
    # two are easy to confuse by name, which is why CONTEXT.md § 1b exists.
    for week in weeks:
        for offset in range(7):
            migrated_db.execute(
                "INSERT INTO gauge_readings_daily"
                " (usgs_site_id, date, param_code, stat_cd, value)"
                " VALUES (%s, %s, %s, %s, %s)",
                (MEMPHIS, week - timedelta(days=offset), "00060", "00003", 200000.0 - 900.0 * offset),
            )
    migrated_db.commit()

    for week, rate in zip(weeks, [388.0, 500.0, 925.0, 1428.0, 2427.0, 2812.5, 2400.0, 1800.0, 1200.0, 800.0]):
        migrated_db.execute(
            "INSERT INTO barge_rates (location, week_ending, horizon, pct_of_tariff)"
            " VALUES (%s, %s, %s, %s)",
            (module.TARGET_LOCATION, week, module.TARGET_HORIZON, rate),
        )
    migrated_db.commit()

    seed_signals.features(
        MEMPHIS,
        module.DESEASONALIZED_FEATURE,
        [
            (week - timedelta(days=d), 200000.0, 18095.0 - 1500.0 * d, 12)
            for week in weeks
            for d in range(7)
        ],
    )

    results = module.capture_all(migrated_db, tmp_path / "thesis")

    assert len(results) == 4, f"expected four captures, got {len(results)}"
    names = [capture.filename for capture, _path, _count in results]
    assert names == [
        "2022_raw_discharge.csv",
        "2023_raw_discharge.csv",
        "2022_deseasonalized.csv",
        "2023_deseasonalized.csv",
    ], names

    by_name = {c.filename: (p, n) for c, p, n in results}

    # THE POPULATED PAIR. The 2022 window holds the seeded weeks.
    for filename, third_column in (
        ("2022_raw_discharge.csv", "memphis_discharge_cfs"),
        ("2022_deseasonalized.csv", "memphis_discharge_min_anomaly"),
    ):
        path, count = by_name[filename]
        assert count == len(weeks), f"{filename} holds {count} rows, expected {len(weeks)}"

        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        # THE HEADER COMES FROM THE CURSOR, so it cannot disagree with the SELECT after an edit -
        # a CSV whose columns are mislabelled is the most convincing wrong table there is.
        assert rows[0] == ["week_ending", "cairo_memphis_nearby", third_column], rows[0]
        assert len(rows) == len(weeks) + 1
        assert rows[1][0] == weeks[0].isoformat()
        assert float(rows[1][1]) == pytest.approx(388.0)
        assert rows[1][2] not in ("", None), "the third column is empty on a seeded week"

        # In week order, which is what makes the pasted table readable as a time series.
        assert [row[0] for row in rows[1:]] == [week.isoformat() for week in weeks]

    # THE EMPTY PAIR. Nothing was seeded in 2023, and the files still exist with their headers.
    for filename in ("2023_raw_discharge.csv", "2023_deseasonalized.csv"):
        path, count = by_name[filename]
        assert count == 0
        assert path.exists(), f"{filename} was not written at all for an empty result"
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == 1 and rows[0][0] == "week_ending"

    # `--out` is REQUIRED. An output path defaulted to the working directory is a path nobody
    # stated, and these files are meant to be found again minutes later by a human.
    with pytest.raises(SystemExit):
        module.parse_args([])
    assert module.parse_args(["--out", "/tmp/thesis"]).out == Path("/tmp/thesis")
