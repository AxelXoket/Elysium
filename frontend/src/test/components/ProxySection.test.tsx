import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import {
  settingsFixture,
  proxyHealthFixture,
} from "@/test/mocks/fixtures";
import { keys } from "@/lib/query/keys";
import type { Settings } from "@/lib/schemas/settings";
import {
  ProxySection,
  PROXY_COPY_LEFT_TEXT,
} from "@/components/settings/ProxySection";
import { TooltipProvider } from "@/components/ui/tooltip";
import { fontSizePx, MD_FROM_PX, parseSheet, remPx, rootTokens } from "@/test/helpers/compiledSheet";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { typeScale } from "@/lib/theme/tokens";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>{children}</TooltipProvider>
    
  );
}

/** The POST calls that left for one path, in order. */
function postsTo(fetchMock: ReturnType<typeof mockFetch>, path: string) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      typeof url === "string" && url.endsWith(path) && init?.method === "POST",
  );
}

/** Every JSON body that left through fetch, whatever the route. */
function sentBodies(
  fetchMock: ReturnType<typeof mockFetch>,
): Record<string, unknown>[] {
  return fetchMock.mock.calls.flatMap(([, init]) =>
    typeof init?.body === "string"
      ? [JSON.parse(init.body) as Record<string, unknown>]
      : [],
  );
}

/**
 * One macrotask, inside act. A send that WAS triggered reaches fetch a few
 * microtasks after the click; a negative assertion on the calls that left is
 * only worth anything after that window has closed.
 */
function settle() {
  return act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/**
 * The switch's closed state, read the way a screen reader reads it.
 *
 * base-ui renders the switch as a <span role="switch">, so the `disabled`
 * attribute jest-dom's toBeDisabled looks for never appears on it; its
 * closed state is the ARIA one, which is also the one assistive technology
 * is told, and an open switch carries no aria-disabled at all (measured, so
 * "open" is asserted as the absence of this marker). The save button is a
 * real <button> and keeps toBeDisabled.
 */
const SWITCH_CLOSED = ["aria-disabled", "true"] as const;

/**
 * The server's own default name for the one proxy. Spelled here because
 * nothing in the frontend carries it, and that is the point: the panel never
 * invents the name, the server does (backend default_names.PROXY, sequence
 * number 1 because there is exactly one proxy).
 */
const SERVER_DEFAULT_PROXY_NAME = "Proxy 1";

/**
 * The proxy routes with the one piece of state these tests turn on behind
 * them: the stored name. A stand-in for backend/routers/settings.py at the
 * fetch boundary, copying its one rule about the label - a null or blank
 * name is answered with the default one, never deleted
 * (`_normalise_proxy_alias`; proven on the real route by
 * test_proxy_alias_default.py::test_an_emptied_box_names_the_proxy). What
 * GET /settings answers is therefore CAUSED by what the panel sent, which
 * is what makes "the server writes the default" a positive control here
 * rather than a scripted second response. Everything not handled falls
 * through to mockFetch's table.
 *
 * Its state is not handed out: an assertion on it would prove the stand-in
 * and nothing else. What proves the panel is the BOX after the refetch, and
 * the stand-in's own positive control is the known-name test beside T-80,
 * where a typed name comes back as itself and not as the default.
 */
function proxyServer(
  fetchMock: ReturnType<typeof mockFetch>,
  initial: Partial<Settings>,
) {
  const state: Settings = {
    ...settingsFixture,
    proxy_configured: true,
    ...initial,
  };
  const original = fetchMock.getMockImplementation();
  if (!original) {
    throw new Error("proxyServer needs a mockFetch implementation to fall back on");
  }
  let gate: Promise<void> | null = null;
  let open: () => void = () => {};
  let failing = false;
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const nameOrDefault = (value: unknown) =>
    typeof value === "string" && value.trim()
      ? value.trim()
      : SERVER_DEFAULT_PROXY_NAME;
  fetchMock.mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET" && url.endsWith("/settings")) {
      if (gate) await gate;
      if (failing) return json({ detail: "internal_error" }, 500);
      return json(state);
    }
    if (method === "POST" && url.endsWith("/settings/proxy")) {
      const body = JSON.parse(String(init?.body)) as {
        proxy_required: boolean;
        proxy_alias: unknown;
      };
      state.proxy_required = body.proxy_required;
      state.proxy_alias = nameOrDefault(body.proxy_alias);
      return json({ ok: true });
    }
    if (method === "POST" && url.endsWith("/settings/proxy/alias")) {
      const body = JSON.parse(String(init?.body)) as { proxy_alias: unknown };
      state.proxy_alias = nameOrDefault(body.proxy_alias);
      return json({ ok: true, proxy_alias: state.proxy_alias });
    }
    return original(input, init);
  });
  return {
    fetchMock,
    /** From now on GET /settings fails (or answers again) - a refetch that
     *  lands on a dead server, with the earlier answer still in the cache. */
    failSettings(fail: boolean) {
      failing = fail;
    },
    /** Hold every GET /settings from now on; the returned function lets them through. */
    holdSettings(): () => void {
      gate = new Promise<void>((resolve) => {
        open = resolve;
      });
      return () => {
        open();
        gate = null;
      };
    },
  };
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

    const toggle = screen.getByRole("switch", { name: "Require proxy" });
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
      expect(screen.getByRole("switch", { name: "Require proxy" })).toBeChecked();
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

    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    await waitFor(() => {
      expect(toggle).not.toBeChecked();
    });
    // The switch is closed until the answer lands; wait for it to open rather
    // than let the pointer delay outrun the fetch.
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Require proxy" })).not.toHaveAttribute(
        ...SWITCH_CLOSED,
      ),
    );

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
      expect(screen.getByRole("switch", { name: "Require proxy" })).not.toBeChecked();
    });

    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeDisabled(); // nothing changed yet
    // The switch is closed until the answer lands; wait for it to open rather
    // than let the pointer delay outrun the fetch.
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Require proxy" })).not.toHaveAttribute(
        ...SWITCH_CLOSED,
      ),
    );

    await user.click(screen.getByRole("switch", { name: "Require proxy" }));
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
      expect(screen.getByRole("switch", { name: "Require proxy" })).toBeInTheDocument();
    });
    // The switch is closed until the answer lands; wait for it to open rather
    // than let the pointer delay outrun the fetch.
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Require proxy" })).not.toHaveAttribute(
        ...SWITCH_CLOSED,
      ),
    );
    await user.click(screen.getByRole("switch", { name: "Require proxy" }));
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

  // The server answers an emptied box with its default name rather than by
  // deleting the stored one (test_proxy_alias_default.py::
  // test_an_emptied_box_names_the_proxy), and the box now SHOWS the stored
  // name, so emptying it is something the user does in view of it. Both
  // doors onto the label - the full save here and the alias-only save below
  // - are driven against the same stand-in server: the request carries an
  // explicit null, and what the next fetch brings back is the default.
  it("an emptied alias box saves null through the full save, and the default name comes back", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_alias: "work" },
    );

    renderWithQueryClient(<ProxySection />, { wrapper });
    const alias = await screen.findByLabelText("Proxy alias input");
    await waitFor(() => expect(alias).toHaveValue("work"));
    // Emptied in view of the stored name: the user watches "work" leave.
    await user.clear(alias);
    expect(alias).toHaveValue("");
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://new.proxy.test",
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = postsTo(server.fetchMock, "/settings/proxy");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBeNull();
    });
    // A return to the default, not a deletion: the server holds its default
    // name and the box shows it once the refetch lands.
    await waitFor(() => expect(alias).toHaveValue(SERVER_DEFAULT_PROXY_NAME));
  });

  it("an emptied alias box reports a reset, not a deletion, and the box never goes blank", async () => {
    // The panel used to answer this with "Name cleared", which was true and
    // is not any more. The cached name must not blink to "no name" in
    // between either: the box shows the stored name until the server's
    // default replaces it, and at no point is it empty.
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_alias: "work" },
    );

    renderWithQueryClient(<ProxySection />, { wrapper });
    const alias = await screen.findByLabelText("Proxy alias input");
    await waitFor(() => expect(alias).toHaveValue("work"));
    // The refetch is held from here, so the window this test is about stays
    // open long enough to be looked at. Without the hold the invalidated GET
    // lands while the feedback text is being awaited, the default is already
    // in the box, and anything asserted after that says nothing about the
    // in-between: reverting the cache guard in useSetProxyAlias left the
    // whole file green.
    const release = server.holdSettings();
    await user.clear(alias);
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = postsTo(server.fetchMock, "/settings/proxy/alias");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBeNull();
    });
    expect(
      await screen.findByText(/Name reset to the default/),
    ).toBeInTheDocument();
    // The exact value, not merely "not empty": the stored name is what the
    // box holds until the server's default physically arrives to take its
    // place. The refetch is still held here, so "work" is the cache having
    // refused the null the request carried, and nothing else.
    expect(alias).toHaveValue("work");
    // Let the held refetch through; the default is what replaces it.
    release();
    await waitFor(() => expect(alias).toHaveValue(SERVER_DEFAULT_PROXY_NAME));
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

/**
 * The kill-switch while the server's answer is not in hand.
 *
 * `requiredToggle` used to start at `false` and mirror the server only once
 * an answer had arrived, so during the fetch - and for good after a failed
 * one - the switch sat live at "off", and a URL-only save wrote that "off"
 * to the server as the user's decision. The "Required:" line right above it
 * was saying "unknown" at the same moment. Three states now: on, off, and no
 * answer yet; in the third the switch and the save button are closed.
 *
 * Driven at the fetch boundary: the settings route fails or is held, and the
 * requests that left are read back off the mock. Nothing here reads the
 * component's state.
 */
describe("the kill-switch while the server's answer is not in hand", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-56.
  it("refuses to write the requirement while the settings fetch failed, even when pushed", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "POST /settings/proxy": { body: { ok: true } },
      "/settings": { status: 500, body: { detail: "internal_error" } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    // The line the user reads at this moment says "unknown".
    await waitFor(() =>
      expect(screen.getByTestId("proxy-required")).toHaveAttribute(
        "data-state",
        "error",
      ),
    );

    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeDisabled();

    // Pushed anyway: the click a pointer would deliver, sent straight at
    // both controls. A closed control drops it.
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    fireEvent.click(save);
    await settle();

    expect(postsTo(fetchMock, "/settings/proxy")).toHaveLength(0);
    // And not through any other door: no request that left this panel
    // carries a proxy_required field at all.
    expect(sentBodies(fetchMock).some((b) => "proxy_required" in b)).toBe(
      false,
    );
  });

  it("keeps the switch closed while the settings fetch is still out, and opens it on the answer", async () => {
    // The other half of "not in hand": the fetch has not failed, it has not
    // finished. Held at the boundary so the window can be looked at.
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_required: true },
    );
    const release = server.holdSettings();
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(screen.getByTestId("proxy-required")).toHaveAttribute(
      "data-state",
      "pending",
    );
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);

    release();
    await waitFor(() => expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED));
    expect(toggle).toBeChecked();
  });

  // The third unknown shape: the answer was in hand, the user moved the
  // switch, and then a refetch failed. The switch closes with the line and
  // the button, the user's unsaved choice stays in view, and when the answer
  // is back the switch reopens WITH that choice and the save carries it.
  // Closing on `requiredToggle == null` left this switch live beside a line
  // saying "unknown" and a closed button, the very contradiction the plan
  // refused the one-line fix for.
  it("closes a dirty switch when the answer is lost again, keeps the choice in view, and reopens with it", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({
        "/settings/proxy/health": { body: proxyHealthFixture },
        "POST /settings/proxy": { body: { ok: true } },
      }),
      { proxy_required: true },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const line = screen.getByTestId("proxy-required");
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    const save = screen.getByRole("button", { name: /save proxy/i });
    await waitFor(() => expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED));
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(toggle).not.toBeChecked();

    // The refetch lands on a dead server; the earlier answer is still cached.
    server.failSettings(true);
    void queryClient.invalidateQueries({ queryKey: keys.settings() });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "error"));
    // The three signals agree: line unknown, switch closed, save closed.
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);
    expect(save).toBeDisabled();
    // The user's choice is what the switch still shows, not a guess...
    expect(toggle).not.toBeChecked();
    // ...and a closed switch drops the click.
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();

    // GROUND: the answer returns; the switch reopens WITH the user's choice
    // and the save carries it.
    server.failSettings(false);
    void queryClient.invalidateQueries({ queryKey: keys.settings() });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "required"));
    await waitFor(() => expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED));
    expect(toggle).not.toBeChecked();
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => {
      const posts = postsTo(server.fetchMock, "/settings/proxy");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_required).toBe(
        false,
      );
    });
  });

  // The untouched half of the same window. Nobody moved the switch, the
  // refetch failed, and the switch closes with the line and the button.
  // What it shows while closed is the last answer the server gave, not off:
  // off is a claim about egress, and this unit exists to stop that claim
  // being made out of a missing answer. The mirror is what holds it there,
  // by writing an ANSWER into the switch and nothing else.
  it("keeps the last answer on show when an untouched switch closes on a lost refetch", async () => {
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_required: true },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const line = screen.getByTestId("proxy-required");
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "required"));
    expect(toggle).toBeChecked();

    server.failSettings(true);
    void queryClient.invalidateQueries({ queryKey: keys.settings() });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "error"));
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);
    expect(screen.getByRole("button", { name: /save proxy/i })).toBeDisabled();
    expect(toggle).toBeChecked();
  });

  // GROUND for the test above: the same script over a server that says the
  // proxy is optional. The switch comes through the failed refetch
  // unchecked, so "still checked" up there is the answer being held rather
  // than a switch that reads checked whatever is done to it.
  it("comes through the same lost refetch unchecked when the answer was optional - the ground", async () => {
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_required: false },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const line = screen.getByTestId("proxy-required");
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "optional"));
    expect(toggle).not.toBeChecked();

    server.failSettings(true);
    void queryClient.invalidateQueries({ queryKey: keys.settings() });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "error"));
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);
    expect(toggle).not.toBeChecked();
  });

  // The three signals are ONE signal, and this is the test that says so.
  // Each of them used to spell "the server's answer is not in hand" in its
  // own terms - the switch on `serverRequired == null`, the button and the
  // line on "pending or error" - and the three sets coincided only because
  // proxy_required is a bare boolean in the schema. They now read a single
  // named term, and what pins them to it is asserting all three in the same
  // breath, in both readings. The URL is typed first so that in the unknown
  // reading the Save is shut by the missing answer and not by having
  // nothing to send.
  it("shuts the switch, the save and the line on one reading of the missing answer", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({
        "/settings/proxy/health": { body: proxyHealthFixture },
        "POST /settings/proxy": { body: { ok: true } },
      }),
      { proxy_required: true },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const line = screen.getByTestId("proxy-required");
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    const save = screen.getByRole("button", { name: /save proxy/i });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "required"));
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );

    // GROUND, the answer in hand: the line prints it, the switch is open,
    // the button is open. Without this half the assertions below would pass
    // on a panel that is shut all the time.
    expect(line).toHaveTextContent("Required: Yes");
    expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED);
    expect(save).toBeEnabled();

    // The answer is lost under a refetch that fails over a warm cache.
    server.failSettings(true);
    void queryClient.invalidateQueries({ queryKey: keys.settings() });
    await waitFor(() => expect(line).toHaveAttribute("data-state", "error"));
    expect(line).toHaveTextContent("Required: unknown");
    expect(toggle).toHaveAttribute(...SWITCH_CLOSED);
    expect(save).toBeDisabled();
  });

  // T-56z, the ground: the same script with the fetch answering. The
  // controls open and the save carries the value the server holds.
  it("sends the value the server holds once its answer is in hand - the ground", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "POST /settings/proxy": { body: { ok: true } },
      "/settings": {
        body: { ...settingsFixture, proxy_configured: true, proxy_required: false },
      },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    await waitFor(() => expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED));
    expect(toggle).not.toBeChecked();

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => {
      const posts = postsTo(fetchMock, "/settings/proxy");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_required).toBe(
        false,
      );
    });
    // POSITIVE CONTROL for `sentBodies`, the reader T-56's absence rests on:
    // the same reader sees the body that did leave.
    expect(
      sentBodies(fetchMock).some((b) => b.proxy_required === false),
    ).toBe(true);
  });

  // POSITIVE CONTROL: the value the old code would have replaced with false.
  it("comes up on when the server says required, and the save sends true", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "POST /settings/proxy": { body: { ok: true } },
      "/settings": {
        body: { ...settingsFixture, proxy_configured: true, proxy_required: true },
      },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    const toggle = screen.getByRole("switch", { name: "Require proxy" });
    // Waits on the ANSWER arriving, the same state the dirty-switch test
    // waits on, not on the switch opening. A switch that is never closed is
    // already open at mount, and waiting on that read the toggle before the
    // fetch had landed: the assertion below would then have reported the
    // starting value under any mutation that drops the gate, which is a
    // reading of the gate and not of what this test is the control for.
    await waitFor(() =>
      expect(screen.getByTestId("proxy-required")).toHaveAttribute(
        "data-state",
        "required",
      ),
    );
    expect(toggle).not.toHaveAttribute(...SWITCH_CLOSED);
    expect(toggle).toBeChecked();

    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));

    await waitFor(() => {
      const posts = postsTo(fetchMock, "/settings/proxy");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_required).toBe(
        true,
      );
    });
  });
});

/**
 * The alias box, now seeded from the stored name (Q-46, the remnant half).
 *
 * The box used to open empty with the stored name printed on a read-only
 * line above it, so "empty" meant two things at once - untouched, or
 * cleared - and the panel told them apart by whether a buffer existed. Now
 * the box shows the name, an empty box is a choice made in view of it, and
 * dirty is the box disagreeing with the stored name. The save is closed
 * while the settings fetch is out or failed, and the cache keeps the stored
 * name when a full save sends null, exactly as the alias-only save already
 * did.
 */
describe("the alias box shows the stored name", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-78.
  it("opens with the name the server holds, and a shown name is not an edit", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: { ...settingsFixture, proxy_configured: true, proxy_alias: "work" },
      },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    const alias = screen.getByLabelText("Proxy alias input");
    await waitFor(() => expect(alias).toHaveValue("work"));
    // Seeded, not typed: nothing has changed, so there is nothing to save.
    expect(screen.getByRole("button", { name: /save proxy/i })).toBeDisabled();
  });

  // GROUND for T-78.
  it("opens empty when the server holds no name - the ground", async () => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": {
        body: { ...settingsFixture, proxy_configured: true, proxy_alias: null },
      },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    await screen.findByText("Proxy configured");
    expect(screen.getByLabelText("Proxy alias input")).toHaveValue("");
  });

  // T-79, the fetch still out. The ground is in the same run: the answer
  // lands, the same button opens, and the save carries the stored name back.
  it("keeps the save closed while the settings fetch is out, then sends the stored name once it lands", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_alias: "work" },
    );
    const release = server.holdSettings();
    renderWithQueryClient(<ProxySection />, { wrapper });
    expect(screen.getByTestId("proxy-required")).toHaveAttribute(
      "data-state",
      "pending",
    );
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    await settle();
    expect(postsTo(server.fetchMock, "/settings/proxy")).toHaveLength(0);
    expect(sentBodies(server.fetchMock).some((b) => "proxy_alias" in b)).toBe(
      false,
    );

    // GROUND.
    release();
    await waitFor(() => expect(save).toBeEnabled());
    expect(screen.getByLabelText("Proxy alias input")).toHaveValue("work");
    await user.click(save);
    await waitFor(() => {
      const posts = postsTo(server.fetchMock, "/settings/proxy");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBe("work");
    });
    // POSITIVE CONTROL for `sentBodies`: the reader the absence above rests
    // on sees the name once it does leave.
    expect(
      sentBodies(server.fetchMock).some((b) => b.proxy_alias === "work"),
    ).toBe(true);
  });

  // T-79, the fetch failed.
  it("keeps the save closed after the settings fetch failed, and no request carries a name", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "POST /settings/proxy": { body: { ok: true } },
      "/settings": { status: 500, body: { detail: "internal_error" } },
    });
    renderWithQueryClient(<ProxySection />, { wrapper });
    await waitFor(() =>
      expect(screen.getByTestId("proxy-required")).toHaveAttribute(
        "data-state",
        "error",
      ),
    );
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://proxy.test.com",
    );
    const save = screen.getByRole("button", { name: /save proxy/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    await settle();
    expect(postsTo(fetchMock, "/settings/proxy")).toHaveLength(0);
    expect(sentBodies(fetchMock).some((b) => "proxy_alias" in b)).toBe(false);
  });

  // T-80, with the positive control shared by the three tests above it.
  it("keeps the stored name in the cache after a full save that sent null, so the box never blanks", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_alias: "work" },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const alias = screen.getByLabelText("Proxy alias input");
    await waitFor(() => expect(alias).toHaveValue("work"));

    // From here the refetch is held, so the window between "save answered"
    // and "refetch landed" stays open long enough to be looked at.
    const release = server.holdSettings();
    await user.clear(alias);
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://new.proxy.test",
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));
    const line = await screen.findByRole("status");
    expect(line).toHaveAttribute("data-feedback", "success");

    // The save sent null: the box was emptied on purpose.
    const posts = postsTo(server.fetchMock, "/settings/proxy");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBeNull();
    // The cache still says "work": the server is writing its default name
    // and only the refetch knows which name that is. Writing the null
    // through would have blanked the box for exactly this window.
    expect(
      queryClient.getQueryData<Settings>(keys.settings())?.proxy_alias,
    ).toBe("work");
    expect(alias).toHaveValue("work");

    // POSITIVE CONTROL: the refetch lands and the default replaces it - a
    // return to the default, not a deletion.
    release();
    await waitFor(() => expect(alias).toHaveValue(SERVER_DEFAULT_PROXY_NAME));
    expect(
      queryClient.getQueryData<Settings>(keys.settings())?.proxy_alias,
    ).toBe(SERVER_DEFAULT_PROXY_NAME);
  });

  // The other half of the same cache rule: a name we KNOW goes into the
  // cache ahead of the refetch. T-80 pins that null does not; this pins that
  // a typed name does, so a stale name cannot flash back after a save. It is
  // also the stand-in server's positive control: the name comes back as
  // itself, so the default in the tests above is caused by the null they
  // sent and not by a stand-in that always answers the default.
  it("keeps a name just saved in the cache before the refetch lands - the known-name half", async () => {
    const user = userEvent.setup();
    const server = proxyServer(
      mockFetch({ "/settings/proxy/health": { body: proxyHealthFixture } }),
      { proxy_alias: "work" },
    );
    const { queryClient } = renderWithQueryClient(<ProxySection />, {
      wrapper,
    });
    const alias = screen.getByLabelText("Proxy alias input");
    await waitFor(() => expect(alias).toHaveValue("work"));

    const release = server.holdSettings();
    await user.clear(alias);
    await user.type(alias, "home");
    await user.type(
      screen.getByLabelText("Proxy URL input"),
      "https://new.proxy.test",
    );
    await user.click(screen.getByRole("button", { name: /save proxy/i }));
    const line = await screen.findByRole("status");
    expect(line).toHaveAttribute("data-feedback", "success");

    const posts = postsTo(server.fetchMock, "/settings/proxy");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0][1]?.body as string).proxy_alias).toBe("home");
    // Held window: the cache and the box already say "home", not "work".
    expect(
      queryClient.getQueryData<Settings>(keys.settings())?.proxy_alias,
    ).toBe("home");
    expect(alias).toHaveValue("home");

    release();
    await waitFor(() =>
      expect(
        queryClient.getQueryData<Settings>(keys.settings())?.proxy_alias,
      ).toBe("home"),
    );
    expect(alias).toHaveValue("home");
  });
});

describe("the two proxy fields keep the size they drew before (KURAL 3.5)", () => {
  // Master's field base carried a breakpoint size that beat the smaller one
  // this call site sets from 768 px on, so the field drew at the label step
  // in a narrow window and at the control step in a wide one. The base
  // carries no breakpoint size now, and the call site says both. Read
  // through the compiled sheet on either side of the breakpoint.
  let sizeAt: (el: Element, widthPx: number) => number;
  beforeAll(async () => {
    const root = parseSheet(await loadGlassRightCss());
    const table = rootTokens(root);
    const rem = remPx(root);
    sizeAt = (el, widthPx) => fontSizePx(root, table, rem, el, { viewportPx: widthPx });
  }, 60_000);
  beforeEach(() => {
    mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
    });
  });
  afterEach(() => vi.restoreAllMocks());

  for (const name of ["Proxy URL input", "Proxy alias input"]) {
    it(`${name}: the label step below 768 px, the control step from there on`, async () => {
      renderWithQueryClient(<ProxySection />, { wrapper });
      const field = await screen.findByLabelText(name);
      expect(sizeAt(field, MD_FROM_PX - 1), "in a narrow window").toBe(typeScale.label.px);
      expect(sizeAt(field, MD_FROM_PX), "in a wide window").toBe(typeScale.control.px);
    });
  }
});
