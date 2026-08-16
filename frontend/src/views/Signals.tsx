/** The sweep table. THE SCANNED COUNT IS THE HEADLINE, NOT THE PASSING ONE.
 *
 * `/api/signals` defaults to every scanned row and this view keeps that default. A `passing_only`
 * toggle exists because a reader will want it, but it starts OFF and the counts stay on screen when
 * it is on - because the scanned rows ARE the multiple-comparisons record (CLAUDE.md 18) and a
 * read-time filter leaves no trace of itself.
 *
 * 1 passing row in a table of 1 reads as a finding. 1 in 6,966 reads as the top of a distribution,
 * where chance alone predicts ~348 at α = 0.05. Those are different claims and the second one is
 * the true one, so the denominator is rendered before the table rather than under it.
 *
 * A REFUSED PAIR IS A ROW WITH A STATED STATUS, NOT AN OMISSION, and it gets the hatch: a row with
 * `status = insufficient_observations` has no statistic and no q-value, and rendering those cells
 * as blank would make them look like zeros that failed to load.
 */

import { useEffect, useState } from "react";
import { getSignals } from "../api/client";
import type { SignalList } from "../api/types";
import { count, decimal } from "../format";

export function Signals() {
  const [data, setData] = useState<SignalList | null>(null);
  const [passingOnly, setPassingOnly] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setFailed(false);
    getSignals({ passingOnly, limit: 100 }).then(
      (d) => live && setData(d),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [passingOnly]);

  if (failed) return <p className="empty">The signals endpoint did not answer.</p>;
  if (data === null) return <p className="empty">Reading the sweep…</p>;

  const run = data.run;

  return (
    <div className="view rise">
      <header className="view-head">
        <h1 className="view-title">Signals</h1>
        <p className="view-lede">
          The lead-lag sweep measures and records; it never selects. What passes a
          correction is reported beside what was scanned to find it.
        </p>
      </header>

      {run !== null ? (
        <div className="denominator" data-testid="sweep-denominator-headline">
          <p className="denominator-line">
            <strong className="num big">{count(run.passing_pairs)}</strong> of{" "}
            <strong className="num big">{count(run.scanned_pairs)}</strong> scanned
            pairs passed Benjamini-Hochberg correction.
          </p>
          <p className="footnote">
            At α = 0.05 a grid of {count(run.grid_size)} tests yields roughly 348
            significant results on pure noise, by construction, every time. Run{" "}
            {count(run.run_id)} · lags {count(run.lag_min)} to {count(run.lag_max)} ·
            commit <span className="mono">{run.git_sha.slice(0, 12)}</span>
            {run.git_dirty ? (
              <>
                {" "}
                · <strong>dirty working tree — these results are not reproducible</strong>
              </>
            ) : null}
          </p>
        </div>
      ) : (
        <p className="empty">No sweep run is on record.</p>
      )}

      <label className="toggle">
        <input
          type="checkbox"
          checked={passingOnly}
          onChange={(e) => setPassingOnly(e.target.checked)}
        />
        <span>Show only rows that passed</span>
        {passingOnly ? (
          <span className="toggle-warn" data-testid="filter-warning">
            filtered — {run !== null ? count(run.scanned_pairs) : "?"} pairs were
            scanned to produce this
          </span>
        ) : null}
      </label>

      <div className="scroll-x">
        <table className="ledger">
          <thead>
            <tr>
              <th scope="col">Feature</th>
              <th scope="col">Site</th>
              <th scope="col">Horizon</th>
              <th scope="col">Lag</th>
              <th scope="col">Regime</th>
              <th scope="col">Statistic</th>
              <th scope="col">q</th>
              <th scope="col">Consistency</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => {
              const unscanned = row.q_value === null;
              return (
                <tr
                  key={`${row.feature_name}-${row.horizon_days}-${row.lag_days}-${row.regime}-${i}`}
                  className={row.passes_gate ? "row-passing" : undefined}
                >
                  <th scope="row" className="mono">
                    {row.feature_name}
                  </th>
                  <td className="mono">{row.site_id}</td>
                  <td className="num">{count(row.horizon_days)}d</td>
                  <td className="num">
                    {row.lag_days > 0 ? "+" : ""}
                    {count(row.lag_days)}d
                  </td>
                  <td>{row.regime}</td>
                  <td className={unscanned ? "num hatch" : "num"}>
                    {row.statistic === null ? "—" : decimal(row.statistic, 3)}
                  </td>
                  <td className={unscanned ? "num hatch" : "num"}>
                    {row.q_value === null ? "—" : decimal(row.q_value, 4)}
                  </td>
                  <td className="num">
                    {row.directional_consistency === null || row.folds === null ? (
                      "—"
                    ) : (
                      <>
                        {decimal(row.directional_consistency, 2)} over{" "}
                        {count(row.folds)} folds
                      </>
                    )}
                  </td>
                  <td>
                    <span className={row.passes_gate ? "flag flag-pass" : "flag"}>
                      {row.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="footnote">
        Showing {count(data.rows.length)} of {count(data.total)} rows matching this
        filter. Directional consistency never appears without its fold count: 4 of 5
        and 40 of 50 are both 80% and are not equally informative. A q-value never
        appears without the grid it was adjusted against — {count(data.rows[0]?.grid_size ?? 0)}{" "}
        tests.
      </p>
    </div>
  );
}
