/**
 * useRetryPress - one "Try again" press, followed to its end.
 *
 * TanStack Query v5 puts an errored query with no data back to `pending`
 * while it fetches again, so `isError` is false for the length of a retry.
 * A line drawn on `isError` alone closed under the button the reader had
 * just pressed (focus fell to the page's body) and came back when the retry
 * failed again. This follows the press instead: `out` is true from the
 * press until the query has SETTLED again (a newer `dataUpdatedAt` or
 * `errorUpdatedAt` than at the press - a fetch that starts and ends inside
 * one render still moves one of them), and `worked` counts the presses that
 * ended with the value read, so the caller can move the keyboard on then.
 *
 * State adjusted during render (React's documented pattern for "a prop
 * changed"), not in an effect.
 */
import { useState } from "react";

/** When a query last settled, from its result. */
export function settledAt(q: { dataUpdatedAt: number; errorUpdatedAt: number }): number {
  return Math.max(q.dataUpdatedAt, q.errorUpdatedAt);
}

export function useRetryPress(settled: number, failed: boolean) {
  const [pressedAt, setPressedAt] = useState<number | null>(null);
  const [worked, setWorked] = useState(0);
  if (pressedAt != null && settled > pressedAt) {
    setPressedAt(null);
    if (!failed) setWorked((w) => w + 1);
  }
  return {
    /** Call from the button's click, with the refetch. */
    press: () => setPressedAt(settled),
    /** A press whose fetch has not settled yet. */
    out: pressedAt != null && !(settled > pressedAt),
    /** How many presses ended with the value read. */
    worked,
  };
}
