/**
 * useFileDrop - drag-and-drop image staging + the file:/// navigation guard
 * (v1.1 KUME B, amended by H9/H14/H18).
 *
 * B0 (the hidden critical bug): there were NO drag handlers anywhere, so a
 * file dropped on the app navigated the top document to `file:///…`. In the
 * packaged exe there is no address bar, so that navigation KILLS the app until
 * restart. The window-level net below is a BUG FIX, not polish, and ships in
 * the same commit as the overlay.
 *
 * H14: every handler gates on `dataTransfer.types` containing "Files" FIRST -
 * a text-selection drag (dropping selected text into the composer) is left
 * completely untouched, including no preventDefault.
 *
 * H9: the drop handler forwards RAW File[] - handleAddAttachments is the
 * single filter+toast authority (a dropped GIF surfaces a reject toast there).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent as ReactDragEvent } from "react";
import { ACCEPTED_IMAGE_TYPES } from "./attachments";
import { useErrorStore } from "@/lib/errors/errorStore";

/** True only for OS file drags - never for text/URL/element drags (H14). */
function isFileDrag(dt: DataTransfer | null | undefined): boolean {
  return dt?.types?.includes("Files") ?? false;
}

/**
 * dragover only exposes item METADATA (not the files). Some sources report an
 * empty type; that is tolerated here - the drop-time filter in
 * handleAddAttachments is the single authority (H9). Used only to decide
 * whether to light up the overlay, never to accept/reject.
 */
function hasImagePayload(dt: DataTransfer | null | undefined): boolean {
  if (!dt) return false;
  return Array.from(dt.items ?? []).some(
    (i) => i.kind === "file" && (ACCEPTED_IMAGE_TYPES.has(i.type) || i.type === ""),
  );
}

export interface FileDropResult {
  dragActive: boolean;
  dropTargetProps: {
    onDragEnter: (e: ReactDragEvent) => void;
    onDragOver: (e: ReactDragEvent) => void;
    onDragLeave: (e: ReactDragEvent) => void;
    onDrop: (e: ReactDragEvent) => void;
  };
}

export function useFileDrop({
  gateOpen,
  onAddFiles,
}: {
  gateOpen: boolean;
  onAddFiles: (files: File[]) => void;
}): FileDropResult {
  const [dragActive, setDragActive] = useState(false);
  // Enter/leave fire per element crossed; a plain boolean would flicker off as
  // the pointer moves between children. A depth counter stays stable.
  const depthRef = useRef(0);

  // B0 window net: a Files drop ANYWHERE (sidebar, right panel, gaps) would
  // navigate to file:/// and brick the packaged SPA. Swallow it. UNCONDITIONAL
  // mount, but Files-only per event (H14: never touch a text-selection drag).
  useEffect(() => {
    const swallow = (e: globalThis.DragEvent) => {
      if (isFileDrag(e.dataTransfer)) e.preventDefault();
    };
    const reset = () => {
      depthRef.current = 0;
      setDragActive(false);
    };
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    // A drag cancelled outside the window (Esc, drop off-window) never fires a
    // leave on our target - reset the counter so the overlay cannot stick.
    window.addEventListener("drop", reset);
    window.addEventListener("dragend", reset);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
      window.removeEventListener("drop", reset);
      window.removeEventListener("dragend", reset);
    };
  }, []);

  const onDragEnter = useCallback(
    (e: ReactDragEvent) => {
      if (!isFileDrag(e.dataTransfer)) return; // H14
      depthRef.current += 1;
      if (gateOpen && hasImagePayload(e.dataTransfer)) setDragActive(true);
    },
    [gateOpen],
  );

  const onDragOver = useCallback(
    (e: ReactDragEvent) => {
      if (!isFileDrag(e.dataTransfer)) return; // H14
      e.preventDefault(); // required to receive the drop
      // dropEffect is not always writable on stubbed events - guard.
      try {
        e.dataTransfer.dropEffect = gateOpen ? "copy" : "none";
      } catch {
        /* ignore read-only dataTransfer in test stubs */
      }
    },
    [gateOpen],
  );

  const onDragLeave = useCallback((e: ReactDragEvent) => {
    if (!isFileDrag(e.dataTransfer)) return; // H14
    depthRef.current = Math.max(0, depthRef.current - 1);
    if (depthRef.current === 0) setDragActive(false);
  }, []);

  const onDrop = useCallback(
    (e: ReactDragEvent) => {
      if (!isFileDrag(e.dataTransfer)) return; // H14
      e.preventDefault();
      depthRef.current = 0;
      setDragActive(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (!gateOpen) {
        // B2 stages nothing while the gate is closed, which is right - but it
        // used to return with no toast, no banner and no overlay, so a real
        // user file simply vanished. This module's own H9/FF9 doctrine says
        // every rejection surfaces exactly once, and this was the one that
        // surfaced zero times.
        if (files.length > 0) {
          useErrorStore
            .getState()
            .pushErrorDirect(
              "attachment_gate_closed",
              "Images cannot be attached right now, so that file was not added.",
              "warning",
            );
        }
        return;
      }
      // H9: RAW files - handleAddAttachments filters + toasts.
      if (files.length > 0) onAddFiles(files);
    },
    [gateOpen, onAddFiles],
  );

  return {
    dragActive,
    dropTargetProps: { onDragEnter, onDragOver, onDragLeave, onDrop },
  };
}
