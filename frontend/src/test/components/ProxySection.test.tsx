import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import {
  settingsFixture,
  proxyHealthFixture,
} from "@/test/mocks/fixtures";
import {
  ProxySection,
  PROXY_COPY_LEFT_TEXT,
} from "@/components/settings/ProxySection";
import { TooltipProvider } from "@/components/ui/tooltip";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>{children}</TooltipProvider>
    
  );
}

describe("Proxy Section Tests", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-10: Proxy save calls POST /settings/proxy with exact backend field names
  it("saves the URL, the required flag and the alias in one request", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    const urlInput = screen.getByLabelText("Proxy URL input");
    await user.type(urlInput, "https://proxy.test.com");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save proxy/i });
    await user.click(saveBtn);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);

      // Verify body field names match backend exactly
      if (postCalls.length > 0) {
        const body = JSON.parse(postCalls[0][1]?.body as string);
        expect(body).toHaveProperty("proxy_url");
        expect(body).toHaveProperty("proxy_required");
        expect(body).toHaveProperty("proxy_alias");
      }
    });
  });

  // T-11: Proxy delete calls DELETE
  it("removing the proxy deletes it on the server", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const deleteBtn = screen.getByRole("button", { name: /remove proxy/i });
    await user.click(deleteBtn);

    await waitFor(() => {
      const deleteCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "DELETE",
      );
      expect(deleteCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  // T-12: Proxy URL not displayed after save
  it("never shows the proxy URL back, not even right after storing it", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    const urlInput = screen.getByLabelText("Proxy URL input") as HTMLInputElement;
    await user.type(urlInput, "https://proxy.test.com");

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const saveBtn = screen.getByRole("button", { name: /save proxy/i });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(urlInput.value).toBe("");
    });
    expect(
      screen.queryByText("https://proxy.test.com"),
    ).not.toBeInTheDocument();
  });

  // T-13: Proxy health status renders
  it("shows whether the proxy answered, and how slowly", async () => {
    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/Healthy/)).toBeInTheDocument();
      expect(screen.getByText(/42ms/)).toBeInTheDocument();
    });
  });

  // FIX-1: "Require proxy" toggle reflects server state on mount
  it("starts from what the server says about requiring the proxy", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_required: true,
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });

    const toggle = screen.getByLabelText("Proxy required toggle");
    await waitFor(() => {
      expect(toggle).toBeChecked();
    });
  });

  // FIX-1: saving with only the URL changed must NOT silently disable
  // proxy_required - the untouched toggle mirrors the server value (true).
  it("saving only the URL leaves the requirement the server holds alone", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_required: true,
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Proxy required toggle")).toBeChecked();
    });

    // Change ONLY the URL; do not touch the toggle
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse(postCalls[0][1]?.body as string);
      expect(body.proxy_required).toBe(true);
    });
  });

  // FIX-1: a user-toggled value is preserved (dirty flag blocks server re-sync)
  it("sends the requirement just toggled, not the one still being refetched", async () => {
    const user = userEvent.setup();
    // Server says proxy_required=false; user switches it on before saving.
    const mock = fetchMock;

    renderWithQueryClient(<ProxySection />, { wrapper });

    const toggle = screen.getByLabelText("Proxy required toggle");
    await waitFor(() => {
      expect(toggle).not.toBeChecked();
    });

    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy") &&
          !call[0].includes("/health") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse(postCalls[0][1]?.body as string);
      expect(body.proxy_required).toBe(true);
    });
  });

  // FIX-3: proxy save failure shows a safe mapped message, never raw detail
  // ── Audit HIGH: the kill-switch had no write path of its own ───────────
  //
  // proxy_required could only ride along with a full POST /settings/proxy,
  // which requires a non-empty URL - and the URL field is write-only. So
  // flipping the switch moved it, left "Save Proxy" disabled, wrote nothing,
  // and completions kept going out direct on an unhealthy proxy while the user
  // believed the block was armed.

  it("the toggle alone can be saved once a proxy is configured", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_required: false,
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByLabelText("Proxy required toggle")).not.toBeChecked();
    });

    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeDisabled(); // nothing changed yet

    await user.click(screen.getByLabelText("Proxy required toggle"));
    expect(save).toBeEnabled(); // URL box still empty - this is the whole fix

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.click(save);

    await waitFor(() => {
      const posts = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/settings/proxy/required") &&
          call[1]?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string)).toEqual({
        proxy_required: true,
      });
    });
  });

  it("the toggle alone cannot be saved before any proxy exists", async () => {
    // Arming with no URL would block every completion behind proxy_missing,
    // and this screen is the only way out - so the full save is the path.
    const user = userEvent.setup();
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: false,
          proxy_required: false,
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });
    await waitFor(() => {
      expect(screen.getByLabelText("Proxy required toggle")).toBeInTheDocument();
    });
    await user.click(screen.getByLabelText("Proxy required toggle"));
    expect(screen.getByRole("button", { name: /save proxy/i })).toBeDisabled();
  });

  // ── Audit LOW: a URL-only update used to erase the stored alias ─────────

  it("an untouched alias box preserves the stored alias", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_alias: "work",
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText("Proxy URL input")).toBeInTheDocument(),
    );
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://new.proxy.test",
    );
    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].endsWith("/settings/proxy") &&
          call[1]?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBe("work");
    });
  });

  // S-9 rewrite, name and claim only: the panel still sends `null` for an
  // emptied box, and the request shape is what this test drives. What
  // changed is what `null` MEANS on the other end - the server now writes
  // the default proxy name instead of deleting the stored one - so a title
  // saying the alias is cleared would be describing something the
  // application no longer does. Backed by
  // test_proxy_alias_default.py::test_an_emptied_box_names_the_proxy.
  it("sends an explicit null for a cleared alias box", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_alias: "work",
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });
    const alias = await screen.findByLabelText("Proxy alias input");
    // Touch it, then leave it empty - an explicit clear.
    await user.type(alias, "x");
    await user.clear(alias);
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://new.proxy.test",
    );
    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].endsWith("/settings/proxy") &&
          call[1]?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBeNull();
    });
  });

  it("an emptied alias box reports a reset, not a deletion, and keeps a name on screen", async () => {
    // The panel used to answer this with "Name cleared", which was true and
    // is not any more: the server stores the default name instead of
    // deleting the stored one. The cached name must not blink to "no name"
    // in between either - writing the sent `null` into the cache would have
    // shown exactly the state this change exists to remove.
    const user = userEvent.setup();
    const mock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: {
          ...settingsFixture,
          proxy_configured: true,
          proxy_alias: "work",
        },
      },
    });

    renderWithQueryClient(<ProxySection />, { wrapper });
    const alias = await screen.findByLabelText("Proxy alias input");
    await screen.findByText(/Alias: work/);
    await user.type(alias, "x");
    await user.clear(alias);

    mock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = mock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].endsWith("/settings/proxy/alias") &&
          call[1]?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBeNull();
    });

    expect(
      await screen.findByText(/Name reset to the default/),
    ).toBeInTheDocument();
    // GROUND: the stored name is still on screen while the refetch lands.
    expect(screen.getByText(/Alias: work/)).toBeInTheDocument();
  });

  it("explains a refused save in its own words, not the upstream detail", async () => {
    const user = userEvent.setup();

    renderWithQueryClient(<ProxySection />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "RAW_UPSTREAM_DETAIL" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("RAW_UPSTREAM_DETAIL")).not.toBeInTheDocument();
  });
});

/**
 * The "Proxy configured" indicator - pending / error / resolved.
 *
 * `settings?.proxy_configured ? "Proxy configured" : "No proxy"` used to
 * collapse the in-flight window into "No proxy" - milder than the API-key
 * dot because the dot was already muted, not danger, but the same wrong
 * claim about a proxy that IS configured. Held open with delayRoute so the
 * assertions land inside that window instead of after it.
 */
describe("configured-status indicator - pending must not read as unconfigured", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says it is checking, not 'No proxy', while settings are in flight", async () => {
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    const release = delayRoute(fetchMock, "GET", "/settings", {
      body: { ...settingsFixture, proxy_configured: true },
    });
    const { container } = renderWithQueryClient(<ProxySection />, { wrapper });

    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(
      container.querySelector('[data-state="pending"]'),
    ).toBeInTheDocument();
    expect(screen.queryByText("No proxy")).not.toBeInTheDocument();
    expect(screen.queryByText("Proxy configured")).not.toBeInTheDocument();

    release();
    await waitFor(() => {
      expect(screen.getByText("Proxy configured")).toBeInTheDocument();
    });
  });

  it("reports an unreachable settings fetch as its own state, not 'No proxy'", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { status: 500, body: { detail: "internal_error" } },
    });
    const { container } = renderWithQueryClient(<ProxySection />, { wrapper });

    expect(
      await screen.findByText("Could not check whether a proxy is configured."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No proxy")).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-state="error"]'),
    ).toBeInTheDocument();
  });

  // GROUND: a genuinely unconfigured proxy still reports "No proxy" once
  // the query actually resolves - the fix must not silence the true alarm.
  it("still reports 'No proxy' once settings resolves for real - the ground", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: false } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(await screen.findByText("No proxy")).toBeInTheDocument();
  });

  // POSITIVE CONTROL.
  it("reports 'Proxy configured' once settings resolves for real - the positive control", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(await screen.findByText("Proxy configured")).toBeInTheDocument();
  });
});

/**
 * The proxy-health indicator - the worst of the three defects, because it is
 * the one that used to wear the real danger colour: `health?.healthy ?
 * "Healthy" : "Unhealthy"` painted a red "Unhealthy" over a healthy proxy for
 * every millisecond between the settings fetch landing and this component's
 * own, uncached health probe answering.
 */
describe("proxy-health indicator - pending must not read as unhealthy", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a muted checking state, never the danger colour, while the probe is in flight", async () => {
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    // Settings resolve immediately, so the health block mounts; only the
    // health probe itself is held open.
    const release = delayRoute(fetchMock, "GET", "/settings/proxy/health", {
      body: proxyHealthFixture,
    });
    renderWithQueryClient(<ProxySection />, { wrapper });

    await screen.findByText("Proxy configured");
    const indicator = await screen.findByTestId("proxy-health-indicator");
    expect(indicator.parentElement).toHaveAttribute("data-state", "pending");
    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(screen.queryByText("Unhealthy")).not.toBeInTheDocument();
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
    // Positively muted, not merely "not styled" - this is what stands in for
    // the colour a collapsed branch would have painted here instead.
    expect(indicator).toHaveStyle({ color: "var(--color-es-text-muted)" });

    release();
    await waitFor(() => {
      // Regex, not an exact string: the fixture carries a latency suffix
      // ("Healthy · 42ms") in the same text node, same as the pre-existing
      // T-13 test above.
      expect(screen.getByText(/^Healthy/)).toBeInTheDocument();
    });
  });

  it("reports a failed health probe as its own state, not as Unhealthy", async () => {
    mockFetch({
      "/settings/proxy/health": { status: 500, body: { detail: "internal_error" } },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });

    expect(await screen.findByText("Could not check the proxy.")).toBeInTheDocument();
    expect(screen.queryByText("Unhealthy")).not.toBeInTheDocument();
    const indicator = await screen.findByTestId("proxy-health-indicator");
    expect(indicator.parentElement).toHaveAttribute("data-state", "error");
  });

  // GROUND: a genuinely unhealthy proxy still reports "Unhealthy" once the
  // probe actually resolves - the fix must not silence the true alarm.
  it("still reports 'Unhealthy' once the probe resolves for real - the ground", async () => {
    mockFetch({
      "/settings/proxy/health": { body: { ...proxyHealthFixture, healthy: false } },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(await screen.findByText(/^Unhealthy/)).toBeInTheDocument();
  });

  // POSITIVE CONTROL.
  it("reports 'Healthy' once the probe resolves for real - the positive control", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(await screen.findByText(/^Healthy/)).toBeInTheDocument();
  });
});

describe("whether a proxy is REQUIRED, while the answer is still loading", () => {
  // The third instance of the same defect in this one file. "No" is not a
  // neutral placeholder here: it says the app may reach the network with no
  // proxy at all, which is the opposite of what somebody who configured one
  // needs to read - and it says it to them about their own machine.
  it("says it is checking rather than that no proxy is required", async () => {
    const required = { ...settingsFixture, proxy_configured: true,
                       proxy_required: true };
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: required },
    });
    const release = delayRoute(fetchMock, "GET", "/settings", { body: required });
    renderWithQueryClient(<ProxySection />, { wrapper });

    const line = await screen.findByTestId("proxy-required");
    expect(line).toHaveAttribute("data-state", "pending");
    release();
    await waitFor(() =>
      expect(screen.getByTestId("proxy-required"))
        .toHaveAttribute("data-state", "required"));
  });

  it("still says optional once the answer really is optional", async () => {
    // Ground. A fix that swallowed the true answer would be worse than the
    // bug: this line is how somebody checks their own setup.
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: { ...settingsFixture, proxy_configured: true,
                             proxy_required: false } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    await waitFor(() =>
      expect(screen.getByTestId("proxy-required"))
        .toHaveAttribute("data-state", "optional"));
  });
});

/**
 * The feedback line: whether anything but a sighted reader ever learns what
 * happened.
 *
 * Two defects on one box.
 *
 * 1. It was a bare <div>. "Require proxy" is a kill-switch: arming it is the
 *    difference between every completion going out through the proxy and
 *    going out direct, and the only report on whether that worked appeared,
 *    silently, at the bottom of a panel. A screen reader user pressed the
 *    control and got nothing back. The sibling ApiKeySection has carried
 *    role="status" on the same box all along, so the house already had an
 *    answer and this component was the one diverging from it.
 *
 * 2. Success and failure were told apart by the COLOUR of the text and by
 *    nothing else: no role, no published kind, and an icon on the error side
 *    only. Colour is the one signal a colour-blind reader loses outright and
 *    the one no assistive technology reads at all.
 *
 * And the same delete-response defect the API key section carried: the server
 * says whether a copy of the proxy URL survived the delete, and the client's
 * schema stripped the field before anybody could read it.
 *
 * These assert what a screen reader would be handed - the accessible role and
 * the text inside it - plus the kind the box publishes about itself. Nothing
 * here reads style.color, because a test that reads the colour is measuring
 * the very signal the defect was about.
 */
describe("the feedback line is announced, and says which kind of answer it is", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mountProxy(routes: Record<string, { status?: number; body: unknown }>) {
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      ...routes,
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    return fetchMock;
  }

  /** Type a URL and press Save. The URL box is the only way to enable Save on
   *  a panel whose other two controls are untouched. */
  async function save(user: ReturnType<typeof userEvent.setup>) {
    await user.type(
      await screen.findByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));
  }

  it("announces nothing until there is something to announce - the ground", async () => {
    // THE GROUND. The box is conditional, so an always-present live region
    // would be its own defect: it would put an empty announced node on the
    // page for every reader, forever. The positive control is in the same
    // test: the identical query finds one the moment a save answers.
    const user = userEvent.setup();
    mountProxy({
      "POST /settings/proxy": { body: { ok: true } },
    });
    await screen.findByText("Proxy configured");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await save(user);
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("hands a successful save to a screen reader, not only to the eye", async () => {
    const user = userEvent.setup();
    mountProxy({ "POST /settings/proxy": { body: { ok: true } } });
    await screen.findByText("Proxy configured");
    await save(user);

    const line = await screen.findByRole("status");
    expect(line.textContent?.trim().length).toBeGreaterThan(0);
    expect(line.getAttribute("data-feedback")).toBe("success");
  });

  it("hands a refused save to a screen reader too", async () => {
    const user = userEvent.setup();
    mountProxy({
      "POST /settings/proxy": { status: 500, body: { detail: "internal_error" } },
    });
    await screen.findByText("Proxy configured");
    await save(user);

    const line = await screen.findByRole("status");
    expect(line.textContent?.trim().length).toBeGreaterThan(0);
    expect(line.getAttribute("data-feedback")).toBe("error");
  });

  it("tells the two apart by something other than the colour", async () => {
    // POSITIVE CONTROL for the claim above. Three separate tests that each
    // look at one outcome all stay green on the day both outcomes start
    // rendering the same announced text with the same kind; this is the only
    // one that holds the two runs side by side.
    const seen: { text: string; kind: string | null }[] = [];
    for (const post of [
      { body: { ok: true } },
      { status: 500, body: { detail: "internal_error" } },
    ]) {
      const user = userEvent.setup();
      mountProxy({ "POST /settings/proxy": post });
      await screen.findByText("Proxy configured");
      await save(user);
      const line = await screen.findByRole("status");
      seen.push({
        text: line.textContent ?? "",
        kind: line.getAttribute("data-feedback"),
      });
      vi.restoreAllMocks();
      document.body.innerHTML = "";
    }

    expect(new Set(seen.map((s) => s.kind)).size).toBe(2);
    expect(new Set(seen.map((s) => s.text)).size).toBe(2);
  });
});

/**
 * Removing the proxy: the copy the credential store would not release.
 *
 * The URL is a secret in its own right and can carry credentials inside it,
 * so the delete route answers with `legacy_copy_left` exactly as the API key
 * route does. Both used to parse a schema that described `{ ok: true }` and
 * nothing else, and zod strips what it was not told about, so the answer was
 * read off the wire and thrown away before any component could see it.
 *
 * The sentence is imported from the component, never restated here.
 */
describe("removing the proxy", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function remove(del: { status?: number; body: unknown }) {
    const user = userEvent.setup();
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "DELETE /settings/proxy": del,
      "/settings": { body: { ...settingsFixture, proxy_configured: true } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    await user.click(
      await screen.findByRole("button", { name: /remove proxy/i }),
    );
    return screen.findByRole("status");
  }

  it("says only that the proxy was removed when nothing was left behind", async () => {
    // THE GROUND, paired with the test below: the same query that finds the
    // warning there must not find it here, or the warning would be permanent
    // and the run below would prove nothing.
    const line = await remove({ body: { ok: true, legacy_copy_left: false } });
    expect(line.getAttribute("data-feedback")).toBe("success");
    expect(line.textContent).not.toBe(PROXY_COPY_LEFT_TEXT);
    expect(screen.queryByText(PROXY_COPY_LEFT_TEXT)).not.toBeInTheDocument();
  });

  it("names the copy of the URL the credential store kept", async () => {
    const line = await remove({ body: { ok: true, legacy_copy_left: true } });
    expect(line).toHaveTextContent(PROXY_COPY_LEFT_TEXT);
    expect(line.getAttribute("data-feedback")).toBe("partial");
    expect(line.getAttribute("data-feedback")).not.toBe("success");
  });

  it("treats a server that never mentions a copy as no copy left", async () => {
    // An older backend that does not send the field must not turn a delete
    // that worked into a failed request. It falls back to the reading that
    // raises no alarm.
    const line = await remove({ body: { ok: true } });
    expect(line.getAttribute("data-feedback")).toBe("success");
    expect(line.textContent).not.toBe(PROXY_COPY_LEFT_TEXT);
  });
});
