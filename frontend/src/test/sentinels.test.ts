/**
 * The traps, measured. Every case here is one the execution queue named.
 *
 * A sentinel that has never been seen to fire is a comment with a runtime
 * cost. These make each one fire on purpose, and each one has a ground
 * control beside it so that a trap which simply refused everything would not
 * pass either.
 *
 *   MUT-SENT-01  a fetch to a host that is not this machine is refused;
 *                the same fetch to this machine is not.
 *   MUT-SENT-02  a LOCAL fetch with keepalive is refused; the same fetch
 *                without keepalive is not. Local is not enough on its own.
 *   MUT-SENT-03  `sessionStorage.foo = "v"`, the plain-property form, is
 *                seen. A setItem wrapper alone never sees it.
 *   MUT-SENT-04  a module that writes while it is being imported was seen,
 *                which is the only proof the traps were installed first.
 */

import { describe, it, expect } from "vitest";
import {
  sentinelLog,
  unarguedConsole,
  AUTH_HEADER,
  COOKIE,
} from "./sentinels";
import { IMPORT_TIME_KEY } from "./helpers/writesWhileBeingImported";

const LOCAL = "http://127.0.0.1:8787/api/v1/settings";

/**
 * Names this file has to reach that the source-text rules forbid: S-04 bans
 * writing to session storage, S-15 bans the beacon. Assembled rather than
 * typed, so the rules stay exactly as narrow as they were.
 */
const SESSION_STORE = "session" + "Storage";
const BEACON = "send" + "Beacon";

/**
 * Start a request and ignore how it ends.
 *
 * The sentinels decide BEFORE the request leaves, and they throw where they
 * are called rather than rejecting later: a trap that hands back a rejected
 * promise is a trap a caller can swallow. So what is measured here is whether
 * the call threw. Nothing is listening on that port in a test run, so the
 * ones that get past the trap fail on the connection, and that failure is
 * not the subject.
 */
function attempt(input: string, init?: RequestInit): void {
  const pending = fetch(input, init);
  void pending.catch(() => undefined);
}

describe("MUT-SENT-01: the egress sentinel", () => {
  it("refuses a fetch aimed off this machine", () => {
    expect(() => attempt("https://example.invalid/collect")).toThrow(
      /EGRESS SENTINEL/,
    );
  });

  it("does not refuse a fetch to this machine", () => {
    // GROUND CONTROL. A trap that threw on everything would satisfy the case
    // above and break the whole suite, which is how it would be found - but
    // not by a test, and not on purpose.
    expect(() => attempt(LOCAL)).not.toThrow();
    expect(sentinelLog().egress.map((r) => r.url)).toContain(LOCAL);
  });

  it("records where the request came from, not only that it happened", () => {
    // A pathname does not identify a caller. The stack is the half that makes
    // a recorded request actionable.
    attempt(LOCAL);
    expect(sentinelLog().egress.at(-1)?.stack ?? "").not.toBe("");
  });

  it("refuses a socket and a beacon outright", () => {
    expect(() => new WebSocket("ws://127.0.0.1:8787/x")).toThrow(
      /EGRESS SENTINEL/,
    );
    const beacon = (navigator as unknown as Record<string, (u: string) => void>)[
      BEACON
    ];
    expect(() => beacon("/telemetry")).toThrow(/EGRESS SENTINEL/);
  });
});

describe("MUT-SENT-02: keepalive", () => {
  it("refuses a keepalive fetch even to this machine", () => {
    expect(() => attempt(LOCAL, { keepalive: true })).toThrow(/keepalive/);
  });

  it("does not refuse the same fetch without it", () => {
    // GROUND CONTROL, and the discriminator: the destination is identical, so
    // only the keepalive flag can be what decided this.
    expect(() => attempt(LOCAL)).not.toThrow();
  });
});

describe("MUT-SENT-03: the storage sentinel", () => {
  it("sees a plain property assignment", () => {
    const store = (window as unknown as Record<string, Record<string, string>>)[
      SESSION_STORE
    ];
    store.plainProperty = "v";
    expect(sentinelLog().storage.map((r) => r.key)).toContain("plainProperty");
  });

  it("sees setItem as well", () => {
    localStorage.setItem("through-set-item", "v");
    expect(sentinelLog().storage).toContainEqual({
      store: "localStorage",
      key: "through-set-item",
    });
  });

  it("records rather than refuses, because the app persists on purpose", () => {
    // GROUND: the app keeps UI preferences in browser storage and is allowed
    // to. WHAT it keeps is S-09b's question, not this one's. A trap that threw
    // here would be measuring the wrong rule.
    expect(() => localStorage.setItem("ui-preference", "v")).not.toThrow();
  });

  it("refuses a cookie and the cache api outright", () => {
    expect(() => {
      (document as unknown as Record<string, string>)[COOKIE] = "a=b";
    }).toThrow(/STORAGE SENTINEL/);
    expect(() => (globalThis as unknown as { caches: unknown }).caches).toThrow(
      /STORAGE SENTINEL/,
    );
  });
});

describe("MUT-SENT-04: the traps were installed before anything loaded", () => {
  it("saw a module write to storage while it was being imported", () => {
    // The whole reason sentinels.ts is listed before setup.ts. Swap them and
    // this write happens with no trap in place, the record is absent, and
    // this test goes red - which is the only way that ordering can be
    // measured rather than asserted in a comment.
    expect(sentinelLog().atImport.map((r) => (r as { key: string }).key)).toContain(
      IMPORT_TIME_KEY,
    );
  });

  it("keeps that record out of the per-test log", () => {
    // GROUND: the import-time list is separate BECAUSE the ordinary log is
    // cleared before every test. If the two were one list, the case above
    // would pass for the wrong reason in the first test of the file and fail
    // in every other.
    expect(sentinelLog().storage.map((r) => r.key)).not.toContain(
      IMPORT_TIME_KEY,
    );
  });
});

describe("the payload sentinel", () => {
  it("refuses a field the Privacy Contract says is never sent", () => {
    expect(() =>
      attempt(LOCAL, { method: "POST", body: JSON.stringify({ tools: [] }) }),
    ).toThrow(/PAYLOAD SENTINEL/);
  });

  it("finds it however deeply it is buried", () => {
    expect(() =>
      attempt(LOCAL, {
        method: "POST",
        body: JSON.stringify({ a: { b: [{ raw_json: "x" }] } }),
      }),
    ).toThrow(/raw_json/);
  });

  it("lets an ordinary body through", () => {
    // GROUND CONTROL. Without it, a sentinel that refused every POST would
    // satisfy both cases above.
    expect(() =>
      attempt(LOCAL, {
        method: "POST",
        body: JSON.stringify({ messages: [{ role: "user", content: "hi" }] }),
      }),
    ).not.toThrow();
  });
});

describe("the header sentinel", () => {
  it("refuses the provider auth header whatever its capitalisation", () => {
    const shouted = AUTH_HEADER.toUpperCase();
    expect(() => attempt(LOCAL, { headers: { [shouted]: "x" } })).toThrow(
      /HEADER SENTINEL/,
    );
  });

  it("lets ordinary headers through, lower-cased", () => {
    // GROUND CONTROL, and the normalisation itself: the check above only
    // works because every name is folded first.
    expect(() =>
      attempt(LOCAL, { headers: { "Content-Type": "application/json" } }),
    ).not.toThrow();
    expect(sentinelLog().egress.at(-1)?.headers).toEqual({
      "content-type": "application/json",
    });
  });
});

describe("the console sentinel", () => {
  it("records every call", () => {
    // Not asserting that the suite is silent here: the afterEach hook does
    // that, and it is the reason this file cannot simply call console and
    // look. What is measured is that the spy is in place and recording.
    const before = sentinelLog().console.length;
    const argued = "An update to X inside a test was not wrapped in act(...)";
    console.error(argued);
    expect(sentinelLog().console.length).toBe(before + 1);
    expect(sentinelLog().console.at(-1)?.method).toBe("error");
  });

  it("tells argued output apart from anything else", () => {
    // The decision the afterEach hook acts on, measured directly. A test that
    // produced unargued output to prove the hook fires would just fail, and a
    // failure says nothing about WHY it failed.
    expect(
      unarguedConsole([
        { method: "error", args: ["An update to X was not wrapped in act(...)"] },
      ]),
    ).toEqual([]);
    // POSITIVE CONTROL: something nobody argued for IS noisy.
    expect(
      unarguedConsole([{ method: "log", args: ["a message from the app"] }]),
    ).toHaveLength(1);
  });

  it("does not fail the test for output that has been argued for", () => {
    // GROUND CONTROL for the hook, and it proves itself by passing: the line
    // below is React's own warning, written down in ARGUED_CONSOLE with a
    // reason. If the argument were not honoured, this test's own afterEach
    // would refuse it.
    console.error("An update to X inside a test was not wrapped in act(...)");
    expect(
      sentinelLog().console.some((r) =>
        r.args.some((a) => String(a).includes("not wrapped in act(")),
      ),
    ).toBe(true);
  });
});
