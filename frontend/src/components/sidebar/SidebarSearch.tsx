import { Search, X } from "lucide-react";

/**
 * SidebarSearch - the SEARCH section. v1 scope is characters only (once a
 * character is picked its chats list below, so name search is enough for
 * now); cross-character / message search is a later step. Purely client-side:
 * the value is lifted to Sidebar and filters the character list in place.
 */
interface SidebarSearchProps {
  value: string;
  onChange: (value: string) => void;
}

export function SidebarSearch({ value, onChange }: SidebarSearchProps) {
  return (
    <div className="px-3 pt-2 pb-3">
      <p
        className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-widest"
        style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
      >
        Search
      </p>
      <div className="sidebar-search">
        <Search size={13} className="sidebar-search-icon" aria-hidden="true" />
        <input
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Search characters…"
          aria-label="Search characters"
          className="sidebar-search-input"
        />
        {value !== "" && (
          <button
            type="button"
            aria-label="Clear search"
            className="sidebar-search-clear"
            onClick={() => onChange("")}
          >
            <X size={12} />
          </button>
        )}
      </div>
    </div>
  );
}
