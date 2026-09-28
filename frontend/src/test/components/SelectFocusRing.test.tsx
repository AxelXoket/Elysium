/**
 * SelectFocusRing.test.tsx - what the shipped stylesheet paints around a
 * drop-down list's trigger when a keyboard lands on it.
 *
 * The shared `SelectTrigger` carries `outline-none` and asks for its focus
 * through `border-ring` and a 50% `ring-ring/50` halo. On the Notes tab both
 * were lost: `.persona-field` pins the border with `!important` and its
 * unlayered `:focus` box-shadow replaces the halo, and the ring that was
 * left measured 1.25:1 on the Limits and Settings views, under WCAG 1.4.11's
 * 3:1. The fix is an outline in the opaque ring token on every trigger.
 *
 * The cascade is settled against the compiled sheet (the project's own Vite
 * pipeline, see helpers/glassSurfaceCss.ts) and the REAL rendered, keyboard
 * focused element, through the same readers the other focus-ring tests use.
 * No component source is read and no class name is copied out of one.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Root } from "postcss";

import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BOUNDARY_SELECT_LABELS } from "@/lib/notebook/labels";
import { useUiStore } from "@/lib/store/uiStore";
import {
  type Candidate,
  candidatesFor,
  cascadeCompare,
  layerOrder,
  parseSheet,
  resolveVars,
  rootTokens,
} from "@/test/helpers/compiledSheet";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";

const STYLES = new Set([
  "none", "hidden", "dotted", "dashed", "solid", "double",
  "groove", "ridge", "inset", "outset", "auto",
]);

let root: Root;
let table: Map<string, string>;
let layers: string[];

beforeAll(async () => {
  root = parseSheet(await loadGlassRightCss());
  table = rootTokens(root);
  layers = layerOrder(root);
}, 60_000);

beforeEach(() => {
  vi.restoreAllMocks();
  useUiStore.setState({ selectedChatId: 7 });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

interface Outline {
  style: string | null;
  width: string | null;
  color: string | null;
}

/** One longhand of the outline as the cascade settles it: the winner among
 *  the declarations of that longhand and of the `outline` shorthand, with
 *  the shorthand's value picked apart for the piece asked about. */
function outlineOf(el: Element): Outline {
  const all = candidatesFor(root, el, /^outline(-style|-width|-color)?$/);
  const pick = (longhand: "style" | "width" | "color"): string | null => {
    let best: Candidate | null = null;
    for (const c of all) {
      if (c.prop !== "outline" && c.prop !== `outline-${longhand}`) continue;
      if (best === null || cascadeCompare(c, best, layers) > 0) best = c;
    }
    if (best === null) return null;
    const value = resolveVars(best.value, table).trim();
    if (best.prop !== "outline") return value;
    const parts = value.split(/\s+/);
    const style = parts.find((p) => STYLES.has(p)) ?? "none";
    const width = parts.find((p) => /^\d+(\.\d+)?px$/.test(p)) ?? "medium";
    const color = parts.filter((p) => p !== style && p !== width).join(" ") || "currentcolor";
    return longhand === "style" ? style : longhand === "width" ? width : color;
  };
  return { style: pick("style"), width: pick("width"), color: pick("color") };
}

/** The ring token's value, read from the compiled sheet. */
function ringToken(): string {
  const ring = table.get("--ring");
  expect(ring, "the sheet no longer defines the ring token").toBeTruthy();
  return resolveVars(ring!, table).trim();
}

function expectRing(el: Element, where: string) {
  const outline = outlineOf(el);
  expect(outline.style, `${where}: the focused trigger paints no outline`).not.toBe("none");
  expect(outline.style, `${where}: nothing styles the outline at all`).not.toBeNull();
  expect(parseFloat(outline.width ?? "0"), `${where}: the outline is thinner than 2px`)
    .toBeGreaterThanOrEqual(2);
  expect(outline.color?.toLowerCase(), `${where}: the outline is not the ring token`)
    .toBe(ringToken().toLowerCase());
}

async function tabTo(el: HTMLElement) {
  const user = userEvent.setup();
  for (let i = 0; i < 40 && document.activeElement !== el; i += 1) await user.tab();
  expect(el, "the keyboard never reached the trigger").toHaveFocus();
  // One more key on the focused trigger. A browser marks Tab focus as
  // focus-visible outright; jsdom's selector engine guesses from the last
  // key event and, once some earlier element was focus-visible, reads a
  // Tab's keyup on the new one as no evidence. A non-modifier key on the
  // element itself is evidence it accepts, and Escape on a closed list does
  // nothing else.
  await user.keyboard("{Escape}");
  expect(el, "the extra key moved focus").toHaveFocus();
  expect(el, "the extra key opened the list").toHaveAttribute("aria-expanded", "false");
  expect(el.matches(":focus-visible"), "keyboard focus does not read as focus-visible")
    .toBe(true);
}

describe("the reader", () => {
  it("read a real stylesheet with its layers and the ring token", () => {
    expect(root.nodes.length).toBeGreaterThan(100);
    expect(layers).toContain("utilities");
    expect(ringToken()).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("POSITIVE CONTROL: sees an outline switched off where one is", () => {
    // The shared trigger UNFOCUSED: the focus rules do not match it, and the
    // base string's `outline-none` wins. If the reader reported a ring here
    // it would report one everywhere.
    render(
      <Select value="a" items={{ a: "Alpha" }}>
        <SelectTrigger aria-label="specimen"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="a">Alpha</SelectItem></SelectContent>
      </Select>,
    );
    const trigger = screen.getByRole("combobox", { name: "specimen" });
    expect(trigger).not.toHaveFocus();
    expect(outlineOf(trigger).style).toBe("none");
  });
});

describe("a drop-down list's trigger under a keyboard", () => {
  it("gets the ring token as its outline, on the bare shared control", async () => {
    render(
      <Select value="a" items={{ a: "Alpha" }}>
        <SelectTrigger aria-label="bare"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="a">Alpha</SelectItem></SelectContent>
      </Select>,
    );
    const trigger = screen.getByRole("combobox", { name: "bare" });
    await tabTo(trigger);
    expectRing(trigger, "bare trigger");
  });

  it("gets it on every qualifier of the Limits view, where it measured 1.25:1", async () => {
    mockFetch({
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
    });
    const labels = Object.values(BOUNDARY_SELECT_LABELS);
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      // A fresh panel per qualifier, each reached from the top of the page.
      cleanup();
      renderWithQueryClient(<BoundaryPanel />);
      const trigger = await screen.findByLabelText(label);
      expect(trigger).toHaveAttribute("role", "combobox");
      await tabTo(trigger);
      expectRing(trigger, label);
    }
  });
});
