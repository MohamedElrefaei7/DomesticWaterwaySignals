/** Mirrors app/api/models.py. THE REFUSAL TYPES DECLARE NO ESTIMATE FIELDS.
 *
 * This file is the type-level half of CLAUDE.md 20's central guarantee. `RefusedConclusion` and
 * `NoCurrentEventConclusion` do not declare `median_pct`, `range_pct` or `matches` - not "declare
 * them as null", DO NOT DECLARE THEM - exactly as the pydantic models do not. So
 * `conclusion.median_pct` on a refusal is a COMPILE ERROR under `strict`, not a runtime `undefined`
 * that renders as a blank.
 *
 * That is the whole reason this is a discriminated union on `gate` rather than one interface with
 * optional fields. One interface with `median_pct?: number` would compile
 * `{conclusion.median_pct ?? 0}` happily, and the tsc gate would have nothing to say about the one
 * line in this codebase that would undo four layers of work.
 */

export interface JobHealth {
  job_name: string;
  /** null means NO SUCCESSFUL RUN IS ON RECORD, which is the most alarming state in the table and
   *  not a quiet one. Measured on both USDA jobs 2026-08-16. `overdue` is true alongside it. */
  last_success: string | null;
  age_seconds: number | null;
  overdue_after_seconds: number;
  overdue: boolean;
}

export interface TableFreshness {
  table: string;
  job_name: string;
  newest: string | null;
  age_seconds: number | null;
  max_staleness_seconds: number;
  stale: boolean;
  error: string | null;
}

export interface HealthResponse {
  degraded: boolean;
  checked_at: string;
  jobs: JobHealth[];
  data: TableFreshness[];
}

/** All five nullable. NULL MEANS THE PAIR WAS NEVER SCANNED - "not measured", not "no
 *  relationship". The UI renders those two differently; see SweepVerdictBlock. */
export interface SweepVerdict {
  best_q: number | null;
  run_id: number | null;
  grid_size: number | null;
  passing_pairs: number | null;
  scanned_pairs: number | null;
}

export interface DetectionCounts {
  raw: number;
  collapsed: number;
}

export interface MatchSummary {
  rank: number;
  event_start: string;
  distance: number;
}

export interface PassedConclusion {
  gate: "passed";
  site_id: string;
  as_of: string;
  sentence: string;
  analogs: number;
  consistent: number;
  window_days: number;
  median_pct: number;
  /** [low, high], in that order. A tuple so a client cannot render a range with one end missing. */
  range_pct: [number, number];
  matches: MatchSummary[];
  detections: DetectionCounts;
  parameters_hash: string;
  sweep: SweepVerdict;
  computed_at: string;
}

/** NO median_pct. NO range_pct. NO matches. Read the interface body - the absence is the contract. */
export interface RefusedConclusion {
  gate: "refused";
  reason: string;
  site_id: string;
  as_of: string;
  sentence: string;
  analogs: number;
  required: number;
  incomplete: number;
  detections: DetectionCounts;
  parameters_hash: string;
  sweep: SweepVerdict;
  computed_at: string;
}

/** A quiet river is not a coverage problem. Its own shape, for that reason. */
export interface NoCurrentEventConclusion {
  gate: "no_current_event";
  site_id: string;
  as_of: string;
  sentence: string;
  detections: DetectionCounts;
  parameters_hash: string;
  sweep: SweepVerdict;
  computed_at: string;
}

export type ConclusionResponse =
  | PassedConclusion
  | RefusedConclusion
  | NoCurrentEventConclusion;

export interface ListEnvelope {
  limit: number;
  offset: number;
  /** The count matching the filters WITHOUT limit/offset. A client holding 500 of 8,260 rows and
   *  not knowing there are 8,260 draws a truncated series that looks like a real one. */
  total: number;
}

export interface Gauge {
  site_id: string;
  name: string;
  river: string;
  tier: number;
  available_params: string[];
  native_cadence_minutes: number;
  declared_iv_record_start: string | null;
  declared_dv_record_start: string | null;
  observed_start: string | null;
  observed_end: string | null;
  /** A COUNT, so 0 is a measurement - "we looked, there are none" - while the bounds are dates and
   *  are null when there is no first or last day to name. */
  observed_days: number;
}

export interface GaugeList extends ListEnvelope {
  rows: Gauge[];
}

export interface GaugeReading {
  date: string;
  param_code: string;
  value: number | null;
  source: string;
}

export interface GaugeSeries extends ListEnvelope {
  site_id: string;
  start: string;
  end: string;
  rows: GaugeReading[];
}

export interface BargeRate {
  location: string;
  week_ending: string;
  horizon: string;
  /** NULL is winter navigation closure on the upper Mississippi in 661 of 774 cases - a fact about
   *  the river. A zero would claim freight was free that week. */
  pct_of_tariff: number | null;
  rate_month: number | null;
}

export interface BargeRateList extends ListEnvelope {
  start: string;
  end: string;
  rows: BargeRate[];
}

export interface LockMovement {
  lock: string;
  week_ending: string;
  commodity: string;
  /** 0 is "reported as none" (8,218 of 26,144 records); null is "not reported" (108). Different
   *  claims. Nothing in this frontend sums this column - see client.ts. */
  tons: number | null;
}

export interface LockMovementList extends ListEnvelope {
  start: string;
  end: string;
  rows: LockMovement[];
}

export interface Signal {
  run_id: number;
  feature_name: string;
  site_id: string;
  series_column: string;
  target_name: string;
  horizon_days: number;
  /** Signed. A negative lag means the target moved before the predictor. */
  lag_days: number;
  regime: string;
  status: string;
  statistic: number | null;
  p_value: number | null;
  q_value: number | null;
  grid_size: number;
  n_tests_adjusted: number;
  n_observations: number;
  n_effective: number | null;
  folds: number | null;
  directional_consistency: number | null;
  passes_gate: boolean;
}

export interface SignalRun {
  run_id: number;
  started_at: string;
  finished_at: string | null;
  grid_size: number;
  lag_min: number;
  lag_max: number;
  horizons: number[];
  regimes: string[];
  feature_filter: string | null;
  git_sha: string;
  git_dirty: boolean;
  seed: number | null;
  scanned_pairs: number;
  passing_pairs: number;
}

export interface SignalList extends ListEnvelope {
  run: SignalRun | null;
  passing_only: boolean;
  computed_at: string;
  rows: Signal[];
}

export interface SignalRunList extends ListEnvelope {
  rows: SignalRun[];
}
