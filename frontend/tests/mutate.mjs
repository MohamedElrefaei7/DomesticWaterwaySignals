/** Mutation confirmation for the frontend guards. Not part of the suite; run by hand.
 *
 *     node tests/mutate.mjs            # all rows
 *     node tests/mutate.mjs 5 7        # selected rows
 *
 * CLAUDE.md 0: a test asserted to catch a regression, without watching it catch one, is a comment
 * wearing a test's clothes. So for every row below this harness reverts the decision, watches the
 * NAMED test fail, restores, and watches it pass again.
 *
 * IT SNAPSHOTS FILE CONTENTS, NOT GIT STATE. Phase 8's mutation run crashed mid-cycle and the
 * following run snapshotted an already-mutated file as its baseline, "restored" to it, and reported
 * red after the restore - which reads exactly like a broken test. Contents in memory, restored in a
 * `finally`, so a crash cannot leave a mutation on disk.
 *
 * AND IT CHECKS THE RED IS FOR THE RIGHT REASON. A mutation that fails to compile turns everything
 * red and proves only that the harness runs. Each row names the test that must fail, and a red that
 * does not name it - or that carries a transform/syntax error - is reported as UNCONFIRMED.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const ROOT = process.cwd();
const read = (p) => readFileSync(resolve(ROOT, p), "utf8");
const write = (p, t) => writeFileSync(resolve(ROOT, p), t);

/** Each row: the decision reverted, the file, the edit, and the test that must go red. */
const ROWS = [
  {
    n: 1,
    what: "Add `?? 0` to the median render on the refusal",
    file: "src/components/ConclusionRefused.tsx",
    find: "      <p className=\"verdict-news\">{reason.news}</p>",
    replace:
      "      <p className=\"verdict-news\">{reason.news}</p>\n" +
      "      <p>{`${(result as unknown as { median_pct?: number }).median_pct ?? 0}%`}</p>",
    test: "tests/conclusion.test.tsx",
    mustFail: "1. refused conclusion renders no numeric estimate",
  },
  {
    n: 2,
    what: "Merge the conclusion components: let the refusal import SpanBar",
    file: "src/components/ConclusionRefused.tsx",
    find: 'import { SweepVerdict } from "./SweepVerdict";',
    replace:
      'import { SweepVerdict } from "./SweepVerdict";\nimport { SpanBar } from "./SpanBar";',
    test: "tests/conclusion.test.tsx",
    mustFail: "2. refusal component imports nothing that renders an estimate",
  },
  {
    n: 3,
    what: "Style the refusal with the error treatment",
    file: "src/components/ConclusionRefused.tsx",
    find: '<section className="verdict" data-testid="conclusion-refused"',
    replace:
      '<section className="verdict error-state" data-testid="conclusion-refused"',
    test: "tests/conclusion.test.tsx",
    mustFail: "4. neither refusal state uses error styling",
  },
  {
    n: 4,
    what: "Move the sweep verdict out of the estimate block (into a footnote)",
    file: "src/components/ConclusionPassed.tsx",
    find: "        <SweepVerdict sweep={result.sweep} />\n      </div>",
    replace: "      </div>\n      <SweepVerdict sweep={result.sweep} />",
    test: "tests/conclusion.test.tsx",
    mustFail:
      "5. passing conclusion renders the sweep verdict in the same container as the median",
  },
  {
    n: 5,
    what: "Render the range at half the median's size",
    file: "src/components/SpanBar.tsx",
    find: 'const RANGE_SIZE = "2.1rem";',
    replace: 'const RANGE_SIZE = "0.825rem";',
    test: "tests/conclusion.test.tsx",
    mustFail: "6. the range is rendered at no smaller a size than the median",
  },
  {
    n: 6,
    what: "Set connectNulls to true",
    file: "src/components/SeriesChart.tsx",
    find: "export const CONNECT_NULLS = false;",
    replace: "export const CONNECT_NULLS = true;",
    test: "tests/chart.test.tsx",
    mustFail: "7. null values break the line and are not interpolated",
  },
  {
    n: 7,
    what: "Drop the truncation sentence",
    file: "src/components/TruncationNote.tsx",
    find: "  if (shown >= total) {",
    replace: "  if (true) {",
    test: "tests/chart.test.tsx",
    mustFail: "9. a truncated response renders the showing-N-of-M sentence",
  },
  {
    n: 8,
    what: "Collapse job-overdue and data-stale into one status column",
    file: "src/views/Health.tsx",
    edits: [
      {
        find: '                <th scope="col" data-testid="data-stale-column">\n                  Data stale\n                </th>',
        replace:
          '                <th scope="col" data-testid="data-stale-column">\n                  Job overdue\n                </th>',
      },
      {
        find: '                      {table.error !== null\n                        ? "failed"\n                        : table.stale\n                          ? "stale"\n                          : "fresh"}',
        replace:
          '                      {health.jobs.some(\n                        (j) => j.job_name === table.job_name && j.overdue,\n                      )\n                        ? "stale"\n                        : "fresh"}',
      },
      {
        find: '                      className={table.stale ? "flag flag-on" : "flag"}',
        replace:
          '                      className={\n                        health.jobs.some(\n                          (j) => j.job_name === table.job_name && j.overdue,\n                        )\n                          ? "flag flag-on"\n                          : "flag"\n                      }',
      },
    ],
    test: "tests/health.test.tsx",
    mustFail: [
      "10. job overdue and data stale render as distinct labelled columns",
      "11. a job overdue with fresh data does not render as a data problem",
    ],
  },
  {
    n: 9,
    what: "Remove the year count from the map legend",
    file: "src/views/RiverMap.tsx",
    find:
      "                  <>\n                    — baseline: <strong>{count(years)} years</strong>\n                  </>",
    replace: "                  <>— baseline available</>",
    test: "tests/map.test.tsx",
    mustFail:
      "13. the legend states the baseline period per gauge, never once for the page",
  },
  {
    n: 10,
    what: "Render a null anomaly as mid-scale instead of hatched",
    file: "src/views/RiverMap.tsx",
    find: 'className={years === null ? "station-mark hatch" : "station-mark"}',
    replace: 'className="station-mark"',
    test: "tests/map.test.tsx",
    mustFail:
      "14. a null anomaly renders in the no-baseline treatment, not mid-scale",
  },
  {
    n: 11,
    what: "Call fetch from a view",
    file: "src/views/Signals.tsx",
    find: "  useEffect(() => {\n    let live = true;",
    replace:
      "  useEffect(() => {\n    void fetch(`/api/signals?passing_only=${passingOnly}`);\n    let live = true;",
    test: "tests/client.test.ts",
    mustFail: "15. no component calls fetch directly",
  },
  {
    n: 12,
    what: "Compute a percentage change in a component",
    file: "src/components/ConclusionPassed.tsx",
    find: "      <p className=\"verdict-sentence\">{result.sentence}</p>",
    replace:
      "      <p className=\"verdict-sentence\">{result.sentence}</p>\n" +
      "      <p>{result.median_pct * 100}</p>",
    test: "tests/client.test.ts",
    mustFail: "17. no component performs arithmetic on a response value",
  },
];

function runTest(file) {
  // `--reporter=verbose` prints every test name, which is what the right-reason check reads.
  // NOT `basic`: it was removed in Vitest 4 and is treated as a custom reporter MODULE PATH, so
  // every run dies in the loader with a non-zero exit and no test names at all - which reads
  // exactly like twelve mutations that all went red. Observed on the first run of this harness.
  const args = ["vitest", "run", "--reporter=verbose", "--no-color"];
  if (file) args.splice(2, 0, file);
  try {
    const out = execFileSync("npx", args, {
      cwd: ROOT,
      stdio: "pipe",
      encoding: "utf8",
      env: { ...process.env },
    });
    return { failed: false, out };
  } catch (err) {
    return {
      failed: true,
      out: `${err.stdout ?? ""}\n${err.stderr ?? ""}`,
    };
  }
}

const WRONG_REASON =
  /Transform failed|SyntaxError|Cannot find module|is not defined|ERR_LOAD_URL|Unhandled Error|Failed to load custom Reporter/;

function confirm(row) {
  const edits = row.edits ?? [{ find: row.find, replace: row.replace }];
  const mustFail = Array.isArray(row.mustFail) ? row.mustFail : [row.mustFail];
  const original = read(row.file);

  let mutated = original;
  for (const { find, replace } of edits) {
    if (!mutated.includes(find)) {
      return {
        n: row.n,
        status: "UNCONFIRMED",
        note: `anchor not found in ${row.file}: ${find.slice(0, 60)}…`,
      };
    }
    mutated = mutated.replace(find, replace);
  }

  try {
    write(row.file, mutated);
    const red = runTest(row.test);

    if (!red.failed) {
      return { n: row.n, status: "NOT RED", note: "the mutation did not fail the suite" };
    }
    if (WRONG_REASON.test(red.out)) {
      return {
        n: row.n,
        status: "WRONG REASON",
        note: "red from a compile/import error rather than the assertion",
      };
    }
    const missing = mustFail.filter((name) => !red.out.includes(name));
    if (missing.length > 0) {
      return {
        n: row.n,
        status: "WRONG TEST",
        note: `expected these to fail and they did not: ${missing.join("; ")}`,
      };
    }
    return { n: row.n, status: "RED", note: mustFail.join(" + ") };
  } finally {
    // Restored on every exit path, including a throw. A mutation left on disk is the failure
    // Phase 8's harness shipped.
    write(row.file, original);
  }
}

const wanted = process.argv.slice(2).map(Number);
const rows = wanted.length > 0 ? ROWS.filter((r) => wanted.includes(r.n)) : ROWS;

const results = [];
for (const row of rows) {
  process.stdout.write(`row ${row.n}: ${row.what}\n`);
  const result = confirm(row);
  results.push({ ...result, what: row.what });
  process.stdout.write(`  → ${result.status}  ${result.note}\n`);
}

// After every restore, the whole suite must be green again. This is the half that catches a
// harness which restored to the wrong content.
process.stdout.write("\nrestored — re-running the full suite\n");
const after = runTest("");
process.stdout.write(after.failed ? "  → SUITE RED AFTER RESTORE\n" : "  → suite green\n");

const bad = results.filter((r) => r.status !== "RED");
process.stdout.write(
  `\n${results.length - bad.length}/${results.length} rows confirmed red and restored\n`,
);
if (bad.length > 0 || after.failed) {
  for (const b of bad) process.stdout.write(`  UNCONFIRMED row ${b.n}: ${b.note}\n`);
  process.exit(1);
}
