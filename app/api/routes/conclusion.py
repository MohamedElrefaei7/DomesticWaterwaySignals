"""`/api/conclusion`. THE ENGINE'S ANSWER, SERIALIZED WITHOUT LOSING ANYTHING IT REFUSED TO SAY.

THIS ROUTE COMPUTES NOTHING. It calls `app.analogs.engine.query`, reads the sweep's run summary,
and chooses which of three response models to build from `result.gate.result`. Every number it
emits was produced beneath it.

THE THREE SHAPES, AND WHY THEY ARE THREE CLASSES RATHER THAN ONE
-----------------------------------------------------------------
    passed              carries the sentence, its denominators, the estimate, and the analogs
    refused             carries the sentence, its counts, and NO ESTIMATE KEYS AT ALL
    no_current_event    carries the sentence and the counts; there was no condition to match

A single model with `median_pct: float | None` would serialize a refusal as `{"median_pct": null}`,
and that is ONE FRONTEND DEFAULT AWAY FROM RENDERING `0%`. `median_pct ?? 0`, `Number(x) || 0`,
a chart library's `defaultValue` - each is a reasonable line of client code and each converts a
refusal into a confident claim of no change. An absent key cannot be defaulted; it is `undefined`
and every renderer shows a gap.

That is the serialization-boundary form of Phase 7's stronger property: on a refusal the estimate
is NEVER COMPUTED. `outcomes.summarize` is not called on the refusing branch, so `result.summary`
is None and there is nothing here to withhold - this module simply has no branch that could read
one.

`no_current_event` IS ITS OWN VALUE, NOT A REFUSAL. Asking about a river that is not in a low-water
condition is not a coverage problem and must not read as one; collapsing it into "insufficient
history" would make an ordinary Tuesday look like a data gap somebody should go and fill.

THE SWEEP'S VERDICT RIDES ON ALL THREE
---------------------------------------
Phase 7 decision 8: an analog output must never be readable without the sweep's verdict beside it.
Serialization is where that coupling is most likely to be dropped, because the block looks like
metadata a frontend does not need - and this project's own numbers are why it is not:

    Phase 6 scanned 6,966 pairs and ONE passed, at lag 0, with zero passing rows at any non-zero
    lag in either direction. An engine that finds confident analogs where the sweep found no
    relationship has a bug, not a discovery (CLAUDE.md § 19).

`scanned_pairs` beside `passing_pairs` for the same reason the sweep stores both: 1 passing reads
as a finding, 1 of 6,966 reads as the top of a distribution.

THE API DOES NOT PERSIST. `persist=False`, and it is the other half of "read-only".
`engine.query`'s default writes an `analog_queries` row and commits; leaving the default would make
every HTTP request a write, which decision 1 forbids and which the deployed read-only role would
reject at runtime. THE CONSEQUENCE IS STATED RATHER THAN HIDDEN: `analog_queries` is the CLI's
research log and does not record questions asked through this API.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request

from app.analogs import engine, gate as gate_module, parameters
from app.api import models
from app.api.cache import CONCLUSION_CACHE, key_from_request
from app.api.dependencies import get_connection, run_summary

router = APIRouter(prefix="/api", tags=["conclusion"])


def _sweep_verdict(conn, result) -> models.SweepVerdict:
    """The sweep's verdict on the pair this answer assumes, as a block that is always present.

    NULLS HERE MEAN "NEVER SCANNED", NOT "NO RELATIONSHIP", and the two are different facts about
    the dataset. `discharge_min` at Memphis and Vicksburg was skipped as degenerate (it is
    `discharge_mean` there, Phase 5 finding 3); a pair that was never scanned has nothing to say
    either way, while a scanned pair with a large q-value says the sweep looked and found nothing.
    Emitting 1.0 or 0 for the unscanned case would collapse them.
    """
    summary = run_summary(conn, result.signal_run_id)
    if summary is None:
        return models.SweepVerdict(
            best_q=result.signal_q_value,
            run_id=result.signal_run_id,
            grid_size=None,
            passing_pairs=None,
            scanned_pairs=None,
        )
    return models.SweepVerdict(
        best_q=result.signal_q_value,
        run_id=summary[0],
        grid_size=summary[3],
        passing_pairs=summary[13],
        scanned_pairs=summary[12],
    )


def _detections(result) -> models.DetectionCounts:
    """Both counts, on every shape. Phase 7 decision 2.

    A sustained low-water period produces a detection every day it continues, so the raw count
    alone would let ONE event satisfy "≥ 4 analogs" several times over - manufactured conviction
    from a single coincidence, in the exact form the gate cannot see. The collapsed count is what
    the gate consumed. Measured on the instance: 161 raw to 6 collapsed at Memphis in 2023.
    """
    return models.DetectionCounts(
        raw=result.n_raw_detections, collapsed=result.n_collapsed_events
    )


def _conclusion(conn, *, as_of: date, site_id: str, computed_at: datetime):
    """Ask the engine, then choose a shape from its verdict. NEVER FROM THE PRESENCE OF A SUMMARY.

    Branching on `result.summary is not None` would make a bug in the engine into an estimate here:
    hand this a refusal that somehow arrived with a summary attached and the caution disappears
    silently. `render.sentence` makes the same choice for the same reason and says so.
    """
    result = engine.query(conn, as_of=as_of, site_id=site_id, persist=False)

    sweep = _sweep_verdict(conn, result)
    detections = _detections(result)
    common = {
        "site_id": result.site_id,
        "as_of": result.as_of,
        "sentence": result.sentence,
        "detections": detections,
        "parameters_hash": result.parameters_hash,
        "sweep": sweep,
        "computed_at": computed_at,
    }

    if result.gate.result == gate_module.NO_CURRENT_EVENT:
        return models.NoCurrentEventConclusion(**common)

    if result.gate.result == gate_module.PASSED:
        summary = result.summary
        return models.PassedConclusion(
            **common,
            analogs=result.gate.n_analogs,
            consistent=result.gate.n_consistent,
            # Read from the parameters module, never written down here. The window is a modelling
            # decision fixed before any outcome was inspected (CLAUDE.md § 19); a copy of the
            # number in a route is a second definition that cannot be changed in one place.
            window_days=parameters.OUTCOME_WINDOW_DAYS,
            median_pct=summary.median_percent,
            range_pct=(summary.low_percent, summary.high_percent),
            matches=[
                models.MatchSummary(
                    rank=match.rank,
                    event_start=match.event_start,
                    distance=match.distance,
                )
                for match in result.matches
            ],
        )

    # Every remaining verdict is a refusal, and its reason is passed through rather than
    # re-derived: `insufficient_analogs`, `inconsistent_direction`, `incomplete_outcomes`. They are
    # different news and the API keeps them distinct for the same reason the engine does - only one
    # of them says more ingest would help.
    return models.RefusedConclusion(
        **common,
        reason=result.gate.result,
        analogs=result.gate.n_analogs,
        required=parameters.MIN_ANALOGS,
        incomplete=result.gate.n_incomplete,
    )


@router.get(
    "/conclusion",
    response_model=models.ConclusionResponse,
    summary="The analog engine's answer for one site on one date. Usually a refusal.",
    response_description=(
        "One of three shapes, discriminated by `gate`. A `refused` body has NO `median_pct`, "
        "`range_pct` or `matches` key - they are absent, not null."
    ),
)
def get_conclusion(
    request: Request,
    site_id: str = Query(
        ...,
        description=(
            "USGS site id, e.g. 07032000 for Memphis. REQUIRED: the site list is human-owned "
            "(migration 0004) and this API does not pick a default one."
        ),
    ),
    as_of: date = Query(
        ...,
        description=(
            "The date to ask about. REQUIRED, and deliberately not defaulted to today - an as-of "
            "date nobody stated is a result nobody can reproduce, and the labelled events this "
            "engine is validated against are historical."
        ),
    ),
    conn=Depends(get_connection),
):
    """Cached for 60 seconds on the FULL query string. See app/api/cache.py for why that matters.

    `computed_at` reaches the body from the cache, so a hit reports when the answer was COMPUTED
    rather than when it was served. A reader seeing a timestamp 40 seconds old knows what they are
    looking at; a timestamp that always says "just now" is a field that looks like provenance and
    carries none.
    """
    cached = CONCLUSION_CACHE.get_or_compute(
        key_from_request(request),
        lambda computed_at: _conclusion(
            conn, as_of=as_of, site_id=site_id, computed_at=computed_at
        ),
    )
    return cached.value
