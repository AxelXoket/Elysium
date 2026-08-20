/**
 * LockOverlay.test.tsx - the lock animation must fire ONCE and get out of the
 * way on time.
 *
 * Audit: the timer effect depended on `onCommit`/`onDone`, which VaultGate
 * passes as fresh inline arrows on every render - and it always re-renders
 * right after the commit, because the lock mutation invalidates vault-status
 * and `unlocked` flips to false. So the cleanup cleared both timeouts and
 * re-armed them mid-animation: a SECOND POST /vault/lock fired (silently -
 * SidebarHeader is unmounted by then, so its onError no longer runs, and the
 * voice teardown thread ran again), and the overlay unmounted ~870 ms late.
 * During that tail every animated child has reached opacity 0, so a fully
 * transparent `position: fixed; inset: 0; z-index: 200` element sat over the
 * visible lock screen and swallowed clicks on the passphrase field.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, screen, fireEvent } from "@testing-library/react";

import { LockOverlay } from "@/components/vault/LockOverlay";
import { useLockAudioWarningStore } from "@/lib/query/vault";

function Parent({
  onCommit,
  onDone,
  renderKey,
}: {
  onCommit: () => void;
  onDone: () => void;
  renderKey: number;
}) {
  // Inline arrows, exactly as VaultGate passes them.
  return (
    <div data-render={renderKey}>
      <LockOverlay onCommit={() => onCommit()} onDone={() => onDone()} />
    </div>
  );
}

describe("LockOverlay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // The audio-left store is module-global (LockOverlay has no other way to
    // reach a mutation result it does not own the call for - see
    // lib/query/vault.ts's useLockAudioWarningStore comment). A leftover
    // warning from one test would otherwise leak into the next.
    useLockAudioWarningStore.setState({ pending: false, audioLeft: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("commits once and unmounts on time despite parent re-renders", () => {
    const onCommit = vi.fn();
    const onDone = vi.fn();
    const view = render(
      <Parent onCommit={onCommit} onDone={onDone} renderKey={0} />,
    );

    // The commit lands...
    act(() => {
      vi.advanceTimersByTime(900);
    });
    expect(onCommit).toHaveBeenCalledTimes(1);

    // ...and the parent re-renders immediately after it (vault-status
    // invalidation), handing down brand-new callback identities.
    view.rerender(<Parent onCommit={onCommit} onDone={onDone} renderKey={1} />);
    view.rerender(<Parent onCommit={onCommit} onDone={onDone} renderKey={2} />);

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("calls the LATEST callbacks, not the ones from first render", () => {
    const first = vi.fn();
    const second = vi.fn();
    const view = render(
      <Parent onCommit={first} onDone={() => {}} renderKey={0} />,
    );
    view.rerender(<Parent onCommit={second} onDone={() => {}} renderKey={1} />);

    act(() => {
      vi.advanceTimersByTime(900);
    });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("clears its timers on unmount", () => {
    const onCommit = vi.fn();
    const onDone = vi.fn();
    const view = render(
      <Parent onCommit={onCommit} onDone={onDone} renderKey={0} />,
    );
    view.unmount();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(onCommit).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });
});

/**
 * "Locked" is a promise about what can be read. A normal lock keeps the
 * overlay exactly as silent as before (GROUND, below). Only a lock that left
 * generated speech readable gets to speak - and it has to wait for the real
 * answer from the backend before deciding which one it is (the pending
 * case), never guess from a timer alone.
 */
describe("LockOverlay - the audio-left warning", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useLockAudioWarningStore.setState({ pending: false, audioLeft: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("GROUND: a clean lock never shows the notice and still calls onDone on time", () => {
    const onCommit = vi.fn();
    const onDone = vi.fn();
    render(<LockOverlay onCommit={onCommit} onDone={onDone} />);

    act(() => {
      vi.advanceTimersByTime(900);
    });
    // The mutation "resolved" clean, same as the default store state.
    act(() => {
      useLockAudioWarningStore.getState().settle([]);
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("lock-audio-left-notice")).toBeNull();
  });

  it("POSITIVE CONTROL: a partial lock holds the veil, names the file, and waits to be acknowledged", () => {
    const onCommit = vi.fn();
    const onDone = vi.fn();
    render(<LockOverlay onCommit={onCommit} onDone={onDone} />);

    act(() => {
      vi.advanceTimersByTime(900);
    });
    act(() => {
      useLockAudioWarningStore.getState().settle(["reply_3f2a1c9d.wav"]);
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    // The normal reveal timer has long since fired, but a partial lock does
    // not hand control back on its own.
    expect(onDone).not.toHaveBeenCalled();
    const notice = screen.getByTestId("lock-audio-left-notice");
    expect(notice).toBeInTheDocument();
    expect(
      screen.getByTestId("lock-audio-left-name-reply_3f2a1c9d.wav"),
    ).toHaveTextContent("reply_3f2a1c9d.wav");

    fireEvent.click(screen.getByTestId("lock-audio-left-ack"));
    expect(onDone, "clicking Got it must not hand control back instantly - the veil still has to fade").not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("waits for a slow /vault/lock instead of guessing clean when its own timer runs out first", () => {
    const onCommit = vi.fn();
    const onDone = vi.fn();
    render(<LockOverlay onCommit={onCommit} onDone={onDone} />);

    act(() => {
      vi.advanceTimersByTime(900);
    });
    act(() => {
      useLockAudioWarningStore.getState().begin();
    });
    // The overlay's own reveal timer elapses before the mutation answers.
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(onDone, "a still-pending lock read as a clean one").not.toHaveBeenCalled();
    expect(screen.queryByTestId("lock-audio-left-notice")).toBeNull();

    // The backend finally answers, with something to report.
    act(() => {
      useLockAudioWarningStore.getState().settle(["late_reply.wav"]);
    });
    expect(screen.getByTestId("lock-audio-left-notice")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
