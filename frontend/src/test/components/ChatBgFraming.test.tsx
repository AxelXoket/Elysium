/**
 * Framing the wallpaper from the settings panel.
 *
 * The picture used to be cover-fitted and centred with no way to say which
 * part mattered. These cover the controls that changed that, and the one
 * hazard the preview introduced on the way: it renders the SAME background
 * the chat does, from the same hook, and a viewer that can write is a viewer
 * that can turn your wallpaper off by being looked at.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatBgFramingPreview } from "@/components/settings/ChatBgFramingPreview";
import { useUiStore } from "@/lib/store/uiStore";

vi.mock("@/lib/store/chatBgDb", () => ({
  // No image in the store - the case that used to clear the flag.
  getChatBgBlob: vi.fn(async () => null),
  putChatBgBlob: vi.fn(async () => {}),
  deleteChatBgBlob: vi.fn(async () => {}),
}));

function box() {
  return screen.getByRole("group", { name: /Wallpaper framing/i });
}

/** jsdom gives everything a zero rect, and a drag divides by the box size. */
function sizeTheBox(node: HTMLElement, width = 400, height = 250) {
  vi.spyOn(node, "getBoundingClientRect").mockReturnValue({
    width, height, top: 0, left: 0, right: width, bottom: height,
    x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
}

beforeEach(() => {
  useUiStore.setState({
    chatBgOn: true,
    chatBgFocusX: 50,
    chatBgFocusY: 50,
    chatBgZoom: 1,
    chatAreaAspect: 1.6,
  });
});

describe("looking at the preview cannot change the setting", () => {
  it("leaves the background on when the image cannot be read", async () => {
    // THE HAZARD. The chat is entitled to notice a missing image and switch
    // the flag off; a preview is not, and it mounts the same hook. Without
    // the passive flag, opening Settings on a slow or busy IndexedDB turned
    // the user's wallpaper off while they were looking at the control for it.
    render(<ChatBgFramingPreview />);
    await screen.findByText("No picture yet");
    expect(useUiStore.getState().chatBgOn).toBe(true);
  });
});

describe("dragging chooses what you see", () => {
  it("shows more of the left when the picture is pulled right", async () => {
    // Grab-and-move, like every photo cropper. In background-position terms
    // that is a DECREASE, which is the sign that is easy to get backwards.
    const { container } = render(<ChatBgFramingPreview />);
    void container;
    const node = box();
    sizeTheBox(node);

    fireEvent.pointerDown(node, { clientX: 200, clientY: 125, pointerId: 1 });
    fireEvent.pointerMove(node, { clientX: 280, clientY: 125, pointerId: 1 });
    fireEvent.pointerUp(node, { pointerId: 1 });

    // 80px of 400 is a fifth of the box, so a fifth of the range.
    expect(useUiStore.getState().chatBgFocusX).toBeCloseTo(30, 5);
    expect(useUiStore.getState().chatBgFocusY).toBeCloseTo(50, 5);
  });

  it("keeps the focus inside the picture however far it is dragged", async () => {
    const node = (render(<ChatBgFramingPreview />), box());
    sizeTheBox(node);

    fireEvent.pointerDown(node, { clientX: 0, clientY: 0, pointerId: 1 });
    fireEvent.pointerMove(node, { clientX: 4000, clientY: 4000, pointerId: 1 });
    fireEvent.pointerUp(node, { pointerId: 1 });

    expect(useUiStore.getState().chatBgFocusX).toBe(0);
    expect(useUiStore.getState().chatBgFocusY).toBe(0);
  });

  it("does not move on a pointer that is only passing over", async () => {
    // No button held means no drag. Without the pointerdown gate the picture
    // would slide around under a pointer merely crossing the panel.
    const node = (render(<ChatBgFramingPreview />), box());
    sizeTheBox(node);

    fireEvent.pointerMove(node, { clientX: 300, clientY: 200, pointerId: 1 });

    expect(useUiStore.getState().chatBgFocusX).toBe(50);
  });

  it("ignores a drag while there is no image to frame", async () => {
    const node = (render(<ChatBgFramingPreview disabled />), box());
    sizeTheBox(node);

    fireEvent.pointerDown(node, { clientX: 200, clientY: 125, pointerId: 1 });
    fireEvent.pointerMove(node, { clientX: 100, clientY: 125, pointerId: 1 });

    expect(useUiStore.getState().chatBgFocusX).toBe(50);
  });
});

describe("the keyboard reaches the same control", () => {
  it("moves the view the way the arrow points", async () => {
    // Opposite sign to the drag, and deliberately so: dragging holds the
    // PICTURE, arrows push the WINDOW over it. Pressing Right should show
    // what is further right, which is an increase.
    const user = userEvent.setup();
    render(<ChatBgFramingPreview />);
    box().focus();

    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowDown}");

    expect(useUiStore.getState().chatBgFocusX).toBe(54);
    expect(useUiStore.getState().chatBgFocusY).toBe(52);
  });

  it("says where the frame is, for a reader that cannot see it", async () => {
    useUiStore.setState({ chatBgFocusX: 25, chatBgFocusY: 75 });
    render(<ChatBgFramingPreview />);
    expect(box()).toHaveAccessibleName(
      /25 percent across and 75 percent down/i,
    );
  });
});

describe("the frame is drawn in the shape of the chat", () => {
  it("takes its ratio from the live chat area", () => {
    // A square preview would be a lie: `cover` crops against the shape it
    // fills, so a crop chosen in a square lands somewhere else on a wide
    // chat area. Same ratio, same crop.
    useUiStore.setState({ chatAreaAspect: 2.2 });
    render(<ChatBgFramingPreview />);
    expect(box()).toHaveStyle({ aspectRatio: "2.2" });
  });

  it("still draws a frame before anything has been measured", () => {
    // First paint, or a window that has not settled. A control that vanishes
    // until a measurement lands reads as a broken panel.
    useUiStore.setState({ chatAreaAspect: null });
    render(<ChatBgFramingPreview />);
    expect(box()).toBeInTheDocument();
  });
});
