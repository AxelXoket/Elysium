/**
 * useChatBackground - object-URL lifecycle + computed style for the chat
 * wallpaper.
 *
 * Reads the persisted scalars (on/lum/contrast/tint) from uiStore and the
 * image Blob from the appearance store, minting a fresh object URL per
 * mount/replace and revoking it on cleanup. StrictMode-safe: the async blob
 * read is guarded by a cancelled flag so a torn-down mount can neither
 * leak its URL nor resurrect a cleared background.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { getChatBgBlob } from "@/lib/store/chatBgDb";
import {
  buildBgLayers,
  computeEff,
  resolveTint,
} from "@/lib/appearance/chatBackground";

export interface ChatBackground {
  /** Background layer styles for the scroll container, or null when off. */
  style: CSSProperties | null;
  /** True when bare-canvas chrome must switch to light (chat-bg-dark). */
  dark: boolean;
}

/**
 * The width/height ratio of whatever element this ref is attached to, kept
 * current as the window is resized.
 *
 * The wallpaper has to answer "which part of the picture" against the area it
 * is actually painted on, and that area changes with the window, the sidebar
 * and the right panel. Measuring it is what lets a zoomed picture crop the
 * same way at every size instead of being framed for one window and wrong in
 * the rest.
 *
 * null until the first measurement, and null again for a zero-sized element:
 * a ratio computed from a height of 0 is Infinity, and callers are expected
 * to fall back rather than to divide by it.
 */
export function useAreaAspect<T extends HTMLElement>(): {
  ref: (node: T | null) => void;
  aspect: number | null;
} {
  const [aspect, setAspect] = useState<number | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: T | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) {
      setAspect(null);
      return;
    }
    const measure = () => {
      const { width, height } = node.getBoundingClientRect();
      // Rounded before comparing: a sub-pixel reflow must not re-render the
      // whole chat, and nobody can see the fourth decimal of a crop.
      const next = width > 0 && height > 0
        ? Math.round((width / height) * 1000) / 1000
        : null;
      setAspect((prev) => (prev === next ? prev : next));
    };
    measure();
    // Guarded: ResizeObserver is everywhere the app ships, but jsdom in the
    // test environment has no such promise, and a missing observer should
    // cost the zoom, not the wallpaper.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  return { ref, aspect };
}

export function useChatBackground(
  /**
   * width / height of the area the wallpaper is painted on, or null when it
   * has not been measured. Only a zoomed picture needs it: `cover` sizes
   * itself, so an unmeasured area is un-zoomed rather than broken.
   */
  areaAspect: number | null = null,
  options: {
    /**
     * Whether this caller may CORRECT the stored state when the image turns
     * out to be unreadable - turning the background off and saying so.
     *
     * The chat does. A preview does not, and the distinction is not academic:
     * a passive viewer that reconciles can turn somebody's wallpaper off just
     * by being looked at. Opening the settings panel is not a statement about
     * whether the picture is still there.
     *
     * The alternative was a second, read-only copy of this hook, which would
     * have been a second copy of the layer maths to drift out of step with
     * the first - the exact thing sharing the hook is for.
     */
    reconcile?: boolean;
  } = {},
): ChatBackground {
  const reconcile = options.reconcile ?? true;
  const on = useUiStore((s) => s.chatBgOn);
  const clearChatBg = useUiStore((s) => s.clearChatBg);
  const rev = useUiStore((s) => s.chatBgRev);
  const lum = useUiStore((s) => s.chatBgLum);
  const contrast = useUiStore((s) => s.chatBgContrast);
  const tint = useUiStore((s) => s.chatBgTint);
  const focusX = useUiStore((s) => s.chatBgFocusX);
  const focusY = useUiStore((s) => s.chatBgFocusY);
  const zoom = useUiStore((s) => s.chatBgZoom);
  const imageAspect = useUiStore((s) => s.chatBgAspect);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    // The off case needs no work here: this run's early return leaves state
    // alone, and the PREVIOUS run's cleanup already revoked its URL and
    // cleared the state when `on` flipped.
    if (!on) return;
    let cancelled = false;
    let url: string | null = null;
    void getChatBgBlob()
      .then((blob) => {
        if (cancelled) return;
        if (!blob) {
          // `chatBgOn` is persisted independently of the blob, so an evicted
          // or missing image left the app claiming a background was set while
          // rendering none. Make the flag agree with what is actually there -
          // but only if this caller is the one entitled to say so.
          if (reconcile) clearChatBg();
          return;
        }
        url = URL.createObjectURL(blob);
        setObjectUrl(url);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // There was no .catch at all. chatBgDb.withStore rejects on
        // req.onerror, tx.onerror and tx.onabort, so any IndexedDB failure
        // became an unhandled promise rejection and this hook returned
        // "no background" forever - indistinguishable from having none. The
        // write path in AppSettingsDialog handles its errors; this one did not.
        //
        // A non-reconciling caller stays silent here too: it would be the
        // SECOND reader of the same failing store, and one problem reported
        // twice reads as two problems.
        if (!reconcile) return;
        clearChatBg();
        useErrorStore
          .getState()
          .pushErrorDirect(
            "chat_background_unreadable",
            getErrorMessage("chat_background_unreadable"),
            "warning",
          );
        void err;
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      setObjectUrl(null);
    };
    // clearChatBg is a zustand action defined once inside the store creator
    // (uiStore.ts:346), so its identity never changes and naming it here
    // cannot widen the set of renders this effect runs on. It is listed
    // because the effect calls it, and a lint rule that is right about the
    // dependency but silenced is worse than one that is satisfied.
  }, [on, rev, reconcile, clearChatBg]);

  if (!on || objectUrl == null) return { style: null, dark: false };

  const resolved = resolveTint(tint, lum);
  const layers = buildBgLayers(
    objectUrl, resolved, contrast,
    { focusX, focusY, zoom }, imageAspect, areaAspect,
  );
  if (layers == null) return { style: null, dark: false };
  return {
    style: layers as CSSProperties,
    dark: computeEff(lum, contrast, resolved) < 0.5,
  };
}
