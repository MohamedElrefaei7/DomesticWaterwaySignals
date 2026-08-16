"""The analog engine: one question, one answer, and the answer is usually "insufficient history".

THE CLI AND THE FUNCTION PHASE 8's API WILL CALL. No cadence entry and no freshness registration -
the engine answers when asked. A scheduled analog run would accumulate outputs nobody reads, which
is `app/signals/sweep.py`'s argument for the same decision one phase earlier.

WHAT THIS IS BUILT ON TOP OF, STATED BEFORE THE CODE
-----------------------------------------------------
Phase 6 scanned 6,966 pairs. ONE passed - `days_below_p10` at Memphis, horizon 7, LAG 0, regime
`all`, statistic -0.137, q 0.0446. Zero passed at any non-zero lag in either direction. The
recovery regime could not be tested at all: 1 to 7 observations at every horizon.

So there is no measured predictive relationship in this dataset, and this engine is expected to
refuse most or all queries. THAT IS THE CORRECT OUTPUT AND IT IS THE DELIVERABLE. An analog engine
that finds confident analogs where the lead-lag sweep found no relationship has a bug, not a
discovery - and every query records the sweep's verdict beside its own answer (migration 0024) so
that contradiction shows up in the data rather than in an argument.

THE THREE EXCLUSIONS, AND WHY THE SECOND ONE IS THE SEDUCTIVE BUG
------------------------------------------------------------------
`eligible_events` is where lookahead is prevented, and it does three things:

  1. THE OUTCOME MUST ALREADY HAVE HAPPENED. `start + window <= as_of`. An analog whose rate move
     reaches past the query date is scored on prices nobody had, which is the entire class of error
     this phase is arranged around.

  2. THE QUERY EVENT IS NOT ITS OWN ANALOG, AND NEITHER IS ANYTHING OVERLAPPING IT. An event
     matching itself reports a perfect distance of zero and a real outcome, so it lands at rank 1
     with the strongest possible similarity - and the sentence then says "the last 5 times
     conditions looked like this" about a set containing this time. It is the most seductive bug in
     the phase because the output looks BETTER, not broken.

  3. THE Z-SCORE POPULATION ENDS AT `as_of` TOO. Standardizing against the full record would score
     a 2015 condition against a spread that includes 2022 - leakage arriving in a number nobody
     reads as a prediction, in a helper nobody reviews as a model.

WHAT IS DELIBERATELY NOT HERE
------------------------------
No similarity cutoff (`parameters.SIMILARITY_CUTOFF` is None): the engine returns the k nearest and
reports every distance, so a human can look at a distribution before claiming what "similar" means.
No fitted weighting anywhere. No selection of an outcome window. No seasonal restriction, which is
why the rendered sentence does not claim one.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - the CLI path, not the test suite
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.analogs import events, gate as gate_module, outcomes, parameters, render, similarity
from app.features.targets import TARGET_HORIZON, TARGET_LOCATION

# Read from the sweep rather than reimplemented. CLAUDE.md § 17 forbids a second implementation of
# a rule that already has one, and "how this project reads its own commit, and refuses rather than
# defaulting" is exactly such a rule - a parallel copy here would be the one that quietly starts
# writing 'unknown'.
from app.signals.sweep import git_state

# The feature whose `value` describes the query condition in the rendered sentence. Not part of the
# similarity metric's identity - `parameters.SIMILARITY_FEATURES` owns that - and named separately
# because the sentence talks about discharge in cfs while the distance talks in z-scores.
CONDITION_FEATURE = "discharge_mean"


FEATURE_SERIES_SQL = """
SELECT date, value, anomaly, climatology_n_years
  FROM features
 WHERE site_id = %(site_id)s AND feature_name = %(feature_name)s
 ORDER BY date
"""

# `pct_of_tariff` is `numeric` and arrives as a Decimal. Cast at the boundary rather than in the
# arithmetic: mixing Decimal and float raises in some operations and silently changes precision in
# others, and `outcomes.py` is a pure module that should never have to know which driver it is
# behind. The NULLs survive the cast, which matters - a week USDA published with no rate is an
# incomplete outcome and not a zero (migration 0017).
RATE_SERIES_SQL = """
SELECT week_ending, pct_of_tariff::double precision
  FROM barge_rates
 WHERE location = %(location)s AND horizon = %(horizon)s
 ORDER BY week_ending
"""

GAUGE_SQL = "SELECT name, river FROM gauges WHERE usgs_site_id = %(site_id)s"

# The sweep's verdict on the pair this query assumes, from ITS MOST RECENT RUN.
#
# Most recent rather than best-across-all-runs, and the difference matters: taking the smallest
# q-value ever recorded for this pair across every run would be selecting the friendliest
# experiment, which is the model-selection failure `signals` is arranged to prevent, performed by
# the consumer instead of the writer. One run is one experiment; the latest one is the current
# answer.
STRONGEST_SIGNAL_SQL = """
SELECT run_id, q_value
  FROM signals
 WHERE feature_name = %(feature_name)s
   AND site_id = %(site_id)s
   AND q_value IS NOT NULL
   AND run_id = (SELECT max(run_id) FROM signals
                  WHERE feature_name = %(feature_name)s AND site_id = %(site_id)s)
 ORDER BY q_value
 LIMIT 1
"""

INSERT_QUERY_SQL = """
INSERT INTO analog_queries
    (as_of_date, site_id, feature_vector, k, outcome_window_days, gate_result, n_raw_detections,
     n_collapsed_events, n_analogs, n_consistent, signal_run_id, signal_q_value, git_sha,
     git_dirty, parameters_hash)
VALUES (%(as_of_date)s, %(site_id)s, %(feature_vector)s, %(k)s, %(outcome_window_days)s,
        %(gate_result)s, %(n_raw_detections)s, %(n_collapsed_events)s, %(n_analogs)s,
        %(n_consistent)s, %(signal_run_id)s, %(signal_q_value)s, %(git_sha)s, %(git_dirty)s,
        %(parameters_hash)s)
RETURNING query_id
"""

INSERT_MATCH_SQL = """
INSERT INTO analog_matches (query_id, rank, event_start, distance, outcome_log_return)
VALUES (%s, %s, %s, %s, %s)
"""


@dataclass(frozen=True)
class Match:
    """One analog as the engine found it: rank, date, distance, and its measured outcome.

    THIS IS THE RECORD SHAPE, NOT THE RETURNED SHAPE. `outcome_log_return` is written to
    `analog_matches` for every match including on a refused query, because the table is the
    research log - and it is stripped out of `AnalogResult.matches`, because that is a claim. See
    migration 0025, which makes the same distinction from the schema's side.
    """

    rank: int
    event_start: date
    distance: float
    outcome_log_return: float | None


@dataclass(frozen=True)
class MatchSummary:
    """One analog as a caller sees it: rank, date, DISTANCE, AND NO OUTCOME.

    The distance is here because a human needs it - there is no similarity cutoff in this project
    and step 2 of the live procedure is somebody reading these numbers before proposing one. The
    outcome is not, because a caller holding per-analog outcomes can compute the median the gate
    refused to give them, and a refusal that can be undone downstream is not a refusal.
    """

    rank: int
    event_start: date
    distance: float


@dataclass(frozen=True)
class AnalogResult:
    """What a query returns. ON A REFUSAL THERE IS NO ESTIMATE ANYWHERE IN HERE.

    `summary` is None unless the gate passed, and it is not merely withheld - `outcomes.summarize`
    is never called on the refusing branch, so no median is ever computed. A value that exists is
    one refactor away from being displayed.

    `signal_q_value` rides on every result so the sweep's verdict cannot be separated from the
    engine's answer, and `parameters_hash` so two results under different settings are never
    mistaken for two observations of one thing.
    """

    as_of: date
    site_id: str
    condition: render.Condition
    gate: gate_module.GateResult
    summary: outcomes.Summary | None
    matches: tuple[MatchSummary, ...]
    n_raw_detections: int
    n_collapsed_events: int
    signal_run_id: int | None
    signal_q_value: float | None
    parameters_hash: str
    query_id: int | None = None

    @property
    def sentence(self) -> str:
        return render.sentence(
            self.condition,
            self.gate,
            self.summary,
            window_days=parameters.OUTCOME_WINDOW_DAYS,
            min_analogs=parameters.MIN_ANALOGS,
        )


# ---------------------------------------------------------------------------------------------
# Eligibility. The lookahead guard, as a pure function so it can be tested without a database.
# ---------------------------------------------------------------------------------------------


def eligible_events(
    candidate_events,
    *,
    query_start: date | None,
    as_of: date,
    window_days: int = parameters.OUTCOME_WINDOW_DAYS,
):
    """The events that may serve as analogs for a query at `as_of`. Both exclusions, stated.

    An event qualifies only when its ENTIRE outcome window is in the past relative to the query,
    AND ends strictly before the query's own event began:

        start + window <= as_of        the outcome already happened
        start + window <  query_start  it does not overlap the condition being asked about

    The second implies the first whenever `query_start <= as_of`, which is always. Both are written
    anyway: the first is the rule that would be wrong to drop if the query event were ever allowed
    to be None-shaped in a different way, and a reader checking for lookahead should find it stated
    rather than inferred.
    """
    window = timedelta(days=window_days)
    kept = []
    for event in candidate_events:
        window_end = event.start + window
        if window_end > as_of:
            continue
        if query_start is not None and window_end >= query_start:
            continue
        kept.append(event)
    return kept


# ---------------------------------------------------------------------------------------------
# Reading.
# ---------------------------------------------------------------------------------------------


def _feature_rows(conn, site_id: str, feature_name: str):
    return conn.execute(
        FEATURE_SERIES_SQL, {"site_id": site_id, "feature_name": feature_name}
    ).fetchall()


def _series_for(rows) -> tuple[dict, str]:
    """`(date -> number, which column it came from)` for one feature at one site.

    THE COLUMN IS CHOSEN FROM THE DATA, NOT FROM A LIST OF FEATURE NAMES, which is the rule
    `app/signals/pairs.py` follows for the same decision: a deseasonalized feature is compared on
    its anomaly, and a run-length feature has no anomaly by construction (a day-of-year median of a
    count is a number with no meaning) so it is compared on its value. A hardcoded mapping would be
    wrong the day the climatology guard starts refusing a site, and wrong silently.
    """
    has_anomaly = any(row[2] is not None for row in rows)
    column = "anomaly" if has_anomaly else "value"
    index = 2 if has_anomaly else 1
    return {row[0]: row[index] for row in rows}, column


def _condition(conn, site_id: str, as_of: date, condition_rows) -> render.Condition:
    """The first half of the sentence, read from the database.

    Each half is allowed to be missing independently - see `render.condition_clause`. A 14-day
    change needs an observation on both ends, and this project does not interpolate one across a
    gap (`CLAUDE.md § 17`); an anomaly needs a climatology deep enough for the eight-year guard.
    """
    gauge = conn.execute(GAUGE_SQL, {"site_id": site_id}).fetchone()
    if gauge is None:
        raise ValueError(
            f"no gauge {site_id!r} in `gauges`. The site list is human-owned (CLAUDE.md § 1) and "
            f"this engine does not invent one."
        )
    name, river = gauge

    by_date = {row[0]: row for row in condition_rows}
    now_row = by_date.get(as_of)
    then_row = by_date.get(as_of - timedelta(days=parameters.CONDITION_LOOKBACK_DAYS))

    change = None
    if now_row is not None and then_row is not None:
        if now_row[1] is not None and then_row[1] is not None:
            change = now_row[1] - then_row[1]

    anomaly = now_row[2] if now_row is not None else None
    n_years = now_row[3] if now_row is not None else None

    return render.Condition(
        site_label=render.site_label(name),
        river=river,
        as_of=as_of,
        change=change,
        lookback_days=parameters.CONDITION_LOOKBACK_DAYS,
        anomaly=anomaly,
        climatology_n_years=n_years,
    )


def _strongest_signal(conn, site_id: str):
    """`(run_id, q_value)` from the sweep's most recent run over this pair, or `(None, None)`.

    NULL is a third state and it is not "no relationship": it means the sweep never scanned this
    feature-site pair, which happens when the pair was skipped as degenerate (`discharge_min` at
    Memphis and Vicksburg) or when no sweep has run at all. Migration 0024's bidirectional CHECK
    keeps the pair together so a q-value can never appear without the run it came from.
    """
    row = conn.execute(
        STRONGEST_SIGNAL_SQL,
        {"feature_name": parameters.ENTRY_FEATURE, "site_id": site_id},
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


# ---------------------------------------------------------------------------------------------
# The query.
# ---------------------------------------------------------------------------------------------


def query(conn, *, as_of: date, site_id: str, persist: bool = True) -> AnalogResult:
    """Ask the engine one question. Returns a result whose gate has already refused or passed.

    The order below is the decision, not an implementation detail:

        detect -> collapse -> exclude -> measure outcomes -> GATE -> (only then) summarize

    `outcomes.summarize` sits AFTER the gate and is not called on the refusing branch. Moving it
    earlier is one of the mutations this phase confirms, and the test that catches it watches
    whether the function was called rather than whether its result was displayed.
    """
    entry_rows = _feature_rows(conn, site_id, parameters.ENTRY_FEATURE)
    entry_series = events.observations_through(
        [(row[0], row[1]) for row in entry_rows], as_of
    )

    detected = events.history(entry_series)

    # Is the river in the condition right now? `is_entry` over the truncated series, which is the
    # same primitive every historical detection went through - so "today counts as an event" and
    # "2022-08-30 counted as an event" cannot drift apart.
    in_condition = events.is_entry(entry_series)
    query_start = detected.events[-1].start if (in_condition and detected.events) else None

    condition_rows = _feature_rows(conn, site_id, CONDITION_FEATURE)
    condition = _condition(conn, site_id, as_of, condition_rows)
    signal_run_id, signal_q_value = _strongest_signal(conn, site_id)
    fingerprint = parameters.parameters_hash()

    def _result(gate_result, summary, matches, records) -> AnalogResult:
        query_id = None
        if persist:
            query_id = _write(
                conn,
                as_of=as_of,
                site_id=site_id,
                gate_result=gate_result,
                detected=detected,
                records=records,
                signal_run_id=signal_run_id,
                signal_q_value=signal_q_value,
                fingerprint=fingerprint,
            )
        return AnalogResult(
            as_of=as_of,
            site_id=site_id,
            condition=condition,
            gate=gate_result,
            summary=summary,
            matches=matches,
            n_raw_detections=detected.n_raw_detections,
            n_collapsed_events=detected.n_collapsed_events,
            signal_run_id=signal_run_id,
            signal_q_value=signal_q_value,
            parameters_hash=fingerprint,
            query_id=query_id,
        )

    if query_start is None:
        # Not in a low-water condition. A distinct verdict rather than "too few analogs" - the
        # engine was asked about a river that is not doing the thing, which is not a coverage
        # problem and must not read as one.
        return _result(gate_module.no_current_event(), None, (), ())

    # EVERY event is offered, including the one being asked about. `eligible_events` is the single
    # place the exclusions live, and handing it a list somebody already pruned would split the
    # guard in two - after which removing half of it breaks nothing visible, which is how a guard
    # stops guarding.
    candidates = eligible_events(detected.events, query_start=query_start, as_of=as_of)

    vectors, population = _vectors(conn, site_id, as_of)
    if not population or not candidates:
        return _result(
            gate_module.evaluate([]),
            None,
            (),
            (),
        )

    query_vector = vectors.get(as_of)
    comparable = [
        (event.start, vectors[event.start])
        for event in candidates
        if event.start in vectors
    ]
    if query_vector is None or not comparable:
        # The condition itself, or every candidate, has a feature missing on its date. NOT a
        # distant analog - an unobserved one, which `similarity.is_comparable` refuses to
        # standardize rather than substituting a value that would become an axis.
        return _result(gate_module.evaluate([]), None, (), ())

    scale = similarity.scale_from(population)
    neighbours = similarity.k_nearest(
        query_vector, comparable, parameters.K_NEAREST, scale
    )

    rate_series = conn.execute(
        RATE_SERIES_SQL, {"location": TARGET_LOCATION, "horizon": TARGET_HORIZON}
    ).fetchall()

    measured = [
        outcomes.measure(neighbour.key, rate_series, parameters.OUTCOME_WINDOW_DAYS)
        for neighbour in neighbours
    ]
    records = tuple(
        Match(
            rank=rank,
            event_start=neighbour.key,
            distance=neighbour.distance,
            outcome_log_return=outcome.log_return,
        )
        for rank, (neighbour, outcome) in enumerate(zip(neighbours, measured), start=1)
    )

    gate_result = gate_module.evaluate(measured)

    # THE SUMMARY IS COMPUTED HERE AND NOWHERE ELSE. Below the gate, on the passing branch only.
    summary = None
    if gate_result.passed:
        summary = outcomes.summarize([o for o in measured if o.complete])

    matches = tuple(
        MatchSummary(rank=r.rank, event_start=r.event_start, distance=r.distance)
        for r in records
    )
    return _result(gate_result, summary, matches, records)


def _vectors(conn, site_id: str, as_of: date):
    """`(date -> vector)` for every date with a complete feature vector, and the z-score population.

    BOTH END AT `as_of`. The population is what `similarity.scale_from` standardizes against, so a
    population running past the query date would score a historical condition against a spread that
    includes the future - leakage in a helper nobody reviews as a model.

    A date missing any one feature is absent from both. `similarity.is_comparable` is what decides,
    and the alternative - substituting a mean for the missing dimension - would make an unobserved
    condition into an average one, which is a fabricated measurement below every gate.
    """
    series = {}
    for feature_name in parameters.SIMILARITY_FEATURES:
        rows = _feature_rows(conn, site_id, feature_name)
        truncated = [row for row in rows if row[0] <= as_of]
        series[feature_name], _ = _series_for(truncated)

    all_dates = set()
    for values in series.values():
        all_dates.update(values)

    vectors = {}
    for day in sorted(all_dates):
        vector = tuple(
            series[name].get(day) for name in parameters.SIMILARITY_FEATURES
        )
        if similarity.is_comparable(vector):
            vectors[day] = vector

    return vectors, list(vectors.values())


def _write(conn, *, as_of, site_id, gate_result, detected, records, signal_run_id,
           signal_q_value, fingerprint) -> int:
    """Write the query row and its matches, and COMMIT. Refusals included.

    Every query is recorded, whatever it decided. A table holding only the queries that produced an
    estimate would make an engine that refuses ninety-nine times in a hundred look like an engine
    that answers - the same disappearing denominator `signals` is built to prevent, one layer up
    and with no q-value to catch it.
    """
    query_id = conn.execute(
        INSERT_QUERY_SQL,
        {
            "as_of_date": as_of,
            "site_id": site_id,
            "feature_vector": list(parameters.SIMILARITY_FEATURES),
            "k": parameters.K_NEAREST,
            "outcome_window_days": parameters.OUTCOME_WINDOW_DAYS,
            "gate_result": gate_result.result,
            "n_raw_detections": detected.n_raw_detections,
            "n_collapsed_events": detected.n_collapsed_events,
            "n_analogs": gate_result.n_analogs,
            "n_consistent": gate_result.n_consistent,
            "signal_run_id": signal_run_id,
            "signal_q_value": signal_q_value,
            "git_sha": _GIT[0],
            "git_dirty": _GIT[1],
            "parameters_hash": fingerprint,
        },
    ).fetchone()[0]

    if records:
        with conn.cursor() as cursor:
            cursor.executemany(
                INSERT_MATCH_SQL,
                [
                    (query_id, r.rank, r.event_start, r.distance, r.outcome_log_return)
                    for r in records
                ],
            )
    conn.commit()
    return query_id


class _GitState:
    """Reads the commit once per process, lazily.

    Lazy because `git_state` shells out and RAISES rather than defaulting when it cannot read a
    sha - which is correct for a run that is about to be recorded, and would be wrong at import
    time, where it would make the module unimportable inside a container with no git.
    """

    def __init__(self):
        self._value = None

    def __getitem__(self, index):
        if self._value is None:
            self._value = git_state()
        return self._value[index]


_GIT = _GitState()


# ---------------------------------------------------------------------------------------------
# The CLI.
# ---------------------------------------------------------------------------------------------


def _print_result(result: AnalogResult, explain: bool) -> None:
    """A human-readable answer. PRINTS AND RETURNS None, like the sweep's `--top`.

    Deliberately not importable as a formatter and deliberately returning nothing: a function that
    hands back a structure is a function something downstream starts consuming, and the shape of
    what downstream may consume is `AnalogResult` - which carries no estimate on a refusal.
    """
    print()
    print(f"  as of {result.as_of}   site {result.site_id}")
    print(f"  parameters {result.parameters_hash[:12]}   k={parameters.K_NEAREST} "
          f"window={parameters.OUTCOME_WINDOW_DAYS}d")
    print()
    print(f"  {result.sentence}")
    print()
    print(f"  detections {result.n_raw_detections} raw -> "
          f"{result.n_collapsed_events} collapsed events")
    print(f"  gate: {result.gate.result}  analogs={result.gate.n_analogs} "
          f"consistent={result.gate.n_consistent} incomplete={result.gate.n_incomplete}")

    if result.signal_q_value is None:
        print(f"  sweep: {parameters.ENTRY_FEATURE} at {result.site_id} was never scanned - "
              f"no signals row. That is not 'no relationship'; it is 'not measured'.")
    else:
        print(f"  sweep: best q = {result.signal_q_value:.4f} (run {result.signal_run_id}). "
              f"AN ENGINE FINDING CONFIDENT ANALOGS WHERE THE SWEEP FOUND NOTHING HAS A BUG.")

    if explain and result.matches:
        print()
        print("  the k nearest, with their distances - THERE IS NO CUTOFF, AND THIS IS WHAT ONE")
        print("  WOULD BE SET FROM (app/analogs/parameters.py: SIMILARITY_CUTOFF is None):")
        print()
        print("    rank  event_start   distance")
        for match in result.matches:
            print(f"    {match.rank:>4}  {match.event_start}   {match.distance:>8.3f}")
    print()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ask the analog engine what happened the last few times conditions looked like this. "
            "Records every query INCLUDING ITS REFUSALS. Expect refusals: Phase 6 scanned 6,966 "
            "pairs and one passed, at lag 0."
        )
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help=(
            "the date to ask about, YYYY-MM-DD. REQUIRED, and deliberately not defaulted to today: "
            "an as-of date nobody stated is a result nobody can reproduce, and the labelled events "
            "this engine is validated against are historical."
        ),
    )
    parser.add_argument(
        "--site",
        required=True,
        help="USGS site id, e.g. 07032000 for Memphis. The site list is human-owned (migration 0004).",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "print the k nearest analogs with their distances. THIS IS STEP 2 OF THE LIVE "
            "PROCEDURE: no similarity cutoff is set in this project, and one would be set from "
            "looking at these numbers."
        ),
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="answer without writing analog_queries/analog_matches. For exploration only.",
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

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()

    with db.connection() as conn:
        result = query(conn, as_of=as_of, site_id=args.site, persist=not args.no_persist)

    _print_result(result, args.explain)

    # A REFUSAL EXITS ZERO. It is the expected answer on this dataset and it is not an error - a
    # non-zero exit would make "insufficient history" look like a failure to a shell, a cron, or
    # the next person reading a log, and it is the deliverable.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
