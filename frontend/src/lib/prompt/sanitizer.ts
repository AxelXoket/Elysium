/**
 * sanitizer.ts - the prompt sanitizer, mirrored from backend/prompt_sanitizer.py.
 *
 * Why a mirror: the context gauge counts what the backend SENDS, and the
 * backend sends card and module text after this sanitizer. The two sides read
 * the same table (shared/prompt_sanitizer.json, mirrored byte for byte in
 * sanitizerTable.ts because the dev server refuses imports from outside the
 * frontend root) and are held to the same
 * vectors (shared/sanitizer_vectors.json), so they cannot disagree on a
 * Unicode version: nothing here asks String.prototype.normalize for anything
 * that changes the output. `markers` (a detector, never an output) is the one
 * place that uses NFKC.
 *
 * Not a content filter. It breaks the forms that let third-party text pass for
 * the app (tags, Limits/notebook/persona prefixes, OOC markers, placeholders,
 * the voice header, invisible smuggling) and nothing else.
 *
 * Indices are UTF-16 code units, and every place Python advances "one
 * character" advances one CODE POINT here (cpLenAt), so astral characters
 * (tag letters, emoji, mathematical letters) come out byte-identical.
 *
 * No cache: nothing decrypted is kept here between calls.
 */

import { SANITIZER_TABLE } from "./sanitizerTable";
import type { SanitizerFoldEntry as FoldEntry, SanitizerRange as Range } from "./sanitizerTableType";

const TABLE = SANITIZER_TABLE;

export const RULES_VERSION: number = TABLE.rules_version;
export const FIELD_MAX = 1 << 20;
export const NAME_MAX = 64;
export const NAME_PRECUT = 4 * NAME_MAX;
export const DEFAULT_NAME = "Character";
export const FIELD_TOO_LARGE_CODE = "prompt_field_too_large";
export const LT = "\u2039";
export const GT = "\u203a";
export const OOC_REPL = "author aside";
export const EXPANSION_MAX = 4;

export const RESERVED: readonly string[] = [
  "roleplay", "craft", "character", "scenario", "examples", "example",
  "author_instructions", "author_note", "reminder", "user_persona",
  "prompt_module",
];
export const PREFIX_PHRASES: readonly string[] = [
  "Limits", "End of limits", "Notebook", "End of notebook", "User Persona",
];
export const PLACEHOLDERS: readonly string[] = [
  "nonce", "card_system_prompt", "card_post_history_instructions",
  "length_words", "module_text",
];

export class PromptFieldTooLargeError extends Error {
  readonly code = FIELD_TOO_LARGE_CODE;
  constructor() {
    super(FIELD_TOO_LARGE_CODE);
  }
}

// ── The table ────────────────────────────────────────────────────

const HANGUL_FIRST = 0xac00;
const HANGUL_LAST = 0xd7a3;

function hangul(cp: number): string {
  const s = cp - HANGUL_FIRST;
  const lead = 0x1100 + Math.floor(s / 588);
  const vowel = 0x1161 + Math.floor((s % 588) / 28);
  const trail = 0x11a7 + (s % 28);
  return String.fromCharCode(lead, vowel) + (trail !== 0x11a7 ? String.fromCharCode(trail) : "");
}

function expandFold(entries: readonly FoldEntry[], withHangul: boolean): Map<number, string> {
  const out = new Map<number, string>();
  for (const e of entries) {
    if (e.length === 2) {
      out.set(e[0] as number, e[1] as string);
    } else if (e.length === 3) {
      for (let cp = e[0] as number; cp <= (e[1] as number); cp++) out.set(cp, e[2] as string);
    } else {
      const base = (e[2] as string).codePointAt(0)!;
      const a = e[0] as number;
      for (let cp = a; cp <= (e[1] as number); cp++) out.set(cp, String.fromCodePoint(base + cp - a));
    }
  }
  if (withHangul) {
    for (let cp = HANGUL_FIRST; cp <= HANGUL_LAST; cp++) out.set(cp, hangul(cp));
  }
  return out;
}

const FOLD: Map<number, string> = expandFold(TABLE.fold, TABLE.hangul);

function inRanges(ranges: readonly Range[], cp: number): boolean {
  let lo = 0;
  let hi = ranges.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const [a, b] = ranges[mid];
    if (cp < a) hi = mid - 1;
    else if (cp > b) lo = mid + 1;
    else return true;
  }
  return false;
}

const isSpace = (cp: number) => inRanges(TABLE.space, cp);
const isInvisible = (cp: number) => inRanges(TABLE.invisible, cp);
const isHan = (cp: number) => inRanges(TABLE.han, cp);
const isUnassignedIgnorable = (cp: number) => inRanges(TABLE.unassigned_ignorable, cp);
const VALID_FLAGS = new Set(TABLE.subdivision_flags);

const cpLenAt = (t: string, i: number): number => {
  const c = t.charCodeAt(i);
  if (c >= 0xd800 && c <= 0xdbff && i + 1 < t.length) {
    const d = t.charCodeAt(i + 1);
    if (d >= 0xdc00 && d <= 0xdfff) return 2;
  }
  return 1;
};

/** Code points, the unit both sides count in (PK-28). */
export function cpLength(t: string): number {
  let n = 0;
  for (let i = 0; i < t.length; i += cpLenAt(t, i)) n++;
  return n;
}

/** The first `n` code points of `t`. */
export function cpSlice(t: string, n: number): string {
  let i = 0;
  for (let k = 0; k < n && i < t.length; k++) i += cpLenAt(t, i);
  return t.slice(0, i);
}

const isAscii = (t: string): boolean => {
  for (let i = 0; i < t.length; i++) if (t.charCodeAt(i) > 0x7f) return false;
  return true;
};

/** Trim by the table's whitespace set (Python's str.isspace), not JS trim(). */
export function stripSpace(t: string): string {
  let a = 0;
  let b = t.length;
  while (a < b) {
    const cp = t.codePointAt(a)!;
    if (!isSpace(cp)) break;
    a += cp > 0xffff ? 2 : 1;
  }
  while (b > a) {
    let j = b - 1;
    const lo = t.charCodeAt(j);
    if (lo >= 0xdc00 && lo <= 0xdfff && j > a) {
      const hi = t.charCodeAt(j - 1);
      if (hi >= 0xd800 && hi <= 0xdbff) j -= 1;
    }
    if (!isSpace(t.codePointAt(j)!)) break;
    b = j;
  }
  return t.slice(a, b);
}

function collapseSpaceRuns(t: string): string {
  let out = "";
  let inRun = false;
  for (let i = 0; i < t.length; ) {
    const cp = t.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    if (isSpace(cp)) {
      if (!inRun) out += " ";
      inRun = true;
    } else {
      out += t.slice(i, i + L);
      inRun = false;
    }
    i += L;
  }
  return out;
}

// ── Step 0 and 0b: the sent text ─────────────────────────────────

/** CR, VT, FF, FS, GS, RS, NEL, LS, PS: each is a line break (step 0). */
const LINE_BREAK_CODES = new Set([0x0d, 0x0b, 0x0c, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]);
const BIDI = /[\u202a-\u202e\u2066-\u2069]/g;
const TAG_CHAR = /[\u{E0000}-\u{E007F}]/u;
const EMOJI_TAG_SEQ = /\u{1F3F4}[\u{E0020}-\u{E007E}]+\u{E007F}/gu;
const VS = /[\ufe00-\ufe0f\u{E0100}-\u{E01EF}]/u;
const VS_G = /[\ufe00-\ufe0f\u{E0100}-\u{E01EF}]/gu;
const DOUBLE_LOOKALIKE = /[\u2e28\u2e29\u2985\u2986]/;
const DOUBLE_MAP: readonly (readonly [string, string])[] = [
  ["\u2e28", "( ("], ["\u2985", "( ("], ["\u2e29", ") )"], ["\u2986", ") )"],
];
const EMOJI_PAIR = "\ufe0f\u200d";

export function foldLinebreaks(t: string): string {
  let out = "";
  let pos = 0;
  for (let i = 0; i < t.length; i++) {
    const c = t.charCodeAt(i);
    if (!LINE_BREAK_CODES.has(c)) continue;
    out += t.slice(pos, i) + "\n";
    if (c === 0x0d && t.charCodeAt(i + 1) === 0x0a) i++;
    pos = i + 1;
  }
  return pos === 0 ? t : out + t.slice(pos);
}

function validFlag(seq: string): boolean {
  const cps = Array.from(seq).slice(1, -1);
  return VALID_FLAGS.has(cps.map((c) => String.fromCodePoint(c.codePointAt(0)! - 0xe0000)).join(""));
}

function isTagChar(cp: number): boolean {
  return cp >= 0xe0000 && cp <= 0xe007f;
}

function removeUnassignedIgnorable(t: string): string {
  let out = "";
  let pos = 0;
  for (let i = 0; i < t.length; ) {
    const cp = t.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    if (isUnassignedIgnorable(cp)) {
      out += t.slice(pos, i);
      pos = i + L;
    }
    i += L;
  }
  return pos === 0 ? t : out + t.slice(pos);
}

function collapseInvisible(g: string): string {
  const head = g.startsWith(EMOJI_PAIR) ? EMOJI_PAIR : String.fromCodePoint(g.codePointAt(0)!);
  let rest = "";
  for (let i = head.length; i < g.length; ) {
    const cp = g.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    if (!isInvisible(cp)) rest += g.slice(i, i + L);
    i += L;
  }
  return head + rest;
}

/** Runs of 2+ invisible code points (spaces between members included)
 * collapse to their first member. Linear scan; Python uses a possessive
 * regex, and invisible and space are disjoint, so the two agree. */
function* invisibleRuns(t: string): Generator<[number, number]> {
  let i = 0;
  while (i < t.length) {
    const cp = t.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    if (!isInvisible(cp)) {
      i += L;
      continue;
    }
    let j = i + L;
    let members = 1;
    for (;;) {
      let k = j;
      while (k < t.length) {
        const c = t.codePointAt(k)!;
        if (!isSpace(c)) break;
        k += c > 0xffff ? 2 : 1;
      }
      if (k < t.length) {
        const c = t.codePointAt(k)!;
        if (isInvisible(c)) {
          j = k + (c > 0xffff ? 2 : 1);
          members++;
          continue;
        }
      }
      break;
    }
    if (members >= 2) {
      yield [i, j];
      i = j;
    } else {
      i += L;
    }
  }
}

function collapseRuns(t: string): string {
  let out = "";
  let pos = 0;
  for (const [a, b] of invisibleRuns(t)) {
    out += t.slice(pos, a) + collapseInvisible(t.slice(a, b));
    pos = b;
  }
  return pos === 0 ? t : out + t.slice(pos);
}

export function stripSent(text: string): string {
  let t = text;
  if (TAG_CHAR.test(t)) {
    const keep = new Map<number, number>();
    for (const m of t.matchAll(EMOJI_TAG_SEQ)) {
      if (validFlag(m[0])) keep.set(m.index!, m.index! + m[0].length);
    }
    let out = "";
    for (let i = 0; i < t.length; ) {
      const end = keep.get(i);
      if (end !== undefined) {
        out += t.slice(i, end);
        i = end;
        continue;
      }
      const cp = t.codePointAt(i)!;
      const L = cp > 0xffff ? 2 : 1;
      if (!isTagChar(cp)) out += t.slice(i, i + L);
      i += L;
    }
    t = out;
  }
  t = t.replace(BIDI, "");
  t = removeUnassignedIgnorable(t);
  if (VS.test(t)) {
    let out = "";
    let pos = 0;
    for (const m of t.matchAll(VS_G)) {
      const i = m.index!;
      const ch = m[0];
      out += t.slice(pos, i);
      pos = i + ch.length;
      if (i === 0) continue;
      const lo = t.charCodeAt(i - 1);
      const prevStart = lo >= 0xdc00 && lo <= 0xdfff && i >= 2 ? i - 2 : i - 1;
      const prev = t.codePointAt(prevStart)!;
      const prevChar = String.fromCodePoint(prev);
      if (VS.test(prevChar)) continue;
      const c = ch.codePointAt(0)!;
      if (c >= 0xfe00 && c <= 0xfe0f) {
        if (c !== 0xfe0e && c !== 0xfe0f) continue;
      } else if (!isHan(prev)) {
        continue;
      }
      out += ch;
    }
    t = out + t.slice(pos);
  }
  return collapseRuns(t);
}

// ── The shadow ───────────────────────────────────────────────────

interface Shadow {
  sh: string;
  idx: ((i: number) => number);
}

export function shadow(t: string): Shadow {
  if (isAscii(t)) return { sh: t, idx: (i) => i };
  const parts: string[] = [];
  const idx: number[] = [];
  let runStart = 0;
  for (let i = 0; i < t.length; ) {
    const c = t.charCodeAt(i);
    if (c < 0x80) {
      i++;
      continue;
    }
    const cp = t.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    const f = FOLD.get(cp);
    if (f === undefined) {
      i += L;
      continue;
    }
    if (i > runStart) {
      parts.push(t.slice(runStart, i));
      for (let k = runStart; k < i; k++) idx.push(k);
    }
    if (f) {
      parts.push(f);
      for (let k = 0; k < f.length; k++) idx.push(i);
    }
    i += L;
    runStart = i;
  }
  if (runStart < t.length) {
    parts.push(t.slice(runStart));
    for (let k = runStart; k < t.length; k++) idx.push(k);
  }
  return { sh: parts.join(""), idx: (i) => idx[i] };
}

// ── The rules (ASCII, case-insensitive, on the shadow) ───────────

const WS = "[ \\t\\n\\r\\f\\v]";
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const words = (phrase: string) =>
  phrase.split(" ").map((w) => Array.from(w).map(esc).join(`${WS}*`)).join(`${WS}*`);
const sep = (name: string) => name.replace(/_/g, "[_ -]?");

const R1_SRC = `<${WS}*(?:/+${WS}*)?(?:${RESERVED.map(sep).join("|")})(?![A-Za-z0-9])`;
const R2_SRC = `\\[${WS}*(?:${PREFIX_PHRASES.map(words).join("|")}|Character${WS}*:)`;
const SEPC = "[ \\t\\n\\r\\f\\v./\\-_]";
const R3_VAR = `[O0]${SEPC}?[O0]${SEPC}?C(?![A-Za-z0-9])\\.?`;
const R3_SRC =
  `(?<![A-Za-z0-9])OOC(?![A-Za-z0-9])\\.?(?:${WS}*:)?` +
  `|(?<=[(\\[])${WS}*${R3_VAR}(?:${WS}*:)?` +
  `|(?<![A-Za-z0-9])${R3_VAR}${WS}*:` +
  `|out${WS}+of${WS}+character${WS}*:`;
const R5_SRC = "^[ \\t]*/(?=/)";
const R6_SRC = `\\{\\{${WS}?(?:${PLACEHOLDERS.join("|")})${WS}?\\}\\}`;
const R7_SRC = `^[ \\t]*([#*_=])[ \\t#*_=]*VOICE${WS}+DELIVERY`;

const rx = (src: string, flags: string) => new RegExp(src, flags);

function r3Text(g: string): string {
  return OOC_REPL + (stripSpace(g).endsWith(":") ? ":" : "");
}

function positions(sh: string, ch: string): number[] {
  const out: number[] = [];
  for (let i = sh.indexOf(ch); i !== -1; i = sh.indexOf(ch, i + 1)) out.push(i);
  return out;
}

function bisectLeft(a: readonly number[], x: number): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export interface OpCounter {
  close: number;
}

function closeOnLine(closePos: number[], nlPos: number[], after: number, shLen: number,
  ops?: OpCounter): number | null {
  if (ops) ops.close += 1;
  const j = bisectLeft(closePos, after);
  const k = bisectLeft(nlPos, after);
  const nl = k < nlPos.length ? nlPos[k] : shLen;
  return j < closePos.length && closePos[j] < nl ? closePos[j] : null;
}

function leadingWs(g: string): number {
  let n = 0;
  while (n < g.length && isSpace(g.charCodeAt(n))) n++;
  return n;
}

function trailingWs(g: string): number {
  let n = 0;
  while (n < g.length && isSpace(g.charCodeAt(g.length - 1 - n))) n++;
  return n;
}

/** The rules, uncached. `ops` counts close-bracket searches (gate lane). */
export function sanitizeText(text: string, ops?: OpCounter): string {
  let t = stripSent(foldLinebreaks(text));
  if (DOUBLE_LOOKALIKE.test(t)) {
    for (const [k, v] of DOUBLE_MAP) t = t.split(k).join(v);
  }
  let doneSimple = false;
  if (isAscii(t)) {
    t = t.replace(rx(R3_SRC, "gi"), (g) => r3Text(g));
    while (t.includes("((")) t = t.split("((").join("( (");
    while (t.includes("))")) t = t.split("))").join(") )");
    t = t.replace(rx(R5_SRC, "gm"), (g) => g + " ");
    t = t.replace(rx(R6_SRC, "gi"), (g) => "{ " + g.slice(1));
    t = t.replace(rx(R7_SRC, "gim"), (g, mark: string) => {
      const at = g.length - g.replace(/^[ \t]*/, "").length;
      return g.slice(0, at) + LT + g.slice(at + mark.length);
    });
    doneSimple = true;
  }
  const { sh, idx } = shadow(t);
  const ltp: number[] = [];
  const gtp: number[] = [];
  const ins: number[] = [];
  const dbl = new Map<number, string>();
  const spans = new Map<number, [number, string]>();
  const nlPos = positions(sh, "\n");
  const gtPos = sh.includes("<") ? positions(sh, ">") : [];
  const rbPos = sh.includes("[") ? positions(sh, "]") : [];
  const pairs: [RegExp, number[]][] = [[rx(R1_SRC, "gi"), gtPos], [rx(R2_SRC, "gi"), rbPos]];
  for (const [r, closes] of pairs) {
    for (const m of sh.matchAll(r)) {
      ltp.push(idx(m.index!));
      const p = closeOnLine(closes, nlPos, m.index! + m[0].length, sh.length, ops);
      if (p !== null) gtp.push(idx(p));
    }
  }
  if (!doneSimple) {
    for (const m of sh.matchAll(rx(R3_SRC, "gi"))) {
      const g = m[0];
      const a = m.index! + leadingWs(g);
      const b = m.index! + g.length - trailingWs(g);
      const last = idx(b - 1);
      spans.set(idx(a), [last + cpLenAt(t, last), r3Text(g)]);
    }
    for (let k = 0; k + 1 < sh.length; k++) {
      const c = sh[k];
      if ((c === "(" || c === ")") && sh[k + 1] === c) {
        if (idx(k) === idx(k + 1)) dbl.set(idx(k), c === "(" ? "( (" : ") )");
        else ins.push(idx(k));
      }
    }
    for (const m of sh.matchAll(rx(R5_SRC, "gm"))) ins.push(idx(m.index! + m[0].length - 1));
    for (const m of sh.matchAll(rx(R6_SRC, "gi"))) ins.push(idx(m.index!));
    for (const m of sh.matchAll(rx(R7_SRC, "gim"))) {
      const at = m[0].length - m[0].replace(/^[ \t]*/, "").length;
      ltp.push(idx(m.index! + at));
    }
  }
  if (!ltp.length && !gtp.length && !ins.length && !dbl.size && !spans.size) return t;
  const events: [number, number][] = [
    ...ltp.map((p): [number, number] => [p, 0]),
    ...gtp.map((p): [number, number] => [p, 1]),
    ...[...dbl.keys()].map((p): [number, number] => [p, 2]),
    ...[...spans.keys()].map((p): [number, number] => [p, 3]),
    ...ins.map((p): [number, number] => [p, 9]),
  ].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const w: string[] = [];
  let pos = 0;
  let last: [number, number] | null = null;
  for (const [i, code] of events) {
    if ((last && last[0] === i && last[1] === code) || (i < pos && code !== 9)) continue;
    last = [i, code];
    const L = cpLenAt(t, i);
    if (code === 9) {
      if (i + L < pos) continue;
      if (i >= pos) {
        w.push(t.slice(pos, i + L));
        pos = i + L;
      }
      w.push(" ");
      continue;
    }
    w.push(t.slice(pos, i));
    if (code === 3) {
      const [end, text_] = spans.get(i)!;
      w.push(text_);
      pos = end;
      continue;
    }
    w.push(code === 0 ? LT : code === 1 ? GT : dbl.get(i)!);
    pos = i + L;
  }
  w.push(t.slice(pos));
  return w.join("");
}

/** A card field, greeting, module or model note, as the backend sends it. */
export function sanitizeField(text: string): string {
  if (!text) return "";
  if (text.length > FIELD_MAX && cpLength(text) > FIELD_MAX) throw new PromptFieldTooLargeError();
  return sanitizeText(text);
}

const NAME_STRIP_ASCII = new Set("[]<>{}");

/** A card name, safe to print six times: one line, no brackets of any
 * script, no markers, at most NAME_MAX code points. */
export function sanitizeName(text: string): string {
  let t = cpSlice(text ?? "", NAME_PRECUT);
  t = foldLinebreaks(t);
  t = stripSpace(collapseSpaceRuns(t));
  let kept = "";
  for (let i = 0; i < t.length; ) {
    const cp = t.codePointAt(i)!;
    const L = cp > 0xffff ? 2 : 1;
    const ch = t.slice(i, i + L);
    if (!NAME_STRIP_ASCII.has(ch) && !inRanges(TABLE.name_strip, cp)) kept += ch;
    i += L;
  }
  t = sanitizeText(kept);
  t = stripSpace(cpSlice(t, NAME_MAX));
  return t || DEFAULT_NAME;
}

/** What marker forms `text` still carries. A detector for tests; it never
 * changes what is sent. */
export function markers(text: string): string[] {
  const found = new Set<string>();
  const folded = foldLinebreaks(text);
  const checks: [string, RegExp][] = [
    ["((", /\(\(/], ["))", /\)\)/], ["OOC", rx(R3_SRC, "i")], ["tag", rx(R1_SRC, "i")],
    ["prefix", rx(R2_SRC, "i")], ["//", rx(R5_SRC, "m")], ["placeholder", rx(R6_SRC, "i")],
    ["voice", rx(R7_SRC, "im")],
  ];
  for (const view of [shadow(folded).sh, folded.normalize("NFKC")]) {
    for (const [label, r] of checks) if (r.test(view)) found.add(label);
  }
  const noValid = folded.replace(EMOJI_TAG_SEQ, (m) => (validFlag(m) ? "" : m));
  if (TAG_CHAR.test(noValid)) found.add("tagchar");
  if (/[\u202a-\u202e\u2066-\u2069]/.test(folded)) found.add("bidi");
  for (const [a, b] of invisibleRuns(folded)) {
    const g = folded.slice(a, b);
    if (collapseInvisible(g) !== g) {
      found.add("zwrun");
      break;
    }
  }
  for (let i = 0; i < folded.length; ) {
    const cp = folded.codePointAt(i)!;
    if (isUnassignedIgnorable(cp)) {
      found.add("unassigned-ignorable");
      break;
    }
    i += cp > 0xffff ? 2 : 1;
  }
  if (/[\ufe00-\ufe0f\u{E0100}-\u{E01EF}]{2,}/u.test(folded)) found.add("vsrun");
  return [...found].sort();
}
