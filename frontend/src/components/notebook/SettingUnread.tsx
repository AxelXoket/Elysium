/**
 * SettingUnread - the line that stands IN PLACE of something that could not
 * be read, with the way out beside it.
 *
 * For a setting, the control is not drawn at all while its value is
 * unknown: a switch drawn off reads as "off" (and a screen reader says
 * "off"), an empty safeword box reads as "no safeword". This line is what
 * the reader sees there instead. The same line, with its own words, stands
 * in for a list that could not be loaded.
 *
 * "Try again" stays focusable while it asks (`PendingButton`), and the line
 * stays open while its own retry is out (`useRetryPress`: the query reads as
 * `pending`, not `error`, meanwhile). A retry that fails leaves the keyboard
 * on the button. A retry that works closes the line, and the keyboard moves
 * to what it unlocked (`focusAfter`, else the first control after the line
 * in its section) - but only when the keyboard was still on this line.
 *
 * `role="status"`, not an alert: nothing the reader did caused it, it comes
 * with the panel opening, and an alert per failed read would talk over them.
 */
import { useLayoutEffect, useRef } from "react";
import { RotateCcw } from "lucide-react";

import { Collapse } from "@/components/motion/Collapse";
import { PendingButton } from "@/components/notebook/PendingButton";
import { useRetryPress } from "@/components/notebook/useRetryPress";
import { SETTING_RETRY, SETTING_UNREAD } from "@/components/notebook/noteProse";

/** What can take the keyboard after the line closes. */
const FOCUSABLE =
  'button, input, textarea, select, [role="switch"], [role="combobox"], [tabindex="0"]';

export function SettingUnread({
  id,
  open,
  retrying,
  settled,
  onRetry,
  text = SETTING_UNREAD,
  tone = "error",
  focusAfter,
}: {
  id?: string;
  /** The read failed (the query's `isError`). */
  open: boolean;
  /** The query is fetching (its `isFetching`). */
  retrying: boolean;
  /** When the query last settled (`settledAt(query)`). */
  settled: number;
  onRetry: () => void;
  /** The words, when it is not a setting. */
  text?: string;
  /** "error" for a setting, "muted" for the older list lines' look. */
  tone?: "error" | "muted";
  /** Where the keyboard goes once a retry has worked. */
  focusAfter?: () => HTMLElement | null;
}) {
  const lineRef = useRef<HTMLDivElement>(null);
  const retry = useRetryPress(settled, open);
  const shown = open || retry.out;
  useLayoutEffect(() => {
    if (retry.worked === 0) return;
    const line = lineRef.current;
    const active = document.activeElement;
    const ours = active == null || active === document.body
      || (line != null && line.contains(active));
    if (!ours) return;
    (focusAfter?.() ?? nextControl(line))?.focus();
    // Only a press that ended with the value read moves the keyboard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retry.worked]);
  return (
    <Collapse open={shown} className="min-w-0">
      <div ref={lineRef} className="flex items-center gap-2">
        <p
          id={id}
          className={tone === "error"
            ? "persona-local-error min-w-0 flex-1"
            : "min-w-0 flex-1 text-label leading-relaxed text-muted-foreground"}
          role="status"
        >
          {text}
        </p>
        <PendingButton
          type="button"
          size="sm"
          variant="ghost"
          pending={retrying || retry.out}
          onClick={() => {
            retry.press();
            onRetry();
          }}
          className="persona-ghost-action h-7 shrink-0 gap-1.5 px-2 text-ui"
        >
          <RotateCcw size={12} className="size-3" />
          {SETTING_RETRY}
        </PendingButton>
      </div>
    </Collapse>
  );
}

/** The first control after the line in its section that can take focus,
 *  else the last one before it (an empty list has nothing after the line),
 *  else the section itself, made focusable for this one hand-over. */
function nextControl(line: HTMLElement | null): HTMLElement | null {
  const section = line?.closest("section") ?? null;
  if (!line || !section) return null;
  const usable = [...section.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) =>
    !line.contains(el)
    && !el.closest("[inert]")
    && !el.hasAttribute("disabled")
    && el.getAttribute("aria-hidden") !== "true"
    && el.tabIndex >= 0);
  const after = usable.find((el) =>
    (line.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
  if (after) return after;
  const before = usable.filter((el) =>
    (line.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING) !== 0);
  if (before.length) return before[before.length - 1];
  if (!section.hasAttribute("tabindex")) section.tabIndex = -1;
  return section;
}
