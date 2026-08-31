import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  CHAT_BG_FOCUS_DEFAULT,
  CHAT_BG_ZOOM_MIN,
  clampFraming,
  type ChatBgFraming,
} from "@/lib/appearance/chatBackground";

// ── Tab type (Phase 6E-A: renamed from "model|info|settings") ──────────────
// Old persisted values ("model", "info", "settings") are migrated in the
// persist config below. Any stale localStorage is normalized on first load.
type RightPanelTab = "models" | "secrets" | "persona" | "notebook";

// ── Appearance defaults ────────────────────────────────────────────────────
// Chosen to exactly match the pre-settings look (bubble text was text-sm +
// leading-relaxed), so a user who never opens the panel sees zero change.
export const MSG_FONT_DEFAULT = 14;
export const MSG_FONT_MIN = 13;
export const MSG_FONT_MAX = 19;
export const MSG_LINE_DEFAULT = 1.625;
export const MSG_LINE_MIN = 1.3;
export const MSG_LINE_MAX = 1.95;

/** Bubble solidity. 1 is today's look and the default, so nobody's chat
 * changes on update. The floor is not 0: a bubble you cannot find is not a
 * setting anyone wants, and the sliders in this app stop at the last value
 * that still works rather than at the last one that parses. */
export const MSG_OPACITY_DEFAULT = 1;
export const MSG_OPACITY_MIN = 0.35;
export const MSG_OPACITY_MAX = 1;

/** The framing half of the chat-background state, as stored. Kept next to the
 * defaults it belongs with so "a new picture starts unframed" and "a fresh
 * profile starts unframed" cannot drift apart. */
const CHAT_BG_FRAMING_DEFAULT_STATE = {
  chatBgFocusX: CHAT_BG_FOCUS_DEFAULT,
  chatBgFocusY: CHAT_BG_FOCUS_DEFAULT,
  chatBgZoom: CHAT_BG_ZOOM_MIN,
};

// v1.1 E2: message contrast preset. Default is the zero-change baseline.
/** How a message bubble's surface catches light. Matte is today's look. */
export type SurfaceFinish = "matte" | "glossy" | "metallic";

export type NarrationVoice = "same" | "narrator" | "skip";

export type MsgContrast = "soft" | "default" | "high";

// v1.1 FF7: generation sampling scalars persist so a vault re-lock (which
// remounts the GenerationSettingsProvider) no longer wipes them. Names are
// NEUTRAL on purpose - max_tokens/context_budget_tokens contain the forbidden
// "token" substring the persisted-key safety scan rejects, so the persisted
// fields use genMaxOutput / genContextBudget instead. Stop sequences are USER
// CONTENT (character names) and NEVER persist - they stay in-memory in the
// GenerationSettingsProvider (accepted loss on lock).
export interface GenPersistedSettings {
  genTemperature: number;
  genTopP: number;
  genTopK: number;
  genRepetitionPenalty: number;
  genMaxOutput: number;
  genSeed: string;
  genContextBudget: number;
}

export const GEN_PERSISTED_DEFAULTS: GenPersistedSettings = {
  genTemperature: 0.8,
  genTopP: 0.9,
  genTopK: 40,
  genRepetitionPenalty: 1.05,
  genMaxOutput: 1024,
  genSeed: "",
  genContextBudget: 16384,
};

/** Defensive clamp for values coming back from localStorage (a corrupted
 * store must never crash the app; request-time builders clamp again). */
function clampGenSettings(
  values: Partial<GenPersistedSettings>,
): Partial<GenPersistedSettings> {
  const num = (v: unknown, min: number, max: number, fallback: number) =>
    typeof v === "number" && Number.isFinite(v)
      ? Math.min(Math.max(v, min), max)
      : fallback;
  const out: Partial<GenPersistedSettings> = {};
  if ("genTemperature" in values)
    out.genTemperature = num(values.genTemperature, 0, 2, 0.8);
  if ("genTopP" in values) out.genTopP = num(values.genTopP, 0, 1, 0.9);
  if ("genTopK" in values) out.genTopK = num(values.genTopK, 0, 500, 40);
  if ("genRepetitionPenalty" in values)
    out.genRepetitionPenalty = num(values.genRepetitionPenalty, 0, 2, 1.05);
  if ("genMaxOutput" in values)
    out.genMaxOutput = num(values.genMaxOutput, 1, 2_000_000, 1024);
  if ("genContextBudget" in values)
    out.genContextBudget = num(values.genContextBudget, 512, 2_000_000, 16384);
  if ("genSeed" in values)
    out.genSeed = typeof values.genSeed === "string" ? values.genSeed : "";
  return out;
}

interface UiState {
  selectedCharacterId: number | null;
  selectedChatId: number | null;
  /**
   * The currently-chosen model id. SESSION-ONLY in this store since v1.2 -
   * NOT written to localStorage (see `partialize` below). An OpenRouter
   * model id ("anthropic/claude-3.5-sonnet") is a NAME a person reads on
   * screen, which the owner's own rule bans from ever sitting outside the
   * vault; the other two selections above are bare numbers, which the rule
   * permits to stay device-local.
   *
   * It now lives in the encrypted settings table instead (POST
   * /settings/model-selection, GET /settings' selected_model_id). This
   * field starts null on every launch and is hydrated from the vault, once,
   * by useStaleSelectionReconciliation - which also pushes every later
   * change back to the vault. That hook only runs after unlock, so there is
   * NOTHING to read here while the vault is locked.
   */
  selectedModelId: string | null;
  activeRightPanelTab: RightPanelTab;
  sidebarCollapsed: boolean;
  /** Focus mode's other half - the right panel's own collapse flag. Kept
   *  separate from sidebarCollapsed so either side can close without the
   *  other, per the panels' own request. */
  rightPanelCollapsed: boolean;

  // Appearance preferences (Settings panel). Message BODIES only - labels,
  // timestamps, and controls never scale with these.
  msgFontPx: number;
  msgLineHeight: number;
  /** How strongly message text stands out from its bubble (v1.1 E2). */
  msgContrast: MsgContrast;
  /** Style *asterisk* narration spans in message text. */
  narrationEnabled: boolean;
  /** Tint "quoted speech" spans in message text. */
  quoteTintEnabled: boolean;

  /**
   * Speak every assistant reply as it arrives (V9-1).
   *
   * Default OFF, and it stays that way until the person asks: voice costs GPU
   * time and makes noise, so it is the one setting that must never surprise
   * anyone by being on. Persisted like the other preferences - somebody who
   * turned it on meant to leave it on.
   *
   * Toggling it mid-stream does NOT retro-fit the reply already arriving; the
   * next one speaks. That is not a limitation but the rule the app was asked
   * for, and it falls out of the flag being read when a request is BUILT.
   */
  continuousVoice: boolean;
  /** The "voice is set up but nothing is chosen" hint was closed. Persisted:
   *  a hint that comes back every launch is a nag. */
  voiceHintDismissed: boolean;
  /** The Settings dialog, hoisted out of SidebarFooter's local state so any
   *  surface can open it - and open it ON a page. Deliberately NOT persisted:
   *  a dialog that reopens itself on launch is a bug, not a preference. */
  settingsOpen: boolean;
  settingsInitialPage: string | null;

  /**
   * Has the device-local narration mode been moved into the vault?
   *
   * A migration flag, and the only reason it is in the store: the narration
   * mode now lives in the vault alone, and the stale localStorage copy has to
   * be cleared or a later change through Settings is silently reverted by it
   * on the next launch. Writing this flag is what clears it - zustand rewrites
   * the persisted blob from `partialize`, and `narrationVoice` is no longer in
   * there, so it goes in the same write. Deletable with
   * lib/voice/narrationMigration.ts once no install can still carry one.
   */
  narrationMigrated: boolean;

  /**
   * Custom message ink, or null to follow the contrast preset (V11).
   *
   * An override ON TOP of the preset rather than a replacement for it: the
   * presets carry measured contrast ratios and a deliberate soft<default<high
   * ordering, and throwing that away for a colour field would undo three
   * versions of care. The picker shows the ratio it is producing instead.
   */
  msgInk: string | null;

  /** Bubble surface finish. Bubbles only - never controls (V11). */
  surfaceFinish: SurfaceFinish;

  /** How solid a message bubble is, 0.35..1. Applies to the BUBBLE only -
   * the text on it stays fully opaque, because a translucent bubble is a
   * design choice and unreadable text is not. */
  msgOpacity: number;

  // Chat background (image blob lives in the appearance blob store, NOT
  // here - persisting only flat scalars keeps localStorage writes tiny).
  chatBgOn: boolean;
  /** 0..1 average luminance of the stored image, written at image-set time. */
  chatBgLum: number;
  /** Scrim opacity AND blend weight, 0..0.85. */
  chatBgContrast: number;
  /** 'auto' or a '#rrggbb' tint. */
  chatBgTint: string;
  /** Which part of the picture to show, 0..100 each. Percentages rather than
   * a pixel rectangle on purpose: the chat area is a different size in every
   * window, and a rectangle measured against one of them is wrong in all the
   * others. See lib/appearance/chatBackground.ts. */
  chatBgFocusX: number;
  chatBgFocusY: number;
  /** 1 = the whole picture (cover), higher crops in. */
  chatBgZoom: number;
  /** width / height of the stored image, recorded when it is set. Needed to
   * know which axis a zoom hangs off; null until an image has been chosen
   * under a build that records it. */
  chatBgAspect: number | null;
  /** Session-only refresh signal: bumped when the image blob is replaced so
   * the object-URL hook reloads. Deliberately NOT persisted. */
  chatBgRev: number;
  /** width / height of the live chat area, published by ChatCanvas so the
   * framing preview in Settings can be drawn at the shape the picture will
   * actually be seen in. Session-only: it is a measurement of this window,
   * not a preference, and restoring a stale one would frame the preview for
   * a window size that no longer exists. */
  chatAreaAspect: number | null;

  /** Animated mist backdrop behind the app frame (WebGL; falls back to the
   * static gradient wherever it cannot or should not run). */
  ambientFogOn: boolean;

  // v1.1 FF7: persisted generation sampling scalars (neutral names).
  genTemperature: number;
  genTopP: number;
  genTopK: number;
  genRepetitionPenalty: number;
  genMaxOutput: number;
  genSeed: string;
  genContextBudget: number;

  selectCharacter: (id: number | null) => void;
  selectChat: (id: number | null) => void;
  selectModel: (id: string | null) => void;
  setActiveRightPanelTab: (tab: RightPanelTab) => void;
  toggleSidebar: () => void;
  toggleRightPanel: () => void;
  setMsgFontPx: (px: number) => void;
  setMsgLineHeight: (lh: number) => void;
  setMsgContrast: (level: MsgContrast) => void;
  setNarrationEnabled: (on: boolean) => void;
  setQuoteTintEnabled: (on: boolean) => void;
  setContinuousVoice: (on: boolean) => void;
  dismissVoiceHint: () => void;
  openSettings: (page?: string) => void;
  setSettingsOpen: (open: boolean) => void;
  markNarrationMigrated: () => void;
  setMsgInk: (hex: string | null) => void;
  setSurfaceFinish: (finish: SurfaceFinish) => void;
  /** Image stored → mark on + record its luminance (contrast/tint kept). */
  setChatBgMeta: (meta: { lum: number; aspect?: number }) => void;
  /** Image removed → mark off (contrast/tint kept for the next image). */
  clearChatBg: () => void;
  setChatBgContrast: (contrast: number) => void;
  setChatBgTint: (tint: string) => void;
  /** Move and/or crop the picture. Partial so the preview can drag the focus
   * without restating the zoom. */
  setChatBgFraming: (framing: Partial<ChatBgFraming>) => void;
  setChatAreaAspect: (aspect: number | null) => void;
  /** Back to the whole picture, centred. */
  resetChatBgFraming: () => void;
  setMsgOpacity: (opacity: number) => void;
  setAmbientFogOn: (on: boolean) => void;
  /** Bulk write-through for persisted generation scalars (FF7). */
  setGenSettings: (values: Partial<GenPersistedSettings>) => void;
}

// Normalize old persisted tab values to new names.
// Called by the Zustand persist migrate function on version upgrade.
function normalizeTab(raw: unknown): RightPanelTab {
  const map: Record<string, RightPanelTab> = {
    model:    "models",   // old "Model" tab → "Models"
    models:   "models",   // already new
    info:     "models",   // old "Info" tab removed → default to "Models"
    settings: "secrets",  // old "Settings" tab → "Secrets"
    secrets:  "secrets",  // already new
    persona:  "persona",  // already new
  };
  return map[raw as string] ?? "models";
}

/**
 * EXACTLY what this store writes to device storage.
 *
 * One list, exported, and the single source of truth. The same thirty-two
 * names used to be written out by hand in three places - here, in
 * settings-persistence.test.ts, and in static-safety.test.ts - and a list
 * that exists three times is a list that goes stale in two of them with
 * nothing to say so.
 *
 * localStorage is not encrypted, so every ADDITION here is a privacy
 * decision. `satisfies` proves each entry is a real field of the state; it
 * does NOT prove the set is the RIGHT set, and it cannot - that is what the
 * behavioural test reading the written blob is for.
 */
export const PERSISTED = [
  "selectedCharacterId",
  "selectedChatId",
  "activeRightPanelTab",
  "sidebarCollapsed",
  "rightPanelCollapsed",
  "msgFontPx",
  "msgLineHeight",
  "msgContrast",
  "narrationEnabled",
  "quoteTintEnabled",
  "continuousVoice",
  "voiceHintDismissed",
  "narrationMigrated",
  "msgInk",
  "surfaceFinish",
  "msgOpacity",
  "chatBgOn",
  "chatBgLum",
  "chatBgContrast",
  "chatBgTint",
  "chatBgFocusX",
  "chatBgFocusY",
  "chatBgZoom",
  "chatBgAspect",
  "ambientFogOn",
  "genTemperature",
  "genTopP",
  "genTopK",
  "genRepetitionPenalty",
  "genMaxOutput",
  "genSeed",
  "genContextBudget",
] as const satisfies readonly (keyof UiState)[];

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      selectedCharacterId: null,
      selectedChatId: null,
      selectedModelId: null,
      activeRightPanelTab: "models",  // default changed from "settings"
      sidebarCollapsed: false,
      rightPanelCollapsed: false,
      msgFontPx: MSG_FONT_DEFAULT,
      msgLineHeight: MSG_LINE_DEFAULT,
      msgContrast: "default",
      narrationEnabled: true,
      quoteTintEnabled: true,
      continuousVoice: false,
      voiceHintDismissed: false,
      narrationMigrated: false,
      settingsOpen: false,
      settingsInitialPage: null,
      msgInk: null,
      surfaceFinish: "matte",
      msgOpacity: MSG_OPACITY_DEFAULT,
      ...CHAT_BG_FRAMING_DEFAULT_STATE,
      chatBgAspect: null,
      chatBgOn: false,
      chatBgLum: 0.5,
      chatBgContrast: 0.35,
      chatBgTint: "auto",
      chatBgRev: 0,
      chatAreaAspect: null,
      ambientFogOn: true,
      ...GEN_PERSISTED_DEFAULTS,

      selectCharacter: (id) =>
        set({ selectedCharacterId: id, selectedChatId: null }),
      selectChat: (id) => set({ selectedChatId: id }),
      selectModel: (id) => set({ selectedModelId: id }),
      setActiveRightPanelTab: (tab) => set({ activeRightPanelTab: tab }),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      toggleRightPanel: () =>
        set((s) => ({ rightPanelCollapsed: !s.rightPanelCollapsed })),
      setMsgFontPx: (px) =>
        set({
          msgFontPx: Math.min(MSG_FONT_MAX, Math.max(MSG_FONT_MIN, px)),
        }),
      setMsgLineHeight: (lh) =>
        set({
          msgLineHeight: Math.min(MSG_LINE_MAX, Math.max(MSG_LINE_MIN, lh)),
        }),
      setMsgContrast: (level) =>
        set({
          // allowlist guard (setChatBgTint pattern): unknown -> default.
          msgContrast:
            level === "soft" || level === "high" ? level : "default",
        }),
      setNarrationEnabled: (on) => set({ narrationEnabled: on }),
      setQuoteTintEnabled: (on) => set({ quoteTintEnabled: on }),
      setContinuousVoice: (on) => set({ continuousVoice: on }),
      dismissVoiceHint: () => set({ voiceHintDismissed: true }),
      openSettings: (page) =>
        set({ settingsOpen: true, settingsInitialPage: page ?? null }),
      setSettingsOpen: (open) =>
        set(open ? { settingsOpen: true } : { settingsOpen: false, settingsInitialPage: null }),
      markNarrationMigrated: () => set({ narrationMigrated: true }),
      setMsgInk: (hex) => set({ msgInk: hex }),
      setSurfaceFinish: (finish) => set({ surfaceFinish: finish }),
      setChatBgMeta: ({ lum, aspect }) =>
        set((s) => ({
          chatBgOn: true,
          chatBgLum: Number.isFinite(lum) ? Math.min(1, Math.max(0, lum)) : 0.5,
          chatBgAspect:
            typeof aspect === "number" && Number.isFinite(aspect) && aspect > 0
              ? aspect
              : null,
          // A NEW PICTURE STARTS UNFRAMED. Carrying the old framing over would
          // apply a crop chosen for a different photo - a portrait's framing
          // on a landscape lands somewhere nobody picked, and the user would
          // have to undo a choice they never made.
          ...CHAT_BG_FRAMING_DEFAULT_STATE,
          chatBgRev: s.chatBgRev + 1,
        })),
      clearChatBg: () => set({ chatBgOn: false }),
      setChatBgFraming: (framing) =>
        set((s) => {
          const next = clampFraming({
            focusX: framing.focusX ?? s.chatBgFocusX,
            focusY: framing.focusY ?? s.chatBgFocusY,
            zoom: framing.zoom ?? s.chatBgZoom,
          });
          return {
            chatBgFocusX: next.focusX,
            chatBgFocusY: next.focusY,
            chatBgZoom: next.zoom,
          };
        }),
      resetChatBgFraming: () => set({ ...CHAT_BG_FRAMING_DEFAULT_STATE }),
      setChatAreaAspect: (aspect) =>
        set({
          chatAreaAspect:
            typeof aspect === "number" && Number.isFinite(aspect) && aspect > 0
              ? aspect
              : null,
        }),
      setMsgOpacity: (opacity) =>
        set({
          msgOpacity: Number.isFinite(opacity)
            ? Math.min(MSG_OPACITY_MAX, Math.max(MSG_OPACITY_MIN, opacity))
            : MSG_OPACITY_MAX,
        }),
      setChatBgContrast: (contrast) =>
        set({
          chatBgContrast: Number.isFinite(contrast)
            ? Math.min(0.85, Math.max(0, contrast))
            : 0.35,
        }),
      setChatBgTint: (tint) =>
        set({
          // 'auto' or #rrggbb only - state-level port of Wisteria's CSS
          // url-injection guard.
          chatBgTint: /^auto$|^#[0-9a-f]{6}$/i.test(tint) ? tint : "auto",
        }),
      setAmbientFogOn: (on) => set({ ambientFogOn: on }),
      setGenSettings: (values) => set(clampGenSettings(values)),
    }),
    {
      name: "elysium-ui-state",
      // Version 3: v1.2 privacy fix - selectedModelId (a plaintext model NAME)
      // no longer persists to localStorage; see the migrate branch below and
      // the field's own doc comment on UiState.
      //
      // Version 2: tab names renamed (model→models, settings→secrets, info→models).
      // The original persist config had no explicit version, which Zustand treats as 0.
      // Bumping to 2 triggers the migrate function for all existing localStorage entries.
      version: 3,
      migrate: (persisted: unknown, fromVersion: number) => {
        const state = { ...(persisted ?? {}) } as Record<string, unknown>;
        if (fromVersion < 2) {
          // Normalize old tab value to new tab name
          state.activeRightPanelTab = normalizeTab(state.activeRightPanelTab);
        }
        if (fromVersion < 3) {
          // v1.2 privacy fix (audit finding): selectedModelId was a plaintext
          // model NAME sitting in this blob, in the clear, inside WebView2's
          // on-disk localStorage - readable with no passphrase, and outliving
          // every lock, relaunch and shutdown because browser_profile.purge()
          // deliberately spares Local Storage. It now lives in the encrypted
          // settings table (see lib/query/settings.ts's useSetSelectedModel).
          //
          // DELETING it here, rather than merely leaving it out of
          // `partialize` going forward, is what actually cleans an existing
          // install: `partialize` only controls what gets WRITTEN on the next
          // save. Without this line the stale copy would stay parked in this
          // rehydrated state - and therefore in this blob, since nothing here
          // would ever prune a key partialize just drops from its own output
          // - and would flow into the live store on every future launch too,
          // pushed straight back into the vault by the reconciliation hook's
          // hydration guard reading a non-null local value first.
          delete state.selectedModelId;
        }
        return state;
      },
      // Only harmless UI preferences are persisted - never secrets, content, or API data.
      // Persona fields are NOT persisted here (Phase 6E-A - persona persistence deferred to 6E-B).
      // ONE list, from the export above. Thirty-two hand-written
      // `key: state.key` lines are thirty-two chances to put a field on the
      // disk by accident, and the source-text guard that watched them could
      // only check the NAMES - `msgFontPx: state.vaultKey` passed it.
      partialize: (state) =>
        Object.fromEntries(
          PERSISTED.map((key) => [key, state[key]]),
        ) as Pick<UiState, (typeof PERSISTED)[number]>,
    },
  ),
);
