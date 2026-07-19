/**
 * parseMessage.ts - roleplay inline formatting scanner.
 *
 * One deterministic left-to-right pass over message text producing typed
 * segments that map 1:1 to <span> nodes (pure React - no HTML strings, no
 * markdown engine). Two orthogonal state sets:
 *  - emphasis bits (em/strong), toggled by asterisk runs
 *  - a quote state, driven by straight or curly double quotes
 *
 * Ruleset ("boundary-lite", tuned for LLM roleplay output):
 *  - an asterisk run of length 1-3 is a delimiter candidate (1=em, 2=strong,
 *    3=both); length ≥ 4 is literal (decorative dividers)
 *  - OPEN guard (strict): previous char must not be a letter/digit and the
 *    next char must not be whitespace/end - kills `5*3*2`, `un*believ*able`
 *  - CLOSE guard (lenient): previous char must not be whitespace
 *  - a blank line force-closes emphasis (no bleed across paragraphs)
 *  - unclosed emphasis styles to the END of the text - settled and streaming
 *    parse identically, so the stream→settle transition never restyles
 *  - streaming only: a delimiter run touching the very end of the buffer is
 *    withheld for that frame (it may still grow `*` → `**`)
 *  - quotes: `"..."` / `“...”` spans INCLUDE the quote marks; pairing is
 *    same-paragraph only, and an unmatched opener stays plain until its
 *    closer arrives (a tinted false positive is louder than an italic one)
 */

export interface MessageSegment {
  text: string;
  em: boolean;
  strong: boolean;
  quote: boolean;
}

export interface ParseMessageOptions {
  /** Text is still accumulating - withhold a trailing asterisk run. */
  streaming?: boolean;
  /** Handle *asterisk* emphasis (off → asterisks are literal text). */
  emphasis?: boolean;
  /** Handle "quoted speech" spans (off → quotes are plain text). */
  quotes?: boolean;
}

function isWhitespace(c: string | undefined): boolean {
  return c !== undefined && /\s/.test(c);
}

function isWordChar(c: string | undefined): boolean {
  return c !== undefined && /[\p{L}\p{N}]/u.test(c);
}

export function parseMessage(
  text: string,
  options: ParseMessageOptions = {},
): MessageSegment[] {
  const { streaming = false, emphasis = true, quotes = true } = options;
  const segments: MessageSegment[] = [];
  const n = text.length;
  let buf = "";
  let em = false;
  let strong = false;
  let quoteCloser: string | null = null;

  const flush = () => {
    if (buf !== "") {
      segments.push({ text: buf, em, strong, quote: quoteCloser != null });
      buf = "";
    }
  };

  let i = 0;
  while (i < n) {
    const c = text[i];

    if (c === "*" && emphasis) {
      let run = 1;
      while (i + run < n && text[i + run] === "*") run += 1;

      // Streaming: a run touching the buffer end may still be growing -
      // withhold it this frame (no italic→bold flicker).
      if (streaming && i + run === n) {
        flush();
        return segments;
      }

      if (run <= 3) {
        const wantsEm = run === 1 || run === 3;
        const wantsStrong = run === 2 || run === 3;
        const anyOn = (wantsEm && em) || (wantsStrong && strong);
        const allOff = !(wantsEm && em) && !(wantsStrong && strong);
        const prev = i > 0 ? text[i - 1] : undefined;
        const next = i + run < n ? text[i + run] : undefined;

        if (anyOn && !isWhitespace(prev) && prev !== undefined) {
          flush();
          if (wantsEm) em = false;
          if (wantsStrong) strong = false;
          i += run;
          continue;
        }
        if (allOff && !isWordChar(prev) && next !== undefined && !isWhitespace(next)) {
          flush();
          if (wantsEm) em = true;
          if (wantsStrong) strong = true;
          i += run;
          continue;
        }
      }

      buf += "*".repeat(run);
      i += run;
      continue;
    }

    if (quotes && quoteCloser == null && (c === '"' || c === "“")) {
      const closer = c === '"' ? '"' : "”";
      // Same-paragraph pairing: the closer must exist before the next blank
      // line, or the opener stays plain (a stray quote must not flip
      // everything after it). During streaming this naturally defers the
      // tint until the closer arrives.
      const paragraphEnd = text.indexOf("\n\n", i + 1);
      const closeIdx = text.indexOf(closer, i + 1);
      if (closeIdx !== -1 && (paragraphEnd === -1 || closeIdx < paragraphEnd)) {
        flush();
        quoteCloser = closer;
        buf += c;
        i += 1;
        continue;
      }
      // fall through as plain text
    } else if (quotes && quoteCloser != null && c === quoteCloser) {
      buf += c;
      flush();
      quoteCloser = null;
      i += 1;
      continue;
    }

    if (c === "\n" && text[i + 1] === "\n" && (em || strong || quoteCloser != null)) {
      // Paragraph boundary: emphasis never bleeds across blank lines.
      // (Only flush when something is styled - plain text stays one segment.)
      flush();
      em = false;
      strong = false;
      quoteCloser = null; // defensive; pairing already prevents this
    }

    buf += c;
    i += 1;
  }

  flush();
  return segments;
}
