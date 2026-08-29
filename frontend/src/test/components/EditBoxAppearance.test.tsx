/**
 * EditBoxAppearance.test.tsx - pressing Edit must not change how the message
 * looks.
 *
 * The complaint this closes: editing a message put the text into what read as
 * "a separate window that matches nothing" - its own fixed dark fill, its own
 * border, and a fixed 14px that ignored the reader's font-size setting. The
 * DOM was never wrong: `MessageBubble` swaps a read-only
 * `<p class="message-text">` for a `<textarea class="message-edit-textarea">`
 * INSIDE the same bubble. Only the CSS was.
 *
 * WHY THIS IS A RENDERED TEST AND NOT A STYLESHEET GREP
 *   The first version of this read the declarations out of `index.css` with a
 *   regex, on the belief that jsdom could not help. That was measured and it
 *   is false: jsdom parses the real stylesheet (425 rules) and `getComputedStyle`
 *   resolves the CASCADE - specificity, source order, later duplicates. A grep
 *   sees the first textual match and stops, so a higher-specificity rule
 *   appended anywhere in the file, or a second `background:` lower in the same
 *   block, would win in the browser while every static assertion stayed green.
 *   Both of those mutations are in the proof list for this file and both go
 *   red here.
 *
 *   What jsdom genuinely cannot do is substitute `var()`. That costs nothing,
 *   because nothing below asks for a resolved pixel value: every assertion
 *   compares the edit box against the read-only paragraph it replaced, and an
 *   unresolved `var(--msg-fs, 0.875rem)` on both sides is exactly the equality
 *   this is about. A literal on either side breaks it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "fs";
import path from "path";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import {
  useUiStore,
  MSG_FONT_DEFAULT,
  MSG_LINE_DEFAULT,
} from "@/lib/store/uiStore";
import { MessageList } from "@/components/chat/MessageList";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";

const CSS = readFileSync(
  path.resolve(__dirname, "../../index.css"),
  "utf-8",
);

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const seed = [
  msg(1, "assistant", "greeting"),
  msg(2, "user", "original question"),
];

describe("edit box wears the bubble, not its own window", () => {
  let sheet: HTMLStyleElement;

  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedModelId: "openai/gpt-4o",
      msgFontPx: MSG_FONT_DEFAULT,
      msgLineHeight: MSG_LINE_DEFAULT,
      msgOpacity: 1,
    });
    sheet = document.createElement("style");
    sheet.textContent = CSS;
    document.head.appendChild(sheet);
  });

  afterEach(() => {
    sheet.remove();
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedModelId: null,
      msgFontPx: MSG_FONT_DEFAULT,
      msgLineHeight: MSG_LINE_DEFAULT,
      msgOpacity: 1,
    });
  });

  /**
   * Renders the chat, measures the user message's read-only paragraph, then
   * opens the editor on that SAME message and measures the textarea that took
   * its place. Same bubble, same ancestors, one step apart - which is the
   * comparison the complaint was actually about.
   */
  async function beforeAndAfter() {
    const user = userEvent.setup();
    mockFetch({ "/chats/1/messages": { body: seed } });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={vi.fn()} />);

    const paragraph = await screen.findByText("original question");
    expect(
      paragraph.className,
      "the read-only twin is not .message-text any more",
    ).toContain("message-text");
    const read = {
      fontSize: getComputedStyle(paragraph).fontSize,
      lineHeight: getComputedStyle(paragraph).lineHeight,
      color: getComputedStyle(paragraph).color,
      backgroundColor: getComputedStyle(paragraph).backgroundColor,
      boxShadow: getComputedStyle(paragraph).boxShadow,
    };

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    return { user, box, read, edit: getComputedStyle(box) };
  }

  it("is the same size and leading as the text it replaced", async () => {
    // The reader's font-size/line-height setting arrives as --msg-fs/--msg-lh
    // on <main>. Both sides must reference it the same way; a literal on
    // either side is the box freezing at 14px while the slider moves.
    const { read, edit } = await beforeAndAfter();
    expect(edit.fontSize).toBe(read.fontSize);
    expect(edit.lineHeight).toBe(read.lineHeight);
  });

  it("keeps the reader's ink", async () => {
    // `color: inherit` is the single declaration that lets the contrast preset
    // and the custom-ink override reach the edit box. Pinning any colour here
    // silently opts the editor out of every appearance setting.
    const { read, edit } = await beforeAndAfter();
    expect(edit.color).toBe(read.color);
  });

  it("paints no fill of its own", async () => {
    // The bubble's own fill - set inline by MessageBubble from bubbleSurface(),
    // and different under every contrast preset, ink override, opacity and
    // finish - has to show through. Anything opaque here is the "separate
    // window" this change removed.
    const { read, edit } = await beforeAndAfter();
    expect(edit.backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(edit.backgroundColor).toBe(read.backgroundColor);
  });

  it("shows its border only while focused", async () => {
    // Both halves, because either alone is satisfiable by a wrong rule: a
    // permanently transparent border is a box with no focus indicator, and a
    // permanently coloured one is the old window back.
    //
    // The box opens FOCUSED - the pencil hands it the caret - so the focused
    // reading comes first and the rest state is reached by moving focus away.
    const { box } = await beforeAndAfter();
    const sides = [
      "borderTopColor",
      "borderRightColor",
      "borderBottomColor",
      "borderLeftColor",
    ] as const;

    expect(box.matches(":focus-visible")).toBe(true);
    const focused = getComputedStyle(box).borderTopColor;
    expect(focused, "the focus affordance is gone").not.toBe("rgba(0, 0, 0, 0)");

    // The rest state is read off an UNFOCUSED twin rather than by blurring
    // this one. jsdom tracks the pseudo-class correctly - `matches()` flips -
    // but a computed style it has already produced for an element is not
    // recomputed when focus moves, so blurring reports the focused value
    // forever. A sibling with the same class in the same bubble resolves
    // through the same cascade and has never been focused.
    const twin = box.cloneNode() as HTMLTextAreaElement;
    box.parentElement!.appendChild(twin);
    expect(twin.matches(":focus-visible")).toBe(false);

    const rest = getComputedStyle(twin);
    for (const side of sides) {
      expect(rest[side], `${side} is painted at rest`).toBe("rgba(0, 0, 0, 0)");
    }
    expect(
      focused,
      "focused and unfocused look identical - one of the two is wrong",
    ).not.toBe(rest.borderTopColor);
    twin.remove();
  });

  it("re-measures its height when the reader changes the font size", async () => {
    // The defect this closes was CREATED by making the box follow --msg-fs.
    // While it inherited a fixed `text-sm`, a pinned height stayed correct
    // forever. Now the slider reflows the text, and the textarea is
    // `resize: none` + `overflow: hidden`, so a stale height clips the lines
    // with no scrollbar and no handle to get them back.
    //
    // jsdom has no layout, so scrollHeight is a stub that stands in for "what
    // the text needs at the current metrics" - the measurement itself is the
    // browser's job; what is under test is whether the component asks again.
    let needed = 40;
    const spy = vi
      .spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get")
      .mockImplementation(() => needed);
    try {
      const { box } = await beforeAndAfter();
      expect(box.style.height).toBe("40px");

      // POSITIVE CONTROL: a re-render alone must not re-measure, or the test
      // below would pass on a component that simply sizes on every render.
      needed = 80;
      act(() => {
        useUiStore.setState({ msgOpacity: 0.8 });
      });
      expect(
        box.style.height,
        "the box re-measures on any render, so the font dependency proves nothing",
      ).toBe("40px");

      act(() => {
        useUiStore.setState({ msgFontPx: 19 });
      });
      expect(
        box.style.height,
        "font size changed under an open edit box and the height stayed pinned",
      ).toBe("80px");

      needed = 120;
      act(() => {
        useUiStore.setState({ msgLineHeight: 1.9 });
      });
      expect(
        box.style.height,
        "line height changed under an open edit box and the height stayed pinned",
      ).toBe("120px");
    } finally {
      spy.mockRestore();
    }
  });

  it("casts no shadow of its own", async () => {
    // A drop shadow is a lifted panel, which is the same "window" claim made
    // with light instead of fill. The paragraph casts none; neither may this.
    const { read, edit } = await beforeAndAfter();
    expect(edit.boxShadow === "" || edit.boxShadow === "none").toBe(true);
    expect(edit.boxShadow).toBe(read.boxShadow);
  });

  it("still reserves the border it shows on focus", async () => {
    // The border is transparent at rest and coloured by :focus-visible. jsdom
    // does not evaluate :focus-visible, so what is asserted is the half that
    // makes the other half possible: a non-zero width. At `border-width: 0`
    // the focus rule paints nothing and, with `outline: none` alongside it,
    // a keyboard user gets no indicator at all.
    const { edit } = await beforeAndAfter();
    expect(edit.borderTopWidth).not.toBe("0px");
    expect(edit.borderTopStyle).not.toBe("none");
  });

  it("is a real textarea the caret can enter", async () => {
    // GROUND CONTROL for everything above: all of it is about a box that must
    // still BE an editable box. Nothing here may make it unclickable or
    // read-only in the course of making it invisible.
    const { user, box } = await beforeAndAfter();
    expect(box.tagName).toBe("TEXTAREA");
    expect(box).not.toHaveAttribute("readonly");
    expect(box).toHaveFocus();
    await user.clear(box);
    await user.type(box, "typed");
    expect((box as HTMLTextAreaElement).value).toBe("typed");
  });

  /**
   * The one claim jsdom cannot render: media queries are not evaluated, so
   * whether reduced motion actually neutralises the border transition has to
   * be read out of the stylesheet. Stated as what it is rather than dressed up
   * as behaviour - and it asserts the NEUTRALISER, not merely that the class
   * is named in the block, which is the difference between a rule that works
   * and a rule that looks like it does.
   */
  it("neutralises its own transition under reduced motion", () => {
    const EDIT = ".message-edit-textarea";
    const stripped = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(
      stripped,
      "the rest border no longer animates; this test is now describing nothing",
    ).toMatch(/transition:\s*border-color\s+var\(--duration-fast\)/);

    const REDUCE = "@media (prefers-reduced-motion: reduce)";
    let neutralised = false;
    for (let from = 0; ; ) {
      const at = stripped.indexOf(REDUCE, from);
      if (at < 0) break;
      // The balanced media block, so a later block cannot be credited for an
      // earlier one's selector list.
      let depth = 0;
      let end = at;
      for (let i = stripped.indexOf("{", at); i < stripped.length; i += 1) {
        if (stripped[i] === "{") depth += 1;
        else if (stripped[i] === "}") {
          depth -= 1;
          if (depth === 0) {
            end = i;
            break;
          }
        }
      }
      const media = stripped.slice(at, end + 1);
      const names = new RegExp(
        EDIT.replace(".", "\\.") + String.raw`\s*[,{]`,
      ).test(media);
      if (names && /transition:[^;]*!important/.test(media)) neutralised = true;
      from = at + REDUCE.length;
    }
    expect(
      neutralised,
      `${EDIT} animates, but no reduced-motion block both names it and overrides transition`,
    ).toBe(true);
  });
});
