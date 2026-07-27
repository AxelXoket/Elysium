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
import { render, act } from "@testing-library/react";

import { LockOverlay } from "@/components/vault/LockOverlay";

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
