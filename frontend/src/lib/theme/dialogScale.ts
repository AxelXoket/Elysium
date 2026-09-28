/**
 * dialogScale.ts - how wide a dialog is allowed to be, and how tall.
 *
 * Four widths and one height, and a dialog picks from here or it is off the
 * scale. The scale has to include the primitive's own default, or every
 * DialogContent that sets no width at all would be off a scale its own
 * component put it on; `ui/dialog.tsx` reads BASE for exactly that reason,
 * so the default and the scale cannot drift apart.
 *
 * The height is one value on purpose. Before this there were two, 85vh at
 * three call sites and 88vh at a fourth with no reason written anywhere, and
 * two of the seven dialogs had no height at all - which is not "shorter", it
 * is "as tall as its content", and a long form then runs past the top and
 * bottom of the window. The page itself never scrolls, so the part that
 * does not fit cannot be reached at all.
 *
 * ImageLightbox is the one named exception and it is named HERE rather than
 * left as an oddity in its own file: it shows a picture at the picture's
 * size, carries no chrome, and 768 is not on the scale.
 */

/** The four widths, narrowest first. Nothing else is a legal dialog width. */
export const DIALOG_WIDTHS = {
  /** 384. `ui/dialog.tsx`'s own default: one question, one confirmation. */
  base: "sm:max-w-sm",
  /** 448. A single-column settings dialog, or a short form. */
  narrow: "sm:max-w-md",
  /** 512. A form dialog with more than five fields. */
  medium: "sm:max-w-lg",
  /** 672. Two columns, or a long scrolling settings surface. */
  wide: "sm:max-w-2xl",
} as const;

export type DialogWidth = (typeof DIALOG_WIDTHS)[keyof typeof DIALOG_WIDTHS];

/** The one height. A second vh value is a second answer to one question. */
export const DIALOG_MAX_HEIGHT = "max-h-[85vh]";

/**
 * The lightbox, which is not on the scale and says so.
 *
 * Content width, no chrome, no close button of the primitive's: the picture
 * is the dialog. Written once here so the exception is a record rather than
 * a surface nobody checked.
 */
export const LIGHTBOX_CLASSES =
  "w-auto max-w-[calc(100%-2rem)] bg-transparent p-0 shadow-none ring-0 sm:max-w-3xl";
