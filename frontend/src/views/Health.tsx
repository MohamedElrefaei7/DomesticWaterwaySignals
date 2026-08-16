/** Two tables, two questions, never one status light.
 *
 * THE PHASE 8 FINDING IS THE REASON THIS VIEW IS SHAPED LIKE THIS. On 2026-08-16 the instance
 * reported every job overdue while `barge_rates` and `lock_movements` reported `stale: false`. That
 * is not an inconsistency, and collapsing the two into a single "status" column would either report
 * a false problem (the tables are fine) or hide a real one (the jobs have never run):
 *
 *   JOB OVERDUE   asks "has this been RUN recently", from `job_runs`, against that job's own
 *                 `overdue_after` in the cadence table.
 *   DATA STALE    asks "is what is ALREADY STORED still inside its freshness window", from MAX(ts)
 *                 on the table itself, against that entry's own `max_staleness`.
 *
 * Two clocks, two sources, two questions (CLAUDE.md 4, 21). The measured mechanism was
 * `last_success: null` on both USDA jobs - a job with no successful run on record is overdue
 * regardless of any threshold - while the rows a backfill CLI landed stayed well inside the ten-day
 * window. So this view renders `last_success: null` as its own state rather than as a very old
 * date, because "never" and "long ago" are different news.
 */

import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import type { HealthResponse } from "../api/types";
import { count, duration, isoDateTime } from "../format";

export function Health() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getHealth().then(setHealth, () => setFailed(true));
  }, []);

  if (failed) {
    return (
      <p className="empty">
        The health endpoint did not answer. That is itself a fact about the system,
        and it is not the same as the system being healthy.
      </p>
    );
  }
  if (health === null) return <p className="empty">Reading the registry…</p>;

  const staleTables = new Set(
    health.data.filter((t) => t.stale).map((t) => t.job_name),
  );
  const overdueWithFreshData = health.jobs.filter(
    (j) =>
      j.overdue &&
      health.data.some((t) => t.job_name === j.job_name) &&
      !staleTables.has(j.job_name),
  );

  return (
    <div className="view rise">
      <header className="view-head">
        <h1 className="view-title">Health</h1>
        <p className="view-lede">
          Checked {isoDateTime(health.checked_at)}.{" "}
          {health.degraded
            ? "Reporting degraded, with HTTP 200 — an uptime monitor that goes red on a stale ingest job cannot be told apart from one that goes red because the API is down."
            : "Nothing overdue, nothing stale."}
        </p>
      </header>

      {overdueWithFreshData.length > 0 ? (
        <aside className="finding" data-testid="overdue-not-stale-note">
          <p className="finding-head display">
            {count(overdueWithFreshData.length)} job
            {overdueWithFreshData.length === 1 ? " is" : "s are"} overdue while the
            data {overdueWithFreshData.length === 1 ? "it writes is" : "they write is"}{" "}
            still fresh
          </p>
          <p>
            This is correct, not contradictory. <strong>Job overdue</strong> asks
            whether something has run recently. <strong>Data stale</strong> asks
            whether what is already stored is still inside its freshness window.
            A table filled by a backfill can sit well inside that window while the
            scheduled job that would normally fill it has never recorded a success.
          </p>
        </aside>
      ) : null}

      <section className="panel">
        <h2 className="panel-title">
          Jobs <span className="panel-sub">has this been run recently?</span>
        </h2>
        <div className="scroll-x">
          <table className="ledger">
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Last success</th>
                <th scope="col">Age</th>
                <th scope="col">Overdue after</th>
                <th scope="col" data-testid="job-overdue-column">
                  Job overdue
                </th>
              </tr>
            </thead>
            <tbody>
              {health.jobs.map((job) => (
                <tr key={job.job_name}>
                  <th scope="row" className="mono">
                    {job.job_name}
                  </th>
                  <td>
                    {job.last_success === null ? (
                      <span className="never" data-testid="never-succeeded">
                        never
                      </span>
                    ) : (
                      isoDateTime(job.last_success)
                    )}
                  </td>
                  <td className="num">
                    {job.age_seconds === null ? "—" : duration(job.age_seconds)}
                  </td>
                  <td className="num">{duration(job.overdue_after_seconds)}</td>
                  <td>
                    <span
                      className={job.overdue ? "flag flag-on" : "flag"}
                      data-testid={`job-overdue-${job.job_name}`}
                    >
                      {job.overdue ? "overdue" : "ok"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          “Last success” is the most recent <em>success</em> row, never the most
          recent row of any status. A job failing nightly has recent activity and no
          recent success. <strong>never</strong> means no successful run is on
          record at all, which is the most alarming state in this table rather than
          a quiet one.
        </p>
      </section>

      <section className="panel">
        <h2 className="panel-title">
          Data <span className="panel-sub">is what is stored still fresh?</span>
        </h2>
        <div className="scroll-x">
          <table className="ledger">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Written by</th>
                <th scope="col">Newest row</th>
                <th scope="col">Age</th>
                <th scope="col">Freshness window</th>
                <th scope="col" data-testid="data-stale-column">
                  Data stale
                </th>
              </tr>
            </thead>
            <tbody>
              {health.data.map((table) => (
                <tr key={table.table}>
                  <th scope="row" className="mono">
                    {table.table}
                  </th>
                  <td className="mono subtle">{table.job_name}</td>
                  <td>
                    {table.newest === null ? (
                      <span className="never">no rows</span>
                    ) : (
                      isoDateTime(table.newest)
                    )}
                  </td>
                  <td className="num">
                    {table.age_seconds === null ? "—" : duration(table.age_seconds)}
                  </td>
                  <td className="num">{duration(table.max_staleness_seconds)}</td>
                  <td>
                    <span
                      className={table.stale ? "flag flag-on" : "flag"}
                      data-testid={`data-stale-${table.table}`}
                    >
                      {table.error !== null
                        ? "failed"
                        : table.stale
                          ? "stale"
                          : "fresh"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          Measured from the data — MAX of the source’s own timestamp column — never
          from the process. A source that accepts a connection and delivers nothing
          looks healthy at every layer except this one. A registered table that
          cannot be queried is a <strong>failed</strong> check, never a skipped one.
        </p>
      </section>
    </div>
  );
}
