/** The typecheck is a test, so a strict-mode violation fails the suite rather than the deploy.
 *
 * THIS IS THE GUARD BEHIND THE REFUSAL TYPES. `RefusedConclusion` declares no `median_pct`, so
 * `result.median_pct` in a refusal component is a compile error - but only if somebody runs the
 * compiler. Left to a separate CI step it is a check that exists and does not run, which is worse
 * than one that does not exist, because it reports green from a step nobody looked at.
 */

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ROOT as root } from "./paths";

describe("the build is checked in CI-equivalent form", () => {
  it("18. tsc --noEmit passes under strict", () => {
    expect(() =>
      execFileSync("npx", ["tsc", "--noEmit"], {
        cwd: root,
        stdio: "pipe",
        encoding: "utf8",
      }),
    ).not.toThrow();
  }, 180_000);

  it("18b. strict is actually on, and so are the flags that carry it", () => {
    // `tsc --noEmit` passing means nothing if strict is off. Assert the config rather than
    // trusting the run - the compiler reports success either way.
    const config: unknown = JSON.parse(
      readFileSync(resolve(root, "tsconfig.json"), "utf8"),
    );
    const options = (config as { compilerOptions: Record<string, unknown> })
      .compilerOptions;

    expect(options["strict"]).toBe(true);
    expect(options["noUncheckedIndexedAccess"]).toBe(true);
    expect(options["exactOptionalPropertyTypes"]).toBe(true);
  });
});
