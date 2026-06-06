import { useUiStore } from "@/lib/store/uiStore";
import { Settings } from "lucide-react";

export function SidebarFooter() {
  const setTab = useUiStore((s) => s.setActiveRightPanelTab);

  return (
    <div className="px-3 py-3">
      <button
        type="button"
        onClick={() => setTab("secrets")}
        className="sidebar-item flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs"
        style={{ color: "var(--color-es-text-muted)" }}
        aria-label="Open secrets and API configuration"
      >
        <Settings size={13} />
        <span>Settings</span>
      </button>
      <span
        className="mt-1 block px-3 text-[10px]"
        style={{ color: "var(--color-es-text-muted)", opacity: 0.45 }}
      >
        v0.1 · Local-first
      </span>
    </div>
  );
}
