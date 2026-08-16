/** A COVERAGE SCHEMATIC, NOT A MAP, AND THE DIFFERENCE IS NOT A COMPROMISE.
 *
 * The brief asked for gauges coloured by percentile against each site's own seasonal climatology,
 * on a geographic map, with locks sized by throughput. NONE OF THOSE THREE THINGS IS AVAILABLE, and
 * the API is fixed for this commit:
 *
 *   NO PERCENTILE, NO CLIMATOLOGY.  No endpoint serves an anomaly or a baseline year count.
 *                                   `app/api/routes/__init__.py` says the API computes no
 *                                   climatology by design. Computing one here would be a derived
 *                                   statistic in a component (decision 9) - and an anomaly computed
 *                                   client-side is precisely the number Phase 5's minimum-years
 *                                   guard exists to refuse.
 *   NO COORDINATES.                  `Gauge` carries no latitude or longitude. CONTEXT.md records
 *                                   that river mile and coordinates are deliberately NULL rather
 *                                   than estimated, with a test that goes red when they land.
 *                                   Hardcoding published USGS coordinates here would invent seed
 *                                   data CLAUDE.md 1 reserves for the human, in a second place,
 *                                   where the database could not correct it.
 *   NO THROUGHPUT.                   `/api/movements` returns rows per commodity with a nullable
 *                                   `tons`. Sizing a lock by throughput means summing them, which
 *                                   decides a NULL contributes zero - the coalesce
 *                                   `app/api/models.py` refuses, performed one layer up.
 *
 * So this renders what is actually known: each gauge's DECLARED record start beside its OBSERVED
 * coverage, which is CLAUDE.md 15's envelope-versus-served comparison and the one measurement the
 * Phase 8 procedure asked for and did not get. Every gauge is in the no-baseline state, and the
 * legend says why rather than letting a colour ramp imply a shared baseline across four sites whose
 * records are 0, 4,335, 6,801 and 13,375 days deep.
 */

import { useEffect, useState } from "react";
import { getGauges } from "../api/client";
import type { Gauge } from "../api/types";
import { count, isoDate } from "../format";

/** Baseline depth in years, per site. The API serves none, so every entry is null and the legend
 *  renders the no-baseline state for all four. The prop exists so that the day an endpoint does
 *  serve a climatology, the legend states each site's own year count rather than one number for
 *  the map - a shared baseline across these four records would be a quiet lie. */
export type Baselines = Record<string, number | null>;

export function RiverMap({ baselines = {} }: { baselines?: Baselines }) {
  const [gauges, setGauges] = useState<Gauge[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getGauges().then((g) => setGauges(g.rows), () => setFailed(true));
  }, []);

  if (failed) return <p className="empty">The gauge catalog did not answer.</p>;
  if (gauges === null) return <p className="empty">Reading the catalog…</p>;

  return (
    <div className="view rise">
      <header className="view-head">
        <h1 className="view-title">River</h1>
        <p className="view-lede">
          Four seeded gauges, ordered by USGS site number — which runs downstream on
          this reach, but is a numbering convention and not a measured river mile.
          No coordinates are seeded, so this is a schematic and not a map.
        </p>
      </header>

      <ol className="reach">
        {gauges.map((gauge) => {
          const years = baselines[gauge.site_id] ?? null;
          const served = gauge.observed_days > 0;
          return (
            <li key={gauge.site_id} className="station">
              <div
                className={years === null ? "station-mark hatch" : "station-mark"}
                data-testid={`baseline-mark-${gauge.site_id}`}
                data-baseline={years === null ? "none" : "served"}
                aria-hidden="true"
              />
              <div className="station-body">
                <h2 className="station-name">{gauge.name}</h2>
                <p className="station-meta mono">
                  {gauge.site_id} · {gauge.river} · tier {count(gauge.tier)} ·{" "}
                  {gauge.available_params.join(", ")}
                </p>

                <dl className="coverage">
                  <div>
                    <dt className="label">Declared daily start</dt>
                    <dd>
                      {gauge.declared_dv_record_start === null
                        ? "none seeded"
                        : isoDate(gauge.declared_dv_record_start)}
                    </dd>
                  </div>
                  <div>
                    <dt className="label">Observed coverage</dt>
                    <dd>
                      {served ? (
                        <>
                          {isoDate(gauge.observed_start ?? "")} →{" "}
                          {isoDate(gauge.observed_end ?? "")}
                        </>
                      ) : (
                        <span className="never">nothing served</span>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="label">Days on record</dt>
                    <dd className="num">{count(gauge.observed_days)}</dd>
                  </div>
                  <div>
                    <dt className="label">Baseline for a percentile</dt>
                    <dd data-testid={`baseline-label-${gauge.site_id}`}>
                      {years === null ? (
                        <span className="never">not served</span>
                      ) : (
                        <>{count(years)}-year seasonal median</>
                      )}
                    </dd>
                  </div>
                </dl>
              </div>
            </li>
          );
        })}
      </ol>

      <aside className="legend" data-testid="map-legend">
        <h2 className="panel-title">Legend</h2>
        <p className="legend-row">
          <span className="hatch swatch" aria-hidden="true" />
          <span>
            <strong>No baseline served.</strong> This API computes no climatology, so
            no gauge on this page is coloured by percentile. A hatch is not a
            mid-scale colour: it says the comparison was not made, rather than that
            it came out average.
          </span>
        </p>
        <ul className="legend-list">
          {gauges.map((gauge) => {
            const years = baselines[gauge.site_id] ?? null;
            return (
              <li key={gauge.site_id} data-testid={`legend-${gauge.site_id}`}>
                <span className="mono">{gauge.site_id}</span>{" "}
                {years === null ? (
                  <>— no baseline period</>
                ) : (
                  <>
                    — baseline: <strong>{count(years)} years</strong>
                  </>
                )}
              </li>
            );
          })}
        </ul>
        <p className="footnote">
          The year count is stated per gauge and never once for the page. These
          records are 0, 4,335, 6,801 and 13,375 days deep; one colour scale over
          four different baselines would imply a comparison nobody made.
        </p>
      </aside>
    </div>
  );
}
