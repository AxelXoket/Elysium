/**
 * A bubble you can see through, with words you can still read.
 *
 * The whole reason this is a colour function and not `opacity` is that
 * `opacity` fades an element AND its contents. These pin the two properties
 * that follow from that: the default is byte-identical to what shipped
 * before, and the thinning only ever touches the fill.
 */
import { describe, expect, it } from "vitest";

import { bubbleSurface } from "@/lib/appearance/bubbleSurface";
import {
  MSG_OPACITY_DEFAULT,
  MSG_OPACITY_MAX,
  MSG_OPACITY_MIN,
} from "@/lib/store/uiStore";

const FILL = "var(--msg-user-bg, var(--color-es-user-bubble))";

describe("a bubble nobody has changed keeps the declaration it had", () => {
  it("returns the fill untouched at full opacity", () => {
    // THE ZERO-CHANGE CONTRACT, exact rather than equivalent: not
    // `color-mix(... 100%, transparent)`, which a browser would paint the
    // same but which makes today's look depend on a colour function.
    expect(bubbleSurface(FILL, 1)).toBe(FILL);
  });

  it("keeps the readable floor a floor, and full opacity the ceiling", () => {
    // The bounds had no test of any kind: dropping MSG_OPACITY_MIN from 0.35
    // to 0.15 left every test in the visual stage green. The floor is not
    // decorative - uiStore's own comment says a bubble you cannot find is
    // not a setting anyone wants, and below roughly a third the text sits on
    // bare wallpaper.
    expect(MSG_OPACITY_MIN, "the readable floor moved").toBe(0.35);
    expect(MSG_OPACITY_MAX, "full opacity is no longer the ceiling").toBe(1);
    expect(MSG_OPACITY_DEFAULT, "the untouched look changed").toBe(1);

    // And the floor is genuinely the lowest thing that renders differently:
    // at the floor the fill is mixed, at the ceiling it is returned whole.
    expect(bubbleSurface(FILL, MSG_OPACITY_MAX)).toBe(FILL);
    expect(bubbleSurface(FILL, MSG_OPACITY_MIN)).not.toBe(FILL);
  });

  it("returns the fill untouched for a value that is not a number", () => {
    // Read from persisted device storage, so a corrupt write must land on
    // "looks like it always did" rather than on an invisible bubble.
    expect(bubbleSurface(FILL, Number.NaN)).toBe(FILL);
    expect(bubbleSurface(FILL, undefined as never)).toBe(FILL);
  });
});

describe("thinning the fill leaves everything else alone", () => {
  it("mixes the fill towards transparent, keeping the variable intact", () => {
    // The variable is passed THROUGH rather than resolved: the actual colour
    // depends on the contrast preset, the custom ink and the dark-wallpaper
    // chrome, and re-deriving that here would be a second copy of the cascade.
    expect(bubbleSurface(FILL, 0.6)).toBe(
      `color-mix(in srgb, ${FILL} 60%, transparent)`,
    );
  });

  it("emits a whole-number percentage", () => {
    // Slider steps land on values like 0.6500000000000001; a CSS declaration
    // that long is a diffing and debugging nuisance for no visible gain.
    expect(bubbleSurface(FILL, 0.65000000001)).toBe(
      `color-mix(in srgb, ${FILL} 65%, transparent)`,
    );
  });

  it("clamps rather than emitting a percentage the browser would drop", () => {
    // An out-of-range percentage invalidates the whole declaration, and an
    // invalid background-color is a TRANSPARENT bubble - the failure would
    // look like the feature working, at maximum strength.
    expect(bubbleSurface(FILL, -3)).toContain(" 0%,");
    expect(bubbleSurface(FILL, 0.2)).toContain(" 20%,");
  });
});
