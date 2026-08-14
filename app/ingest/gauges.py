"""The gauge registry: what each site is, and what it actually serves.

CLAUDE.md § 1 puts the gauge site list on the never-invent list, and § 14 requires per-entity
availability, cadence, and period of record to be RECORDED rather than assumed uniform. This
module is how the rest of the ingest layer reads that record.

TWO READERS, ONE SOURCE
-----------------------
The seed lives in `migrations/0004_gauges.sql` and nowhere else. This module reads it two ways,
and the split is deliberate:

  load(conn)   — the RUNTIME path. Reads the `gauges` table. This is what the ingest job and the
                 backfill use, because the database is what is actually deployed and a migration
                 file on someone's laptop is not.

  parse_seed() — the GUARD path. Parses the migration file's INSERT statement directly, so the
                 unit tier — which has no database — can assert what is seeded. This is what
                 makes "exactly these four sites" a test that fails offline rather than a comment.

The obvious alternative was to declare the four rows here as Python and have the migration
mirror them. That is two tables of the same fact, and CLAUDE.md § 4 is explicit about what those
do: they diverge silently, and the divergence produces confident wrong answers. So there is one
copy, in the migration, and a parser here that reads it.

The parser is written against THIS PROJECT'S OWN FILE, whose layout is fixed and documented in
0004 itself. It is not a general SQL parser and does not try to be; it raises on anything it does
not recognise rather than skipping it, because a seed row this cannot read is a seed row the
guard tests would silently stop guarding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
SEED_MIGRATION = MIGRATIONS_DIR / "0004_gauges.sql"


class SeedParseError(RuntimeError):
    """The seed migration did not have the shape this parser is written against."""


@dataclass(frozen=True)
class Gauge:
    """One site, as seeded and as deployed."""

    usgs_site_id: str
    name: str
    river: str
    river_mile: float | None
    lat: float | None
    lon: float | None
    tier: int
    available_params: tuple[str, ...]
    native_cadence_minutes: int
    record_start: date

    def requested_pairs(self) -> set[tuple[str, str]]:
        """The (site, parameter) pairs an ingest of this site must ask for AND receive.

        The bridge between this registry and the client's set-equality assertion. Availability is
        per site (stage is absent at Memphis and Vicksburg), so this is the only correct way to
        build a request - a cross product over some global parameter list would ask two sites for
        a series they do not serve and hard-fail every run.
        """
        return {(self.usgs_site_id, param) for param in self.available_params}


# ---------------------------------------------------------------------------------------------
# Runtime path: read the deployed table.
# ---------------------------------------------------------------------------------------------

COLUMNS = (
    "usgs_site_id",
    "name",
    "river",
    "river_mile",
    "lat",
    "lon",
    "tier",
    "available_params",
    "native_cadence_minutes",
    "record_start",
)


def load(conn, site_ids=None) -> list[Gauge]:
    """Every seeded gauge, or just the named ones, ordered by site id.

    Raises if a requested site is not in the registry rather than returning a shorter list. A
    backfill asked to run for a site that does not exist should stop and say so; quietly running
    for zero sites and reporting success is CLAUDE.md § 2's theme 1 with a command-line argument
    attached.
    """
    sql = f"SELECT {', '.join(COLUMNS)} FROM gauges"
    params: tuple = ()
    if site_ids is not None:
        site_ids = list(site_ids)
        if not site_ids:
            raise ValueError("load() called with an empty site_ids list")
        sql += " WHERE usgs_site_id = ANY(%s)"
        params = (site_ids,)
    sql += " ORDER BY usgs_site_id"

    rows = conn.execute(sql, params).fetchall()
    gauges = [
        Gauge(
            usgs_site_id=row[0],
            name=row[1],
            river=row[2],
            river_mile=None if row[3] is None else float(row[3]),
            lat=None if row[4] is None else float(row[4]),
            lon=None if row[5] is None else float(row[5]),
            tier=row[6],
            available_params=tuple(row[7]),
            native_cadence_minutes=row[8],
            record_start=row[9],
        )
        for row in rows
    ]

    if site_ids is not None:
        found = {g.usgs_site_id for g in gauges}
        unknown = sorted(set(site_ids) - found)
        if unknown:
            raise ValueError(
                f"no such gauge(s) in the registry: {unknown}. Known sites: "
                f"{sorted(found)}. The gauge list is human-owned (CLAUDE.md § 1) - add a site by "
                f"writing a new numbered migration, not by passing it on the command line."
            )

    if not gauges:
        raise ValueError(
            "the gauges table is empty. Migration 0004 seeds four sites; either it has not been "
            "applied or the rows were removed. Run `python3 -m app.orchestration.migrate`."
        )

    return gauges


# ---------------------------------------------------------------------------------------------
# Guard path: parse the seed out of the migration file.
# ---------------------------------------------------------------------------------------------

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+gauges\s*\((?P<columns>[^)]*)\)\s*VALUES\s*(?P<values>.*?);",
    re.IGNORECASE | re.DOTALL,
)


def _split_top_level(text: str, opener: str, closer: str) -> list[str]:
    """Split `text` into the substrings inside each TOP-LEVEL opener/closer pair.

    Quote-aware and nesting-aware, which a regex here would not be: the seed rows contain commas
    inside quoted names ('Mississippi River at St. Louis, MO') and brackets inside ARRAY[...].
    """
    groups: list[str] = []
    depth = 0
    start = 0
    in_quote = False
    index = 0

    while index < len(text):
        char = text[index]

        if in_quote:
            # '' is SQL's escaped single quote, not the end of one string and the start of
            # another. Skipping both characters is what keeps a name like 'O''Fallon' in one
            # piece.
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_quote = False
        elif char == "'":
            in_quote = True
        elif char == opener:
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                groups.append(text[start:index])
            elif depth < 0:
                raise SeedParseError(
                    f"unbalanced {closer!r} at offset {index} in the seed statement"
                )

        index += 1

    if depth != 0 or in_quote:
        raise SeedParseError(
            f"unterminated {'string' if in_quote else opener!r} in the seed statement"
        )
    return groups


def _split_fields(row: str) -> list[str]:
    """One VALUES tuple's body into its fields, on top-level commas only."""
    fields: list[str] = []
    depth = 0
    in_quote = False
    current = []
    index = 0

    while index < len(row):
        char = row[index]

        if in_quote:
            current.append(char)
            if char == "'":
                if index + 1 < len(row) and row[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_quote = False
        elif char == "'":
            in_quote = True
            current.append(char)
        elif char in "([":
            depth += 1
            current.append(char)
        elif char in ")]":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(char)

        index += 1

    fields.append("".join(current).strip())
    return fields


def _parse_literal(raw: str):
    """One SQL literal from the seed into a Python value.

    Raises on anything unrecognised. A seed field this cannot read is a field the guard tests
    would otherwise stop guarding without saying so.
    """
    text = raw.strip()

    if text.upper() == "NULL":
        return None

    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")

    upper = text.upper()

    if upper.startswith("DATE"):
        inner = text[4:].strip()
        if not (inner.startswith("'") and inner.endswith("'")):
            raise SeedParseError(f"malformed DATE literal: {raw!r}")
        return date.fromisoformat(inner[1:-1])

    if upper.startswith("ARRAY["):
        inner = text[len("ARRAY[") : text.rindex("]")]
        if not inner.strip():
            return ()
        return tuple(_parse_literal(item) for item in _split_fields(inner))

    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError as exc:
        raise SeedParseError(
            f"unrecognised literal in the seed: {raw!r}. This parser reads only the forms "
            f"migrations/0004_gauges.sql uses (quoted string, NULL, integer, numeric, "
            f"DATE '...', ARRAY[...]). Extend it deliberately rather than loosening it."
        ) from exc


def parse_seed(sql_text: str | None = None) -> list[Gauge]:
    """The gauges seeded by migration 0004, read from the migration file itself.

    The unit tier's window into what is seeded. Returns them in file order.
    """
    if sql_text is None:
        if not SEED_MIGRATION.is_file():
            raise SeedParseError(
                f"seed migration not found at {SEED_MIGRATION}. The gauge registry's single copy "
                f"lives there; this parser has no fallback and deliberately does not carry one."
            )
        sql_text = SEED_MIGRATION.read_text(encoding="utf-8")

    # Comments first: 0004 explains at length why stage is absent and why lat/lon are NULL, and
    # that prose contains commas, quotes, and the word ARRAY. Stripping line comments before
    # matching is what keeps the file free to document itself.
    uncommented = "\n".join(
        line.split("--", 1)[0] if not _inside_quotes(line) else line
        for line in sql_text.splitlines()
    )

    match = _INSERT_RE.search(uncommented)
    if match is None:
        raise SeedParseError(
            f"no `INSERT INTO gauges (...) VALUES ...;` statement found in {SEED_MIGRATION.name}. "
            f"If the seed moved, move this parser with it - do not let the guard tests pass "
            f"against a file that no longer seeds anything."
        )

    columns = [c.strip() for c in match.group("columns").split(",")]
    unexpected = set(columns) ^ set(COLUMNS)
    if unexpected:
        raise SeedParseError(
            f"the seed INSERT names columns {columns}, which does not match the expected set "
            f"{list(COLUMNS)}. Difference: {sorted(unexpected)}"
        )

    gauges: list[Gauge] = []
    for row in _split_top_level(match.group("values"), "(", ")"):
        fields = _split_fields(row)
        if len(fields) != len(columns):
            raise SeedParseError(
                f"seed row has {len(fields)} field(s) for {len(columns)} column(s): {row!r}"
            )
        values = {col: _parse_literal(raw) for col, raw in zip(columns, fields)}
        gauges.append(
            Gauge(
                usgs_site_id=values["usgs_site_id"],
                name=values["name"],
                river=values["river"],
                river_mile=values["river_mile"],
                lat=values["lat"],
                lon=values["lon"],
                tier=values["tier"],
                available_params=tuple(values["available_params"] or ()),
                native_cadence_minutes=values["native_cadence_minutes"],
                record_start=values["record_start"],
            )
        )

    if not gauges:
        raise SeedParseError(
            "the seed INSERT was found but produced no rows. An empty registry means the ingest "
            "requests nothing, receives nothing, and reports success - which is exactly the "
            "vacuous pass CLAUDE.md § 2's theme 2 describes."
        )

    return gauges


def _inside_quotes(line: str) -> bool:
    """True when a line's `--` falls inside a string literal rather than starting a comment.

    Cheap and sufficient: an odd number of unescaped quotes before the first `--` means it is
    inside one. The seed has no such line today; this exists so that adding a site named
    'Something -- odd' does not silently truncate the row.
    """
    marker = line.find("--")
    if marker == -1:
        return False
    return line[:marker].count("'") % 2 == 1
