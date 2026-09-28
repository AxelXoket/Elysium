/**
 * ErrorToastFocusPause.test.tsx - the toast countdown and the keyboard.
 *
 * The countdown already paused for two things: the tab going hidden, and the
 * pointer resting on the toast. Both are covered next door in
 * ErrorToastStack.test.tsx and neither is touched here. The gap was the
 * third: reaching the dismiss button by tab is the pointer-free way to rest
 * on a toast, and it counted for nothing - so the one person who cannot
 * hover was the one person the toast could expire underneath.
 *
 * Real DOM focus, not a class name and not a synthetic hover: `.focus()`
 * moves `document.activeElement` and fires the focusin React listens to, so
 * what is asserted is the same event path a tab press produces.
 *
 * No window is retyped. MAX_DISMISS_MS and EXIT_ANIMATION_MS come from the
 * component, and the one test that needs an exact window works on a message
 * long enough to hit the cap and PROVES it is at the cap before relying on
 * it. A hand-copied 4500 would keep this file green long after the reading
 * allowance had moved.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ErrorToastStack,
  EXIT_ANIMATION_MS,
  MAX_DISMISS_MS,
} from "@/components/errors/ErrorToastStack";
import { useErrorStore } from "@/lib/errors";

const SHORT = "Read this before it goes.";

/** Any toast, whatever its message costs to read, must be gone by here. */
const PAST_ANY_WINDOW = MAX_DISMISS_MS + EXIT_ANIMATION_MS;

/** Long enough that the reading allowance is capped, so this message's window
 *  is exactly MAX_DISMISS_MS. The test that uses it does not take that on
 *  trust - it asserts the toast is still up just short of the cap and gone
 *  just past it, which is only true of a message sitting on the ceiling. */
const AT_THE_CAP =
  "The request could not be completed and nothing was saved. "
    .repeat(12)
    .trim();

function toastOf(text: string): HTMLElement {
  return screen.getByText(text).closest('[role="status"]') as HTMLElement;
}

function dismissButtonIn(toast: HTMLElement): HTMLElement {
  return toast.querySelector("button") as HTMLElement;
}

describe("a toast somebody is holding with the keyboard", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    // Unmount BEFORE the body is wiped. The stack portals into the body, so
    // emptying it first pulls the portal's node out from under React and its
    // own cleanup then fails on a node that is no longer anybody's child.
    cleanup();
    document.body.innerHTML = "";
  });

  it("closes on its own when nobody is holding it", async () => {
    // GROUND CONTROL. Everything below asserts a toast STAYING, and an
    // assertion like that passes just as well on a component that never
    // dismisses anything at all. This is the proof that the countdown still
    // runs, so the ones that follow are measuring a pause rather than a
    // permanently frozen timer.
    useErrorStore.getState().pushErrorDirect("custom_error", SHORT);
    render(<ErrorToastStack />);

    expect(screen.queryByText(SHORT)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAST_ANY_WINDOW);
    });

    expect(screen.queryByText(SHORT)).not.toBeInTheDocument();
  });

  it("does not expire while the keyboard is inside it", async () => {
    // RED BEFORE THE FIX: focus paused nothing, so this toast was gone.
    useErrorStore.getState().pushErrorDirect("custom_error", SHORT);
    render(<ErrorToastStack />);

    const button = dismissButtonIn(toastOf(SHORT));
    act(() => {
      button.focus();
    });

    // Without this the test could pass on a focus that never landed, which is
    // the same green a component with no countdown at all would produce.
    expect(document.activeElement).toBe(button);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAST_ANY_WINDOW * 2);
    });

    expect(
      screen.queryByText(SHORT),
      "it expired under a keyboard user who was reading it",
    ).toBeInTheDocument();
  });

  it("gives back what was left once focus leaves, not a fresh window", async () => {
    // The pause must not be permanent, and it must not be generous either. A
    // toast that is focused once and never closes is the leak this mechanism
    // was built to avoid; a toast that comes back with a whole new window is
    // the same leak in instalments.
    useErrorStore.getState().pushErrorDirect("custom_error", AT_THE_CAP);
    render(<ErrorToastStack />);

    // Most of the window burns before focus arrives, so what is owed
    // afterwards is a remainder rather than the whole thing. Surviving this
    // far is also what pins the message to the cap: a shorter window would
    // already have closed it.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MAX_DISMISS_MS - 200);
    });
    const button = dismissButtonIn(toastOf(AT_THE_CAP));
    act(() => {
      button.focus();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAST_ANY_WINDOW * 2);
    });
    expect(screen.queryByText(AT_THE_CAP)).toBeInTheDocument();

    act(() => {
      button.blur();
    });
    expect(document.activeElement).not.toBe(button);

    // Only the 200ms remainder is owed. A fresh window here would keep it on
    // screen, and a window longer than the cap would too.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200 + EXIT_ANIMATION_MS);
    });
    expect(
      screen.queryByText(AT_THE_CAP),
      "the resume handed back a whole new window instead of what was left",
    ).not.toBeInTheDocument();
  });

  it("treats a step between two controls inside the toast as staying", async () => {
    // focusout fires BEFORE the matching focusin, so a tab step from one
    // control in the toast to another looks momentarily like a departure. If
    // that gap resumes the countdown, moving around inside a toast costs the
    // reader time they never left to spend. The guard is relatedTarget.
    useErrorStore.getState().pushErrorDirect("custom_error", SHORT);
    render(<ErrorToastStack />);

    const toast = toastOf(SHORT);
    const button = dismissButtonIn(toast);
    act(() => {
      button.focus();
    });

    act(() => {
      // Focus leaving the button for something still inside the toast.
      fireEvent.focusOut(button, { relatedTarget: toast });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAST_ANY_WINDOW * 2);
    });

    expect(
      screen.queryByText(SHORT),
      "moving between controls inside the toast restarted the countdown",
    ).toBeInTheDocument();
  });
});

describe("the source axis, with the pause in place", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    // Unmount BEFORE the body is wiped. The stack portals into the body, so
    // emptying it first pulls the portal's node out from under React and its
    // own cleanup then fails on a node that is no longer anybody's child.
    cleanup();
    document.body.innerHTML = "";
  });

  it("still collapses two pushes that name the same source", () => {
    // POSITIVE CONTROL for the test below. Two toasts appearing there only
    // means something if one code with one source still produces one toast -
    // otherwise the split proves nothing about `source` and everything about
    // a dedupe that stopped working.
    useErrorStore
      .getState()
      .pushErrorDirect("vault_locked", "First notice failed.", "error", {
        source: "orphaned-copy",
      });
    useErrorStore
      .getState()
      .pushErrorDirect("vault_locked", "Second notice failed.", "error", {
        source: "orphaned-copy",
      });

    render(<ErrorToastStack />);

    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("keeps two sources apart, and pauses only the one being read", async () => {
    // Two buttons that can fail the same way at once. Before the axis
    // existed the second push was byte-identical to the first and vanished.
    // Holding one of them with the keyboard must not hold the other, or the
    // pause would turn one reader's attention into a stack that never drains.
    useErrorStore
      .getState()
      .pushErrorDirect("vault_locked", "First notice failed.", "error", {
        source: "orphaned-copy",
      });
    useErrorStore
      .getState()
      .pushErrorDirect("vault_locked", "Second notice failed.", "error", {
        source: "premigrate-copy",
      });

    render(<ErrorToastStack />);
    expect(screen.getAllByRole("status")).toHaveLength(2);

    act(() => {
      dismissButtonIn(toastOf("First notice failed.")).focus();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAST_ANY_WINDOW);
    });

    expect(screen.queryByText("First notice failed.")).toBeInTheDocument();
    expect(
      screen.queryByText("Second notice failed."),
      "focus on one toast froze the other one too",
    ).not.toBeInTheDocument();
  });
});
