/**
 * vaultLockUi - tiny channel between the sidebar's lock button and the
 * VaultGate's lock-closing overlay (same module-callback pattern as
 * setVaultLockedHandler in lib/api/client).
 *
 * The button OWNS the API call but hands it over as a `commit` callback: the
 * overlay fires it at the choreography's "click" moment, so the app stays
 * visible on screen while the lock snaps shut, and only then does the gate
 * flip underneath the deepening scrim.
 *
 * requestVaultLockAnimation returns false when no handler is registered
 * (tests, unexpected states) - the caller then commits immediately. Locking
 * never depends on the animation.
 */

let handler: ((commit: () => void) => void) | null = null;

export function setVaultLockAnimationHandler(
  h: ((commit: () => void) => void) | null,
): void {
  handler = h;
}

/** Ask the gate to play the lock-closing overlay; the overlay will invoke
 * `commit` (the actual lock API call) at the right beat. False = nobody is
 * listening, commit it yourself. */
export function requestVaultLockAnimation(commit: () => void): boolean {
  if (handler == null) return false;
  handler(commit);
  return true;
}
