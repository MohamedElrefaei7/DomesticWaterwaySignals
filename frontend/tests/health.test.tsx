/** Two clocks, two columns, and the Phase 8 finding must survive the render. */

import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Health } from "../src/views/Health";
import { DegradedBanner } from "../src/components/DegradedBanner";
import type { HealthResponse } from "../src/api/types";
import healthFixture from "./fixtures/health.json";

const health = healthFixture as unknown as HealthResponse;

vi.mock("../src/api/client", () => ({
  getHealth: vi.fn(),
}));

import { getHealth } from "../src/api/client";

beforeEach(() => {
  vi.mocked(getHealth).mockResolvedValue(health);
});

const renderHealth = () =>
  render(
    <MemoryRouter>
      <Health />
    </MemoryRouter>,
  );

describe("job liveness and data freshness are different questions", () => {
  it("10. job overdue and data stale render as distinct labelled columns", async () => {
    renderHealth();

    const jobColumn = await screen.findByTestId("job-overdue-column");
    const dataColumn = screen.getByTestId("data-stale-column");

    expect(jobColumn).toHaveTextContent("Job overdue");
    expect(dataColumn).toHaveTextContent("Data stale");

    // Two different tables, so neither verdict can be read off the other's row.
    expect(jobColumn.closest("table")).not.toBe(dataColumn.closest("table"));

    // And they are asking different questions, in words, on the page.
    expect(screen.getByText(/has this been run recently\?/i)).toBeInTheDocument();
    expect(
      screen.getByText(/is what is stored still fresh\?/i),
    ).toBeInTheDocument();
  });

  it("11. a job overdue with fresh data does not render as a data problem", async () => {
    renderHealth();

    // The measured case, 2026-08-16: usda_rates_ingest overdue, barge_rates NOT stale.
    const jobFlag = await screen.findByTestId("job-overdue-usda_rates_ingest");
    const dataFlag = screen.getByTestId("data-stale-barge_rates");

    expect(jobFlag).toHaveTextContent("overdue");
    expect(dataFlag).toHaveTextContent("fresh");
    expect(dataFlag).not.toHaveTextContent("stale");

    // The table's row must not inherit the job's alarm styling.
    expect(dataFlag.className).not.toMatch(/flag-on/);
    expect(jobFlag.className).toMatch(/flag-on/);

    // The same holds for lock_movements, so this cannot pass on one lucky row.
    expect(screen.getByTestId("data-stale-lock_movements")).toHaveTextContent(
      "fresh",
    );

    // And the disagreement is explained rather than left looking like a bug.
    const note = screen.getByTestId("overdue-not-stale-note");
    expect(note).toHaveTextContent(/correct, not contradictory/i);
  });

  it("11b. no successful run on record renders as never, not as a stale date", async () => {
    renderHealth();
    // `last_success: null` is the most alarming state in the table, not a quiet one, and it is
    // the mechanism behind the whole finding above.
    const nevers = await screen.findAllByTestId("never-succeeded");
    expect(nevers).toHaveLength(2);
  });

  it("11c. a stale table still renders as stale", async () => {
    // Otherwise test 11 could pass over a view that rendered every table as fresh.
    renderHealth();
    expect(await screen.findByTestId("data-stale-gauge_readings")).toHaveTextContent(
      "stale",
    );
  });
});

describe("degraded is visible without clicking", () => {
  it("12. degraded true renders the banner on the main view", () => {
    render(
      <MemoryRouter>
        <DegradedBanner health={health} />
      </MemoryRouter>,
    );

    const banner = screen.getByTestId("degraded-banner");
    expect(banner).toHaveTextContent("6 of 6 jobs overdue");
    expect(banner).toHaveTextContent("3 of 5 tables stale");
    // It names what is behind rather than only that something is.
    expect(banner).toHaveTextContent(/2 jobs have no successful run on record/i);
    expect(within(banner).getByRole("link")).toHaveAttribute("href", "/health");
  });

  it("12b. a healthy system renders no banner at all", async () => {
    // A banner that is always present is furniture, and furniture is not a warning.
    const healthy: HealthResponse = {
      ...health,
      degraded: false,
      jobs: health.jobs.map((j) => ({ ...j, overdue: false })),
      data: health.data.map((t) => ({ ...t, stale: false })),
    };
    render(
      <MemoryRouter>
        <DegradedBanner health={healthy} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("degraded-banner")).toBeNull();

    // And with no health response at all, nothing is claimed in either direction.
    render(
      <MemoryRouter>
        <DegradedBanner health={null} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("degraded-banner")).toBeNull();
  });

  it("12c. the health view survives an endpoint that does not answer", async () => {
    vi.mocked(getHealth).mockRejectedValue(new Error("no route to host"));
    renderHealth();
    await waitFor(() =>
      expect(
        screen.getByText(/not the same as the system being healthy/i),
      ).toBeInTheDocument(),
    );
  });
});
