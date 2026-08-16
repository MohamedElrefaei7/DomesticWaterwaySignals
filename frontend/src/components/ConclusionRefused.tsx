/** The gate refused. A FIRST-CLASS ANSWER, RENDERED AT FULL WEIGHT.
 *
 * THIS COMPONENT CANNOT DISPLAY AN ESTIMATE, AND THE REASON IS STRUCTURAL RATHER THAN CAREFUL.
 * `RefusedConclusion` does not declare `median_pct`, `range_pct` or `matches`, so
 * `result.median_pct` here is a COMPILE ERROR under `strict` - not an `undefined` that renders as a
 * blank, and not a `null` that `?? 0` turns into a confident zero. It imports neither `SpanBar` nor
 * `ConclusionPassed`, and `tests/conclusion.test.tsx` asserts that statically, because an import is
 * how the capability would come back.
 *
 * IT USES NO ERROR STYLING. A refusal is the correct output of a working system - Phase 6 scanned
 * 6,966 pairs and one passed, so refusing is what this engine should mostly do. Rendering it in
 * warning red would tell a reader the system is broken while it is behaving exactly as designed,
 * and would editorialize the science by making the rarer answer look like the healthy one. The
 * accent colour in this app means "the river is binding" or "a job is overdue". It never means
 * "the engine declined to guess".
 *
 * The reasons stay distinct because they are different news (CLAUDE.md 19): too few events is a
 * coverage problem that more history fixes; an unmeasurable outcome is a publication problem; an
 * inconsistent direction is a statement about the relationship that no amount of ingest improves.
 */

import type { RefusedConclusion } from "../api/types";
import { count } from "../format";
import { SweepVerdict } from "./SweepVerdict";

const REASONS: Record<string, { title: string; news: string }> = {
  insufficient_analogs: {
    title: "Not enough comparable events",
    news: "More history would change this. The record is too short at this site for the gate to be evaluated.",
  },
  inconsistent_direction: {
    title: "The comparable events disagree",
    news: "More history would not change this on its own. The events that exist point in different directions, which is a statement about the relationship.",
  },
  incomplete_outcomes: {
    title: "Outcomes could not be measured",
    news: "A publication gap, not a river one. Some analogs have no measurable outcome at the end of the window, and none was filled in.",
  },
};

export function ConclusionRefused({ result }: { result: RefusedConclusion }) {
  const reason = REASONS[result.reason] ?? {
    title: "Refused",
    news: `The engine reported the reason "${result.reason}".`,
  };

  return (
    <section className="verdict" data-testid="conclusion-refused" data-gate="refused">
      <header className="verdict-head">
        <span className="label">Insufficient history</span>
        <h2 className="verdict-title">{reason.title}</h2>
      </header>

      <p className="verdict-sentence">{result.sentence}</p>
      <p className="verdict-news">{reason.news}</p>

      <div className="estimate" data-testid="estimate-block">
        <dl className="facts facts-wide">
          <div>
            <dt className="label">Comparable events found</dt>
            <dd className="num big">{count(result.analogs)}</dd>
          </div>
          <div>
            <dt className="label">Required before reporting</dt>
            <dd className="num big">{count(result.required)}</dd>
          </div>
          <div>
            <dt className="label">Outcomes unmeasurable</dt>
            <dd className="num big">{count(result.incomplete)}</dd>
          </div>
        </dl>
        <SweepVerdict sweep={result.sweep} />
      </div>

      <dl className="facts">
        <div>
          <dt className="label">Detections</dt>
          <dd className="num">
            {count(result.detections.raw)} raw →{" "}
            {count(result.detections.collapsed)} collapsed
          </dd>
        </div>
        <div>
          <dt className="label">Parameters</dt>
          <dd className="mono">{result.parameters_hash}</dd>
        </div>
      </dl>
    </section>
  );
}
