/**
 * ParseMessage.test.ts - the roleplay inline-formatting scanner.
 *
 * Pins the "boundary-lite" ruleset edge-case table: asterisk emphasis with
 * strict-open/lenient-close guards, paragraph resets, style-to-end for
 * unclosed spans, streaming withholding, and same-paragraph quote pairing.
 */
import { describe, it, expect } from "vitest";
import { parseMessage } from "@/lib/chat/parseMessage";
import type { MessageSegment } from "@/lib/chat/parseMessage";

function seg(
  text: string,
  flags: Partial<Omit<MessageSegment, "text">> = {},
): MessageSegment {
  return { text, em: false, strong: false, quote: false, ...flags };
}

describe("parseMessage - emphasis", () => {
  it("styles *narration* and hides the delimiters", () => {
    expect(parseMessage("*waves*")).toEqual([seg("waves", { em: true })]);
  });

  it("maps ** to strong and *** to both", () => {
    expect(parseMessage("**bold**")).toEqual([seg("bold", { strong: true })]);
    expect(parseMessage("***both***")).toEqual([
      seg("both", { em: true, strong: true }),
    ]);
  });

  it("nests strong inside narration", () => {
    expect(parseMessage("*a **b** c*")).toEqual([
      seg("a ", { em: true }),
      seg("b", { em: true, strong: true }),
      seg(" c", { em: true }),
    ]);
  });

  it("leaves math and mid-word asterisks literal (open guard)", () => {
    expect(parseMessage("5*3*2")).toEqual([seg("5*3*2")]);
    expect(parseMessage("2 * 3")).toEqual([seg("2 * 3")]);
    expect(parseMessage("un*believ*able")).toEqual([seg("un*believ*able")]);
  });

  it("closes without trailing whitespace (lenient close guard)", () => {
    expect(parseMessage("*She acts.*She speaks")).toEqual([
      seg("She acts.", { em: true }),
      seg("She speaks"),
    ]);
  });

  it("spans a single newline but resets at a blank line", () => {
    expect(parseMessage("*over one\nnewline*")).toEqual([
      seg("over one\nnewline", { em: true }),
    ]);
    expect(parseMessage("*paragraph\n\nbleed")).toEqual([
      seg("paragraph", { em: true }),
      seg("\n\nbleed"),
    ]);
  });

  it("styles an unclosed span to the end (settled and streaming agree)", () => {
    const settled = parseMessage("*unclosed to the end");
    const streaming = parseMessage("*unclosed to the end", {
      streaming: true,
    });
    expect(settled).toEqual([seg("unclosed to the end", { em: true })]);
    expect(streaming).toEqual(settled);
  });

  it("withholds a trailing delimiter run while streaming", () => {
    expect(parseMessage("She waves*", { streaming: true })).toEqual([
      seg("She waves"),
    ]);
    expect(parseMessage("She waves**", { streaming: true })).toEqual([
      seg("She waves"),
    ]);
    // Settled, the same text closes nothing and stays literal at the end.
    expect(parseMessage("She waves*")).toEqual([seg("She waves*")]);
  });

  it("treats runs of four or more and empty pairs as literal", () => {
    expect(parseMessage("****")).toEqual([seg("****")]);
    expect(parseMessage("a ** b")).toEqual([seg("a ** b")]);
  });

  it("keeps asterisks literal when emphasis is disabled", () => {
    expect(parseMessage("*waves*", { emphasis: false })).toEqual([
      seg("*waves*"),
    ]);
  });
});

describe("parseMessage - quotes", () => {
  it("tints straight and curly quoted speech, marks included", () => {
    expect(parseMessage('"speech"')).toEqual([
      seg('"speech"', { quote: true }),
    ]);
    expect(parseMessage("“speech”")).toEqual([
      seg("“speech”", { quote: true }),
    ]);
  });

  it("leaves an unmatched opener plain until closed", () => {
    expect(parseMessage('"start of speech')).toEqual([
      seg('"start of speech'),
    ]);
  });

  it("does not pair across a blank line", () => {
    expect(parseMessage('say "this\n\nnot" paired')).toEqual([
      seg('say "this\n\nnot" paired'),
    ]);
  });

  it("composes with emphasis in both directions", () => {
    expect(parseMessage('*acts "says" acts*')).toEqual([
      seg("acts ", { em: true }),
      seg('"says"', { em: true, quote: true }),
      seg(" acts", { em: true }),
    ]);
    expect(parseMessage('"says *loud*"')).toEqual([
      seg('"says ', { quote: true }),
      seg("loud", { em: true, quote: true }),
      seg('"', { quote: true }),
    ]);
  });

  it("keeps quotes plain when the tint is disabled", () => {
    expect(parseMessage('"speech"', { quotes: false })).toEqual([
      seg('"speech"'),
    ]);
  });
});
