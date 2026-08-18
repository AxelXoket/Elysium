/**
 * VaultGate.test.tsx - boot gate for full-DB encryption.
 *
 * A stateful fetch stub plays the backend: status flips to unlocked after a
 * successful init/unlock, so the invalidation → refetch → children flow is
 * exercised end to end (the same path the real app takes).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  renderWithQueryClient,
  createTestQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { VaultGate } from "@/components/vault/VaultGate";

interface VaultSim {
  initialized: boolean;
  unlocked: boolean;
  passphrase: string | null;
}

/** Stateful backend stand-in for /vault/*. */
function stubVaultFetch(sim: VaultSim) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status });

      if (url.endsWith("/vault/status")) {
        return json({ initialized: sim.initialized, unlocked: sim.unlocked });
      }
      if (url.endsWith("/vault/init")) {
        sim.initialized = true;
        sim.unlocked = true;
        sim.passphrase = body.passphrase;
        return json({ ok: true, migrated: false });
      }
      if (url.endsWith("/vault/unlock")) {
        if (body.passphrase !== sim.passphrase) {
          return json({ detail: "wrong_passphrase" }, 401);
        }
        sim.unlocked = true;
        return json({ ok: true });
      }
      return json({}, 404);
    }),
  );
}

const APP_MARKER = <div data-testid="app-root">app</div>;

describe("VaultGate", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the app directly when the vault is unlocked", async () => {
    stubVaultFetch({ initialized: true, unlocked: true, passphrase: "x" });
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);
    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });

  it("walks first-run setup: create passphrase → app", async () => {
    const user = userEvent.setup();
    stubVaultFetch({ initialized: false, unlocked: false, passphrase: null });
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);

    await screen.findByText("Protect your world");
    // Exact labels, not /passphrase/i - the reveal buttons carry
    // "Show passphrase" and would otherwise match the regex too.
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "seaside-orchid-9",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));

    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });

  it("FF15: setup copy names the wallpaper as the encryption exception", async () => {
    stubVaultFetch({ initialized: false, unlocked: false, passphrase: null });
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);

    await screen.findByText("Protect your world");
    // The claim is narrowed: the decorative wallpaper is NOT encrypted.
    expect(
      screen.getByText(/except the decorative chat wallpaper/i),
    ).toBeInTheDocument();
  });

  it("rejects mismatched entries locally without calling the API", async () => {
    const user = userEvent.setup();
    stubVaultFetch({ initialized: false, unlocked: false, passphrase: null });
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);

    await screen.findByText("Protect your world");
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "different-thing-1",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));

    expect(
      await screen.findByText("The two entries do not match."),
    ).toBeInTheDocument();
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) =>
      String(c[0]),
    );
    expect(calls.some((u) => u.endsWith("/vault/init"))).toBe(false);
  });

  it("locks: wrong passphrase shows the error and clears the field, right one opens", async () => {
    const user = userEvent.setup();
    stubVaultFetch({
      initialized: true,
      unlocked: false,
      passphrase: "right-horse-42",
    });
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);

    await screen.findByText("Elysium is locked");
    await user.type(screen.getByLabelText("Passphrase"), "wrong-guess-11");
    await user.click(screen.getByRole("button", { name: "Unlock" }));

    expect(await screen.findByText("Wrong passphrase.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Passphrase")).toHaveValue("");
    });

    await user.type(screen.getByLabelText("Passphrase"), "right-horse-42");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByTestId("app-root")).toBeInTheDocument();
  });
});

describe("VaultGate lock hygiene", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("purges every non-vault query from the cache when the vault locks", async () => {
    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = createTestQueryClient();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>, {
      client: qc,
    });
    await screen.findByTestId("app-root");

    // Seed user-data caches the way a running app would hold them.
    qc.setQueryData(["chats"], [{ id: 1 }]);
    qc.setQueryData(["messages", 1], [{ id: 10, content: "private text" }]);
    qc.setQueryData(["characters"], [{ id: 2 }]);
    qc.setQueryData(["settings"], { api_key_set: true });
    // Two keys the four obvious ones do not cover. The predicate is written
    // as "everything that is not vault", and the way to break that without
    // breaking this test is to turn it into an allowlist of the names a test
    // author would think to seed. These are here so that rewrite fails.
    qc.setQueryData(["personas"], [{ id: 3, name: "a persona" }]);
    qc.setQueryData(["tts", "models"], [{ uid: "voice-1" }]);

    // Backend locks out from under the app; the gate refetches status.
    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });

    await screen.findByText("Elysium is locked");
    // Watch-point 3: EVERY key that is not the gate's own must be gone -
    // a prefix/exact-key mistake here would leave chat text in RAM.
    await waitFor(() => {
      const roots = qc
        .getQueryCache()
        .getAll()
        .map((q) => q.queryKey[0]);
      expect(roots.length).toBeGreaterThan(0);
      expect(roots.every((k) => k === "vault")).toBe(true);
    });
    expect(qc.getQueryData(["messages", 1])).toBeUndefined();
    expect(qc.getQueryData(["chats"])).toBeUndefined();
    expect(qc.getQueryData(["characters"])).toBeUndefined();
    expect(qc.getQueryData(["settings"])).toBeUndefined();
  });

  it("silences a reply being read aloud when the vault locks", async () => {
    // The audio element lives outside React, so unmounting the app does not
    // stop it. Without an explicit stop, a private conversation keeps being
    // narrated over a screen that says the vault is closed, which is the one
    // way this app could leak content with the lock screen showing. The
    // adjacent comment in VaultGate calls this "audit-2", so it was found and
    // fixed once already; nothing was pinning it.
    const playerStore = await import("@/lib/voice/playerStore");
    const stopped = vi.spyOn(playerStore, "stopVoicePlayback");

    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = createTestQueryClient();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>, { client: qc });
    await screen.findByTestId("app-root");
    expect(stopped).not.toHaveBeenCalled();

    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });
    await screen.findByText("Elysium is locked");

    expect(stopped, "the voice kept reading over the lock screen")
      .toHaveBeenCalled();
    stopped.mockRestore();
  });

  it("keeps the two navigation ids across a lock, and nothing else", async () => {
    // APPROVAL now, not characterisation. Decided 2026-08-18: the
    // ids stay.
    //
    // What survives is selectedChatId and selectedCharacterId - two
    // numbers naming which conversation and which character were
    // open. They are classified as PREFERENCES rather than content,
    // and that classification is not casual: uiStore's partialize
    // allowlist, static-safety's S-09b and the README line about the
    // local profile all say the same thing. The reason they persist
    // is that unlocking should put you back where you were.
    //
    // The error store is left standing too: its entries are already
    // sanitised sentences from the catalogue, never message text. A
    // toast queued just before a lock can still appear after the
    // next unlock, which is untidy rather than a leak.
    //
    // What did NOT survive the decision is the mutation cache - see
    // the test below. It held the API key verbatim.
    //
    // The purge above is a predicate over the QUERY cache. Zustand stores are
    // not in it, so nothing here reaches them, and two of them are still
    // holding things after the vault has closed:
    //
    //   useErrorStore  - a toast raised just before the lock is still queued,
    //                    and ErrorToastStack remounts on unlock, so it can
    //                    pop up moments after the passphrase is accepted,
    //                    belonging to a session that ended.
    //   useUiStore     - selectedChatId and selectedCharacterId survive, and
    //                    are additionally persisted to localStorage, so they
    //                    outlive the lock, the app, and the machine restart.
    //
    // Neither holds message text. They hold numeric ids and an already
    // sanitised sentence, which is why this is recorded rather than treated
    // as a leak of content. What it costs is the promise's shape: "locked"
    // should mean the session is over, and for these two it does not.
    const { useErrorStore } = await import("@/lib/errors");
    const { useUiStore } = await import("@/lib/store/uiStore");

    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = createTestQueryClient();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>, { client: qc });
    await screen.findByTestId("app-root");

    useErrorStore.getState().clearAll();
    useErrorStore.getState().pushErrorDirect("chat_not_found", "Chat not found.");
    useUiStore.setState({ selectedChatId: 41, selectedCharacterId: 7 });

    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });
    await screen.findByText("Elysium is locked");

    // The query cache emptied; these did not.
    expect(useErrorStore.getState().errors).toHaveLength(1);
    expect(useUiStore.getState().selectedChatId).toBe(41);
    expect(useUiStore.getState().selectedCharacterId).toBe(7);

    useErrorStore.getState().clearAll();
    useUiStore.setState({ selectedChatId: null, selectedCharacterId: null });
  });

  it("does not leave the API key in the mutation cache when the vault locks", async () => {
    // The one thing on this screen that was a real leak rather than untidy.
    //
    // removeQueries sweeps the QUERY cache. A mutation is not in it: TanStack
    // keeps a mutation's `variables` until garbage collection, five minutes
    // after its last observer goes. And the settings save carries the
    // OpenRouter API key as its variables, verbatim - so the key sat in
    // memory for five minutes behind a lock screen that said the session was
    // over.
    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = createTestQueryClient();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>, { client: qc });
    await screen.findByTestId("app-root");

    const SECRET = "sk-or-v1-should-not-outlive-the-lock";
    const mutation = qc.getMutationCache().build(qc, {
      mutationFn: async (key: string) => key,
    });
    await mutation.execute(SECRET);
    // Ground first: without this the assertion below passes on an empty cache
    // and proves nothing at all.
    expect(JSON.stringify(qc.getMutationCache().getAll())).toContain(SECRET);

    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });
    await screen.findByText("Elysium is locked");

    expect(
      JSON.stringify(qc.getMutationCache().getAll()),
      "the key the user typed is still in memory behind the lock screen",
    ).not.toContain(SECRET);
  });

  it("does not itself abort a stream - the unmount does", async () => {
    // REWRITTEN 2026-08-18, because the sentence underneath it was
    // wrong about production and would have kept being believed.
    //
    // It is true that VaultGate has no reference to streams: no
    // import, no stopChat, no abort. It is NOT true that a reply
    // running at lock time keeps running. The gate stops rendering
    // children, ChatCanvas unmounts, and useStreamingCompletion's
    // cleanup aborts every controller it holds - so the request does
    // end, and the cache is not repopulated. This test renders a
    // dummy child, which is exactly why it could not see that.
    //
    // So what it pins now is the narrow, true thing: the abort comes
    // from unmounting, not from the lock. That distinction matters -
    // if the app ever keeps the tree mounted behind the lock screen,
    // the abort disappears with it and this comment is the record of
    // where to look.
    //
    // The purge is a one-shot sweep of the query cache, and a live reply is
    // not in the cache: useStreamingCompletion writes deltas straight in with
    // qc.setQueryData(keys.messages(chatId), ...). VaultGate has no reference
    // to streams at all (no import, no stopChat, no abort), so a stream that
    // was running when the vault closed is still running afterwards, and its
    // next write RECREATES a messages entry under a key the lock believed it
    // had erased. Nothing mounted reads it while locked, so this is data
    // sitting in memory rather than data on screen, which is why it is
    // recorded and not called a breach.
    //
    // The mechanism is what this pins: the registry still holds the
    // controller and the controller was never aborted. Delete and Clear both
    // call stopChat for exactly this reason; locking does not.
    const { useStreamRegistry, registerStream } = await import(
      "@/lib/chat/streamRegistry"
    );

    const sim: VaultSim = { initialized: true, unlocked: true, passphrase: "x" };
    stubVaultFetch(sim);
    const qc = createTestQueryClient();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>, { client: qc });
    await screen.findByTestId("app-root");

    const controller = new AbortController();
    registerStream(9, controller);

    sim.unlocked = false;
    await qc.invalidateQueries({ queryKey: ["vault"] });
    await screen.findByText("Elysium is locked");

    expect(useStreamRegistry.getState().controllers.has(9)).toBe(true);
    expect(
      controller.signal.aborted,
      "the lock learned to stop in-flight replies",
    ).toBe(false);

    useStreamRegistry.setState({ controllers: new Map() });
  });
});

/**
 * Audit: what the gate says when something other than a wrong passphrase
 * happens, and what it does NOT say about the plaintext copy it left behind.
 */
describe("VaultGate - the states nobody rendered", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  function stubInitFailure(detail: string, status = 500) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (data: unknown, s = 200) =>
          new Response(JSON.stringify(data), { status: s });
        if (url.endsWith("/vault/status")) {
          return json({ initialized: false, unlocked: false });
        }
        if (url.endsWith("/vault/init")) return json({ detail }, status);
        return json({}, 404);
      }),
    );
  }

  async function attemptCreate() {
    const user = userEvent.setup();
    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);
    await screen.findByText("Protect your world");
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "seaside-orchid-9",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));
  }

  it("names the one-file fix instead of blaming the backend", async () => {
    // encrypted_db_without_identity: the vault data is intact and salt.bin is
    // gone. It used to read "Setup failed. Is the backend running?" - the
    // backend IS running, so the user retried forever, and nothing said that
    // restoring one 16-byte file recovers everything.
    stubInitFailure("encrypted_db_without_identity", 409);
    await attemptCreate();

    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent(/salt\.bin/);
    expect(error).not.toHaveTextContent(/backend running/i);
  });

  it("explains a failed init rather than pointing at the backend", async () => {
    stubInitFailure("vault_init_failed");
    await attemptCreate();
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent(/writable|free space/i);
    expect(error).not.toHaveTextContent(/backend running/i);
  });

  it("keeps the short-passphrase message", async () => {
    stubInitFailure("passphrase_too_short", 400);
    await attemptCreate();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /at least 12 characters/i,
    );
  });

  it("explains a passphrase that is long but repetitive", async () => {
    // Long enough to pass the length check and no harder to guess than its
    // shortest piece. "Too short" would be wrong and unhelpful here, so the
    // backend sends a different code and the screen has to carry it.
    stubInitFailure("passphrase_too_simple", 422);
    await attemptCreate();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /repetitive|unrelated words/i,
    );
  });

  it("explains a passphrase anyone would guess first", async () => {
    stubInitFailure("passphrase_too_common", 422);
    await attemptCreate();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /anyone guessing would try/i,
    );
  });

  it("names the plaintext copy the migration kept", async () => {
    // The screen just promised "everything ... is encrypted on disk with this
    // passphrase". For an upgrading user the pre-vault database is kept
    // readable beside the vault, forever - /vault/init has always reported it
    // and nothing rendered it.
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (data: unknown, s = 200) =>
          new Response(JSON.stringify(data), { status: s });
        if (url.endsWith("/vault/status")) {
          return json({ initialized: false, unlocked: false });
        }
        if (url.endsWith("/vault/init")) {
          return json({
            ok: true,
            migrated: true,
            backup: "app.db.plain.bak-1753400000",
          });
        }
        return json({}, 404);
      }),
    );

    renderWithQueryClient(<VaultGate>{APP_MARKER}</VaultGate>);
    await screen.findByText("Protect your world");
    await user.type(screen.getByLabelText("Passphrase"), "seaside-orchid-9");
    await user.type(
      screen.getByLabelText("Repeat passphrase"),
      "seaside-orchid-9",
    );
    await user.click(screen.getByRole("button", { name: "Create vault" }));

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("app.db.plain.bak-1753400000");
    expect(notice).toHaveTextContent(/not encrypted/i);
  });
});
