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
    void getChatBgBlob().then((blob) => {
      if (cancelled || !blob) return;
      url = URL.createObjectURL(blob);
      setObjectUrl(url);
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
