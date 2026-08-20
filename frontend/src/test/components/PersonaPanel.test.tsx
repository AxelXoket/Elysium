import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { RightPanel } from "@/components/layout/RightPanel";
import { PersonaPanel } from "@/components/persona/PersonaPanel";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import type { Persona } from "@/lib/schemas/personas";
import type { ReactNode } from "react";

const activePersona: Persona = {
  id: 1,
  display_name: "Focused Self",
  description: "Prefers concise, careful answers.",
  is_active: true,
  created_at: "2026-01-01T00:00:00",
  updated_at: "2026-01-01T00:00:00",
};

const inactivePersona: Persona = {
  id: 2,
  display_name: "Formal Self",
  description: "Prefers formal wording.",
  is_active: false,
  created_at: "2026-01-02T00:00:00",
  updated_at: "2026-01-02T00:00:00",
};

function wrapper({ children }: { children: ReactNode }) {
  // GenerationSettingsProvider mirrors production (AppShell provides it):
  // RightPanel keeps every tab mounted, so the Models panel's settings
  // consumers run even when the Persona tab is active.
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

function mockPersonaApi(initialPersonas: Persona[]) {
  let personas = initialPersonas.map((persona) => ({ ...persona }));
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (!url.includes("/personas")) {
      return new Response(JSON.stringify({ detail: "not_found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }

    if (method === "GET") {
      return json(personas);
    }

    if (method === "POST" && url.endsWith("/personas")) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const created: Persona = {
        id: 99,
        display_name: body.display_name,
        description: body.description,
        is_active: false,
        created_at: "2026-01-03T00:00:00",
        updated_at: "2026-01-03T00:00:00",
      };
      personas = [...personas, created];
      return json(created, 201);
    }

    if (method === "PATCH") {
      const id = Number(url.match(/\/personas\/(\d+)/)?.[1]);
      const body = JSON.parse(String(init?.body ?? "{}"));
      personas = personas.map((persona) =>
        persona.id === id
          ? {
              ...persona,
              ...body,
              updated_at: "2026-01-04T00:00:00",
            }
          : persona,
      );
      return json(personas.find((persona) => persona.id === id));
    }

    if (method === "DELETE") {
      const id = Number(url.match(/\/personas\/(\d+)/)?.[1]);
      personas = personas.filter((persona) => persona.id !== id);
      return json({ ok: true });
    }

    if (method === "POST" && url.includes("/select")) {
      const id = Number(url.match(/\/personas\/(\d+)\/select/)?.[1]);
      personas = personas.map((persona) => ({
        ...persona,
        is_active: persona.id === id,
      }));
      return json({ ok: true, selected_persona_id: id });
    }

    return json({ detail: "not_found" }, 404);
  });

  vi.stubGlobal("fetch", mock);
  return mock;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("PersonaPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
    useUiStore.setState({ activeRightPanelTab: "persona" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders real Persona Panel UI in the right Persona tab", async () => {
    mockPersonaApi([]);
    renderWithQueryClient(<RightPanel />, { wrapper });

    expect(screen.getByRole("tab", { name: /persona/i })).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Persona" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/user identity/i)).toBeInTheDocument();
    expect(screen.queryByText(/coming in/i)).not.toBeInTheDocument();
  });

  it("renders empty state when no personas exist", async () => {
    mockPersonaApi([]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    expect(await screen.findByText("No personas yet")).toBeInTheDocument();
    expect(screen.getByText(/No active persona/i)).toBeInTheDocument();
  });

  it("renders persona list and highlights backend active persona", async () => {
    mockPersonaApi([activePersona, inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    expect((await screen.findAllByText("Focused Self")).length).toBeGreaterThan(0);
    expect(screen.getByText("Formal Self")).toBeInTheDocument();

    const activeCard = screen.getByTestId("persona-card-1");
    expect(within(activeCard).getByText("Active")).toBeInTheDocument();
    expect(activeCard.className).toContain("is-active");
  });

  it("shows the required privacy note", async () => {
    mockPersonaApi([]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    expect(
      await screen.findByText(
        "Only the selected persona is used in generation. Saved inactive personas are not sent.",
      ),
    ).toBeInTheDocument();
  });

  it("opens create form inline and creates a persona", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([]);
    const localSetItem = vi.spyOn(Storage.prototype, "setItem");
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    await user.click(await screen.findByRole("button", { name: /new persona/i }));
    await user.type(screen.getByLabelText(/display name/i), "Quiet Self");
    await user.type(screen.getByLabelText(/description/i), "Prefers soft replies.");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      const postCall = mock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/personas") &&
          (init as RequestInit | undefined)?.method === "POST" &&
          !String(url).includes("/select"),
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse((postCall?.[1] as RequestInit).body as string);
      expect(body).toEqual({
        display_name: "Quiet Self",
        description: "Prefers soft replies.",
      });
    });
    expect(localSetItem).not.toHaveBeenCalled();
  });

  it("opens edit form inline and patches only changed fields", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([activePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-1");
    await user.click(within(card).getByRole("button", { name: /edit/i }));
    await user.clear(screen.getByLabelText(/description/i));
    await user.type(screen.getByLabelText(/description/i), "Now prefers direct replies.");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      const patchCall = mock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/personas/1") &&
          (init as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      const body = JSON.parse((patchCall?.[1] as RequestInit).body as string);
      expect(body).toEqual({ description: "Now prefers direct replies." });
    });
  });

  it("shows delete confirmation before deleting", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /delete/i }));

    expect(screen.getByText("Delete this persona?")).toBeInTheDocument();
    expect(
      mock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/personas/2") &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("the confirm button is painted by the light-panel danger pattern, not the dark shell's token", async () => {
    // This panel lives inside `.glass-right`, the app's one LIGHT surface.
    // shadcn's `variant="destructive"` paints from `--destructive`, which
    // `:root` sets for the DARK shell and `.glass-right` never overrides, so
    // the confirm button measured 3.29:1 at rest and 2.93:1 on hover - below
    // the 4.5:1 AA floor for text and below even the 3:1 floor for graphics,
    // and WORSE than the trigger button two blocks away that opens it.
    //
    // Asserted on the rendered DOM rather than on the source file, and on
    // the CLASS rather than the colour, because jsdom does not resolve
    // Tailwind's generated utility rules - glass-right-danger-contrast.test.ts
    // says so in its own module comment. The NUMBER for this class is proved
    // separately, by glass-right-danger-contrast.test.ts's
    // "persona danger ACTION colour" case - which had to be written after a
    // watchdog measured that nothing defended it: setting the class back to
    // the failing rose left all 1654 frontend tests green. This test holds
    // the component to the pattern; that one holds the pattern to its ratio.
    const user = userEvent.setup();
    mockPersonaApi([inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /delete/i }));

    // GROUND: the confirm step really is open, so the query below is looking
    // at the confirm button and not at the trigger it replaced.
    expect(screen.getByText("Delete this persona?")).toBeInTheDocument();
    const confirm = screen
      .getAllByRole("button", { name: /delete/i })
      .find((el) => el.textContent?.trim() === "Delete");
    expect(confirm, "the confirm button disappeared").toBeDefined();

    expect(confirm!.className).toContain("persona-danger-action");
    // The discriminating half: naming the house class is not enough if the
    // destructive variant is still applied alongside it.
    expect(confirm!.className).not.toMatch(/\bbg-destructive\b/);
    expect(confirm!.className).not.toMatch(/\btext-destructive\b/);
  });

  it("cancel delete hides confirmation without calling delete", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /delete/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByText("Delete this persona?")).not.toBeInTheDocument();
    expect(
      mock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/personas/2") &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("confirm delete calls the delete mutation", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /delete/i }));
    await user.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/personas/2") &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toBe(true);
    });
  });

  it("deletes the card that was clicked and leaves its neighbour standing", async () => {
    // Every delete test above runs against a list of ONE, where "the right
    // row died" cannot be told apart from "a row died". Two is the list
    // anybody who bothers with personas at all actually has.
    const user = userEvent.setup();
    const mock = mockPersonaApi([activePersona, inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /delete/i }));
    // Scoped to this card on purpose. The confirmation replaces the trigger
    // INSIDE the card it belongs to, so with a neighbour on screen an
    // unscoped query matches that neighbour's still-live Delete button too -
    // which is the whole reason a one-row list could not catch a wrong-row
    // bug in the first place.
    await user.click(
      within(await screen.findByTestId("persona-card-2")).getByRole("button", {
        name: /^delete$/i,
      }),
    );

    await waitFor(() => {
      expect(screen.queryByTestId("persona-card-2")).not.toBeInTheDocument();
    });
    expect(
      screen.getByTestId("persona-card-1"),
      "the other persona went with it",
    ).toBeInTheDocument();

    const deletes = mock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
    );
    expect(deletes, "more than one persona was deleted").toHaveLength(1);
    expect(String(deletes[0][0])).toMatch(/\/personas\/2$/);
  });

  it("says so when the server refuses to create a persona", async () => {
    // The panel had no failure test of any kind: create, edit, delete and
    // select were only ever asked of a server that says yes. Deleting the
    // pushError from the create handler left all 1315 tests green.
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if ((init?.method ?? "GET") === "POST" && url.endsWith("/personas")) {
          return json({ detail: "vault_locked" }, 423);
        }
        if (url.includes("/personas")) return json([]);
        return json({ detail: "not_found" }, 404);
      }),
    );
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    await user.click(await screen.findByRole("button", { name: /new persona/i }));
    await user.type(screen.getByLabelText(/display name/i), "Quiet Self");
    await user.type(screen.getByLabelText(/description/i), "Prefers soft replies.");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(
        useErrorStore.getState().errors,
        "the persona was refused and nothing told the reader",
      ).toHaveLength(1);
    });
  });

  it("select persona calls select mutation", async () => {
    const user = userEvent.setup();
    const mock = mockPersonaApi([activePersona, inactivePersona]);
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    const card = await screen.findByTestId("persona-card-2");
    await user.click(within(card).getByRole("button", { name: /^select$/i }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/personas/2/select") &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  it("keeps panel stable during loading", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>(() => {
            // intentionally pending
          }),
      ),
    );
    renderWithQueryClient(<PersonaPanel />, { wrapper });

    expect(screen.getByRole("button", { name: /new persona/i })).toBeInTheDocument();
  });
});
