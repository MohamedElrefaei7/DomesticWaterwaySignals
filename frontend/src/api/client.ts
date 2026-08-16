/** THE ONLY MODULE IN THIS APP THAT CALLS `fetch` OR BUILDS A URL. Decision 8.
 *
 * WHY THE RULE IS ABSOLUTE RATHER THAN A CONVENTION. The API rejects rather than clamps: a series
 * request with no `start`/`end` is a 422, a span over five years is a 422, a limit over the maximum
 * is a 422 (app/api/dependencies.py). Those rejections are correct - a clamp is a lie the client
 * cannot detect - but they mean a component that assembles its own query string turns a
 * one-character mistake into a view that renders as broken rather than as wrong. Centralizing the
 * construction makes the required arguments a TYPE ERROR instead of a runtime 422.
 *
 * `seriesRange` is the reason `start` and `end` are one argument object rather than two optional
 * parameters: there is no call shape in this file that can omit one of them.
 *
 * NOTHING HERE AGGREGATES. No sum over commodities, no mean over weeks, no percent change. Decision
 * 9, and it binds this file hardest because this is where the rows arrive and where a `reduce` would
 * look most reasonable. `lock_movements.tons` is nullable AND meaningfully zero, so a sum silently
 * decides a NULL contributes nothing - the coalesce app/api/models.py refuses, performed one layer
 * up where nothing can see it.
 */

import type {
  BargeRateList,
  ConclusionResponse,
  GaugeList,
  GaugeSeries,
  HealthResponse,
  LockMovementList,
  SignalList,
  SignalRunList,
} from "./types";

/** Relative, so the built bundle works behind Caddy in Phase 10 without a rebuild, and the dev
 *  server proxies it to loopback:8000 (vite.config.ts). No host is compiled into the bundle. */
const BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly correlationId: string | null;

  constructor(
    status: number,
    code: string | null,
    correlationId: string | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
  }
}

/** Error bodies carry a code and a correlation id and nothing else (CLAUDE.md 20). This reads
 *  exactly those two fields and never surfaces a raw body: `DATABASE_URL` carries the password and
 *  it is the string psycopg puts in an OperationalError. */
async function request<T>(path: string, params?: URLSearchParams): Promise<T> {
  const url = params ? `${BASE}${path}?${params.toString()}` : `${BASE}${path}`;
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    let code: string | null = null;
    let correlationId: string | null = null;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object") {
        const record = body as Record<string, unknown>;
        code = typeof record["code"] === "string" ? record["code"] : null;
        correlationId =
          typeof record["correlation_id"] === "string"
            ? record["correlation_id"]
            : null;
      }
    } catch {
      // A body that does not parse is not an error worth reporting over the status.
    }
    throw new ApiError(
      response.status,
      code,
      correlationId,
      `${path} responded ${response.status}`,
    );
  }

  return (await response.json()) as T;
}

/** An inclusive date window. Both ends required - the API has no default and neither does this. */
export interface DateRange {
  start: string;
  end: string;
}

export interface Page {
  limit?: number;
  offset?: number;
}

function paged(params: URLSearchParams, page?: Page): URLSearchParams {
  if (page?.limit !== undefined) params.set("limit", String(page.limit));
  if (page?.offset !== undefined) params.set("offset", String(page.offset));
  return params;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function getConclusion(
  siteId: string,
  asOf: string,
): Promise<ConclusionResponse> {
  return request<ConclusionResponse>(
    "/conclusion",
    new URLSearchParams({ site_id: siteId, as_of: asOf }),
  );
}

export function getGauges(page?: Page): Promise<GaugeList> {
  return request<GaugeList>("/gauges", paged(new URLSearchParams(), page));
}

/** `range` is required and is a single object. There is no overload that omits it, which is what
 *  makes the API's "series endpoints require an explicit date range" a compile-time fact here. */
export function getGaugeSeries(
  siteId: string,
  range: DateRange,
  options?: Page & { source?: string },
): Promise<GaugeSeries> {
  const params = new URLSearchParams({ start: range.start, end: range.end });
  if (options?.source !== undefined) params.set("source", options.source);
  return request<GaugeSeries>(
    `/gauges/${encodeURIComponent(siteId)}/series`,
    paged(params, options),
  );
}

export function getRates(
  range: DateRange,
  options?: Page & { segment?: string; horizon?: string },
): Promise<BargeRateList> {
  const params = new URLSearchParams({ start: range.start, end: range.end });
  if (options?.segment !== undefined) params.set("segment", options.segment);
  if (options?.horizon !== undefined) params.set("horizon", options.horizon);
  return request<BargeRateList>("/rates", paged(params, options));
}

export function getMovements(
  range: DateRange,
  options?: Page & { lock?: string; commodity?: string },
): Promise<LockMovementList> {
  const params = new URLSearchParams({ start: range.start, end: range.end });
  if (options?.lock !== undefined) params.set("lock", options.lock);
  if (options?.commodity !== undefined)
    params.set("commodity", options.commodity);
  return request<LockMovementList>("/movements", paged(params, options));
}

/** `passingOnly` defaults to undefined, which sends no parameter, which the API reads as ALL
 *  SCANNED ROWS. That default is deliberate at both ends: the scanned rows are the
 *  multiple-comparisons record, and a client-side default of `true` would hand a reader 1 row in a
 *  table of 1 rather than 1 in 6,966, at read time, leaving no trace of itself. */
export function getSignals(options?: Page & {
  runId?: number;
  passingOnly?: boolean;
}): Promise<SignalList> {
  const params = new URLSearchParams();
  if (options?.runId !== undefined) params.set("run_id", String(options.runId));
  if (options?.passingOnly !== undefined)
    params.set("passing_only", String(options.passingOnly));
  return request<SignalList>("/signals", paged(params, options));
}

export function getSignalRuns(page?: Page): Promise<SignalRunList> {
  return request<SignalRunList>(
    "/signals/runs",
    paged(new URLSearchParams(), page),
  );
}
