import { create } from "zustand";
import { persist } from "zustand/middleware";

// ── Tab type (Phase 6E-A: renamed from "model|info|settings") ──────────────
// Old persisted values ("model", "info", "settings") are migrated in the
// persist config below. Any stale localStorage is normalized on first load.
type RightPanelTab = "models" | "secrets" | "persona";

interface UiState {
  selectedCharacterId: number | null;
  selectedChatId: number | null;
  selectedModelId: string | null;
  activeRightPanelTab: RightPanelTab;
  sidebarCollapsed: boolean;

  selectCharacter: (id: number | null) => void;
  selectChat: (id: number | null) => void;
  selectModel: (id: string | null) => void;
  setActiveRightPanelTab: (tab: RightPanelTab) => void;
  toggleSidebar: () => void;
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

      selectCharacter: (id) =>
        set({ selectedCharacterId: id, selectedChatId: null }),
      selectChat: (id) => set({ selectedChatId: id }),
      selectModel: (id) => set({ selectedModelId: id }),
      setActiveRightPanelTab: (tab) => set({ activeRightPanelTab: tab }),
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
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
      // Only harmless UI preferences are persisted — never secrets, content, or API data.
      // Persona fields are NOT persisted here (Phase 6E-A — persona persistence deferred to 6E-B).
      partialize: (state) => ({
        selectedCharacterId: state.selectedCharacterId,
        selectedChatId: state.selectedChatId,
        selectedModelId: state.selectedModelId,
        activeRightPanelTab: state.activeRightPanelTab,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    },
  ),
);
