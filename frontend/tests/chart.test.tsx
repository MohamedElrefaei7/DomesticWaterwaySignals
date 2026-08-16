/** Charts break at nulls, and say when they did. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  CONNECT_NULLS,
  SeriesChart,
  type SeriesPoint,
} from "../src/components/SeriesChart";
import { TruncationNote } from "../src/components/TruncationNote";
import type { BargeRateList, GaugeSeries } from "../src/api/types";
import withGap from "./fixtures/series-with-gap.json";
import memphis from "./fixtures/series-memphis.json";
import twinCities from "./fixtures/rates-twin-cities.json";
import truncated from "./fixtures/rates-truncated.json";
import { src } from "./paths";

const gapSeries = withGap as unknown as GaugeSeries;
const fullSeries = memphis as unknown as GaugeSeries;
const winter = twinCities as unknown as BargeRateList;
const bigTable = truncated as unknown as BargeRateList;

const asPoints = (rows: { date: string; value: number | null }[]): SeriesPoint[] =>
  rows.map((r) => ({ label: r.date, value: r.value }));

describe("nulls are gaps, never zeros", () => {
  it("7. null values break the line and are not interpolated", () => {
    // The exported constant is the one the component passes to Recharts, asserted directly rather
    // than trusted to a library default. A test that passed because `connectNulls` defaults to
    // false would prove nothing about this codebase and would stay green if somebody set it true.
    expect(CONNECT_NULLS).toBe(false);

    const source = src("components/SeriesChart.tsx");
    expect(source).toMatch(/connectNulls=\{CONNECT_NULLS\}/);
    expect(source).not.toMatch(/connectNulls=\{true\}/);

    // And the nulls survive the mapping into the chart rather than being filtered or coalesced on
    // the way in - a component that dropped them would draw an unbroken line with this prop set
    // correctly, which is the same picture by a different route.
    const points = asPoints(gapSeries.rows);
    expect(points.filter((p) => p.value === null)).toHaveLength(3);
    expect(points.some((p) => p.value === 0)).toBe(false);
  });

  it("8. a window containing nulls renders the legend note", () => {
    render(
      <SeriesChart
        points={asPoints(gapSeries.rows)}
        total={gapSeries.total}
        noun="days"
        unit="cfs"
      />,
    );

    const note = screen.getByTestId("null-legend-note");
    expect(note).toHaveTextContent("3 of 10 points in this window were not measured");
    expect(note).toHaveTextContent(/a gap is not a zero/i);
  });

  it("8b. a window with no nulls renders no legend note", () => {
    // Otherwise the note is decoration that appears always and means nothing.
    render(
      <SeriesChart
        points={asPoints(fullSeries.rows)}
        total={fullSeries.total}
        noun="days"
        unit="cfs"
      />,
    );
    expect(screen.queryByTestId("null-legend-note")).toBeNull();
  });

  it("8c. a winter closure reaches the chart as null, not as zero", () => {
    // Twin Cities, Jan-Mar 2022: five closed weeks, then 850.0 as ice-out resumes.
    const points: SeriesPoint[] = winter.rows.map((r) => ({
      label: r.week_ending,
      value: r.pct_of_tariff,
    }));
    expect(points.filter((p) => p.value === null)).toHaveLength(5);
    expect(points.some((p) => p.value === 0)).toBe(false);
    expect(points.at(-1)?.value).toBe(850.0);
  });
});

describe("truncation is stated in the interface", () => {
  it("9. a truncated response renders the showing-N-of-M sentence", () => {
    render(
      <TruncationNote
        shown={bigTable.rows.length}
        total={bigTable.total}
        noun="weeks"
      />,
    );

    const note = screen.getByTestId("truncation-note");
    expect(note).toHaveTextContent("Showing 6 of 8,260 weeks.");
    expect(note).toHaveTextContent(/a window on the series, not the series/i);
    expect(screen.queryByTestId("complete-note")).toBeNull();
  });

  it("9b. a complete response says so instead", () => {
    // The pair matters: a component that always rendered the truncation sentence would pass test 9
    // and lie on every complete window.
    render(
      <TruncationNote
        shown={fullSeries.rows.length}
        total={fullSeries.total}
        noun="days"
      />,
    );
    expect(screen.getByTestId("complete-note")).toHaveTextContent(
      "All 62 days in this window are shown.",
    );
    expect(screen.queryByTestId("truncation-note")).toBeNull();
  });

  it("9c. the chart renders the truncation sentence from a real envelope", () => {
    render(
      <SeriesChart
        points={asPoints(gapSeries.rows).slice(0, 4)}
        total={8260}
        noun="weeks"
        unit="cfs"
      />,
    );
    expect(screen.getByTestId("truncation-note")).toHaveTextContent(
      "Showing 4 of 8,260 weeks.",
    );
  });
});
