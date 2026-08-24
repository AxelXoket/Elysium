/**
 * draftStore.ts - unsent text, held for exactly as long as the process lives.
 *
 * WHY THIS EXISTS. Composer drafts lived in ChatCanvas state and edit drafts
 * lived in MessageBubble state, which meant they lived exactly as long as
 * those components did. VaultGate swaps `children` for the lock screen and
 * its keyed wrapper remounts the subtree, so locking the vault destroyed
 * every unsent sentence in the app. A chat switch preserved drafts and a lock
 * did not, which is why it read as "locking eats my text" rather than as the
 * ordinary consequence of unmounting that it was. Module scope is the fix:
 * this store is created once when the module is first imported and is not
 * owned by any component, so no remount can take it down.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. Nothing here is written to the device.
 * Not to device storage, not to session storage, not to a browser database,
 * not through zustand's disk middleware, not to the backend. Drafts are the
 * most sensitive text in the app: half-written thoughts the user has not
 * decided to keep, addressed to a character they may not want named on an
 * unencrypted disk. The vault exists so conversation content is unreadable
 * without the passphrase, and a draft that outlived the process would sit
 * outside it in plaintext. So the lifetime is exactly the renderer process:
 * quitting the app, or reloading the renderer, takes every draft with it, and
 * that is the intended contract rather than a limitation.
 *
 * This file lives in lib/store, which is the ONE directory static-safety
 * S-09 exempts from its device-storage scan. That exemption is why S-28
 * exists and names this file specifically: the folder that would normally
 * catch a mistake of this kind is the folder that cannot see it here.
 *
 * S-28 scans this file's RAW text, with no comment stripping, which is why
 * the paragraph above spells none of the forbidden API names. A stripper is a
 * regex and not a lexer, so an unbalanced comment opener inside a string can
 * blind it - and this is the one file where no other rule is behind it. The
 * prose paying a small price in directness is what lets the gate be exact.
 *
 * MEMORY. Two ceilings, both counted in UTF-8 bytes rather than UTF-16 code
 * units, because bytes are what a paste actually costs and `String.length`
 * undercounts every non-Latin script the app supports. A write that would
 * breach either ceiling is REFUSED: the existing draft is left exactly as it
 * was and the user is told. There is no eviction of any kind - no LRU, no
 * FIFO, nothing quietly dropped to make room. Silently discarding one draft
 * to accept another is the same bug this file was written to fix, just with a
 * different trigger.
 */
import { create } from "zustand";
import { useErrorStore, getErrorMessage } from "@/lib/errors";

/**
 * Per-buffer ceiling. Generous next to any real message - a 2 MiB composer
 * draft is roughly a novella - and small enough that a runaway paste loop
 * cannot eat the budget on its own.
 */
export const MAX_DRAFT_BYTES = 2 * 1024 * 1024;

/**
 * Whole-store ceiling across every composer and edit buffer. Bounds the worst
 * case a user could reach by opening many chats and pasting into each.
 */
export const MAX_TOTAL_DRAFT_BYTES = 64 * 1024 * 1024;

/**
 * UTF-8 byte length, counted rather than produced.
 *
 * The obvious spelling is `new TextEncoder().encode(text).length`, and it was
 * the first one here. It allocates a Uint8Array of the WHOLE draft, transcodes
 * into it, reads one number and throws the array away - on every keystroke,
 * inside a keydown handler, on the main thread. At the 2 MiB this store
 * advertises as supported that is megabytes of garbage per second, so the size
 * the file says is fine was the size at which its own write path degraded.
 *
 * This counts the same bytes without producing any. The surrogate arithmetic
 * is the part worth stating: a well-formed pair is one code point and four
 * UTF-8 bytes, while a LONE surrogate is not encodable at all and every UTF-8
 * encoder replaces it with U+FFFD, which is three. `draftStore.test.ts` pins
 * this against TextEncoder over a corpus that includes both.
 */
export function draftByteSize(text: string): number {
  let bytes = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 0x80) {
      bytes += 1;
    } else if (code < 0x800) {
      bytes += 2;
    } else if (code >= 0xd800 && code <= 0xdbff) {
      const next = i + 1 < text.length ? text.charCodeAt(i + 1) : 0;
      if (next >= 0xdc00 && next <= 0xdfff) {
        // A real pair: one code point above the BMP, four bytes, two units.
        bytes += 4;
        i += 1;
      } else {
        // Unpaired high surrogate -> U+FFFD.
        bytes += 3;
      }
    } else {
      // Includes unpaired LOW surrogates, which also become U+FFFD (3).
      bytes += 3;
    }
  }
  return bytes;
}

/**
 * An edit buffer is either being typed in or has been handed to the server.
 *
 * The distinction is what lets a failed save give the box back. `editing`
 * means the box is on screen; `committing` means the user pressed Save and
 * the box has closed, but the text is kept because the save can still fail,
 * be aborted, or be refused by a stream that already owns the chat. Success
 * deletes the entry outright; anything else puts it back to `editing`, which
 * reopens the box with the text the user actually typed.
 */
export type EditPhase = "editing" | "committing";

interface DraftEntry {
  text: string;
  /** Cached UTF-8 size of `text`, so the running total never rescans. */
  bytes: number;
}

interface EditEntry extends DraftEntry {
  phase: EditPhase;
}

/** Why a write was refused. `null` means it was accepted. */
type Refusal = "too_large" | "budget";

/**
 * The catalogue codes the two refusals raise.
 *
 * Exported, and not inlined at the call site, because static-safety S-24
 * deliberately skips a `pushErrorDirect` whose code is a VARIABLE - it checks
 * those "where they are produced". These are produced right here, out of two
 * string literals, so nothing was checking them: `draft_to_large` would have
 * passed every gate in the repo and shipped a toast reading "Something went
 * wrong. Please try again." Naming them gives errorCatalogue.test.ts an
 * import to assert against.
 */
export const DRAFT_TOO_LARGE_CODE = "draft_too_large";
export const DRAFT_BUDGET_CODE = "draft_budget_exhausted";

export interface DraftState {
  /** chatId -> the composer buffer for that chat. */
  composer: Record<number, DraftEntry>;
  /** `${chatId}:${messageId}` -> the edit buffer for that message. */
  edits: Record<string, EditEntry>;
  /**
   * Running total of every entry's `bytes`, maintained by delta. Recomputing
   * it by walking both maps on each keystroke would make typing cost grow
   * with the number of open drafts.
   */
  totalBytes: number;

  setComposerDraft: (chatId: number, text: string) => boolean;
  clearComposerDraft: (chatId: number) => void;

  openEditDraft: (chatId: number, messageId: number, text: string) => boolean;
  setEditDraft: (chatId: number, messageId: number, text: string) => boolean;
  /** Save pressed: close the box, keep the text until the outcome is known. */
  commitEditDraft: (chatId: number, messageId: number) => void;
  /** The save did not land: put the box back with the text still in it. */
  reopenEditDraft: (chatId: number, messageId: number) => void;
  clearEditDraft: (chatId: number, messageId: number) => void;

  /** A chat was really deleted: drop its composer buffer and every edit. */
  forgetChat: (chatId: number) => void;
  /** Those messages were really deleted: drop only their edit buffers. */
  forgetMessages: (chatId: number, messageIds: readonly number[]) => void;
  /** Every message in the chat was deleted, but the chat itself survives. */
  forgetChatMessages: (chatId: number) => void;

  /**
   * Drop everything.
   *
   * One app caller, and it is the one case where a draft must NOT survive:
   * a vault RESET (VaultGate's useResetVault). Chat ids restart at 1 in the
   * new vault, so without this the first chat would open holding text from
   * the vault the user just destroyed. Also used by test scaffolding.
   */
  clearAll: () => void;
}

/** The canonical key for an edit buffer. Never build this string by hand. */
export function editDraftKey(chatId: number, messageId: number): string {
  return `${chatId}:${messageId}`;
}

/**
 * Decide whether a write fits, given what the entry used to cost.
 *
 * Both ceilings are checked against the SAME delta the write would apply, so
 * replacing a large draft with a smaller one is always accepted even when the
 * store is already near the budget.
 */
function refuse(
  nextBytes: number,
  previousBytes: number,
  totalBytes: number,
): Refusal | null {
  if (nextBytes > MAX_DRAFT_BYTES) return "too_large";
  if (totalBytes - previousBytes + nextBytes > MAX_TOTAL_DRAFT_BYTES) {
    return "budget";
  }
  return null;
}

/**
 * Tell the user, once per chat, why their text was not taken.
 *
 * A refusal the user cannot see is indistinguishable from the app losing the
 * text, which is the failure this whole file exists to prevent. The sentence
 * comes from the shared catalogue rather than being typed here, so it is
 * reviewed with every other sentence the app can show.
 *
 * The chat id is not decoration: the error store's dedup identity includes it
 * (K-21), so a refusal raised without one collapses every chat's refusal into
 * a single event and the SECOND conversation to fail is told nothing at all.
 * That exact bug has been fixed in this codebase once already; passing the id
 * is what keeps it fixed.
 */
function announce(refusal: Refusal, chatId: number): void {
  const code =
    refusal === "too_large" ? DRAFT_TOO_LARGE_CODE : DRAFT_BUDGET_CODE;
  useErrorStore
    .getState()
    .pushErrorDirect(code, getErrorMessage(code), "warning", { chatId });
}

export const useDraftStore = create<DraftState>()((set, get) => ({
  composer: {},
  edits: {},
  totalBytes: 0,

  setComposerDraft: (chatId, text) => {
    const state = get();
    const previous = state.composer[chatId];
    const previousBytes = previous?.bytes ?? 0;
    if (previous?.text === text) return true;

    // An emptied composer drops its entry entirely. The map is meant to hold
    // genuinely unsent text, and a chat the user cleared out is not that.
    if (text === "") {
      if (previous == null) return true;
      const composer = { ...state.composer };
      delete composer[chatId];
      set({ composer, totalBytes: state.totalBytes - previousBytes });
      return true;
    }

    const bytes = draftByteSize(text);
    const refusal = refuse(bytes, previousBytes, state.totalBytes);
    if (refusal != null) {
      announce(refusal, chatId);
      return false;
    }

    set({
      composer: { ...state.composer, [chatId]: { text, bytes } },
      totalBytes: state.totalBytes - previousBytes + bytes,
    });
    return true;
  },

  clearComposerDraft: (chatId) => {
    const state = get();
    const previous = state.composer[chatId];
    if (previous == null) return;
    const composer = { ...state.composer };
    delete composer[chatId];
    set({ composer, totalBytes: state.totalBytes - previous.bytes });
  },

  openEditDraft: (chatId, messageId, text) => {
    // Opening a box that ALREADY has a buffer shows what is in it rather than
    // the message's stored content. The buffer only survives an open save, so
    // reaching here with one means a previous attempt did not land, and the
    // text in it is the user's - overwriting it with the original message
    // would destroy the one copy of their words that was left.
    const existing = get().edits[editDraftKey(chatId, messageId)];
    if (existing != null) {
      setEditPhase(set, get, chatId, messageId, "editing");
      return true;
    }
    return writeEdit(set, get, chatId, messageId, text);
  },

  setEditDraft: (chatId, messageId, text) =>
    writeEdit(set, get, chatId, messageId, text),

  commitEditDraft: (chatId, messageId) =>
    setEditPhase(set, get, chatId, messageId, "committing"),

  reopenEditDraft: (chatId, messageId) =>
    setEditPhase(set, get, chatId, messageId, "editing"),

  clearEditDraft: (chatId, messageId) => {
    const state = get();
    const key = editDraftKey(chatId, messageId);
    const previous = state.edits[key];
    if (previous == null) return;
    const edits = { ...state.edits };
    delete edits[key];
    set({ edits, totalBytes: state.totalBytes - previous.bytes });
  },

  forgetChat: (chatId) => {
    const state = get();
    const composerEntry = state.composer[chatId];
    const dropped = dropEditsOfChat(state.edits, chatId);
    // Nothing to do is not the same as "free to write anyway": a `set` here
    // notifies every subscriber, and the sibling clear actions all guard.
    if (composerEntry == null && dropped == null) return;

    const composer = composerEntry != null ? { ...state.composer } : state.composer;
    if (composerEntry != null) delete composer[chatId];
    set({
      composer,
      edits: dropped?.edits ?? state.edits,
      totalBytes:
        state.totalBytes -
        (composerEntry?.bytes ?? 0) -
        (dropped?.freedBytes ?? 0),
    });
  },

  forgetChatMessages: (chatId) => {
    const state = get();
    const dropped = dropEditsOfChat(state.edits, chatId);
    if (dropped == null) return;
    set({
      edits: dropped.edits,
      totalBytes: state.totalBytes - dropped.freedBytes,
    });
  },

  forgetMessages: (chatId, messageIds) => {
    if (messageIds.length === 0) return;
    const state = get();
    // Direct lookups, not a scan: the caller already knows exactly which
    // messages died, so building their keys is O(ids) rather than O(entries),
    // and nothing has to parse a key back into numbers to decide.
    let edits: Record<string, EditEntry> | null = null;
    let freedBytes = 0;
    for (const messageId of messageIds) {
      const key = editDraftKey(chatId, messageId);
      const entry = state.edits[key];
      if (entry == null) continue;
      if (edits == null) edits = { ...state.edits };
      delete edits[key];
      freedBytes += entry.bytes;
    }
    if (edits == null) return;
    set({ edits, totalBytes: state.totalBytes - freedBytes });
  },

  clearAll: () => set({ composer: {}, edits: {}, totalBytes: 0 }),
}));

type SetState = (partial: Partial<DraftState>) => void;
type GetState = () => DraftState;

/** Shared body for opening and updating an edit buffer. */
function writeEdit(
  set: SetState,
  get: GetState,
  chatId: number,
  messageId: number,
  text: string,
): boolean {
  const state = get();
  const key = editDraftKey(chatId, messageId);
  const previous = state.edits[key];
  const previousBytes = previous?.bytes ?? 0;
  if (previous?.text === text && previous.phase === "editing") return true;

  // Unlike the composer, an EMPTY edit buffer keeps its entry: the entry's
  // existence is what holds the box open, so deleting it on an emptied
  // textarea would close the box under the user mid-sentence.
  const bytes = draftByteSize(text);
  const refusal = refuse(bytes, previousBytes, state.totalBytes);
  if (refusal != null) {
    announce(refusal, chatId);
    return false;
  }

  set({
    edits: { ...state.edits, [key]: { text, bytes, phase: "editing" } },
    totalBytes: state.totalBytes - previousBytes + bytes,
  });
  return true;
}

/** Move an existing buffer between phases without touching its text. */
function setEditPhase(
  set: SetState,
  get: GetState,
  chatId: number,
  messageId: number,
  phase: EditPhase,
): void {
  const state = get();
  const key = editDraftKey(chatId, messageId);
  const previous = state.edits[key];
  if (previous == null || previous.phase === phase) return;
  set({ edits: { ...state.edits, [key]: { ...previous, phase } } });
}

/**
 * Remove every edit buffer belonging to one chat.
 *
 * Returns null when nothing matched, and that distinction is the whole point.
 * This used to report only the BYTES it freed and treat zero as "nothing
 * matched" - but an emptied edit buffer is a deliberate, reachable state
 * worth exactly zero bytes, so clearing a box and then deleting its message
 * left the entry behind forever, in a function named `forget`.
 *
 * Keys are matched by prefix rather than parsed back into numbers. Parsing
 * invited two failures that simply cannot arise here: a NaN id compares false
 * against everything and could never be forgotten, and a key without the
 * separator produced two silently wrong ids.
 */
function dropEditsOfChat(
  edits: Record<string, EditEntry>,
  chatId: number,
): { edits: Record<string, EditEntry>; freedBytes: number } | null {
  const prefix = `${chatId}:`;
  let kept: Record<string, EditEntry> | null = null;
  let freedBytes = 0;
  for (const [key, entry] of Object.entries(edits)) {
    if (!key.startsWith(prefix)) continue;
    if (kept == null) kept = { ...edits };
    delete kept[key];
    freedBytes += entry.bytes;
  }
  return kept == null ? null : { edits: kept, freedBytes };
}
