/**
 * chatBackground.ts - chat wallpaper math and image processing.
 *
 * Ports Wisteria's background pipeline (v1: plain cover-fit, no focal
 * cropper). All luminance math deliberately stays in NON-linear sRGB -
 * both the 24×24 image average and hexLum skip gamma linearization, because
 * the 0.55 auto-tint threshold and the 0.5 text threshold were tuned
 * against those values; "fixing" one side desynchronizes the two decisions.
 *
 * The image pipeline is Blob-only: decode via createImageBitmap, encode via
 * canvas.toBlob - no FileReader/data-URI anywhere (static-safety S-21).
 */

/** Longest side after downscale (Wisteria parity). */
export const CHAT_BG_MAX_DIMENSION = 2048;
/** JPEG re-encode quality (Wisteria parity). */
export const CHAT_BG_JPEG_QUALITY = 0.9;
/** Contrast (scrim) slider bounds/default (Wisteria parity). */
export const CHAT_BG_CONTRAST_MIN = 0;
export const CHAT_BG_CONTRAST_MAX = 0.85;
export const CHAT_BG_CONTRAST_DEFAULT = 0.35;

/** Auto-tint endpoints mapped to Elysium Azure tokens: paper (light canvas)
 * and ink (dark surface) - behaviorally equivalent to Wisteria's
 * bone/charcoal. */
export const CHAT_BG_PAPER = "#EDF3FA";
export const CHAT_BG_INK = "#161A1D";

/** Tint swatches (auto first) - blue/neutral family only. hexLum values
 * span both sides of 0.5. */
export const CHAT_BG_TINTS: readonly { id: string; label: string }[] = [
  { id: "auto", label: "Auto" },
  { id: CHAT_BG_PAPER, label: "Paper" },
  { id: CHAT_BG_INK, label: "Ink" },
  { id: "#2A3648", label: "Slate" },
  { id: "#4A6C94", label: "Steel" },
  { id: "#8FB2D9", label: "Sky" },
  { id: "#7FA1B3", label: "Mist" },
];

/** Rec.709 luminance of a #rrggbb hex, over non-linear sRGB, 0..1. */
export function hexLum(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return 0.5;
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 0xff;
  const g = (v >> 8) & 0xff;
  const b = v & 0xff;
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

/** 'auto' resolves with the image's own brightness class (reinforcing it,
 * not fighting it): light image → paper scrim, dark image → ink scrim. */
export function resolveTint(tint: string, lum: number): string {
  if (tint === "auto") return lum >= 0.55 ? CHAT_BG_PAPER : CHAT_BG_INK;
  return tint;
}

/** Effective brightness under the scrim; < 0.5 means bare-canvas chrome
 * must switch to its light (chat-bg-dark) variants. */
export function computeEff(lum: number, contrast: number, tintHex: string): number {
  return lum * (1 - contrast) + hexLum(tintHex) * contrast;
}

export interface ChatBgLayers {
  backgroundImage: string;
  backgroundSize: string;
  backgroundPosition: string;
  backgroundRepeat: string;
}

/** Two layers, scrim FIRST (top): a uniform tint at alpha=contrast over the
 * cover-fit image. Per-layer size/position lists are comma-matched. */
export function buildBgLayers(
  objectUrl: string,
  tintHex: string,
  contrast: number,
): ChatBgLayers | null {
  // CSS url("...") injection guard (Wisteria parity); blob: URLs never
  // contain quotes, so this only rejects hostile/garbage input.
  if (objectUrl.includes('"')) return null;
  const m = /^#([0-9a-f]{6})$/i.exec(tintHex.trim());
  if (!m) return null;
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 0xff;
  const g = (v >> 8) & 0xff;
  const b = v & 0xff;
  const c = Math.min(CHAT_BG_CONTRAST_MAX, Math.max(CHAT_BG_CONTRAST_MIN, contrast));
  const scrim = `rgba(${r}, ${g}, ${b}, ${c})`;
  return {
    backgroundImage: `linear-gradient(${scrim}, ${scrim}), url("${objectUrl}")`,
    backgroundSize: "100% 100%, cover",
    backgroundPosition: "0 0, center",
    backgroundRepeat: "no-repeat",
  };
}

export interface ProcessedChatBg {
  blob: Blob;
  lum: number;
}

/**
 * Decode → downscale (longest side ≤ 2048) → luminance-sample (24×24
 * average) → re-encode as JPEG q0.9. The canvas is pre-filled with the
 * paper color so transparent PNG regions become theme paper, not black
 * (fixes a Wisteria quirk).
 */
export async function processChatBgImage(file: Blob): Promise<ProcessedChatBg> {
  const bitmap = await createImageBitmap(file);
  try {
    const maxSide = Math.max(bitmap.width, bitmap.height);
    const k = maxSide > CHAT_BG_MAX_DIMENSION ? CHAT_BG_MAX_DIMENSION / maxSide : 1;
    const w = Math.max(1, Math.round(bitmap.width * k));
    const h = Math.max(1, Math.round(bitmap.height * k));

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas_unavailable");
    ctx.fillStyle = CHAT_BG_PAPER;
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bitmap, 0, 0, w, h);

    const sample = document.createElement("canvas");
    sample.width = 24;
    sample.height = 24;
    const sctx = sample.getContext("2d", { willReadFrequently: true });
    if (!sctx) throw new Error("canvas_unavailable");
    sctx.drawImage(canvas, 0, 0, 24, 24);
    const data = sctx.getImageData(0, 0, 24, 24).data;
    let lum = 0;
    for (let i = 0; i < data.length; i += 4) {
      lum += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
    }
    lum /= 255 * 576;

    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (out) => (out ? resolve(out) : reject(new Error("encode_failed"))),
        "image/jpeg",
        CHAT_BG_JPEG_QUALITY,
      );
    });
    return { blob, lum };
  } finally {
    bitmap.close();
  }
}
