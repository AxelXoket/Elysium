/**
 * The switch that lets a model answer with a picture.
 *
 * Three things matter here and none is cosmetic. It must be OFF unless the
 * vault says otherwise, because turning it on changes what leaves this machine.
 * It must stay usable when the model open right now cannot draw: it is a
 * standing preference, not a property of the current selection, and greying it
 * out would make it mean something different from what it says. And the note
 * underneath must not overstate what the catalogue knows - "no output modality
 * list" is not the same fact as "a list without image in it", the backend asks
 * for a picture in the first case and not the second, and the panel used to
 * print the second sentence for both.
 *
 * The two sentences are imported rather than matched by substring. The old
 * `/does not list image output/i` regex would have matched a replacement copy
 * that said the opposite thing about the same subject.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  IMAGE_OUTPUT_NOT_ASKED_NOTE,
  IMAGE_OUTPUT_UNKNOWN_NOTE,
  ImageOutputSetting,
} from "@/components/generation/ImageOutputSetting";

describe("ImageOutputSetting", () => {
  it("reads off, and says what off means", () => {
    render(
      <ImageOutputSetting enabled={false} supported="yes" onChange={() => {}} />,
    );
    const toggle = screen.getByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/replies are text only/i)).toBeInTheDocument();
  });

  it("says where the pictures go and that they are not sent back", () => {
    render(<ImageOutputSetting enabled supported="yes" onChange={() => {}} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByText(/encrypted vault/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/never sent back to the model/i),
    ).toBeInTheDocument();
  });

  it("asks to be turned on", async () => {
    const onChange = vi.fn();
    render(
      <ImageOutputSetting enabled={false} supported="yes" onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("asks to be turned off again", async () => {
    const onChange = vi.fn();
    render(<ImageOutputSetting enabled supported="yes" onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("stays on for a model that cannot draw, and says so", () => {
    render(
      <ImageOutputSetting enabled supported="no" onChange={() => {}} />,
    );
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch")).not.toBeDisabled();
    expect(
      screen.getByText(IMAGE_OUTPUT_NOT_ASKED_NOTE),
    ).toBeInTheDocument();
  });

  it("does not claim a picture will not be asked for when nothing is known", () => {
    // The defect this unit closes, at the smallest surface that has it. The
    // backend asks for a picture in exactly this state.
    render(
      <ImageOutputSetting enabled supported="unknown" onChange={() => {}} />,
    );
    expect(
      screen.queryByText(IMAGE_OUTPUT_NOT_ASKED_NOTE),
      "the panel made a false negative claim about the request",
    ).toBeNull();
  });

  it("says what it does know instead", () => {
    // Positive control for the absence above: same render, same props, and
    // this one proves the note area is reached and produces copy.
    render(
      <ImageOutputSetting enabled supported="unknown" onChange={() => {}} />,
    );
    expect(screen.getByText(IMAGE_OUTPUT_UNKNOWN_NOTE)).toBeInTheDocument();
  });

  it("says neither note when the model declares image output", () => {
    render(<ImageOutputSetting enabled supported="yes" onChange={() => {}} />);
    expect(screen.queryByText(IMAGE_OUTPUT_NOT_ASKED_NOTE)).toBeNull();
    expect(screen.queryByText(IMAGE_OUTPUT_UNKNOWN_NOTE)).toBeNull();
  });

  it("says nothing about the model when it is off", () => {
    render(
      <ImageOutputSetting enabled={false} supported="no" onChange={() => {}} />,
    );
    expect(
      screen.queryByText(IMAGE_OUTPUT_NOT_ASKED_NOTE),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(IMAGE_OUTPUT_UNKNOWN_NOTE),
    ).not.toBeInTheDocument();
  });

  it("cannot be pressed twice while the save is in flight", async () => {
    const onChange = vi.fn();
    render(
      <ImageOutputSetting
        enabled={false}
        supported="yes"
        busy
        onChange={onChange}
      />,
    );
    const toggle = screen.getByRole("switch");
    expect(toggle).toBeDisabled();
    await userEvent.click(toggle);
    expect(onChange).not.toHaveBeenCalled();
  });
});
