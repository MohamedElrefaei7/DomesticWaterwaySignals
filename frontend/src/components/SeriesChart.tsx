/** A line chart that BREAKS AT NULLS. This is the single most likely place a fabrication reappears.
 *
 * `connectNulls` defaults to FALSE in Recharts, and this component sets it explicitly to false
 * anyway. Both facts matter and neither replaces the other:
 *
 *   - Setting it explicitly means the decision is visible at the call site rather than inherited
 *     from a library default that a major version is free to change.
 *   - `tests/chart.test.tsx` asserts the prop is literally false rather than trusting the default,
 *     because a test that passes because of a default is a test that proves nothing about this
 *     codebase and stays green if somebody sets it to true.
 *
 * WHAT CONNECTING ACROSS NULLS WOULD DO HERE. `barge_rates.pct_of_tariff` is NULL in 774 of 8,260
 * nearby records, and 661 of those are winter navigation closure on the upper Mississippi. Joining
 * across them draws a line sloping smoothly from December's rate to March's - through the closure -
 * and a reader sees a quiet winter market. The truth is that there was no market: the river was
 * shut. The interpolated version is prettier, which is exactly why it is the default everywhere and
 * why this needs a test rather than a comment.
 *
 * A window containing nulls SAYS SO, in words, beneath the chart. A broken line is only legible as
 * a gap if the reader already knows to look for one; at 62 points on a phone it reads as noise.
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { count } from "../format";
import { TruncationNote } from "./TruncationNote";

/** Explicit, exported, and asserted by the test. Never inlined into the JSX as a bare `false`,
 *  so the mutation that flips it is a one-line diff a reviewer can see. */
export const CONNECT_NULLS = false;

export interface SeriesPoint {
  /** The x label, already a calendar date string. No timezone arithmetic happens in this file. */
  label: string;
  /** null means not measured. It reaches Recharts as null and breaks the line. */
  value: number | null;
}

export function SeriesChart({
  points,
  total,
  noun,
  unit,
  colour = "var(--ink)",
}: {
  points: SeriesPoint[];
  total: number;
  noun: string;
  unit: string;
  colour?: string;
}) {
  const nullCount = points.filter((p) => p.value === null).length;

  return (
    <figure className="chart">
      <div className="chart-plot">
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={points} margin={{ top: 8, right: 8, bottom: 4, left: 4 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: "var(--ink-3)" }}
              stroke="var(--rule-strong)"
              minTickGap={28}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "var(--ink-3)" }}
              stroke="var(--rule-strong)"
              width={62}
            />
            {/* A null point has no value to format, and Recharts hands it through as null or
                undefined depending on the path. BOTH render as "not measured" rather than as an
                empty tooltip - an empty tooltip over a gap reads as a rendering bug and invites
                somebody to "fix" it by connecting the line. */}
            <Tooltip
              formatter={(v) =>
                v === null || v === undefined || typeof v !== "number"
                  ? "not measured"
                  : `${count(v)} ${unit}`
              }
            />
            <Line
              type="linear"
              dataKey="value"
              stroke={colour}
              strokeWidth={1.75}
              dot={false}
              isAnimationActive={false}
              connectNulls={CONNECT_NULLS}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <figcaption className="chart-caption">
        <TruncationNote shown={points.length} total={total} noun={noun} />

        {nullCount > 0 ? (
          <p className="legend-note" data-testid="null-legend-note">
            <span className="hatch swatch" aria-hidden="true" />
            <span>
              <strong>
                {count(nullCount)} of {count(points.length)} points in this window
                were not measured.
              </strong>{" "}
              The line breaks at each one. It is not drawn through them, and a gap
              is not a zero.
            </span>
          </p>
        ) : null}
      </figcaption>
    </figure>
  );
}
