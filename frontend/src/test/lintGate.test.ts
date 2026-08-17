/**
 * The gate that runs ESLint.
 *
 * K-38: `npm run lint` was red - thirteen errors and a warning - and nothing
 * anywhere ran it. No CI exists, the one installed git hook runs only
 * verify_hygiene.py, and no test in either suite mentioned eslint. Two
 * documents recorded it as clean while it was not. A rule nobody runs is a
 * comment, so the findings were cleaned and this was written to keep them
 * cleaned.
 *
 * It sits on the frontend side rather than in pytest deliberately. Measured:
 * the tree scan takes about five seconds, the longest single vitest file takes
 * about ten, and vitest runs fifteen workers - so here it disappears into slack
 * that already exists. Bolted onto pytest, which is single-process, it would
 * add its five seconds to every backend run.
 *
 * NOTHING HERE RESTATES THE CONFIG. The rule severities are read back out of
 * ESLint itself with calculateConfigForFile, which reports what ESLint
 * ACTUALLY resolved for a given path - a stronger question than "what does the
 * file say", because a rule can be written at the top of a flat config and then
 * reset to nothing by a later block. That distinction is not hypothetical here:
 * eslint.config.js:22-27 records overrides REPLACING rather than appending as
 * one of the three things it had to be careful about.
 *
 * Three devices, and the middle one is the one K-34 was opened over:
 *
 *   * A FLOOR on how much was scanned. `results.length` being truthy is not a
 *     measurement; an `ignores` line that swallowed the source tree would pass
 *     it. Today the scan sees 275 files.
 *   * A POSITIVE CONTROL that runs the SHIPPED config. `lintText` feeds
 *     synthetic source through the same resolved configuration real files get,
 *     so the question is "does OUR config catch this", not "can ESLint catch
 *     things". The second question is always yes and answers nothing.
 *   * A DISCRIMINATING half for that control: the same forbidden text under a
 *     test path must NOT fire, and clean text must not fire anywhere. A rule
 *     that rejects everything is not a rule, it is an outage.
 *
 * What it cannot do, so a green run is not read as more than it is: ESLint
 * silently skips files no `files` pattern matches when it walks a directory
 * (measured - the "File ignored because no matching configuration" warning
 * appears only when such a file is named explicitly). So this gate proves the
 * matched set is clean and large; it cannot prove a NEW kind of file was
 * matched at all. That is a scope question and it needs its own answer.
 */
import { describe, it, expect } from "vitest";
// The flat-config class directly, rather than `loadESLint`. That helper returns
// a union of the flat and legacy classes, so its constructor does not typecheck
// without narrowing - and on ESLint 10 there is no legacy class left to choose
// between anyway.
import { ESLint } from "eslint";
import path from "node:path";

/** frontend/, the directory eslint.config.js lives in. */
const CWD = path.resolve(__dirname, "..", "..");

/**
 * How few files would mean the scan had collapsed.
 *
 * 275 today. The floor is well under that so ordinary growth or a deleted
 * component does not fail it, and well over the handful an `ignores` mistake
 * would leave behind.
 */
const FILE_FLOOR = 200;

/** The four rules KADEME 21 added. Named, not defined - the severities and
 *  messages are read back from ESLint below, never restated here. */
const PRIVACY_RULE_NAMES = [
  "no-console",
  "no-restricted-globals",
  "no-restricted-properties",
  "no-restricted-syntax",
] as const;

function engine(): ESLint {
  return new ESLint({ cwd: CWD });
}

describe("the ESLint gate", () => {
  it("resolves the privacy rules to errors for a source file", async () => {
    const eslint = engine();
    const config = await eslint.calculateConfigForFile(
      "src/lib/errors/errorStore.ts",
    );

    // GROUND: a config that resolved to almost nothing would satisfy every
    // assertion below by having no opinion at all.
    expect(Object.keys(config.rules ?? {}).length).toBeGreaterThan(50);

    for (const name of PRIVACY_RULE_NAMES) {
      const entry = (config.rules ?? {})[name];
      expect(entry, `${name} is not configured for src/`).toBeDefined();
      // Flat config reports severity numerically. 2 is error; 1 would mean the
      // rule is present but cannot fail a run, which is the state this gate
      // exists to notice.
      expect(
        Array.isArray(entry) ? entry[0] : entry,
        `${name} is configured but not as an error`,
      ).toBe(2);
    }
  }, 60_000);

  it("finds nothing to report across the whole tree", async () => {
    const eslint = engine();
    const results = await eslint.lintFiles(["."]);

    // GROUND before verdict: how much was actually looked at.
    expect(
      results.length,
      "the scan returned almost nothing, so a clean result means nothing",
    ).toBeGreaterThan(FILE_FLOOR);

    const errors = results.flatMap((r) =>
      r.messages
        .filter((m) => m.severity === 2)
        .map((m) => `${r.filePath}:${m.line} ${m.ruleId} ${m.message}`),
    );
    // Warnings are held to the same line on purpose. `eslint .` exits 0 with
    // warnings outstanding, which is how the one warning K-38 found sat in
    // plain sight next to twelve errors and outlived them.
    const warnings = results.flatMap((r) =>
      r.messages
        .filter((m) => m.severity === 1)
        .map((m) => `${r.filePath}:${m.line} ${m.ruleId} ${m.message}`),
    );

    expect(errors, "eslint errors").toEqual([]);
    expect(warnings, "eslint warnings").toEqual([]);
  }, 120_000);

  it("still catches a forbidden global under the shipped config", async () => {
    const eslint = engine();
    const forbidden = 'export const x = localStorage.getItem("a");\n';

    // POSITIVE CONTROL, through the same resolved config a real file gets.
    const caught = await eslint.lintText(forbidden, {
      filePath: path.join(CWD, "src", "lib", "__probe.ts"),
    });
    const rules = caught[0].messages.map((m) => m.ruleId);
    expect(
      rules,
      "the shipped config no longer objects to localStorage in src/lib",
    ).toContain("no-restricted-globals");

    // DISCRIMINATING half one: clean text must pass the same path. Without
    // this, a config that rejected every input would satisfy the assertion
    // above.
    const clean = await eslint.lintText('export const x = 1;\n', {
      filePath: path.join(CWD, "src", "lib", "__probe.ts"),
    });
    expect(clean[0].messages.map((m) => m.ruleId)).toEqual([]);
  }, 60_000);

  it("leaves the test tree exempt, which is why the tests can name what they forbid", async () => {
    const eslint = engine();
    // DISCRIMINATING half two, and a real property rather than a curiosity:
    // static-safety.test.ts has to contain every literal it searches for, so
    // the exemption is load-bearing. If it ever disappears, that file goes red
    // for doing its job and this says which of the two broke.
    const exempt = await eslint.lintText(
      'export const x = localStorage.getItem("a");\n',
      { filePath: path.join(CWD, "src", "test", "__probe.ts") },
    );
    expect(
      exempt[0].messages.map((m) => m.ruleId),
      "the src/test exemption is gone",
    ).toEqual([]);
  }, 60_000);
});
