import { UserCircle } from "lucide-react";

/**
 * PersonaPanel — Phase 6E-A shell only.
 *
 * Persona setup (display name, persona prompt) is coming in Phase 6E-B.
 * No persistence of any kind is implemented here:
 *   - no localStorage / sessionStorage / IndexedDB
 *   - no Zustand state writes
 *   - no backend endpoint
 *   - no functional form inputs
 *
 * This panel exists to make the tab structure complete and intentional.
 */
export function PersonaPanel() {
  return (
    <div className="flex h-full flex-col items-center justify-center p-6 text-center">
      <div
        className="flex h-14 w-14 items-center justify-center rounded-2xl"
        style={{
          backgroundColor: "rgba(167, 200, 161, 0.10)",
          color: "var(--color-es-primary-sage)",
        }}
      >
        <UserCircle size={28} strokeWidth={1.5} />
      </div>

      <h3
        className="mt-4 text-sm font-semibold"
        style={{ color: "var(--color-es-text-light)" }}
      >
        Your Persona
      </h3>

      <p
        className="mt-2 max-w-[220px] text-xs leading-relaxed"
        style={{ color: "var(--color-es-text-muted)" }}
      >
        Set your display name and personal context for conversations.
      </p>

      <div
        className="mt-5 rounded-xl px-4 py-2.5 text-xs"
        style={{
          backgroundColor: "rgba(215, 168, 110, 0.10)",
          color: "var(--color-es-accent-amber)",
          border: "1px solid rgba(215, 168, 110, 0.18)",
        }}
      >
        Coming in Phase 6E-B
      </div>
    </div>
  );
}
