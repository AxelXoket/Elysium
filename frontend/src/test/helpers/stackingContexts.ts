/**
 * stackingContexts.ts - every surface that opens a PERMANENT stacking
 * context, and nothing that does not.
 *
 * The table is the design reference's section 6.4, and it exists because a
 * z-index number is only comparable inside one context: an element cannot be
 * lifted over a sibling of its PARENT by any value at all. The error toast
 * proved it - it sat at 35 inside .elysium-stage, whose own z-index opens a
 * context, and read as a deliberate choice to paint under dialogs.
 *
 * Membership, and the criterion is the reference's word for word: a rule is
 * a member when it is not inside a `@keyframes` and carries at least one of
 * a non-auto `z-index` on a non-static position (or on no declared position
 * at all, in which case the position comes from a class in TSX and the row
 * says so), `backdrop-filter`, `filter`, `isolation`, `contain`,
 * `mix-blend-mode`, `content-visibility`, or a `will-change` that is not
 * `auto`. `transform` and an `opacity` below 1 do NOT make a member on
 * their own: both are usually a keyframe step or a transition state, so the
 * context they open is temporary.
 *
 * Two lists, because the compiled sheet holds two kinds of rule. The first
 * is what index.css writes: a surface somebody designed. The second is what
 * Tailwind and the shadcn import generate from a class a component used, so
 * a new entry there means a component reached for a utility that opens a
 * context - which is the same event, arriving by the other door.
 *
 * Measured, not transcribed: 49 declarations over 38 selectors, which is
 * the figure OLCUM item 17 records for the permanent-context count.
 */

/** [selector, the declaration that opens the context] */
export type StackingContextRow = readonly [string, string];

/** What index.css itself opens. Section 6.4's table, one row per hit. */
export const PERSISTENT_STACKING_CONTEXTS: ReadonlyArray<StackingContextRow> = [
  [".elysium-shell::before", "position:fixed + z-index"],
  [".mist-canvas", "position:absolute + z-index"],
  [".elysium-page", "isolation"],
  [".elysium-stage", "position:relative + z-index"],
  [".lock-overlay", "position:fixed + z-index"],
  [".lock-overlay-glyph", "filter"],
  [".drop-overlay", "position:absolute + z-index"],
  [".glass-dark", "backdrop-filter"],
  [".glass-dark", "-webkit-backdrop-filter"],
  [".glass-dark::after", "mix-blend-mode"],
  [".glass-dark > *", "position:relative + z-index"],
  [".persona-menu", "backdrop-filter"],
  [".persona-menu", "-webkit-backdrop-filter"],
  [".persona-menu", "z-index (position: TSX)"],
  [".chat-newchat-dock::before", "backdrop-filter"],
  [".chat-newchat-dock::before", "-webkit-backdrop-filter"],
  [".chat-newchat-dock::before", "position:absolute + z-index"],
  [".glass-right", "backdrop-filter"],
  [".glass-right", "-webkit-backdrop-filter"],
  [".persona-select-action:hover:not(:disabled), .persona-ghost-action:hover:not(:disabled), .persona-danger-action:hover:not(:disabled)", "filter"],
  [".warm-canvas", "isolation"],
  [".warm-canvas::before", "position:absolute + z-index"],
  [".canvas-mist", "position:absolute + z-index"],
  [".panel-mist", "position:absolute + z-index"],
  [".glass-dark > .panel-mist", "position:absolute + z-index"],
  [".model-row-lazy", "content-visibility"],
  [".vault-stage", "position:relative + z-index"],
  [".error-toast-stack", "z-index (position: TSX)"],
  [".sidebar-dialog", "isolation"],
  [".sidebar-dialog > :not([data-slot=\"dialog-close\"])", "position:relative + z-index"],
  [".sidebar-dialog > [data-slot=\"dialog-close\"]", "position:absolute + z-index"],
  [".sidebar-dialog-action:hover:not(:disabled), .sidebar-dialog-cancel:hover:not(:disabled)", "filter"],
  [".generation-control.is-disabled", "filter"],
  [".settings-section-disabled", "filter"],
  [".jump-to-latest", "position:absolute + z-index"],
  [".glass-dark .elysium-leafmark", "filter"],
  [".composer-icon-button:hover:not(:disabled)", "filter"],
  [".chat-action-menu, .chat-action-confirm", "backdrop-filter"],
  [".chat-action-menu, .chat-action-confirm", "-webkit-backdrop-filter"],
  [".chat-action-menu, .chat-action-confirm", "z-index (position: TSX)"],
  [".message-actions", "position:absolute + z-index"],
  [".message-bubble-shell:hover .message-action-button, .message-bubble-shell:focus-within .message-action-button", "backdrop-filter"],
  [".message-bubble-shell:hover .message-action-button, .message-bubble-shell:focus-within .message-action-button", "-webkit-backdrop-filter"],
  [".message-action-confirm", "backdrop-filter"],
  [".message-action-confirm", "-webkit-backdrop-filter"],
  [".message-action-confirm", "position:absolute + z-index"],
  [".es-side-panel", "contain"],
  [".panel-toggle", "position:absolute + z-index"],
  [".warm-canvas:has(.message-action-confirm) .panel-toggle", "z-index (position: TSX)"],
];

/** What a utility class opens. A new row here is a component's doing. */
export const UTILITY_STACKING_CONTEXTS: ReadonlyArray<StackingContextRow> = [
  [".isolate", "isolation"],
  [".z-10", "z-index (position: TSX)"],
  [".z-50", "z-index (position: TSX)"],
  [".blur", "filter"],
  [".filter", "filter"],
  [".backdrop-filter", "-webkit-backdrop-filter"],
  [".backdrop-filter", "backdrop-filter"],
  ["&[data-slot=\"kbd\"]", "isolation"],
  ["&[data-slot=\"kbd\"]", "z-index (position: TSX)"],
];

/** The counts OLCUM item 17 carries for the permanent inventory. */
export const PERSISTENT_CONTEXT_COUNTS = {
  declarations: 49,
  selectors: 38,
} as const;
