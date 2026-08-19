/**
 * FAZ 4 - choosing an extractor, and the dry run.
 *
 * The dry run is the answer to the one question that could not be settled by
 * reasoning: whether a small model reads the user's Turkish well enough. So
 * the tests here are mostly about whether the screen shows enough to JUDGE
 * that - the output beside the source, the count of what was silently
 * discarded, and what it cost.
 *
 * A dry run that shows only its successes would be worse than no dry run: it
 * would look like a clean result on a model that dropped half of what it read.
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

function dryRunBody(over: Record<string, unknown> = {}) {
  return {
    model_id: "cheap/one",
    prompt_language: "en",
    source: "user: kardesi degirmenin sahibi",
    raw: '{"facts": []}',
    proposals: [],
    dropped: 0,
    failure: null,
    usage: { tokens_in: 900, tokens_out: 40, cost: 0.00007,
             request_id: "gen-1", finish_reason: "stop" },
    ...over,
  };
}

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

  it("cannot be tried until a model is chosen", async () => {
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect(await screen.findByRole("button", { name: /try it/i }))
      .toBeDisabled();
  });

  it("shows the output NEXT TO what it read", async () => {
    // The whole point. A result with no source to compare it against is a
    // number, not evidence - and the six failure shapes are only visible in
    // the comparison.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": {
        body: dryRunBody({
          proposals: [{ text: "Her brother owns the mill.",
                        evidence: "kardesi degirmenin sahibi",
                        kind: "fact", durability: "permanent",
                        importance: 2, supersedes: null }],
        }),
      },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);

    await user.click(await screen.findByRole("button", { name: /try it/i }));

    const panel = await screen.findByTestId("dry-run-result");
    expect(panel.textContent).toContain("Her brother owns the mill.");
    // The quote stayed in the transcript's own language, which is what makes
    // the grounding check possible at all.
    expect(panel.textContent).toContain("kardesi degirmenin sahibi");
    expect(panel.textContent).toMatch(/nothing was saved/i);
  });

  it("says how many were discarded before the user saw them", async () => {
    // A dry run showing only its successes would read as a clean result on a
    // model that invented half its quotes.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": { body: dryRunBody({ dropped: 3 }) },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    expect((await screen.findByTestId("dry-run-result")).textContent)
      .toMatch(/3 more were discarded/i);
  });

  it("reports a failure as a failure, not as an empty result", async () => {
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": {
        body: dryRunBody({ failure: "truncated", proposals: [] }),
      },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/could not be used/i);
    expect(alert.textContent).toMatch(/never as "nothing found"/i);
  });

  it("shows what the run cost", async () => {
    // It is the user's key. A background feature that spends without saying
    // how much is the one thing this design refuses.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": { body: dryRunBody() },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    expect((await screen.findByTestId("dry-run-result")).textContent)
      .toMatch(/0\.00007 credits/);
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

  it("explains a failure in words, not in snake_case", async () => {
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": {
        body: dryRunBody({ failure: "no_facts_key" }),
      },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/left out the one key/i);
    expect(alert.textContent).not.toMatch(/no_facts_key/);
  });

  it("says WHY each discarded proposal was discarded", async () => {
    // "3 discarded" cannot distinguish the defence working from the defence
    // eating a true Turkish fact over an apostrophe.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/7/extract/dry-run": {
        body: dryRunBody({ dropped: 3,
                           dropped_by_reason: { ungrounded: 2, too_long: 1 } }),
      },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    const panel = await screen.findByTestId("dry-run-result");
    expect(panel.textContent).toMatch(/2 quoted something that is not in the text/i);
    expect(panel.textContent).toMatch(/1 was longer than a note may be/i);
  });
  it("does not offer a second run while the first is still being billed",
     async () => {
    // The right panel remounts its panels on every tab switch, on purpose.
    // With the in-flight flag living in the component, a user who started a
    // run and switched tabs came back to an enabled button and an empty panel
    // while the first call - which is not cancellable and runs to completion
    // server-side - was still being billed. It looks like nothing happened.
    // Clicking again pays twice, on their own key.
    const user = userEvent.setup();
    let release: (v: unknown) => void = () => {};
    const held = new Promise((r) => { release = r; });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body),
                   { status: 200,
                     headers: { "Content-Type": "application/json" } });
    let calls = 0;

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/vault/status"))
        return json({ initialized: true, unlocked: true });
      if (url.includes("/extract/models")) return json(MODELS);
      if (url.includes("/extract/settings"))
        return json({ model_id: "cheap/one", prompt_language: "en" });
      if (url.includes("/extract/dry-run")) {
        calls += 1;
        await held;
        return json(dryRunBody());
      }
      return json({});
    }));

    // ONE QueryClient across both mounts - the client the app keeps across a
    // tab switch. A fresh one per mount would test nothing at all.
    const first = renderWithQueryClient(<ExtractionSettings />);
    await user.click(await screen.findByRole("button", { name: /try it/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /try it/i })).toBeDisabled());

    first.unmount();
    renderWithQueryClient(<ExtractionSettings />,
                          { client: first.queryClient });

    const button = await screen.findByRole("button", { name: /try it/i });
    expect(button).toBeDisabled();
    expect(calls).toBe(1);

    release(null);
    await waitFor(() => expect(calls).toBe(1));
  });
});
