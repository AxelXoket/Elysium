import { create } from "zustand";
import { persist } from "zustand/middleware";

// ── Tab type (Phase 6E-A: renamed from "model|info|settings") ──────────────
// Old persisted values ("model", "info", "settings") are migrated in the
// persist config below. Any stale localStorage is normalized on first load.
type RightPanelTab = "models" | "secrets" | "persona";

// ── Appearance defaults ────────────────────────────────────────────────────
// Chosen to exactly match the pre-settings look (bubble text was text-sm +
// leading-relaxed), so a user who never opens the panel sees zero change.
export const MSG_FONT_DEFAULT = 14;
export const MSG_FONT_MIN = 13;
export const MSG_FONT_MAX = 19;
export const MSG_LINE_DEFAULT = 1.625;
export const MSG_LINE_MIN = 1.3;
export const MSG_LINE_MAX = 1.95;

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
  selectedModelId: string | null;
  activeRightPanelTab: RightPanelTab;
  sidebarCollapsed: boolean;

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

  // Chat background (image blob lives in the appearance blob store, NOT
  // here - persisting only flat scalars keeps localStorage writes tiny).
  chatBgOn: boolean;
  /** 0..1 average luminance of the stored image, written at image-set time. */
  chatBgLum: number;
  /** Scrim opacity AND blend weight, 0..0.85. */
  chatBgContrast: number;
  /** 'auto' or a '#rrggbb' tint. */
  chatBgTint: string;
  /** Session-only refresh signal: bumped when the image blob is replaced so
   * the object-URL hook reloads. Deliberately NOT persisted. */
  chatBgRev: number;

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
  setChatBgMeta: (meta: { lum: number }) => void;
  /** Image removed → mark off (contrast/tint kept for the next image). */
  clearChatBg: () => void;
  setChatBgContrast: (contrast: number) => void;
  setChatBgTint: (tint: string) => void;
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

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      selectedCharacterId: null,
      selectedChatId: null,
      selectedModelId: null,
      activeRightPanelTab: "models",  // default changed from "settings"
      sidebarCollapsed: false,
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
      chatBgOn: false,
      chatBgLum: 0.5,
      chatBgContrast: 0.35,
      chatBgTint: "auto",
      chatBgRev: 0,
      ambientFogOn: true,
      ...GEN_PERSISTED_DEFAULTS,

      selectCharacter: (id) =>
        set({ selectedCharacterId: id, selectedChatId: null }),
      selectChat: (id) => set({ selectedChatId: id }),
      selectModel: (id) => set({ selectedModelId: id }),
      setActiveRightPanelTab: (tab) => set({ activeRightPanelTab: tab }),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
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
      setChatBgMeta: ({ lum }) =>
        set((s) => ({
          chatBgOn: true,
          chatBgLum: Number.isFinite(lum) ? Math.min(1, Math.max(0, lum)) : 0.5,
          chatBgRev: s.chatBgRev + 1,
        })),
      clearChatBg: () => set({ chatBgOn: false }),
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
      // Version 2: tab names renamed (model→models, settings→secrets, info→models).
      // The original persist config had no explicit version, which Zustand treats as 0.
      // Bumping to 2 triggers the migrate function for all existing localStorage entries.
      version: 2,
      migrate: (persisted: unknown, fromVersion: number) => {
        const state = (persisted ?? {}) as Record<string, unknown>;
        if (fromVersion < 2) {
          // Normalize old tab value to new tab name
          return {
            ...state,
            activeRightPanelTab: normalizeTab(state.activeRightPanelTab),
          };
        }
        return state;
      },
      // Only harmless UI preferences are persisted - never secrets, content, or API data.
      // Persona fields are NOT persisted here (Phase 6E-A - persona persistence deferred to 6E-B).
      partialize: (state) => ({
        selectedCharacterId: state.selectedCharacterId,
        selectedChatId: state.selectedChatId,
        selectedModelId: state.selectedModelId,
        activeRightPanelTab: state.activeRightPanelTab,
        sidebarCollapsed: state.sidebarCollapsed,
        msgFontPx: state.msgFontPx,
        msgLineHeight: state.msgLineHeight,
        msgContrast: state.msgContrast,
        narrationEnabled: state.narrationEnabled,
        quoteTintEnabled: state.quoteTintEnabled,
        continuousVoice: state.continuousVoice,
        voiceHintDismissed: state.voiceHintDismissed,
        narrationMigrated: state.narrationMigrated,
        msgInk: state.msgInk,
        surfaceFinish: state.surfaceFinish,
        chatBgOn: state.chatBgOn,
        chatBgLum: state.chatBgLum,
        chatBgContrast: state.chatBgContrast,
        chatBgTint: state.chatBgTint,
        ambientFogOn: state.ambientFogOn,
        // v1.1 (FF7) generation sampling scalars - neutral names, never
        // stopSequences (those are user content).
        genTemperature: state.genTemperature,
        genTopP: state.genTopP,
        genTopK: state.genTopK,
        genRepetitionPenalty: state.genRepetitionPenalty,
        genMaxOutput: state.genMaxOutput,
        genSeed: state.genSeed,
        genContextBudget: state.genContextBudget,
      }),
    },
  ),
);
