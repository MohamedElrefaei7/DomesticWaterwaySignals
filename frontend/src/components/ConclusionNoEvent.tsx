/** The river is not constrained. A STATEMENT ABOUT THE WORLD, NOT AN ABSENCE OF ONE.
 *
 * DISTINCT FROM A REFUSAL, DELIBERATELY, AND THE DISTINCTION IS THE POINT. "We do not have enough
 * history" and "the river is running normally" are different facts and only one of them is a
 * problem. Rendering a quiet river as `insufficient_analogs` reads as "we lack the data", which
 * sends somebody to buy more of it for a question nobody asked - Phase 7 built this as its own
 * verdict for exactly that reason and the API kept it as its own shape.
 *
 * So: different copy, different visual treatment, and no error styling on either this or the
 * refusal. This one is the calmest surface in the app, because it is the answer that means nothing
 * is wrong.
 *
 * Measured on the instance at `as_of=2022-09-06` - a week before the same site's 2022 event opens,
 * inside the year of a real event. The detector is date-sensitive across that boundary, which is
 * what makes this state a real answer rather than a default.
 */

import type { NoCurrentEventConclusion } from "../api/types";
import { count, isoDate } from "../format";
import { SweepVerdict } from "./SweepVerdict";

export function ConclusionNoEvent({
  result,
}: {
  result: NoCurrentEventConclusion;
}) {
  return (
    <section
      className="verdict verdict-quiet"
      data-testid="conclusion-no-event"
      data-gate="no_current_event"
    >
      <header className="verdict-head">
        <span className="label">No current event</span>
        <h2 className="verdict-title">The river is not binding here</h2>
      </header>

      <p className="verdict-sentence">{result.sentence}</p>
      <p className="verdict-news">
        Nothing was refused and nothing is missing. On {isoDate(result.as_of)} this
        site was not in a low-water condition, so no comparable historical
        conditions were looked for.
      </p>

      <div className="estimate" data-testid="estimate-block">
        <dl className="facts facts-wide">
          <div>
            <dt className="label">Detections on this date</dt>
            <dd className="num big">{count(result.detections.raw)}</dd>
          </div>
          <div>
            <dt className="label">Collapsed events</dt>
            <dd className="num big">{count(result.detections.collapsed)}</dd>
          </div>
        </dl>
        <SweepVerdict sweep={result.sweep} />
      </div>
    </section>
  );
}
