/**
 * css-contract.test.ts - rules that only exist if something can match them.
 *
 * Three audit findings shared one shape: CSS that reads as intentional, is
 * documented in a comment right above it, and cannot fire. jsdom has no layout
 * and no `prefers-reduced-transparency`, so these are checked statically -
 * against the selectors the components actually render. The blur coverage
 * case is the exception to "statically": it parses the COMPILED sheet with
 * postcss, because reading selectors out of raw lines is how it went blind.
 * The T-89 block at the end goes one step further and puts its questions to
 * that compiled sheet about elements the components have actually rendered.
 *
 * The layer, stacking-context, geometry and font-stack sections at the
 * bottom are newer and read the COMPILED sheet throughout, because a rule
 * that does not survive the build is not a rule. Two of their claims are
 * about the tree rather than the sheet - where the toast portals to, and
 * which nodes carry an inline layer - so those render. That is why this file
 * pulls in React: the claim decided the method, not the other way round.
 */
import { describe, it, expect, beforeAll, afterEach, vi, beforeEach } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import path from "path";
import { createElement, type ReactNode } from "react";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Root } from "postcss";
import { renderWithQueryClient } from "./helpers/renderWithQueryClient";
import {
  mockFetch,
  apiEscapes,
  guardAgainstApiEscapes,
  shellRoutes,
} from "./mocks/api";
import {
  characterFixture,
  chatFixture,
  messageFixture,
  modelFixture,
  modelListFixture,
  proxyHealthFixture,
  settingsFixture,
  attachmentFixture,
} from "./mocks/fixtures";
import {
  composite,
  frameColour,
  layerPaintOver,
  loadGlassRightCss,
  paintAlpha,
  parseLayer,
  parseRgbColour,
  screenOver,
  type Box,
  type Region,
} from "./helpers/glassSurfaceCss";
import {
  backgroundLayers,
  candidatesFor,
  clipRightPx,
  clipsAndMasksOn,
  declarationOnSelector,
  conditional,
  insetRightPx,
  nearestDeclaration,
  paintedStops,
  paintLayersBehind,
  parseSheet,
  remPx,
  resolvedSelectors,
  resolveVars,
  rootTokens,
  scrollbarWidthPx,
  tokensOf,
  winningDeclaration,
  type PaintLayer,
} from "./helpers/compiledSheet";
import {
  AA_NON_TEXT,
  contrastRatio,
  luminance,
  type Rgb,
} from "@/lib/appearance/contrast";
import { CHAT_BG_DARK_CHROME_BELOW } from "@/lib/appearance/chatBackground";
import { tokens, SYSTEM_FONT_GENERIC, TYPE_SCALE_STEPS } from "@/lib/theme/tokens";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/layout/AppShell";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import {
  FOG_RAMP_BASE,
  FOG_RAMP_STEPS,
  HUE_TRAVEL,
  LIFT,
  LIFT_HI,
  LIFT_LO,
  LIFT_REACH,
  makeFragSrc,
  PANEL_BANDS,
  RAMP_COMPRESS,
  WEATHER_AMOUNT,
} from "@/components/backdrop/fogShader";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { RightPanel } from "@/components/layout/RightPanel";
import { CharacterCreateDialog } from "@/components/characters/CharacterCreateDialog";
import { CharacterEditDialog } from "@/components/characters/CharacterEditDialog";
import { AppSettingsDialog } from "@/components/settings/AppSettingsDialog";
import { GenerationSettingsDialog } from "@/components/generation/GenerationSettingsDialog";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { queryClient } from "@/lib/query/queryClient";
import { useUiStore } from "@/lib/store/uiStore";
import { CharacterImportDialog } from "@/components/characters/CharacterImportDialog";
import { ChatCreateDialog } from "@/components/chats/ChatCreateDialog";
import { ImageLightbox } from "@/components/chat/ImageLightbox";
import { ChatList } from "@/components/sidebar/ChatList";
import { ErrorToastStack } from "@/components/errors/ErrorToastStack";
import { LockOverlay } from "@/components/vault/LockOverlay";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useErrorStore } from "@/lib/errors";
import { useLockAudioWarningStore } from "@/lib/query/vault";
import {
  DIALOG_MAX_HEIGHT,
  DIALOG_WIDTHS,
  LIGHTBOX_CLASSES,
} from "@/lib/theme/dialogScale";
import { INLINE_Z_INDEX_EXCEPTIONS, VAULT_STAGE_Z_INDEX } from "@/lib/theme/layers";
import { cn } from "@/lib/utils";
import {
  PERSISTENT_CONTEXT_COUNTS,
  PERSISTENT_STACKING_CONTEXTS,
  UTILITY_STACKING_CONTEXTS,
} from "./helpers/stackingContexts";
import { API_BASE } from "@/lib/api/base";

// The whole-file escape net, registered once: this file mounts the app shell
// and six dialogs, and a render that lost its stub would otherwise report a
// missing element rather than the requests that left. See mocks/api.ts.
guardAgainstApiEscapes();

/**
 * The wallpaper hook, switchable per test. jsdom has no image pipeline, so
 * the chat scroller's dark-wallpaper state is reached the way
 * ContrastOrthogonality.test.tsx reaches it: the hook reports a dark image
 * and the component answers with its chat-bg-dark class. Off by default,
 * and then the real hook answers, so every other render in this file sees
 * the app as it is.
 */
const wallpaper = vi.hoisted(() => ({ dark: false }));
vi.mock("@/lib/appearance/useChatBackground", async (importOriginal) => {
  const real =
    await importOriginal<typeof import("@/lib/appearance/useChatBackground")>();
  return {
    ...real,
    // The real hook is always called, so the hook count of a mounted
    // ChatCanvas never changes when the switch flips during teardown.
    useChatBackground: () => {
      const answer = real.useChatBackground();
      return wallpaper.dark
        ? {
            style: { backgroundImage: 'linear-gradient(#000,#000), url("blob:x")' },
            dark: true,
          }
        : answer;
    },
  };
});

const SRC = path.resolve(__dirname, "..");
const CSS_RAW = readFileSync(path.join(SRC, "index.css"), "utf-8");
/** Comments explain the rules; they are not the rules. Stripped before every
 *  selector check, or the explanation of a removed class would keep matching. */
const CSS = CSS_RAW.replace(/\/\*[\s\S]*?\*\//g, "");

function allComponentSource(): string {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        if (entry === "test" || entry === "node_modules") continue;
        walk(full);
        continue;
      }
      if (/\.tsx?$/.test(entry)) out.push(readFileSync(full, "utf-8"));
    }
  };
  walk(SRC);
  return out.join("\n");
}

const COMPONENTS = allComponentSource();

/** The first balanced `{...}` block inside `text`. */
function blockAfter(text: string): string {
  let depth = 0;
  for (let i = text.indexOf("{"); i >= 0 && i < text.length; i += 1) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(0, i + 1);
    }
  }
  throw new Error("unterminated block");
}

const REDUCED = "@media (prefers-reduced-transparency: reduce)";

/** Every reduced-transparency block joined - there is more than one, and the
 *  surface-finish one is separate on purpose. */
function reducedBlocks(): string {
  const out: string[] = [];
  let from = 0;
  for (;;) {
    const at = CSS.indexOf(REDUCED, from);
    if (at < 0) break;
    out.push(blockAfter(CSS.slice(at, at + 4000)));
    from = at + REDUCED.length;
  }
  expect(out.length, "the reduced-transparency block is gone").toBeGreaterThan(0);
  return out.join("\n");
}

describe("reduced-transparency block", () => {
  const block = reducedBlocks();

  it("names the fog classes MistCanvas actually renders", async () => {
    // .ambient-fog-canvas / .ambient-mist / .chat-fog appeared exactly once in
    // the whole repo - inside this selector list - so the animated WebGL fog
    // kept running for users who had turned transparency effects off.
    //
    // The live half of that list used to be typed out here, which is the same
    // hole one step removed: a hand-typed name survives a rename in the
    // component, so the block would go on hiding a class nothing renders and
    // this test would stay green. The names come from rendering the fog
    // layers now. The import is deferred so only this one rule pays for React
    // and jsdom and the rest of the file stays fs-only.
    const { fogRootClassNames } = await import("./helpers/fogClassNames");
    const roots = fogRootClassNames();

    // Positive control, and the reason this rule was rewritten: an empty
    // render is covered by any selector list at all, including an empty one.
    expect(roots, "no fog layer rendered").toHaveLength(4);

    /** The block NAMES a class, rather than merely containing its text -
     *  `.mist` must not pass because `.mist-canvas` is written here. */
    const names = (block: string, cls: string) =>
      new RegExp(`\\.${cls.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`)
        .test(block);

    for (const { component, classes } of roots) {
      expect(classes, `${component} rendered no element to hide`)
        .not.toHaveLength(0);
      // display:none on the root takes the layer's whole subtree with it, so
      // one named class per layer is the requirement - not every descendant.
      expect(
        classes.some((cls) => names(block, cls)),
        `${component} renders "${classes.join(" ")}" and the ` +
          "reduced-transparency block names none of those classes",
      ).toBe(true);
    }

    for (const dead of [".ambient-fog-canvas", ".ambient-mist", ".chat-fog"]) {
      expect(block).not.toContain(dead);
    }
  });

  it("covers every blurred surface in the stylesheet", async () => {
    // The list used to cover only [class*="glass"] and .message-bubble-shell in
    // practice; five blurred, text-bearing surfaces kept a live backdrop-filter
    // - which is exactly the readability cost the block was written to remove.
    //
    // This case used to walk the raw text upwards from each `backdrop-filter:`
    // line to the first line ending in `{`, which in a multi-line selector
    // list is the LAST selector of the list. Measured: `.chat-action-menu`
    // shares one rule with `.chat-action-confirm`, so the scan saw only the
    // confirm, found it covered, and the menu kept its live blur with
    // transparency reduced while this test stayed green. And
    // `selector.includes("glass")` let three surfaces certify themselves by
    // their own spelling without the reduced-transparency block naming them.
    // Both holes are the same mistake, reading CSS as lines, so the sheet is
    // parsed now: the COMPILED sheet, through postcss, every selector of every
    // blurred rule with nesting folded in, and `[class*="glass"]` is applied
    // as the attribute substring it is rather than as a keyword. The imports
    // here were deferred to keep the file fs-only; the T-89 block at the end
    // imports the same pipeline statically and shares its cached compile.
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    const {
      parseSheet,
      resolvedSelectors,
      inMedia,
      REDUCED_TRANSPARENCY,
      switchOffReaches,
    } = await import("./helpers/compiledSheet");
    const root = parseSheet(await loadGlassRightCss());

    const blurred = new Set<string>();
    let blurRules = 0;
    root.walkDecls(/^(-webkit-)?backdrop-filter$/, (decl) => {
      // `none !important` is the switch-off, not a surface.
      if (!/blur\(/.test(decl.value)) return;
      const rule = decl.parent;
      if (!rule || rule.type !== "rule") return;
      blurRules += 1;
      // No per-rule comparison against `rule.selectors` here. For a
      // top-level rule that is `resolvedSelectors` measured against its own
      // first line, which cannot go red; for a NESTED blurred rule it is
      // wrong, because the folded selector deliberately no longer contains
      // the raw `&`. The detector for a narrowed collector is the named
      // surface below, which a narrowing really does drop.
      for (const selector of resolvedSelectors(rule)) blurred.add(selector);
    });

    const switchedOff = new Set<string>();
    root.walkDecls(/^(-webkit-)?backdrop-filter$/, (decl) => {
      if (!/^none\b/.test(decl.value.trim())) return;
      if (!inMedia(decl, REDUCED_TRANSPARENCY)) return;
      const rule = decl.parent;
      if (!rule || rule.type !== "rule") return;
      for (const selector of resolvedSelectors(rule)) switchedOff.add(selector);
    });

    // Floors. Both sets are built by matching a property name in the sheet;
    // a typo in either pattern empties it and the loop below then iterates
    // nothing, or covers nothing, and passes.
    expect(
      blurred.size,
      "found no blurred surface in the stylesheet - did the scan break?",
    ).toBeGreaterThan(3);
    expect(
      blurRules,
      "no rule carries a blur; the surfaces below came from nowhere",
    ).toBeGreaterThan(0);
    expect(
      switchedOff.size,
      "the reduced-transparency block switches nothing off",
    ).toBeGreaterThan(0);
    // The scanner's own positive control, and the reason this case exists.
    // `.chat-action-menu` shares its rule with `.chat-action-confirm`, which
    // is exactly the shape the old line-walking scan dropped; the floors
    // above cannot see that, because a scan narrowed to one selector per
    // rule still finds plenty of surfaces and still covers all of them. So
    // the surface the hole hid is named here: if it is not in the set, the
    // set is not the sheet's blurred surfaces, whatever its size.
    expect(
      blurred,
      "the blur scan no longer reaches .chat-action-menu; a rule's selector " +
        "list is being read as fewer selectors than it has",
    ).toContain(".chat-action-menu");

    for (const selector of blurred) {
      const covered = [...switchedOff].some((entry) =>
        switchOffReaches(entry, selector),
      );
      expect(covered, `${selector} keeps its blur with transparency reduced`)
        .toBe(true);
    }
  }, 60_000);

  it("lists no class that no component renders", () => {
    const classes = [...block.matchAll(/\.([a-z][a-z0-9-]*)/g)].map((m) => m[1]);
    // Floor. The block being non-empty is checked where it is built; that
    // says nothing about this regex still finding class names inside it.
    expect(
      classes.length,
      "no class names parsed out of the reduced-transparency block",
    ).toBeGreaterThan(3);
    for (const cls of new Set(classes)) {
      // A dash-prefix counts too: some class names are composed at render time
      // (`surface-${surfaceFinish}`), so the full string never appears in the
      // source. The rule being tested is "something can produce this", not
      // "this literal is typed somewhere".
      const prefix = cls.includes("-")
        ? `${cls.slice(0, cls.lastIndexOf("-"))}-`
        : null;
      const used =
        COMPONENTS.includes(cls) ||
        (prefix != null && COMPONENTS.includes(prefix)) ||
        CSS.includes(`.${cls} {`) ||
        CSS.includes(`.${cls}:`) ||
        CSS.includes(`.${cls}::`);
      expect(used, `.${cls} is listed here but nothing renders or defines it`)
        .toBe(true);
    }
  });
});

describe("dark-wallpaper scrollbar", () => {
  it("writes the dark-wallpaper thumb as a two-class rule and drops the one-class version", () => {
    // Both match the SAME element: the scroller carries Tailwind's
    // overflow-y-auto and sits inside .elysium-page. A single-class selector
    // (0-1-1) lost to the page-wide two-class rule (0-2-1), so the thumb stayed
    // dark-on-dark over a dark wallpaper.
    expect(CSS).toContain(
      ".elysium-page .chat-bg-dark::-webkit-scrollbar-thumb",
    );
    const bare = CSS.match(/(^|\n)\.chat-bg-dark::-webkit-scrollbar-thumb\s*\{/);
    expect(bare, "the outranked single-class rule is still there").toBeNull();
  });
});

describe("message-ink preview", () => {
  it("has something that can put the class on a preview", () => {
    // `.settings-preview.msg-ink-custom` existed but no component ever added
    // the class or the variable to a preview, so the picker showed a contrast
    // ratio and no colour at all.
    expect(CSS).toContain(".settings-preview.msg-ink-custom");
    expect(COMPONENTS).toMatch(/settings-preview[\s\S]{0,400}msg-ink-custom/);
    expect(COMPONENTS).toContain("--msg-ink-custom");
  });
});

/**
 * The handle's conditional yield to a delete confirmation.
 *
 * Same shape as the rest of this file: three numbers in three separate rules
 * have to stay ordered, and nothing about that ordering is observable in jsdom
 * (index.css is never loaded into the test DOM, and the outcome is a paint
 * order, not a value any node carries). So the numbers are read back out of
 * the stylesheet and compared. Every one is READ, never retyped - hardcoding
 * "5" here would let the rule and its test drift apart in lockstep.
 */
describe("panel handle yields to a delete confirmation", () => {
  function zIndexOf(selector: string): number {
    const at = CSS.indexOf(selector + " {");
    expect(at, `no rule for "${selector}"`).toBeGreaterThan(-1);
    const block = CSS.slice(at, CSS.indexOf("}", at));
    const m = block.match(/z-index:\s*(-?\d+)\s*;/);
    expect(m, `"${selector}" declares no z-index`).not.toBeNull();
    return Number(m![1]);
  }

  const YIELD = ".warm-canvas:has(.message-action-confirm) .panel-toggle";

  it("clears the confirm panel while it is open", () => {
    expect(
      zIndexOf(YIELD),
      "the handle still paints over an irreversible choice",
    ).toBeLessThan(zIndexOf(".message-action-confirm"));
  });

  it("yields no further than it has to", () => {
    // The minimal-yield half. Dropping under the hover action row as well
    // would be a second, unasked-for concession: that row is not irreversible
    // and the handle is the only way back into a collapsed panel.
    expect(
      zIndexOf(YIELD),
      "the handle gave up more ground than the confirm needed",
    ).toBeGreaterThan(zIndexOf(".message-actions"));
  });

  it("stays on top when no confirm is open", () => {
    // GROUND CONTROL: the yield is conditional. If the base rule had simply
    // been lowered, both assertions above would still pass while the handle
    // sat under every bubble all the time.
    expect(zIndexOf(".panel-toggle")).toBeGreaterThan(
      zIndexOf(".message-action-confirm"),
    );
  });

  it("depends only on selectors components actually render", () => {
    const src = allComponentSource();
    for (const cls of ["warm-canvas", "message-action-confirm", "panel-toggle"]) {
      expect(src.includes(cls), `nothing renders .${cls}`).toBe(true);
    }
  });
});

/**
 * T-89: the standard scrollbar look is granted BY NAME, not by page scope.
 *
 * Three groups of scroller used to look different, and the line between
 * them was never `.elysium-page`: a dialog is portalled to document.body,
 * so the page's descendant selector cannot reach it and only a selector
 * that names it can. IP-06 ADIM 1 names the two portalled surfaces (the
 * sidebar dialogs' shared class and the Select popup's slot) in the rule
 * that carries `scrollbar-width`, keeps `scrollbar-gutter` on the page's
 * own scrollers, where a reserved strip prevents a layout shift that a
 * floating popup never has, and gives each surface's SCROLLERS their own
 * `scrollbar-color`: a light surface's scrollers take the family's dark
 * thumb, a dark surface's its light one, and each is measured here against
 * every stop of the surface it sits on.
 *
 * The colour is on the scrollers and not on the surface boxes for a reason
 * the last block here guards. `scrollbar-color` inherits, and Chromium
 * drops the whole `::-webkit-scrollbar` family for any element that ends
 * up with a non-auto value, so a surface-wide declaration reached the
 * composer's message box - which scrolls on its own, outside the chat
 * scroller - and swapped its 5px bar for the browser's default one with
 * arrow buttons. That is the shape this step exists to remove, not to
 * spread.
 *
 * Every assertion below is a cascade question put to the COMPILED sheet
 * about an element a component actually rendered: the dialogs are opened,
 * the popup is mounted, the shell is mounted for the page scrollers, and
 * `Element.matches` decides what each rule reaches. No class string is
 * compared to a selector string, so a rename on either side is a red test
 * rather than a green one that stopped looking.
 */
describe("the scrollbar look is granted by name, not by page scope (T-89)", () => {
  const h = createElement;
  const WIDTH = /^scrollbar-width$/;
  /** The one width the app draws. `auto` and `none` are the two values that
   *  would pass a "some rule matched" assertion while changing the bar. */
  const THIN = "thin";
  /** The one gutter the app reserves. `auto` reserves nothing, which is the
   *  layout shift the (b) rule exists to prevent, and a rule that still
   *  MATCHES while saying `auto` passes any "is a gutter reserved" question
   *  that only asks which selectors reached the element. */
  const STABLE = "stable";
  const OVERFLOW_Y = /^overflow-y$/;
  const GUTTER = /^scrollbar-gutter$/;
  const COLOUR = /^scrollbar-color$/;
  /** What stacks a box above or below its siblings, and what takes the
   *  pointer. Both are asked of the panel's decoration: a scrollbar is
   *  drawn on top of its scroller, so anything that paints over the
   *  scroller would paint over the bar too. */
  const Z_INDEX = /^z-index$/;
  const POINTER_EVENTS = /^pointer-events$/;
  // The paint reader lives in paintLayersBehind now; the surface behind a bar
  // is never one declaration.

  let css = "";
  let root: Root;
  let table: Map<string, string>;
  const initialTab = useUiStore.getState().activeRightPanelTab;

  beforeAll(async () => {
    css = await loadGlassRightCss();
    root = parseSheet(css);
    table = rootTokens(root);
  }, 60_000);

  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    wallpaper.dark = false;
    useUiStore.setState({ selectedChatId: null, activeRightPanelTab: initialTab });
  });

  /** The distinct selectors of rules declaring `prop` that reach `el`. */
  const cover = (el: Element, prop: RegExp): string[] => [
    ...new Set(candidatesFor(root, el, prop).map((c) => c.selector)),
  ];

  /** The same question asked for SAFETY rather than for the cascade: every
   *  declaration that could reach `el` or any ancestor of it, `@media` and
   *  `@supports` included. "Nothing reaches it" has to mean nothing, or the
   *  defect walks back in wearing a responsive tweak. */
  const couldEverReach = (el: Element, prop: RegExp): string[] => {
    const out = new Set<string>();
    for (let node: Element | null = el; node; node = node.parentElement) {
      for (const c of candidatesFor(root, node, prop, { includeConditional: true })) {
        out.add(`${c.selector} { ${c.prop}: ${c.value} }`);
      }
    }
    return [...out];
  };

  /** Only what is declared on `el` ITSELF, conditions included. An own
   *  declaration beats any inherited value, so this is the set that decides
   *  an element's own fate regardless of what its ancestors say. */
  const declaredOn = (el: Element, prop: RegExp): string[] =>
    candidatesFor(root, el, prop, { includeConditional: true }).map(
      (c) => `${c.selector} { ${c.prop}: ${c.value} }`,
    );

  function Wrapped({ children }: { children: ReactNode }) {
    return h(TooltipProvider, null, h(GenerationSettingsProvider, null, children));
  }

  // ---- the portalled scrollers, as the components render them ----

  async function characterCreateDialog(): Promise<Element> {
    mockFetch({});
    const user = userEvent.setup();
    renderWithQueryClient(
      h(CharacterCreateDialog, { trigger: h(Button, null, "Create") }),
      { wrapper: TooltipProvider },
    );
    await user.click(screen.getByRole("button", { name: "Create" }));
    return screen.findByRole("dialog");
  }

  async function characterEditDialog(): Promise<Element> {
    mockFetch({});
    const user = userEvent.setup();
    renderWithQueryClient(
      h(CharacterEditDialog, {
        character: characterFixture,
        trigger: h(Button, null, "Edit"),
      }),
      { wrapper: TooltipProvider },
    );
    await user.click(screen.getByRole("button", { name: "Edit" }));
    return screen.findByRole("dialog");
  }

  async function appSettingsDialog(): Promise<Element> {
    mockFetch({ "/settings": { body: settingsFixture } });
    renderWithQueryClient(
      h(AppSettingsDialog, { open: true, onOpenChange: () => undefined }),
      { wrapper: Wrapped },
    );
    return screen.findByRole("dialog");
  }

  async function generationSettingsDialog(): Promise<Element> {
    mockFetch({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: modelListFixture },
    });
    const user = userEvent.setup();
    renderWithQueryClient(
      h(GenerationSettingsDialog, { selectedModel: modelFixture }),
      { wrapper: Wrapped },
    );
    await user.click(screen.getByTestId("generation-settings-trigger"));
    return screen.findByRole("dialog");
  }

  async function selectPopup(): Promise<Element> {
    render(
      h(
        Select,
        { open: true, defaultValue: "a" },
        h(SelectTrigger, null, h(SelectValue, null)),
        h(SelectContent, null, h(SelectItem, { value: "a" }, "A")),
      ),
    );
    const list = await screen.findByRole("listbox");
    const popup = list.closest('[data-slot="select-content"]');
    if (!popup) throw new Error("the listbox is not inside the Select popup slot");
    return popup;
  }

  // ---- the page scrollers, inside the real shell and its real surfaces ----

  function mountShell(): void {
    // Both panels open. They are persisted preferences, so a case earlier in
    // the file that collapses one leaves the next mount without the scroller
    // this block is about, and the failure reads as "no scroller" rather than
    // as leaked state.
    useUiStore.setState({ sidebarCollapsed: false, rightPanelCollapsed: false });
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      [`/chats/${chatFixture.id}/messages`]: { body: [messageFixture] },
      "/chats": { body: [chatFixture] },
    });
    render(h(Providers, null, h(AppShell, null)));
  }

  /** The scroller an element sits in: the nearest ancestor carrying the
   *  utility the plan names as the raw container. */
  function scrollerAround(nodes: Element[], what: string): Element {
    for (const node of nodes) {
      const scroller = node.closest(".overflow-y-auto");
      if (scroller) return scroller;
    }
    throw new Error(`${what} is not inside a scroller`);
  }

  /** GROUND CONTROL for the page scope, and the sidebar surface (.glass-dark):
   *  the character list. */
  async function characterListScroller(): Promise<Element> {
    mountShell();
    const found = await screen.findAllByText(characterFixture.name);
    return scrollerAround(found, "the character card");
  }

  /** The right panel's notebook tab, the scroller inside .glass-right. */
  async function notebookTabScroller(): Promise<Element> {
    useUiStore.setState({ activeRightPanelTab: "notebook" });
    mountShell();
    const panel = await screen.findByRole("tabpanel");
    return scrollerAround([panel], "the notebook tab");
  }

  /** The right panel's models tab, whose scroller is a ScrollArea viewport
   *  rather than a plain overflow container. It is the third selector of the
   *  width rule and of the gutter rule, and the only element in the app that
   *  either of them reaches by that name. */
  async function modelsTabViewport(): Promise<Element> {
    useUiStore.setState({ activeRightPanelTab: "models" });
    mountShell();
    const panel = await screen.findByRole("tabpanel");
    const viewport = panel.querySelector('[data-slot="scroll-area-viewport"]');
    if (!viewport) throw new Error("the models tab rendered no ScrollArea viewport");
    return viewport;
  }

  /** The chat scroller on the light canvas (.warm-canvas), a chat selected. */
  async function chatScroller(): Promise<Element> {
    useUiStore.setState({ selectedChatId: chatFixture.id });
    mountShell();
    const found = await screen.findAllByText(messageFixture.content);
    return scrollerAround(found, "the message");
  }

  /** The same scroller over a dark wallpaper (see the hook switch above). */
  async function darkChatScroller(): Promise<Element> {
    wallpaper.dark = true;
    const scroller = await chatScroller();
    expect(
      scroller.classList.contains("chat-bg-dark"),
      "the dark-wallpaper state did not render",
    ).toBe(true);
    return scroller;
  }

  type Mount = () => Promise<Element>;

  /** The portalled group: their scrollers are outside `.elysium-page`, so
   *  until the width rule named them by slot no rule reached them. */
  const GROUP_B: Array<[string, Mount]> = [
    ["CharacterCreateDialog", characterCreateDialog],
    ["CharacterEditDialog", characterEditDialog],
    ["AppSettingsDialog", appSettingsDialog],
    ["SelectContent", selectPopup],
  ];

  it("the reader can say no, and the sheet has something to say yes with", () => {
    // POSITIVE CONTROL for every "is reached" below: a matcher that answered
    // yes to everything would pass them all.
    expect(cover(document.createElement("div"), WIDTH)).toEqual([]);
    // GROUND: the sheet carries rules to be reached at all.
    let declared = 0;
    root.walkDecls(WIDTH, () => {
      declared += 1;
    });
    expect(declared, "no rule in the compiled sheet declares scrollbar-width").toBeGreaterThan(0);
  });

  it.each(GROUP_B)(
    "%s is reached by a rule declaring scrollbar-width",
    async (name, mount) => {
      const el = await mount();
      // The element measured is the scroller the plan names, not its overlay.
      expect(el.classList.contains("overflow-y-auto"), `${name}: not the scroller`).toBe(true);
      const reached = cover(el, WIDTH);
      expect(reached, `${name} is reached by no rule declaring scrollbar-width`).not.toEqual([]);
      // Reaching it is not enough; the VALUE is the whole point. `auto` here
      // leaves the standard properties half-applied - the colour rules are
      // still non-auto, so Chromium drops the webkit family anyway and this
      // surface falls to the platform's wide bar with arrow buttons, which is
      // the regression this step exists to remove. `none` would hide it.
      expect(
        winningDeclaration(root, el, WIDTH)?.value,
        `${name} is reached, but the value that wins on it is not a thin bar`,
      ).toBe(THIN);
      // It got there by NAME. The element is portalled to document.body, so
      // a page-scoped selector cannot be what matched it.
      expect(
        reached.filter((s) => s.startsWith(".elysium-page")),
        `${name} was reached through the page scope, which a portal is never inside`,
      ).toEqual([]);
      // And nothing takes it back under a condition. The cascade reader
      // drops `@media` and `@supports` by design, so the value asserted
      // above is the unconditional one and a responsive rule setting
      // `scrollbar-width: none` on this surface would be invisible to it.
      // The colour side of this block already has this leg; the width side
      // is where a bar is actually removed.
      const conditioned = declaredOn(el, WIDTH).filter((d) => !d.includes(THIN));
      expect(
        conditioned,
        `${name} is given a scrollbar-width other than ${THIN} by a conditional rule ` +
          `(${conditioned.join("; ")})`,
      ).toEqual([]);
    },
    15_000,
  );

  it("GROUND CONTROL: a scroller inside .elysium-page is reached too", async () => {
    // Without this, "nothing is reached" would pass on an empty sheet.
    expect(cover(await characterListScroller(), WIDTH)).not.toEqual([]);
  }, 20_000);

  it("GenerationSettingsDialog is reached on its own name, so it does not lean on .sidebar-dialog", async () => {
    // The plan writes this by name: removing `.sidebar-dialog` from the list
    // (M-06-1) takes the cover from the three B dialogs and from nothing
    // else, because this one is named a second time.
    expect(cover(await generationSettingsDialog(), WIDTH)).toContain(".generation-dialog");
  }, 15_000);

  it.each(GROUP_B)("%s reserves no scrollbar gutter", async (name, mount) => {
    const el = await mount();
    expect(
      cover(el, GUTTER),
      `${name} reserves a gutter although a floating surface has no layout shift to prevent`,
    ).toEqual([]);
  }, 15_000);

  /** The three selectors the gutter rule carries, each reached by the
   *  element the app really draws that scroller on. These are the positive
   *  controls for "group B reserves none" above: without them that absence
   *  would pass on a sheet that reserved a gutter nowhere at all. The VALUE
   *  is asserted alongside, because `stable` is the whole of the claim - a
   *  rule that still matches while saying `auto` reserves nothing, and this
   *  step MOVED the declaration (it deleted the duplicate inside the
   *  `.generation-dialog` geometry rule), which is exactly when a value
   *  slips. */
  const GUTTERED: Array<[string, Mount]> = [
    ["the generation dialog", generationSettingsDialog],
    ["a page scroller", characterListScroller],
    ["a page ScrollArea viewport", modelsTabViewport],
  ];

  it.each(GUTTERED)("POSITIVE CONTROL: %s does reserve a gutter", async (name, mount) => {
    const el = await mount();
    expect(cover(el, GUTTER), `${name} is reached by no rule declaring a gutter`).not.toEqual(
      [],
    );
    expect(
      winningDeclaration(root, el, GUTTER)?.value,
      `${name} is reached by a gutter rule, but the value that wins on it reserves nothing`,
    ).toBe(STABLE);
  }, 25_000);

  /* ---- where the bar runs, and what is painted there ----------------
   *
   * A scrollbar is not a point on a surface and it is not the whole of one
   * either: it is a narrow band down the right edge, travelling from the top
   * of its scroller to the bottom. Which layers are over it, and what each
   * of them paints there, is a question about geometry - and reading it as
   * "every stop the surface contains" gets it wrong in both directions at
   * once. Too generous, because a radial highlight centred at 12% of the
   * width and a shadow that dies at 46% of the height get stacked onto a
   * band neither of them reaches. Too harsh nowhere in particular, which is
   * worse: two errors cancelling read like a measurement.
   *
   * So the ground is taken per BAND of the bar's travel, over the bar's own
   * width, with each layer placed in the box the sheet gives it and
   * evaluated by `layerPaintOver` at the parameter range that band really
   * covers. The ratios come back per band, the worst is the answer, and the
   * failure message names the band.
   */

  /**
   * How wide Chromium draws a `scrollbar-width: thin` bar, in CSS pixels.
   *
   * This is a MEASUREMENT, not a declaration, and it cannot be otherwise:
   * `thin` is a keyword and the engine chooses the thickness. Rendered
   * headless over the six surfaces of this suite at device scales 1, 1.25,
   * 1.5 and 2, `offsetWidth - clientWidth` came back 10.00 CSS px on every
   * one of them at every scale, with the thumb painted inside that track.
   *
   * It is deliberately NOT read from `::-webkit-scrollbar { width }`.
   * Chromium drops the whole webkit family for any element whose computed
   * `scrollbar-width` is not `auto`, which after this rule split is every
   * scroller measured here, so that declaration is inert on all of them -
   * it sized the band at 8.80 px while the engine drew 10.00, leaving the
   * inboard 1.20 px of every bar unmeasured. ADIM 2 deletes the rule
   * outright, at which point the same reader would have returned the 5 px
   * the base layer declares and moved this band again without a word. ADIM 2
   * pins its own bar width to this constant for the same reason.
   */
  const SCROLLBAR_THIN_PX = 10;

  /** The bands of the bar's travel, named so a failure says where. They
   *  TILE it: the bar runs the whole height of its scroller, so leaving a
   *  gap anywhere - and in particular at the panel's lit top-right corner,
   *  which is the worst ground in the app - would be a reader that reports a
   *  better number by looking at less. The case below holds them to it. */
  const BANDS = [
    { name: "lit corner", y0: 0, y1: 0.15 },
    { name: "upper", y0: 0.15, y1: 0.4 },
    { name: "middle", y0: 0.4, y1: 0.7 },
    { name: "foot", y0: 0.7, y1: 1 },
  ];

  /**
   * How finely the bar's band is diced before an envelope is taken over it.
   *
   * An envelope is a BOUND, not a point: it offers each layer its own
   * extreme over the region, and two layers' extremes can sit at different
   * places in it. The sidebar shows the cost - over the whole lit corner at
   * once the reader combines the `::before` radial's peak, which is at the
   * bar's INNER edge, with the sheen's peak at the outer one, and reports a
   * corner lighter than any pixel there: 2.94:1 against the 3.01:1 the
   * panel actually paints. Dicing makes the bound converge on the pixel.
   * Measured on that corner: one cell per band 2.94, 6 by 4 2.98, 10 by 6
   * 3.01, and 16 by 10 and 40 by 24 the same 3.01, so this grid is past the
   * knee and a finer one moves nothing.
   */
  const CELLS_X = 16;
  const CELLS_Y = 10;

  /**
   * The bar's band in one y-range of its travel, diced, in the column the
   * app really draws it in.
   *
   * `insetPx` is the distance from the surface's right edge to the
   * scroller's, which is never zero on a panel: the sidebar wraps its list
   * in `px-3` and the aside carries its own right border, so the bar runs a
   * dozen pixels inboard of the edge the panel lights. The cells therefore
   * run from `w - inset - SCROLLBAR_THIN_PX` to `w - inset` and do NOT
   * reach x = 1. Measuring at the edge instead reads a column with no
   * scrollbar in it, which is a number about the wrong pixels.
   */
  function barCells(
    surface: Box,
    band: { y0: number; y1: number },
    insetPx: number,
  ): Region[] {
    const outer = (surface.w - insetPx) / surface.w;
    const inner = (surface.w - insetPx - SCROLLBAR_THIN_PX) / surface.w;
    const cells: Region[] = [];
    for (let i = 0; i < CELLS_X; i += 1) {
      for (let j = 0; j < CELLS_Y; j += 1) {
        cells.push({
          x0: inner + ((outer - inner) * i) / CELLS_X,
          x1: inner + ((outer - inner) * (i + 1)) / CELLS_X,
          y0: band.y0 + ((band.y1 - band.y0) * j) / CELLS_Y,
          y1: band.y0 + ((band.y1 - band.y0) * (j + 1)) / CELLS_Y,
        });
      }
    }
    return cells;
  }

  /** A neutral grey that reads exactly `ratio`:1 against `ground`, lighter
   *  than it. Derived from the ground the run just measured rather than
   *  typed, so a control that demonstrates a breach goes on demonstrating
   *  it when the ground moves. */
  function greyAt(ratio: number, ground: string): string {
    const target = ratio * (luminance(parseRgbColour(ground)) + 0.05) - 0.05;
    if (target > 1) {
      throw new Error(`no grey reads ${ratio}:1 against ${ground}; it is too light`);
    }
    const srgb = target <= 0.0031308 ? target * 12.92 : 1.055 * target ** (1 / 2.4) - 0.055;
    const c = Math.round(srgb * 255);
    return `rgb(${c}, ${c}, ${c})`;
  }

  it("the bands tile the bar's travel", () => {
    /* The region the bar is measured over is the last input of the paint
     * model that nothing else pins, and it is the one the verdict turns on:
     * move the bands down a little and every other assertion in this file
     * stays green while the worst ground in the app - the panel's lit top
     * corner - simply stops being looked at. A reader is at its most
     * dangerous where dropping something makes it kinder, so the shape of
     * the region is asserted here rather than left to the constants'
     * comments. The x axis has a case of its own below, because it comes
     * from the rendered tree rather than from a constant. */
    expect(BANDS[0].y0, "the bands start below the top of the scroller").toBe(0);
    expect(BANDS[BANDS.length - 1].y1, "the bands stop above its foot").toBe(1);
    expect(
      BANDS.every((band, i) => i === 0 || band.y0 === BANDS[i - 1].y1),
      "the bands leave a gap in the bar's travel",
    ).toBe(true);
    // GROUND: they are real bands and not one degenerate line, so the
    // tiling above is not satisfied by an empty set.
    expect(BANDS.length).toBeGreaterThan(1);
    expect(BANDS.every((band) => band.y1 > band.y0)).toBe(true);
  });

  /** A cell of the bar, put into ONE layer's own coordinates. A full-bleed
   *  layer shares the surface's box; a right-edge band has its own narrower
   *  one, where the bar sits at the far end of the gradient rather than at
   *  the far end of the panel; a left-edge band the bar never crosses is
   *  simply not over it; and a layer clipped off the right edge is not over
   *  the part of the cell inside the clip at all. Null means this layer
   *  paints nothing in this cell. */
  function barRegionIn(
    layer: PaintLayer,
    surface: Box,
    cell: Region,
  ): { box: Box; region: Region } | null {
    const anchor = layer.anchor;
    if (anchor.edge === "left") {
      if (anchor.bandPx <= cell.x0 * surface.w) return null;
      throw new Error(
        `${layer.source} is a band down the LEFT edge wide enough to reach the bar; ` +
          "placing a bar inside one is a case this reader has never had to read",
      );
    }
    // The clip is applied in the SURFACE's pixels before the layer's box is
    // chosen, which is sound for both of the shapes left here: a full-bleed
    // box and a right-edge band both end at the surface's right edge, so
    // cutting n pixels off either cuts the same column of the surface. The
    // gradient itself is untouched - a clip moves no stop - so the layer is
    // still evaluated over its own geometry, just over less of it.
    let clipped = cell;
    if (layer.clipRightPx > 0) {
      const cut = (surface.w - layer.clipRightPx) / surface.w;
      if (cell.x0 >= cut) return null;
      clipped = { ...cell, x1: Math.min(cell.x1, cut) };
    }
    if (anchor.edge === "full") return { box: surface, region: clipped };
    const box = { w: anchor.bandPx, h: surface.h };
    const left = surface.w - anchor.bandPx;
    const x0 = (clipped.x0 * surface.w - left) / anchor.bandPx;
    const x1 = (clipped.x1 * surface.w - left) / anchor.bandPx;
    if (x1 <= 0) return null;
    return { box, region: { x0: Math.max(x0, 0), x1, y0: clipped.y0, y1: clipped.y1 } };
  }

  /** Paint with its own alpha: what a box has built up out of its layers
   *  before it ever meets the surface under it. */
  interface Paint {
    rgb: Rgb;
    a: number;
  }

  const NOTHING: Paint = { rgb: { r: 0, g: 0, b: 0 }, a: 0 };

  /** One stop laid over paint that is itself translucent. */
  function overPaint(stop: string, under: Paint): Paint {
    const src = parseRgbColour(stop);
    const sa = paintAlpha(stop);
    const a = sa + under.a * (1 - sa);
    if (a === 0) return NOTHING;
    const channel = (x: number, y: number) =>
      Math.round((x * sa + y * under.a * (1 - sa)) / a);
    return {
      rgb: {
        r: channel(src.r, under.rgb.r),
        g: channel(src.g, under.rgb.g),
        b: channel(src.b, under.rgb.b),
      },
      a,
    };
  }

  /** The layers of one painted BOX, kept together. `opacity` and
   *  `mix-blend-mode` belong to the element, not to each of its background
   *  layers: applying the sidebar's 0.9 once per layer, which an earlier
   *  version of this did, multiplies it five times over a pseudo-element
   *  that carries five backgrounds, and screen-blends each layer against the
   *  panel instead of blending the finished pseudo-element against it. */
  interface PaintGroup {
    source: string;
    opacity: number;
    screen: boolean;
    layers: PaintLayer[];
  }

  function groupLayers(layers: PaintLayer[]): PaintGroup[] {
    const out: PaintGroup[] = [];
    for (const layer of layers) {
      const last = out[out.length - 1];
      if (last && last.source === layer.source) {
        last.layers.push(layer);
        continue;
      }
      out.push({
        source: layer.source,
        opacity: layer.opacity,
        screen: layer.screen,
        layers: [layer],
      });
    }
    return out;
  }

  /** Every paint the group can show at the bar in this band, the empty one
   *  included when a layer of it is absent somewhere in the band. */
  function groupPaints(group: PaintGroup, surface: Box, cell: Region): Paint[] {
    let paints: Paint[] = [NOTHING];
    for (const layer of group.layers) {
      const placed = barRegionIn(layer, surface, cell);
      if (!placed) continue;
      const { stops, canBeAbsent } = layerPaintOver(layer.value, placed.box, placed.region);
      if (stops.length === 0) continue;
      const next = new Map<string, Paint>();
      for (const under of paints) {
        if (canBeAbsent) next.set(`${under.rgb.r},${under.rgb.g},${under.rgb.b},${under.a}`, under);
        for (const stop of stops) {
          const paint = overPaint(stop, under);
          next.set(`${paint.rgb.r},${paint.rgb.g},${paint.rgb.b},${paint.a}`, paint);
        }
      }
      if (next.size > 64) {
        throw new Error(
          `${group.source} takes ${next.size} distinct paints at the bar; a box with ` +
            "that many independent layers needs a reader that prunes, not this one",
        );
      }
      paints = [...next.values()];
    }
    return paints;
  }

  /**
   * Every ground the bar can be over in one band of its travel, as an
   * envelope of what the layers there can do. Each painted box is
   * composited among its own layers first, then flattened onto the ground
   * under it at its own opacity and blend mode; a layer that is absent
   * somewhere in the band contributes an "absent" branch, because what is
   * under it really does show through there. The set is pruned to its
   * lightest and darkest member after every box, and those two bound the
   * contrast a thumb of any lightness reaches in the band.
   */
  function groundEnvelope(
    layers: PaintLayer[],
    surface: Box,
    band: { y0: number; y1: number },
    insetPx: number,
    cells: Region[] = barCells(surface, band, insetPx),
  ): string[] {
    const found: Rgb[] = [];
    for (const cell of cells) {
      found.push(...cellEnvelope(layers, surface, cell).map(parseRgbColour));
    }
    return extremes(found).map((g) => `rgb(${g.r}, ${g.g}, ${g.b})`);
  }

  /** The same, over one cell, where the bound is tight. */
  function cellEnvelope(layers: PaintLayer[], surface: Box, cell: Region): string[] {
    let grounds: Rgb[] = [frameColour(css)];
    for (const group of groupLayers(layers)) {
      const next: Rgb[] = [];
      for (const under of grounds) {
        for (const paint of groupPaints(group, surface, cell)) {
          const alpha = paint.a * group.opacity;
          if (alpha <= 0) {
            next.push(under);
            continue;
          }
          const colour =
            `rgba(${paint.rgb.r}, ${paint.rgb.g}, ${paint.rgb.b}, ` +
            `${Number(alpha.toFixed(4))})`;
          next.push(group.screen ? screenOver(colour, under) : composite(colour, under));
        }
      }
      grounds = extremes(next);
    }
    return grounds.map((g) => `rgb(${g.r}, ${g.g}, ${g.b})`);
  }

  /** The lightest and the darkest of a set. Those two bound the contrast a
   *  thumb of any lightness can reach over the set, and keeping every
   *  combination instead would be exponential in the number of layers. */
  function extremes(colours: Rgb[]): Rgb[] {
    let light = colours[0];
    let dark = colours[0];
    for (const c of colours) {
      if (luminance(c) > luminance(light)) light = c;
      if (luminance(c) < luminance(dark)) dark = c;
    }
    return light === dark ? [light] : [light, dark];
  }

  /**
   * The thumb as the eye meets it. `scrollbar-color` is `<thumb> <track>`;
   * the track is laid on the scroller's surface and the thumb on the track,
   * so with a transparent track the two legs the plan names (thumb against
   * track, thumb against the surface behind it) are the same number, and
   * with an opaque track they differ - which is why both are asked and not
   * only the one the shipped sheet happens to make redundant.
   */
  function worstThumbRatio(thumb: string, track: string, grounds: string[]): number {
    let worst = Number.POSITIVE_INFINITY;
    for (const ground of grounds) {
      const surface = parseRgbColour(ground);
      const trackPaint = track === "transparent" ? surface : composite(track, surface);
      const thumbPaint = composite(thumb, trackPaint);
      worst = Math.min(
        worst,
        contrastRatio(thumbPaint, trackPaint),
        contrastRatio(thumbPaint, surface),
      );
    }
    return worst;
  }

  /* ---- the layers no document in this suite can hold ------------------
   *
   * Two of the layers under the sidebar's bar are not in the DOM a test
   * renders, and neither of them is optional in the app.
   *
   * `PanelMist` draws a WebGL canvas inside each side panel with a veil
   * over it. It is on by default and gated on a desktop-width media query,
   * and the case below proves both by asking the store and the query. What
   * it cannot do is keep the element: `FogGL` asks for a `webgl` context on
   * its first effect, jsdom hands back null, and the gate's failure rung
   * takes the whole mist back out again. A headless screenshot has no WebGL
   * either, so the canvas is the one layer nothing in this repo can read off
   * a render.
   *
   * So both are put in from the sheet and from the shader's own numbers,
   * with the ground control that the sheet still declares them where this
   * expects, and with a sensitivity case that drives the fog to white and
   * watches the guard fall under the floor - which is the proof that the
   * layers are in the stack rather than decoration in a comment.
   */

  /** The mist's veil, and where it sits. `.panel-mist` is `z-index: -1`
   *  inside a panel that owns a stacking context, and the panel's own
   *  pseudo-elements are `z-index: auto`, so the veil paints ABOVE the
   *  panel's background and BELOW its glow and its sheen. */
  const PANEL_MIST_LIFT = /^z-index$/;
  const BACKGROUND = /^background$/;

  /** `smoothstep` as GLSL defines it, which is what the fog's ramp and its
   *  lift are both written in. */
  function smoothstep(lo: number, hi: number, t: number): number {
    const x = Math.min(Math.max((t - lo) / (hi - lo), 0), 1);
    return x * x * (3 - 2 * x);
  }

  /** The shader's `ramp()`, evaluated from the table the module exports.
   *  Not a copy of the numbers: the colours and the thresholds are its own,
   *  and the case below proves they are the ones the GLSL it generates
   *  really carries. */
  function fogRamp(t: number): Rgb {
    const clamped = Math.min(Math.max(t, 0), 1);
    let c = [...FOG_RAMP_BASE] as number[];
    for (const step of FOG_RAMP_STEPS) {
      const f = smoothstep(step.lo, step.hi, clamped);
      c = c.map((channel, i) => channel + (step.colour[i] - channel) * f);
    }
    return { r: c[0] * 255, g: c[1] * 255, b: c[2] * 255 };
  }

  /** The lightest pixel one panel's fog can paint, swept from the shader's
   *  own constants over every value its three free inputs can take.
   *
   *  `d` is a diagonal tendency moved by the weather cell, `v` is the
   *  normalised fog density, and both the base colour and the billow lift
   *  are `ramp()` of those. All three inputs are `smoothstep`ed to [0, 1]
   *  before they get here, so a grid over the unit cube is the whole of
   *  their range and the maximum over it is the ceiling. */
  function fogCeiling(band: readonly [number, number], steps = 60): Rgb {
    const [lo, hi] = band;
    const span = hi - lo;
    let brightest: Rgb | null = null;
    for (let i = 0; i <= steps; i += 1) {
      const diag = i / steps;
      for (let j = 0; j <= steps; j += 1) {
        const w3 = j / steps;
        const d = lo + span * (0.5 + (diag - 0.5) * RAMP_COMPRESS + (w3 - 0.5) * WEATHER_AMOUNT);
        for (let k = 0; k <= steps; k += 1) {
          const v = k / steps;
          const base = fogRamp(d + (v - 0.5) * HUE_TRAVEL * span);
          const lifted = fogRamp(Math.min(d + LIFT_REACH * span, hi));
          const weight = smoothstep(LIFT_LO, LIFT_HI, v) * LIFT;
          const mixed: Rgb = {
            r: base.r + (lifted.r - base.r) * weight,
            g: base.g + (lifted.g - base.g) * weight,
            b: base.b + (lifted.b - base.b) * weight,
          };
          if (!brightest || luminance(mixed) > luminance(brightest)) brightest = mixed;
        }
      }
    }
    if (!brightest) throw new Error("the fog sweep produced nothing");
    return {
      r: Math.round(brightest.r),
      g: Math.round(brightest.g),
      b: Math.round(brightest.b),
    };
  }

  /** The lightest pixel the LEFT panel's fog can paint. `PanelMist` clamps
   *  each side to a sub-band of the shader's colour ramp, and the left one
   *  is the deep-blue end of it. Computed, not typed: it is a stand-in for a
   *  layer no reader here can see, and the canvas is opaque, so with the fog
   *  on it IS the ground and the panel's own gradient never reaches the eye. */
  const fogCeilingMemo = new Map<string, string>();
  const panelFogCeiling = (side: "left" | "right"): string => {
    const cached = fogCeilingMemo.get(side);
    if (cached) return cached;
    const c = fogCeiling(PANEL_BANDS[side]);
    const value = `rgb(${c.r}, ${c.g}, ${c.b})`;
    fogCeilingMemo.set(side, value);
    return value;
  };

  it("the fog ceiling is derived from the shader the app really builds", () => {
    /* The number the sidebar's whole reading used to rest on was a hand
     * sweep written into a comment, and nothing tied it to the shader. Now
     * the sweep runs here, off the module's own exported ramp and tuning
     * constants, so retuning the fog moves the guard's ground with it.
     *
     * The one thing an exported table cannot promise on its own is that the
     * GLSL says the same, since the shader is a string built next to it. So
     * the source the module really generates is asked, as a VALUE returned
     * by the function the component calls, for the numbers this evaluated. */
    const src = makeFragSrc(PANEL_BANDS.left[0], PANEL_BANDS.left[1]);
    const shows = (x: number) => (Number.isInteger(x) ? x.toFixed(1) : String(x));
    const rampThresholds = [...src.matchAll(/smoothstep\(([\d.]+), ([\d.]+), t\)/g)].map(
      (m) => [Number(m[1]), Number(m[2])],
    );
    expect(src).toContain(`vec3(${FOG_RAMP_BASE.map(shows).join(", ")})`);
    for (const step of FOG_RAMP_STEPS) {
      expect(
        src,
        `the shader does not carry the ramp step this table calls ${step.colour.join(", ")}`,
      ).toContain(`vec3(${step.colour.map(shows).join(", ")})`);
      // The thresholds are compared as NUMBERS, not as text: GLSL writes
      // 0.50 where a TypeScript literal is 0.5, and a formatting difference
      // is not a drift.
      expect(
        rampThresholds,
        `the shader does not carry the ramp threshold ${step.lo} to ${step.hi}`,
      ).toContainEqual([step.lo, step.hi]);
    }
    /* Each tuning constant, in the EXPRESSION the shader uses it in.
     *
     * Asking only whether the source contains the number is a question that
     * cannot fail: `makeFragSrc` interpolates the same constant into the
     * string being searched, so both sides move together, and the ramp's own
     * colour literals supply near-misses on top of that - `0.85` is inside
     * `vec3(0.784, 0.855, 0.925)`, so `toContain("0.85")` passes on a shader
     * that never uses LIFT_HI at all. The whole expression pins the constant
     * to its place instead: a GLSL edit that writes a different number where
     * a constant belongs, or moves a constant to another term, leaves the
     * expected text absent. The count is pinned too, so a second use
     * appearing somewhere the sweep does not model is red as well. */
    const SPAN = (PANEL_BANDS.left[1] - PANEL_BANDS.left[0]).toFixed(4);
    const HI = PANEL_BANDS.left[1].toFixed(4);
    const uses: Array<[string, string]> = [
      ["HUE_TRAVEL", `ramp(d + (v - 0.5) * ${HUE_TRAVEL} * ${SPAN})`],
      ["RAMP_COMPRESS", `(diag - 0.5) * ${RAMP_COMPRESS}`],
      ["WEATHER_AMOUNT", `(w3 - 0.5) * ${WEATHER_AMOUNT}`],
      ["LIFT_REACH", `ramp(min(d + ${LIFT_REACH} * ${SPAN}, ${HI}))`],
      ["LIFT_LO and LIFT_HI and LIFT", `smoothstep(${LIFT_LO}, ${LIFT_HI}, v) * ${LIFT}`],
    ];
    const occurrences = (text: string, needle: string): number =>
      text.split(needle).length - 1;
    for (const [name, expression] of uses) {
      expect(
        occurrences(src, expression),
        `the shader does not build "${expression}" exactly once, so ${name} is not ` +
          "used where the ceiling swept here assumes it is",
      ).toBe(1);
    }
    // GROUND: the reader can tell one of those expressions from a near miss,
    // which is the failure the old `toContain(<number>)` legs could not see.
    expect(occurrences(src, `smoothstep(${LIFT_LO}, 0.55, v) * ${LIFT}`)).toBe(0);
    // GROUND: the reader is not agreeing with everything. A colour the ramp
    // does not carry is not in the source.
    expect(src).not.toContain("vec3(0.5, 0.5, 0.5)");

    // GROUND: the sweep really explores its three inputs. With any one of
    // them left at its middle the answer is the band's own midpoint colour,
    // and the ceiling has to be strictly lighter than that.
    const [lo, hi] = PANEL_BANDS.left;
    const midpoint = fogRamp(lo + (hi - lo) * 0.5);
    expect(
      luminance(parseRgbColour(panelFogCeiling("left"))),
      "the fog ceiling is no lighter than the band's midpoint, so the sweep is not " +
        "reaching the bright end of its own band",
    ).toBeGreaterThan(luminance({
      r: Math.round(midpoint.r),
      g: Math.round(midpoint.g),
      b: Math.round(midpoint.b),
    }));

    // The sweep converges: a finer grid finds the same ceiling, so the grid
    // is not what the number is made of.
    const coarse = fogCeiling(PANEL_BANDS.left, 40);
    const fine = fogCeiling(PANEL_BANDS.left, 90);
    expect(fine).toEqual(coarse);
    // And the two bands really are different regions of the ramp, or one
    // panel's ceiling would be standing in for both.
    expect(panelFogCeiling("right")).not.toBe(panelFogCeiling("left"));
    expect(
      luminance(parseRgbColour(panelFogCeiling("right"))),
    ).toBeGreaterThan(luminance(parseRgbColour(panelFogCeiling("left"))));
  });

  /** A fog brighter than the shader can go, for the sensitivity control:
   *  the brightest thing any canvas under that veil could paint. */
  const IMPOSSIBLE_FOG = "rgb(255, 255, 255)";

  /** The mist layers for a panel, in paint order, ready to splice under its
   *  pseudo-elements. Throws rather than returning nothing if the sheet has
   *  moved, so a rename is a red test and not a quietly thinner stack. */
  function mistLayers(surfaceRule: string, veilSelector: string, fog: string): PaintLayer[] {
    const lift = declarationOnSelector(root, `${surfaceRule} > .panel-mist`, PANEL_MIST_LIFT);
    if (lift !== "-1") {
      throw new Error(
        `${surfaceRule} no longer lifts its panel mist to z-index -1 (got ${lift}), ` +
          "so where the veil paints in the stack is no longer what this measures",
      );
    }
    const veil = declarationOnSelector(root, veilSelector, BACKGROUND);
    if (!veil) {
      throw new Error(`${veilSelector} declares no background; the veil has moved or been renamed`);
    }
    const rem = remPx(root);
    const out: PaintLayer[] = [
      {
        value: fog,
        anchor: { edge: "full" },
        opacity: 1,
        screen: false,
        // The canvas is a real element with its own box; whatever clips the
        // panel's decoration does not reach it.
        clipRightPx: clipRightPx(root, ".panel-mist-gl", rem),
        source: `the fog canvas inside ${surfaceRule}`,
      },
    ];
    const veilClip = clipRightPx(root, veilSelector, rem);
    for (const value of backgroundLayers(veil, table)) {
      out.push({
        value,
        anchor: { edge: "full" },
        opacity: 1,
        screen: false,
        clipRightPx: veilClip,
        source: veilSelector,
      });
    }
    return out;
  }

  /** The surface's own layers with the mist put in where it paints: above
   *  the surface's background, below its pseudo-elements. */
  function withMist(painted: { surfaceRule: string; layers: PaintLayer[] }, mist: PaintLayer[]) {
    const own = painted.layers.filter((l) => l.source === painted.surfaceRule);
    const over = painted.layers.filter((l) => l.source !== painted.surfaceRule);
    return [...own, ...mist, ...over];
  }

  /** The boxes a surface is drawn in: the narrowest and the widest the app
   *  gives it, so no reading below rests on one guessed aspect. Only two
   *  things in the sheet depend on it at all - a corner gradient's angle
   *  and a non-axis-aligned one's projection - and every band is measured
   *  in both boxes with the worst kept. */
  const SIDEBAR_BOXES: Box[] = [
    // `--sidebar-width: clamp(264px, 27vw, 320px)`, read off the sheet
    // below, against the shortest and tallest windows the app is used in.
    { w: 264, h: 700 },
    { w: 320, h: 1440 },
  ];
  /** The right panel and the chat canvas share the page's remaining width;
   *  these bracket the two of them across the same window range. */
  const PANEL_BOXES: Box[] = [
    { w: 300, h: 700 },
    { w: 420, h: 1440 },
  ];
  const CANVAS_BOXES: Box[] = [
    { w: 520, h: 700 },
    { w: 1000, h: 1440 },
  ];
  /** A centred dialog and a popup anchored under its trigger. */
  const DIALOG_BOXES: Box[] = [
    { w: 420, h: 420 },
    { w: 640, h: 760 },
  ];
  const POPUP_BOXES: Box[] = [
    { w: 180, h: 200 },
    { w: 320, h: 420 },
  ];

  interface BandGround {
    band: string;
    box: string;
    grounds: string[];
  }

  /**
   * The thumb and track that reach `el` (declared on it or inherited from
   * the nearest ancestor that declares one) and the ground behind it per
   * band of the bar's travel (the nearest painted ancestor as painted, or
   * the app frame when the surface is a wallpaper the sheet cannot know).
   */
  function scrollbarPaint(
    el: Element,
    name: string,
    boxes: Box[],
    options: { ground?: "frame"; mist?: [string, string] } = {},
  ) {
    const colour = nearestDeclaration(root, el, COLOUR);
    if (!colour) throw new Error(`${name}: no scrollbar-color reaches the scroller or its ancestors`);
    // The two members are tokens in the sheet, so they are resolved the way
    // the browser resolves them, against the theme block, before anything is
    // measured. Nothing here types a colour.
    const [thumb, track, ...rest] = tokensOf(
      resolveVars(colour.declaration.value, table),
    );
    expect(rest, `${name}: scrollbar-color takes two colours`).toEqual([]);
    const colourRule = colour.declaration.selector;
    if (options.ground === "frame") {
      /* The wallpaper is a user image applied as an INLINE background on the
       * scroller itself (ChatCanvas hands `chatBg.style` to it), so no sheet
       * reader can see it and the ground has to be bounded rather than read.
       * Two grounds, and the second is the one that matters: the app frame,
       * the darkest thing the sheet declares and what a dark wallpaper tends
       * towards, and the LIGHTEST ground this class ever sits on, which is
       * the threshold its own gate flips at. `.chat-bg-dark` is added only
       * when the image's effective brightness is below
       * CHAT_BG_DARK_CHROME_BELOW on the non-linear Rec.709 scale the
       * production module uses, so a neutral grey at that threshold is the
       * bound. Asked with the frame alone the case passes at 18.5:1 on the
       * easiest ground in the app and says nothing.
       *
       * The bound is the GATE's, not this reader's, and it is a bound on the
       * image's average: a picture whose channels sit apart, or one that is
       * dark overall with a bright patch under the bar, passes the same gate
       * and paints lighter there. That is a limit of the GATE rather than
       * of this reader, and it is written here rather than papered over. */
      const f = frameColour(css);
      const admitted = Math.round(CHAT_BG_DARK_CHROME_BELOW * 255);
      return {
        thumb,
        track,
        colourRule,
        surfaceRule: "the wallpaper on the scroller itself",
        layers: [] as PaintLayer[],
        // The ground here is one flat colour over the whole surface, so
        // which column the bar runs in cannot change the answer; there is
        // no geometry to place.
        insetPx: 0,
        bands: [
          { band: "the whole bar", box: "the frame", grounds: [`rgb(${f.r}, ${f.g}, ${f.b})`] },
          {
            band: "the whole bar",
            box: "the gate's lightest wallpaper",
            grounds: [`rgb(${admitted}, ${admitted}, ${admitted})`],
          },
        ] as BandGround[],
      };
    }
    const painted = paintLayersBehind(root, el, table);
    if (!painted) throw new Error(`${name}: no background reaches the scroller or its ancestors`);
    /* Where the bar actually runs, walked on the tree the component
     * rendered: out from the scroller through every wrapper's padding and
     * margin to the surface whose paint is the ground. The scroller is not
     * the surface on any panel in this app, and reading the column off the
     * surface's own edge instead is how a reading gets taken somewhere the
     * bar never is. */
    const insetPx = insetRightPx(root, el, painted.surface, remPx(root), table);
    const layers = options.mist
      ? withMist(painted, mistLayers(painted.surfaceRule, options.mist[0], options.mist[1]))
      : painted.layers;
    // The fog is a user toggle and `prefers-reduced-transparency` removes it
    // outright, so fog-OFF is a shipped configuration and it is the one in
    // which the panel's own gradient is the ground rather than a layer the
    // opaque canvas hides. Both are measured and the worse is the answer;
    // with only the fog-on stack in the set, that gradient could be
    // repainted any colour with every assertion here green.
    const stacks: Array<[string, PaintLayer[]]> = options.mist
      ? [
          ["", layers],
          [" fog off", painted.layers],
        ]
      : [["", layers]];
    const bands: BandGround[] = [];
    for (const [tag, stack] of stacks) {
      for (const box of boxes) {
        for (const band of BANDS) {
          bands.push({
            band: `${band.name}${tag}`,
            box: `${box.w}x${box.h}`,
            grounds: groundEnvelope(stack, box, band, insetPx),
          });
        }
      }
    }
    const surfaceRule =
      layers.length > 1
        ? `${painted.surfaceRule} and ${layers.length - 1} layer(s) over it ` +
          `(${[...new Set(layers.map((l) => l.source))].join(", ")})`
        : painted.surfaceRule;
    expect(
      bands.filter((b) => b.grounds.length > 0),
      `${name}: the surface (${surfaceRule}) paints nothing this reader can measure`,
    ).toHaveLength(bands.length);
    return { thumb, track, colourRule, surfaceRule, layers, bands, insetPx };
  }

  /** The worst band, with the table that produced it.
   *
   *  Pruning a band's grounds to its lightest and darkest is only a valid
   *  bound while the thumb's own painted luminance lies OUTSIDE that range.
   *  Inside it, the ground that hurts most is an intermediate one and both
   *  ends can read comfortably: a mid grey thumb between a near-black and a
   *  near-white ground clears the floor against both and disappears against
   *  the middle. Nothing in this app is in that position - the thumbs are
   *  either lighter than every ground they run over or darker than every one
   *  - so the precondition is checked rather than assumed, and a thumb that
   *  ever lands inside the range stops the run instead of being measured
   *  wrongly. */
  function worstBand(
    thumb: string,
    track: string,
    bands: BandGround[],
  ): { worst: number; band: BandGround; table: string } {
    let worst = Number.POSITIVE_INFINITY;
    let at = bands[0];
    const rows: string[] = [];
    for (const band of bands) {
      const lit = band.grounds.map((g) => luminance(parseRgbColour(g)));
      const painted = band.grounds.map((g) =>
        luminance(composite(thumb, parseRgbColour(g))),
      );
      // At or outside: a thumb that lands exactly ON one end reads 1:1
      // there, which IS the worst the band can do, so the pair still bounds
      // it. Strictly between the two ends is the case that does not.
      const above = painted.every((t) => t >= Math.max(...lit));
      const below = painted.every((t) => t <= Math.min(...lit));
      if (!above && !below) {
        throw new Error(
          `the thumb ${thumb} paints inside the range of grounds in the ${band.band} ` +
            `(${band.grounds.join(" / ")}), so the lightest and the darkest do not ` +
            "bound its contrast and this envelope cannot answer for that band",
        );
      }
      const ratio = worstThumbRatio(thumb, track, band.grounds);
      rows.push(`${band.box} ${band.band}: ${band.grounds.join(" / ")} -> ${ratio.toFixed(2)}:1`);
      if (ratio < worst) {
        worst = ratio;
        at = band;
      }
    }
    return { worst, band: at, table: rows.join("; ") };
  }

  interface Surfaced {
    name: string;
    mount: Mount;
    boxes: Box[];
    ground?: "frame";
    /** The veil selector and the fog stand-in, for a surface the panel mist
     *  renders inside. */
    mist?: [string, string];
    /** The selector of a veil this reader does NOT model into the bands: the
     *  milk over the canvas mist, the right panel's own veil. Whatever the
     *  fog under it paints, the veil's alpha floors the result, so the two
     *  ends of "the veil over anything" bound the ground there. */
    unmodelledVeil?: string;
  }

  const SURFACED: Surfaced[] = [
    ...GROUP_B.map(([name, mount]) => ({
      name,
      mount,
      boxes: name === "SelectContent" ? POPUP_BOXES : DIALOG_BOXES,
    })),
    { name: "GenerationSettingsDialog", mount: generationSettingsDialog, boxes: DIALOG_BOXES },
    {
      name: "CharacterList in the sidebar",
      mount: characterListScroller,
      boxes: SIDEBAR_BOXES,
      mist: [".panel-mist-left .panel-mist-veil", panelFogCeiling("left")],
    },
    {
      name: "RightPanel notebook tab",
      mount: notebookTabScroller,
      boxes: PANEL_BOXES,
      unmodelledVeil: ".panel-mist-right .panel-mist-veil",
    },
    {
      name: "ChatCanvas on the light canvas",
      mount: chatScroller,
      boxes: CANVAS_BOXES,
      unmodelledVeil: ".canvas-mist-milk",
    },
    // The wallpaper image is not in the sheet; it is an inline background on
    // the scroller itself. Bounded by the threshold its own class gate flips
    // at, with the app frame beside it.
    {
      name: "ChatCanvas over a dark wallpaper",
      mount: darkChatScroller,
      boxes: CANVAS_BOXES,
      ground: "frame",
    },
  ];

  it("POSITIVE CONTROL: the ratio reader tells a visible thumb from an invisible one", async () => {
    const { track, bands } = scrollbarPaint(
      await characterCreateDialog(),
      "CharacterCreateDialog",
      DIALOG_BOXES,
    );
    const grounds = bands[0].grounds;
    // Solid white clears WCAG 1.4.11 on a dark dialog; a thumb painted in
    // the surface's own colour is the surface and reads about 1:1.
    expect(worstThumbRatio("#ffffff", track, grounds)).toBeGreaterThanOrEqual(AA_NON_TEXT);
    expect(worstThumbRatio(grounds[0], track, [grounds[0]])).toBeLessThan(AA_NON_TEXT);
  }, 15_000);

  it("POSITIVE CONTROL: the envelope keeps the darkest ground as well as the lightest", () => {
    // `extremes` is what makes the envelope a bound rather than a list, and
    // half of it is easy to lose: on the surfaces where the thumb is light
    // the lightest ground is the worst case, so a reader that kept only that
    // one would go on agreeing with every assertion in this file. It is the
    // DARK thumbs on the light surfaces whose worst case is the other end.
    const set = [
      { r: 247, g: 250, b: 253 },
      { r: 210, g: 218, b: 225 },
      { r: 168, g: 193, b: 222 },
    ];
    expect(extremes(set)).toEqual([set[0], set[2]]);
    // GROUND: it is not a function that returns its input, and a single
    // colour comes back once rather than twice.
    expect(extremes([set[1]])).toEqual([set[1]]);
    // And the harm, numerically: the light end of that set clears the floor
    // for the canvas thumb and the dark end does not, so dropping the dark
    // end is the difference between a guard and a decoration.
    // Read off the sheet, not typed: this control demonstrates the harm
    // with the value the app really ships, so a retune of the token moves
    // the demonstration with it instead of leaving it arguing about a
    // colour nothing paints any more.
    const thumb = resolveVars(tokens.colors.scrollbarOnLight, table);
    expect(worstThumbRatio(thumb, "transparent", ["rgb(247, 250, 253)"])).toBeGreaterThan(
      AA_NON_TEXT,
    );
    expect(worstThumbRatio(thumb, "transparent", ["rgb(126, 150, 178)"])).toBeLessThan(
      AA_NON_TEXT,
    );
    // And the precondition that makes two ends a BOUND is checked, not
    // assumed: a thumb that paints between the two ends is not bounded by
    // them, and the reader says so rather than reporting the better number.
    const bands: BandGround[] = [
      { band: "a made-up band", box: "n/a", grounds: ["rgb(0, 0, 0)", "rgb(255, 255, 255)"] },
    ];
    expect(() => worstBand("rgb(150, 150, 150)", "transparent", bands)).toThrow(
      /paints inside the range of grounds/,
    );
    // GROUND: the same call with a thumb outside the range answers normally,
    // and answers with the worse of the two ends. White on this pair reads
    // 1:1 against the white end and 21:1 against the black one.
    expect(worstBand("rgb(255, 255, 255)", "transparent", bands).worst).toBeCloseTo(1, 5);
    expect(
      worstBand("rgb(255, 255, 255)", "transparent", [
        { ...bands[0], grounds: ["rgb(0, 0, 0)", "rgb(90, 90, 90)"] },
      ]).worst,
    ).toBeGreaterThan(AA_NON_TEXT);
  });

  it("POSITIVE CONTROL: the ratio reader measures the thumb against the SURFACE, not only the rail", async () => {
    // The shipped rail is transparent, so the two legs of `worstThumbRatio`
    // give the same number and dropping either one changes nothing. They
    // are not the same question: an opaque rail can be light enough to
    // leave the thumb clearing 3:1 against IT while the pair of them paint
    // a stripe over the surface they run down. Here the rail is opaque and
    // the two legs disagree, so the surface leg has to be the answer.
    const ground = "rgb(255, 255, 255)";
    const thumb = "rgb(230, 230, 230)";
    const rail = "rgb(20, 20, 20)";
    expect(contrastRatio(parseRgbColour(thumb), parseRgbColour(rail))).toBeGreaterThan(AA_NON_TEXT);
    expect(worstThumbRatio(thumb, rail, [ground])).toBeLessThan(AA_NON_TEXT);
  });

  /*
   * The ground here is the surface AS PAINTED, per band of the bar's own
   * travel: every background layer of the surface, the mist canvas and its
   * veil where a panel has one, its `::before` at that pseudo-element's own
   * opacity and its `::after` with `mix-blend-mode: screen` honoured, each
   * placed in the box the sheet gives it and evaluated over the band. That
   * matters and is not a formality - `.glass-dark` lights its own right
   * edge, where the bar is, and reading the base gradient alone put the
   * sidebar at 3.60:1 while the pixels were at 2.2:1. The assertion below is
   * the reason the dark-surface thumb leaves the ink family.
   */
  it.each(SURFACED)(
    "$name paints its thumb at 3:1 or better against its surface as painted",
    async ({ name, mount, boxes, ground, mist, unmodelledVeil }) => {
      const { thumb, track, bands, surfaceRule, colourRule, insetPx } = scrollbarPaint(
        await mount(),
        name,
        boxes,
        { ground, mist },
      );
      const { worst, band, table: rows } = worstBand(thumb, track, bands);
      if (mist) {
        // The fog-off stack has to be a DIFFERENT stack, or the second set of
        // bands is the first one measured twice and the configuration where
        // the panel's own gradient is the ground is still unmeasured.
        const named = (tag: string) =>
          bands.filter((x) => x.band.endsWith(tag)).map((x) => x.grounds.join(" / "));
        const on = named("corner");
        const off = named("corner fog off");
        expect(off, `${name}: no fog-off band was measured`).not.toEqual([]);
        expect(
          off,
          `${name}: the fog-off bands read the same ground as the fog-on ones, so the ` +
            "panel's own gradient is not in the measurement after all",
        ).not.toEqual(on);
      }
      if (ground === "frame") {
        // The easy ground must not be the one the verdict rests on: a dark
        // wallpaper is bounded by the gate that put the class there, not by
        // the app frame it merely tends towards.
        expect(
          band.box,
          `${name}: the worst reading came from ${band.box}, so the bound the gate ` +
            "admits is not what this case is measuring",
        ).toBe("the gate's lightest wallpaper");
      }
      expect(
        worst,
        `${name}: thumb ${thumb} (from ${colourRule}) on track ${track} over ${surfaceRule} ` +
          `reads ${worst.toFixed(2)}:1 at its worst, in the ${band.band} of a ${band.box} ` +
          `surface over (${band.grounds.join(" / ")}); the floor is ${AA_NON_TEXT}:1. ` +
          `The bar's column is ${SCROLLBAR_THIN_PX}px wide, ${insetPx}px inboard of the ` +
          `surface's right edge. Per band: ${rows}`,
      ).toBeGreaterThanOrEqual(AA_NON_TEXT);

      if (unmodelledVeil) {
        /* The right panel and the chat canvas each render a fog canvas of
         * their own, and no reader here can see a WebGL surface. A white
         * bound is NOT enough for them: the shader's hue travel is not
         * clamped to an instance's band, so the fog under the right panel
         * reaches its dark end and the mist DARKENS that surface rather
         * than only lightening it. The bound that is actually true is the
         * veil's: whatever the canvas paints, it paints UNDER a veil whose
         * own alpha floors the result, so "this veil over black" and "this
         * veil over white" bracket every fog the shader can produce, and a
         * thumb that clears at both ends clears everywhere between.
         *
         * Read from the sheet, stop by stop, so a veil that is retuned
         * thinner is measured thinner instead of being trusted. */
        const veil = declarationOnSelector(root, unmodelledVeil, BACKGROUND);
        if (!veil) {
          throw new Error(`${name}: ${unmodelledVeil} declares no background to bound with`);
        }
        const ends = ["rgb(0, 0, 0)", "rgb(255, 255, 255)"];
        const bounds: string[] = [];
        for (const value of backgroundLayers(veil, table)) {
          for (const stop of parseLayer(value).stops) {
            for (const end of ends) {
              const under = parseRgbColour(end);
              const over = composite(stop.colour, under);
              bounds.push(`rgb(${over.r}, ${over.g}, ${over.b})`);
            }
          }
        }
        expect(
          bounds.length,
          `${name}: ${unmodelledVeil} yielded no stops to bound the fog with`,
        ).toBeGreaterThan(0);
        const worstUnder = worstThumbRatio(thumb, track, bounds);
        expect(
          worstUnder,
          `${name}: thumb ${thumb} reads ${worstUnder.toFixed(2)}:1 under ${unmodelledVeil} ` +
            `over the extremes anything below it could paint (${bounds.join(" / ")})`,
        ).toBeGreaterThanOrEqual(AA_NON_TEXT);
        // GROUND: the bound is not vacuous. The same veil is opaque enough
        // to hide the ends it is laid over, so the two brackets differ -
        // a veil at alpha 1 would collapse them and prove nothing.
        expect(
          new Set(bounds).size,
          `${name}: ${unmodelledVeil} hides its ground completely, so the bracket is one colour`,
        ).toBeGreaterThan(1);
        // And it BRACKETS, which is the whole reason it replaced a white
        // bound: the same veil over a mid grey has to fall strictly inside
        // it on both sides. A one-sided bound would pass the ratio leg above
        // while saying nothing about the direction the shader really goes.
        const middles = backgroundLayers(veil, table).flatMap((value) =>
          parseLayer(value).stops.map((stop) => {
            const over = composite(stop.colour, { r: 128, g: 128, b: 128 });
            return luminance(over);
          }),
        );
        const lit = bounds.map((g) => luminance(parseRgbColour(g)));
        expect(
          Math.min(...lit),
          `${name}: the bracket for ${unmodelledVeil} has no dark side, so it bounds ` +
            "only the direction someone assumed the fog goes",
        ).toBeLessThan(Math.min(...middles));
        expect(
          Math.max(...lit),
          `${name}: the bracket for ${unmodelledVeil} has no light side`,
        ).toBeGreaterThan(Math.max(...middles));
      }

      // The second member of `scrollbar-color` is the RAIL the thumb runs
      // in, and it is a full-height band down the edge of the surface. The
      // contrast leg above cannot speak for it: an opaque rail can be light
      // enough to leave the thumb clearing 3:1 and still paint a stripe over
      // the surface. So the rail must contribute no paint of its own, asked
      // through the same reader that decides what a background paints rather
      // than by naming a keyword here.
      expect(
        paintedStops(track, table),
        `${name}: the track "${track}" (from ${colourRule}) paints, so it draws a ` +
          `rail down the edge of ${surfaceRule}`,
      ).toEqual([]);
    },
    20_000,
  );

  it("the veil scan reads a full-bleed painted child of a surface as a layer", async () => {
    /* `paintLayersBehind` looks for the shape the panel mist uses - an
     * absolutely positioned, full-bleed, painted child of the surface - and
     * in jsdom the app never renders one, because the fog gate takes the
     * mist back out on its WebGL rung (the case below proves that). So the
     * scan has no live subject here, and an edge with no subject is an edge
     * that can be deleted with every other assertion still green.
     *
     * The subject is planted instead: a child with the veil's own classes,
     * inside the sidebar the shell really rendered, so the sheet, the
     * surface and the cascade are all the app's and only the element is
     * mine. What is asserted is that the scan finds it and reads its
     * background, and that the same child fails the scan when it is not
     * full-bleed - the distinction the scan exists to make. */
    const scroller = await characterListScroller();
    const panel = scroller.closest(".glass-dark");
    if (!panel) throw new Error("the sidebar scroller is not inside .glass-dark");
    const before = paintLayersBehind(root, scroller, table);
    if (!before) throw new Error("nothing paints behind the sidebar scroller");
    expect(
      before.layers.filter((l) => l.source.includes("panel-mist-veil")),
      "a veil is already in the layer list, so this case is not about a planted one",
    ).toEqual([]);

    const mist = document.createElement("div");
    mist.className = "panel-mist panel-mist-left";
    const veil = document.createElement("div");
    veil.className = "panel-mist-veil";
    mist.appendChild(veil);
    panel.appendChild(mist);
    try {
      const found = paintLayersBehind(root, scroller, table);
      if (!found) throw new Error("nothing paints behind the sidebar scroller");
      const veilLayers = found.layers.filter((l) => l.source.includes("panel-mist-veil"));
      expect(
        veilLayers,
        "the scan did not read the veil the sidebar really draws over its background",
      ).not.toEqual([]);
      // It is the veil's own paint, not a placeholder, and it sits ABOVE the
      // surface and BELOW the pseudo-elements, which is where z-index -1 in a
      // panel that owns a stacking context puts it.
      const declared = declarationOnSelector(root, ".panel-mist-left .panel-mist-veil", BACKGROUND);
      expect(veilLayers.map((l) => l.value)).toEqual(backgroundLayers(declared ?? "", table));
      const sources = found.layers.map((l) => l.source);
      expect(sources.indexOf(found.surfaceRule)).toBeLessThan(
        sources.findIndex((x) => x.includes("panel-mist-veil")),
      );
      expect(sources.findIndex((x) => x.includes("panel-mist-veil"))).toBeLessThan(
        sources.findIndex((x) => x.endsWith("::after")),
      );

      // GROUND: the scan is about the SHAPE, not about the class name. A
      // painted child the sheet does not place full-bleed is not a veil, and
      // reading one as a veil would lift the ground behind every bar.
      veil.className = "sidebar-item";
      const after = paintLayersBehind(root, scroller, table);
      expect(
        after?.layers.filter((l) => l.source.includes("panel-mist-veil")),
        "a child that is not full-bleed is still being read as a veil",
      ).toEqual([]);
    } finally {
      mist.remove();
    }
  }, 25_000);

  it("a layer whose ramp dies over the bar lets the layer under it show through", () => {
    /* The absence branch, where it is consumed rather than where it is
     * computed. `layerPaintOver` says a layer can be absent somewhere in a
     * region; `groupPaints` has to answer with the bare ground as WELL as
     * the painted one when it does, because the bar really does run over
     * both. A reader that always took the painted branch would report the
     * lighter ground everywhere and never notice.
     *
     * Hand-built so the arithmetic is checkable: an opaque base, and over it
     * a gradient that is solid at the right edge and fully transparent by
     * 40% of the width. The cell spans the whole width, so both happen in
     * it. */
    const box: Box = { w: 100, h: 100 };
    const cell: Region = { x0: 0, x1: 1, y0: 0, y1: 1 };
    const layer = (value: string, source: string): PaintLayer => ({
      value,
      anchor: { edge: "full" },
      opacity: 1,
      screen: false,
      clipRightPx: 0,
      source,
    });
    const dies = [
      layer("rgb(0, 0, 0)", "the base"),
      layer("linear-gradient(90deg, rgba(255, 255, 255, 0) 40%, rgb(255, 255, 255) 100%)", "the cover"),
    ];
    const grounds = cellEnvelope(dies, box, cell);
    expect(
      grounds,
      "the bare base is not among the grounds, so the absent branch is never taken",
    ).toContain("rgb(0, 0, 0)");
    expect(grounds).toContain("rgb(255, 255, 255)");

    // GROUND: a cover that never reaches zero alpha covers the whole cell,
    // and then the bare base is NOT a ground the bar is ever over. Without
    // this leg a reader that always offered the absent branch would pass the
    // assertion above and measure every bar against a ground nothing paints.
    const covers = [
      dies[0],
      layer("linear-gradient(90deg, rgb(120, 120, 120) 0%, rgb(255, 255, 255) 100%)", "the cover"),
    ];
    expect(
      cellEnvelope(covers, box, cell),
      "an opaque cover still lets the base through, so absence is being assumed",
    ).not.toContain("rgb(0, 0, 0)");
  });
  it("the fog gate opens by default, and jsdom still cannot hold the layer it opens", async () => {
    // GROUND for modelling the mist from the sheet instead of from a render.
    // The gate is `on && !reduced && desktop && !failed`: the store says on,
    // the desktop query can be answered yes, and the mist STILL leaves no
    // element behind, because the WebGL context jsdom hands back is null and
    // the failure rung closes the gate again.
    expect(
      useUiStore.getState().ambientFogOn,
      "the ambient fog is off by default, so the veil is not part of the shipped look",
    ).toBe(true);
    const context = vi.spyOn(HTMLCanvasElement.prototype, "getContext");
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: /min-width:\s*901px/.test(query),
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    );
    const { container } = render(h(PanelMist, { side: "left" }));
    expect(window.matchMedia("(min-width: 901px)").matches).toBe(true);
    expect(
      context.mock.results.map((r) => r.value),
      "jsdom answered the webgl request with something, so the mist could be rendered after all",
    ).toEqual([null]);
    expect(
      container.querySelector(".panel-mist-veil"),
      "the veil IS in the document, so it can be measured from a render and this " +
        "case is the one to delete",
    ).toBeNull();
  });

  it("the sidebar's ground has the veil and the fog in it, and both move the number", async () => {
    // GROUND CONTROL for the two layers no render can hold: they are in the
    // layer list by name, in the right order, and taking them out or driving
    // them brighter moves the measured ratio. A stack that named them and
    // measured without them is exactly what this catches.
    const scroller = await characterListScroller();
    const painted = paintLayersBehind(root, scroller, table);
    if (!painted) throw new Error("nothing paints behind the sidebar scroller");
    const withFog = withMist(
      painted,
      mistLayers(painted.surfaceRule, ".panel-mist-left .panel-mist-veil", panelFogCeiling("left")),
    );
    const sources = withFog.map((l) => l.source);
    expect(
      sources.filter((s) => s.includes("panel-mist-veil")),
      `the veil is not in the layer list (${[...new Set(sources)].join(", ")})`,
    ).not.toEqual([]);
    expect(
      sources.filter((s) => s.startsWith("the fog canvas")),
      `the fog canvas is not in the layer list (${[...new Set(sources)].join(", ")})`,
    ).not.toEqual([]);
    // Order: the panel's own background, then the fog, then the veil, then
    // the pseudo-elements. The mist is `z-index: -1` in a panel that owns a
    // stacking context and the pseudo-elements are `z-index: auto`.
    const fogAt = sources.findIndex((s) => s.startsWith("the fog canvas"));
    const veilAt = sources.findIndex((s) => s.includes("panel-mist-veil"));
    const sheenAt = sources.findIndex((s) => s.endsWith("::after"));
    expect(sources.indexOf(painted.surfaceRule)).toBeLessThan(fogAt);
    expect(fogAt).toBeLessThan(veilAt);
    expect(veilAt).toBeLessThan(sheenAt);

    // SENSITIVITY. The fog is opaque, so it IS the ground where it paints:
    // drive it brighter than the shader can go and the guard has to fall
    // under the floor. A model that named the fog and measured without it
    // reports the same number for both.
    const thumb = tokensOf(
      resolveVars(nearestDeclaration(root, scroller, COLOUR)!.declaration.value, table),
    )[0];
    const litCorner = BANDS[0];
    const inset = insetRightPx(root, scroller, painted.surface, remPx(root), table);
    const shipped = worstThumbRatio(
      thumb,
      "transparent",
      groundEnvelope(withFog, SIDEBAR_BOXES[1], litCorner, inset),
    );
    const impossible = worstThumbRatio(
      thumb,
      "transparent",
      groundEnvelope(
        withMist(
          painted,
          mistLayers(painted.surfaceRule, ".panel-mist-left .panel-mist-veil", IMPOSSIBLE_FOG),
        ),
        SIDEBAR_BOXES[1],
        litCorner,
        inset,
      ),
    );
    // The fog is the layer that COSTS: it is opaque and lighter than the
    // panel's own gradient, so it is the ground wherever it paints and the
    // guard has to be strictly harsher with it than without it. Without this
    // leg, modelling the fog as painting nothing would leave every
    // assertion in the file green - a reader is at its most dangerous when
    // dropping a layer makes it kinder.
    const withoutFog = worstThumbRatio(
      thumb,
      "transparent",
      groundEnvelope(
        withMist(
          painted,
          mistLayers(painted.surfaceRule, ".panel-mist-left .panel-mist-veil", "rgba(0, 0, 0, 0)"),
        ),
        SIDEBAR_BOXES[1],
        litCorner,
        inset,
      ),
    );
    expect(
      shipped,
      `the lit corner reads ${shipped.toFixed(2)}:1 with the fog and ` +
        `${withoutFog.toFixed(2)}:1 without it, so the fog is not reaching the measurement`,
    ).toBeLessThan(withoutFog);
    expect(
      shipped,
      "the shipped fog does not clear the floor at the lit corner",
    ).toBeGreaterThanOrEqual(AA_NON_TEXT);
    expect(
      impossible,
      `a white fog reads ${impossible.toFixed(2)}:1, the same side of the floor as the ` +
        "shipped one, so the fog layer is not reaching the measurement",
    ).toBeLessThan(AA_NON_TEXT);
  }, 25_000);

  it("the sidebar's bar never starts at the panel's top pixel", async () => {
    // The lit corner band is measured from y = 0, which is a BOUND rather
    // than a place the bar reaches: the sidebar puts a header, a persona
    // strip, a search field and two separators above its first scroller, so
    // the bar starts below them and over a darker part of the gradient. The
    // assertion above is therefore stricter than the app, and this is the
    // fact that makes it so - asked of the render, not assumed.
    const scroller = await characterListScroller();
    const panel = scroller.closest(".glass-dark");
    expect(panel, "the sidebar list is not inside the dark panel any more").not.toBeNull();
    let above = 0;
    for (let node: Element | null = scroller; node && node !== panel; node = node.parentElement) {
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        above += 1;
      }
    }
    expect(
      above,
      "the sidebar's scroller is the first thing in the panel, so its bar does start " +
        "at the top pixel and the lit corner is reachable after all",
    ).toBeGreaterThan(0);
  }, 20_000);

  it("the ground reader sees the decoration, not just the base gradient", async () => {
    // The pin for the surface model itself. A reader that takes only the
    // bottom background layer reported the sidebar at 3.60:1 while the
    // shipped pixels were at 2.2:1, because `.glass-dark` lights its own
    // right edge - where the bar is - with a `::before` glow and a 46px
    // `::after` sheen in `mix-blend-mode: screen`. Both the thumb choice and
    // the assertion above rest on those layers being visible here, and a
    // thumb that clears the floor either way would not notice them going
    // missing. So they are asserted directly.
    const scroller = await characterListScroller();
    const painted = paintLayersBehind(root, scroller, table);
    if (!painted) throw new Error("nothing paints behind the sidebar scroller");

    const sources = [...new Set(painted.layers.map((l) => l.source))];
    expect(
      sources.filter((s) => s.endsWith("::before")),
      `the reader found no ::before layer behind the sidebar bar (${sources.join(", ")})`,
    ).not.toEqual([]);
    expect(
      sources.filter((s) => s.endsWith("::after")),
      `the reader found no ::after layer behind the sidebar bar (${sources.join(", ")})`,
    ).not.toEqual([]);
    expect(
      painted.layers.some((l) => l.screen),
      "no layer behind the sidebar bar is screen-blended, so the sheen is being read as ordinary paint",
    ).toBe(true);
    // And the sheen is placed where the sheet puts it: a band down the right
    // edge, not the whole panel. Read as full-bleed it would lift the ground
    // everywhere instead of only under the bar.
    expect(
      painted.layers.filter((l) => l.screen).map((l) => l.anchor.edge),
      "the screen-blended sheen is being placed across the whole panel",
    ).toEqual(["right"]);

    // And the layers CHANGE the answer, in the bar's own column: the lit
    // corner has to come out lighter there than anything the base gradient
    // alone can produce, or the reader is walking them and then throwing
    // them away.
    const box = SIDEBAR_BOXES[1];
    const litCorner = BANDS[0];
    const inset = insetRightPx(root, scroller, painted.surface, remPx(root), table);
    const base = painted.layers.filter((l) => l.source === painted.surfaceRule);
    const lightestBase = Math.max(
      ...groundEnvelope(base, box, litCorner, inset).map((g) => luminance(parseRgbColour(g))),
    );
    const lightestAll = Math.max(
      ...groundEnvelope(painted.layers, box, litCorner, inset).map((g) =>
        luminance(parseRgbColour(g)),
      ),
    );
    expect(
      lightestAll,
      "the decorated ground is no lighter than the base gradient, so the " +
        "overlay layers are not reaching the measurement",
    ).toBeGreaterThan(lightestBase);

    // And the geometry is honest about the layers that CANNOT be over the
    // bar: the `::before` carries three radial highlights, and two of them
    // are centred far enough left that their ramps die before the bar's own
    // band. A reader that stacked all three at their peaks would report a
    // lighter corner than the panel can paint.
    const before = painted.layers.filter((l) => l.source.endsWith("::before"));
    const cells = barCells(box, litCorner, inset);
    const reaching = before.filter((layer) =>
      cells.some((cell) => {
        const placed = barRegionIn(layer, box, cell);
        return (
          placed !== null &&
          layerPaintOver(layer.value, placed.box, placed.region).stops.length > 0
        );
      }),
    );
    expect(
      reaching.length,
      "no layer of the sidebar's ::before reaches the bar's column, so the " +
        "comparison above is between two empty stacks",
    ).toBeGreaterThan(0);
    expect(
      before.length,
      "the sidebar's ::before no longer carries several background layers",
    ).toBeGreaterThan(reaching.length);
  }, 25_000);

  it("the sidebar's glow and sheen are not cut out of any column", async () => {
    /* Nothing in the app is cut to make room for a scrollbar.
     *
     * Clipping `.glass-dark`'s glow and its 46px sheen off the panel's
     * right edge buys no contrast under the bar. The two columns do not
     * touch: the bar runs a dozen pixels inboard of the edge such a clip
     * cuts, so the cut removes a visible stripe of the panel's boundary and
     * moves the contrast under the bar by nothing. The decoration paints its
     * whole box, and this case is what keeps it that way: no clip, no mask,
     * in either spelling.
     *
     * The contrast under the bar is carried by the column the bands are
     * measured in instead, which is the case below this one. */
    const rem = remPx(root);
    for (const selector of [".glass-dark::before", ".glass-dark::after"]) {
      expect(
        clipsAndMasksOn(root, selector),
        `${selector} has something taking a bite out of it; the panel's decoration ` +
          "is meant to paint its whole box",
      ).toEqual([]);
      expect(clipRightPx(root, selector, rem)).toBe(0);
    }

    // GROUND: the reader is not blind, in either spelling. A probe sheet
    // that clips and masks the same two selectors is seen doing it, and the
    // clip's width is read back off it.
    const cutProbe = parseSheet(
      ".glass-dark::before,\n.glass-dark::after { clip-path: inset(0 0.55rem 0 0); }\n" +
        ".glass-dark::after { -webkit-mask-image: linear-gradient(90deg, #000, transparent); }\n",
    );
    expect(clipsAndMasksOn(cutProbe, ".glass-dark::before")).toEqual([
      "clip-path: inset(0 0.55rem 0 0)",
    ]);
    expect(clipsAndMasksOn(cutProbe, ".glass-dark::after")).toHaveLength(2);
    expect(clipRightPx(cutProbe, ".glass-dark::before", rem)).toBe(0.55 * rem);
    // GROUND: and it does not call the initial value a cut, or every box in
    // the sheet that writes `clip-path: none` would fail this case.
    const noneProbe = parseSheet(".glass-dark::before { clip-path: none; }\n");
    expect(clipsAndMasksOn(noneProbe, ".glass-dark::before")).toEqual([]);

    // And on the rendered tree: every layer the ground model walks is
    // whole, and there are decoration layers among them to be whole.
    const scroller = await characterListScroller();
    const painted = paintLayersBehind(root, scroller, table);
    if (!painted) throw new Error("nothing paints behind the sidebar scroller");
    const decoration = painted.layers.filter(
      (l) => l.source.endsWith("::before") || l.source.endsWith("::after"),
    );
    expect(
      decoration.length,
      "the sidebar has no ::before or ::after layers, so there is nothing to be uncut",
    ).toBeGreaterThan(0);
    expect(
      painted.layers.filter((l) => l.clipRightPx !== 0).map((l) => l.source),
      "a layer behind the sidebar's bar is clipped",
    ).toEqual([]);

    // And the decoration really does paint IN the bar's column, which is
    // what a clip at the panel's edge takes away. Both pseudo-elements
    // named, so losing either is red.
    const inset = insetRightPx(root, scroller, painted.surface, rem, table);
    for (const box of SIDEBAR_BOXES) {
      for (const suffix of ["::before", "::after"]) {
        const paints = barCells(box, BANDS[0], inset).some((cell) =>
          decoration
            .filter((l) => l.source.endsWith(suffix))
            .some((layer) => {
              const placed = barRegionIn(layer, box, cell);
              return (
                placed !== null &&
                layerPaintOver(layer.value, placed.box, placed.region).stops.some(
                  (stop) => paintAlpha(stop) > 0,
                )
              );
            }),
        );
        expect(
          paints,
          `the sidebar's ${suffix} paints nothing in the bar's own column of a ` +
            `${box.w}x${box.h} sidebar, so the ratio below is measured over a ` +
            "stack the panel does not have there",
        ).toBe(true);
      }
    }
  }, 25_000);

  it("the bar's column is read off the scroller's own box, not off the panel's edge", async () => {
    /* The x axis of the measurement, and the finding that rewrote it.
     *
     * Every band in this file used to be taken at the surface's right edge,
     * on the assumption that a scroller reaches it. The sidebar's does not:
     * `CharacterList` and `ChatList` each wrap their scroller in `px-3`, so
     * the bar runs twelve pixels inboard of the panel's lit edge, over a
     * darker part of the same gradients. The column is therefore walked out
     * of the rendered tree - padding, margins and borders, all the way to
     * the surface whose paint is the ground - and the bands follow it. */
    const rem = remPx(root);
    const scroller = await characterListScroller();
    const painted = paintLayersBehind(root, scroller, table);
    if (!painted) throw new Error("nothing paints behind the sidebar scroller");
    const inset = insetRightPx(root, scroller, painted.surface, rem, table);
    expect(
      inset,
      "the sidebar's scroller is reported flush with the panel, so the wrappers' " +
        "padding is not reaching the reader",
    ).toBeGreaterThan(0);

    // The cells span exactly the bar's own column, and they do NOT reach
    // the surface's edge, which is where a band sized from the panel's width
    // instead of the scroller's box would put them.
    const box = SIDEBAR_BOXES[0];
    const cells = barCells(box, BANDS[0], inset);
    expect(Math.max(...cells.map((c) => c.x1))).toBeCloseTo((box.w - inset) / box.w, 10);
    expect(Math.min(...cells.map((c) => c.x0))).toBeCloseTo(
      (box.w - inset - SCROLLBAR_THIN_PX) / box.w,
      10,
    );
    expect(
      Math.max(...cells.map((c) => c.x1)),
      "the band still reaches the panel's right edge, where no scrollbar runs",
    ).toBeLessThan(1);

    /* And the band's WIDTH is the drawn bar's, not the sheet's.
     * `::-webkit-scrollbar { width }` is inert on every scroller here -
     * Chromium drops the whole family for an element whose `scrollbar-width`
     * is not `auto` - and it is narrower than the bar the engine draws, so a
     * band sized from it leaves the inboard strip of every bar unmeasured.
     * On a panel that strip is the LIT one, which is the direction that
     * quietly reports a better number. */
    const declaredBar = scrollbarWidthPx(root, rem);
    expect(
      SCROLLBAR_THIN_PX,
      `the band is ${SCROLLBAR_THIN_PX}px and the inert ::-webkit-scrollbar rule ` +
        `declares ${declaredBar}px; sizing the band from that rule would leave the ` +
        `inboard ${(SCROLLBAR_THIN_PX - declaredBar).toFixed(2)}px of every bar unmeasured`,
    ).toBeGreaterThan(declaredBar);

    /* SENSITIVITY, on a probe tree: the band is a function of the padding,
     * not a number this file chose. The same three classes the sidebar uses,
     * planted under a `.glass-dark` host, and the reader follows each one. */
    const host = document.createElement("div");
    host.className = "glass-dark";
    host.innerHTML = '<div class="px-3"><div class="overflow-y-auto"></div></div>';
    document.body.appendChild(host);
    try {
      const wrapper = host.firstElementChild as HTMLElement;
      const probe = wrapper.firstElementChild as HTMLElement;
      const atPx3 = insetRightPx(root, probe, host, rem, table);
      expect(atPx3, "the probe tree's px-3 wrapper is not being read").toBe(inset);
      /* The doubled step is written as an inline style, not as a second
       * Tailwind class. Tailwind scans this file as raw text, so a class
       * name spelled here is emitted into the SHIPPED stylesheet whether or
       * not anything renders it, and a dead padding rule rode out to users
       * on the strength of this one line. The value is still the sheet's own
       * spacing token doubled rather than a pixel count chosen here, and
       * `insetRightPx` resolves an inline declaration through the same token
       * table, so the leg is unchanged: double the padding, double the
       * inset. */
      wrapper.className = "";
      wrapper.setAttribute("style", "padding-inline: calc(var(--spacing) * 6)");
      const atPx6 = insetRightPx(root, probe, host, rem, table);
      expect(
        atPx6,
        "doubling the wrapper's padding did not move the bar's column",
      ).toBe(atPx3 * 2);
      // ...and the band moves with it, by the same pixels.
      const moved = barCells(box, BANDS[0], atPx6);
      expect(Math.max(...moved.map((c) => c.x1))).toBeCloseTo(
        Math.max(...cells.map((c) => c.x1)) - atPx3 / box.w,
        10,
      );
      // GROUND: with no wrapper padding at all the scroller IS flush, so the
      // reader is not simply always answering "inboard".
      wrapper.removeAttribute("style");
      wrapper.className = "";
      expect(insetRightPx(root, probe, host, rem, table)).toBe(0);
    } finally {
      host.remove();
    }

    /* POSITIVE CONTROL: at this column the floor is a live constraint. A
     * thumb dimmed to read 2:1 against the lightest ground the band offers
     * is caught, and the same arithmetic at the floor itself is not - so a
     * pass here is the thumb's doing and not the reader's. The grey is
     * derived from the ground this run measured, never typed. */
    const withFog = withMist(
      painted,
      mistLayers(
        painted.surfaceRule,
        ".panel-mist-left .panel-mist-veil",
        panelFogCeiling("left"),
      ),
    );
    const grounds = groundEnvelope(withFog, box, BANDS[0], inset);
    const lightest = grounds.reduce((a, b) =>
      luminance(parseRgbColour(a)) > luminance(parseRgbColour(b)) ? a : b,
    );
    expect(
      worstThumbRatio(greyAt(2, lightest), "transparent", grounds),
      "a thumb at 2:1 against this band's lightest ground clears the floor here, so " +
        "the floor is not a constraint at this column",
    ).toBeLessThan(AA_NON_TEXT);
    expect(
      worstThumbRatio(greyAt(AA_NON_TEXT, lightest), "transparent", grounds),
      "a thumb derived to sit exactly on the floor is reported under it",
    ).toBeGreaterThanOrEqual(AA_NON_TEXT);
    // And the shipped thumb, in this column, clears.
    const thumb = tokensOf(
      resolveVars(nearestDeclaration(root, scroller, COLOUR)!.declaration.value, table),
    )[0];
    const shipped = worstThumbRatio(thumb, "transparent", grounds);
    expect(
      shipped,
      `the sidebar's lit corner reads ${shipped.toFixed(2)}:1 in the bar's real column ` +
        `(${SCROLLBAR_THIN_PX}px wide, ${inset}px inboard) over (${grounds.join(" / ")})`,
    ).toBeGreaterThanOrEqual(AA_NON_TEXT);
  }, 25_000);

  it("the bar keeps to its own column: the gutter reserves it and nothing paints over it", async () => {
    /* Area hygiene, the third rule this step is held to: no overflow and no
     * area violation. Two halves.
     *
     * (a) Where the sheet reserves a gutter, the scroller's content box
     * stops where the bar begins, so the bar overlays no content and the
     * content never reaches under the bar. Where it does not, the platform
     * bar is drawn over the content - which is why the three page scrollers
     * carry `stable` and the portalled pair, whose scroller IS their own
     * box, do not need it.
     *
     * (a)'s `content[1] === bar[0]` is an identity inside this file's MODEL
     * of the box, not a measurement: jsdom computes no layout, `clientWidth`
     * is 0 on every element here, and both sides of that equality subtract
     * SCROLLBAR_THIN_PX from the same modelled width, so the line cannot go
     * red while the declaration above it passes. The load-bearing half is
     * that declaration - each mounted scroller's winning `scrollbar-gutter`,
     * read off the compiled sheet - and the real guard is the mutation that
     * drops the gutter from `.generation-dialog`, which turns this case red.
     * The identity is kept because it states the geometry a reserved strip
     * is for; it is not evidence on its own.
     *
     * (b) Nothing decorative is stacked above the content, so no layer of
     * the panel paints over the bar. */
    const barColumn = (w: number): [number, number] => [w - SCROLLBAR_THIN_PX, w];
    const contentColumn = (w: number, gutter: string | null): [number, number] => [
      0,
      gutter === STABLE ? w - SCROLLBAR_THIN_PX : w,
    ];
    const scrollerWidth = SIDEBAR_BOXES[0].w;

    for (const [name, mount] of GUTTERED) {
      const el = await mount();
      const gutter = winningDeclaration(root, el, GUTTER)?.value ?? null;
      expect(gutter, `${name} does not reserve a gutter`).toBe(STABLE);
      const bar = barColumn(scrollerWidth);
      const content = contentColumn(scrollerWidth, gutter);
      expect(
        content[1],
        `${name} reserves a gutter and its content still runs under the bar`,
      ).toBe(bar[0]);
      // The bar stays inside the scroller's own box at both ends.
      expect(bar[0]).toBeGreaterThanOrEqual(0);
      expect(bar[1]).toBe(scrollerWidth);
      cleanup();
    }
    // GROUND: the identity above is not true of every scroller. A box with
    // no gutter has its content running the full width, under the bar - so
    // the three that reserve one are doing something the model can see.
    const noGutter = contentColumn(scrollerWidth, null);
    expect(noGutter[1]).toBeGreaterThan(barColumn(scrollerWidth)[0]);
    const dialog = await characterCreateDialog();
    expect(
      winningDeclaration(root, dialog, GUTTER)?.value ?? null,
      "the portalled dialog reserves a gutter after all, so the control above is not " +
        "the case it claims to be",
    ).toBeNull();

    // (b) Every painted `.glass-dark` decoration sits BELOW the content, so
    // the bar is never painted over. `.glass-dark > *` is what lifts the
    // content, and the pseudo-elements declare no z-index at all.
    const contentZ = declarationOnSelector(root, ".glass-dark > *", Z_INDEX);
    expect(contentZ, "the sidebar no longer lifts its content above its decoration").not
      .toBeNull();
    /* The decoration of a panel is what it paints OUTSIDE its content flow:
     * its own two pseudo-elements, and the boxes the sheet takes out of flow
     * inside it, which here is the wrapper the mist renders into. A painted
     * rule that is neither - a hovered list row, say - is content, and
     * content is what the level below is measured against rather than a
     * violation of it. */
    const decorating: string[] = [];
    root.walkRules((rule) => {
      if (conditional(rule)) return;
      let paints = false;
      let outOfFlow = false;
      rule.walkDecls(/^background(-color|-image)?$/, () => {
        paints = true;
      });
      rule.walkDecls(/^position$/, (decl) => {
        const value = decl.value.trim();
        if (value === "absolute" || value === "fixed") outOfFlow = true;
      });
      for (const selector of resolvedSelectors(rule)) {
        if (!selector.startsWith(".glass-dark")) continue;
        if (selector === ".glass-dark" || selector === ".glass-dark > *") continue;
        const ownPseudo =
          selector === ".glass-dark::before" || selector === ".glass-dark::after";
        if (!(ownPseudo ? paints || outOfFlow : outOfFlow)) continue;
        if (!decorating.includes(selector)) decorating.push(selector);
      }
    });
    expect(
      decorating,
      "the sheet has no .glass-dark decoration outside the content flow, so the " +
        "stacking check below is asking about an empty set",
    ).not.toEqual([]);
    for (const selector of decorating) {
      const declared = declarationOnSelector(root, selector, Z_INDEX);
      // No z-index is `auto`, which on these boxes paints below a positioned
      // sibling that has one.
      const level = declared === null ? 0 : Number(declared);
      expect(
        level,
        `${selector} is stacked at ${declared} and the sidebar's content at ${contentZ}, ` +
          "so the decoration paints over the scroller and its bar",
      ).toBeLessThan(Number(contentZ));
      /* And it takes no pointer. The value can be declared on the selector
       * itself (the pseudo-elements do) or on the class the panel-scoped
       * rule qualifies - `.glass-dark > .panel-mist` positions the box that
       * `.panel-mist` makes transparent to the mouse - so both are asked. */
      const base = selector.slice(selector.lastIndexOf(" ") + 1);
      const pointer =
        declarationOnSelector(root, selector, POINTER_EVENTS) ??
        declarationOnSelector(root, base, POINTER_EVENTS);
      expect(
        pointer,
        `${selector} takes the pointer, so it is over the bar for the mouse even if ` +
          "it paints under it",
      ).toBe("none");
    }
    // GROUND: a probe sheet that lifts the sheen above the content is
    // caught by the same reader.
    const overProbe = parseSheet(
      ".glass-dark > * { z-index: 1; }\n.glass-dark::after { background: #fff; z-index: 2; }\n",
    );
    expect(
      Number(declarationOnSelector(overProbe, ".glass-dark::after", Z_INDEX)),
    ).toBeGreaterThan(Number(declarationOnSelector(overProbe, ".glass-dark > *", Z_INDEX)));
  }, 30_000);

  it("POSITIVE CONTROL: the track reader tells an invisible rail from a painted one", async () => {
    const { track } = scrollbarPaint(
      await characterCreateDialog(),
      "CharacterCreateDialog",
      DIALOG_BOXES,
    );
    // The shipped rail paints nothing...
    expect(paintedStops(track, table)).toEqual([]);
    // ...and a solid one would, so the assertion above is not free.
    expect(paintedStops("rgba(255, 255, 255, 1)", table)).not.toEqual([]);
  }, 15_000);

  it("the dark wallpaper takes the dark surfaces' thumb, not the canvas's", async () => {
    // The dark-wallpaper scroller matches the light list AND the dark one at
    // the same specificity (both 0-2-0), so only source order separates
    // them. Asserted on the VALUES rather than on which selector won, so it
    // survives a rename and still goes red on a reorder. Each shell is
    // unmounted before the next one, or three of them stack in the document
    // and the queries answer from the first.
    const sidebar = scrollbarPaint(await characterListScroller(), "sidebar", SIDEBAR_BOXES, {
      mist: [".panel-mist-left .panel-mist-veil", panelFogCeiling("left")],
    });
    cleanup();
    queryClient.clear();

    wallpaper.dark = true;
    const wallpaperThumb = scrollbarPaint(
      await darkChatScroller(),
      "dark wallpaper",
      CANVAS_BOXES,
      { ground: "frame" },
    );
    cleanup();
    queryClient.clear();
    wallpaper.dark = false;

    const canvas = scrollbarPaint(await chatScroller(), "light canvas", CANVAS_BOXES);

    expect(
      wallpaperThumb.thumb,
      "the chat scroller over a dark wallpaper is not painted with the dark surfaces' thumb",
    ).toBe(sidebar.thumb);
    expect(
      canvas.thumb,
      "the light canvas and the dark surfaces ended up with the same thumb",
    ).not.toBe(sidebar.thumb);
  }, 30_000);

  /* ---- the colour stops at the scrollers; it does not flood the surface ----
   *
   * `scrollbar-color` inherits and `scrollbar-width` does not. A colour on a
   * SURFACE therefore reaches everything inside it, and Chromium honours the
   * `::-webkit-scrollbar` family only while an element's `scrollbar-color` is
   * `auto`, so anything that scrolls inside that surface and is not one of
   * these scrollers would lose the 5px bar `@layer base` gives it and fall
   * back to the browser's default wide bar with arrow buttons. The colour
   * lives on the scrollers so that cannot happen.
   *
   * The composer's message box is that element. It scrolls on itself (the
   * component caps its height and sets its own overflow), it sits inside
   * the canvas, and it sits OUTSIDE the chat scroller, so no rule here is
   * meant to reach it and the base family is the whole of its look.
   */

  /** The bar itself, not the thumb or the track: this is the rule that sets
   *  a width, so this is the one an inherited colour costs. */
  const WEBKIT_BAR = /::-webkit-scrollbar(?![-\w])/;

  /** The `::-webkit-scrollbar` rules that reach `el`'s own bar.
   *  `candidatesFor` cannot answer this and should not: it drops
   *  pseudo-element selectors because their declarations paint another
   *  box. Here that other box is the question, so the pseudo-element is
   *  cut off and the element part is put to the selector engine. */
  function webkitBarSelectors(el: Element): string[] {
    const out = new Set<string>();
    root.walkRules((rule) => {
      if (conditional(rule)) return;
      for (const selector of resolvedSelectors(rule)) {
        if (!WEBKIT_BAR.test(selector)) continue;
        const base = selector.slice(0, selector.indexOf("::")).trim() || "*";
        if (el.matches(base)) out.add(selector);
      }
    });
    return [...out];
  }

  /** The composer's message box and, from the same render, the chat
   *  scroller it sits beside. ChatCanvas rather than the whole shell: it is
   *  what Composer.test.tsx and DraftPersistence.test.tsx mount, it renders
   *  the same `.warm-canvas` and the same scroller, and it settles without
   *  the background refetches that make an AppShell render log act()
   *  warnings. The message is a USER one, so its edit control exists. */
  async function composerAndScroller(): Promise<{
    box: HTMLElement;
    scroller: Element;
  }> {
    useUiStore.setState({
      selectedChatId: chatFixture.id,
      selectedModelId: modelFixture.id,
      selectedCharacterId: characterFixture.id,
    });
    mockFetch({
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      [`/chats/${chatFixture.id}/messages`]: {
        body: [{ ...messageFixture, role: "user" }],
      },
    });
    renderWithQueryClient(h(ChatCanvas), { wrapper: Wrapped });
    const box = await screen.findByRole("textbox", { name: "Message" });
    const found = await screen.findAllByText(messageFixture.content);
    return { box, scroller: scrollerAround(found, "the message") };
  }

  it("the composer's message box scrolls on itself, outside the chat scroller", async () => {
    // The premise of the two cases below. If the component stops capping the
    // box, or moves it inside the scroller, they stop being about anything
    // and this says so instead of passing quietly.
    const { box, scroller } = await composerAndScroller();
    const style = getComputedStyle(box);
    expect(style.overflowY, "the message box no longer scrolls on itself").toBe("auto");
    expect(style.maxHeight, "the message box is no longer capped").not.toBe("");
    expect(
      scroller.contains(box),
      "the message box moved inside the chat scroller",
    ).toBe(false);
    expect(
      box.closest(".warm-canvas"),
      "the message box is no longer inside the canvas surface",
    ).not.toBeNull();
    expect(
      box.closest(".overflow-y-auto"),
      "the message box is inside a scroller container after all",
    ).toBeNull();
  }, 20_000);

  it("the chat scroller is given its colour by name; the canvas box around it is not", async () => {
    const { box, scroller } = await composerAndScroller();
    const canvas = box.closest(".warm-canvas");
    if (!canvas) throw new Error("the composer is not inside the canvas surface");

    // GROUND: a rule declaring scrollbar-color reaches the scroller, and it
    // reaches it directly, which is what keeps the value off everything else
    // inside the surface.
    expect(
      cover(scroller, COLOUR),
      "no rule declaring scrollbar-color reaches the chat scroller",
    ).not.toEqual([]);
    expect(
      nearestDeclaration(root, scroller, COLOUR)?.element,
      "the scroller takes its colour from an ancestor instead of its own rule",
    ).toBe(scroller);

    // The surface box declares none, under any condition. This is the
    // architectural invariant the whole split exists for - the colour lives
    // on scrollers, never on a surface - and it is asked with `@media` and
    // `@supports` included, because a conditional declaration on the surface
    // floods its descendants exactly the same way. POSITIVE CONTROL for the
    // absence is the scroller two lines up: the reader does find rules when
    // there are rules to find.
    expect(
      declaredOn(canvas, COLOUR),
      "the canvas surface declares a scrollbar-color, which every scrolling " +
        "descendant then inherits",
    ).toEqual([]);
  }, 20_000);

  it("the composer's message box keeps the base webkit bar, because no scrollbar-color reaches it", async () => {
    const { box, scroller } = await composerAndScroller();

    // GROUND CONTROL, in this very render and independent of WHERE the
    // colour is declared: something in the sheet does reach the scroller
    // beside the box. An empty sheet, or a reader that found nothing, fails
    // here instead of passing the absence below. The render has settled -
    // the scroller was located by a message that arrived.
    expect(
      nearestDeclaration(root, scroller, COLOUR),
      "no scrollbar-color reaches the chat scroller at all",
    ).not.toBeNull();

    // The finding. Its computed value is `auto`, so Chromium keeps the
    // webkit family for it.
    expect(
      computedScrollbarColour(box),
      "the composer's message box does not compute scrollbar-color: auto, " +
        "so Chromium drops its ::-webkit-scrollbar rules and paints the " +
        "browser's default bar with arrow buttons",
    ).toBe("auto");

    // And it is `auto` for BOTH reasons it needs to be, not just one. No
    // non-auto value is declared on the box itself, under any condition...
    expect(
      declaredOn(box, COLOUR).filter((d) => !/\bauto\b/.test(d)),
      "a non-auto scrollbar-color is declared on the composer's message box",
    ).toEqual([]);
    // ...and nothing in the sheet reaches it by inheritance either, which is
    // the leg that matters: a value on `.warm-canvas` matches the canvas and
    // not the box, and would still land on it.
    const reaching = couldEverReach(box, COLOUR).filter((d) => !/\bauto\b/.test(d));
    expect(
      reaching,
      `a non-auto scrollbar-color could reach the composer's message box (${reaching.join("; ")})`,
    ).toEqual([]);

    // POSITIVE CONTROL for the harm: there is something to lose. The base
    // layer gives this box a 5px bar, and that is what a non-auto
    // scrollbar-color would switch off.
    expect(
      webkitBarSelectors(box),
      "no ::-webkit-scrollbar rule reaches the message box, so the reader proves nothing",
    ).not.toEqual([]);
  }, 20_000);

  /** The page's own fields, each inside a scroller that DOES carry a thumb
   *  colour, so each is a live inheritance case rather than a hypothetical. */
  const PAGE_FIELDS: Array<[string, () => Promise<{ field: Element; scroller: Element }>]> = [
    ["the composer's message box", async () => {
      const { box, scroller } = await composerAndScroller();
      return { field: box, scroller };
    }],
    ["the message edit box", async () => {
      const user = userEvent.setup();
      await composerAndScroller();
      await user.click(await screen.findByRole("button", { name: "Edit message" }));
      const field = await screen.findByRole("textbox", { name: "Edit message text" });
      return { field, scroller: scrollerAround([field], "the edit box") };
    }],
    ["the persona description field", async () => {
      useUiStore.setState({ activeRightPanelTab: "persona" });
      mockFetch({
        "/settings": { body: settingsFixture },
        "/personas": { body: [] },
        "/models/openrouter": { body: modelListFixture },
      });
      renderWithQueryClient(h(RightPanel), { wrapper: Wrapped });
      // The field lives in the create form, which the panel opens on demand.
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: /new persona/i }));
      const field = await screen.findByLabelText(/description/i);
      return { field, scroller: scrollerAround([field], "the persona field") };
    }],
    // Two native `input`s, each inside a scroller that carries a thumb
    // colour. This pair was a `select` and an `input` while the notebook tab
    // still drew native selects; IP-03 replaced all eight of them with the
    // Base UI Select, whose popup is a div, so the app renders no native
    // `select` anywhere now. The `select` member of the field rule keeps its
    // guard in the next test, which plants a real one under every surface
    // that carries a colour.
    ["the notebook tab's new-limit field", async () => {
      const scroller = await notebookTabScroller();
      const field = await screen.findByLabelText("New limit");
      if (field.tagName.toLowerCase() !== "input") {
        throw new Error(
          `the notebook tab's new-limit field is a ${field.tagName.toLowerCase()}`,
        );
      }
      if (!scroller.contains(field)) {
        throw new Error("the new-limit field is outside the notebook tab's scroller");
      }
      return { field, scroller };
    }],
    ["the persona display name field", async () => {
      useUiStore.setState({ activeRightPanelTab: "persona" });
      mockFetch({
        "/settings": { body: settingsFixture },
        "/personas": { body: [] },
        "/models/openrouter": { body: modelListFixture },
      });
      renderWithQueryClient(h(RightPanel), { wrapper: Wrapped });
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: /new persona/i }));
      const field = await screen.findByLabelText(/display name/i);
      if (field.tagName.toLowerCase() !== "input") {
        throw new Error(`the persona display name field is a ${field.tagName.toLowerCase()}`);
      }
      return { field, scroller: scrollerAround([field], "the persona name field") };
    }],
  ];

  it.each(PAGE_FIELDS)(
    "%s computes scrollbar-color: auto, inside a scroller that does not",
    async (name, mount) => {
      const { field, scroller } = await mount();
      // GROUND: the scroller around it IS given a colour, so this is a real
      // inheritance case and not a field sitting where nothing was declared.
      expect(
        computedScrollbarColour(scroller),
        `${name}: the scroller around it has no thumb colour, so nothing was inherited`,
      ).not.toBe("auto");
      expect(
        computedScrollbarColour(field),
        `${name} computes ${computedScrollbarColour(field)}, so Chromium drops its ` +
          "::-webkit-scrollbar rules and paints the browser's default bar",
      ).toBe("auto");
      // `computedScrollbarColour` goes through the cascade reader, which
      // drops conditionals, so a flood written inside an `@media` would land
      // on this field in a browser and be invisible here. An own declaration
      // beats any inherited value whatever the weights are, so this asks
      // only about the field itself.
      const conditioned = declaredOn(field, COLOUR).filter((d) => !d.includes("auto"));
      expect(
        conditioned,
        `${name} is given a thumb colour by a conditional rule (${conditioned.join("; ")})`,
      ).toEqual([]);
    },
    25_000,
  );

  it("every tag the field rule names is cut off, under every surface that carries a colour", async () => {
    /* The rule lists `textarea, input, select` under five surfaces, and the
     * cases above can only reach the pairs the app happens to render today:
     * no native `select` is drawn inside a dialog, no `input` inside the
     * chat scroller. So the tags the app does not render there are asked
     * the same question with a control of that tag put into the surface the
     * app DID render. It is still the sheet's cascade against a real
     * element in the real document; only the element is mine.
     *
     * Without this a rule member can be deleted with the suite green, and
     * `select` is now exactly that member: IP-03 converted the notebook
     * tab's four native selects to the Base UI Select, so the app renders no
     * native one and only the planted control asks that member anything. */
    mountShell();
    await screen.findAllByText(characterFixture.name);
    const dialog = await characterCreateDialog();
    // The Select popup is its own scroller and carries the colour on itself,
    // so it has to be in the document for its own member of the field rule to
    // be asked at all.
    const popup = await selectPopup();

    const hosts = [...document.querySelectorAll("*")].filter(
      (el) => computedScrollbarColour(el) !== "auto",
    );
    expect(
      hosts.length,
      "nothing in the shell or the dialog carries a thumb colour, so there is no " +
        "inheritance to cut",
    ).toBeGreaterThan(0);
    expect(dialog.isConnected, "the dialog left the document before it was asked").toBe(true);
    expect(popup.isConnected, "the Select popup left the document before it was asked").toBe(true);
    expect(
      hosts,
      "the Select popup is not among the hosts, so its own field rule is never asked",
    ).toContain(popup);

    const planted: Element[] = [];
    try {
      for (const host of hosts) {
        // POSITIVE CONTROL, per host: the colour really does reach a child
        // here. A `div` is not in the field rule, so it inherits, and that
        // is what proves the `auto` below is the rule doing its job rather
        // than nothing having been declared in the first place.
        const bystander = document.createElement("div");
        host.appendChild(bystander);
        planted.push(bystander);
        expect(
          computedScrollbarColour(bystander),
          `a plain child of a scroller matched by ${cover(host, COLOUR).join(", ")} ` +
            "inherits no colour, so this host proves nothing",
        ).not.toBe("auto");

        for (const tag of ["textarea", "input", "select"]) {
          const field = document.createElement(tag);
          host.appendChild(field);
          planted.push(field);
          expect(
            computedScrollbarColour(field),
            `a <${tag}> inside a scroller matched by ${cover(host, COLOUR).join(", ")} ` +
              `computes ${computedScrollbarColour(field)}, so Chromium drops its ` +
              "::-webkit-scrollbar rules and paints the browser's default bar",
          ).toBe("auto");
        }
      }
    } finally {
      for (const node of planted) node.remove();
    }
  }, 30_000);

  it("POSITIVE CONTROL: the safety reader sees what the cascade reader skips", async () => {
    // Two controls, because the first alone is not enough. The chat scroller
    // IS reached, so a reader that found nothing anywhere fails here...
    const { scroller } = await composerAndScroller();
    const reached = couldEverReach(scroller, COLOUR);
    expect(reached, "the safety reader finds nothing even on a scroller").not.toEqual([]);
    expect(reached.join(" ")).toContain("scrollbar-color");

    // ...but the scroller is reached by an UNCONDITIONAL rule, so that leg
    // never exercises the conditional mode and would pass with the flag
    // inverted. This one puts the two modes side by side on a sheet written
    // here for the purpose, in both shapes a compiler emits: a top-level
    // `@media`, and a `@media` nested inside the rule, where the
    // declaration's parent is the at-rule and not the rule. The cascade
    // reader must see neither; the safety reader must see both.
    const probe = parseSheet(
      "@media (min-width: 1px) { .probe-a { scrollbar-color: red blue; } }\n" +
        ".probe-b { @media (min-width: 1px) { scrollbar-color: red blue; } }",
    );
    for (const cls of ["probe-a", "probe-b"]) {
      const el = document.createElement("div");
      el.className = cls;
      expect(
        candidatesFor(probe, el, COLOUR),
        `.${cls}: the cascade reader is reporting a conditional declaration`,
      ).toEqual([]);
      expect(
        candidatesFor(probe, el, COLOUR, { includeConditional: true }),
        `.${cls}: the safety reader cannot see a conditional declaration`,
      ).not.toEqual([]);
    }
  }, 20_000);

  /** The value an element ends up computing for `scrollbar-color`: its own
   *  winning declaration, or the nearest ancestor's, or `auto` when none
   *  reaches it. This is the property as the browser resolves it, which is
   *  what decides whether the webkit family survives. */
  function computedScrollbarColour(el: Element): string {
    const own = winningDeclaration(root, el, COLOUR);
    if (own) return own.value;
    const inherited = nearestDeclaration(root, el, COLOUR);
    return inherited ? inherited.declaration.value : "auto";
  }

  /** Whether the SHEET says this element overflows, however it got that -
   *  a utility class or a hand-written rule. A colour reaches every
   *  descendant and costs nothing on a box that never shows a bar; the set
   *  that matters is the one that does, and it is the set ADIM 2 has to keep
   *  whole. */
  const scrolls = (el: Element): boolean => {
    const overflow = winningDeclaration(root, el, OVERFLOW_Y)?.value;
    return overflow === "auto" || overflow === "scroll";
  };

  /** The settings dialog opened on its Text page, one click down its own
   *  nav. Its root page is a list of rows and renders no field; the pages
   *  behind it do, and the live case this rule exists for (the voice note)
   *  is one of them. */
  async function appSettingsFieldPage(): Promise<Element> {
    const user = userEvent.setup();
    const dialog = await appSettingsDialog();
    await user.click(await screen.findByText("Text & readability"));
    await screen.findAllByRole("slider");
    return dialog;
  }

  /** The portalled surfaces that render a field. The Select popup renders a
   *  listbox and nothing to type in, so it has nothing to check here. */
  const FIELDED_DIALOGS: Array<[string, Mount]> = [
    ["CharacterCreateDialog", characterCreateDialog],
    ["CharacterEditDialog", characterEditDialog],
    ["AppSettingsDialog on a settings page", appSettingsFieldPage],
  ];

  it.each(FIELDED_DIALOGS)(
    "%s hands its thumb colour to its own bar and to none of its fields",
    async (name, mount) => {
      const dialog = await mount();

      // GROUND: the dialog box itself, which IS the scroller, does take a
      // colour. If it did not, every field below would read `auto` for the
      // wrong reason and this case would pass on a dead rule.
      expect(
        computedScrollbarColour(dialog),
        `${name}: the dialog's own scrollbar has no colour`,
      ).not.toBe("auto");

      // The fields are real rendered controls, not a selector guess. Each one
      // scrolls on its own the moment something bounds its height, and an
      // inherited colour is what would cost it the base bar.
      const fields = [
        ...dialog.querySelectorAll("textarea"),
        ...dialog.querySelectorAll("input"),
      ];
      expect(fields.length, `${name} rendered no field to check`).toBeGreaterThan(0);
      for (const field of fields) {
        const which =
          field.getAttribute("aria-label") ??
          field.getAttribute("name") ??
          field.getAttribute("placeholder") ??
          field.id ??
          "unnamed";
        expect(
          computedScrollbarColour(field),
          `${name}: ${field.tagName.toLowerCase()} "${which}" computes ` +
            `${computedScrollbarColour(field)}, so Chromium drops its ` +
            "::-webkit-scrollbar rules and paints the browser's default bar",
        ).toBe("auto");
        // Conditionals included, for the reason the page fields carry the
        // same leg: a flood aimed at a dialog's fields from inside an
        // `@media` is invisible to the cascade reader.
        const conditioned = declaredOn(field, COLOUR).filter((d) => !d.includes("auto"));
        expect(
          conditioned,
          `${name}: ${field.tagName.toLowerCase()} "${which}" is given a thumb colour ` +
            `by a conditional rule (${conditioned.join("; ")})`,
        ).toEqual([]);
      }
    },
    15_000,
  );

  it("every scroller the width rule reaches is given a colour too", async () => {
    // The two lists are written separately, so they can drift: a scroller
    // inside .elysium-page but outside the three surfaces would take
    // `scrollbar-width: thin` with the browser's own thumb colour, which is
    // the mismatch the whole step exists to remove. Asked of the real shell,
    // element by element, rather than by comparing two selector lists.
    mountShell();
    await screen.findAllByText(characterFixture.name);

    const thinned = [...document.querySelectorAll("*")].filter(
      (el) => cover(el, WIDTH).length > 0,
    );
    // GROUND: the shell rendered scrollers for this to be about.
    expect(thinned.length, "no element in the shell is given scrollbar-width").toBeGreaterThan(0);

    for (const el of thinned) {
      expect(
        computedScrollbarColour(el),
        `a scroller matched by ${cover(el, WIDTH).join(", ")} computes no thumb ` +
          "colour, so it takes the browser's own next to thumbs that are ours",
      ).not.toBe("auto");
      // One width for the whole app, which is what "one appearance" means
      // once the colour is per surface.
      expect(
        winningDeclaration(root, el, WIDTH)?.value,
        `a scroller matched by ${cover(el, WIDTH).join(", ")} is not drawn thin`,
      ).toBe(THIN);
    }
  }, 20_000);

  it("nothing inherits a thumb colour without also being drawn thin", async () => {
    // The third shape of the ADIM 2 trap, and the mirror of the case above.
    // A scroller that is NOT a field and is not in the width list would take
    // a colour by inheritance, lose the @layer base bar to it, and get the
    // platform's wide bar. Asked of the shell element by element rather than
    // by reasoning about selector lists.
    mountShell();
    await screen.findAllByText(characterFixture.name);

    const coloured = [...document.querySelectorAll("*")].filter(
      (el) => scrolls(el) && computedScrollbarColour(el) !== "auto",
    );
    // GROUND: the shell rendered scrollers that do take a colour.
    expect(coloured.length, "no scroller in the shell takes a thumb colour").toBeGreaterThan(0);

    for (const el of coloured) {
      expect(
        cover(el, WIDTH),
        `<${el.tagName.toLowerCase()}> scrolls and takes the thumb colour ` +
          `${computedScrollbarColour(el)}, but no rule draws it thin, so it keeps ` +
          "the colour and loses the bar it was drawn with",
      ).not.toEqual([]);
    }
  }, 20_000);

  /* The architectural invariant, asked of every page surface rather than of
   * the canvas alone: a colour on a SURFACE floods every scrolling descendant,
   * which is how the composer's message box lost its bar in the first place.
   * The surface is not named by a class here; it is whichever ancestor the
   * sheet says paints the ground behind that scroller.
   *
   * The two portalled surfaces are their own scroller, so their colour has
   * nowhere lower to go; the equivalent guard for them is the field rule,
   * which the FIELDED_DIALOGS cases assert. */
  it("every page surface hands its colour down to its scrollers and declares none itself", async () => {
    // A chat open so the canvas scrolls, and the right panel on the tab whose
    // scroller is a plain overflow container: the Models tab hands its
    // scrolling to a ScrollArea whose overflow is set inline by the library,
    // so the sheet cannot see it and this case would silently be about two
    // surfaces instead of three.
    useUiStore.setState({
      selectedChatId: chatFixture.id,
      activeRightPanelTab: "notebook",
    });
    mountShell();
    await screen.findAllByText(messageFixture.content);

    // The scrollers are whatever a colour rule actually reaches in the shell,
    // and each surface is whichever ancestor the sheet says paints the ground
    // behind one. No class string is named on either side.
    // The thumb-carrying scrollers, not every element a colour rule touches:
    // the field rule matches inputs too, and its value is `auto` precisely so
    // they are NOT part of this question.
    const scrollers = [...document.querySelectorAll("*")].filter(
      (el) => scrolls(el) && computedScrollbarColour(el) !== "auto",
    );
    const surfaces = new Map<Element, string>();
    for (const scroller of scrollers) {
      const painted = paintLayersBehind(root, scroller, table);
      if (painted) surfaces.set(painted.surface, painted.surfaceRule);
    }

    // GROUND: the shell rendered the three page surfaces, so the absence
    // below is about where the colour lives and not about an empty document.
    expect(
      scrollers.length,
      "no rule declaring scrollbar-color reaches anything in the shell",
    ).toBeGreaterThan(0);
    expect(
      [...new Set(surfaces.values())].length,
      `only ${[...new Set(surfaces.values())].join(", ")} painted behind a scroller; ` +
        "the sidebar, the right panel and the canvas should all be here",
    ).toBeGreaterThanOrEqual(3);

    for (const [surface, rule] of surfaces) {
      // `@media` and `@supports` included: a conditional flood is a flood.
      expect(
        declaredOn(surface, COLOUR),
        `${rule} declares a scrollbar-color, which every scrolling descendant ` +
          "inside it then inherits, fields included",
      ).toEqual([]);
    }
  }, 25_000);

  it("POSITIVE CONTROL: the webkit reader tells a reached element from an unreached one", async () => {
    // `::-webkit-scrollbar` in @layer base is unscoped, so a bare div is
    // reached; `.generation-dialog::-webkit-scrollbar` is not for it.
    const bare = document.createElement("div");
    expect(webkitBarSelectors(bare)).not.toEqual([]);
    expect(webkitBarSelectors(bare).filter((s) => s.startsWith(".generation-dialog"))).toEqual(
      [],
    );
  });
});

/**
 * T-51, T-84, T-88 and the dead-variable check.
 *
 * Four claims about the sheet the browser is actually handed, so all four
 * read the COMPILED text rather than index.css: a rule that never survives
 * the build is not a rule, and a variable nothing declares is not a value.
 * The two that are about what the page DOES - where the toast ends up, and
 * which nodes carry an inline layer - render instead, because those are
 * facts about the tree and not about the stylesheet.
 */
describe("the layer table (T-51)", () => {
  let sheet = "";
  let root: Root;

  beforeAll(async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    sheet = await loadGlassRightCss();
    root = parseSheet(sheet);
  }, 60_000);

  /**
   * The z-index a selector declares, read out of the compiled sheet by
   * PARSING it.
   *
   * It used to slice the text between `selector + " {"` and the next `}`,
   * which is the shape this file's own docstring calls "how it went blind".
   * Two ways that lies: the first `}` closes a NESTED rule rather than the
   * block, so a later declaration in the same rule is never seen, and
   * `indexOf` takes the first occurrence, so an override for the same
   * selector further down the sheet is invisible. Parsed, the rule is found
   * by its RESOLVED selector - nesting folded in by walking parents - and the
   * answer is the declaration that WINS: `!important` first, then the last
   * one, which is what the browser reads.
   */
  function layerOf(selector: string): number {
    const found: { value: number; important: boolean }[] = [];
    root.walkRules((rule) => {
      if (!resolvedSelectors(rule).includes(selector)) return;
      rule.each((node) => {
        if (node.type !== "decl" || node.prop.toLowerCase() !== "z-index") return;
        const value = Number(node.value.trim());
        if (Number.isNaN(value)) return;
        found.push({ value, important: node.important === true });
      });
    });
    expect(found.length, `no rule declares a z-index for "${selector}"`)
      .toBeGreaterThan(0);
    const important = found.filter((d) => d.important);
    const winners = important.length > 0 ? important : found;
    return winners[winners.length - 1].value;
  }

  /**
   * The layer utility a real `DialogContent` renders with.
   *
   * Reading `.z-50` by name was half a test. The number came out of the
   * sheet, which is right, but nothing tied that rule to the dialog - and
   * `.z-50` is still emitted by five other call sites (the overlay, the two
   * select slots, the tooltip's two), so `ui/dialog.tsx` could be moved to
   * `z-10` and the ordering assertion would go on reading 50 and passing
   * while every dialog painted under the menus, under the toast, and under
   * its own overlay. The tier is taken off the rendered node instead, and
   * the sheet is then asked what THAT utility means.
   */
  async function dialogTier(): Promise<string> {
    render(
      createElement(
        Dialog,
        { open: true },
        createElement(DialogContent, null, createElement(DialogTitle, null, "Tier")),
      ),
    );
    await screen.findByRole("dialog");
    const node = document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
    expect(node, "no dialog rendered to read a tier from").not.toBeNull();
    const tiers = [...node!.classList].filter((cls) => /^z-\d+$/.test(cls));
    expect(tiers, "the dialog renders no layer utility of its own")
      .toHaveLength(1);
    cleanup();
    return tiers[0];
  }

  it("orders menu under dialog under toast under lock", async () => {
    // The whole table in one line, and every number comes from the sheet.
    // The dialog's is a Tailwind utility rather than a rule of ours, which
    // is exactly why it has to be read rather than assumed: the primitive
    // could change it without touching index.css at all.
    const menu = Math.max(
      layerOf(".persona-menu"),
      layerOf(".chat-action-menu"),
      layerOf(".chat-action-confirm"),
    );
    const dialog = layerOf("." + await dialogTier());
    const toast = layerOf(".error-toast-stack");
    const lock = layerOf(".lock-overlay");
    // A LOWER number paints UNDER, so each message names what the tier above
    // is allowed to cover. The three used to say the opposite of what they
    // assert, and the last one named the precise defect this guard exists to
    // prevent - a toast painting over the lock screen.
    expect(menu, "a dialog no longer paints over menus").toBeLessThan(dialog);
    expect(dialog, "an error toast no longer paints over dialogs")
      .toBeLessThan(toast);
    expect(toast, "the lock screen no longer paints over the toasts")
      .toBeLessThan(lock);
  }, 30_000);

  it("GROUND: the reader returns real, different numbers", async () => {
    // Without this the ordering above would pass just as well on a reader
    // that answered 0 for everything.
    const values = [
      layerOf(".persona-menu"),
      layerOf("." + await dialogTier()),
      layerOf(".error-toast-stack"),
      layerOf(".lock-overlay"),
    ];
    expect(new Set(values).size, "the reader is answering one number for everything")
      .toBe(4);
    expect(Math.min(...values)).toBeGreaterThan(0);
  }, 30_000);

  it("GROUND: the reader takes the LAST declaration, not the first it finds", () => {
    // The whole reason this stopped being a substring search. Handed a sheet
    // where the selector is declared twice, a reader that takes the first
    // match reports the losing number; handed one where the rule nests, a
    // reader that slices to the first `}` never reaches the declaration at
    // all. Both shapes go through the real function, over a synthetic tree.
    const twice = parseSheet(
      ".probe-layer { z-index: 5; }\n.probe-layer { z-index: 9; }");
    const nested = parseSheet(
      ".probe-nest { &:hover { color: red; } z-index: 7; }");
    const readFrom = (tree: Root, selector: string): number => {
      const saved = root;
      root = tree;
      try {
        return layerOf(selector);
      } finally {
        root = saved;
      }
    };
    expect(readFrom(twice, ".probe-layer"), "the first rule won").toBe(9);
    expect(readFrom(nested, ".probe-nest"), "a nested rule cut the block short")
      .toBe(7);
  });

  it("leaves the lock no isolating ancestor, which is what makes 200 beat 60", () => {
    // The number only wins if both surfaces land in the SAME stacking
    // context. Before this unit the toast could not reach the lock for two
    // reasons - 35 < 200, and it rendered inside `.elysium-page`, whose
    // `isolation: isolate` imprisons everything under it, so no number could
    // have lifted it. Portalling it to the body removed that barrier and left
    // arithmetic holding the order alone, which makes "nothing above either
    // of them opens a context" the assumption the whole table now rests on.
    //
    // The lock is a child of the unstyled `div#root`; so is the app. So the
    // claim to hold is that the elements ABOVE them stay unstyled. A rule
    // for `#root`, or an `isolation` on `body` or `html`, would silently make
    // every number in the table meaningless.
    const above = new Set(["html", "body", "#root", ":root", "*"]);
    const offenders: string[] = [];
    root.walkRules((rule) => {
      for (const selector of resolvedSelectors(rule)) {
        if (!above.has(selector)) continue;
        rule.each((node) => {
          if (node.type !== "decl") return;
          const prop = node.prop.toLowerCase();
          const value = node.value.replace(/!important$/i, "").trim().toLowerCase();
          const opens =
            (prop === "isolation" && value !== "auto") ||
            (prop === "filter" && value !== "none") ||
            (prop === "backdrop-filter" && value !== "none") ||
            (prop === "contain" && value !== "none") ||
            (prop === "mix-blend-mode" && value !== "normal") ||
            (prop === "transform" && value !== "none") ||
            (prop === "perspective" && value !== "none");
          if (opens) offenders.push(`${selector} { ${prop}: ${value} }`);
        });
      }
    });
    expect(
      offenders,
      "something above the lock opens a stacking context - the layer numbers no longer order anything",
    ).toEqual([]);
    // POSITIVE CONTROL: the same walk over a sheet that DOES style the root
    // reports it, so the empty list above is a reading.
    const probe = parseSheet("#root { isolation: isolate; }");
    const seen: string[] = [];
    probe.walkRules((rule) => {
      for (const selector of resolvedSelectors(rule)) {
        if (above.has(selector)) seen.push(selector);
      }
    });
    expect(seen, "the walk cannot see a rule for the root at all").toEqual(["#root"]);
  });

  it("puts the toast stack beside the dialog in the body, not inside the page", () => {
    // The half a number cannot express. A dialog and a toast are open at the
    // same time; both are direct children of the body, and the toast comes
    // after, which is the order the browser paints in when the layers tie.
    // jsdom carries no paint order, but it carries portal order, and that is
    // what is measured.
    useErrorStore.getState().pushErrorDirect("custom_error", "Over the dialog.");
    render(
      createElement(
        Dialog,
        { open: true },
        createElement(
          DialogContent,
          null,
          createElement(DialogTitle, null, "A dialog"),
          createElement(ErrorToastStack),
        ),
      ),
    );

    const toast = screen.getByTestId("error-toast-stack");
    const dialog = document.querySelector('[data-slot="dialog-content"]');
    expect(dialog, "the dialog did not render").not.toBeNull();
    expect(toast.parentElement).toBe(document.body);
    const children = [...document.body.children];
    const dialogHost = children.find((c) => c.contains(dialog!));
    expect(dialogHost, "the dialog is not a child of the body").toBeDefined();
    expect(children.indexOf(toast)).toBeGreaterThan(children.indexOf(dialogHost!));
  });

  it("GROUND: the stack is a child of the body with no dialog open at all", () => {
    useErrorStore.getState().pushErrorDirect("custom_error", "On its own.");
    render(createElement(ErrorToastStack));
    expect(screen.getByTestId("error-toast-stack").parentElement).toBe(document.body);
  });

  /** Every node on the page carrying an inline layer it is not allowed. */
  function inlineLayerOffenders(): string[] {
    const allowed = new Set(INLINE_Z_INDEX_EXCEPTIONS.map((e) => e.selector));
    const offenders: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("[style]")) {
      if (!el.style.zIndex) continue;
      if ([...allowed].some((sel) => el.matches(sel))) continue;
      offenders.push(`${el.tagName.toLowerCase()}.${el.className}: ${el.style.zIndex}`);
    }
    return offenders;
  }

  it("POSITIVE CONTROL: the escape reader reports a request that got out", () => {
    // Six assertions in this file and one next door say `apiEscapes()` is
    // empty, and an empty list proves nothing until the reader has been seen
    // finding something. It is pure in its argument for exactly this, so the
    // same function is handed records rather than the live log.
    //
    // The two regimes behind it, because they are the whole point. `mockFetch`
    // installs its mock with `vi.stubGlobal("fetch", ...)`, which REPLACES the
    // egress sentinel's own wrapper - so while a mock is installed the
    // sentinel records nothing and nothing can leave. With no mock the
    // sentinel is still there, and its host list holds 127.0.0.1 on purpose
    // (the app's backend binds a random loopback port), so it records the
    // call and forwards it to the real transport. An empty log while a render
    // is on screen is therefore "the mock took it", and a non-empty one is
    // "the running application answered it".
    const escaped = apiEscapes([
      { kind: "fetch", url: `${API_BASE}/chats`, stack: "", headers: {} },
    ]);
    expect(escaped, "the reader cannot see a request that reached the wire")
      .toEqual([`fetch ${API_BASE}/chats`]);
    // And it does NOT report something aimed somewhere else, so an empty list
    // is a statement about this app's API rather than about traffic in
    // general.
    expect(
      apiEscapes([{ kind: "image", url: "blob:local-object", stack: "" }]),
      "the reader counts anything at all as an escape",
    ).toEqual([]);
  });

  it("carries no inline zIndex outside the named exception list", async () => {
    // A number written in a style attribute competes with the table without
    // appearing in it, which is where the chat action menu's 40 sat, written
    // at its two call sites where no reader of the table could see it. The
    // list is imported, never retyped.
    //
    // The shell is query-driven, so the transport is stubbed BEFORE it
    // mounts. Without that, `useStaleSelectionReconciliation` fires the chat,
    // character, model and settings queries against the real base URL and the
    // egress sentinel forwards a loopback call rather than refusing it, so
    // the suite talks to the running application's own API.
    mockFetch(shellRoutes());
    render(createElement(Providers, null, createElement(AppShell)));
    // Waited for on purpose, and on a node that only appears once the
    // character query has ANSWERED: rendering and reading in the same tick
    // sweeps a shell whose data has not arrived, and leaves React logging an
    // update outside act() on the way out.
    await screen.findByText(String(characterFixture.name));
    // GROUND: the sweep saw style attributes at all. On a shell that rendered
    // nothing with one, "no inline layer" is green for the wrong reason.
    expect(
      document.querySelectorAll("[style]").length,
      "nothing on the page carries a style attribute, so the sweep is vacuous",
    ).toBeGreaterThan(0);
    expect(inlineLayerOffenders(), "an inline z-index outside the exception list")
      .toEqual([]);
    expect(apiEscapes(), "the shell reached the real transport").toEqual([]);
  }, 30_000);

  it("including the popup the defect was actually in", async () => {
    // The shell alone never opens the chat action menu, so the two values
    // this unit took out of ChatList sat outside the sweep above: putting
    // one back left it green. The menu is opened here, and it is the one
    // that portals to the body with its coordinates written inline - the
    // exact shape the rule is about.
    const user = userEvent.setup();
    useUiStore.setState({ selectedCharacterId: 1, selectedChatId: null });
    mockFetch({ "/chats": { body: [chatFixture] } });
    renderWithQueryClient(createElement(ChatList));
    await screen.findByText(String(chatFixture.title));
    await user.click(
      screen.getByRole("button", { name: /open chat actions for/i }),
    );
    // GROUND: the menu really opened, and it really is placed inline - so a
    // green sweep means "no layer there" and not "nothing was rendered".
    const menu = await screen.findByRole("menu");
    expect(menu.style.position, "the menu is not placed inline at all")
      .toBe("fixed");
    expect(inlineLayerOffenders(), "the chat action menu writes its own layer")
      .toEqual([]);
    expect(apiEscapes(), "the chat list reached the real transport").toEqual([]);
  }, 30_000);

  it("GROUND: the one node on the exception list really renders, and is found", () => {
    // Without this the sweep above could pass on an app that renders nothing
    // with a style attribute at all, and the exception list could be emptied
    // without anything noticing.
    expect(INLINE_Z_INDEX_EXCEPTIONS.length, "the exception list is empty")
      .toBeGreaterThan(0);
    vi.useFakeTimers();
    try {
      useLockAudioWarningStore.setState({ pending: false, audioLeft: [] });
      render(createElement(LockOverlay, { onCommit: vi.fn(), onDone: vi.fn() }));
      act(() => {
        vi.advanceTimersByTime(900);
      });
      act(() => {
        useLockAudioWarningStore.getState().settle(["reply_3f2a1c9d.wav"]);
      });
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      const stage = document.querySelector<HTMLElement>(".vault-stage");
      expect(stage, "the exception's own node did not render").not.toBeNull();
      expect(stage!.style.zIndex).toBe(String(VAULT_STAGE_Z_INDEX));
      expect(
        INLINE_Z_INDEX_EXCEPTIONS.some((e) => stage!.matches(e.selector)),
        "the node that renders is not the one the list names",
      ).toBe(true);
    } finally {
      vi.runOnlyPendingTimers();
      vi.useRealTimers();
      useLockAudioWarningStore.setState({ pending: false, audioLeft: [] });
    }
  });
});

describe("permanent stacking contexts (T-84)", () => {
  let sheet = "";

  beforeAll(async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    sheet = await loadGlassRightCss();
  }, 60_000);

  /**
   * Every rule in `tree` that opens a PERMANENT stacking context.
   *
   * The tree is a parameter so the GROUND below can feed it a sheet whose
   * answer is known. It used to close over the module's own `sheet`, which
   * made that control unable to fail: it parsed a probe, threw it away, and
   * then asked the real sheet a question the function cannot answer "yes" to
   * whatever it is handed.
   */
  function openers(tree: Root): { selector: string; how: string }[] {
    const out: { selector: string; how: string }[] = [];
    tree.walkRules((rule) => {
      for (let p: unknown = rule.parent; p; p = (p as { parent?: unknown }).parent) {
        const node = p as { type?: string; name?: string };
        if (node.type === "atrule" && /keyframes/.test(node.name ?? "")) return;
      }
      let position: string | null = null;
      let zIndex: string | null = null;
      const hits: string[] = [];
      rule.each((n) => {
        if (n.type !== "decl") return;
        const prop = n.prop.toLowerCase();
        const value = n.value.replace(/!important$/i, "").trim().toLowerCase();
        if (prop === "position") position = value;
        if (prop === "z-index") zIndex = value;
        if ((prop === "backdrop-filter" || prop === "-webkit-backdrop-filter") && value !== "none") hits.push(prop);
        else if (prop === "filter" && value !== "none") hits.push(prop);
        else if (prop === "isolation" && value !== "auto") hits.push(prop);
        else if (prop === "contain" && value !== "none") hits.push(prop);
        else if (prop === "will-change" && value !== "auto") hits.push(prop);
        else if (prop === "mix-blend-mode" && value !== "normal") hits.push(prop);
        else if (prop === "content-visibility") hits.push(prop);
      });
      if (zIndex && zIndex !== "auto" && position !== "static") {
        hits.push(position ? `position:${position} + z-index` : "z-index (position: TSX)");
      }
      for (const how of hits) out.push({ selector: rule.selector, how });
    });
    return out;
  }

  it("every surface that opens one is written down", () => {
    const known = new Set(
      [...PERSISTENT_STACKING_CONTEXTS, ...UTILITY_STACKING_CONTEXTS]
        .map(([selector, how]) => `${selector} :: ${how}`),
    );
    const surprises = openers(parseSheet(sheet))
      .map((o) => `${o.selector} :: ${o.how}`)
      .filter((row) => !known.has(row));
    expect(
      surprises,
      "a new permanent stacking context was opened without going into the table",
    ).toEqual([]);
  });

  it("and everything written down is still there", () => {
    // The other direction: a row deleted from the constant to make the check
    // above pass would leave the table describing a sheet that moved on.
    const live = new Set(
      openers(parseSheet(sheet)).map((o) => `${o.selector} :: ${o.how}`));
    const stale = [...PERSISTENT_STACKING_CONTEXTS, ...UTILITY_STACKING_CONTEXTS]
      .map(([selector, how]) => `${selector} :: ${how}`)
      .filter((row) => !live.has(row));
    expect(stale, "the table names a context the sheet no longer opens").toEqual([]);
  });

  it("counts what the audit counted", () => {
    // OLCUM item 17's permanent-context figures, so the table cannot quietly
    // grow or shrink while both directions above stay green together.
    const authored = PERSISTENT_STACKING_CONTEXTS;
    expect(authored.length).toBe(PERSISTENT_CONTEXT_COUNTS.declarations);
    expect(new Set(authored.map(([s]) => s)).size)
      .toBe(PERSISTENT_CONTEXT_COUNTS.selectors);
  });

  it("GROUND: transform and a faded opacity do NOT make a member", () => {
    // The criterion's own dividing line. Both are usually a keyframe step or
    // a transition state, so the context they open is temporary; a checker
    // that counted them would report a list several times this size.
    //
    // The probe is what the reader is RUN OVER, which is the whole control.
    // Written the other way - parse a probe, then ask the real sheet - the
    // assertion was structurally green: `openers` never emits "transform" or
    // "opacity" for any input at all, so it could not have reported one.
    const probe = parseSheet(
      ".probe { transform: translateY(-1px); opacity: 0.5; }\n" +
      ".probe-real { isolation: isolate; }");
    expect(openers(probe).map((o) => `${o.selector} :: ${o.how}`))
      .toEqual([".probe-real :: isolation"]);
  });
});

describe("the body font stack (T-88)", () => {
  let sheet = "";

  beforeAll(async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    sheet = await loadGlassRightCss();
  }, 60_000);

  /** The declared value of a token, from the compiled sheet. */
  function tokenValue(name: string): string {
    const table = rootTokens(parseSheet(sheet));
    const value = table.get(name);
    expect(value, `the sheet declares no ${name}`).toBeDefined();
    return resolveVars(value!, table).trim();
  }

  it("starts with a generic keyword, not a family somebody named", () => {
    // KARAR-V: the app renders in the reader's own Windows font, so the
    // stack opens with the generic that asks for it and pins nothing. The
    // expected value is imported; a font name typed here would be the exact
    // copied constant that keeps a test green after the stack moves.
    const first = tokenValue("--font-sans").split(",")[0].trim();
    expect(first, "the stack no longer opens with a generic keyword")
      .toBe(SYSTEM_FONT_GENERIC);
    expect(first.startsWith('"'), "a quoted family name is first").toBe(false);
  });

  it("GROUND: the heading stack resolves through the same first member", () => {
    // --font-heading is `var(--font-sans)`, so reading it through the same
    // resolver must land on the same member. If it does not, the reader is
    // not resolving anything and the assertion above is reading text.
    expect(tokenValue("--font-heading").split(",")[0].trim())
      .toBe(SYSTEM_FONT_GENERIC);
  });

  it("POSITIVE CONTROL: a pinned family name would be caught", () => {
    const pinned = '"Segoe UI", system-ui, sans-serif'.split(",")[0].trim();
    expect(pinned).not.toBe(SYSTEM_FONT_GENERIC);
    expect(pinned.startsWith('"')).toBe(true);
  });

  it("keeps the tail that the system-ui regression needs", () => {
    // The stack's tail is not decoration: it is the fix for a known
    // system-ui regression (the VS Code / Tahoma case). Dropping the dead
    // first member must not become an excuse to prune the rest.
    const members = tokenValue("--font-sans").split(",").map((m) => m.trim());
    expect(members.length, "the stack lost its fallbacks").toBeGreaterThan(5);
    expect(members[members.length - 1]).toBe("sans-serif");
  });
});

describe("no rule reads a variable nothing declares", () => {
  let sheet = "";

  beforeAll(async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    sheet = await loadGlassRightCss();
  }, 60_000);

  it("the Radix variables are gone, name and reader together", () => {
    // Four rules bound the popup slots' transform-origin to --radix-*
    // variables. Nothing in this app is Radix - it is Base UI - so nothing
    // ever declared them, and each rule fell to its `top` fallback. Being
    // unlayered, that fallback beat the utility carrying the origin Base UI
    // computes, so every popup grew from its top edge wherever it opened,
    // while the rules read as deliberate positioning. Deleted with this unit.
    expect(sheet, "a rule still reads a --radix-* variable").not.toMatch(/--radix-/);
  });

  it("GROUND: the popup slots the deleted rules named are still in the sheet", () => {
    // Without this, "no --radix-" would pass just as well on a sheet where
    // the slots themselves had been deleted, which would be a far bigger
    // change hiding behind a green test.
    expect(sheet).toMatch(/\[data-slot="dialog-content"\]/);
    expect(sheet).toMatch(/\[data-slot="select-content"\]/);
  });
});

/**
 * T-85: dialog geometry, over all seven DialogContents.
 *
 * Six take a width from the scale and the one height; ImageLightbox is the
 * named exception and is excluded BY NAME, with its own classes asserted so
 * the exception is a record rather than a gap.
 *
 * Each dialog is opened the way a person opens it, and the class list is read
 * off the rendered node. The alternative - reading the className out of the
 * source - would pass on a component that never renders, and would be the
 * source-as-proxy the house rules forbid.
 */
describe("dialog geometry (T-85)", () => {
  const WIDTHS = new Set<string>(Object.values(DIALOG_WIDTHS));
  let sheet = "";

  /**
   * KURAL 4.3's table, in pixels.
   *
   * These four numbers are the claim, so they are written here rather than
   * imported: `DIALOG_WIDTHS` holds the class NAMES, and a test that checked
   * the names against themselves would be comparing the scale with itself -
   * which is what let `medium` move from 512 to 576 with the whole suite
   * green. The names come from the constant; the pixels come from the design
   * reference; the sheet is asked what the names actually resolve to.
   */
  const REF_WIDTH_PX: Record<keyof typeof DIALOG_WIDTHS, number> = {
    base: 384,
    narrow: 448,
    medium: 512,
    wide: 672,
  };

  /**
   * KURAL 4.3's height, as a value.
   *
   * The reference's row reads, with the utility's name broken so this file
   * emits no candidate of its own: "Yukseklik | `max-h-` + `[85vh]` | - |
   * Tek deger. Baska bir vh degeri yazilmaz." One value, and no second vh
   * value is written.
   *
   * Here for the same reason the four widths are here, and the gap it closes
   * is the one the widths had: `DIALOG_MAX_HEIGHT` holds the class NAME, and
   * the assertions above ask each dialog to carry that name - so the six
   * dialogs stay on ONE height whatever it is, and changing the constant
   * moves all six to a second vh value in a single line with the whole suite
   * green. The name comes from the constant; the value comes from the design
   * reference; the sheet is asked what the name resolves to.
   *
   * Written as the VALUE and never as the class: Tailwind scans test files as
   * raw text, so spelling the utility here would keep emitting it from this
   * file after production stopped asking for it, and the reading below would
   * go on finding a rule nothing renders.
   */
  const REF_MAX_HEIGHT = "85vh";

  beforeAll(async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    sheet = await loadGlassRightCss();
  }, 60_000);

  beforeEach(() => {
    useUiStore.setState({ selectedCharacterId: 1, selectedChatId: null });
  });

  /** The rendered DialogContent node, whatever opened it. */
  function content(): HTMLElement {
    const node = document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
    expect(node, "no dialog rendered").not.toBeNull();
    return node!;
  }

  /**
   * Every size class on `root` or under it that is not one of the seven steps.
   *
   * The class-closure half of IP-05 FIX 1, run where the shell cannot reach.
   * A RETIRED stock class emits no rule at all once the `--text-*` namespace
   * is cleared, so the compiled-sheet net in test/lib/fontScale.test.ts
   * cannot see one; that file's render sweep walks only what `AppShell`
   * paints on first render, and a dialog is not in it. These seven dialogs
   * are the surfaces outside both nets, and they are already open here.
   *
   * The retired names are BUILT rather than written: Tailwind scans test
   * files as raw text, so spelling one here would emit the very utility this
   * says is gone.
   */
  function offScaleSizeClasses(root: HTMLElement): string[] {
    const allowed = new Set(TYPE_SCALE_STEPS.map((s) => `text-${s}`));
    const retired = new Set(["x" + "s", "s" + "m", "b" + "ase", "l" + "g",
                             "x" + "l", "2x" + "l", "3x" + "l"]);
    const out: string[] = [];
    for (const el of [root, ...root.querySelectorAll<HTMLElement>("*")]) {
      for (const cls of el.classList) {
        // Variants stripped FIRST. Anchored at the start of the raw class,
        // this sweep could not see a retired step behind a breakpoint or a
        // state prefix - which is the likeliest form there is, and the exact
        // one KURAL 3.6 is about: putting a breakpoint variant of a retired
        // step back on a ui/ primitive's base string left every net green.
        const bare = cls.split(":").pop() ?? cls;
        if (allowed.has(bare)) continue;
        if (!bare.startsWith("text-")) continue;
        const step = bare.slice(5);
        if (retired.has(step) || /^(\d|\[)/.test(step)) out.push(cls);
      }
    }
    return out;
  }

  it("GROUND: the size-class sweep reports what it is looking for", () => {
    // Same predicate over a node that carries the two shapes it hunts, so an
    // empty list on each dialog below is a reading and not a broken matcher.
    const probe = document.createElement("div");
    const hunted = ["text-" + "sm", "text-" + "[10px]", "md:" + "text-" + "sm"];
    probe.className = [...hunted, `text-${TYPE_SCALE_STEPS[1]}`,
                       `md:text-${TYPE_SCALE_STEPS[2]}`].join(" ");
    expect(offScaleSizeClasses(probe).sort(), "the dialog sweep went blind")
      .toEqual([...hunted].sort());
  });

  function assertOnScale(node: HTMLElement, name: string) {
    const classes = new Set(node.classList);
    expect(classes.size, `${name} rendered with no classes`).toBeGreaterThan(0);
    expect(offScaleSizeClasses(node), `${name} carries a size class off the scale`)
      .toEqual([]);
    const widths = [...classes].filter((c) => c.startsWith("sm:max-w-"));
    expect(widths, `${name} declares more than one width`).toHaveLength(1);
    expect(WIDTHS.has(widths[0]), `${name} width ${widths[0]} is off the scale`)
      .toBe(true);
    expect(classes.has(DIALOG_MAX_HEIGHT), `${name} is not on the one height`)
      .toBe(true);
    const heights = [...classes].filter((c) => c.startsWith("max-h-"));
    expect(heights, `${name} declares a second height`).toEqual([DIALOG_MAX_HEIGHT]);
  }

  it("the chat create dialog is on the scale", async () => {
    const user = userEvent.setup();
    mockFetch({ "/chats": { body: chatFixture } });
    renderWithQueryClient(
      createElement(ChatCreateDialog, {
        trigger: createElement(Button, null, "Open"),
      }),
    );
    await user.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    assertOnScale(content(), "ChatCreateDialog");
  }, 30_000);

  it("the character create dialog is on the scale", async () => {
    const user = userEvent.setup();
    mockFetch({ "/characters": { body: characterFixture } });
    renderWithQueryClient(
      createElement(CharacterCreateDialog, {
        trigger: createElement(Button, null, "Open"),
      }),
    );
    await user.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    assertOnScale(content(), "CharacterCreateDialog");
  }, 30_000);

  it("the character edit dialog is on the scale", async () => {
    const user = userEvent.setup();
    mockFetch({ "/characters/1": { body: characterFixture } });
    renderWithQueryClient(
      createElement(CharacterEditDialog, {
        character: characterFixture,
        trigger: createElement(Button, null, "Open"),
      }),
    );
    await user.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    assertOnScale(content(), "CharacterEditDialog");
  }, 30_000);

  it("the character import dialog is on the scale, height included", async () => {
    // This one and the chat create dialog had NO height class at all before
    // this unit, which is not "shorter" - it is "as tall as its content", so
    // a long paste ran past the top and bottom of the window. The page
    // itself never scrolls, so the part that did not fit could not be
    // reached at all.
    const user = userEvent.setup();
    mockFetch({ "/characters/import": { body: characterFixture } });
    renderWithQueryClient(
      createElement(CharacterImportDialog, {
        trigger: createElement(Button, null, "Open"),
      }),
    );
    await user.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    assertOnScale(content(), "CharacterImportDialog");
  }, 30_000);

  it("the app settings dialog is on the scale", async () => {
    // Stubbed before it mounts: this dialog fires the tag-preference and
    // active-voice queries, and an unstubbed render forwards them through the
    // egress sentinel to the app's real backend.
    mockFetch({ ...shellRoutes(), "/tts": { body: {} } });
    renderWithQueryClient(
      createElement(AppSettingsDialog, { open: true, onOpenChange: vi.fn() }),
    );
    await screen.findByRole("dialog");
    assertOnScale(content(), "AppSettingsDialog");
    expect(apiEscapes(), "the settings dialog reached the real transport")
      .toEqual([]);
  }, 30_000);

  it("the generation settings dialog is on the scale, at the one height", async () => {
    // The 88vh outlier. Its reason was never written down anywhere, and a
    // second vh value is a second answer to one question.
    const user = userEvent.setup();
    mockFetch({ "/settings": { body: settingsFixture } });
    renderWithQueryClient(
      createElement(
        GenerationSettingsProvider,
        null,
        createElement(GenerationSettingsDialog, { selectedModel: modelFixture }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: /sampling/i }));
    await screen.findByRole("dialog");
    assertOnScale(content(), "GenerationSettingsDialog");
    expect(apiEscapes(), "the generation dialog reached the real transport")
      .toEqual([]);
  }, 30_000);

  it("the lightbox is the named exception, and carries nothing the exception does not name", async () => {
    // POSITIVE CONTROL for the exclusion. Excluding a dialog by name is only
    // honest if the thing excluded is bounded on BOTH sides. This used to
    // assert a subset - every class of LIGHTBOX_CLASSES is present - which is
    // exactly what the second mutation for this row walks through: adding
    // `glass` leaves all of them in place and takes the chrome with it.
    //
    // Two assertions now, because one mutation lives at each end. The class
    // list is compared for EQUALITY, which catches a class added at the call
    // site; and the chrome the exception exists to avoid is asserted absent
    // by name, which catches a class added to the constant itself - where
    // equality alone would move with the mutation and stay green.
    //
    // The expected list is DERIVED, never typed: the primitive's own base
    // string is read off a bare DialogContent rendered here and merged with
    // LIGHTBOX_CLASSES through the app's own `cn`, which is what
    // ui/dialog.tsx does. So this follows the primitive and pins the
    // difference rather than a copy of the whole string.
    render(
      createElement(
        Dialog,
        { open: true },
        createElement(DialogContent, null, createElement(DialogTitle, null, "Bare")),
      ),
    );
    await screen.findByRole("dialog");
    const bare = content().className;
    // GROUND for the absence below: an ordinary dialog DOES render the
    // primitive's close button, and it is the one with no `aria-label` of its
    // own (it names itself with an sr-only span instead). Both halves matter,
    // because the lightbox renders a close control of its OWN and the two
    // carry the same `data-slot`.
    const bareCloses = document.querySelectorAll('[data-slot="dialog-close"]');
    expect(
      bareCloses.length,
      "an ordinary dialog no longer renders the primitive's close button",
    ).toBe(1);
    expect(
      bareCloses[0].getAttribute("aria-label"),
      "the primitive's close button grew an aria-label, so the two are no longer distinguishable",
    ).toBeNull();
    cleanup();

    const objectUrl = "blob:lightbox-probe";
    const realCreate = URL.createObjectURL;
    URL.createObjectURL = vi.fn(() => objectUrl);
    try {
      mockFetch({ "/uploads/images/1": { body: {} } });
      renderWithQueryClient(
        createElement(ImageLightbox, {
          attachment: attachmentFixture,
          onClose: vi.fn(),
        }),
      );
      await screen.findByRole("dialog");
      const classes = [...content().classList];
      expect(
        [...classes].sort(),
        "the lightbox carries a class the exception does not name",
      ).toEqual(cn(bare, LIGHTBOX_CLASSES).split(" ").sort());
      // The chrome, by name. These are index.css classes with no production
      // export to import, and the names ARE the claim: a test that read them
      // off the component would go green the moment the component took them
      // on, which is the mutation.
      for (const chrome of ["glass", "glass-dialog", "sidebar-dialog"]) {
        expect(classes, `the lightbox took on the dialog chrome: ${chrome}`)
          .not.toContain(chrome);
      }
      expect(classes, "the lightbox joined the scale's one height")
        .not.toContain(DIALOG_MAX_HEIGHT);
      // `showCloseButton={false}` is the other half of what IP-05 FIX 5
      // names, and nothing held it: putting the primitive's close button back
      // left this file green. "No close button" is the wrong question - the
      // lightbox has one of its own, a solid-backed control pinned to the
      // picture's corner so it never disappears into the image. The right
      // question is whether the primitive's is there BESIDE it, which is what
      // a second node with the same slot means.
      const closes = content().querySelectorAll('[data-slot="dialog-close"]');
      expect(
        closes.length,
        "the primitive's close button is back, beside the lightbox's own",
      ).toBe(1);
      expect(
        closes[0].getAttribute("aria-label"),
        "the one close control on the lightbox is not the lightbox's own",
      ).toBe("Close");
      // And it really is off the scale, which is why it is excluded above.
      const widths = classes.filter((c) => c.startsWith("sm:max-w-"));
      expect(widths).toHaveLength(1);
      expect(WIDTHS.has(widths[0]), "the lightbox is on the scale after all")
        .toBe(false);
      expect(apiEscapes(), "the lightbox reached the real transport").toEqual([]);
    } finally {
      URL.createObjectURL = realCreate;
    }
  }, 30_000);

  it("the scale itself is four widths and one height", () => {
    // The shape of the scale, so a fifth width cannot be added without the
    // decision being made here rather than at a call site.
    expect(Object.keys(DIALOG_WIDTHS)).toHaveLength(4);
    expect(new Set(Object.values(DIALOG_WIDTHS)).size).toBe(4);
    expect(DIALOG_MAX_HEIGHT.startsWith("max-h-")).toBe(true);
  });

  /**
   * What `property` resolves to for the utility class `utility`, read out of
   * the tree it is handed.
   *
   * Takes the TREE rather than closing over `sheet`, so the grounds below can
   * run this same function over a probe whose answer is known. A reader that
   * can only be pointed at the real sheet is a reader nobody has ever seen
   * report anything.
   */
  const declOf = (tree: Root, utility: string, property: string): string | null => {
    const tokens = rootTokens(tree);
    // A class selector as the sheet spells it, with CSS's own escapes taken
    // back off: Tailwind writes `sm:max-w-sm` as `.sm\:max-w-sm`, and a hand
    // built escape on this side is a spelling contest rather than a reading.
    const unescaped = (selector: string): string =>
      selector.split(String.fromCharCode(92)).join("");
    let value: string | null = null;
    tree.walkRules((rule) => {
      const names = resolvedSelectors(rule).map(unescaped);
      if (!names.includes("." + utility)) return;
      // walkDecls rather than each: Tailwind writes the breakpoint as a
      // media query NESTED inside the utility rule, so the declaration is
      // a grandchild and a direct-children scan finds nothing at all.
      rule.walkDecls(property, (node) => {
        value = resolveVars(node.value, tokens).trim();
      });
    });
    return value;
  };

  it("and the one height is the height REF 4.3 tabulates", () => {
    // The presence assertions above hold every dialog to the SAME height and
    // cannot tell which height that is: moving the constant one step takes
    // all six with it and leaves them agreeing with each other. The class
    // name is asked what it resolves to in the compiled sheet, and the answer
    // is compared with the value the design reference wrote down.
    const resolved = declOf(parseSheet(sheet), DIALOG_MAX_HEIGHT, "max-height");
    expect(resolved, `the sheet emits no max-height for ${DIALOG_MAX_HEIGHT}`)
      .not.toBeNull();
    expect(resolved, `${DIALOG_MAX_HEIGHT} is not REF 4.3's one height`)
      .toBe(REF_MAX_HEIGHT);
    // GROUND: the same reader, over a probe carrying a DIFFERENT height, so
    // the match above is a reading rather than a function that agrees with
    // whatever it is handed. The probe's rule is built here rather than taken
    // from the sheet, because the sheet has only the one height in it - which
    // is the claim.
    const other = String(Number(REF_MAX_HEIGHT.replace("vh", "")) + 3) + "vh";
    const probe = parseSheet(`.probe-height{max-height:${other}}`);
    expect(declOf(probe, "probe-height", "max-height"),
      "the height reader cannot report a second value").toBe(other);
    // And it answers nothing for a utility the tree does not carry, so a
    // missing rule cannot read as a match.
    expect(declOf(probe, "probe-absent", "max-height")).toBeNull();
  }, 60_000);

  it("and the four widths are the four widths REF 4.3 tabulates", () => {
    // The shape alone pins nothing: `medium` could move from 512 to 576 with
    // every assertion above still green, because a fifth width is impossible
    // while a WRONG width is free. Each class name is asked what it resolves
    // to in the compiled sheet, and the answer is compared with the number
    // the design reference wrote down.
    const widthOf = (utility: string): number => {
      const value = declOf(parseSheet(sheet), utility, "max-width");
      expect(value, `the sheet emits no max-width for ${utility}`).not.toBeNull();
      const m = /^([\d.]+)rem$/.exec(value!);
      expect(m, `${utility} resolves to ${value}, which is not a rem length`)
        .not.toBeNull();
      return Number(m![1]) * 16;
    };
    for (const [name, utility] of Object.entries(DIALOG_WIDTHS)) {
      expect(widthOf(utility), `${name} (${utility}) is not REF 4.3's width`)
        .toBe(REF_WIDTH_PX[name as keyof typeof DIALOG_WIDTHS]);
    }
    // GROUND: the reader can report a number that is NOT on the table, so
    // the four matches above are readings rather than a function that agrees
    // with whatever it is handed. The lightbox's own width is the one
    // off-scale utility this app compiles, and it is 768.
    const lightboxWidth = LIGHTBOX_CLASSES.split(" ")
      .find((c) => c.startsWith("sm:max-w-"))!;
    expect(Object.values(REF_WIDTH_PX)).not.toContain(widthOf(lightboxWidth));
  }, 60_000);

  it("the primitive's own default is the scale's base", async () => {
    // A scale that leaves the primitive's default out declares its own
    // component off-scale: every DialogContent that sets no width takes it.
    render(
      createElement(
        Dialog,
        { open: true },
        createElement(DialogContent, null, createElement(DialogTitle, null, "Bare")),
      ),
    );
    await screen.findByRole("dialog");
    const widths = [...content().classList].filter((c) => c.startsWith("sm:max-w-"));
    expect(widths).toEqual([DIALOG_WIDTHS.base]);
  }, 30_000);
});
