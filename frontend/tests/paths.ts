/** Source-file resolution for the static assertions, with a guard against resolving nowhere.
 *
 * Tests 2, 15 and 17 are STATIC: they read this project's own source and assert things about it. A
 * path helper that silently resolved to an empty or wrong directory would make all three pass over
 * nothing - a check that verifies the exact thing responsible for a failure and reports it correct
 * (CLAUDE.md 2, theme 2). So this module refuses to load if it cannot see the source tree, and the
 * scanners that use it assert a minimum file count on top.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

export const ROOT = process.cwd();

if (!existsSync(resolve(ROOT, "src/api/client.ts"))) {
  throw new Error(
    `test path resolution is broken: no src/api/client.ts under ${ROOT}. ` +
      `The static assertions would scan an empty set and pass vacuously.`,
  );
}

export const src = (rel: string): string =>
  readFileSync(resolve(ROOT, "src", rel), "utf8");

export function sourcesIn(rel: string): { name: string; text: string }[] {
  const dir = resolve(ROOT, "src", rel);
  const files = readdirSync(dir).filter(
    (f) => f.endsWith(".tsx") || f.endsWith(".ts"),
  );
  if (files.length === 0) {
    throw new Error(`no source files under ${dir}`);
  }
  return files.map((f) => ({
    name: `${rel}/${f}`,
    text: readFileSync(resolve(dir, f), "utf8"),
  }));
}

/** Block comments, line comments and string literals removed, so a field name mentioned in prose
 *  cannot trip a scanner. Every static rule runs against code only. */
export function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ")
    .replace(/(["'`])(?:\\.|(?!\1)[^\\])*\1/g, '""');
}
