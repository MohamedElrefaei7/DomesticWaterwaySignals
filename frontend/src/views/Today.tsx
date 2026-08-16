/** The main view. One question: is the river binding, and does history say anything about it?
 *
 * THE CONCLUSION SWITCHES ON `gate` AND EACH SHAPE HAS ITS OWN COMPONENT. There is no shared
 * component here with conditional fields, and the switch is exhaustive rather than a chain of
 * truthy checks. `{result.median_pct != null && <Median/>}` is the tempting version - it is shorter,
 * it looks DRY, and it puts a numeric render one truthy-check away from appearing on a refusal.
 * Under `strict` the exhaustive switch also means adding a fourth gate value to the API fails the
 * typecheck here rather than silently rendering nothing.
 */

import { useEffect, useState } from "react";
import { getConclusion, getGaugeSeries, getHealth } from "../api/client";
import type {
  ConclusionResponse,
  GaugeSeries,
  HealthResponse,
} from "../api/types";
import { ConclusionNoEvent } from "../components/ConclusionNoEvent";
import { ConclusionPassed } from "../components/ConclusionPassed";
import { ConclusionRefused } from "../components/ConclusionRefused";
import { DegradedBanner } from "../components/DegradedBanner";
import { SeriesChart } from "../components/SeriesChart";
import { count, isoDate } from "../format";

/** Memphis. The only site with a passing gate on record, and the site both labelled events belong
 *  to. Not a default the user cannot change - see the site control below. */
const DEFAULT_SITE = "07032000";
const DEFAULT_AS_OF = "2022-10-11";
const SITES = [
  { id: "07010000", label: "St. Louis" },
  { id: "07032000", label: "Memphis" },
  { id: "07289000", label: "Vicksburg" },
  { id: "07374000", label: "Baton Rouge" },
];

/** The window drawn beneath the conclusion: the six weeks either side of the date asked about. */
function windowAround(asOf: string): { start: string; end: string } {
  // layout-arithmetic: a fixed calendar window around the query date. Nothing derived from a
  // response, and no number produced here is displayed as a measurement.
  const day = 86400000;
  const centre = Date.parse(`${asOf}T00:00:00Z`);
  return {
    start: new Date(centre - 40 * day).toISOString().slice(0, 10),
    end: new Date(centre + 21 * day).toISOString().slice(0, 10),
  };
}

export function Conclusion({ result }: { result: ConclusionResponse }) {
  switch (result.gate) {
    case "passed":
      return <ConclusionPassed result={result} />;
    case "refused":
      return <ConclusionRefused result={result} />;
    case "no_current_event":
      return <ConclusionNoEvent result={result} />;
  }
}

export function Today() {
  const [site, setSite] = useState(DEFAULT_SITE);
  const [asOf, setAsOf] = useState(DEFAULT_AS_OF);
  const [result, setResult] = useState<ConclusionResponse | null>(null);
  const [series, setSeries] = useState<GaugeSeries | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth, () => setHealth(null));
  }, []);

  useEffect(() => {
    let live = true;
    setError(null);
    setResult(null);
    setSeries(null);

    getConclusion(site, asOf).then(
      (r) => live && setResult(r),
      () => live && setError("The conclusion endpoint did not answer."),
    );
    getGaugeSeries(site, windowAround(asOf)).then(
      (s) => live && setSeries(s),
      () => live && setSeries(null),
    );

    return () => {
      live = false;
    };
  }, [site, asOf]);

  return (
    <div className="view rise">
      <DegradedBanner health={health} />

      <header className="view-head">
        <h1 className="view-title">Today</h1>
        <p className="view-lede">
          Is the river binding at this gauge, and does the record say anything about
          what happened the last few times it looked like this?
        </p>
      </header>

      <form className="controls" onSubmit={(e) => e.preventDefault()}>
        <label>
          <span className="label">Gauge</span>
          <select value={site} onChange={(e) => setSite(e.target.value)}>
            {SITES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">As of</span>
          <input
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
          />
        </label>
      </form>

      {error !== null ? <p className="empty">{error}</p> : null}
      {result === null && error === null ? (
        <p className="empty">Asking the engine…</p>
      ) : null}
      {result !== null ? <Conclusion result={result} /> : null}

      {series !== null ? (
        <section className="panel">
          <h2 className="panel-title">
            Discharge{" "}
            <span className="panel-sub">
              {isoDate(series.start)} to {isoDate(series.end)} · {count(series.total)}{" "}
              days on record in this window
            </span>
          </h2>
          <SeriesChart
            points={series.rows.map((r) => ({ label: r.date, value: r.value }))}
            total={series.total}
            noun="days"
            unit="cfs"
          />
        </section>
      ) : null}
    </div>
  );
}
