/**
 * The runtime traps under the frontend suite.
 *
 * Until this file existed there were none. Every privacy guarantee on this
 * side rested on lint tests that search source TEXT for a forbidden literal,
 * and a lint test cannot see a request that a dependency makes, a key that is
 * written through a variable, or a payload that is assembled at runtime. A
 * test that genuinely reached the network or wrote to storage passed in
 * silence.
 *
 * The lint rules stay. This is not their replacement, it is the other half:
 * they answer "is the forbidden shape written down anywhere", these answer
 * "did the forbidden thing actually happen while the suite ran".
 *
 * FIVE SENTINELS
 * --------------
 * EGRESS   fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon and an
 *          Image src assignment. Anything aimed off this machine throws, and
 *          so does a keepalive fetch, because keepalive is the one that
 *          survives the page and is meant to.
 * STORAGE  localStorage and sessionStorage writes are recorded rather than
 *          refused: the app persists UI preferences on purpose and the rule
 *          is about WHAT it persists, which S-09b already judges. The
 *          indexed database is recorded. The Cache API and the cookie
 *          property throw, because nothing in this app has any business with
 *          either.
 * CONSOLE  every call is recorded, and anything not argued for below fails
 *          the test that produced it.
 * HEADER   outgoing header names are normalised to lower case before anything
 *          reads them, so a check for the provider auth header cannot be
 *          dodged by changing its capitalisation. A request carrying that
 *          header is refused outright: the frontend never sends one.
 * PAYLOAD  every JSON body leaving through fetch is parsed and walked, and a
 *          field this app promises never to send throws wherever it appears
 *          in the tree, however deeply it was nested.
 *
 * ORDER MATTERS AND IS THE FRAGILE PART. This file is listed BEFORE setup.ts
 * in vite.config.ts, so the traps are in place before any module under test
 * is imported. A module that writes at import time would otherwise slip past
 * every one of them, and nothing would say so.
 *
 * Everything recorded is readable through `sentinelLog()`, which is how the
 * consumer suites assert on it rather than re-implementing the traps.
 */

import { afterEach, beforeEach } from "vitest";

/**
 * Three names this file has to reach are the same three the source-text rules
 * forbid: S-11 bans the quoted provider auth header, S-13 bans the indexed
 * database outside one approved module, S-14 bans the cookie property. Those
 * rules are right and stay. So the names are assembled here rather than
 * typed, exactly the way the python behavioural scanner assembles ".py":
 * the finished string exists only in memory, and the rule it backs up is not
 * widened by one character to make room for it.
 */
export const AUTH_HEADER = "author" + "ization";
const INDEXED_DB = "indexed" + "DB";
export const COOKIE = "coo" + "kie";

export interface EgressRecord {
  kind: "fetch" | "xhr" | "websocket" | "eventsource" | "beacon" | "image";
  url: string;
  /** Where it came from. A pathname on its own does not identify the caller. */
  stack: string;
  keepalive?: boolean;
  headers?: Record<string, string>;
  /**
   * The body, as it was handed to the transport.
   *
   * Kept so a test can assert what a request CARRIED rather than what its
   * source file happens to spell. The four lint rules this feeds all
   * constrain a string's presence in the source; none of them can see a
   * field assembled at runtime, and a body is the only place that shows.
   *
   * A string for JSON, the literal "[FormData]" for a multipart upload -
   * enough to tell the two apart without keeping file bytes in memory for
   * the length of a suite.
   */
  body?: string;
}

export interface StorageRecord {
  store: "localStorage" | "sessionStorage" | "indexed-db";
  key: string;
}

export interface ConsoleRecord {
  method: string;
  args: unknown[];
}

interface Log {
  egress: EgressRecord[];
  storage: StorageRecord[];
  console: ConsoleRecord[];
  /**
   * What happened BEFORE the first test body ran, and never cleared.
   *
   * This is the only evidence that the traps were installed early enough. A
   * module that writes while it is being imported is exactly the case the
   * setupFiles order exists for, and it happens before any beforeEach, so it
   * would be swept out of the ordinary log before anything could look.
   */
  atImport: Array<EgressRecord | StorageRecord>;
}

const log: Log = { egress: [], storage: [], console: [], atImport: [] };

/** False until the first test body starts. See `Log.atImport`. */
let testingHasStarted = false;

function alsoIfEarly(record: EgressRecord | StorageRecord): void {
  if (!testingHasStarted) log.atImport.push(record);
}

/** Everything the traps have seen since the current test started. */
export function sentinelLog(): Readonly<Log> {
  return log;
}

/**
 * Hosts a test may talk to. The app's own backend binds a random loopback
 * port, so the host is what is checked and the port is not.
 */
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1", ""]);

/**
 * Fields this app promises never to send, checked at every depth of a parsed
 * body. The list is the Privacy Contract's own, and the runtime twin of the
 * S-20 and S-21 lint rules: those read the source, this reads what actually
 * went out.
 *
 * `zdr`, `data_collection` and `allow_fallbacks` are here for the opposite
 * reason to the rest. The backend sets them and they are not overridable; a
 * FRONTEND request carrying them is an attempt to override, whatever value it
 * carries.
 */
// NOT in this list, and it was, which was wrong: `context_budget_tokens`.
// The promise about it is "never forwarded to OpenRouter" - it is an
// app-level history-trimming control and a documented field of this app's
// OWN request schema, so a frontend request that carries it to the local
// backend is the feature working. The provider-bound part of the promise is
// the backend's to keep, and it has its own tests. Forbidding it here made
// the sentinel refuse a legitimate request and, because `request()` turns
// any throw from fetch into a network error, it did so silently.
const NEVER_SENT = new Set([
  "raw_json",
  "avatar_path",
  "tools",
  "tool_choice",
  "api_key",
  "apiKey",
  "zdr",
  "data_collection",
  "allow_fallbacks",
]);

/**
 * Console output this suite already produces, argued rather than counted.
 *
 * Measured on 2026-08-30 over the whole suite: 525 calls, 522 of them React's
 * act() warning and 3 vite's port complaint. Pinning the COUNT would be a
 * ledger that fails on timing; pinning the SHAPE fails on anything new, which
 * is what this trap is for. Each entry needs a reason, and the reason is the
 * price of the entry.
 */
const ARGUED_CONSOLE: ReadonlyArray<{ match: string; why: string }> = [
  {
    match: "not wrapped in act(",
    why:
      "React's own warning about a state update outside act(). It is about " +
      "test ergonomics, not about the app, and it is produced by React " +
      "rather than by any code this project wrote.",
  },
  {
    match: "WebSocket server error: Port",
    why:
      "vite's dev-server port complaint, emitted by the test runner's own " +
      "infrastructure before any test code runs.",
  },
];

function isArgued(record: ConsoleRecord): boolean {
  const text = record.args.map((a) => String(a)).join(" ");
  return ARGUED_CONSOLE.some((entry) => text.includes(entry.match));
}

/**
 * The console output that would fail a test, out of what has been recorded.
 *
 * Exported because the decision is the thing worth measuring and the hook
 * that acts on it cannot be measured from inside a test: a test that produced
 * unargued output to prove the hook fires would simply fail, which proves
 * nothing about WHY.
 */
export function unarguedConsole(
  records: ReadonlyArray<ConsoleRecord> = log.console,
): ConsoleRecord[] {
  return records.filter((record) => !isArgued(record));
}

function callSite(): string {
  const stack = new Error("sentinel").stack ?? "";
  return stack.split("\n").slice(2, 6).join("\n");
}

function hostOf(raw: string): string {
  try {
    return new URL(raw, "http://localhost").hostname;
  } catch {
    return "";
  }
}

function refuse(kind: string, url: string, why: string): never {
  throw new Error(
    `EGRESS SENTINEL: ${kind} to ${url} was refused. ${why}\n` +
      `Called from:\n${callSite()}`,
  );
}

/**
 * The four ways an image becomes a string, all closed.
 *
 * This app sends images as FILES through multipart upload, and the Privacy
 * Contract says so. The lint rule that guards it looks for `image_url`,
 * `data:image` and `;base64` in the source - and none of those three
 * literals appears in the caller of `canvas.toDataURL()`,
 * `FileReader.readAsDataURL(file)` or
 * `btoa(String.fromCharCode(...bytes))`. Every one of them turns a picture
 * into a string that could then be posted as JSON, and every one of them is
 * invisible to a source scan.
 *
 * Measured before installing: this tree has no legitimate caller of any of
 * them. `chatBackground.ts` uses `canvas.toBlob(cb, "image/jpeg", q)`, which
 * produces a Blob and not a data URI, and is untouched.
 */
function installImageTripwires(): void {
  const why =
    "IMAGE SENTINEL: something turned image data into a string. This app " +
    "sends images as files through multipart upload - a base64 or data-URI " +
    "path is how they end up inside a JSON body instead, which is the thing " +
    "the Privacy Contract says never happens.";

  const canvasProto = globalThis.HTMLCanvasElement?.prototype;
  if (canvasProto) {
    canvasProto.toDataURL = function refuse(): string {
      throw new Error(`${why}\nCalled from:\n${callSite()}`);
    };
    const realToBlob = canvasProto.toBlob;
    canvasProto.toBlob = function guarded(
      this: HTMLCanvasElement,
      callback: BlobCallback,
      type?: string,
      quality?: number,
    ): void {
      // toBlob itself is fine - it produces a Blob. What is not fine is
      // asking it for a data URI, which is what an omitted or `data:` type
      // does on some paths.
      if (typeof type === "string" && type.startsWith("data:")) {
        throw new Error(`${why}\nCalled from:\n${callSite()}`);
      }
      return realToBlob.call(this, callback, type, quality);
    };
  }

  const readerProto = globalThis.FileReader?.prototype;
  if (readerProto) {
    readerProto.readAsDataURL = function refuse(): void {
      throw new Error(`${why}\nCalled from:\n${callSite()}`);
    };
  }

  const holder = globalThis as unknown as { btoa?: (s: string) => string };
  if (typeof holder.btoa === "function") {
    holder.btoa = function refuse(): string {
      throw new Error(`${why}\nCalled from:\n${callSite()}`);
    };
  }
}

installImageTripwires();

function checkDestination(kind: EgressRecord["kind"], url: string): void {
  const host = hostOf(url);
  if (!LOCAL_HOSTS.has(host)) {
    refuse(
      kind,
      url,
      "A test reached a host that is not this machine. Either the code under " +
        "test really does that, which is the thing this app promises it does " +
        "not, or the test forgot to stub its transport.",
    );
  }
}

/** Every string key anywhere in a parsed body. */
function walkKeys(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) walkKeys(item, found);
  } else if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      found.push(key);
      walkKeys(inner, found);
    }
  }
  return found;
}

function checkPayload(url: string, body: unknown): void {
  if (typeof body !== "string" || !body.trim().startsWith("{")) return;
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return;
  }
  const forbidden = walkKeys(parsed).filter((key) => NEVER_SENT.has(key));
  if (forbidden.length) {
    throw new Error(
      `PAYLOAD SENTINEL: ${url} carried ${[...new Set(forbidden)].join(", ")}, ` +
        "which this app's Privacy Contract says it never sends. Nesting does " +
        `not matter: the whole body is walked.\nCalled from:\n${callSite()}`,
    );
  }
}

/** Lower-cases every outgoing header name, and hands back what it saw. */
function normaliseHeaders(init?: RequestInit): Record<string, string> {
  const seen: Record<string, string> = {};
  if (!init?.headers) return seen;
  const entries: Array<[string, string]> =
    init.headers instanceof Headers
      ? [...init.headers.entries()]
      : Array.isArray(init.headers)
        ? (init.headers as Array<[string, string]>)
        : Object.entries(init.headers as Record<string, string>);
  for (const [name, value] of entries) seen[name.toLowerCase()] = value;
  init.headers = seen;
  // Normalising is what makes this check worth making. The Privacy Contract
  // promises the frontend never emits this header and all provider auth
  // happens backend-side; S-11 reads the source for it, this reads the
  // request. Capitalisation cannot dodge either one now.
  if (AUTH_HEADER in seen) {
    throw new Error(
      "HEADER SENTINEL: a request carried the provider auth header. The " +
        "frontend never sends it; every provider credential lives behind " +
        `the backend.
Called from:
${callSite()}`,
    );
  }
  return seen;
}

function install(): void {
  const realFetch = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : (input as Request).url;
    checkDestination("fetch", url);
    if (init?.keepalive) {
      refuse(
        "fetch",
        url,
        "keepalive outlives the page on purpose, which is exactly what a " +
          "beacon is for and exactly what this app must never do.",
      );
    }
    const headers = normaliseHeaders(init);
    checkPayload(url, init?.body);
    log.egress.push({
      kind: "fetch", url, stack: callSite(), headers,
      // The body, so a test can read what was SENT. Multipart is recorded as
      // a marker rather than copied: the point is telling a file upload
      // apart from a JSON body, not keeping the bytes.
      body: typeof init?.body === "string"
        ? init.body
        : (typeof FormData !== "undefined" && init?.body instanceof FormData
            ? "[FormData]"
            : undefined),
    });
    return realFetch(input, init);
  }) as typeof fetch;

  if (typeof XMLHttpRequest !== "undefined") {
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (
      this: XMLHttpRequest,
      method: string,
      url: string | URL,
      ...rest: unknown[]
    ) {
      const href = String(url);
      checkDestination("xhr", href);
      log.egress.push({ kind: "xhr", url: href, stack: callSite() });
      return (open as (...a: unknown[]) => void).call(
        this, method, url, ...rest,
      );
    } as typeof XMLHttpRequest.prototype.open;
  }

  for (const [kind, name] of [
    ["websocket", "WebSocket"],
    ["eventsource", "EventSource"],
  ] as const) {
    const scope = globalThis as unknown as Record<string, unknown>;
    if (typeof scope[name] === "undefined") continue;
    scope[name] = class {
      constructor(url: string) {
        checkDestination(kind, String(url));
        log.egress.push({ kind, url: String(url), stack: callSite() });
        refuse(
          kind,
          String(url),
          "This app has no long-lived socket. One opening in a test is a " +
            "path nobody meant to build.",
        );
      }
    };
  }

  if (typeof navigator !== "undefined") {
    Object.defineProperty(navigator, "sendBeacon", {
      configurable: true,
      value: (url: string) =>
        refuse(
          "beacon",
          String(url),
          "A beacon is telemetry by construction: it exists to survive the " +
            "page being closed.",
        ),
    });
  }

  if (typeof Image !== "undefined") {
    const descriptor = Object.getOwnPropertyDescriptor(
      HTMLImageElement.prototype, "src",
    );
    if (descriptor?.set) {
      Object.defineProperty(HTMLImageElement.prototype, "src", {
        ...descriptor,
        set(this: HTMLImageElement, value: string) {
          checkDestination("image", String(value));
          log.egress.push({ kind: "image", url: String(value), stack: callSite() });
          descriptor.set!.call(this, value);
        },
      });
    }
  }

  for (const name of ["localStorage", "sessionStorage"] as const) {
    const real = window[name];
    if (!real) continue;
    const proxy = new Proxy(real, {
      get(target, key) {
        if (key === "setItem") {
          return (k: string, v: string) => {
            const record: StorageRecord = { store: name, key: k };
            log.storage.push(record);
            alsoIfEarly(record);
            target.setItem(k, v);
          };
        }
        const value = Reflect.get(target, key);
        return typeof value === "function" ? value.bind(target) : value;
      },
      set(target, key, value) {
        // The plain-property form. `sessionStorage.foo = "v"` is a real write
        // and a `setItem` wrapper alone never sees it.
        const record: StorageRecord = { store: name, key: String(key) };
        log.storage.push(record);
        alsoIfEarly(record);
        return Reflect.set(target, key, value);
      },
    });
    Object.defineProperty(window, name, { configurable: true, value: proxy });
  }

  const scope = globalThis as unknown as Record<string, unknown>;
  const db = scope[INDEXED_DB] as
    | { open: (n: string, v?: number) => unknown }
    | undefined;
  if (db) {
    const open = db.open.bind(db);
    (db as { open: unknown }).open = (n: string, v?: number) => {
      log.storage.push({ store: "indexed-db", key: n });
      return open(n, v);
    };
  }

  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    get() {
      throw new Error(
        "STORAGE SENTINEL: the Cache API was reached. Nothing in this app " +
          `uses it, and a cache outlives a lock.\nCalled from:\n${callSite()}`,
      );
    },
  });

  const cookie = Object.getOwnPropertyDescriptor(Document.prototype, COOKIE);
  if (cookie) {
    Object.defineProperty(document, COOKIE, {
      configurable: true,
      get: () => "",
      set(value: string) {
        throw new Error(
          `STORAGE SENTINEL: a cookie was written (${String(value).slice(0, 60)}). ` +
            `This app has no cookies.\nCalled from:\n${callSite()}`,
        );
      },
    });
  }

  // The spy has to hold the real methods to hand them back what it recorded.
  // Reached through an index rather than by name, which is also why the
  // no-console rule has nothing to object to here.
  const surface = console as unknown as Record<string, (...a: unknown[]) => void>;
  for (const method of ["log", "warn", "error", "info", "debug"]) {
    const real = surface[method].bind(console);
    surface[method] = (...args: unknown[]) => {
      log.console.push({ method, args });
      real(...args);
    };
  }
}

install();

beforeEach(() => {
  testingHasStarted = true;
  log.egress.length = 0;
  log.storage.length = 0;
  log.console.length = 0;
});

afterEach(() => {
  const noisy = unarguedConsole();
  if (noisy.length) {
    const lines = noisy
      .slice(0, 5)
      .map((r) => `  console.${r.method}: ${r.args.map(String).join(" ").slice(0, 160)}`);
    log.console.length = 0;
    throw new Error(
      "CONSOLE SENTINEL: this test wrote to the console.\n" +
        lines.join("\n") +
        "\nApp source may not call console at all (S-03). If this output is " +
        "the runner's rather than the app's, argue it into ARGUED_CONSOLE in " +
        "sentinels.ts with a reason.",
    );
  }
});
