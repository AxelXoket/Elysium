/**
 * The switch that lets a model answer with a picture.
 *
 * Two things matter here and neither is cosmetic. It must be OFF unless the
 * vault says otherwise, because turning it on changes what leaves this machine.
 * And it must stay usable when the model open right now cannot draw: it is a
 * standing preference, not a property of the current selection, and greying it
 * out would make it mean something different from what it says.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImageOutputSetting } from "@/components/generation/ImageOutputSetting";

describe("ImageOutputSetting", () => {
  it("reads off, and says what off means", () => {
    render(
      <ImageOutputSetting enabled={false} supported onChange={() => {}} />,
    );
    const toggle = screen.getByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/replies are text only/i)).toBeInTheDocument();
  });

  it("says where the pictures go and that they are not sent back", () => {
    render(<ImageOutputSetting enabled supported onChange={() => {}} />);
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
      <ImageOutputSetting enabled={false} supported onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("asks to be turned off again", async () => {
    const onChange = vi.fn();
    render(<ImageOutputSetting enabled supported onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("stays on for a model that cannot draw, and says so", () => {
    render(
      <ImageOutputSetting enabled supported={false} onChange={() => {}} />,
    );
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch")).not.toBeDisabled();
    expect(
      screen.getByText(/does not list image output/i),
    ).toBeInTheDocument();
  });

  it("says nothing about the model when it is off", () => {
    render(
      <ImageOutputSetting enabled={false} supported={false} onChange={() => {}} />,
    );
    expect(
      screen.queryByText(/does not list image output/i),
    ).not.toBeInTheDocument();
  });

  it("cannot be pressed twice while the save is in flight", async () => {
    const onChange = vi.fn();
    render(
      <ImageOutputSetting enabled={false} supported busy onChange={onChange} />,
    );
    const toggle = screen.getByRole("switch");
    expect(toggle).toBeDisabled();
    await userEvent.click(toggle);
    expect(onChange).not.toHaveBeenCalled();
  });
});
