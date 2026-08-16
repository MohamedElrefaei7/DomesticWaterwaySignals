/** "Showing 500 of 8,260." A SENTENCE THE READER SEES, not a console warning.
 *
 * A client that receives 500 of 8,260 rows and does not say so draws a chart of a truncated series,
 * AND IT LOOKS LIKE A REAL SERIES - smooth, plausible, ending on a date that is not the end of
 * anything. Nobody downstream can check it, because there is nothing wrong with the picture.
 *
 * The API puts `total` on every list envelope specifically so this is renderable. Receiving that
 * field and not rendering it is the same omission one layer up.
 *
 * A disabled "load more" button is not this. Neither is a scrollbar. The reader has to be told, in
 * words, that what they are looking at is a window.
 */

import { count } from "../format";

export function TruncationNote({
  shown,
  total,
  noun,
}: {
  shown: number;
  total: number;
  noun: string;
}) {
  if (shown >= total) {
    return (
      <p className="complete-note" data-testid="complete-note">
        All {count(total)} {noun} in this window are shown.
      </p>
    );
  }

  return (
    <p className="truncation" data-testid="truncation-note" role="status">
      <strong>
        Showing {count(shown)} of {count(total)} {noun}.
      </strong>{" "}
      This chart is a window on the series, not the series. Narrow the date range
      to see a complete one.
    </p>
  );
}
