/**
 * rightPanelTabs.ts - the whole right panel, every tab showing what it can
 * show, for the suites that read every line in it.
 *
 * The Notes tab is the fixture in notesTab.ts, unchanged, so a state added
 * there is read here too. The other three get what puts their words on
 * screen: a stored API key (so "Test stored key" is there), a model list
 * with a model chosen and one model that makes pictures (so a capability
 * that goes out, and its filter, are drawn beside one that comes in), a
 * persona on file and in use, and the chat the panel is open on with its
 * character (so the context gauge is drawn).
 *
 * The tabs are walked by their triggers, read off the rendered panel rather
 * than listed here: a fifth tab is walked on the day it lands.
 */
import { createElement } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { expect } from "vitest";
import { RightPanel } from "@/components/layout/RightPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import {
  characterFixture,
  chatFixture,
  modelFixture,
  modelListFixture,
  personaFixture,
  proxyHealthFixture,
  settingsFixture,
} from "@/test/mocks/fixtures";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import {
  NOTES_TAB_CHAT,
  notesTabRoutes,
  settleNotesTab,
  type NotesTabOptions,
} from "@/test/helpers/notesTab";

/** A model that makes pictures as well as reading words, and whose list
 *  entry says nothing about the parameters it takes: chosen, it is the one
 *  the generation card says it cannot tell about. */
export const DRAWING_MODEL = {
  ...modelFixture,
  id: "vendor/draws-pictures",
  name: "Draws Pictures",
  canonical_slug: "vendor/draws-pictures",
  input_modalities: ["text"],
  output_modalities: ["text", "image"],
  supported_parameters: [],
};

/** The states the four tabs show only when something is set or has gone
 *  wrong, on top of the Notes tab's own. Each puts lines on screen the
 *  rest state never draws. */
export interface RightPanelOptions extends NotesTabOptions {
  /** A proxy is on file: the proxy's health and "Remove proxy" are drawn. */
  proxyConfigured?: boolean;
  /** The model list cannot be fetched: the Models tab says so. */
  modelListFails?: boolean;
  /** Stop sequences are saved, so the generation card reads as custom. */
  stopSequences?: string[];
}

/** The fewest tabs the panel can have and still be the panel. The walk reads
 *  them off the triggers; this is what stops a walk that found none from
 *  passing as a walk of everything. */
export const RIGHT_PANEL_MIN_TABS = 4;

/** Every route the four tabs ask for. The mock matches by substring in
 *  insertion order, so the Notes tab's routes come first and `/settings`,
 *  which would also answer `/settings/proxy/health`, comes last. */
export function rightPanelRoutes(
  options: RightPanelOptions = {},
): Record<string, { status?: number; body: unknown }> {
  // The Notes tab's own `/settings` answer is left out: the one below
  // answers for every tab.
  const notes = Object.fromEntries(
    Object.entries(notesTabRoutes(options)).filter(([route]) => route !== "/settings"));
  const privacy = { on: settingsFixture.screen_privacy_enabled ?? false };
  return {
    ...notes,
    "/settings/proxy/health": { body: proxyHealthFixture },
    // The screen-privacy switch turns over each time this body is read: the
    // mock reads a route's body when it answers, and one press of the switch
    // is one POST, so the next read of the settings sees the other state.
    // What the request asked for is not looked at.
    "POST /settings/screen-privacy": { get body() {
      privacy.on = !privacy.on;
      return { ok: true };
    } },
    "/models/openrouter": options.modelListFails
      ? { status: 500, body: { detail: "internal_error" } }
      : { body: {
        ...modelListFixture, count: 2, models: [modelFixture, DRAWING_MODEL],
      } },
    "/personas": { body: [personaFixture] },
    "/tts/voice-mode": { body: { enabled: false, active: false, prompt_chars: 0 } },
    // The context gauge reads the prompt modules and the layer setting (the
    // prompt layer). Its settings route goes first: the mock matches by
    // substring in insertion order, and "/prompt-modules" would answer it too.
    "/prompt-modules/settings": {
      body: { prompt_layer_enabled: false, reply_length_words: null },
    },
    "/prompt-modules": { body: { modules: [] } },
    "/chats": { body: [{ ...chatFixture, id: NOTES_TAB_CHAT }] },
    "/characters": { body: [characterFixture] },
    "/settings": { get body() {
      return {
        ...settingsFixture,
        selected_persona_id: personaFixture.id,
        selected_model_id: modelFixture.id,
        screen_privacy_enabled: privacy.on,
        proxy_configured: options.proxyConfigured ?? settingsFixture.proxy_configured,
        stop_sequences: options.stopSequences ?? settingsFixture.stop_sequences,
      };
    } },
  };
}

/** Opens the panel on the tab the shell opens on, with the fixture's model
 *  chosen: the shell hands the store the vault's choice, and the panel reads
 *  it from there. */
export function mountRightPanel(options: RightPanelOptions = {}) {
  useUiStore.setState({
    selectedChatId: NOTES_TAB_CHAT, selectedModelId: modelFixture.id,
    activeRightPanelTab: "models", rightPanelCollapsed: false,
  });
  const fetchMock = mockFetch(rightPanelRoutes(options));
  const rendered = renderWithQueryClient(createElement(RightPanel), {
    wrapper: GenerationSettingsProvider,
  });
  return { fetchMock, ...rendered };
}

/** Whether the open tab is the Notes tab: the one the strip is in. */
export function isNotesTab(panel: Element): boolean {
  return panel.querySelector('[data-testid="backfill-strip"]') !== null;
}

/** Opens one tab by its trigger and waits until it has drawn: every query it
 *  started has an answer, and the Notes tab has settled the way its own
 *  fixture settles. */
export async function openRightPanelTab(
  user: { click: (element: Element) => Promise<void> },
  name: string,
  client: QueryClient,
): Promise<void> {
  await user.click(screen.getByRole("tab", { name }));
  await waitFor(() => {
    expect(screen.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
  });
  await waitFor(() => expect(client.isFetching(), `the ${name} tab is still fetching`).toBe(0));
  if (isNotesTab(screen.getByRole("tabpanel"))) await settleNotesTab();
  await motionAtRest(screen.getByRole("tabpanel"));
}

/** The opacity each element in `within` carries in its style attribute,
 *  where motion writes an entrance while it runs. */
const inlineOpacities = (within: Element): string =>
  [...within.querySelectorAll<HTMLElement>("[style]")].map((el) => el.style.opacity).join("|");

/** Waits until motion has laid every entrance in `within` down: two
 *  readings of every inline opacity, taken a few frames apart, agree. A tab
 *  whose answers were already in hand is on screen the moment it is clicked,
 *  and its entrance is still at its first frame then. */
export async function motionAtRest(within: Element): Promise<void> {
  await waitFor(async () => {
    const before = inlineOpacities(within);
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(inlineOpacities(within), "an entrance is still moving").toBe(before);
  });
}

/** The tabs the panel renders, by the words on their triggers. */
export function rightPanelTabNames(): string[] {
  const names = screen.getAllByRole("tab").map((t) => (t.textContent ?? "").trim());
  expect(names.length, "the panel rendered fewer tabs than it has")
    .toBeGreaterThanOrEqual(RIGHT_PANEL_MIN_TABS);
  return names;
}

/** The store back to the tab the shell opens on, for an `afterEach`. */
export function closeRightPanel() {
  useUiStore.setState({
    selectedChatId: null, selectedModelId: null, activeRightPanelTab: "models",
    rightPanelCollapsed: false,
  });
}
