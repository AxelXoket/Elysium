/**
 * InkPicker.tsx - a colour wheel for message text, with the ratio in the open.
 *
 * The contrast presets each ship a measured ratio and are ordered on purpose.
 * A free colour field would quietly discard that - the first pastel anybody
 * likes lands near 2:1 - so the number this choice is producing is shown right
 * next to the wheel, live.
 *
 * It WARNS, it does not block. Somebody choosing a softer look on their own
 * screen for their own eyes is entitled to; the point is that they choose it
 * knowing, rather than discovering it at midnight on a long message. The repair
 * button is offered beside the warning rather than applied automatically -
 * silently changing the colour somebody just picked is worse than telling them
 * it is hard to read.
 *
 * The wheel is a conic gradient with a radial white wash: hue around,
 * saturation outward, which is the mapping every other picker has taught. No
 * canvas, no dependency, and it scales with device pixel ratio for free.
 */

import { useRef, useState } from "react";

import {
  AA_NORMAL,
  contrastRatio,
  hslToRgb,
  nudgeToRatio,
  parseHex,
  rgbToHsl,
  toHex,
  verdict,
  wheelToHs,
} from "@/lib/appearance/contrast";
import { useUiStore } from "@/lib/store/uiStore";

/** The bubble surface each preset actually paints, so the ratio is measured
 *  against what the ink will really sit on rather than against plain white. */
const PRESET_BG: Record<string, string> = {
  soft: "#EFF3F8",
  default: "#F4F7FB",
  high: "#FFFFFF",
};

const SIZE = 132;

export function InkPicker() {
  const ink = useUiStore((s) => s.msgInk);
  const setInk = useUiStore((s) => s.setMsgInk);
  const preset = useUiStore((s) => s.msgContrast);
  const wheelRef = useRef<HTMLDivElement>(null);
  // Hue/saturation live here rather than being read back out of the hex on
  // every render, because that round trip is lossy: at very low or very high
  // lightness many (h,s) pairs collapse to the same colour, and the wheel
  // marker would jump somewhere the person never clicked.
  //
  // SEEDED from the saved ink at mount, though. A fixed starting point meant
  // that re-opening the picker and nudging Lightness one step rebuilt the
  // colour from the default blue hue - a saved #b32d2d became #3a5978 from a
  // control labelled "Lightness", and with no marker on the wheel and a hex
  // field that could not be typed into, the original was unrecoverable.
  const [hsl, setHsl] = useState(() => {
    const parsed = ink ? parseHex(ink) : null;
    return parsed ? rgbToHsl(parsed) : { h: 210, s: 0.35, l: 0.3 };
  });
  // The hex field is typed into CHARACTER BY CHARACTER, so it cannot be
  // controlled by the committed value: "#", "#3", "#33" do not parse, setInk
  // was never called, and React restored the previous DOM value on every
  // keystroke - the field was uneditable except by pasting a complete colour.
  const [hexDraft, setHexDraft] = useState<string | null>(null);

  const background = parseHex(PRESET_BG[preset] ?? PRESET_BG.default)!;
  const current = ink ? parseHex(ink) : null;
  const ratio = current ? contrastRatio(current, background) : null;
  const grade = ratio == null ? null : verdict(ratio);

  const pick = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = wheelRef.current?.getBoundingClientRect();
    if (!box) return;
    const radius = box.width / 2;
    const { h, s } = wheelToHs(
      event.clientX - box.left - radius,
      event.clientY - box.top - radius,
      radius,
    );
    setHsl((prev) => ({ ...prev, h, s }));
    setInk(toHex(hslToRgb(h, s, hsl.l)));
  };

  return (
    <div className="space-y-2">
      <span className="settings-label">Message ink</span>
      <span className="settings-hint opacity-70">
        Overrides the text colour from the contrast preset. Leave it off to
        follow the preset.
      </span>

      <div className="flex items-start gap-3">
        <div
          ref={wheelRef}
          role="application"
          aria-label="Message ink colour wheel"
          onPointerDown={pick}
          onPointerMove={(e) => e.buttons === 1 && pick(e)}
          style={{
            width: SIZE,
            height: SIZE,
            borderRadius: "50%",
            cursor: "crosshair",
            touchAction: "none",
            border: "1px solid var(--color-es-hairline)",
            backgroundImage:
              "radial-gradient(circle closest-side, #fff, transparent), " +
              "conic-gradient(from 90deg, red, yellow, lime, aqua, blue, magenta, red)",
          }}
        />

        <div className="min-w-0 flex-1 space-y-2">
          <label className="block space-y-1">
            <span className="settings-value opacity-70">Lightness</span>
            <input
              type="range"
              min={0.1}
              max={0.9}
              step={0.01}
              value={hsl.l}
              onChange={(e) => {
                const l = Number(e.target.value);
                setHsl((prev) => ({ ...prev, l }));
                // Keeps the hue already chosen and moves only lightness -
                // which is the dial that actually fixes a failing ratio.
                setInk(toHex(hslToRgb(hsl.h, hsl.s, l)));
              }}
              className="w-full"
            />
          </label>

          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              style={{
                width: 18,
                height: 18,
                borderRadius: 5,
                border: "1px solid var(--color-es-hairline)",
                backgroundColor: ink ?? "transparent",
              }}
            />
            <input
              type="text"
              value={hexDraft ?? ink ?? ""}
              placeholder="follows preset"
              onChange={(e) => {
                const next = e.target.value;
                // The draft is what the field SHOWS; the store gets only
                // values that actually parse, so a half-typed colour never
                // repaints the app - and never blocks the next keystroke.
                setHexDraft(next);
                const parsed = parseHex(next);
                if (parsed) {
                  setInk(toHex(parsed));
                  setHsl(rgbToHsl(parsed));
                } else if (next.trim() === "") {
                  setInk(null);
                }
              }}
              onBlur={() => setHexDraft(null)}
              className="settings-text-input w-24"
              aria-label="Message ink hex value"
            />
          </div>

          {ratio != null && (
            <p
              className="settings-value"
              data-grade={grade ?? undefined}
              style={{ opacity: grade === "low" ? 1 : 0.7 }}
            >
              Contrast {ratio.toFixed(1)}:1{" "}
              {grade === "low"
                ? "- hard to read on this preset"
                : grade === "aaa"
                  ? "- excellent"
                  : "- fine"}
            </p>
          )}

          {grade === "low" && current && (
            <button
              type="button"
              className="settings-segment-button"
              onClick={() =>
                setInk(toHex(nudgeToRatio(current, background, AA_NORMAL)))
              }
            >
              Make it readable
            </button>
          )}

          {ink && (
            <button
              type="button"
              className="settings-segment-button"
              onClick={() => setInk(null)}
            >
              Follow preset
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
