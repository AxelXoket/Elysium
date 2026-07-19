import { useState } from "react";
import { AppSettingsDialog } from "@/components/settings/AppSettingsDialog";
import { Settings } from "lucide-react";

export function SidebarFooter() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="px-3 py-3">
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        className="sidebar-item flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs"
        style={{ color: "var(--color-es-text-muted)" }}
        aria-label="Open settings"
      >
        <Settings size={13} />
        <span>Settings</span>
      </button>
      <span
        className="mt-1 block px-3 text-[10px]"
        style={{ color: "var(--color-es-text-muted)", opacity: 0.45 }}
      >
        v{__APP_VERSION__} · Local-first
      </span>

      <AppSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
