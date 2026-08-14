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

WHAT ELSE LIVES HERE, AND WHY IT IS NOT ITS OWN MODULE
-----------------------------------------------------
`gauge_known_gaps` (migration 0012) is read the same two ways: the ranges an endpoint is known
not to serve are part of the record of what a site actually serves, and separating them would put
half of that record behind a second import that the half needing it would have to remember.

The gaps exist so an empty window can be reported as EXPECTED or UNEXPLAINED. Neither is fatal.
They are never consulted to decide what to request - see 0012, and the test that asserts the
backfill still walks every window inside a known gap.
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

    # ONE RECORD START PER ENDPOINT, because measurement says they differ (CLAUDE.md § 15).
    #
    # iv_record_start is NULL AT THE THREE ROLLING-RETENTION SITES, and None here is that NULL
    # rather than a missing value. Memphis, Vicksburg and Baton Rouge serve instantaneous values
    # on a moving window of recent weeks; a rolling window is not a start date, and any date
    # stored for them would be false within weeks. 0004 seeded one anyway (the Phase 3
    # assumption), 0007 recorded that the measurement contradicted it, and 0011 replaced it with
    # NULL. St. Louis keeps 2007-10-01, which is a real fixed start.
    #
    # A None here means the instantaneous backfill has nothing to walk from and must say so
    # rather than compute one - see backfill.resume_point.
    #
    # dv_record_start is the first date of the site's CONTINUOUS daily record, as measured
    # 2026-08-14 by a full-range request counting values per year (0011). At St. Louis it is a
    # bound rather than a discovered start: its record predates the 1990 request floor.
    iv_record_start: date | None
    dv_record_start: date

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
    "iv_record_start",
    "dv_record_start",
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
            iv_record_start=row[9],
            dv_record_start=row[10],
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

# The HEADER only - `INSERT INTO <table> (cols) VALUES` - and never the rows. Where the statement
# ENDS is found by a quote-aware scan (_values_clause), because a regex terminating at the first
# `;` truncates a row whose text contains one, and 0012's notes do: "endpoint serves nothing in
# this range; segment before it deliberately not ingested". The truncation is not silent - the
# bracket scanner raises on the unterminated string it is handed - but the error it raises names
# the wrong thing entirely.
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+gauges\s*\((?P<columns>[^)]*)\)\s*VALUES\s*",
    re.IGNORECASE | re.DOTALL,
)

# RECORD STARTS ARRIVE AS UPDATEs SPREAD ACROSS THE SEQUENCE, not in 0004's INSERT. The daily
# endpoint did not exist when the registry was written, so 0008 added `dv_record_start` and seeded
# it; 0011 then corrected three of those four values and NULLed `iv_record_start` at the three
# rolling-retention sites. This parser reads EVERY numbered migration in order and applies each
# UPDATE it finds, so the unit tier sees the same effective seed the deployed table holds.
#
# Reading only the migration that introduced a column is the version that goes stale silently: it
# would still report Memphis's daily floor as 1990-01-01 - the exact value 0011 exists to correct -
# and would report it with the same confidence as a right answer.
_RECORD_START_UPDATE_RE = re.compile(
    r"UPDATE\s+gauges\s+SET\s+(?P<column>iv_record_start|dv_record_start)\s*=\s*"
    r"(?P<value>DATE\s*'[\d-]+'|NULL)\s*"
    r"WHERE\s+usgs_site_id\s*=\s*'(?P<site>\d{8})'\s*;",
    re.IGNORECASE,
)

# Zero-padded four-digit prefixes, so lexical order IS numeric order. The migration runner
# enforces that shape (CLAUDE.md § 12); this glob relies on it rather than re-deriving it.
MIGRATION_GLOB = "[0-9][0-9][0-9][0-9]_*.sql"

# What 0004's INSERT names. NOT the same as COLUMNS, which is what the DEPLOYED table carries:
# 0007 renamed record_start -> iv_record_start and 0008 added dv_record_start, and an applied
# migration is never edited to match (CLAUDE.md § 3). The mapping between the two lives here, in
# one place, rather than as a silent positional assumption.
SEED_INSERT_COLUMNS = (
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


def _values_clause(text: str, start: int) -> str:
    """Everything from `start` up to the statement's terminating `;`, ignoring quoted ones.

    SQL's statement terminator inside a string literal is just a character. Finding the end of a
    VALUES clause with a regex means finding the first `;` in the file, which for 0012 is in the
    middle of a note.
    """
    index = start
    in_quote = False
    while index < len(text):
        char = text[index]
        if in_quote:
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_quote = False
        elif char == "'":
            in_quote = True
        elif char == ";":
            return text[start:index]
        index += 1

    raise SeedParseError(
        "the INSERT statement is not terminated by a `;` outside a string literal. Either the "
        "statement is unfinished or a quote in it is unbalanced; this parser will not guess where "
        "it was meant to end."
    )


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
    uncommented = _strip_line_comments(sql_text)

    match = _INSERT_RE.search(uncommented)
    if match is None:
        raise SeedParseError(
            f"no `INSERT INTO gauges (...) VALUES ...;` statement found in {SEED_MIGRATION.name}. "
            f"If the seed moved, move this parser with it - do not let the guard tests pass "
            f"against a file that no longer seeds anything."
        )

    columns = [c.strip() for c in match.group("columns").split(",")]
    unexpected = set(columns) ^ set(SEED_INSERT_COLUMNS)
    if unexpected:
        raise SeedParseError(
            f"the seed INSERT names columns {columns}, which does not match the expected set "
            f"{list(SEED_INSERT_COLUMNS)}. Difference: {sorted(unexpected)}"
        )

    starts = parse_record_starts()

    gauges: list[Gauge] = []
    for row in _split_top_level(_values_clause(uncommented, match.end()), "(", ")"):
        fields = _split_fields(row)
        if len(fields) != len(columns):
            raise SeedParseError(
                f"seed row has {len(fields)} field(s) for {len(columns)} column(s): {row!r}"
            )
        values = {col: _parse_literal(raw) for col, raw in zip(columns, fields)}
        site_id = values["usgs_site_id"]
        site_starts = starts.get(site_id, {})

        if "dv_record_start" not in site_starts:
            raise SeedParseError(
                f"site {site_id} is seeded in {SEED_MIGRATION.name} but no migration sets a "
                f"dv_record_start for it. The daily backfill would have no floor to walk from. "
                f"(The deployed table's NOT NULL catches this too - this catches it offline, in "
                f"the session that introduced it.)"
            )

        gauges.append(
            Gauge(
                usgs_site_id=site_id,
                name=values["name"],
                river=values["river"],
                river_mile=values["river_mile"],
                lat=values["lat"],
                lon=values["lon"],
                tier=values["tier"],
                available_params=tuple(values["available_params"] or ()),
                native_cadence_minutes=values["native_cadence_minutes"],
                # 0004's `record_start` is what 0007 renamed to iv_record_start - unless a later
                # migration changed it, which 0011 does: NULL at the three rolling-retention
                # sites. Membership rather than `.get(...)` with a default, because "set to NULL"
                # and "never mentioned" are different facts and only one of them is a correction.
                iv_record_start=(
                    site_starts["iv_record_start"]
                    if "iv_record_start" in site_starts
                    else values["record_start"]
                ),
                dv_record_start=site_starts["dv_record_start"],
            )
        )

    seeded_ids = {g.usgs_site_id for g in gauges}
    orphaned = sorted(set(starts) - seeded_ids)
    if orphaned:
        raise SeedParseError(
            f"migrations set a record start for site(s) {orphaned}, which {SEED_MIGRATION.name} "
            f"does not seed. Either the site belongs in the registry or the UPDATE is addressing "
            f"a site number that does not exist - in the deployed database the latter updates "
            f"zero rows and reports success."
        )

    if not gauges:
        raise SeedParseError(
            "the seed INSERT was found but produced no rows. An empty registry means the ingest "
            "requests nothing, receives nothing, and reports success - which is exactly the "
            "vacuous pass CLAUDE.md § 2's theme 2 describes."
        )

    return gauges


def parse_record_starts() -> dict[str, dict[str, date | None]]:
    """site id -> {column -> value}, from every record-start UPDATE in the migration sequence.

    Applied IN MIGRATION ORDER, last write wins - the same order the database applies them in, so
    a correction in a later file is what this reports. `None` in the returned mapping is a real
    `= NULL` and is distinguishable from a column the sequence never mentions, which is simply
    absent.
    """
    paths = sorted(MIGRATIONS_DIR.glob(MIGRATION_GLOB))
    if not paths:
        raise SeedParseError(
            f"no numbered migrations found in {MIGRATIONS_DIR}. The gauge registry's single copy "
            f"lives there; this parser has no fallback and deliberately does not carry one."
        )

    starts: dict[str, dict[str, date | None]] = {}
    for path in paths:
        text = _strip_line_comments(path.read_text(encoding="utf-8"))
        for match in _RECORD_START_UPDATE_RE.finditer(text):
            raw = match.group("value")
            starts.setdefault(match.group("site"), {})[match.group("column").lower()] = (
                None if raw.strip().upper() == "NULL" else _parse_literal(raw)
            )

    if not starts:
        raise SeedParseError(
            f"no `UPDATE gauges SET <iv|dv>_record_start = ... WHERE usgs_site_id = '...';` "
            f"statements found anywhere in {MIGRATIONS_DIR}. If the record starts moved, move this "
            f"parser with them rather than letting the guard tests pass against files that no "
            f"longer set anything."
        )
    return starts


def parse_daily_floors() -> dict[str, date]:
    """site id -> dv_record_start, as the sequence leaves it. The daily backfill's floors."""
    return {
        site: columns["dv_record_start"]
        for site, columns in parse_record_starts().items()
        if columns.get("dv_record_start") is not None
    }


# ---------------------------------------------------------------------------------------------
# Known gaps: the ranges a source will not serve.
# ---------------------------------------------------------------------------------------------
#
# Seeded by migration 0012 from the full-range measurement of 2026-08-14, and read the same two
# ways the registry is: `load_known_gaps` for the runtime, `parse_known_gaps` for the unit tier.
#
# What they are FOR is deciding whether a window that came back empty is expected or is a
# surprise. What they are NOT for is deciding what to request: see 0012, and the test that
# asserts the backfill still walks every window inside a gap.

KNOWN_GAP_COLUMNS = ("usgs_site_id", "source", "gap_start", "gap_end", "note")
KNOWN_GAPS_MIGRATION = MIGRATIONS_DIR / "0012_gauge_known_gaps.sql"

SOURCE_DAILY = "dv"
SOURCE_INSTANTANEOUS = "iv"

# The two verdicts an empty window gets. Named constants rather than bare strings because both
# callers and both tests compare against them, and a typo in one of four string literals is a
# comparison that is simply always false.
EXPECTED = "expected"
UNEXPLAINED = "unexplained"

_KNOWN_GAP_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+gauge_known_gaps\s*\((?P<columns>[^)]*)\)\s*VALUES\s*",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class KnownGap:
    """A range one endpoint is known not to serve at one site.

    INCLUSIVE ON BOTH ENDS: `gap_start` is the first missing day and `gap_end` is the last, so the
    record resumes on `gap_end + 1 day`. Read as exclusive instead, the boundary days - the last
    day that has data and the first day that has it again - would be treated as missing, which is
    how a genuine one-day edge of a gap disappears into the gap.
    """

    usgs_site_id: str
    source: str
    gap_start: date
    gap_end: date
    note: str

    def contains_window(self, start: date, end: date) -> bool:
        """True only when [start, end] falls ENTIRELY inside this gap.

        Entirely, not partially. A window that straddles a boundary covers days this gap does not
        explain, and calling it expected is what hides a real gap edge: the days outside the row
        returned nothing, nobody was told, and the row gets read afterwards as though it had
        accounted for them.
        """
        return self.gap_start <= start and end <= self.gap_end


def load_known_gaps(conn, source: str | None = None, site_ids=None) -> list[KnownGap]:
    """Every known gap, or just one endpoint's / one site's, ordered by site and start date."""
    sql = f"SELECT {', '.join(KNOWN_GAP_COLUMNS)} FROM gauge_known_gaps"
    clauses: list[str] = []
    params: list = []
    if source is not None:
        clauses.append("source = %s")
        params.append(source)
    if site_ids is not None:
        site_ids = list(site_ids)
        if not site_ids:
            raise ValueError("load_known_gaps() called with an empty site_ids list")
        clauses.append("usgs_site_id = ANY(%s)")
        params.append(site_ids)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY usgs_site_id, source, gap_start"

    # NO EMPTINESS CHECK HERE, unlike load(). An empty gauges table means the ingest requests
    # nothing and reports success; an empty gap table means every empty window is unexplained,
    # which is loud rather than silent and is the correct reading of a source with no known gaps.
    return [
        KnownGap(
            usgs_site_id=row[0],
            source=row[1],
            gap_start=row[2],
            gap_end=row[3],
            note=row[4],
        )
        for row in conn.execute(sql, tuple(params)).fetchall()
    ]


def parse_known_gaps(sql_text: str | None = None) -> list[KnownGap]:
    """The gaps seeded by migration 0012, read from the migration file itself.

    The unit tier's window into the seeded boundaries, for the same reason `parse_seed` exists: a
    boundary that only a database can check is a boundary that goes unchecked in the session where
    someone adjusts it.
    """
    if sql_text is None:
        if not KNOWN_GAPS_MIGRATION.is_file():
            raise SeedParseError(
                f"known-gaps migration not found at {KNOWN_GAPS_MIGRATION}. If the seed moved, "
                f"move this parser with it - do not let the guard tests pass against a file that "
                f"no longer seeds anything."
            )
        sql_text = KNOWN_GAPS_MIGRATION.read_text(encoding="utf-8")

    uncommented = _strip_line_comments(sql_text)
    match = _KNOWN_GAP_INSERT_RE.search(uncommented)
    if match is None:
        raise SeedParseError(
            f"no `INSERT INTO gauge_known_gaps (...) VALUES ...;` statement found in "
            f"{KNOWN_GAPS_MIGRATION.name}."
        )

    columns = [c.strip() for c in match.group("columns").split(",")]
    unexpected = set(columns) ^ set(KNOWN_GAP_COLUMNS)
    if unexpected:
        raise SeedParseError(
            f"the known-gaps INSERT names columns {columns}, which does not match the expected "
            f"set {list(KNOWN_GAP_COLUMNS)}. Difference: {sorted(unexpected)}"
        )

    gaps: list[KnownGap] = []
    for row in _split_top_level(_values_clause(uncommented, match.end()), "(", ")"):
        fields = _split_fields(row)
        if len(fields) != len(columns):
            raise SeedParseError(
                f"known-gap row has {len(fields)} field(s) for {len(columns)} column(s): {row!r}"
            )
        values = {col: _parse_literal(raw) for col, raw in zip(columns, fields)}
        if values["gap_end"] < values["gap_start"]:
            raise SeedParseError(
                f"known-gap row for {values['usgs_site_id']} ends ({values['gap_end']}) before it "
                f"starts ({values['gap_start']}). Such a row matches no window and would sit in "
                f"the table doing nothing. (The database's CHECK catches this too; this catches "
                f"it offline.)"
            )
        gaps.append(
            KnownGap(
                usgs_site_id=values["usgs_site_id"],
                source=values["source"],
                gap_start=values["gap_start"],
                gap_end=values["gap_end"],
                note=values["note"],
            )
        )

    if not gaps:
        raise SeedParseError(
            "the known-gaps INSERT was found but produced no rows. Every empty window would then "
            "be reported as unexplained - which is the safe direction to be wrong in, and still "
            "not what this file says."
        )
    return gaps


def classify_empty_window(
    site_id: str,
    start: date,
    end: date,
    known_gaps,
    source: str = SOURCE_DAILY,
) -> str:
    """EXPECTED when [start, end] lies entirely inside one known gap for this site; else UNEXPLAINED.

    THIS NEVER RAISES AND NEVER DECIDES ANYTHING IS FATAL. An empty window is ordinary either way
    (CLAUDE.md § 14) - the difference between the two verdicts is what gets logged, and that is
    the whole of it. A missing SERIES is the fatal case and it lives in the client, where it can
    tell the difference.

    Containment is against a SINGLE gap rather than the union of several. Two adjacent rows with
    data between them do not jointly explain a window that spans both, and merging them here would
    invent an explanation the measurement never supported.
    """
    return (
        EXPECTED
        if explain_empty_window(site_id, start, end, known_gaps, source) is not None
        else UNEXPLAINED
    )


def explain_empty_window(
    site_id: str,
    start: date,
    end: date,
    known_gaps,
    source: str = SOURCE_DAILY,
) -> KnownGap | None:
    """The gap that explains [start, end], or None when nothing does.

    The verdict and the evidence come from ONE search rather than two. A caller that logs a
    warning after separately deciding the window was expected, or names a gap that was not the one
    the classification matched, is reporting something the code did not do.
    """
    for gap in known_gaps:
        if (
            gap.usgs_site_id == site_id
            and gap.source == source
            and gap.contains_window(start, end)
        ):
            return gap
    return None


def _strip_line_comments(sql_text: str) -> str:
    """The statements, with `--` comments removed.

    Every migration in this repository explains itself at length, and that prose contains commas,
    quotes, the word ARRAY, and - in 0008 and 0011 - lines that look very much like the UPDATE
    statements the record-start parser matches. 0008's comment block tabulates the floors it is
    about to set, and 0011's explains the values it replaces. Stripping comments before matching
    is what keeps a file free to document itself without the documentation being parsed as data.
    """
    return "\n".join(
        line.split("--", 1)[0] if not _inside_quotes(line) else line
        for line in sql_text.splitlines()
    )


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
