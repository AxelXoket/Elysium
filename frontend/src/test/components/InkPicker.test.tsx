/**
 * V11 - the ink picker.
 *
 * The behaviour worth pinning is the honesty of it. The contrast presets ship
 * measured ratios; a colour field that let somebody land on 2:1 without saying
 * so would quietly undo that. So: the ratio is shown, a failing choice is
 * named, the repair is OFFERED rather than applied, and the ratio is measured
 * against the surface the ink will actually sit on - not against plain white.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { InkPicker } from "@/components/settings/InkPicker";
import { useUiStore } from "@/lib/store/uiStore";
import { contrastRatio, parseHex, verdict } from "@/lib/appearance/contrast";

describe("InkPicker", () => {
  beforeEach(() => {
    useUiStore.setState({ msgInk: null, msgContrast: "default" });
  });

  it("shows no ratio until a colour is actually chosen", () => {
    render(<InkPicker />);
    expect(screen.queryByText(/Contrast/)).toBeNull();
  });

  it("names a failing choice instead of just showing a number", () => {
    useUiStore.setState({ msgInk: "#B9D4F0" });
    render(<InkPicker />);
    expect(screen.getByText(/hard to read/)).toBeTruthy();
  });

  it("offers the repair rather than applying it", () => {
    // Silently changing the colour somebody just picked is worse than telling
    // them it is hard to read.
    useUiStore.setState({ msgInk: "#B9D4F0" });
    render(<InkPicker />);
    expect(useUiStore.getState().msgInk).toBe("#B9D4F0");

    fireEvent.click(screen.getByText("Make it readable"));
    const fixed = parseHex(useUiStore.getState().msgInk!)!;
    expect(verdict(contrastRatio(fixed, parseHex("#F4F7FB")!))).not.toBe("low");
    // The store value was the trigger; this is the consequence. Both the
    // warning and the repair button are gated on the grade still being low,
    // so a repair that fixed the number while leaving the alarm on screen
    // would have passed everything above.
    expect(screen.queryByText(/hard to read/)).toBeNull();
    expect(screen.queryByText("Make it readable")).toBeNull();
  });

  it("scores the same ink lower on the tinted surface than on white", () => {
    // The high preset paints a whiter bubble, so the same ink scores
    // differently - measuring against a fixed white would report a ratio the
    // person never actually sees.
    //
    // KADEME 19a renamed this. It used to be called "measures against the
    // preset's own surface, not plain white", which is a claim about the
    // PICKER; the body never renders the picker and reads two hard-coded
    // hexes. What it really shows is that the arithmetic separates the two
    // surfaces at all - the reason the picker's choice of surface matters.
    // Which surface the picker actually passes is still not pinned here.
    const ink = parseHex("#6A7C90")!;
    const onDefault = contrastRatio(ink, parseHex("#F4F7FB")!);
    const onHigh = contrastRatio(ink, parseHex("#FFFFFF")!);
    expect(onHigh).toBeGreaterThan(onDefault);
  });

  it("hands control back to the preset", () => {
    useUiStore.setState({ msgInk: "#123456" });
    render(<InkPicker />);
    fireEvent.click(screen.getByText("Follow preset"));
    expect(useUiStore.getState().msgInk).toBeNull();
    // With no custom ink there is nothing to hand back, so the button that
    // does the handing back has to go. Asserting only the store left the
    // gate around it unproven.
    expect(screen.queryByText("Follow preset")).toBeNull();
  });

  it("ignores a half-typed hex instead of writing rubbish to the store", () => {
    render(<InkPicker />);
    const field = screen.getByLabelText("Message ink hex value");
    fireEvent.change(field, { target: { value: "#12" } });
    expect(useUiStore.getState().msgInk).toBeNull();
    fireEvent.change(field, { target: { value: "#123456" } });
    expect(useUiStore.getState().msgInk).toBe("#123456");
  });
});

// ── Audit: two controls that could not do what they were labelled ─────────

describe("InkPicker - reopening a saved colour", () => {
  beforeEach(() => {
    useUiStore.setState({ msgInk: null, msgContrast: "default" });
  });

  it("the Lightness slider keeps the SAVED hue, not the default one", () => {
    // Hue/saturation lived in component state initialised to a fixed
    // {h:210, s:0.35} and were never seeded from the persisted colour, so a
    // fresh mount rebuilt the ink from the default blue: a saved red became a
    // blue-grey from a control labelled "Lightness".
    useUiStore.setState({ msgInk: "#b32d2d" });
    render(<InkPicker />);

    const slider = screen.getByLabelText(/Lightness/i);
    fireEvent.change(slider, { target: { value: "0.35" } });

    const next = parseHex(useUiStore.getState().msgInk!)!;
    expect(next.r).toBeGreaterThan(next.b);
    expect(next.r).toBeGreaterThan(next.g);
  });

  it("a greyscale ink does not throw the hue somewhere arbitrary", () => {
    useUiStore.setState({ msgInk: "#808080" });
    render(<InkPicker />);
    fireEvent.change(screen.getByLabelText(/Lightness/i), {
      target: { value: "0.4" },
    });
    const next = parseHex(useUiStore.getState().msgInk!)!;
    expect(Math.abs(next.r - next.g)).toBeLessThan(2);
    expect(Math.abs(next.g - next.b)).toBeLessThan(2);
  });
});

describe("InkPicker - the hex field", () => {
  beforeEach(() => {
    useUiStore.setState({ msgInk: null, msgContrast: "default" });
  });

  it("can be typed into one character at a time", () => {
    // It was controlled by the committed value and only committed on a
    // complete parse, so "#", "#3", "#33" left state unchanged and React
    // restored the previous DOM value on every keystroke: the documented way
    // to enter an exact colour was a dead control.
    render(<InkPicker />);
    const field = screen.getByLabelText("Message ink hex value");

    for (const partial of ["#", "#3", "#33", "#336", "#3366", "#3366c", "#3366cc"]) {
      fireEvent.change(field, { target: { value: partial } });
      expect(field).toHaveValue(partial);
    }
    expect(useUiStore.getState().msgInk?.toUpperCase()).toBe("#3366CC");
  });

  it("a half-typed value never repaints the app", () => {
    useUiStore.setState({ msgInk: "#B32D2D" });
    render(<InkPicker />);
    const field = screen.getByLabelText("Message ink hex value");

    fireEvent.change(field, { target: { value: "#33" } });
    expect(field).toHaveValue("#33");
    expect(useUiStore.getState().msgInk).toBe("#B32D2D");
  });

  it("clearing the field returns the ink to the preset", () => {
    useUiStore.setState({ msgInk: "#B32D2D" });
    render(<InkPicker />);
    fireEvent.change(screen.getByLabelText("Message ink hex value"), {
      target: { value: "" },
    });
    expect(useUiStore.getState().msgInk).toBeNull();
  });

  it("leaving the field drops the draft and shows the committed value", () => {
    useUiStore.setState({ msgInk: "#B32D2D" });
    render(<InkPicker />);
    const field = screen.getByLabelText("Message ink hex value");
    fireEvent.change(field, { target: { value: "#zz" } });
    fireEvent.blur(field);
    expect(field).toHaveValue("#B32D2D");
  });
});
