/** The sweep's verdict. RIDES ON ALL THREE GATE SHAPES, INCLUDING BOTH REFUSALS.
 *
 * ONE COMPONENT RATHER THAN THREE COPIES, for the same reason the API shares `run_summary` between
 * the conclusion route and the signals route: two renderings of one denominator can disagree about
 * it, and the disagreement is invisible because each looks right on its own page.
 *
 * IT RENDERS NO ESTIMATE. Counts and a q-value are what CLAUDE.md 20 permits to survive into a
 * refusal - "a count, a stated threshold, or a sweep statistic" - so this component is safe to
 * import from the refusal shapes, and `tests/conclusion.test.tsx` asserts that it never reaches for
 * `median_pct`, `range_pct` or `matches`.
 *
 * NULL IS "NOT MEASURED", NOT "NO RELATIONSHIP". A null verdict means the sweep never scanned this
 * pair. Rendering that as a blank, or worse as "no relationship found", converts an absence of
 * evidence into evidence of absence at the last inch of a project built to keep them apart. It gets
 * the hatch, like every other unmeasured surface in this app.
 */

import type { SweepVerdict as SweepVerdictData } from "../api/types";
import { count, decimal } from "../format";

export function SweepVerdict({ sweep }: { sweep: SweepVerdictData }) {
  const scanned = sweep.scanned_pairs;
  const passing = sweep.passing_pairs;

  if (scanned === null || passing === null) {
    return (
      <div className="sweep hatch" data-testid="sweep-verdict">
        <span className="label">Lead-lag sweep</span>
        <p className="sweep-line">
          This pair was never scanned. That is <strong>not measured</strong>, which
          is a different statement from no relationship.
        </p>
      </div>
    );
  }

  return (
    <div className="sweep" data-testid="sweep-verdict">
      <span className="label">Lead-lag sweep</span>
      <p className="sweep-line">
        <strong data-testid="sweep-denominator">
          {count(passing)} of {count(scanned)} pairs
        </strong>{" "}
        passed correction
        {sweep.best_q !== null ? (
          <> · best q = {decimal(sweep.best_q, 4)}</>
        ) : null}
        {sweep.run_id !== null ? <> · run {count(sweep.run_id)}</> : null}
      </p>
    </div>
  );
}
