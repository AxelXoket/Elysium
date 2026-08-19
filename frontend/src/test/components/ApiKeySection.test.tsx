/**
 * ApiKeySection - checking the key that is ALREADY stored.
 *
 * The save box has always been able to tell a good key from a bad one, and
 * only at the moment one is typed. The key that quietly stops working is the
 * one saved last month, and the panel had no way to ask about it.
 *
 * WHAT IS ACTUALLY UNDER TEST
 *
 * One property above all the others: "OpenRouter rejected your key" and "we
 * could not reach OpenRouter" must never arrive as the same answer. They are
 * opposite instructions. One means replace the key; the other means the key
 * was never asked about, and a user who deletes a working key because their
 * proxy was down has been actively misled by this screen.
 *
 * SO NO TEST HERE RESTATES A SENTENCE. Copy is not behaviour; a test holding
 * its own copy of the wording goes green on the day the wording and the branch
 * stop agreeing, which is the only day it was needed. What is asserted instead
 * is what the component publishes about itself: `data-feedback` names the KIND
 * of answer, and the rendered text of the three outcomes is compared to
 * ITSELF, three ways, so two branches collapsing into one sentence is red.
 *
 * The sentences that do appear below by name are read out of the shared error
 * catalogue through getErrorMessage, which is the one writer of those strings.
 *
 * The ground underneath all of it: with nobody pressing anything, the app
 * never puts the stored key on the wire.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import { settingsFixture, proxyHealthFixture } from "@/test/mocks/fixtures";
import { ApiKeySection } from "@/components/settings/ApiKeySection";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getErrorMessage } from "@/lib/errors";

const CHECK_URL = "/settings/api-key/check";

function wrapper({ children }: { children: React.ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>;
}

/**
 * The check route is registered FIRST on purpose.
 *
 * mockFetch matches by substring in insertion order, and "/settings" contains
 * every settings path there is. Registered the other way round, a POST to the
 * check endpoint would be answered with the settings fixture, the response
 * schema would reject it, and every outcome below would arrive as the same
 * parse error: three green-looking tests all measuring one bug in the mock.
 */
function mount(
  check: { status?: number; body: unknown },
  settings = settingsFixture,
) {
  const fetchMock = mockFetch({
    [`POST ${CHECK_URL}`]: check,
    "/settings/proxy/health": { body: proxyHealthFixture },
    "/settings": { body: settings },
  });
  renderWithQueryClient(<ApiKeySection />, { wrapper });
  return fetchMock;
}

/** Every request this render made to the check endpoint. */
function checkCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls.filter(
    (call) => typeof call[0] === "string" && call[0].includes(CHECK_URL),
  );
}

/** Wait for the panel to know a key is stored, then press the control.
 *  The button only exists once GET /settings has answered, so a plain
 *  getByRole here would be racing the fixture rather than the component. */
async function pressTest(): Promise<void> {
  const user = userEvent.setup();
  const button = await screen.findByRole("button", {
    name: /test stored key/i,
  });
  await user.click(button);
}

/** The answer line, once it appears: what it says and what kind it claims to
 *  be. Read off the rendered page, never off a table this file holds. */
async function answer(): Promise<{ text: string; kind: string | null }> {
  const line = await screen.findByRole("status");
  return {
    text: line.textContent ?? "",
    kind: line.getAttribute("data-feedback"),
  };
}

describe("API key check", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  // ── the ground ─────────────────────────────────────────────────────────────

  describe("before anybody presses it", () => {
    let fetchMock: ReturnType<typeof mockFetch>;

    beforeEach(() => {
      fetchMock = mount({ body: { key_status: "valid" } });
    });

    it("checks nothing and says nothing until the button is pressed", async () => {
      // THE GROUND. Without it, every assertion in this file is equally
      // satisfied by a component that checks the key on mount, on a timer, or
      // on every settings refetch. Each of those puts the stored key on the
      // wire with nobody asking, on a screen whose whole promise is that it
      // does not do that.
      await waitFor(() => {
        expect(screen.getByText("API key is set")).toBeInTheDocument();
      });
      // Give any mount-time effect that wanted to fire, the chance to fire.
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(checkCalls(fetchMock)).toHaveLength(0);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      // The positive control. Without it the emptiness above is equally well
      // explained by a button wired to nothing at all.
      await pressTest();
      await waitFor(() => {
        expect(checkCalls(fetchMock)).toHaveLength(1);
      });
      expect((await answer()).kind).toBe("success");
    });

    it("asks about the stored key without sending one", async () => {
      // The request has no body at all: the backend reads the secret itself.
      // A check that had to carry the key would be indistinguishable from a
      // save, and this screen's key field is write-only for a reason.
      await pressTest();
      await waitFor(() => {
        expect(checkCalls(fetchMock)).toHaveLength(1);
      });

      const [, init] = checkCalls(fetchMock)[0] as [string, RequestInit];
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined();
    });
  });

  // ── the three outcomes ─────────────────────────────────────────────────────

  it("reports success when OpenRouter accepts the stored key", async () => {
    mount({ body: { key_status: "valid" } });
    await pressTest();
    const { kind, text } = await answer();
    expect(kind).toBe("success");
    expect(text.length).toBeGreaterThan(0);
  });

  it("reports a rejection, on a 200, not as a failed request", async () => {
    // The backend answers 200 for a rejection because the CHECK succeeded and
    // only the key failed. Were it ever a 4xx, this would land in the catch
    // path and reach the reader wearing the same generic sentence as an
    // unreachable provider, which is the collapse the feature exists to stop.
    mount({ body: { key_status: "invalid" } });
    await pressTest();
    expect((await answer()).kind).toBe("error");
  });

  it("reports could-not-check as neither success nor rejection", async () => {
    mount({ body: { key_status: "validation_unavailable" } });
    await pressTest();
    const { kind } = await answer();
    expect(kind).toBe("unknown");
    expect(kind).not.toBe("success");
    expect(kind).not.toBe("error");
  });

  it("gives three answers that differ in both kind and wording", async () => {
    // THE POINT OF THE FEATURE, asserted as a difference rather than as three
    // separate comparisons against three literals. Three such tests all stay
    // green on the day two branches start rendering the same sentence. This
    // one turns red, because it is the only one that looks at all three.
    const seen: { text: string; kind: string | null }[] = [];
    for (const status of ["valid", "invalid", "validation_unavailable"]) {
      mount({ body: { key_status: status } });
      await pressTest();
      seen.push(await answer());
      vi.restoreAllMocks();
      document.body.innerHTML = "";
    }

    expect(new Set(seen.map((s) => s.kind)).size).toBe(3);
    expect(new Set(seen.map((s) => s.text)).size).toBe(3);
    // Named explicitly, because this is the pair that matters: a rejected key
    // and a provider we never reached.
    expect(seen[1].text).not.toBe(seen[2].text);
    expect(seen[1].kind).not.toBe(seen[2].kind);
  });

  // ── the check itself failing is not a verdict on the key ───────────────────

  it("does not blame the key when the proxy gate refuses the request", async () => {
    // 503 proxy_missing: the request never left the machine, so nothing was
    // learned about the key. The sentence has to be the proxy's, and it is
    // read from the shared catalogue rather than restated here.
    mount({ status: 503, body: { detail: "proxy_missing" } });
    await pressTest();
    expect((await answer()).text).toBe(getErrorMessage("proxy_missing"));
  });

  it("does not claim success for an answer it does not understand", async () => {
    // A future or corrupted key_status must fail closed. The strict response
    // schema turns it into a parse error rather than letting an unrecognised
    // word fall through to the reassuring branch.
    mount({ body: { key_status: "probably_fine" } });
    await pressTest();
    const { kind, text } = await answer();
    expect(kind).not.toBe("success");
    expect(text).toBe(getErrorMessage("invalid_response_shape"));
  });

  // ── nothing stored, nothing to offer ───────────────────────────────────────

  it("offers no test button when there is no stored key", async () => {
    mount(
      { body: { key_status: "not_set" } },
      { ...settingsFixture, api_key_set: false },
    );
    await waitFor(() => {
      expect(screen.getByText("No API key configured")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /test stored key/i }),
    ).not.toBeInTheDocument();
  });
});

/**
 * The stored-key status indicator - pending / error / resolved.
 *
 * `settings?.api_key_set ? "set" : "unset"` used to collapse "GET /settings
 * has not answered yet" into "unset": a user whose key WAS set saw a
 * danger-red "No API key configured" for the ~30ms before the query landed.
 * Every test above waits past that window with `findBy`/`waitFor`; these do
 * not - they assert what the DOM says WHILE the fetch is still open.
 */
describe("stored-key status indicator - pending must not read as unset", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("says it is checking, not that no key is set, while settings are in flight", async () => {
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
    });
    // settingsFixture has api_key_set: true - the case the old branch got
    // wrong. Held open so the render below happens before it answers.
    const release = delayRoute(fetchMock, "GET", "/settings", {
      body: settingsFixture,
    });
    const { container } = renderWithQueryClient(<ApiKeySection />, { wrapper });

    expect(screen.getByText("Checking for a stored key…")).toBeInTheDocument();
    expect(
      container.querySelector('[data-state="pending"]'),
    ).toBeInTheDocument();
    expect(screen.queryByText("No API key configured")).not.toBeInTheDocument();
    expect(screen.queryByText("API key is set")).not.toBeInTheDocument();
    // The coloured dot is not merely repainted while pending - it is not
    // rendered at all, so a collapsed branch cannot pass this by accident.
    expect(container.querySelector(".rounded-full")).not.toBeInTheDocument();

    release();
    await waitFor(() => {
      expect(screen.getByText("API key is set")).toBeInTheDocument();
    });
  });

  it("reports an unreachable settings fetch as its own state, not a verdict on the key", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { status: 500, body: { detail: "internal_error" } },
    });
    const { container } = renderWithQueryClient(<ApiKeySection />, { wrapper });

    expect(
      await screen.findByText("Could not check whether a key is stored."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No API key configured")).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-state="error"]'),
    ).toBeInTheDocument();
  });

  // GROUND: the fix must not silence a genuinely unconfigured setup once the
  // query actually resolves.
  it("still reports no key once settings resolves for real - the ground", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, api_key_set: false } },
    });
    renderWithQueryClient(<ApiKeySection />, { wrapper });
    expect(await screen.findByText("No API key configured")).toBeInTheDocument();
  });

  // POSITIVE CONTROL: a configured setup reads as configured once resolved.
  it("reports the key is set once settings resolves for real - the positive control", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, api_key_set: true } },
    });
    renderWithQueryClient(<ApiKeySection />, { wrapper });
    expect(await screen.findByText("API key is set")).toBeInTheDocument();
  });
});
