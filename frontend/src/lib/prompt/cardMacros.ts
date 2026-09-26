/**
 * The card macro engine, TypeScript half. The Python half is
 * backend/card_macros.py; both are held to shared/macro_vectors.json by their
 * own suites, so read the rules there. In short:
 *
 *   {{char}} <bot> <char>   -> the character's name
 *   {{user}} <user>         -> the active persona's name
 *   {{original}}            -> "" (decision A5)
 *   {{// anything}}         -> "" (newlines allowed, shortest match)
 *   {{trim}}                -> "" plus the CR/LF run in the card text on each side
 *
 * ASCII case-insensitive, spaces and tabs allowed inside the braces, one
 * left-to-right pass, values never re-scanned, unknown macros kept verbatim,
 * no escape syntax. The output of one field is bounded by
 * FIELD_MAX_CODE_POINTS, counted in code points (not UTF-16 units, which is
 * what .length gives) so both languages refuse the same inputs, and checked
 * before the output is built.
 *
 * The regexes carry no `i` and no `u` flag on purpose: `/iu` would match the
 * long s (U+017F) as an "s" and let `{{uſer}}` through.
 *
 * Never call this on text a person typed into the chat, on model output or on
 * notes. Split example dialogue with splitExamples BEFORE rendering.
 */

/** Per-field ceiling after macros, in code points. Pinned against
 *  shared/macro_vectors.json "constants" by the test suite. */
export const FIELD_MAX_CODE_POINTS = 1 << 20;

/** What `{{user}}` becomes when no persona is active. */
export const DEFAULT_USER_NAME = "User";

/** The error code of the size refusal; the backend sends the same string. */
export const FIELD_TOO_LARGE_CODE = "prompt_field_too_large";

export interface MacroContext {
  char: string;
  user: string;
}

export class MacroOutputTooLargeError extends Error {
  readonly code = FIELD_TOO_LARGE_CODE;

  constructor() {
    super(FIELD_TOO_LARGE_CODE);
    this.name = "MacroOutputTooLargeError";
  }
}

const VALUE = 0;
const TRIM = 1;

type Token = [start: number, end: number, kind: number, value: string];

/** Code points, counted the way Python's len() counts them: a valid surrogate
 *  pair is one, a lone surrogate is one. */
function codePointLength(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) i++;
    }
    n++;
  }
  return n;
}

function isNewline(c: string): boolean {
  return c === "\r" || c === "\n";
}

function stripNewlinesStart(s: string): string {
  let i = 0;
  while (i < s.length && isNewline(s[i])) i++;
  return i === 0 ? s : s.slice(i);
}

function stripNewlinesEnd(s: string): string {
  let j = s.length;
  while (j > 0 && isNewline(s[j - 1])) j--;
  return j === s.length ? s : s.slice(0, j);
}

/** Yield every known token, left to right. A generator so the caller can
 *  walk it twice (size, then build) without holding a token list. */
function* scan(text: string, ctx: MacroContext): Generator<Token> {
  const candidate = /\{\{|</g;
  const brace = /\{\{[ \t]*([A-Za-z]+)[ \t]*\}\}/y;
  const angle = /<([A-Za-z]+)>/y;
  const n = text.length;
  let i = 0;
  // Once no "}}" follows some point, none follows any later point either;
  // remembering that keeps many unclosed "{{//" linear.
  let noCloserFrom = n + 1;
  while (i < n) {
    candidate.lastIndex = i;
    const m = candidate.exec(text);
    if (m === null) break;
    const s = m.index;
    if (text.startsWith("{{", s)) {
      if (text.startsWith("{{//", s)) {
        const closeAt = s + 4 >= noCloserFrom ? -1 : text.indexOf("}}", s + 4);
        if (closeAt === -1) {
          noCloserFrom = Math.min(noCloserFrom, s + 4);
        } else {
          yield [s, closeAt + 2, VALUE, ""];
          i = closeAt + 2;
          continue;
        }
        i = s + 1;
        continue;
      }
      brace.lastIndex = s;
      const b = brace.exec(text);
      if (b !== null) {
        const end = s + b[0].length;
        const name = b[1].toLowerCase();
        if (name === "trim") yield [s, end, TRIM, ""];
        else if (name === "char") yield [s, end, VALUE, ctx.char];
        else if (name === "user") yield [s, end, VALUE, ctx.user];
        else if (name === "original") yield [s, end, VALUE, ""];
        i = end;
        continue;
      }
      i = s + 1;
      continue;
    }
    angle.lastIndex = s;
    const a = angle.exec(text);
    if (a !== null) {
      const end = s + a[0].length;
      const name = a[1].toLowerCase();
      if (name === "user") yield [s, end, VALUE, ctx.user];
      else if (name === "bot" || name === "char") yield [s, end, VALUE, ctx.char];
      i = end;
      continue;
    }
    i = s + 1;
  }
}

/** Replace the card macros in `text`. Throws MacroOutputTooLargeError when
 *  the result could exceed FIELD_MAX_CODE_POINTS; nothing is built then. */
export function renderMacros(text: string, ctx: MacroContext): string {
  if (!text) return "";
  const charLen = codePointLength(ctx.char);
  const userLen = codePointLength(ctx.user);
  let bound = codePointLength(text);
  let found = false;
  for (const [start, end, , value] of scan(text, ctx)) {
    // Token text is ASCII except inside a comment, so only a comment needs
    // counting; a value is one of the two names or "".
    const tokenLen =
      text.startsWith("{{//", start) ? codePointLength(text.slice(start, end)) : end - start;
    const valueLen = value === "" ? 0 : value === ctx.char ? charLen : userLen;
    bound += valueLen - tokenLen;
    found = true;
  }
  if (bound > FIELD_MAX_CODE_POINTS) throw new MacroOutputTooLargeError();
  if (!found) return text;

  const out: string[] = [];
  let pos = 0;
  let stripNextGap = false;
  for (const [start, end, kind, value] of scan(text, ctx)) {
    let gap = text.slice(pos, start);
    if (stripNextGap) {
      gap = stripNewlinesStart(gap);
      stripNextGap = false;
    }
    if (kind === TRIM) {
      gap = stripNewlinesEnd(gap);
      stripNextGap = true;
    }
    out.push(gap, value);
    pos = end;
  }
  let tail = text.slice(pos);
  if (stripNextGap) tail = stripNewlinesStart(tail);
  out.push(tail);
  return out.join("");
}

function isExampleSpace(c: string): boolean {
  return c === " " || c === "\t" || c === "\r" || c === "\n";
}

/** Split example dialogue on `<START>` (ASCII case-insensitive, anywhere).
 *  Blocks are trimmed of space, tab, CR and LF only; empty ones are dropped;
 *  the marker is not returned. Call BEFORE renderMacros. */
export function splitExamples(text: string): string[] {
  if (!text) return [];
  const blocks: string[] = [];
  for (const part of text.split(/<[Ss][Tt][Aa][Rr][Tt]>/)) {
    let i = 0;
    let j = part.length;
    while (i < j && isExampleSpace(part[i])) i++;
    while (j > i && isExampleSpace(part[j - 1])) j--;
    if (j > i) blocks.push(part.slice(i, j));
  }
  return blocks;
}
