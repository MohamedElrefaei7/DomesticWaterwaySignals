/** Display formatting ONLY. Rounding and symbols. No derivation.
 *
 * Decision 9's boundary lives here: this module may round a number for display and may put a `%` or
 * a thousands separator on it. It may not compute one. There is no function in this file that takes
 * two numbers and returns a third, and that is the property to preserve when adding to it - a
 * `percentChange(a, b)` helper here would be the gate bypass wearing a formatter's clothes.
 */

const PCT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});
const WHOLE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** A signed percentage, with a real minus sign rather than a hyphen. */
export function pct(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${PCT.format(Math.abs(value))}%`;
}

export function count(value: number): string {
  return WHOLE.format(value);
}

export function decimal(value: number, places: number): string {
  return value.toFixed(places);
}

/** An ISO date, as the source stated it. No timezone arithmetic: these are calendar dates and
 *  `new Date("2022-10-11").toLocaleDateString()` renders the tenth in any zone west of UTC. */
export function isoDate(value: string): string {
  return value.slice(0, 10);
}

export function isoDateTime(value: string): string {
  return value.replace("T", " ").slice(0, 16) + " UTC";
}

/** Seconds as a coarse human duration. Used for job ages and staleness windows, never for a
 *  displayed measurement. */
export function duration(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}
