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

/** Framing bounds. Zoom 1 is plain cover - the whole picture, biggest side
 * touching. Above that the picture is scaled up and the surplus is cropped
 * away, which is what makes a focal point mean anything on a photo whose
 * shape already matches the window. */
export const CHAT_BG_ZOOM_MIN = 1;
export const CHAT_BG_ZOOM_MAX = 3;
export const CHAT_BG_FOCUS_DEFAULT = 50;

/** How the picture sits inside the chat area. Percentages, because that is
 * the unit that survives a resize: `background-position: 40% 25%` keeps the
 * same point of the PICTURE over the same point of the WINDOW at every size,
 * which is exactly what a stored pixel rectangle could not do. */
export interface ChatBgFraming {
  /** 0 = show the left edge, 100 = the right. */
  focusX: number;
  /** 0 = show the top, 100 = the bottom. */
  focusY: number;
  /** 1 = fit the whole picture (cover). Higher crops in. */
  zoom: number;
}

export const CHAT_BG_FRAMING_DEFAULT: ChatBgFraming = {
  focusX: CHAT_BG_FOCUS_DEFAULT,
  focusY: CHAT_BG_FOCUS_DEFAULT,
  zoom: CHAT_BG_ZOOM_MIN,
};

export function clampFraming(framing: Partial<ChatBgFraming>): ChatBgFraming {
  const pct = (v: unknown, fallback: number) =>
    typeof v === "number" && Number.isFinite(v)
      ? Math.min(100, Math.max(0, v))
      : fallback;
  const zoom =
    typeof framing.zoom === "number" && Number.isFinite(framing.zoom)
      ? Math.min(CHAT_BG_ZOOM_MAX, Math.max(CHAT_BG_ZOOM_MIN, framing.zoom))
      : CHAT_BG_ZOOM_MIN;
  return {
    focusX: pct(framing.focusX, CHAT_BG_FOCUS_DEFAULT),
    focusY: pct(framing.focusY, CHAT_BG_FOCUS_DEFAULT),
    zoom,
  };
}

/**
 * The `background-size` for the image layer.
 *
 * At zoom 1 this is the literal keyword `cover`, not a computed equivalent.
 * That is deliberate: `cover` needs no measurement, so a background keeps
 * working before the chat area has been measured, in a test environment with
 * no layout, and on the first paint after a resize. Every existing user is on
 * zoom 1, so every existing user keeps the exact string they have now.
 *
 * Above zoom 1 the keyword cannot help - there is no `cover * 1.4` - so the
 * scale is written out. Which axis carries it depends on which one `cover`
 * was already stretching to fill: the picture is pinned along its binding
 * axis and allowed to overflow on the other, and `auto` keeps the aspect
 * ratio. Without the two ratios we cannot know which axis binds, so an
 * unmeasured area falls back to `cover` rather than guessing and skewing the
 * picture.
 */
export function bgSizeFor(
  zoom: number,
  imageAspect: number | null,
  areaAspect: number | null,
): string {
  const z = clampFraming({ zoom }).zoom;
  if (z <= CHAT_BG_ZOOM_MIN) return "cover";
  if (
    imageAspect == null || areaAspect == null ||
    !Number.isFinite(imageAspect) || !Number.isFinite(areaAspect) ||
    imageAspect <= 0 || areaAspect <= 0
  ) {
    return "cover";
  }
  const scale = `${(z * 100).toFixed(2).replace(/\.?0+$/, "")}%`;
  // Wider than the area: cover was matching HEIGHT, so height carries the zoom.
  return imageAspect >= areaAspect ? `auto ${scale}` : `${scale} auto`;
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
  framing: Partial<ChatBgFraming> = CHAT_BG_FRAMING_DEFAULT,
  imageAspect: number | null = null,
  areaAspect: number | null = null,
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
  const { focusX, focusY, zoom } = clampFraming(framing);
  const size = bgSizeFor(zoom, imageAspect, areaAspect);
  // `center` rather than `50% 50%` at the default, so an untouched background
  // keeps the byte-identical declaration it has always had.
  const position =
    focusX === CHAT_BG_FOCUS_DEFAULT && focusY === CHAT_BG_FOCUS_DEFAULT
      ? "center"
      : `${round2(focusX)}% ${round2(focusY)}%`;
  return {
    backgroundImage: `linear-gradient(${scrim}, ${scrim}), url("${objectUrl}")`,
    backgroundSize: `100% 100%, ${size}`,
    backgroundPosition: `0 0, ${position}`,
    backgroundRepeat: "no-repeat",
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export interface ProcessedChatBg {
  blob: Blob;
  lum: number;
  /** width / height of the stored image. Recorded here because it is free at
   * this point and expensive later: `bgSizeFor` needs it to know which axis a
   * zoom hangs off, and the alternative is decoding the blob again on every
   * mount just to read two numbers. */
  aspect: number;
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
    return { blob, lum, aspect: w / h };
  } finally {
    bitmap.close();
  }
}
