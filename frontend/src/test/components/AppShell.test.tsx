import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/layout/AppShell";
import { queryClient } from "@/lib/query/queryClient";
import { useUiStore } from "@/lib/store/uiStore";
import { readFileSync } from "fs";
import path from "path";
import { mockFetch } from "@/test/mocks/api";
import {
  settingsFixture,
  proxyHealthFixture,
  characterFixture,
  chatFixture,
  modelListFixture,
  messageFixture,
} from "@/test/mocks/fixtures";

describe("the app shell comes up", () => {
  // Mock fetch so these synchronous render assertions neither hit a live dev
  // backend nor leak unhandled rejections into the shared singleton client.
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      "/chats": { body: [chatFixture] },
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
  });

  it("renders the Elysium heading", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    // "Elysium" now renders as the brand wordmark in more than one place
    // (sidebar header + empty-state welcome), so assert presence, not unique.
    expect(screen.getAllByText("Elysium").length).toBeGreaterThan(0);
  });

  it("renders the sidebar with Characters section", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByText("Characters")).toBeInTheDocument();
  });

  // Updated from "Settings" to "Secrets" (Phase 6E-A tab rename).
  //
  // The arrow in this comment used to be the literal characters "a-hat, dagger,
  // right-quote": UTF-8 bytes for an arrow that were decoded as Latin-1 once
  // and written back. The file is valid UTF-8, so nothing complained; only a
  // reader would notice. Written as ASCII now so it cannot happen twice.
  //
  // KADEME 17a also folded "T-73: renders Secrets tab" into this test. It was
  // the same render under the same beforeEach asserting the same
  // getByRole("tab", {name: /secrets/i}) - not a second angle on the tab, a
  // second copy of this test.
  // FAZ 3 renamed the LABEL to "Security" - the panel now holds the switches
  // that protect the secrets as well as the secrets themselves. The stored
  // VALUE is still "secrets", so no persist migration was needed; asserting
  // all four labels together means the next tab added has to change this line.
  it("renders the right panel with all four tabs", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    for (const name of [/models/i, /security/i, /persona/i, /notes/i]) {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    }
  });

  it("renders the composer", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-72: Right panel renders Models tab
  it("T-72: renders Models tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /models/i })).toBeInTheDocument();
  });

  // T-74: Right panel renders Persona tab
  it("T-74: renders Persona tab", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /persona/i })).toBeInTheDocument();
  });

  // T-75: Sidebar footer shows the app version (injected from package.json)
  it("T-75: sidebar footer shows the app version", () => {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
    expect(screen.getByText(/^v\d+\.\d+/i)).toBeInTheDocument();
  });
});

describe("AppShell stale selection reconciliation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
      selectedModelId: null,
    });
  });

  it("clears a stale persisted chat selection once server data loads", async () => {
    queryClient.clear();
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      "/chats": { body: [chatFixture] },
    });
    useUiStore.setState({
      selectedChatId: 999, // persisted id that no longer exists server-side
      selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o",
    });

    render(
      <Providers>
        <AppShell />
      </Providers>,
    );

    await waitFor(() => {
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });
    // Valid selections survive
    expect(useUiStore.getState().selectedCharacterId).toBe(characterFixture.id);
    expect(useUiStore.getState().selectedModelId).toBe("openai/gpt-4o");
  });
});

describe("focus mode panel collapse", () => {
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      "/chats": { body: [chatFixture] },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    useUiStore.setState({ sidebarCollapsed: false, rightPanelCollapsed: false });
  });

  function renderShell() {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
  }

  /** The panel element itself, found through content it owns. */
  function sidebarAside(): HTMLElement {
    const el = document.querySelector("aside.glass-dark");
    if (!el) throw new Error("sidebar aside not found");
    return el as HTMLElement;
  }

  function rightAside(): HTMLElement {
    const el = document.querySelector("aside.glass-right");
    if (!el) throw new Error("right panel aside not found");
    return el as HTMLElement;
  }

  // A smoke test, NOT a ground control - the earlier comment here claimed it
  // passed before the feature, which cannot be true: the button it queries did
  // not exist. The real ground controls are the in-test ones below (open-state
  // tab stops, open-state chevron), which measure a before AND an after.
  it("offers both handles and shows the panels by default", () => {
    renderShell();
    expect(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Collapse right panel" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Characters")).toBeInTheDocument();
  });

  it("collapses and re-expands the sidebar through one button", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(sidebarAside().style.width).toBe("0px");
    // ONE button whose icon and label swap, never two stacked - so the collapse
    // label must be gone, not merely joined by an expand label.
    expect(
      screen.queryByRole("button", { name: "Collapse sidebar" }),
    ).toBeNull();
    const handle = screen.getByRole("button", { name: "Expand sidebar" });
    expect(handle).toHaveAttribute("aria-expanded", "false");

    await user.click(handle);
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
    expect(sidebarAside().style.width).toBe("var(--sidebar-width)");
  });

  it("collapses and re-expands the right panel through one button", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(
      screen.getByRole("button", { name: "Collapse right panel" }),
    );

    expect(useUiStore.getState().rightPanelCollapsed).toBe(true);
    expect(rightAside().style.width).toBe("0px");
    expect(
      screen.queryByRole("button", { name: "Collapse right panel" }),
    ).toBeNull();
    const handle = screen.getByRole("button", { name: "Expand right panel" });
    expect(handle).toHaveAttribute("aria-expanded", "false");

    await user.click(handle);
    expect(useUiStore.getState().rightPanelCollapsed).toBe(false);
    expect(rightAside().style.width).toBe("var(--right-panel-width)");
  });

  it("closes each side independently", async () => {
    // The two flags are deliberately separate so one side can close without
    // the other. The first version of this test drove the STORE directly
    // (`useUiStore.setState({ sidebarCollapsed: true })`), which bypasses the
    // toggle actions - the exact place a shared flag would live. Mutating
    // toggleRightPanel to flip both flags left it green. It drives the real
    // buttons now, in both directions.
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(
      useUiStore.getState().rightPanelCollapsed,
      "collapsing the sidebar took the right panel with it",
    ).toBe(false);
    expect(rightAside().style.width).toBe("var(--right-panel-width)");

    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    await user.click(
      screen.getByRole("button", { name: "Collapse right panel" }),
    );
    expect(useUiStore.getState().rightPanelCollapsed).toBe(true);
    expect(
      useUiStore.getState().sidebarCollapsed,
      "collapsing the right panel took the sidebar with it",
    ).toBe(false);
    expect(sidebarAside().style.width).toBe("var(--sidebar-width)");
  });

  /**
   * The tab order a BROWSER would compute.
   *
   * jsdom does not implement `inert`: React writes the attribute, jsdom
   * ignores it, and neither `.focus()` nor userEvent.tab() skips the subtree -
   * measured, a tab walk steps straight into an inert div. So the ring cannot
   * be DRIVEN here, it has to be computed: the standard focusable set, minus
   * anything under an `[inert]` ancestor. That subtraction is the whole point.
   */
  const FOCUSABLE = [
    "input:not([type=hidden]):not([disabled])",
    "button:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "a[href]",
    "[tabindex]:not([disabled])",
  ].join(", ");

  function tabbableWithin(root: Element): HTMLElement[] {
    return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (el) =>
        Number(el.getAttribute("tabindex") ?? 0) >= 0 && !el.closest("[inert]"),
    );
  }

  // The regression Collapse.tsx is named for, asserted for BOTH panels. An
  // `inert` attribute assertion is not this: deleting `inert` from the sidebar
  // left every test green, because the only a11y assertion covered the right
  // panel and it keyed on aria-hidden.
  it.each([
    ["sidebar", "Collapse sidebar", "Expand sidebar", () => sidebarAside()],
    [
      "right panel",
      "Collapse right panel",
      "Expand right panel",
      () => rightAside(),
    ],
  ])(
    "takes the collapsed %s out of the tab order without unmounting it",
    async (_name, closeLabel, openLabel, aside) => {
      const user = userEvent.setup();
      renderShell();

      // GROUND CONTROL: open, the panel really owns tab stops - so an empty
      // set later measures the collapse and not an empty render.
      expect(tabbableWithin(aside()).length).toBeGreaterThan(0);

      await user.click(screen.getByRole("button", { name: closeLabel }));

      // Still mounted, which is the point: scroll position and typed search
      // text survive. Without this the claim below passes for free.
      expect(aside().querySelectorAll(FOCUSABLE).length).toBeGreaterThan(0);
      expect(tabbableWithin(aside())).toEqual([]);

      // POSITIVE CONTROL: reopening puts the stops back.
      await user.click(screen.getByRole("button", { name: openLabel }));
      expect(tabbableWithin(aside()).length).toBeGreaterThan(0);
    },
  );

  it("names both landmarks and drops the collapsed one", async () => {
    // Two unnamed asides both map to role complementary, so a screen reader's
    // landmark list reads "complementary, complementary, main" with no way to
    // tell them apart. And with the state on an inner wrapper the landmark
    // survived the collapse as an EMPTY region to walk into.
    const user = userEvent.setup();
    renderShell();

    const named = screen
      .getAllByRole("complementary")
      .map((el) => el.getAttribute("aria-label"));
    expect(named).toHaveLength(2);
    expect(named.every((n) => n != null && n.length > 0)).toBe(true);
    expect(new Set(named).size, "the two landmarks share a name").toBe(2);

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(screen.getAllByRole("complementary")).toHaveLength(1);
  });

  it("shows no arrow once the panel is closed", async () => {
    // While a panel is shut its handle is the ONLY way back, so it must not
    // look like the affordance that shut it. Lucide names its icons in the
    // class list, which is the only handle a DOM test has on which glyph is
    // rendered - a chevron here would mean the closed state is wearing the
    // open state's clothes.
    const user = userEvent.setup();
    renderShell();

    const glyph = () =>
      screen.getByRole("button", { name: /sidebar/ }).querySelector("svg");

    // GROUND CONTROL: open, it IS a chevron.
    expect(glyph()?.getAttribute("class")).toMatch(/chevron/);

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(glyph()?.getAttribute("class")).not.toMatch(/chevron/);
    expect(glyph()?.getAttribute("class")).toMatch(/messages-square/);
  });

  it("points the toggle at the panel it controls", () => {
    renderShell();
    expect(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    ).toHaveAttribute("aria-controls", "es-sidebar");
    expect(
      screen.getByRole("button", { name: "Collapse right panel" }),
    ).toHaveAttribute("aria-controls", "es-right-panel");
  });

  it("marks the collapsed state on the handle for its two-shape styling", async () => {
    // `data-collapsed` is what drives the whole open/closed appearance in CSS
    // (rounded chevron vs hard-edged square carrying the panel's subject).
    // Nothing tested it, so the mechanism the feature is named for could break
    // silently.
    const user = userEvent.setup();
    renderShell();

    const handle = () => screen.getByRole("button", { name: /sidebar/ });
    expect(handle()).toHaveAttribute("data-collapsed", "false");
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(handle()).toHaveAttribute("data-collapsed", "true");
  });

  // The accessibility half, and the reason this is not a plain `display: none`:
  // the panel stays MOUNTED so scroll position and typed search text survive a
  // toggle, which means its controls would otherwise still be tabbable behind a
  // zero width. That exact shape has bitten this app before (see Collapse.tsx).
  it("keeps the collapsed panel mounted but out of the accessibility tree", async () => {
    const user = userEvent.setup();
    renderShell();

    const tab = screen.getByRole("tab", { name: /models/i });
    expect(document.body.contains(tab)).toBe(true);

    await user.click(
      screen.getByRole("button", { name: "Collapse right panel" }),
    );

    // Still in the DOM - not unmounted.
    expect(document.body.contains(tab)).toBe(true);
    // Hidden from assistive technology and from the tab order. Both live on
    // the ASIDE, not on an inner wrapper: one level down, the landmark itself
    // survived the collapse as an empty region for a screen reader to enter.
    expect(rightAside()).toHaveAttribute("aria-hidden", "true");
    expect(rightAside()).toHaveAttribute("inert");
    // POSITIVE CONTROL: the same query is clean while the panel is open, so
    // this is measuring the collapse and not a permanently hidden panel.
    await user.click(screen.getByRole("button", { name: "Expand right panel" }));
    expect(rightAside()).not.toHaveAttribute("aria-hidden");
    expect(rightAside()).not.toHaveAttribute("inert");
  });
});

/**
 * The holes a mutation pass found in the suite above.
 *
 * Seven of fifteen mutations survived it: the chevron could point the wrong
 * way, both closed handles could wear the same icon, either portal dismissal
 * could be deleted, the message gutter could go to zero, and the panel id the
 * hover reveal depends on could be renamed - all with every test green. Each
 * test here was checked to fail on its own mutation and on nothing else.
 */
describe("focus mode panel collapse - the gaps", () => {
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
      "/models/openrouter": { body: modelListFixture },
      "/personas": { body: [] },
      // BEFORE "/chats": mockFetch matches by substring, first key wins.
      "/chats/1/messages": { body: [messageFixture] },
      "/chats": { body: [chatFixture] },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    useUiStore.setState({
      sidebarCollapsed: false,
      rightPanelCollapsed: false,
      selectedCharacterId: null,
      selectedChatId: null,
    });
  });

  function renderShell() {
    render(
      <Providers>
        <AppShell />
      </Providers>,
    );
  }

  function sidebarAside(): HTMLElement {
    const el = document.querySelector("aside.glass-dark");
    if (!el) throw new Error("sidebar aside not found");
    return el as HTMLElement;
  }

  const glyphOf = (name: string | RegExp) =>
    screen.getByRole("button", { name }).querySelector("svg")
      ?.getAttribute("class") ?? "";

  it("points each open chevron at the panel it will close", () => {
    // The existing glyph test matches /chevron/, which BOTH directions
    // satisfy - so a handle pointing away from its own panel was invisible.
    renderShell();
    expect(glyphOf("Collapse sidebar")).toMatch(/chevron-left/);
    expect(glyphOf("Collapse sidebar")).not.toMatch(/chevron-right/);
    expect(glyphOf("Collapse right panel")).toMatch(/chevron-right/);
    expect(glyphOf("Collapse right panel")).not.toMatch(/chevron-left/);
  });

  it("gives each closed handle its own panel's subject", async () => {
    // The existing test only inspects the LEFT handle, so collapsing both
    // sides onto one icon stayed green.
    const user = userEvent.setup();
    renderShell();

    // GROUND CONTROL: while open, neither handle wears a subject icon.
    expect(glyphOf("Collapse sidebar")).not.toMatch(
      /messages-square|sliders-horizontal/,
    );

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    await user.click(
      screen.getByRole("button", { name: "Collapse right panel" }),
    );

    expect(glyphOf("Expand sidebar")).toMatch(/messages-square/);
    expect(glyphOf("Expand right panel")).toMatch(/sliders-horizontal/);
    expect(
      glyphOf("Expand right panel"),
      "both closed handles wear the same icon",
    ).not.toMatch(/messages-square/);
  });

  it("closes a portaled row menu when the sidebar collapses by keyboard", async () => {
    const user = userEvent.setup();
    useUiStore.setState({ selectedCharacterId: characterFixture.id });
    renderShell();

    const trigger = await screen.findByRole("button", {
      name: /^Open chat actions for /,
    });
    await user.click(trigger);

    // GROUND CONTROL: the menu is open and genuinely OUTSIDE the panel, so
    // `inert` on the aside could never reach it.
    const menu = screen.getByRole("menu");
    expect(sidebarAside().contains(menu)).toBe(false);

    // POSITIVE CONTROL: a keyboard activation that does not collapse the
    // sidebar leaves the menu alone, so the close below is caused by the
    // collapse and not by any Enter press.
    screen.getByRole("button", { name: "Collapse right panel" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.queryByRole("menu")).not.toBeNull();

    // Keyboard, not click: a click fires pointerdown, which the popup's own
    // outside-click handler already eats - a click-driven version of this
    // passes with the dismissal deleted and proves nothing.
    screen.getByRole("button", { name: "Collapse sidebar" }).focus();
    await user.keyboard("{Enter}");

    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes the portaled persona menu when the sidebar collapses by keyboard", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Change persona" }));
    const menu = screen.getByRole("menu", { name: "Select persona" });
    expect(sidebarAside().contains(menu)).toBe(false);

    screen.getByRole("button", { name: "Collapse right panel" }).focus();
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("menu", { name: "Select persona" }),
    ).not.toBeNull();

    screen.getByRole("button", { name: "Collapse sidebar" }).focus();
    await user.keyboard("{Enter}");
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(screen.queryByRole("menu", { name: "Select persona" })).toBeNull();
  });

  it("wires each handle to the element it actually controls", () => {
    // The existing aria-controls test compares against a RETYPED string, so
    // renaming the aside's id left it green while both the screen-reader
    // relationship AND the CSS hover reveal (which selects the same id) broke.
    renderShell();
    const pairs: [string, Element | null][] = [
      ["Collapse sidebar", document.querySelector("aside.glass-dark")],
      ["Collapse right panel", document.querySelector("aside.glass-right")],
    ];
    for (const [label, aside] of pairs) {
      const id = screen
        .getByRole("button", { name: label })
        .getAttribute("aria-controls");
      expect(id, `${label} carries no aria-controls`).toBeTruthy();
      expect(
        document.getElementById(id!),
        `aria-controls="${id}" resolves to nothing`,
      ).toBe(aside);
    }
  });

  it("pads the message column with the shared handle gutter", async () => {
    useUiStore.setState({
      selectedCharacterId: characterFixture.id,
      selectedChatId: chatFixture.id,
    });
    renderShell();

    const body = await screen.findByText(messageFixture.content);
    expect(
      body.closest(".chat-gutter"),
      "no .chat-gutter ancestor: the message column is not clearing the handles",
    ).not.toBeNull();
    // POSITIVE CONTROL: the class is not simply everywhere.
    expect(document.querySelectorAll(".chat-gutter").length).toBeLessThan(4);
  });
});

/**
 * The gutter has to CLEAR the handle, and that is arithmetic over three
 * tokens rather than anything jsdom can observe: index.css is never loaded
 * into the test DOM, and getComputedStyle returns a calc() custom property
 * verbatim instead of resolving it. So the numbers are read out of the
 * stylesheet and summed. Not a behavioural test, and said so plainly - it is
 * the only thing in this environment that catches the gutter going to zero.
 */
describe("chat gutter clears the panel handles", () => {
  const css = readFileSync(
    path.resolve(__dirname, "../../index.css"),
    "utf-8",
  );

  function tokenPx(name: string): number {
    const m = css.match(new RegExp(`--${name}:\\s*([0-9.]+)px\\s*;`));
    if (!m) throw new Error(`token --${name} is not declared as a px length`);
    return Number(m[1]);
  }

  function resolvePx(expr: string): number {
    const body = expr.trim().replace(/^calc\(/, "").replace(/\)$/, "");
    return body.split("+").reduce((sum, term) => {
      const t = term.trim();
      const ref = t.match(/^var\(--([a-z0-9-]+)\)$/);
      if (ref) return sum + tokenPx(ref[1]);
      const lit = t.match(/^([0-9.]+)px$/);
      if (!lit) throw new Error(`unsupported term in --chat-gutter: "${t}"`);
      return sum + Number(lit[1]);
    }, 0);
  }

  it("leaves the message column clear of a closed handle", () => {
    const decl = css.match(/--chat-gutter:\s*([^;]+);/s);
    expect(decl, "--chat-gutter is not declared in index.css").not.toBeNull();

    const gutter = resolvePx(decl![1]);
    const inset = tokenPx("panel-toggle-inset");
    const closed = tokenPx("panel-toggle-size-closed");

    // The handle occupies inset..inset+closed from the canvas edge. Anything
    // at or below that runs a bubble under the only way back into a hidden
    // panel.
    expect(
      gutter,
      `gutter ${gutter}px does not clear the ${inset}+${closed}px handle`,
    ).toBeGreaterThan(inset + closed);
  });
});
