/** The six tests that decide whether this phase preserved what the seven below it built. */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConclusionNoEvent } from "../src/components/ConclusionNoEvent";
import { ConclusionPassed } from "../src/components/ConclusionPassed";
import { ConclusionRefused } from "../src/components/ConclusionRefused";
import type {
  NoCurrentEventConclusion,
  PassedConclusion,
  RefusedConclusion,
} from "../src/api/types";
import passedFixture from "./fixtures/conclusion-passed.json";
import refusedFixture from "./fixtures/conclusion-refused.json";
import noEventFixture from "./fixtures/conclusion-no-event.json";
import { code, src } from "./paths";

// `as unknown as` because JSON imports widen the `range_pct` tuple to `number[]`. The cast is at
// the fixture boundary only; every component below receives the real type.
const passed = passedFixture as unknown as PassedConclusion;
const refused = refusedFixture as unknown as RefusedConclusion;
const noEvent = noEventFixture as unknown as NoCurrentEventConclusion;

/** Any number carrying a percent sign. `+7%`, `-48%`, `7.35%`, `0%`. */
const PERCENT_NUMBER = /[-−+]?\d[\d,]*(\.\d+)?\s*%/;

describe("the refusal shapes carry no estimate", () => {
  it("1. refused conclusion renders no numeric estimate", () => {
    const { container } = render(<ConclusionRefused result={refused} />);

    // Walk every text node in the rendered tree, not just the fields we thought to check.
    // The failure this catches is a number three levels down that nobody looked at.
    const text = container.textContent ?? "";
    expect(text).not.toMatch(PERCENT_NUMBER);

    // And the estimate keys are absent from the body itself, so there is nothing to render.
    expect(refused).not.toHaveProperty("median_pct");
    expect(refused).not.toHaveProperty("range_pct");
    expect(refused).not.toHaveProperty("matches");

    // The counts that make a refusal actionable ARE present. A test that only asserted absence
    // would pass over a component that rendered nothing at all.
    const block = within(screen.getByTestId("estimate-block"));
    expect(block.getAllByText("2").length).toBeGreaterThan(0);
    expect(block.getAllByText("4").length).toBeGreaterThan(0);
  });

  it("1b. no_current_event renders no numeric estimate either", () => {
    const { container } = render(<ConclusionNoEvent result={noEvent} />);
    expect(container.textContent ?? "").not.toMatch(PERCENT_NUMBER);
  });

  it("2. refusal component imports nothing that renders an estimate", () => {
    // Comments and strings stripped: both files EXPLAIN in prose why the estimate keys are absent,
    // and a scanner that read the explanation as a violation would force the explanation out.
    const refusedSource = code(src("components/ConclusionRefused.tsx"));
    const noEventSource = code(src("components/ConclusionNoEvent.tsx"));

    for (const source of [refusedSource, noEventSource]) {
      // The capability would come back as an import. SpanBar is the only component in this app
      // that renders a median or a range.
      expect(source).not.toMatch(/^\s*import\b.*\bSpanBar\b/m);
      expect(source).not.toMatch(/^\s*import\b.*\bConclusionPassed\b/m);
      // And the fields themselves are never named in these files.
      expect(source).not.toMatch(/\bmedian_pct\b/);
      expect(source).not.toMatch(/\brange_pct\b/);
      expect(source).not.toMatch(/\.matches\b/);
    }

    // The guard is only meaningful if SpanBar really is what renders an estimate. Assert that,
    // so this test cannot pass because the estimate moved somewhere else.
    expect(code(src("components/SpanBar.tsx"))).toMatch(/\bmedian\b/);
    expect(code(src("components/ConclusionPassed.tsx"))).toMatch(/\bSpanBar\b/);
  });

  it("3. no_current_event renders distinct copy from refused", () => {
    const { container: refusedEl } = render(<ConclusionRefused result={refused} />);
    const refusedText = refusedEl.textContent ?? "";
    const { container: quietEl } = render(<ConclusionNoEvent result={noEvent} />);
    const quietText = quietEl.textContent ?? "";

    expect(refusedText).not.toEqual(quietText);

    // A quiet river is not a coverage problem, and must not read as one.
    expect(quietText).toMatch(/not binding|not in a low-water condition/i);
    expect(quietText).not.toMatch(/insufficient history/i);
    expect(refusedText).toMatch(/insufficient history/i);

    // Distinct discriminators reach the DOM, so a future refactor cannot merge the two shapes
    // without this failing.
    expect(screen.getByTestId("conclusion-no-event")).toHaveAttribute(
      "data-gate",
      "no_current_event",
    );
  });

  it("4. neither refusal state uses error styling", () => {
    const { container: refusedEl } = render(<ConclusionRefused result={refused} />);
    const { container: quietEl } = render(<ConclusionNoEvent result={noEvent} />);

    const ERROR_STYLING = /\b(error|danger|alert|warning|critical|failure)\b/;

    for (const root of [refusedEl, quietEl]) {
      for (const el of root.querySelectorAll("*")) {
        expect(el.className.toString()).not.toMatch(ERROR_STYLING);
      }
      expect(root.querySelector('[role="alert"]')).toBeNull();
    }

    // Parity is structural: both refusal shapes use the same band primitive as the passing one,
    // so neither can drift lighter without `.verdict` changing for all three.
    render(<ConclusionPassed result={passed} />);
    expect(screen.getByTestId("conclusion-passed")).toHaveClass("verdict");
    expect(screen.getByTestId("conclusion-refused")).toHaveClass("verdict");
    expect(screen.getByTestId("conclusion-no-event")).toHaveClass("verdict");
  });
});

describe("the passing shape carries its denominator", () => {
  it("5. passing conclusion renders the sweep verdict in the same container as the median", () => {
    render(<ConclusionPassed result={passed} />);

    // SAME CONTAINER, not merely somewhere in the document. A document-wide assertion passes over
    // a layout that puts "1 of 6,966" in a footer, which is the dishonesty this exists to prevent.
    const block = screen.getByTestId("estimate-block");

    expect(within(block).getByTestId("median-label")).toHaveTextContent(
      /^\+7(\.\d)?%$/,
    );
    expect(within(block).getByTestId("sweep-denominator")).toHaveTextContent(
      "1 of 6,966 pairs",
    );
  });

  it("6. the range is rendered at no smaller a size than the median", () => {
    render(<ConclusionPassed result={passed} />);

    const range = screen.getByTestId("range-label");
    const median = screen.getByTestId("median-label");

    const rangeSize = parseFloat(getComputedStyle(range).fontSize);
    const medianSize = parseFloat(getComputedStyle(median).fontSize);

    // The guard is only meaningful if both sizes actually resolved. Two NaNs compare false, but
    // two zeros would compare equal and pass vacuously - CLAUDE.md 2's theme 2.
    expect(rangeSize).toBeGreaterThan(0);
    expect(medianSize).toBeGreaterThan(0);
    expect(rangeSize).toBeGreaterThanOrEqual(medianSize);

    // The range spans zero, and the interface says so in words rather than leaving it to be read
    // off two signs.
    expect(screen.getByTestId("spans-zero-note")).toBeInTheDocument();
  });

  it("6b. the analog dates travel with the claim", () => {
    render(<ConclusionPassed result={passed} />);
    // CLAUDE.md 19: 3 of 4 reads the same whether the events span forty years or four.
    for (const match of passed.matches) {
      expect(screen.getByText(match.event_start)).toBeInTheDocument();
    }
  });

  it("6c. the passing view carries the not-cleared-for-quotation band", () => {
    render(<ConclusionPassed result={passed} />);
    // CONTEXT.md forbids quoting this sentence until three human decisions are settled.
    const band = screen.getByTestId("provisional-band");
    expect(band).toHaveTextContent(/not cleared for quotation/i);
    expect(band).toHaveTextContent(/MIN_ANALOGS/);
  });
});
