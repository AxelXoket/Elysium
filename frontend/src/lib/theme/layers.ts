/**
 * Layers - the one place an inline z-index is allowed to come from.
 *
 * The layer order itself lives in index.css and nowhere else, under the
 * heading "The layer table", and it is NOT repeated here: four tiers with
 * the selectors that belong to each, read back out of the compiled sheet by
 * test/css-contract.test.ts rather than mirrored anywhere. Two numbers
 * written twice are two numbers that disagree eventually, and the dialog
 * tier in particular is not a rule of this app's at all - it is the `z-50`
 * Tailwind utility that ui/dialog.tsx, ui/tooltip.tsx and ui/select.tsx all
 * set, which is exactly the kind of thing a copy here would get wrong.
 *
 * A component that sets `style={{ zIndex }}` competes with that table
 * without appearing in it, which is how the chat action menu's layer came to
 * be written at its two call sites where the table could not see it; so
 * inline values are banned, except for the ones named below.
 *
 * The one condition for a name here: the value orders children INSIDE a
 * stacking context the parent already opened, and is comparable to nothing in
 * the sheet's table. `.vault-stage` sits inside `.lock-overlay` (z-index 200,
 * its own context) and its 1 only puts the card above the veils beside it.
 * The list is imported by the test and by the call site, so the number is
 * written once.
 */
export const VAULT_STAGE_Z_INDEX = 1;

export interface InlineZIndexException {
  /** The class the node carries, which is how the test finds it. */
  selector: string;
  zIndex: number;
  where: string;
  reason: string;
}

export const INLINE_Z_INDEX_EXCEPTIONS: ReadonlyArray<InlineZIndexException> = [
  {
    selector: ".vault-stage",
    zIndex: VAULT_STAGE_Z_INDEX,
    where: "components/vault/LockOverlay.tsx",
    reason:
      "orders the warning card inside .lock-overlay's own stacking context; " +
      "competes with nothing in the sheet's table",
  },
];
