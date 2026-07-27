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
import { useEffect, useState, type CSSProperties } from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
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

export function useChatBackground(): ChatBackground {
  const on = useUiStore((s) => s.chatBgOn);
  const clearChatBg = useUiStore((s) => s.clearChatBg);
  const rev = useUiStore((s) => s.chatBgRev);
  const lum = useUiStore((s) => s.chatBgLum);
  const contrast = useUiStore((s) => s.chatBgContrast);
  const tint = useUiStore((s) => s.chatBgTint);
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
          // rendering none. Make the flag agree with what is actually there.
          clearChatBg();
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
        clearChatBg();
        useErrorStore
          .getState()
          .pushErrorDirect(
            "chat_background_unreadable",
            "The chat background could not be loaded, so it has been turned off.",
            "warning",
          );
        void err;
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      setObjectUrl(null);
    };
  }, [on, rev]);

  if (!on || objectUrl == null) return { style: null, dark: false };

  const resolved = resolveTint(tint, lum);
  const layers = buildBgLayers(objectUrl, resolved, contrast);
  if (layers == null) return { style: null, dark: false };
  return {
    style: layers as CSSProperties,
    dark: computeEff(lum, contrast, resolved) < 0.5,
  };
}
