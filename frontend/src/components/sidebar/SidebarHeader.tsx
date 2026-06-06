export function SidebarHeader() {
  return (
    <div className="px-4 py-4">
      <div className="sidebar-brand">
        <div className="elysium-leafmark" aria-hidden="true">
          <i />
          <span />
          <span />
          <span />
        </div>
        <div className="flex min-w-0 flex-col">
          <span
            className="truncate text-sm font-semibold leading-none tracking-tight"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Elysium
          </span>
          <span
            className="mt-1 truncate text-[10px] leading-none"
            style={{ color: "var(--color-es-text-muted)", opacity: 0.76 }}
          >
            Local-first · Private
          </span>
        </div>
      </div>
    </div>
  );
}
