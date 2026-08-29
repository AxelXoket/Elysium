import { readFileSync } from "fs";
import path from "path";
import { describe, expect, it } from "vitest";

/**
 * The install instructions a fresh clone follows, bound to the declarations
 * the tooling actually reads.
 *
 * README said "Node.js 20+" while the installed dependency set needs
 * `^20.19.0 || ^22.13.0 || >=24`: vite's floor is 20.19, eslint's and jsdom's
 * is 22.13, and 21.x and 23.x satisfy neither. A reader on Node 20.10 or 22.5
 * followed the README and got an install that cannot run vite, eslint, jsdom
 * or vitest. Nothing caught it because the range lived only in prose - there
 * was no `engines` field at all.
 *
 * These are string-equality checks over documentation, not behaviour, and they
 * are the right shape for exactly that reason: the claim under test IS a
 * string, and the failure mode is two copies of it drifting apart.
 */

const repoRoot = path.resolve(__dirname, "../../..");
const pkg = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../package.json"), "utf-8"),
) as { engines?: { node?: string }; version?: string };
const readme = readFileSync(path.join(repoRoot, "README.md"), "utf-8");

describe("install docs match the declarations", () => {
  it("declares a Node range in package.json", () => {
    expect(
      pkg.engines?.node,
      "no engines.node: nothing tells npm or a reader which Node runs this",
    ).toBeTruthy();
  });

  it("states that same range in the README prerequisites", () => {
    const range = pkg.engines!.node!;
    expect(
      readme.includes(range),
      `README does not carry the declared range "${range}"`,
    ).toBe(true);

    // POSITIVE CONTROL: the check can fail. A range this file never declared
    // must be absent, so passing above is not just "any string is in a long
    // document".
    expect(readme.includes("^18.0.0 || ^19.4.2 || >=21")).toBe(false);
  });

  it("no longer offers the bare floor that admitted broken versions", () => {
    // GROUND CONTROL for the fix itself: this substring was present before it
    // and is what a reader on Node 21 matched against.
    expect(readme).not.toMatch(/\*\*Node\.js 20\+\*\*/);
  });
});
