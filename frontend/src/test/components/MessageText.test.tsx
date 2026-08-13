/**
 * MessageText.test.tsx - the component that draws every message body.
 *
 * KADEME 19a: this file did not exist. `parseMessage` itself is well covered
 * (ParseMessage.test.ts, 16 tests) but that is the SCANNER, not the drawing.
 * Everything between a segment list and the DOM was unproven: the class
 * strings, how they are joined, which segments get a wrapper element at all,
 * whether each settings toggle drives its own half of the parser, and whether
 * the `streaming` prop reaches the parser. A whole-tree grep for
 * `strong-span` found the CSS rule and the parser's `strong` flag - the class
 * itself reached no test. Measured, not guessed.
 *
 * The only other place this component renders is the settings preview
 * (AppSettings.test.tsx), which shows one narration span and one quote span
 * through a dialog. That proves the preview, not the component.
 *
 * jsdom applies no stylesheet, so the honest assertion is the class name and
 * the text, never an italic face or an amber tint. index.css turns
 * .narration-span / .strong-span / .quote-span into real rules; that half
 * needs a browser and is listed in the plan's honesty section.
 *
 * Not asserted here on purpose: the `segments == null` fast path when both
 * toggles are off is an OPTIMISATION, not a behaviour - with both options
 * false the parser returns one unstyled segment and the DOM is identical
 * either way. Claiming a test for it would be claiming a mutation this file
 * cannot kill.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render } from "@testing-library/react";

import { MessageText } from "@/components/chat/MessageText";
import { useUiStore } from "@/lib/store/uiStore";

/** Both roleplay stylings are user preferences; every test states its own. */
function styling(narration: boolean, quotes: boolean) {
  useUiStore.setState({
    narrationEnabled: narration,
    quoteTintEnabled: quotes,
  });
}

function draw(text: string, streaming = false) {
  return render(<MessageText text={text} streaming={streaming} />).container;
}

beforeEach(() => {
  styling(true, true);
});

describe("what reaches the page", () => {
  it("gives bold text the strong class", () => {
    // Zero coverage before KADEME 19a: the parser's `strong` flag was tested,
    // the class it turns into was not.
    const el = draw("**shouted**").querySelector("span");
    expect(el, "nothing was wrapped at all").not.toBeNull();
    expect(el).toHaveClass("strong-span");
    expect(el).not.toHaveClass("narration-span");
    expect(el!.textContent).toBe("shouted");
  });

  it("gives narration the narration class", () => {
    const el = draw("*she looked away*").querySelector("span");
    expect(el).toHaveClass("narration-span");
    expect(el).not.toHaveClass("strong-span");
  });

  it("carries every styling a segment has, as separate class names", () => {
    // `***all***` inside quotes is the one input that turns on all three
    // flags at once. If the class list were glued together instead of
    // joined with spaces, CSS would match none of them.
    const spans = draw('"***every one***"').querySelectorAll("span");
    const loud = [...spans].find((s) => s.textContent === "every one");
    expect(loud, "the trebly styled segment never rendered").toBeDefined();
    expect([...loud!.classList].sort()).toEqual([
      "narration-span",
      "quote-span",
      "strong-span",
    ]);
  });

  it("wraps only the segments that carry a styling", () => {
    // The unstyled branch returns a Fragment, deliberately: a <span> around
    // every plain run would triple the node count of a long message for no
    // visual effect.
    const container = draw("plain *lit* plain");
    expect(container.querySelectorAll("span")).toHaveLength(1);
    expect(container.textContent).toBe("plain lit plain");
  });

  it("drops the delimiters and nothing else", () => {
    // The invariant a reader would notice first if the segment walk lost or
    // repeated a character.
    expect(draw('**bold** and *em* and "said"').textContent).toBe(
      'bold and em and "said"',
    );
  });

  it("prints text that looks like markup as text", () => {
    const container = draw("*he typed* <b>not bold</b> & co.");
    expect(container.querySelector("b"), "the string became an element").toBeNull();
    expect(container.textContent).toBe("he typed <b>not bold</b> & co.");
  });
});

describe("each toggle drives its own half", () => {
  it("styles narration and leaves quotes alone when only narration is on", () => {
    styling(true, false);
    const container = draw('*a look* "a word"');
    expect(container.querySelectorAll(".narration-span")).toHaveLength(1);
    expect(container.querySelectorAll(".quote-span")).toHaveLength(0);
    // The quote marks were not consumed by a parser that was told to ignore
    // them, and the asterisks were.
    expect(container.textContent).toBe('a look "a word"');
  });

  it("tints quotes and leaves asterisks alone when only the tint is on", () => {
    styling(false, true);
    const container = draw('*a look* "a word"');
    expect(container.querySelectorAll(".quote-span").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".narration-span")).toHaveLength(0);
    expect(container.querySelectorAll(".strong-span")).toHaveLength(0);
    expect(container.textContent).toBe('*a look* "a word"');
  });

  it("renders the raw string when both are off", () => {
    styling(false, false);
    const container = draw('*a look* "a word" **loud**');
    expect(container.querySelectorAll("span")).toHaveLength(0);
    expect(container.textContent).toBe('*a look* "a word" **loud**');
  });
});

describe("while the reply is still arriving", () => {
  it("withholds a delimiter that may still be growing", () => {
    // A lone trailing `*` could become `**` on the next frame. Drawing it
    // would flash a stray asterisk at the end of every streamed message.
    expect(draw("she paused *", true).textContent).toBe("she paused ");
    // Settled, the same buffer is final, so the asterisk is just an asterisk.
    expect(draw("she paused *", false).textContent).toBe("she paused *");
  });

  it("grows the text without restyling what was already drawn", () => {
    // The stream to settle transition must not restyle anything - that is
    // the invariant the variant carousel leans on.
    //
    // The input matters and the first version of this test got it wrong:
    // `*rain on the roof` parses identically either way, so it passed even
    // with the `streaming` prop severed from the parser. A trailing run is
    // what separates the two paths, so that is what it uses now.
    const mid = draw("*rain falls**", true).querySelector("span");
    const done = draw("*rain falls**", false).querySelector("span");

    // Same styling on both sides of the transition...
    expect(mid).toHaveClass("narration-span");
    expect(done).toHaveClass("narration-span");
    // ...while the buffer genuinely was still short mid-stream, which is the
    // half that proves the prop reached the parser at all.
    expect(mid!.textContent).toBe("rain falls");
    expect(done!.textContent).toBe("rain falls**");
  });
});
