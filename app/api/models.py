"""The response shapes. NO NUMERIC DEFAULTS ANYWHERE, AND THE REFUSAL SHAPE HAS NO ESTIMATE KEYS.

TWO PROPERTIES CARRY THIS WHOLE MODULE, AND BOTH ARE ONE CHARACTER AWAY FROM BEING LOST.

1. A NULLABLE FIELD IS DECLARED NULLABLE AND GIVEN NO DEFAULT.

   `pct_of_tariff: float = 0` is shorter, silences a type checker, and converts a winter navigation
   closure into a week when barge freight was free (migration 0017). `tons: float = 0` converts a
   reporting gap into "no grain moved" - and USDA publishes an explicit `tons = 0` on 8,218 of
   26,144 records, so zero is the routine way that source says nothing moved and its silence means
   something else entirely (migration 0018). Phase 4 spent three commits establishing that those
   two NULLs mean different things; a default in this file un-establishes both.

   `float | None` with NO default is what preserves it: the field is REQUIRED - a route that forgot
   to read the column fails loudly instead of emitting a plausible zero - and NULL reaches the
   client as `null`.

   `tests/api/test_contract.py::test_no_response_model_declares_a_numeric_default` introspects
   every model in this module, so a field added later is covered without anybody remembering.

2. A REFUSAL IS A DIFFERENT SHAPE, NOT THE SAME SHAPE WITH NULLS IN IT.

   `RefusedConclusion` does not declare `median_pct`, `range_pct` or `matches`. Not "declares them
   as None" - DOES NOT DECLARE THEM, so they are absent from the serialized body, and A CLIENT
   CANNOT DEFAULT A KEY THAT DOES NOT EXIST. `median_pct ?? 0` renders `0%`; `body.median_pct` on a
   key that was never there is `undefined`, and every renderer in the world shows that as a gap.

   This is the serialization-boundary form of Phase 7's decision that a refused query never has an
   estimate COMPUTED. `outcomes.summarize` is not called on the refusing branch, so there is no
   number to withhold; these models make sure there is no KEY to fill in either.

WHY A DISCRIMINATED UNION RATHER THAN ONE MODEL WITH OPTIONAL FIELDS: so the two shapes are two
shapes in the OpenAPI document as well as at runtime. A generated client gets two types and has to
branch; a single type with everything optional gets one type where every estimate is `float | null`
and the branch is the client author's to remember.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------------------------
# Health.
# ---------------------------------------------------------------------------------------------


class JobHealth(BaseModel):
    """One scheduled job, as the cadence table and `job_runs` describe it.

    `last_success` IS THE MOST RECENT `success` ROW'S finished_at and nothing else (CLAUDE.md § 4).
    A job failing nightly has recent activity and no recent success, and the two must not be
    reported by the same field. This model does not compute it - `app.orchestration.heartbeat`
    does, and the route maps its verdict.
    """

    job_name: str
    # NULL means no successful run is on record, which is the most alarming state in the table -
    # not a quiet one. `overdue` is True alongside it, computed by the heartbeat.
    last_success: datetime | None
    age_seconds: float | None
    overdue_after_seconds: float
    overdue: bool


class TableFreshness(BaseModel):
    """One registered table's data freshness. MEASURED FROM THE DATA, NOT FROM THE PROCESS.

    `newest` is MAX(the source's own timestamp column) on the ingested table (CLAUDE.md § 4). A
    source that accepts a connection and delivers nothing looks healthy at every layer except this
    one, and a job-status field cannot see it.
    """

    table: str
    job_name: str
    newest: datetime | None
    age_seconds: float | None
    max_staleness_seconds: float
    stale: bool
    # A registered table that cannot be queried is a FAILED check, never a skipped one
    # (CLAUDE.md § 13). The class of failure is named; the exception text is not - see errors.py.
    error: str | None


class HealthResponse(BaseModel):
    """Per-job and per-table, never a bare boolean, and returned with HTTP 200 while degraded.

    NEVER `{"status": "ok"}`. A one-word health check is what let the prior project record
    "Completed" while the whole stack had been down for two and a half months (CLAUDE.md § 2).

    200 WHILE DEGRADED IS THE DECISION. An uptime monitor that goes red on a stale ingest job is
    indistinguishable from one that goes red because the API is down, and those need different
    responses at different hours. `degraded` is a field so a monitor can alert on the field.
    """

    degraded: bool
    checked_at: datetime
    jobs: list[JobHealth]
    data: list[TableFreshness]


# ---------------------------------------------------------------------------------------------
# The conclusion. Three shapes, one discriminator.
# ---------------------------------------------------------------------------------------------


class SweepVerdict(BaseModel):
    """The lead-lag sweep's verdict on the relationship the analog output assumes.

    RIDES ON EVERY CONCLUSION RESPONSE, PASSING OR REFUSED (Phase 7 decision 8). An analog output
    must never be readable without it, and serialization is where that coupling is most likely to
    be dropped, because this block looks like metadata a frontend does not need.

    `scanned_pairs` beside `passing_pairs` because a passing count without its denominator is the
    dishonest form (CLAUDE.md § 18). Phase 6 scanned 6,966 pairs and one passed; "1 passing" and
    "1 of 6,966" are different claims and only the second one is a result.

    ALL FIVE ARE NULLABLE, and null means the sweep never scanned this pair - which is NOT "no
    relationship", it is "not measured". Migration 0024 keeps `run_id` and `q_value` together with
    a bidirectional CHECK so a q-value can never appear without the run it came from.
    """

    best_q: float | None
    run_id: int | None
    grid_size: int | None
    passing_pairs: int | None
    scanned_pairs: int | None


class DetectionCounts(BaseModel):
    """Raw detections and collapsed events. BOTH, ALWAYS (Phase 7 decision 2).

    A sustained low-water period produces a detection every day it continues, so one event would
    satisfy "≥ 4 analogs" several times over. `collapsed` is what the gate consumed; `raw` is what
    the detector saw. A history whose raw count is 161 and whose collapsed count is 6 is the honest
    description of this dataset, and it is only readable if both are kept.
    """

    raw: int
    collapsed: int


class MatchSummary(BaseModel):
    """One analog: rank, date, distance. NO OUTCOME.

    The per-analog outcome is deliberately absent, exactly as it is from `engine.MatchSummary`: a
    caller holding per-analog outcomes can compute the median the gate refused to give them, and a
    refusal that can be undone downstream is not a refusal.

    THE DISTANCE IS HERE BECAUSE THERE IS NO SIMILARITY CUTOFF IN THIS PROJECT
    (`parameters.SIMILARITY_CUTOFF` is None) and one would be set from looking at these numbers.

    THE DATES ARE HERE BECAUSE THE ANALOG COUNT ASSUMES INDEPENDENCE AND THE ANALOGS ARE NOT
    INDEPENDENT (CLAUDE.md § 19). Every analog behind both of this project's passing queries falls
    inside 2015-2022, and the 2023 pass rests on the immediately preceding year. 4 of 4 agreeing is
    the same number whether the events span forty years or four; the reader makes that discount,
    and they can only make it if the dates travel with the claim.
    """

    rank: int
    event_start: date
    distance: float


class PassedConclusion(BaseModel):
    """The gate passed. The sentence AND its denominators, in one response.

    `sentence` already carries K, D and the window inside the prose (CLAUDE.md § 19) because the
    sentence is the unit that gets quoted. `analogs`, `consistent` and `window_days` are the same
    facts as fields, in the SAME response, so a client cannot render one without having the other -
    which is the failure a split between a "claim" endpoint and a "detail" endpoint produces.
    """

    gate: Literal["passed"] = "passed"
    site_id: str
    as_of: date
    sentence: str
    analogs: int
    consistent: int
    window_days: int
    median_pct: float
    # [low, high] as measured, in the order low-then-high. A tuple rather than two fields so a
    # client cannot render a range with one end missing.
    range_pct: tuple[float, float]
    matches: list[MatchSummary]
    detections: DetectionCounts
    parameters_hash: str
    sweep: SweepVerdict
    computed_at: datetime


class RefusedConclusion(BaseModel):
    """The gate refused. THERE IS NO median_pct, range_pct OR matches FIELD ON THIS CLASS.

    Read the class body: the estimate keys are not here. That absence is the contract, and it is
    asserted three ways in `tests/api/test_contract.py` - by name, by a recursive walk over every
    numeric leaf, and by the mutation that adds `median_pct: float | None = None` and watches two
    tests go red.

    `analogs` and `required` are counts and a threshold. They are the two numbers that make a
    refusal actionable rather than merely negative: too few events is a coverage problem that more
    history fixes, and knowing how many were needed is what says how far short it fell.

    `reason` is the engine's own vocabulary, passed through: `insufficient_analogs`,
    `inconsistent_direction`, `incomplete_outcomes`. They stay distinct because they are different
    news - only one of them says more ingest would help (CLAUDE.md § 19).
    """

    gate: Literal["refused"] = "refused"
    reason: str
    site_id: str
    as_of: date
    sentence: str
    analogs: int
    required: int
    incomplete: int
    detections: DetectionCounts
    parameters_hash: str
    sweep: SweepVerdict
    computed_at: datetime


class NoCurrentEventConclusion(BaseModel):
    """The river is not in a low-water condition. A DISTINCT GATE VALUE, NOT A REFUSAL.

    Phase 7 built this as its own verdict for a reason that survives into the API: asking about a
    river that is not doing the thing is not a coverage problem and must not read as one. A quiet
    river reported as `insufficient_analogs` reads as "we lack the history", which sends somebody
    to buy more data for a question nobody asked.

    Measured on the instance: `--as-of 2022-09-06` returns exactly this, a week before the same
    site's 2022 event opens.
    """

    gate: Literal["no_current_event"] = "no_current_event"
    site_id: str
    as_of: date
    sentence: str
    detections: DetectionCounts
    parameters_hash: str
    sweep: SweepVerdict
    computed_at: datetime


ConclusionResponse = Annotated[
    Union[PassedConclusion, RefusedConclusion, NoCurrentEventConclusion],
    Field(discriminator="gate"),
]


# ---------------------------------------------------------------------------------------------
# Lists. Every one of them carries its own bound and its own denominator.
# ---------------------------------------------------------------------------------------------


class ListEnvelope(BaseModel):
    """`limit`, `offset` and `total` on every list response. Decision 6.

    `total` MATTERS BEYOND PAGINATION, and that is why it is on the envelope rather than in a
    `Link` header nobody reads. A client that receives 500 of 8,260 rate rows and does not know
    there are 8,260 will draw a chart of a truncated series, and IT WILL LOOK LIKE A REAL SERIES -
    smooth, plausible, ending on a date that is not the end of anything. That is CLAUDE.md § 2's
    theme 1 arriving at the presentation layer, where nobody downstream can check it.

    `total` is the count of rows matching the filters WITHOUT limit or offset applied. A `total`
    that echoed `len(rows)` would satisfy every shape test in this file and defeat the entire
    purpose, which is why `test_total_reflects_the_unpaginated_count` seeds more rows than the
    limit and reads a real database.
    """

    limit: int
    offset: int
    total: int


class Gauge(BaseModel):
    """One seeded gauge, with its DECLARED record starts and its OBSERVED coverage beside them.

    BOTH, DELIBERATELY, AND THE PAIR IS THE POINT (CLAUDE.md § 15): a catalog's date range reports
    an envelope, not what an endpoint will serve, and where they disagree WHAT IS SERVED IS WHAT IS
    TRUE. Memphis is catalogued 1933-2026 with 26,886 values and serves nothing between 1994 and
    2014; its daily record start is seeded 2014-10-01 and its observed coverage is what the table
    actually holds. Reporting only the declared value would restate the catalog's claim as fact.

    `observed_days` is a COUNT, so 0 is a measurement - "we looked, there are none". The bounds are
    dates and are null when there is no first or last day to name. That asymmetry is deliberate and
    is the same distinction migration 0018 makes between a reported zero and an absent field.
    """

    site_id: str
    name: str
    river: str
    tier: int
    available_params: list[str]
    native_cadence_minutes: int
    declared_iv_record_start: date | None
    declared_dv_record_start: date | None
    observed_start: date | None
    observed_end: date | None
    observed_days: int


class GaugeList(ListEnvelope):
    rows: list[Gauge]


class GaugeReading(BaseModel):
    """One daily value from `gauge_series`, WITH THE SOURCE IT CAME FROM.

    `source` is not decoration. The instantaneous-derived mean and the published daily mean are not
    identical measurements - different day boundaries, different sampling - so a series that
    switches source mid-history HAS A SEAM, and this column is what keeps that seam visible instead
    of hidden (CLAUDE.md § 15). The precedence rule itself lives once, in the view; nothing here
    re-derives it.
    """

    date: date
    param_code: str
    value: float | None
    source: str


class GaugeSeries(ListEnvelope):
    site_id: str
    start: date
    end: date
    rows: list[GaugeReading]


class BargeRate(BaseModel):
    """One published weekly rate. `pct_of_tariff` IS NULLABLE AND NULL SURVIVES TO THE CLIENT.

    NULL means USDA published no `rate` field for this location-week, which in 661 of 774 cases is
    WINTER NAVIGATION CLOSURE ON THE UPPER MISSISSIPPI - a fact about the river, not a gap in
    ingest (migration 0017). A zero would claim barge freight was free that week, which is never
    true and drags every average over the series toward it.

    `location` is USDA's own field name and its own vocabulary, stored verbatim. The query
    parameter is `segment` for the reason stated in routes/series.py; the RESPONSE says `location`,
    because that is what the source calls it and what the column is called.
    """

    location: str
    week_ending: date
    horizon: str
    pct_of_tariff: float | None
    # The calendar month a forward quote applies to. NULL on the `nearby` horizon, where there is
    # no forward month to name - a legitimately absent field rather than a missing one, which is
    # the distinction migration 0016 makes for the three sibling datasets.
    rate_month: int | None


class BargeRateList(ListEnvelope):
    start: date
    end: date
    rows: list[BargeRate]


class LockMovement(BaseModel):
    """One published lock movement. NOT SUMMED, NOT COALESCED, IN EITHER DIRECTION.

    `tons` is nullable AND meaningfully zero, and the two mean different things:

        0     REPORTED AS NONE. 8,218 of 26,144 records. Near-zero movement during a low-water
              event is the signal this project studies, so these are the observations that matter
              most.
        NULL  NOT REPORTED. 108 of 26,144, on three locks. A reporting gap says nothing about the
              river.

    Coalescing either way is one line long and looks like tidying (CLAUDE.md § 16). Both directions
    have their own test here, because a single test can be satisfied by a wrong implementation.

    ROWS ARE RETURNED PER COMMODITY, NEVER AGGREGATED. `lock_movements` is sparse - 1,434 zeros in
    2,840 rows at MS Lock 15 - which makes how to aggregate a modelling decision (CLAUDE.md § 1). A
    sum over commodities silently decides that a NULL contributes nothing, which is the coalesce
    this model refuses, performed one layer up where nothing can see it.

    `tons` IS THE ONLY MEASURE. The source publishes no barge count and the dataset is
    downbound-only by construction, so migration 0016 dropped both columns rather than leaving them
    always-NULL - a column that would always be NULL looks like data, and every query filtering on
    it returns nothing forever with nothing to say why (CLAUDE.md § 16). This model does not
    re-create them.
    """

    lock: str
    week_ending: date
    commodity: str
    tons: float | None


class LockMovementList(ListEnvelope):
    start: date
    end: date
    rows: list[LockMovement]


# ---------------------------------------------------------------------------------------------
# Signals. The sweep's own table, and the denominator is the default view.
# ---------------------------------------------------------------------------------------------


class Signal(BaseModel):
    """One scanned combination. `q_value` AND `grid_size` TRAVEL TOGETHER OR NOT AT ALL.

    A q-value is meaningless without knowing how many tests it was adjusted against (CLAUDE.md
    § 18). A later run over a narrower grid produces smaller q-values IN THE SAME COLUMN, IN THE
    SAME UNITS, from a different experiment - so `grid_size` rides on the row rather than being
    looked up from the run, and it is denormalized in the database for exactly this reason.

    `status` distinguishes a scanned pair from a refused one. A refusal is a row with a stated
    status, never an omission: an omitted pair is indistinguishable from a pair nobody enumerated,
    and the count of enumerated pairs is the denominator this table exists to preserve.

    `directional_consistency` never appears without `folds`: 4 of 5 and 40 of 50 are both 80% and
    are not equally informative.
    """

    run_id: int
    feature_name: str
    site_id: str
    series_column: str
    target_name: str
    horizon_days: int
    # SIGNED. A negative lag means the target moved before the predictor, which is a finding about
    # the world rather than an artefact to filter (CLAUDE.md § 18).
    lag_days: int
    regime: str
    status: str
    statistic: float | None
    p_value: float | None
    q_value: float | None
    grid_size: int
    n_tests_adjusted: int
    n_observations: int
    n_effective: float | None
    folds: int | None
    directional_consistency: float | None
    passes_gate: bool


class SignalRun(BaseModel):
    """One sweep run: its parameters, its commit, and its two counts.

    `git_dirty` beside `git_sha` rather than a `-dirty` suffix: a dirty run's sha names a commit
    whose code is NOT what ran, so the results are worth keeping AND are not reproducible, and
    those are two different facts about one row.

    `passing_pairs` is never emitted without `scanned_pairs`.
    """

    run_id: int
    started_at: datetime
    finished_at: datetime | None
    grid_size: int
    lag_min: int
    lag_max: int
    horizons: list[int]
    regimes: list[str]
    feature_filter: str | None
    git_sha: str
    git_dirty: bool
    seed: int | None
    scanned_pairs: int
    passing_pairs: int


class SignalList(ListEnvelope):
    """The rows, the run they came from, and when this was computed.

    `run` is on the envelope because a page of signal rows without the run's grid size and scanned
    count is a page of q-values with no experiment attached.
    """

    run: SignalRun | None
    passing_only: bool
    computed_at: datetime
    rows: list[Signal]


class SignalRunList(ListEnvelope):
    rows: list[SignalRun]
