/** The colour scale must not imply a baseline nobody computed.
 *
 * TESTS 13 AND 14 ARE ADAPTED FROM THE BRIEF, AND THE REASON IS RECORDED IN CONTEXT.md. The brief
 * asked that the legend state each gauge's climatology year count. NO ENDPOINT SERVES ONE - the
 * API computes no climatology by design - so a test asserting a rendered year count could only pass
 * against a number the frontend invented, which is decision 9's failure wearing a test's clothes.
 *
 * What these two assert instead is the PROPERTY those tests exist to protect: the baseline is
 * stated per gauge and never once for the page, and a gauge without one is rendered as unmeasured
 * rather than as mid-scale. Both are asserted with a mixture of served and unserved baselines, so
 * the guard is not vacuous the day an endpoint starts serving them.
 */

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RiverMap } from "../src/views/RiverMap";
import type { GaugeList } from "../src/api/types";
import gaugesFixture from "./fixtures/gauges.json";

const gauges = gaugesFixture as unknown as GaugeList;

vi.mock("../src/api/client", () => ({ getGauges: vi.fn() }));
import { getGauges } from "../src/api/client";

beforeEach(() => {
  vi.mocked(getGauges).mockResolvedValue(gauges);
});

describe("the legend states its own baseline", () => {
  it("13. the legend states the baseline period per gauge, never once for the page", async () => {
    // A mixture: two sites with a served baseline of DIFFERENT depths, two with none. The point of
    // the test is that 11 and 37 are reported separately rather than averaged into one scale.
    render(
      <RiverMap
        baselines={{ "07010000": 37, "07032000": 11, "07289000": null }}
      />,
    );

    const legend = await screen.findByTestId("map-legend");

    expect(
      within(legend).getByTestId("legend-07010000"),
    ).toHaveTextContent("37 years");
    expect(
      within(legend).getByTestId("legend-07032000"),
    ).toHaveTextContent("11 years");
    expect(
      within(legend).getByTestId("legend-07289000"),
    ).toHaveTextContent(/no baseline period/i);
    // Not passed at all is the same claim as null: unserved.
    expect(
      within(legend).getByTestId("legend-07374000"),
    ).toHaveTextContent(/no baseline period/i);

    // Per gauge, in the body too.
    expect(screen.getByTestId("baseline-label-07010000")).toHaveTextContent(
      "37-year seasonal median",
    );
    expect(screen.getByTestId("baseline-label-07032000")).toHaveTextContent(
      "11-year seasonal median",
    );
  });

  it("13b. with nothing served, the legend says so rather than falling silent", async () => {
    // The state the live API actually produces today.
    render(<RiverMap />);
    const legend = await screen.findByTestId("map-legend");
    expect(legend).toHaveTextContent(/no baseline served/i);
    expect(legend).toHaveTextContent(/computes no climatology/i);

    for (const site of gauges.rows) {
      expect(screen.getByTestId(`legend-${site.site_id}`)).toHaveTextContent(
        /no baseline period/i,
      );
    }
  });
});

describe("an unmeasured baseline is not a mid-scale colour", () => {
  it("14. a null anomaly renders in the no-baseline treatment, not mid-scale", async () => {
    render(<RiverMap baselines={{ "07010000": 37 }} />);

    const unserved = await screen.findByTestId("baseline-mark-07289000");
    const served = screen.getByTestId("baseline-mark-07010000");

    // The hatch is this app's one vocabulary for "not measured". It is textural, so it cannot be
    // read as a point on a ramp between two colours.
    expect(unserved.className).toMatch(/\bhatch\b/);
    expect(unserved).toHaveAttribute("data-baseline", "none");

    // And a served baseline does NOT get it, so the treatment discriminates.
    expect(served.className).not.toMatch(/\bhatch\b/);
    expect(served).toHaveAttribute("data-baseline", "served");
  });

  it("14b. the schematic does not claim to be a map", async () => {
    render(<RiverMap />);
    // No coordinates are seeded, and inventing them would put seed data in a second place.
    expect(
      await screen.findByText(/schematic and not a map/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/not a measured river mile/i)).toBeInTheDocument();
  });

  it("14c. a gauge serving nothing says so rather than showing an empty range", async () => {
    render(<RiverMap />);
    // Baton Rouge: observed_days 0, both bounds null.
    const nothing = await screen.findAllByText(/nothing served/i);
    expect(nothing.length).toBeGreaterThan(0);
  });
});
