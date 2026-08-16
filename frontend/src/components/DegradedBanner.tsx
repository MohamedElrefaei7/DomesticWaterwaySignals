/** `degraded: true`, visible without clicking, on the main view and not only on /health.
 *
 * The prior project recorded "Completed" while the whole stack had been down for two and a half
 * months (CLAUDE.md 2). The API's answer to that is a health endpoint that is per-job and per-table
 * and returns 200 with a `degraded` field rather than a bare `{"status": "ok"}`. A frontend that
 * receives `degraded: true` and renders the conclusion anyway, unmarked, restores the exact failure
 * one layer up - the reader sees a confident answer computed from data nobody has refreshed.
 *
 * IT SAYS WHAT IS WRONG, NOT THAT SOMETHING IS. "Degraded" alone sends a reader to the logs. Naming
 * the overdue jobs and the stale tables separately is what lets them decide whether the number they
 * are looking at is affected - a stale `barge_rates` matters to a rate chart and an overdue
 * heartbeat does not.
 */

import { Link } from "react-router-dom";
import type { HealthResponse } from "../api/types";
import { count } from "../format";

export function DegradedBanner({ health }: { health: HealthResponse | null }) {
  if (health === null || !health.degraded) return null;

  const overdue = health.jobs.filter((j) => j.overdue);
  const stale = health.data.filter((t) => t.stale);
  const neverRan = overdue.filter((j) => j.last_success === null);

  return (
    <div className="degraded" role="status" data-testid="degraded-banner">
      <span className="label degraded-label">Degraded</span>
      <p className="degraded-body">
        <strong>
          {count(overdue.length)} of {count(health.jobs.length)} jobs overdue
        </strong>
        {stale.length > 0 ? (
          <>
            {" · "}
            <strong>
              {count(stale.length)} of {count(health.data.length)} tables stale
            </strong>
          </>
        ) : (
          <> · no table is stale</>
        )}
        {neverRan.length > 0 ? (
          <>
            {" · "}
            {count(neverRan.length)} job
            {neverRan.length === 1 ? " has" : "s have"} no successful run on record
          </>
        ) : null}
        . <Link to="/health">See what is behind</Link>.
      </p>
    </div>
  );
}
