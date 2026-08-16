/** THE RANGE IS THE FINDING. THE MEDIAN IS A TICK ON IT.
 *
 * The usual rendering of this data is a big `+7%` with a small range underneath, and that rendering
 * is wrong here in a way this project cares about: the 2022 range is −48% to +18% and it SPANS
 * ZERO. A layout that makes the median dominant tells a reader the rate rose. The range is what
 * says nobody knows that.
 *
 * So the bar is the primary object, the zero axis is drawn, and the median is a tick mark. The
 * range label is rendered at a font size >= the median's, asserted by
 * `tests/conclusion.test.tsx::the range is rendered at no smaller a size than the median`.
 *
 * WHY THESE TWO FONT SIZES ARE INLINE. They are the assertion's subject. Reading them from a
 * stylesheet through jsdom's `getComputedStyle` returns the unresolved `var(--text-xl)` rather than
 * a length, so the guard would compare two empty strings and pass over any mutation - which is
 * CLAUDE.md 2's theme 2, a check that verifies the thing responsible for the failure and reports it
 * correct. Inline, the test reads real lengths and the mutation that halves one turns it red.
 */

import { pct } from "../format";

const MEDIAN_SIZE = "1.65rem";
/** Not smaller than the median's. This is a contract, not a preference (CLAUDE.md 21). */
const RANGE_SIZE = "2.1rem";

export function SpanBar({
  low,
  high,
  median,
}: {
  low: number;
  high: number;
  median: number;
}) {
  // layout-arithmetic: positions only. Nothing computed here is displayed as a number - every
  // number on screen comes from `low`, `high` and `median` unchanged, through `pct()`. See
  // tests/client.test.ts on why this marker exists and what it permits.
  const min = Math.min(low, 0);
  const max = Math.max(high, 0);
  const width = max - min || 1;
  const place = (v: number) => `${((v - min) / width) * 100}%`;

  const spansZero = low < 0 && high > 0;

  return (
    <div className="span" data-testid="span-bar">
      <div className="span-scale" aria-hidden="true">
        <div
          className="span-fill"
          style={{ left: place(low), right: `calc(100% - ${place(high)})` }}
        />
        <div className="span-zero" style={{ left: place(0) }} />
        <div className="span-tick" style={{ left: place(median) }} />
      </div>

      <div className="span-legend">
        <span
          className="num span-range"
          data-testid="range-label"
          style={{ fontSize: RANGE_SIZE }}
        >
          {pct(low)} to {pct(high)}
        </span>
        <span className="span-median-wrap">
          <span className="label">median</span>
          <span
            className="num span-median"
            data-testid="median-label"
            style={{ fontSize: MEDIAN_SIZE }}
          >
            {pct(median)}
          </span>
        </span>
      </div>

      {spansZero ? (
        <p className="span-note" data-testid="spans-zero-note">
          The range crosses zero. These analogs do not agree on the direction.
        </p>
      ) : null}
    </div>
  );
}
