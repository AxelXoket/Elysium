/**
 * The TypeScript half of the shared narration contract.
 *
 * `shared/narrative_corpus.json` is asserted by BOTH suites - this file and
 * `backend/tests/test_narrative_corpus.py`. Narration is decided twice in this
 * app: parseMessage tints it on screen, speech_prep hands it to the TTS
 * engine. Two implementations in two languages drift, and the drift is
 * invisible - the screen italicises a span the ear hears in the character's
 * own voice, or the reverse. A disagreement has to fail a test here, not
 * surprise someone mid-conversation.
 *
 * Adjacent emphasised segments are merged before comparing: parseMessage also
 * flushes on quote boundaries, so `*She whispers, "stay", and steps back.*`
 * arrives as three em segments. The corpus is about where NARRATION starts and
 * stops, not about quote tinting, so the quote splits are normalised away.
 */
import { describe, it, expect } from "vitest";

import corpus from "../../../../shared/narrative_corpus.json";
import { parseMessage } from "@/lib/chat/parseMessage";

interface Case {
  name: string;
  text: string;
  em: string[];
}

function narrationSpans(text: string): string[] {
  const segments = parseMessage(text, { emphasis: true, quotes: true });
  const spans: string[] = [];
  let open = false;
  for (const seg of segments) {
    if (seg.em) {
      if (open) spans[spans.length - 1] += seg.text;
      else {
        spans.push(seg.text);
        open = true;
      }
    } else {
      open = false;
    }
  }
  return spans;
}

describe("narration agrees with the shared corpus", () => {
  const cases = corpus.cases as Case[];

  it("has a corpus to assert against", () => {
    // A silently missing or emptied corpus would leave this suite green while
    // asserting nothing at all.
    expect(cases.length).toBeGreaterThanOrEqual(10);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect(narrationSpans(c.text)).toEqual(c.em);
    });
  }
});
