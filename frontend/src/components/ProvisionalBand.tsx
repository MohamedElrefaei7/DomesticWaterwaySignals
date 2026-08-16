/** NOT CLEARED FOR QUOTATION. This band exists because CONTEXT.md says so.
 *
 * `CONTEXT.md`, in the block headed "THREE QUESTIONS COME BEFORE PHASE 9, AND THEY ARE ALL HUMAN
 * DECISIONS":
 *
 *   > DO NOT put either sentence in a README, a UI or a résumé until the three questions at the top
 *   > of this section are settled.
 *
 * All three are open, and none of them is this layer's to settle (CLAUDE.md 1). `CLAUDE.md`'s
 * precedence line is `CLAUDE.md > CONTEXT.md > any handoff document`, so the log outranks the phase
 * brief on this point and the passing view carries the caveat rather than omitting it.
 *
 * IT IS NOT DISMISSIBLE AND IT IS NOT A TOOLTIP. A caveat behind an interaction is a caveat that
 * does not appear in the screenshot, and the screenshot is what gets quoted - the same reasoning
 * that puts the sweep denominator beside the median rather than under a hover.
 *
 * TO REMOVE IT: settle the three questions in their own commit, then delete this component and its
 * one usage in ConclusionPassed. It is deliberately a whole component so that deletion is a visible
 * one-line diff rather than an edit somebody makes while doing something else.
 */

const OPEN_QUESTIONS = [
  "Is MIN_ANALOGS = 4 compatible with a 70% consistency threshold? At four analogs the achievable " +
    "consistencies are 0 / 25 / 50 / 75 / 100%, so a pass at 3 of 4 clears a bar that cannot be " +
    "evaluated at that resolution.",
  "Does the analog count need a discount for temporal clustering? Every analog behind both passes " +
    "falls inside 2015–2022.",
  "Why do the engine and the sweep disagree in sign? Both sentences say the rate rose; the sweep's " +
    "one surviving row is −0.137.",
];

export function ProvisionalBand() {
  return (
    <aside className="provisional" data-testid="provisional-band">
      <p className="provisional-head display">Provisional — not cleared for quotation</p>
      <p className="provisional-body">
        Three human decisions stand between this result and a claim anybody should
        repeat. They are recorded in <code>CONTEXT.md</code> and none of them is a
        code change.
      </p>
      <ol className="provisional-list">
        {OPEN_QUESTIONS.map((q, i) => (
          <li key={i}>{q}</li>
        ))}
      </ol>
    </aside>
  );
}
