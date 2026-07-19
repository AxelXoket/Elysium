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
  /** Style *asterisk* narration spans in message text. */
  narrationEnabled: boolean;
  /** Tint "quoted speech" spans in message text. */
  quoteTintEnabled: boolean;

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

  selectCharacter: (id: number | null) => void;
  selectChat: (id: number | null) => void;
  selectModel: (id: string | null) => void;
  setActiveRightPanelTab: (tab: RightPanelTab) => void;
  toggleSidebar: () => void;
  setMsgFontPx: (px: number) => void;
  setMsgLineHeight: (lh: number) => void;
  setNarrationEnabled: (on: boolean) => void;
  setQuoteTintEnabled: (on: boolean) => void;
  /** Image stored → mark on + record its luminance (contrast/tint kept). */
  setChatBgMeta: (meta: { lum: number }) => void;
  /** Image removed → mark off (contrast/tint kept for the next image). */
  clearChatBg: () => void;
  setChatBgContrast: (contrast: number) => void;
  setChatBgTint: (tint: string) => void;
  setAmbientFogOn: (on: boolean) => void;
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
      narrationEnabled: true,
      quoteTintEnabled: true,
      chatBgOn: false,
      chatBgLum: 0.5,
      chatBgContrast: 0.35,
      chatBgTint: "auto",
      chatBgRev: 0,
      ambientFogOn: true,

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
      setNarrationEnabled: (on) => set({ narrationEnabled: on }),
      setQuoteTintEnabled: (on) => set({ quoteTintEnabled: on }),
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
        narrationEnabled: state.narrationEnabled,
        quoteTintEnabled: state.quoteTintEnabled,
        chatBgOn: state.chatBgOn,
        chatBgLum: state.chatBgLum,
        chatBgContrast: state.chatBgContrast,
        chatBgTint: state.chatBgTint,
        ambientFogOn: state.ambientFogOn,
      }),
    },
  ),
);
