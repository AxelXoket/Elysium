import { motion as m } from "motion/react";
import { ImagePlus } from "lucide-react";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { MAX_ATTACHMENTS } from "./attachments";

/**
 * DropZoneOverlay - the full-panel drop affordance (v1.1 KUME B3, z per H19).
 *
 * Pure presentation. `pointer-events: none` (from the CSS class) is mandatory:
 * every drag event must stay on <main> so the enter/leave depth counter in
 * useFileDrop never storms. The milk veil is opacity-based, NOT a
 * backdrop-filter, because a blur over the animated canvas fog recomposites
 * every frame (same living-mist law as the lock overlay).
 */
export function DropZoneOverlay() {
  const reduced = useReducedMotion();
  return (
    <m.div
      className="drop-overlay"
      role="status"
      aria-label="Drop images to attach"
      initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.985 }}
      animate={reduced ? { opacity: 1 } : { opacity: 1, scale: 1 }}
      transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
    >
      <div className="drop-overlay-frame">
        <ImagePlus
          size={44}
          style={{ color: "var(--color-es-primary-sage-deep)" }}
          aria-hidden="true"
        />
        <p
          className="text-base font-medium"
          style={{ color: "var(--color-es-text-dark)" }}
        >
          Drop your image here
        </p>
        <p
          className="text-xs"
          style={{ color: "var(--color-es-text-dark)", opacity: 0.65 }}
        >
          {`PNG, JPEG or WebP - up to ${MAX_ATTACHMENTS} per message`}
        </p>
      </div>
    </m.div>
  );
}
