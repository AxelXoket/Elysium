/**
 * FAZ 4 - choosing an extractor.
 *
 * The panel makes two choices and spends nothing doing it: which model reads
 * the last few turns, and which language its instructions are written in. So
 * the tests here are about the two things a settings screen can get wrong -
 * saying something false about the saved state, and failing to save a change.
 *
 * "Not chosen - suggestions are off" is the string under the most pressure: it
 * tells the user a background job is not running, and it used to be rendered
 * from an unanswered query on every tab switch.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";

const MODELS = {
  models: [
    { id: "cheap/one", provider: "P", prompt_price: 0.06,
      context_length: 131000, endpoints: 3 },
    { id: "lonely/two", provider: "Q", prompt_price: 0.05,
      context_length: 400000, endpoints: 1 },
  ],
};

describe("ExtractionSettings", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("starts with nothing chosen, and says what that means", async () => {
    // No default and no automatic pick: it is the user's API key.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect(await screen.findByText(/suggestions are off/i)).toBeInTheDocument();
  });

  it("warns which models are pinned to one provider", async () => {
    // With provider fallback disabled a single-endpoint model stops working
    // when that one machine does - and the user is the one who has to know.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect(await screen.findByText(/lonely\/two/))
      .toBeInTheDocument();
  });

  it("spends nothing on its own, even with a model chosen", async () => {
    // The panel used to carry a "try it" button that billed the user's key on
    // click. It is gone, and this is the guard that keeps it gone: opening the
    // screen reads the catalogue and the saved choice, and asks for nothing
    // that costs money.
    //
    // Two controls, because an absence assertion on a screen that failed to
    // render passes for the wrong reason. Ground: the panel is really there,
    // with a model selected. Positive: the same fetch spy that reports zero
    // extraction calls does report the reads, so it is not simply blind.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);

    await waitFor(() =>
      expect(screen.getByLabelText(/extraction model/i))
        .toHaveValue("cheap/one"));

    const urls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((c: unknown[]) => String(c[0]));
    expect(urls.some((u) => u.includes("/extract/models"))).toBe(true);
    expect(urls.filter((u) => u.includes("/extract/dry-run"))).toEqual([]);
    // Nothing on this screen fires a POST by itself either; the only writes it
    // makes are the two selects, and both need a user.
    const writes = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .filter((c: unknown[]) =>
        (c[1] as RequestInit | undefined)?.method === "POST");
    expect(writes).toEqual([]);
  });

  it("lets the instruction language be switched", async () => {
    // Untested assumption, shipped as a choice rather than a bet.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/extract/settings": { body: { ok: true } },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);

    // Inert until the saved choice is known - see the test below for why
    // that matters more here than it looks.
    const select = await screen.findByLabelText(/instruction language/i);
    await waitFor(() => expect(select).toBeEnabled());
    await user.selectOptions(select, "tr");

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/extract/settings")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ prompt_language: "tr" });
    });
  });

  it("never says suggestions are off before it knows", async () => {
    // "Not chosen - suggestions are off" is the one string in this panel that
    // must not be shown falsely: it tells the user a background job is not
    // running. Rendered from `data?.model_id ?? ""` while the query was still
    // in flight, it appeared on every single tab switch - including for a user
    // who HAD chosen a model.
    let release: () => void = () => {};
    const held = new Promise<void>((r) => { release = r; });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body),
                   { status: 200,
                     headers: { "Content-Type": "application/json" } });

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/vault/status"))
        return json({ initialized: true, unlocked: true });
      if (url.includes("/extract/models")) return json(MODELS);
      if (url.includes("/extract/settings")) {
        await held;
        return json({ model_id: "cheap/one", prompt_language: "en" });
      }
      return json({});
    }));

    renderWithQueryClient(<ExtractionSettings />);

    expect(await screen.findByText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText(/suggestions are off/i)).not.toBeInTheDocument();

    release();
    await waitFor(() =>
      expect(screen.getByLabelText(/extraction model/i))
        .toHaveValue("cheap/one"));
  });

  it("says so when the list could not be fetched", async () => {
    // An empty picker and a failed fetch look identical, and they mean
    // opposite things: "no model qualifies" versus "we could not ask".
    mockFetch({
      "/notebook/extract/models": { status: 502,
                                    body: { detail: "openrouter_unreachable" } },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect((await screen.findByRole("alert")).textContent)
      .toMatch(/could not be fetched/i);
  });
});
