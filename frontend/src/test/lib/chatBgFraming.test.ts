/**
 * Choosing WHICH PART of the wallpaper you see.
 *
 * Until now the picture was cover-fitted and centred, full stop - the file's
 * own header called it "v1: plain cover-fit, no focal cropper". A portrait
 * photo in a landscape window lost its top and bottom with nobody able to say
 * which half mattered.
 *
 * The fix is deliberately percentages and not a stored pixel rectangle. A
 * rectangle is measured against one window size and is wrong at every other,
 * so it would have to be recomputed on each resize; `background-position: 40%
 * 25%` holds the same point of the picture over the same point of the window
 * at every size, for free, in the browser's own layout pass.
 */
import { describe, expect, it } from "vitest";

import {
  bgSizeFor,
  buildBgLayers,
  CHAT_BG_FRAMING_DEFAULT,
  CHAT_BG_ZOOM_MAX,
  clampFraming,
} from "@/lib/appearance/chatBackground";

const URL_ = "blob:http://localhost/abc";
const TINT = "#161A1D";

describe("an untouched background is left exactly as it was", () => {
  it("emits the same declarations it did before framing existed", () => {
    // THE ZERO-CHANGE CONTRACT. Everyone who already has a wallpaper is on the
    // defaults, so if this drifts, their chat visibly re-frames itself on
    // update for a feature they never touched.
    const layers = buildBgLayers(URL_, TINT, 0.35);
    expect(layers).not.toBeNull();
    expect(layers!.backgroundSize).toBe("100% 100%, cover");
    expect(layers!.backgroundPosition).toBe("0 0, center");
  });

  it("says `center`, not `50% 50%`", () => {
    // Equivalent to a browser, not to a diff. Spelling it differently would
    // make every future review of this file ask whether the pixels moved.
    const layers = buildBgLayers(URL_, TINT, 0.35, CHAT_BG_FRAMING_DEFAULT, 1.5, 1.8);
    expect(layers!.backgroundPosition).toBe("0 0, center");
  });
});

describe("the focal point survives a resize", () => {
  it("is expressed in percent, so the browser re-fits it at every size", () => {
    const layers = buildBgLayers(URL_, TINT, 0.35, { focusX: 20, focusY: 80, zoom: 1 });
    expect(layers!.backgroundPosition).toBe("0 0, 20% 80%");
  });

  it("keeps cover at zoom 1 whatever the focus", () => {
    // Moving the picture must not also rescale it - one control, one effect.
    const layers = buildBgLayers(URL_, TINT, 0.35, { focusX: 0, focusY: 100, zoom: 1 });
    expect(layers!.backgroundSize).toBe("100% 100%, cover");
  });
});

describe("zoom hangs off whichever axis cover was already filling", () => {
  it("returns a height-scaled background-size when the picture is wider than the area", () => {
    // A 2:1 photo in a 4:3 window: cover was matching WIDTH and spilling
    // height, so height is what has to grow. Scaling width instead would
    // leave a gap down the sides.
    expect(bgSizeFor(1.5, 2.0, 4 / 3)).toBe("auto 150%");
  });

  it("returns a width-scaled background-size when the picture is taller than the area", () => {
    expect(bgSizeFor(1.5, 0.6, 4 / 3)).toBe("150% auto");
  });

  it("never emits two explicit lengths, which would squash the picture", () => {
    for (const [img, area] of [[2.0, 1.33], [0.6, 1.33], [1.0, 1.0]] as const) {
      const size = bgSizeFor(2, img, area);
      expect(size.split(" ").filter((p) => p !== "auto")).toHaveLength(1);
    }
  });
});

describe("an unmeasured chat area falls back instead of guessing", () => {
  it("uses cover when the area has not been measured yet", () => {
    // First paint, a test environment with no layout, the moment after a
    // window drag. Guessing an aspect here would visibly skew the picture;
    // `cover` is merely un-zoomed, which nobody can see going wrong.
    expect(bgSizeFor(2, 1.5, null)).toBe("cover");
    expect(bgSizeFor(2, null, 1.5)).toBe("cover");
  });

  it("uses cover for a degenerate aspect rather than dividing by it", () => {
    expect(bgSizeFor(2, 0, 1.5)).toBe("cover");
    expect(bgSizeFor(2, 1.5, Number.NaN)).toBe("cover");
  });
});

describe("stored framing is clamped on the way in", () => {
  it("holds the focus inside the picture", () => {
    expect(clampFraming({ focusX: -40, focusY: 999, zoom: 1 })).toMatchObject({
      focusX: 0,
      focusY: 100,
    });
  });

  it("refuses a zoom that would shrink the picture below cover", () => {
    // Under 1 the picture would no longer fill the chat area and the canvas
    // would show through at the edges - a broken-looking wallpaper, not a
    // creative choice.
    expect(clampFraming({ zoom: 0.2 }).zoom).toBe(1);
    expect(clampFraming({ zoom: 99 }).zoom).toBe(CHAT_BG_ZOOM_MAX);

    // The line above is self-referential: it imports the ceiling and then
    // checks the clamp lands on the ceiling, so raising CHAT_BG_ZOOM_MAX
    // moves BOTH sides and nothing fails. Measured in KADEME 19a - changing
    // it from 3 to 5 kept all 128 tests in this stage green. It proves that
    // clamping happens, not what it clamps to, so the value needs its own pin.
    expect(CHAT_BG_ZOOM_MAX, "the zoom ceiling moved").toBe(3);
  });

  it("falls back to centred rather than throwing on junk", () => {
    // This is read from persisted device storage, which a person can edit and
    // a bad write can corrupt. A wallpaper is not worth a white screen.
    const junk = { focusX: "left", focusY: undefined, zoom: null } as never;
    expect(clampFraming(junk)).toEqual(CHAT_BG_FRAMING_DEFAULT);
  });
});
