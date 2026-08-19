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
import { ProxySection } from "@/components/settings/ProxySection";
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

  it("a cleared alias box still clears the stored alias", async () => {
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
