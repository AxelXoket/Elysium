/**
 * ChatBgFramingPreview - drag the wallpaper to choose what you see.
 *
 * WHY THE BOX IS THE SHAPE IT IS
 *   It is drawn at the LIVE chat area's width-to-height ratio, published by
 *   ChatCanvas. A square preview would be a lie: `cover` crops against the
 *   shape of the thing it fills, so a picture framed inside a square and then
 *   painted on a wide chat area is cropped somewhere the user never saw. Same
 *   ratio, same crop, at any size.
 *
 * WHY IT SHARES THE HOOK
 *   The picture here comes from `useChatBackground`, the same hook the chat
 *   itself uses, handed the same chat-area ratio. There is no second copy of
 *   the layer maths to fall out of step with the first, so what is in this box
 *   is what lands behind the messages - not an approximation of it.
 *
 * THE DRAG
 *   Grab-and-move, the way every photo cropper behaves: pull the picture right
 *   and you see more of its left side. In `background-position` terms that is
 *   a DECREASE, which is why the deltas below are subtracted. A full sweep of
 *   the box covers the full range, so the control feels the same on a small
 *   window as on a large one.
 */
import { useCallback, useRef, type CSSProperties, type PointerEvent } from "react";

import { useChatBackground } from "@/lib/appearance/useChatBackground";
import { useUiStore } from "@/lib/store/uiStore";

/** Used only to draw the box when no chat area has been measured yet. The
 * picture is still shown cover-fitted, which is what the chat would do too. */
const FALLBACK_ASPECT = 16 / 10;

/** One arrow-key press, in percent. Small enough to place a face, large
 * enough that crossing the picture is not a hundred presses. */
const KEY_STEP = 2;

/**
 * Take or give back the pointer, if this environment has the calls at all.
 *
 * Both are best-effort by construction. The drag works without capture - it
 * just stops tracking once the pointer leaves the box - so a missing or
 * refused API is a small loss of polish, while an exception thrown out of an
 * event handler unmounts the settings panel around it.
 */
function capture(
  event: PointerEvent<HTMLDivElement>,
  action: "set" | "release",
): void {
  const node = event.currentTarget;
  try {
    if (action === "set") {
      node.setPointerCapture?.(event.pointerId);
    } else if (node.hasPointerCapture?.(event.pointerId)) {
      node.releasePointerCapture?.(event.pointerId);
    }
  } catch {
    // A pointer that has already been released throws NotFoundError; the
    // drag is over either way, which is all this function is for.
  }
}

export function ChatBgFramingPreview({ disabled }: { disabled?: boolean }) {
  const measured = useUiStore((s) => s.chatAreaAspect);
  const focusX = useUiStore((s) => s.chatBgFocusX);
  const focusY = useUiStore((s) => s.chatBgFocusY);
  const setChatBgFraming = useUiStore((s) => s.setChatBgFraming);
  const aspect = measured ?? FALLBACK_ASPECT;
  // Passive: this is a viewer, not the owner of the setting. Opening the
  // panel must not be able to turn somebody's wallpaper off because the blob
  // read happened to fail while they were looking at it.
  const background = useChatBackground(measured, { reconcile: false });
  const boxRef = useRef<HTMLDivElement>(null);
  const dragFrom = useRef<{ x: number; y: number } | null>(null);

  const nudge = useCallback(
    (dxPercent: number, dyPercent: number) => {
      setChatBgFraming({
        focusX: focusX + dxPercent,
        focusY: focusY + dyPercent,
      });
    },
    [focusX, focusY, setChatBgFraming],
  );

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    dragFrom.current = { x: event.clientX, y: event.clientY };
    // Capture, so a drag that leaves the box still steers it. Without this the
    // picture stops moving the moment the pointer crosses the edge, which is
    // exactly when someone is pushing towards a corner.
    //
    // Optional, though: pointer capture is a convenience on top of a drag that
    // already works without it, so an environment that does not implement it
    // should cost the overshoot handling and nothing else. Throwing here would
    // take the whole settings panel down with it.
    capture(event, "set");
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const from = dragFrom.current;
    const box = boxRef.current;
    if (!from || !box || disabled) return;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const dx = ((event.clientX - from.x) / rect.width) * 100;
    const dy = ((event.clientY - from.y) / rect.height) * 100;
    // Subtracted: dragging the picture right shows more of its LEFT.
    nudge(-dx, -dy);
    dragFrom.current = { x: event.clientX, y: event.clientY };
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    dragFrom.current = null;
    capture(event, "release");
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    // Arrow keys move the VIEW the way the arrow points, which is the
    // opposite sign to the drag: there the user holds the picture, here they
    // are pushing the window over it.
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-KEY_STEP, 0],
      ArrowRight: [KEY_STEP, 0],
      ArrowUp: [0, -KEY_STEP],
      ArrowDown: [0, KEY_STEP],
    };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    nudge(move[0], move[1]);
  };

  return (
    <div
      ref={boxRef}
      className="chat-bg-framing-preview"
      role="group"
      tabIndex={disabled ? -1 : 0}
      aria-label={
        `Wallpaper framing. Showing ${Math.round(focusX)} percent across and ` +
        `${Math.round(focusY)} percent down the picture. ` +
        "Drag, or use the arrow keys."
      }
      aria-disabled={disabled || undefined}
      data-empty={background.style == null ? "true" : undefined}
      style={
        {
          aspectRatio: `${aspect}`,
          ...(background.style ?? {}),
        } as CSSProperties
      }
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={handleKeyDown}
    >
      {/* The frame markings sit ON the picture, so the box reads as a viewport
          over something larger rather than as a plain thumbnail. */}
      <span className="chat-bg-framing-grid" aria-hidden="true" />
      {background.style == null && (
        <span className="chat-bg-framing-empty">No picture yet</span>
      )}
    </div>
  );
}
