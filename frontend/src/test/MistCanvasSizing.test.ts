/**
 * MistCanvasSizing.test.ts - the fog buffer has to match the box it is shown in.
 *
 * The defect: a `<canvas>` has no `object-fit`, so the default `fill` scales
 * its bitmap's two axes INDEPENDENTLY to cover the element. Any drift between
 * the drawing buffer's aspect and the CSS box's aspect therefore shows up as
 * an anisotropic stretch - and because the shader adds its wind in the same
 * space it draws the noise in, the horizontal DRIFT SPEED is distorted by the
 * identical factor. That is why the symptom reads as "the background's motion,
 * extremely compressed" rather than simply as a different-looking fog.
 *
 * These are arithmetic, deliberately. jsdom has no WebGL and no layout, so the
 * component itself cannot be rendered into a measurable state; the sizing rule
 * was pulled out into a pure function precisely so the part that was wrong can
 * be pinned by input and output.
 */
import { describe, it, expect } from "vitest";
import { bufferSizeFor } from "@/components/backdrop/mistBuffer";

/** Real geometry at a 1600x900 window, from the panel width tokens. */
const WINDOW = { w: 1600, h: 900 };
const SIDEBAR = { w: 319, h: 855 };
const RIGHT_PANEL = { w: 385, h: 855 };
const PAGE_MAX_EDGE = 480;
const PANEL_MAX_EDGE = 960;

const aspect = ({ width, height }: { width: number; height: number }) =>
  width / height;

describe("fog buffer sizing", () => {
  it("refuses to size a collapsed panel", () => {
    // THE BUG. A collapsed panel is a real zero-width box, and its collapsed
    // state is persisted - so this is what happens on every launch with a
    // panel shut, not some exotic path. The old code answered this case with
    // `|| window.innerWidth`.
    expect(bufferSizeFor(0, SIDEBAR.h, PANEL_MAX_EDGE)).toBeNull();
    expect(bufferSizeFor(SIDEBAR.w, 0, PANEL_MAX_EDGE)).toBeNull();
    expect(bufferSizeFor(0, 0, PANEL_MAX_EDGE)).toBeNull();
  });

  it("never returns the window's size for a panel", () => {
    // The specific wrong answer, named. 1600 wide squashed into a 319px panel
    // is the ~3x horizontal compression the owner reported.
    const answer = bufferSizeFor(0, SIDEBAR.h, PANEL_MAX_EDGE);
    expect(answer).not.toEqual({ width: 960, height: 513 });
    expect(answer?.width).not.toBe(WINDOW.w);
  });

  it("keeps the element's aspect for every instance", () => {
    // GROUND CONTROL for the whole file: if the buffer tracks the box's
    // aspect, `object-fit: fill` has nothing to distort. Checked across the
    // three real shapes AND both caps, since the page instance uses a
    // different one.
    const cases = [
      { box: WINDOW, cap: PAGE_MAX_EDGE },
      { box: SIDEBAR, cap: PANEL_MAX_EDGE },
      { box: RIGHT_PANEL, cap: PANEL_MAX_EDGE },
      { box: { w: 836, h: 855 }, cap: PANEL_MAX_EDGE }, // chat canvas
      { box: { w: 1156, h: 855 }, cap: PANEL_MAX_EDGE }, // chat, sidebar shut
    ];
    for (const { box, cap } of cases) {
      const buf = bufferSizeFor(box.w, box.h, cap)!;
      expect(buf, `${box.w}x${box.h} produced nothing`).not.toBeNull();
      const drift = Math.abs(aspect(buf) / (box.w / box.h) - 1);
      // Rounding to whole pixels is the only permitted source of error.
      expect(drift, `${box.w}x${box.h} -> ${buf.width}x${buf.height}`).toBeLessThan(
        0.005,
      );
    }
  });

  it("caps the long edge without skewing the short one", () => {
    // POSITIVE CONTROL for the test above: the cap is real and does fire, so
    // "aspect preserved" is not passing merely because nothing was scaled.
    const capped = bufferSizeFor(WINDOW.w, WINDOW.h, PAGE_MAX_EDGE)!;
    expect(Math.max(capped.width, capped.height)).toBe(PAGE_MAX_EDGE);
    expect(capped).toEqual({ width: 480, height: 270 });

    const uncapped = bufferSizeFor(SIDEBAR.w, SIDEBAR.h, PANEL_MAX_EDGE)!;
    expect(uncapped).toEqual({ width: SIDEBAR.w, height: SIDEBAR.h });
  });

  it("never returns a zero dimension for a sliver", () => {
    // Mid-transition a panel passes through single-digit widths. A zero here
    // would be an invalid drawing buffer, not a small one.
    const sliver = bufferSizeFor(1, 855, PANEL_MAX_EDGE)!;
    expect(sliver.width).toBeGreaterThanOrEqual(1);
    expect(sliver.height).toBeGreaterThanOrEqual(1);
  });

  it("leaves nothing for object-fit to squash, before or after a collapse", () => {
    // The user-visible contract, written as the number that was wrong.
    // `fill` scales the two axes independently, so the visible distortion is
    // exactly (cssW/bufW) / (cssH/bufH). Anything but 1 is a stretch.
    const distortion = (
      box: { w: number; h: number },
      buf: { width: number; height: number },
    ) => box.w / buf.width / (box.h / buf.height);

    // What the old fallback produced: a collapsed panel measured 0, took the
    // window, and the panel then displayed a 960x513 bitmap in a 319x855 box.
    expect(distortion(SIDEBAR, { width: 960, height: 513 })).toBeCloseTo(
      0.199,
      2,
    );

    // What it produces now, across a full collapse-and-expand: collapsed
    // returns null so the buffer is untouched, and the first non-zero width
    // re-sizes it to match.
    expect(bufferSizeFor(0, SIDEBAR.h, PANEL_MAX_EDGE)).toBeNull();
    for (const w of [1, 40, 160, 280, SIDEBAR.w]) {
      const buf = bufferSizeFor(w, SIDEBAR.h, PANEL_MAX_EDGE)!;
      expect(
        distortion({ w, h: SIDEBAR.h }, buf),
        `${w}px into ${buf.width}x${buf.height}`,
      ).toBeCloseTo(1, 1);
    }
  });
});
