/**
 * A setting that could not be read is never drawn
 * as a value, and "Try again" keeps the keyboard.
 *
 * Two controls in the Notes tab hang off a saved value: the safeword box
 * and the "keep suggestions without asking" switch. A two-state control
 * cannot say "unknown": a switch drawn off reads (and is announced) as off,
 * an empty box reads as "no safeword". So until the value is read the
 * control is NOT DRAWN; a line says it could not be loaded, with Try again,
 * and the line that explains "Off, ..." waits for the value too.
 *
 * Driven over the network boundary: the route fails, the reader sees the
 * line, presses "Try again" from the keyboard, the route answers, the
 * control arrives and takes the keyboard.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { SETTING_RETRY, SETTING_UNREAD } from "@/components/notebook/noteProse";
import { AUTO_ACCEPT_OFF } from "@/components/notebook/noteProse";
import { SAFEWORD_OFF } from "@/components/notebook/limitsProse";
import { says } from "@/test/helpers/prose";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";

/** A route whose answer can be changed between requests: the mock reads
 *  `status` and `body` when it answers. */
function flaky(good: unknown) {
  const state = { failing: true };
  return {
    state,
    route: {
      get status() { return state.failing ? 500 : 200; },
      get body() { return state.failing ? { detail: "internal_error" } : good; },
    },
  };
}

const retryButton = () => screen.findByRole("button", { name: SETTING_RETRY });

describe("the safeword box when the saved word cannot be read", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function mount(word: ReturnType<typeof flaky>) {
    mockFetch({
      "/notebook/safeword": word.route,
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    });
    return renderWithQueryClient(<BoundaryPanel />);
  }

  it("says it could not be loaded, and draws no box that could pass for a value", async () => {
    mount(flaky({ word: "mor" }));
    const line = await screen.findByText(SETTING_UNREAD);
    await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
    expect(screen.queryByLabelText(/^safeword$/i)).toBeNull();
    // No box, so no instruction about leaving it empty.
    expect(screen.queryByText(says(SAFEWORD_OFF))).toBeNull();
  });

  it("Try again from the keyboard reads it, and the box arrives with the keyboard on it", async () => {
    const user = userEvent.setup();
    const word = flaky({ word: "mor" });
    mount(word);
    const retry = await retryButton();
    await waitFor(() => expect(hiddenFromReaders(retry)).toBe(false));
    // A retry that fails again keeps the keyboard on the button.
    retry.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(retry).not.toHaveAttribute("aria-disabled"));
    expect(retry).toHaveFocus();
    word.state.failing = false;
    await user.keyboard("{Enter}");
    const box = await screen.findByLabelText(/^safeword$/i);
    await waitFor(() => expect(box).toHaveValue("mor"));
    await waitFor(() => expect(box).toHaveFocus());
    expect(hiddenFromReaders(screen.getByText(SETTING_UNREAD))).toBe(true);
  });

  it("a slow retry keeps its line and the keyboard while it is out, and after it fails", async () => {
    // The query reads `pending`, not `error`, while it fetches again: a line
    // drawn on `isError` alone closed under the pressed button.
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "/notebook/safeword": { status: 500, body: { detail: "internal_error" } },
      "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      "/notebook/boundaries": { body: { boundaries: [], use_global: true } },
    });
    renderWithQueryClient(<BoundaryPanel />);
    const retry = await retryButton();
    await waitFor(() => expect(hiddenFromReaders(retry)).toBe(false));
    const release = delayRoute(fetchMock, "GET", "/notebook/safeword",
      { status: 500, body: { detail: "internal_error" } });
    retry.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(retry).toHaveAttribute("aria-disabled", "true"));
    // Mid-fetch: the line is still there and the button still has focus.
    expect(hiddenFromReaders(screen.getByText(SETTING_UNREAD))).toBe(false);
    expect(retry).toHaveFocus();
    release();
    await waitFor(() => expect(retry).not.toHaveAttribute("aria-disabled"));
    expect(retry).toHaveFocus();
    expect(hiddenFromReaders(screen.getByText(SETTING_UNREAD))).toBe(false);
  });

  it("GROUND: a word that was read shows no such line", async () => {
    const word = flaky({ word: "mor" });
    word.state.failing = false;
    mount(word);
    const box = await screen.findByLabelText(/^safeword$/i);
    await waitFor(() => expect(box).toHaveValue("mor"));
    expect(hiddenFromReaders(screen.getByText(SETTING_UNREAD))).toBe(true);
    // POSITIVE CONTROL for the absence above: beside a box, it is said.
    expect(screen.getByText(says(SAFEWORD_OFF))).toBeTruthy();
  });
});

describe("the auto-accept switch when its saved value cannot be read", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function statusBody() {
    return {
      stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
      spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
      worker: { state: "closed", failures: 0, total_failures: 0, queued: 0,
        dropped_offers: 0, runs: 0, batch_size: 20,
        backlog: { chats: 0, messages: 0 }, backfill: null },
      daily_cap: 60,
    };
  }

  function mount(auto: ReturnType<typeof flaky>) {
    mockFetch({
      "/notebook/worker": { body: statusBody() },
      "/notebook/auto-accept": auto.route,
    });
    return renderWithQueryClient(<WorkerPanel />);
  }

  const theSwitch = () =>
    screen.queryByRole("switch", { name: /keep suggestions without asking/i });

  it("draws no switch and no 'Off, ...' line, and says it could not be loaded", async () => {
    // The stored value is ON: a switch that guesses would show one of two
    // states, and neither is known.
    mount(flaky({ enabled: true, effective: true, overridden: false }));
    const line = await screen.findByText(SETTING_UNREAD);
    await waitFor(() => expect(hiddenFromReaders(line)).toBe(false));
    expect(theSwitch()).toBeNull();
    expect(hiddenFromReaders(screen.getByText(AUTO_ACCEPT_OFF))).toBe(true);
  });

  it("Try again from the keyboard reads it, and the switch arrives with the keyboard on it", async () => {
    const user = userEvent.setup();
    const auto = flaky({ enabled: true, effective: true, overridden: false });
    mount(auto);
    const retry = await retryButton();
    await waitFor(() => expect(hiddenFromReaders(retry)).toBe(false));
    auto.state.failing = false;
    retry.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(theSwitch()).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(theSwitch()).toHaveFocus());
    expect(hiddenFromReaders(screen.getByText(AUTO_ACCEPT_OFF))).toBe(false);
  });

  it("GROUND: a value that was read draws the switch and no line", async () => {
    const auto = flaky({ enabled: false, effective: false, overridden: false });
    auto.state.failing = false;
    mount(auto);
    await waitFor(() => expect(theSwitch()).toHaveAttribute("aria-checked", "false"));
    expect(hiddenFromReaders(screen.getByText(SETTING_UNREAD))).toBe(true);
  });
});
