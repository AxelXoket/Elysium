/**
 * DraftPersistence.test.tsx - unsent text must outlive a remount.
 *
 * The bug these tests were written for: VaultGate swaps `children` for the
 * lock screen and its keyed motion wrapper remounts the whole subtree, so
 * every draft held in component state died with it. Composer drafts lived in
 * ChatCanvas (`liveDrafts`/`failedDrafts`), edit drafts lived in MessageBubble
 * (`editing`/`editDraft`), and a lock/unlock cycle destroyed both while a
 * chat switch preserved them - which is why this reads to a user as "only
 * locking eats my text".
 *
 * These are behavioural tests: they drive the real components through the
 * real gate and never look at where the state is kept. That is deliberate. A
 * test asserting "the store has an entry" would keep passing if the store
 * were wired to nothing.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { VaultGate } from "@/components/vault/VaultGate";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { keys } from "@/lib/query/keys";
import { useDraftStore, editDraftKey } from "@/lib/store/draftStore";
import { RESET_CONFIRM_PHRASE } from "@/components/vault/VaultGate";
import {
  mockFetchWithStreams,
  sseEventsFor,
  sseResponse,
  jsonResponse,
  controlledSseResponse,
} from "../helpers/streamMocks";
import {
  settingsFixture,
  modelListFixture,
  completionFixture,
  messageFixture,
  chatFixture,
} from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: ReactNode }) {
  return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
}

function msg(
  id: number,
  chatId: number,
  role: "user" | "assistant",
  content: string,
): Message {
  return {
    id,
    chat_id: chatId,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** Chat 1 carries a user turn so the edit pencil has something to open. */
const chat1Messages = [
  msg(1, 1, "assistant", "greeting"),
  msg(2, 1, "user", "original question"),
  msg(3, 1, "assistant", "old reply"),
];

interface VaultSim {
  unlocked: boolean;
}

/**
 * One fetch stub playing both the vault and the data endpoints, so a test can
 * lock the app mid-session the way the real gate does: flip the flag, let the
 * status query refetch, and the gate re-renders on its own.
 */
function stubAppFetch(
  sim: VaultSim,
  routes: Record<string, () => unknown>,
): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), {
          status,
          headers: { "Content-Type": "application/json" },
        });

      if (url.includes("/vault/status")) {
        return json({ initialized: true, unlocked: sim.unlocked });
      }
      // Every data endpoint answers 423 while locked, exactly as the backend
      // does. Without it the app would keep serving a locked vault's rows.
      if (!sim.unlocked) return json({ detail: "vault_locked" }, 423);

      for (const [pattern, body] of Object.entries(routes)) {
        if (url.includes(pattern)) return json(body());
      }
      return json({ detail: `No mock for ${url}` }, 404);
    }),
  );
}

const defaultRoutes: Record<string, () => unknown> = {
  "/settings": () => settingsFixture,
  "/models": () => modelListFixture,
  "/chats/1/messages": () => chat1Messages,
  "/chats/2/messages": () => [],
  "/chats": () => [],
};

const composer = () => screen.getByLabelText("Message") as HTMLTextAreaElement;

async function waitForComposerReady() {
  await waitFor(() => {
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });
}

/** Flip the vault shut and wait for the lock screen to actually be up. */
async function lock(sim: VaultSim, qc: QueryClient) {
  sim.unlocked = false;
  await act(async () => {
    await qc.invalidateQueries({ queryKey: keys.vault() });
  });
  await screen.findByText("Elysium is locked");
}

/** Open it again and wait for the app to come back. */
async function unlock(sim: VaultSim, qc: QueryClient) {
  sim.unlocked = true;
  await act(async () => {
    await qc.invalidateQueries({ queryKey: keys.vault() });
  });
  await waitForComposerReady();
}

describe("draft survival across remounts", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    // Stated here rather than leaned on: the global afterEach in test/setup.ts
    // also clears this store, but a suite whose beforeEach does not establish
    // its own preconditions is one config change away from silent bleed.
    useDraftStore.getState().clearAll();
    localStorage.clear();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
  });

  // 1. The behaviour that already worked, kept as a ground control. If this
  // one goes red the harness is broken, not the feature: it passes on the
  // ORIGINAL code, so it can only fail by regression.
  it("keeps a composer draft across a chat switch (A to B to A)", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    await user.type(composer(), "chat one text");
    act(() => useUiStore.setState({ selectedChatId: 2 }));
    await waitFor(() => expect(composer().value).toBe(""));

    act(() => useUiStore.setState({ selectedChatId: 1 }));
    await waitFor(() => expect(composer().value).toBe("chat one text"));
  });

  // 2. The reported bug, composer half.
  it("keeps a composer draft across a vault lock and unlock", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    const { queryClient } = renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await waitForComposerReady();

    await user.type(composer(), "half a thought I am not finished with");
    await lock(sim, queryClient);
    await unlock(sim, queryClient);

    expect(composer().value, "the lock screen ate an unsent composer draft").toBe(
      "half a thought I am not finished with",
    );
  });

  // 3. Isolation must survive the same trip.
  it("keeps several chats' drafts isolated across a lock", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    const { queryClient } = renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await waitForComposerReady();

    await user.type(composer(), "text for one");
    act(() => useUiStore.setState({ selectedChatId: 2 }));
    await waitFor(() => expect(composer().value).toBe(""));
    await user.type(composer(), "text for two");

    await lock(sim, queryClient);
    await unlock(sim, queryClient);

    // Chat 2 was open when the lock came down, so it is open now.
    expect(composer().value, "chat 2 lost its draft").toBe("text for two");
    act(() => useUiStore.setState({ selectedChatId: 1 }));
    await waitFor(() =>
      expect(composer().value, "chat 1 lost its draft").toBe("text for one"),
    );
  });

  // 4. The reported bug, message-edit half.
  it("keeps an open message edit, and its retyped text, across a lock", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    const { queryClient } = renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await waitForComposerReady();
    await screen.findByText("original question");

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = () => screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box());
    await user.type(box(), "a rewritten question");

    await lock(sim, queryClient);
    await unlock(sim, queryClient);
    await screen.findByText("greeting");

    // Both halves matter: the box must be OPEN again, and it must hold the
    // retyped text rather than the message's stored content.
    const reopened = await screen.findByRole("textbox", {
      name: "Edit message text",
    });
    expect(reopened, "the edit box did not survive the lock").toBeInTheDocument();
    expect(reopened, "the edit box came back with the wrong text").toHaveValue(
      "a rewritten question",
    );
  });

  // 4b. The half a "the text came back" assertion cannot see.
  it("restores the edit box WITHOUT selecting its text or stealing focus", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    const { queryClient } = renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await waitForComposerReady();
    await screen.findByText("original question");

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const opened = screen.getByRole("textbox", {
      name: "Edit message text",
    }) as HTMLTextAreaElement;
    await user.clear(opened);
    await user.type(opened, "a rewritten question");

    await lock(sim, queryClient);
    await unlock(sim, queryClient);
    await screen.findByText("greeting");

    const restored = (await screen.findByRole("textbox", {
      name: "Edit message text",
    })) as HTMLTextAreaElement;

    // Recovering the text and then highlighting all of it hands the user a
    // box where their next keystroke deletes everything that was recovered -
    // and the textarea is controlled, so the browser's own undo cannot get it
    // back. Saving the draft and arming a one-key delete is not saving it.
    expect(
      restored.selectionStart === 0 &&
        restored.selectionEnd === restored.value.length &&
        restored.value.length > 0,
      "the restored draft came back selected, so one keystroke would wipe it",
    ).toBe(false);

    // And a restore must not yank the caret across the page either: the box
    // can be anywhere in a long chat, and the user was not asking to go there.
    expect(
      document.activeElement,
      "restoring an edit box stole focus",
    ).not.toBe(restored);
  });

  // Ground control for the test above: the pencil press still selects, which
  // is what makes "type over it" work when the user DID ask for the box.
  it("still selects the text when the user opens the box themselves", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await screen.findByText("original question");

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", {
      name: "Edit message text",
    }) as HTMLTextAreaElement;

    expect(box.value).toBe("original question");
    expect(document.activeElement).toBe(box);
    expect(box.selectionStart).toBe(0);
    expect(box.selectionEnd).toBe(box.value.length);
  });

  // 5. Edit entries must not bleed between messages.
  it("keeps edit drafts of different messages apart", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    const twoUserTurns = [
      msg(1, 1, "user", "first question"),
      msg(2, 1, "assistant", "first reply"),
      msg(3, 1, "user", "second question"),
      msg(4, 1, "assistant", "second reply"),
    ];
    stubAppFetch(sim, {
      ...defaultRoutes,
      "/chats/1/messages": () => twoUserTurns,
    });
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await screen.findByText("second question");

    // Edit the SECOND user turn; the first must stay untouched and closed.
    const pencils = screen.getAllByRole("button", { name: "Edit message" });
    expect(pencils).toHaveLength(2);
    await user.click(pencils[1]);
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.type(box, "only the second one changed");

    const boxes = screen.getAllByRole("textbox", { name: "Edit message text" });
    expect(boxes, "editing one message opened another one's box").toHaveLength(1);
    expect(boxes[0]).toHaveValue("only the second one changed");
    expect(screen.getByText("first question")).toBeInTheDocument();
  });

  // 8. Cancel is the user saying "throw it away", and it has to stick.
  it("discards the edit draft on an explicit Cancel, across a lock too", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    const { queryClient } = renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await waitForComposerReady();
    await screen.findByText("original question");

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.type(box, "typed then abandoned");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("textbox", { name: "Edit message text" }),
      ).not.toBeInTheDocument(),
    );

    await lock(sim, queryClient);
    await unlock(sim, queryClient);
    await screen.findByText("original question");

    expect(
      screen.queryByRole("textbox", { name: "Edit message text" }),
      "a cancelled edit came back after the lock",
    ).not.toBeInTheDocument();
  });

  // Wiping the vault is the one event drafts must NOT survive.
  it("destroys every draft when the vault is reset", async () => {
    const user = userEvent.setup();
    // Starts as a real, initialised vault sitting locked - the only state the
    // reset door exists in. The wipe flips `initialized`, which is what makes
    // the gate swing to first-run setup on its own.
    const vault = { initialized: true };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (data: unknown, status = 200) =>
          new Response(JSON.stringify(data), {
            status,
            headers: { "Content-Type": "application/json" },
          });
        if (url.includes("/vault/status")) {
          return json({ initialized: vault.initialized, unlocked: false });
        }
        if (url.includes("/vault/reset")) {
          vault.initialized = false;
          return json({ ok: true, left: [] });
        }
        return json({ detail: "vault_locked" }, 423);
      }),
    );

    // Text the user was writing in the vault they are about to destroy.
    act(() => {
      const d = useDraftStore.getState();
      d.setComposerDraft(1, "something from the old vault");
      d.openEditDraft(1, 2, "an edit from the old vault");
    });

    renderWithQueryClient(
      <VaultGate>
        <ChatCanvas />
      </VaultGate>,
      { wrapper },
    );
    await screen.findByText("Elysium is locked");
    await user.click(
      screen.getByRole("button", { name: /forgot your passphrase\?/i }),
    );
    await screen.findByText("Start over instead");
    await user.type(
      screen.getByLabelText(new RegExp(RESET_CONFIRM_PHRASE)),
      RESET_CONFIRM_PHRASE,
    );
    await user.click(screen.getByRole("button", { name: "Delete everything" }));

    // Chat ids are AUTOINCREMENT and the wipe takes the sequence with the
    // database, so the NEW vault's first chat is id 1 again - the same key
    // these drafts are filed under. Surviving here means the first chat of a
    // fresh vault opens holding text from the vault the user just destroyed.
    await waitFor(() => {
      const state = useDraftStore.getState();
      expect(state.composer[1], "a draft outlived the vault wipe").toBeUndefined();
      expect(state.edits[editDraftKey(1, 2)]).toBeUndefined();
      expect(state.totalBytes).toBe(0);
    });
  });

  // 11. Persistence prohibition, at the behavioural level.
  it("writes no draft text to browser storage", async () => {
    const user = userEvent.setup();
    const sim: VaultSim = { unlocked: true };
    stubAppFetch(sim, defaultRoutes);
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    const composerCanary = "zzq-draft-canary-9137";
    const editCanary = "zzq-edit-canary-4471";
    await user.type(composer(), composerCanary);
    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.type(box, editCanary);

    // Browser storage is plaintext on disk, outside the vault, and outlives
    // the session the draft belongs to. Neither area may hold either canary.
    // PRECONDITIONS. Deleting the two typing blocks above used to leave this
    // test green, which means it was asserting that nothing had happened.
    expect(
      useDraftStore.getState().composer[1]?.text,
      "the composer canary never reached the store",
    ).toBe(composerCanary);
    expect(
      useDraftStore.getState().edits[editDraftKey(1, 2)]?.text,
      "the edit canary never reached the store",
    ).toBe(editCanary);

    const sweep = () =>
      [
        ...Object.values(localStorage),
        ...Object.values(sessionStorage),
      ].join(" ");
    expect(sweep(), "a composer draft reached device storage").not.toContain(
      composerCanary,
    );
    expect(sweep(), "an edit draft reached device storage").not.toContain(
      editCanary,
    );

    // POSITIVE CONTROL on the same sweep: a scan that cannot see a value that
    // IS there proves nothing by not seeing one that is not.
    localStorage.setItem("proof", composerCanary);
    expect(
      sweep(),
      "the storage sweep is blind, so its clean result meant nothing",
    ).toContain(composerCanary);
    localStorage.clear();
  });
});

/**
 * The send and edit pipelines, from the drafts' point of view: exactly one
 * outcome may discard a buffer, and every other outcome has to give it back.
 */
describe("drafts through the send and edit pipelines", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useDraftStore.getState().clearAll();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useDraftStore.getState().clearAll();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
  });

  /** Messages mirror server truth across a send, like liveSendRoutes does. */
  function sendRoutes(stream?: () => Promise<Response> | Response) {
    let sent = false;
    return {
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      "/chats/1/messages": {
        response: () =>
          jsonResponse(
            sent
              ? [
                  messageFixture,
                  completionFixture.user_message,
                  completionFixture.assistant_message,
                ]
              : [messageFixture],
          ),
      },
      "/chats/2/messages": { response: () => jsonResponse([]) },
      "/chats/1/complete/stream": {
        response: async () => {
          const res = stream
            ? await stream()
            : sseResponse(sseEventsFor(completionFixture));
          sent = true;
          return res;
        },
      },
      "/chats": { body: [] },
    };
  }

  // 6. Success clears its OWN entry and nothing else.
  it("a successful send clears only the sending chat's draft", async () => {
    const user = userEvent.setup();
    mockFetchWithStreams(sendRoutes());
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    // A draft parked in another chat must be untouched by chat 1's send.
    act(() => {
      useDraftStore.getState().setComposerDraft(2, "the other chat's draft");
    });

    await user.type(composer(), "this one gets sent");
    // PRECONDITION. Without it this passes on a composer that never took the
    // text: an empty box disables Send, and every assertion below is happy
    // with a chat that sent nothing at all.
    expect(useDraftStore.getState().composer[1]?.text).toBe("this one gets sent");

    const sent = mockFetchWithStreams(sendRoutes());
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(composer().value).toBe(""));
    // And the send has to have actually left, or "the draft is gone" is just
    // a description of an empty box.
    await waitFor(() =>
      expect(
        sent.mock.calls.some(
          (call: unknown[]) =>
            typeof call[0] === "string" &&
            call[0].includes("/complete/stream") &&
            (call[1] as RequestInit)?.method === "POST",
        ),
        "no completion request was ever made",
      ).toBe(true),
    );
    await waitFor(() =>
      expect(useDraftStore.getState().composer[1]).toBeUndefined(),
    );
    expect(
      useDraftStore.getState().composer[2]?.text,
      "a send in one chat cleared another chat's draft",
    ).toBe("the other chat's draft");
  });

  // 7. Failure hands the text back.
  it("a failed send restores the draft it tried to send", async () => {
    const user = userEvent.setup();
    mockFetchWithStreams(
      sendRoutes(() =>
        jsonResponse({ detail: "openrouter_completion_error" }, 502),
      ),
    );
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    await user.type(composer(), "the send that fails");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    // The composer empties on dispatch and fills again when the send fails:
    // the user never has to retype what the app already had.
    await waitFor(() =>
      expect(useDraftStore.getState().composer[1]?.text).toBe(
        "the send that fails",
      ),
    );
    await waitFor(() => expect(composer().value).toBe("the send that fails"));
  });

  /** Chat 1 with one user turn, and an edit stream that answers `outcome`. */
  function editRoutes(outcome: "ok" | "fail") {
    return {
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      // BEFORE the messages route on purpose: the edit URL is
      // /chats/1/messages/2/edit/stream, which CONTAINS "/chats/1/messages",
      // and the mock matches by substring with first-match-wins. Listed the
      // other way round the edit stream is served a JSON array of messages
      // and `done` never arrives.
      "/edit/stream": {
        response: () =>
          outcome === "fail"
            ? jsonResponse({ detail: "openrouter_completion_error" }, 502)
            : sseResponse(sseEventsFor(completionFixture)),
      },
      "/chats/1/messages": {
        response: () =>
          jsonResponse([
            msg(1, 1, "assistant", "greeting"),
            msg(2, 1, "user", "original question"),
            msg(3, 1, "assistant", "old reply"),
          ]),
      },
      "/chats": { body: [] },
    };
  }

  /** Open the pencil on the one user turn and retype it. */
  async function retype(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.type(box, "a rewritten question");
    await user.click(screen.getByRole("button", { name: "Save" }));
  }

  // 6, edit half. A committed edit is the one outcome that discards a buffer.
  it("a successful edit clears its own buffer", async () => {
    const user = userEvent.setup();
    mockFetchWithStreams(editRoutes("ok"));
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    // A buffer on a different message must not go with it.
    act(() => {
      useDraftStore.getState().openEditDraft(1, 99, "somebody else's edit");
    });

    await retype(user);

    await waitFor(() =>
      expect(
        useDraftStore.getState().edits[editDraftKey(1, 2)],
      ).toBeUndefined(),
    );
    expect(
      useDraftStore.getState().edits[editDraftKey(1, 99)]?.text,
      "a committed edit cleared another message's buffer",
    ).toBe("somebody else's edit");
  });

  // 7, edit half. Anything else hands the box back with the text in it.
  it("a failed edit reopens the box with the retyped text", async () => {
    const user = userEvent.setup();
    mockFetchWithStreams(editRoutes("fail"));
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    await retype(user);

    const key = editDraftKey(1, 2);
    await waitFor(() =>
      expect(useDraftStore.getState().edits[key]?.phase).toBe("editing"),
    );
    expect(useDraftStore.getState().edits[key]?.text).toBe(
      "a rewritten question",
    );
    // And the user can see it: the box is back on screen with their words.
    await waitFor(() =>
      expect(
        screen.getByRole("textbox", { name: "Edit message text" }),
      ).toHaveValue("a rewritten question"),
    );
  });

  // 9. Deletion cleans up only what really got deleted.
  it("a successful chat delete clears that chat's drafts, a failed one does not", async () => {
    const { useDeleteChat } = await import("@/lib/query/chats");
    const { renderHookWithQueryClient } = await import(
      "@/test/helpers/renderWithQueryClient"
    );

    mockFetchWithStreams({
      "/chats/1": { response: () => jsonResponse({ ok: true }) },
      "/chats": { body: [] },
    });

    act(() => {
      const d = useDraftStore.getState();
      d.setComposerDraft(1, "doomed chat draft");
      d.openEditDraft(1, 10, "doomed edit");
      d.setComposerDraft(2, "innocent bystander");
    });

    const { result } = renderHookWithQueryClient(() => useDeleteChat());
    await act(async () => {
      await result.current.mutateAsync(1);
    });

    const after = useDraftStore.getState();
    expect(after.composer[1]).toBeUndefined();
    expect(after.edits[editDraftKey(1, 10)]).toBeUndefined();
    expect(
      after.composer[2]?.text,
      "deleting one chat took another chat's draft",
    ).toBe("innocent bystander");
  });

  it("closes the box while the save is in flight, and keeps the text", async () => {
    // The box must be shut for the whole commit window: the entry still
    // exists (it has to, so a failure can give it back), so anything that
    // treated "an entry exists" as "the box is open" would leave a live
    // textarea on a message the server is already rewriting.
    const user = userEvent.setup();
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      "/edit/stream": { response: () => stream.response },
      "/chats/1/messages": {
        response: () =>
          jsonResponse([
            msg(1, 1, "assistant", "greeting"),
            msg(2, 1, "user", "original question"),
            msg(3, 1, "assistant", "old reply"),
          ]),
      },
      "/chats": { body: [] },
    });
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await retype(user);

    const key = editDraftKey(1, 2);
    await waitFor(() =>
      expect(useDraftStore.getState().edits[key]?.phase).toBe("committing"),
    );
    expect(
      screen.queryByRole("textbox", { name: "Edit message text" }),
      "the edit box stayed open while the save was in flight",
    ).not.toBeInTheDocument();
    // The text is held, not shown - that is what makes a failure recoverable.
    expect(useDraftStore.getState().edits[key]?.text).toBe(
      "a rewritten question",
    );
    stream.close();
  });

  it("an error frame arriving AFTER done does not reopen the box", async () => {
    // K-28's rule, from the drafts' side. Once the atomic swap has landed the
    // edit is committed, so a late error frame must not be read as a failure
    // and must not hand back a box on a message that has already been
    // rewritten - the user would be looking at an edit box on text that is
    // no longer there.
    const user = userEvent.setup();
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      "/edit/stream": { response: () => stream.response },
      "/chats/1/messages": {
        response: () =>
          jsonResponse([
            msg(1, 1, "assistant", "greeting"),
            msg(2, 1, "user", "original question"),
            msg(3, 1, "assistant", "old reply"),
          ]),
      },
      "/chats": { body: [] },
    });
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await retype(user);

    const key = editDraftKey(1, 2);
    await waitFor(() =>
      expect(useDraftStore.getState().edits[key]?.phase).toBe("committing"),
    );

    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "openai/gpt-4o",
      user_message: msg(2, 1, "user", "a rewritten question"),
      assistant_message: msg(4, 1, "assistant", "a new reply"),
    });
    await waitFor(() =>
      expect(useDraftStore.getState().edits[key]).toBeUndefined(),
    );

    // The late frame. Nothing may come back.
    stream.emit({ type: "error", status: 502, code: "openrouter_completion_error" });
    stream.close();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(
      useDraftStore.getState().edits[key],
      "a late error frame resurrected a committed edit's box",
    ).toBeUndefined();
    expect(
      screen.queryByRole("textbox", { name: "Edit message text" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the box and the text when a save cannot be dispatched", async () => {
    // The confirmation dialog's own button used to call the commit directly,
    // skipping the check that the save can actually land. The box closed, the
    // request was never made, and the retyped text sat in a phase that
    // renders nothing - invisible and unreachable for the life of the app.
    const user = userEvent.setup();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      "/chats/1/messages": {
        response: () =>
          jsonResponse([
            msg(1, 1, "user", "original question"),
            msg(2, 1, "assistant", "reply one"),
            msg(3, 1, "assistant", "reply two"),
            msg(4, 1, "assistant", "reply three"),
          ]),
      },
      "/chats": { body: [] },
    });
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await screen.findByText("original question");

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.type(box, "a rewritten question");
    // Three rows behind it, so the destructive confirmation opens.
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Save and delete" });

    // The save becomes undispatchable while the dialog is up.
    act(() => useUiStore.setState({ selectedModelId: null }));
    await user.click(screen.getByRole("button", { name: "Save and delete" }));

    const key = editDraftKey(1, 1);
    // Nothing was sent, so nothing may have been given up: the box is still
    // there and still holds the user's words.
    expect(useDraftStore.getState().edits[key]?.phase).toBe("editing");
    expect(useDraftStore.getState().edits[key]?.text).toBe(
      "a rewritten question",
    );
    expect(
      screen.getByRole("textbox", { name: "Edit message text" }),
    ).toHaveValue("a rewritten question");
  });

  it("a 404 chat delete clears the drafts, because the chat really is gone", async () => {
    const { useDeleteChat } = await import("@/lib/query/chats");
    const { renderHookWithQueryClient } = await import(
      "@/test/helpers/renderWithQueryClient"
    );

    mockFetchWithStreams({
      "/chats/1": {
        response: () => jsonResponse({ detail: "chat_not_found" }, 404),
      },
      "/chats": { body: [] },
    });

    act(() => {
      const d = useDraftStore.getState();
      d.setComposerDraft(1, "for a chat that no longer exists");
      d.openEditDraft(1, 10, "and an edit on it");
      d.setComposerDraft(2, "innocent bystander");
    });

    const { result } = renderHookWithQueryClient(() => useDeleteChat());
    await act(async () => {
      await result.current.mutateAsync(1).catch(() => {});
    });

    // "Already deleted" IS the deletion the user asked for. The sibling
    // message-delete has always said so; this one used to disagree.
    const after = useDraftStore.getState();
    expect(after.composer[1]).toBeUndefined();
    expect(after.edits[editDraftKey(1, 10)]).toBeUndefined();
    expect(after.composer[2]?.text).toBe("innocent bystander");
  });

  it("deleting a character clears the drafts of every chat it took", async () => {
    // The widest destruction path in the app: the server cascade takes every
    // chat the character had. Their drafts are un-evictable by design, so
    // leaving them behind means they hold budget until the process ends.
    const { useDeleteCharacter } = await import("@/lib/query/characters");
    const { renderHookWithQueryClient, createTestQueryClient } = await import(
      "@/test/helpers/renderWithQueryClient"
    );
    const { keys } = await import("@/lib/query/keys");

    mockFetchWithStreams({
      "/characters/7": { response: () => jsonResponse({ ok: true }) },
      "/characters": { body: [] },
      "/chats": { body: [] },
    });

    const client = createTestQueryClient();
    // The doomed list is read from the cached chat list.
    client.setQueryData(keys.chats(), [
      { ...chatFixture, id: 11, character_id: 7 },
      { ...chatFixture, id: 12, character_id: 7 },
      { ...chatFixture, id: 13, character_id: 8 },
    ]);

    act(() => {
      const d = useDraftStore.getState();
      d.setComposerDraft(11, "draft in the first doomed chat");
      d.openEditDraft(12, 40, "edit in the second doomed chat");
      d.setComposerDraft(13, "another character's chat");
    });

    const { result } = renderHookWithQueryClient(() => useDeleteCharacter(), {
      client,
    });
    await act(async () => {
      await result.current.mutateAsync(7).catch(() => {});
    });

    const after = useDraftStore.getState();
    expect(after.composer[11]).toBeUndefined();
    expect(after.edits[editDraftKey(12, 40)]).toBeUndefined();
    expect(
      after.composer[13]?.text,
      "deleting one character took another one's drafts",
    ).toBe("another character's chat");
  });

  it("a FAILED chat delete leaves the drafts exactly where they were", async () => {
    const { useDeleteChat } = await import("@/lib/query/chats");
    const { renderHookWithQueryClient } = await import(
      "@/test/helpers/renderWithQueryClient"
    );

    mockFetchWithStreams({
      "/chats/1": {
        response: () => jsonResponse({ detail: "server_error" }, 500),
      },
      "/chats": { body: [] },
    });

    act(() => {
      const d = useDraftStore.getState();
      d.setComposerDraft(1, "still mine");
      d.openEditDraft(1, 10, "still open");
    });

    const { result } = renderHookWithQueryClient(() => useDeleteChat());
    await act(async () => {
      await result.current.mutateAsync(1).catch(() => {});
    });

    // The chat is still there, so the text is too. Clearing on the failure
    // path would destroy a draft over a delete that never happened.
    const after = useDraftStore.getState();
    expect(after.composer[1]?.text, "a failed delete ate the draft").toBe(
      "still mine",
    );
    expect(after.edits[editDraftKey(1, 10)]?.text).toBe("still open");
  });
});
