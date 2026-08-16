/** The gate passed. The estimate and its denominators, in one block that cannot be split.
 *
 * `data-testid="estimate-block"` wraps the median AND the sweep verdict TOGETHER, and
 * `tests/conclusion.test.tsx` asserts both strings are inside that one element rather than merely
 * somewhere in the document. A document-wide assertion would pass over a layout that put "1 of
 * 6,966" in a footer, which is the exact dishonesty this is meant to prevent: `median +7%` on a card
 * with the denominator collapsed into a tooltip nobody opens is the API's guarantee undone at the
 * last inch.
 *
 * THE ANALOG DATES ARE RENDERED, NOT SUMMARIZED. CLAUDE.md 19: the analog count assumes
 * independence and these analogs are not independent - all four fall inside 2015-2022. 3 of 4 reads
 * the same whether the events span forty years or four, and the reader can only make that discount
 * if the dates travel with the claim.
 */

import type { PassedConclusion } from "../api/types";
import { count, isoDate } from "../format";
import { ProvisionalBand } from "./ProvisionalBand";
import { SpanBar } from "./SpanBar";
import { SweepVerdict } from "./SweepVerdict";

export function ConclusionPassed({ result }: { result: PassedConclusion }) {
  return (
    <section className="verdict" data-testid="conclusion-passed" data-gate="passed">
      <header className="verdict-head">
        <span className="label">Gate passed</span>
        <h2 className="verdict-title">
          {count(result.analogs)} comparable events, {count(result.consistent)}{" "}
          agreeing on direction
        </h2>
      </header>

      <ProvisionalBand />

      <div className="estimate" data-testid="estimate-block">
        <SpanBar
          low={result.range_pct[0]}
          high={result.range_pct[1]}
          median={result.median_pct}
        />
        <SweepVerdict sweep={result.sweep} />
      </div>

      <p className="verdict-sentence">{result.sentence}</p>

      <div className="verdict-detail">
        <div>
          <span className="label">Analogs</span>
          <table className="ledger">
            <thead>
              <tr>
                <th scope="col">Rank</th>
                <th scope="col">Event start</th>
                <th scope="col">Distance</th>
              </tr>
            </thead>
            <tbody>
              {result.matches.map((m) => (
                <tr key={m.rank}>
                  <td className="num">{count(m.rank)}</td>
                  <td>{isoDate(m.event_start)}</td>
                  <td className="num">{m.distance.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="footnote">
            Every analog falls inside 2015–2022. Four events in eight years are not
            four independent draws.
          </p>
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
            <dt className="label">Outcome window</dt>
            <dd className="num">{count(result.window_days)} days</dd>
          </div>
          <div>
            <dt className="label">Parameters</dt>
            <dd className="mono">{result.parameters_hash}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
