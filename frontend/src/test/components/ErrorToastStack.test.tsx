import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/layout/AppShell";
import { Providers } from "@/app/providers";
import { ErrorToastStack } from "@/components/errors/ErrorToastStack";
import { useErrorStore } from "@/lib/errors";
import type { ApiError } from "@/lib/api/client";

const EXIT_ANIMATION_MS = 180;
const AUTO_DISMISS_MS = 4_500;

describe("ErrorToastStack", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("renders safe mapped errors from the error store", () => {
    const apiError: ApiError = {
      status: 422,
      detail: "api_key_invalid",
      message: "raw backend detail should not render",
    };

    useErrorStore.getState().pushError(apiError);
    render(<ErrorToastStack />);

    expect(
      screen.getByText("API key is invalid. Please check it and try again."),
    ).toBeInTheDocument();
  });

  it("does not display raw backend or provider detail", () => {
    const apiError: ApiError = {
      status: 502,
      detail: "openrouter_completion_error",
      message: "Raw upstream body: internal server error with provider payload",
    };

    useErrorStore.getState().pushError(apiError);
    render(<ErrorToastStack />);

    expect(
      screen.getByText("The provider returned an error. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/internal server error/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/provider payload/i)).not.toBeInTheDocument();
  });

  it("clicking a toast dismisses it", () => {
    useErrorStore
      .getState()
      .pushErrorDirect("custom_error", "Click dismisses this toast.");
    render(<ErrorToastStack />);

    fireEvent.click(screen.getByText("Click dismisses this toast."));
    act(() => {
      vi.advanceTimersByTime(EXIT_ANIMATION_MS);
    });

    expect(screen.queryByText("Click dismisses this toast.")).not.toBeInTheDocument();
  });

  it("keeps a toast up long enough to read, then removes it", () => {
    // The upper bound alone was a one-sided test. AUTO_DISMISS_MS above is a
    // COPY of the production constant, not an import of it, so cutting the
    // real one to 1200ms left this green: the test advanced 4680ms and found
    // the toast gone, which is exactly what it asked for. A reader who never
    // got to finish the sentence would have noticed; the suite would not.
    //
    // READING_FLOOR is not the production value restated. It is the weakest
    // claim worth making, that a sentence stays up for at least three seconds,
    // so this stays green if somebody deliberately tunes 4500 and red if the
    // toast starts flashing past.
    const READING_FLOOR = 3_000;

    useErrorStore
      .getState()
      .pushErrorDirect("custom_error", "Auto dismisses this toast.");
    render(<ErrorToastStack />);

    act(() => {
      vi.advanceTimersByTime(READING_FLOOR);
    });
    expect(
      screen.queryByText("Auto dismisses this toast."),
      "the toast went away before it could be read",
    ).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(AUTO_DISMISS_MS + EXIT_ANIMATION_MS - READING_FLOOR);
    });
    expect(screen.queryByText("Auto dismisses this toast.")).not.toBeInTheDocument();
  });

  it("waits for the tab to come back before it counts down", async () => {
    // K-25, and this test used to assert the opposite. The countdown was a
    // plain setTimeout with no listeners at all, so a toast raised as the
    // user alt-tabbed away was dismissed 4.5 seconds later and never seen -
    // counted as shown, and if the queue was full it took a waiting error
    // down with it.
    useErrorStore
      .getState()
      .pushErrorDirect("custom_error", "Raised while you were away.");
    render(<ErrorToastStack />);

    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_DISMISS_MS * 4);
    });

    expect(
      screen.queryByText("Raised while you were away."),
      "it expired while the tab was hidden",
    ).toBeInTheDocument();

    // And it does not live forever: coming back starts the clock again.
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "visible",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_DISMISS_MS + EXIT_ANIMATION_MS);
    });
    expect(
      screen.queryByText("Raised while you were away."),
    ).not.toBeInTheDocument();
  });

  it("does not hand back the whole window every time it resumes", async () => {
    // The trap in the naive version of this fix. A bare paused flag restarts
    // the full 4.5 seconds on every resume, so a toast in a window that keeps
    // losing focus never expires at all - a leak wearing a fix's clothes.
    // What is tracked is the REMAINING time.
    useErrorStore.getState().pushErrorDirect("custom_error", "Nearly done.");
    render(<ErrorToastStack />);

    // Most of the window passes while visible.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_DISMISS_MS - 200);
    });
    expect(screen.queryByText("Nearly done.")).toBeInTheDocument();

    // A brief hide and unhide must not buy another full window.
    act(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "visible",
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400 + EXIT_ANIMATION_MS);
    });
    expect(
      screen.queryByText("Nearly done."),
      "the resume handed back the whole window instead of what was left",
    ).not.toBeInTheDocument();
  });

  it("does not vanish from under the pointer", async () => {
    // Resting the pointer on a toast is what somebody does while reading it.
    useErrorStore.getState().pushErrorDirect("custom_error", "Read me.");
    render(<ErrorToastStack />);
    const toast = screen.getByText("Read me.");

    act(() => {
      fireEvent.mouseEnter(toast.closest('[role="status"]') as HTMLElement);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_DISMISS_MS * 3);
    });
    expect(screen.queryByText("Read me.")).toBeInTheDocument();

    act(() => {
      fireEvent.mouseLeave(toast.closest('[role="status"]') as HTMLElement);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_DISMISS_MS + EXIT_ANIMATION_MS);
    });
    expect(screen.queryByText("Read me.")).not.toBeInTheDocument();
  });

  it("shows at most 5 visible toasts and queues extras", () => {
    for (let i = 1; i <= 7; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Queued toast ${i}`);
    }

    render(<ErrorToastStack />);

    expect(screen.getAllByRole("status")).toHaveLength(5);
    expect(useErrorStore.getState().queuedErrors).toHaveLength(2);
    expect(screen.queryByText("Queued toast 6")).not.toBeInTheDocument();
    expect(screen.queryByText("Queued toast 7")).not.toBeInTheDocument();
  });

  it("shows the next queued toast after a visible toast exits", () => {
    for (let i = 1; i <= 6; i++) {
      useErrorStore.getState().pushErrorDirect(`code_${i}`, `Queued toast ${i}`);
    }

    render(<ErrorToastStack />);

    fireEvent.click(screen.getByText("Queued toast 1"));
    act(() => {
      vi.advanceTimersByTime(EXIT_ANIMATION_MS);
    });

    expect(screen.queryByText("Queued toast 1")).not.toBeInTheDocument();
    expect(screen.getByText("Queued toast 6")).toBeInTheDocument();
    expect(screen.getAllByRole("status")).toHaveLength(5);
    expect(useErrorStore.getState().queuedErrors).toHaveLength(0);
  });

  it("uses a polite live region and non-button toasts with a dismiss button", () => {
    useErrorStore
      .getState()
      .pushErrorDirect("a11y_code", "Accessible toast message.");
    render(<ErrorToastStack />);

    const stack = screen.getByTestId("error-toast-stack");
    expect(stack).toHaveAttribute("aria-live", "polite");

    const toast = screen.getByRole("status");
    expect(toast.tagName).not.toBe("BUTTON");
    expect(toast).toHaveTextContent("Accessible toast message.");

    // Dedicated dismiss control inside the toast
    const dismissBtn = screen.getByRole("button", { name: "Dismiss" });
    expect(toast.contains(dismissBtn)).toBe(true);
  });

  it("dismiss button removes the toast", () => {
    useErrorStore
      .getState()
      .pushErrorDirect("a11y_code", "Dismiss via button.");
    render(<ErrorToastStack />);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    act(() => {
      vi.advanceTimersByTime(EXIT_ANIMATION_MS);
    });

    expect(screen.queryByText("Dismiss via button.")).not.toBeInTheDocument();
  });

  it("mounts over the app shell without requiring backend data", () => {
    useErrorStore
      .getState()
      .pushErrorDirect("mount_error", "Mounted over the chat canvas.");

    render(
      <Providers>
        <AppShell />
      </Providers>,
    );

    const stack = screen.getByTestId("error-toast-stack");
    expect(stack).toBeInTheDocument();
    expect(within(stack).getByText("Mounted over the chat canvas.")).toBeInTheDocument();
    expect(stack.className).toContain("absolute");
    expect(stack.className).toContain("left-1/2");
  });
});
