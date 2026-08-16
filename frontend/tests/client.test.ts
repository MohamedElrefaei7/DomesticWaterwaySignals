/** One client, no component-built URLs, and no arithmetic on a response value. */

import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  getGaugeSeries,
  getRates,
  getSignals,
} from "../src/api/client";
import { code, src, sourcesIn } from "./paths";

const COMPONENT_SOURCES = [...sourcesIn("components"), ...sourcesIn("views")];

describe("all data fetching goes through one typed client", () => {
  it("15. no component calls fetch directly", () => {
    // The guard is only meaningful if it is looking at something. A directory that silently
    // returned nothing would make this pass over an empty set - CLAUDE.md 2's theme 2, and the
    // exact shape of the ingress test that passed because the set it constrained was empty.
    expect(COMPONENT_SOURCES.length).toBeGreaterThanOrEqual(10);

    for (const { name, text } of COMPONENT_SOURCES) {
      const body = code(text);
      expect(body, `${name} calls fetch`).not.toMatch(/\bfetch\s*\(/);
      expect(body, `${name} builds a query string`).not.toMatch(
        /\bURLSearchParams\b/,
      );
      expect(body, `${name} hardcodes an API path`).not.toMatch(/["'`]\/api\//);
      expect(body, `${name} uses XMLHttpRequest`).not.toMatch(
        /\bXMLHttpRequest\b/,
      );
    }

    // And exactly one module in the app does call it.
    const client = code(src("api/client.ts"));
    expect(client).toMatch(/\bfetch\s*\(/);
  });

  it("16. the client always sends start and end on series requests", async () => {
    const seen: string[] = [];
    const stub = vi.fn(async (url: string) => {
      seen.push(url);
      return {
        ok: true,
        json: async () => ({ rows: [], limit: 500, offset: 0, total: 0 }),
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", stub);

    await getGaugeSeries("07032000", { start: "2022-09-01", end: "2022-11-01" });
    await getRates({ start: "2022-01-01", end: "2022-03-31" }, {
      segment: "Twin Cities",
      horizon: "nearby",
    });

    expect(seen).toHaveLength(2);
    for (const url of seen) {
      const query = new URLSearchParams(url.split("?")[1] ?? "");
      expect(query.get("start")).toBeTruthy();
      expect(query.get("end")).toBeTruthy();
    }

    expect(seen[0]).toContain("/api/gauges/07032000/series");
    const rates = new URLSearchParams(seen[1]?.split("?")[1] ?? "");
    expect(rates.get("segment")).toBe("Twin Cities");
    expect(rates.get("horizon")).toBe("nearby");

    // There is no call shape that can omit the window: `range` is one required object, not two
    // optional parameters. Asserted structurally because a runtime test cannot reach the case.
    const client = code(src("api/client.ts"));
    expect(client).toMatch(/range:\s*DateRange/);
    expect(client).not.toMatch(/range\?:\s*DateRange/);
  });

  it("16b. /api/signals defaults to every scanned row", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        seen.push(url);
        return {
          ok: true,
          json: async () => ({ rows: [], limit: 50, offset: 0, total: 0 }),
        } as unknown as Response;
      }),
    );

    await getSignals();

    // No `passing_only` parameter at all, which the API reads as ALL SCANNED ROWS. A client-side
    // default of true would hand a reader 1 row in a table of 1 rather than 1 in 6,966.
    expect(seen[0]).not.toContain("passing_only");
  });

  it("16c. an error body never reaches the caller as text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        json: async () => ({
          code: "span_too_long",
          correlation_id: "b17f",
          detail: "postgresql://waterway:hunter2@db:5432/waterway",
        }),
      })) as unknown as typeof fetch,
    );

    await expect(
      getRates({ start: "2000-01-01", end: "2026-01-01" }),
    ).rejects.toBeInstanceOf(ApiError);

    try {
      await getRates({ start: "2000-01-01", end: "2026-01-01" });
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(422);
      expect(api.code).toBe("span_too_long");
      expect(api.correlationId).toBe("b17f");
      // DATABASE_URL carries the password and it is what psycopg puts in an OperationalError.
      expect(api.message).not.toContain("hunter2");
      expect(api.message).not.toContain("postgresql://");
    }
  });
});

describe("the frontend computes no derived statistic", () => {
  /** Response fields whose values are measurements. Arithmetic on any of these produces a number
   *  the API did not return, which is the gate bypass decision 9 exists to prevent. */
  const MEASURES = [
    "median_pct",
    "range_pct",
    "pct_of_tariff",
    "tons",
    "statistic",
    "q_value",
    "p_value",
    "n_effective",
    "n_observations",
    "distance",
    "analogs",
    "consistent",
    "observed_days",
    "scanned_pairs",
    "passing_pairs",
    "directional_consistency",
    "median_pct",
  ];

  it("17. no component performs arithmetic on a response value", () => {
    expect(COMPONENT_SOURCES.length).toBeGreaterThanOrEqual(10);

    for (const { name, text } of COMPONENT_SOURCES) {
      const body = code(text);

      for (const field of MEASURES) {
        // `x.median_pct * 2`, `median_pct + other`, `100 - result.analogs`.
        const after = new RegExp(`\\b${field}\\b\\s*[-+*/%]\\s*[\\w(]`);
        const before = new RegExp(`[\\w)]\\s*[-+*/%]\\s*\\b${field}\\b`);
        expect(body, `${name} does arithmetic on ${field}`).not.toMatch(after);
        expect(body, `${name} does arithmetic on ${field}`).not.toMatch(before);
      }

      // The aggregation vocabulary, which is how a derived statistic usually arrives.
      expect(body, `${name} reduces a response array`).not.toMatch(/\.reduce\s*\(/);
      expect(body, `${name} scales by a hundred`).not.toMatch(/[*/]\s*100\b/);
    }
  });

  it("17b. arithmetic that IS present is declared as layout and produces no rendered number", () => {
    // Two components position things: SpanBar places a tick, Today picks a calendar window.
    // Both carry an explicit marker, so an unmarked arithmetic expression added later is visible
    // in review rather than indistinguishable from these.
    const marked = COMPONENT_SOURCES.filter(({ text }) =>
      text.includes("layout-arithmetic:"),
    ).map(({ name }) => name);

    expect(marked.sort()).toEqual(["components/SpanBar.tsx", "views/Today.tsx"]);

    // SpanBar's displayed numbers come from `pct()` applied to unmodified props. If a computed
    // value were rendered, it would have to pass through an expression rather than a bare name.
    const spanBar = code(src("components/SpanBar.tsx"));
    expect(spanBar).toMatch(/\{pct\(low\)\}/);
    expect(spanBar).toMatch(/\{pct\(high\)\}/);
    expect(spanBar).toMatch(/\{pct\(median\)\}/);
  });

  it("17c. the formatting module derives nothing", () => {
    const format = code(src("format.ts"));
    // A `percentChange(a, b)` here would be the gate bypass wearing a formatter's clothes.
    for (const banned of [
      "percentChange",
      "median",
      "mean",
      "average",
      "sum",
      "ratio",
      "delta",
      "anomaly",
    ]) {
      expect(format, `format.ts exports ${banned}`).not.toMatch(
        new RegExp(`function\\s+${banned}\\b`, "i"),
      );
    }
  });
});
