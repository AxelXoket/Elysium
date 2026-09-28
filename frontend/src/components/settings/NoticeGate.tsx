/**
 * NoticeGate - the four-way split the vault leftover notices read through.
 *
 * Five notices in this folder (PlaintextBackupNotice, OrphanedCopyNotice,
 * RotationBackupNotice, EmptyStubNotice, PremigrateBackupNotice) each answer
 * one question from GET /vault/status: is a particular file still on disk.
 * Every one of them used to gate on `status.data?.<flag>`, which folds the
 * query FAILING into the query answering "no": a 500, a body the schema
 * rejects, a refetch that fails after the notice is mounted, all rendered
 * the same nothing as a clean disk. In the shipped app the outage case is
 * caught one level up: VaultGate (VaultGate.tsx:297, `isError || !status`)
 * reads this same query and swaps the whole app for its "Cannot reach the
 * backend" card, so a user does not see a notice while the backend is down.
 * The error line here is defence in depth for whatever does reach a mounted
 * notice, including the cases above and a notice rendered anywhere the gate
 * does not wrap. On a screen whose job is to admit that an unencrypted copy
 * of the database exists, silence is the wrong default for a failure, and no
 * console call survives in this app to leave a trace either.
 *
 * ProxySection and ApiKeySection already split their own status reads four
 * ways for this reason ("pending and error get their own branch so neither
 * one can be read as the true unconfigured state"). This file is that split
 * written once, so the five notices cannot drift apart again:
 *
 *   pending  the first answer has not landed. A quiet "Checking for ..."
 *            line: muted text, no icon, no border, no colour. Rendering
 *            nothing here would draw loading as the empty state, which is
 *            the one collapse this split exists to stop, and a line this
 *            plain cannot be mistaken for a warning. In practice the line is
 *            almost never painted: VaultGate mounts the app only after this
 *            same query has succeeded, so a notice's first render already
 *            finds the answer in the cache.
 *   error    the query is in error. "Could not check for ... right now."
 *            Same quiet shape as pending. It names what was being looked
 *            for, so the lines stay tellable apart when several notices sit
 *            on one screen, and it borrows none of the warnings' wording,
 *            icon or danger colour.
 *   present  the flag says the file is there: the notice renders itself,
 *            exactly as before.
 *   absent   the query answered and the flag is off: nothing, or whatever
 *            the caller hands in as `absent` (PlaintextBackupNotice confirms
 *            a deletion there).
 *
 * `error` outranks stale data on purpose, as in the precedent: after a
 * failed refetch TanStack keeps the previous answer in `data`, and that
 * answer can be wrong in either direction (the file the user just deleted,
 * or one that appeared since). useVaultStatus retries every 2.5s while in
 * error, so the honest line gives way to the real answer on its own.
 *
 * Shape: a plain function that hands back either the data to render with or
 * the element to return instead, rather than a wrapper component with a
 * render prop. The notices already early-return above their JSX, so this
 * keeps each call site to a few lines and leaves every warning byte for byte
 * as it was. Tailwind utilities only, the same classes the notices' own hint
 * paragraphs use; nothing in index.css.
 */
import type { ReactElement } from "react";

import type { VaultStatus } from "@/lib/schemas/vault";

/** The three fields of a `useVaultStatus()` result the split reads. Named
 *  structurally so a caller is not tied to TanStack's result type. */
export interface VaultStatusRead {
  isPending: boolean;
  isError: boolean;
  data: VaultStatus | undefined;
}

export interface NoticeGateOptions {
  /** Stem of the notice's own data-testid: "empty-stub" for the
   *  "empty-stub-notice" section. The check line renders as "<id>-check". */
  id: string;
  /** What the check looks for, as a noun phrase that completes both
   *  "Checking for ..." and "Could not check for ... right now.":
   *  "a leftover recovery file". */
  subject: string;
  /** Rendered when the check ran and found nothing. Null when omitted. */
  absent?: ReactElement | null;
}

export type NoticeGateResult =
  | { state: "present"; data: VaultStatus }
  | { state: "pending" | "error" | "absent"; fallback: ReactElement | null };

/**
 * Decide which of the four states a notice is in. `isPresent` is the one
 * line the notice keeps for itself: what, in the status body, means the file
 * is there. It only runs on a body that actually arrived.
 */
export function noticeGate(
  status: VaultStatusRead,
  isPresent: (status: VaultStatus) => boolean,
  options: NoticeGateOptions,
): NoticeGateResult {
  if (status.isPending) {
    return { state: "pending", fallback: checkLine(options, "pending") };
  }
  if (status.isError) {
    return { state: "error", fallback: checkLine(options, "error") };
  }
  const data = status.data;
  if (data !== undefined && isPresent(data)) {
    return { state: "present", data };
  }
  return { state: "absent", fallback: options.absent ?? null };
}

/**
 * The line a notice shows instead of itself while the answer is unknown.
 * Same element, classes and muted token as the hint paragraphs inside the
 * notices and AutoLockControl's own pending/error line on this surface, and
 * deliberately none of the warning furniture: no section, no border, no
 * icon, no role. `data-state` publishes which of the two it is, so a reader
 * of the rendered page (a test included) can tell them apart without
 * parsing English.
 *
 * A plain element factory rather than a component, and not only for
 * brevity: this file exports a function that is not a component, and the
 * react-refresh rule (rightly) refuses a component beside it.
 */
function checkLine(
  { id, subject }: NoticeGateOptions,
  state: "pending" | "error",
): ReactElement {
  return (
    <p
      data-testid={`${id}-check`}
      data-state={state}
      className="text-label leading-relaxed text-muted-foreground"
    >
      {state === "pending"
        ? `Checking for ${subject}…`
        : `Could not check for ${subject} right now.`}
    </p>
  );
}
