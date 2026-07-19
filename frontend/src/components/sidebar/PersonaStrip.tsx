import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePersonas, useSelectPersona } from "@/lib/query/personas";
import { findActivePersona } from "@/lib/personas";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { Check, ChevronDown, Loader2, UserCircle } from "lucide-react";

/**
 * PersonaStrip - the always-visible identity anchor at the top of the sidebar.
 *
 * Shows the active persona (who "you" are in the conversation) and, on click,
 * a portaled switcher to change it in one tap. The active persona is derived
 * from the backend (`is_active`, set via POST /personas/{id}/select) - this
 * component never keeps its own active-persona state, it just mirrors and
 * mutates the same source the Persona panel uses.
 *
 * Deliberately NOT labelled "Playing as…" - the framing is the app's own
 * "Persona" terminology, matching the right-panel tab.
 */
function initialOf(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed[0].toUpperCase() : "?";
}

export function PersonaStrip() {
  const { data: personas, isLoading } = usePersonas();
  const selectPersona = useSelectPersona();
  const setActiveTab = useUiStore((s) => s.setActiveRightPanelTab);
  const pushError = useErrorStore((s) => s.pushError);

  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(
    null,
  );
  const [pendingId, setPendingId] = useState<number | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const active = findActivePersona(personas);

  const openMenu = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPos({ top: rect.bottom + 6, left: rect.left, width: rect.width });
    setOpen(true);
  };

  // On open, move focus into the menu (it is portaled to body, so natural Tab
  // order from the trigger would skip it entirely). Escape returns focus.
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);

  // Portaled dropdown: close on Escape (return focus), outside click, or any
  // scroll/resize (the fixed coords would otherwise drift). Same discipline as
  // the chat ⋯ menu, plus the ARIA menu keyboard pattern (arrows/Home/End
  // move focus through the items, wrapping).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const items = menuRef.current
        ? Array.from(
            menuRef.current.querySelectorAll<HTMLButtonElement>(
              "button:not(:disabled)",
            ),
          )
        : [];
      if (items.length === 0) return;
      e.preventDefault();
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      let next: number;
      if (e.key === "Home") next = 0;
      else if (e.key === "End") next = items.length - 1;
      else if (e.key === "ArrowDown")
        next = current < 0 ? 0 : (current + 1) % items.length;
      else next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
      items[next]?.focus();
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    // Capture-phase scroll also fires for the menu's OWN internal scroll
    // region - closing then would make personas below the fold unreachable.
    // Only outside scrolls (which detach the fixed anchor) close the menu.
    const onReflow = (e: Event) => {
      if (e.type === "scroll" && menuRef.current?.contains(e.target as Node)) {
        return;
      }
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open]);

  const handleSelect = async (id: number) => {
    if (id === active?.id) {
      setOpen(false);
      return;
    }
    setPendingId(id);
    try {
      await selectPersona.mutateAsync(id);
      setOpen(false);
    } catch (err) {
      pushError(err);
    } finally {
      setPendingId(null);
    }
  };

  const goManage = () => {
    setOpen(false);
    setActiveTab("persona");
  };

  const hasPersonas = personas != null && personas.length > 0;

  return (
    <div className="px-3 pt-3">
      <button
        ref={triggerRef}
        type="button"
        className="persona-strip"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Change persona"
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        <span className="persona-strip-avatar" aria-hidden="true">
          {active ? initialOf(active.display_name) : <UserCircle size={15} />}
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="persona-strip-label">Persona</span>
          <span className="persona-strip-name">
            {isLoading ? "…" : active ? active.display_name : "No persona"}
          </span>
        </span>
        <ChevronDown size={13} className="persona-strip-chevron" />
      </button>

      {open &&
        pos &&
        createPortal(
          <div
            ref={menuRef}
            className="persona-menu"
            role="menu"
            aria-label="Select persona"
            style={{
              position: "fixed",
              top: pos.top,
              left: pos.left,
              width: Math.max(pos.width, 200),
            }}
          >
            {hasPersonas ? (
              <>
                <div className="persona-menu-scroll">
                  {personas.map((persona) => (
                    <button
                      key={persona.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={persona.is_active}
                      className="persona-menu-item"
                      disabled={selectPersona.isPending}
                      onClick={() => void handleSelect(persona.id)}
                    >
                      <span className="persona-menu-avatar" aria-hidden="true">
                        {initialOf(persona.display_name)}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-left">
                        {persona.display_name}
                      </span>
                      {pendingId === persona.id ? (
                        <Loader2 size={13} className="shrink-0 animate-spin" />
                      ) : persona.is_active ? (
                        <Check size={13} className="shrink-0" />
                      ) : null}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="persona-menu-manage"
                  onClick={goManage}
                >
                  Manage personas
                </button>
              </>
            ) : (
              <div className="persona-menu-empty">
                <p>No personas yet.</p>
                <button
                  type="button"
                  className="persona-menu-manage mt-1"
                  onClick={goManage}
                >
                  Create one
                </button>
              </div>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
