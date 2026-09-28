/**
 * Elysium Seasonal token names and values, the JS mirror of the @theme block
 * in index.css.
 *
 * Two kinds of field, and the difference is what motion needs. The `var()`
 * strings are for anything that ends up in CSS (an inline style, a className
 * with an arbitrary value) and stay strings. `easeValues` and
 * `durationSeconds` are NUMBERS, because motion's `transition.ease` and
 * `transition.duration` take a bezier tuple and seconds, not a `var()`; the
 * seven call sites that used to spell `[0.4, 0, 0.2, 1]` by hand read
 * `easeValues.smooth` now, and the three that wrote `0.18` read
 * `durationSeconds.fast`.
 *
 * Written by hand, held equal to index.css by a test rather than generated:
 * test/tokens-sync.test.ts parses the @theme block and compares both
 * directions, by name for the `var()` fields and by numeric value for the two
 * numeric ones, so a token added on one side and forgotten on the other is a
 * red test rather than a quiet drift. A name here is only ever the name in
 * the stylesheet; a value here is only ever the value in the stylesheet.
 */
export const tokens = {
  colors: {
    background: "var(--color-es-background)",
    sidebar: "var(--color-es-sidebar)",
    surface: "var(--color-es-surface)",
    surfaceElevated: "var(--color-es-surface-elevated)",
    borderDark: "var(--color-es-border-dark)",
    warmCanvas: "var(--color-es-warm-canvas)",
    warmCanvasSoft: "var(--color-es-warm-canvas-soft)",
    borderWarm: "var(--color-es-border-warm)",
    scrollbarOnLight: "var(--color-es-scrollbar-on-light)",
    scrollbarOnDark: "var(--color-es-scrollbar-on-dark)",
    primarySage: "var(--color-es-primary-sage)",
    primarySageDeep: "var(--color-es-primary-sage-deep)",
    primaryInk: "var(--color-es-primary-ink)",
    accentAmber: "var(--color-es-accent-amber)",
    meterWarning: "var(--color-es-meter-warning)",
    accentStrong: "var(--color-es-accent-strong)",
    mistBlue: "var(--color-es-mist-blue)",
    textLight: "var(--color-es-text-light)",
    textMuted: "var(--color-es-text-muted)",
    textDark: "var(--color-es-text-dark)",
    success: "var(--color-es-success)",
    warning: "var(--color-es-warning)",
    danger: "var(--color-es-danger)",
    glassSidebar: "var(--color-es-glass-sidebar)",
    glassRight: "var(--color-es-glass-right)",
    glassBorderDark: "var(--color-es-glass-border-dark)",
    glassBorderWarm: "var(--color-es-glass-border-warm)",
    glassDialog: "var(--color-es-glass-dialog)",
    canvasFrom: "var(--color-es-canvas-from)",
    canvasTo: "var(--color-es-canvas-to)",
    userBubble: "var(--color-es-user-bubble)",
    userBubbleText: "var(--color-es-user-bubble-text)",
    asstBubble: "var(--color-es-asst-bubble)",
    asstBubbleText: "var(--color-es-asst-bubble-text)",
    frameBorder: "var(--color-es-frame-border)",
    hairline: "var(--color-es-hairline)",
  },
  duration: {
    hover: "var(--duration-hover)",
    fast: "var(--duration-fast)",
    normal: "var(--duration-normal)",
    layout: "var(--duration-layout)",
    exit: "var(--duration-exit)",
    panel: "var(--duration-panel)",
    column: "var(--duration-column)",
  },
  ease: {
    spring: "var(--ease-spring)",
    smooth: "var(--ease-smooth)",
    resize: "var(--ease-resize)",
    exit: "var(--ease-exit)",
  },
  /** The seven type steps, by name; the numbers live in `typeScale`. */
  text: {
    micro: "var(--text-micro)",
    label: "var(--text-label)",
    ui: "var(--text-ui)",
    control: "var(--text-control)",
    body: "var(--text-body)",
    title: "var(--text-title)",
    display: "var(--text-display)",
  },
} as const;

/** One step of the type scale: size and line height in px, the weight the
 *  step carries as its own (null when the step inherits, which is 400 by
 *  default or whatever the call site sets), and the tracking as CSS writes
 *  it. */
export interface TypeStep {
  px: number;
  lineHeight: number;
  weight: 400 | 600 | null;
  tracking: string;
}

/**
 * The type scale, seven steps, and a size is chosen from here or not at all.
 *
 * Sizes were an accumulation, not a scale: nine px values and five rem
 * literals in the stylesheet, four Tailwind steps in the components, three
 * of them arbitrary. This is the list. 13 is the chrome base (sidebar, tabs,
 * buttons, menu items, controls), 14 is the base of the ui/ primitives and
 * dialog body text and is closed to new call sites, 15 is the message body
 * default (MSG_FONT_DEFAULT reads it), 16 titles, 20 the empty state, 11 a
 * badge or a counter and never prose, 12 a label.
 *
 * Weights: 400, 600 and 700 are the only ones on the scale, because they
 * fall the same way in every system font family; 500 is Semibold in Segoe
 * UI and Regular in Arial, so it is banned (KURAL 3.4). Only the steps that
 * are always emphasised carry a weight of their own: micro, title and
 * display at 600, and the label step at 400. The label's is written down
 * rather than inherited on purpose - a label inside a semibold heading
 * block would otherwise come out semibold, which is the one thing a quiet
 * label must not be. 13, 14 and 15 inherit, so chrome is 400 or 600 by the
 * call site. The @theme
 * block in index.css mirrors this table token for token and
 * test/tokens-sync.test.ts holds the two equal.
 */
export const typeScale = {
  micro: { px: 11, lineHeight: 15, weight: 600, tracking: "0.01em" },
  label: { px: 12, lineHeight: 16, weight: 400, tracking: "0.005em" },
  ui: { px: 13, lineHeight: 18, weight: null, tracking: "0" },
  control: { px: 14, lineHeight: 20, weight: null, tracking: "0" },
  body: { px: 15, lineHeight: 22, weight: null, tracking: "0" },
  title: { px: 16, lineHeight: 22, weight: 600, tracking: "-0.005em" },
  display: { px: 20, lineHeight: 26, weight: 600, tracking: "-0.01em" },
} as const satisfies Record<keyof typeof tokens.text, TypeStep>;

/**
 * One row of the deviation inventory: a declaration whose step is NOT the one
 * the transition table names for it.
 *
 * Every retired step name in this file's prose, the rows below included, is
 * ASSEMBLED from fragments.
 * Tailwind scans this module as raw text and treats a name it finds as a
 * class somebody wrote, and what that costs depends on the name. An
 * ARBITRARY size spelled here emits a rule for itself at once and turns
 * test/lib/fontScale.test.ts's scale assertion red. A retired STOCK step
 * name emits nothing while index.css clears the `--text-*` namespace before
 * the seven steps are declared, and comes back the moment that reset goes -
 * both halves measured, and the accurate two-part version is written out in
 * fontScale.test.ts. The arbitrary half was measured the hard way, twice: a
 * rem size written out in one of the reasons below emitted a rule for itself
 * and turned that assertion red, and so did the sentence that was added here
 * to warn about it. The rows assemble either kind rather than lean on the
 * reset holding.
 */
export interface TypeScaleDeviation {
  /** The selector or the call site, spelled the way it is written. */
  where: string;
  /**
   * What it computed to before, in px, or null when there was no before.
   *
   * Null is markup this work ADDED rather than moved: a new element still
   * chooses a step, and choosing one the transition table would not name for
   * it is the same decision as moving to it, so it is owed the same row.
   */
  fromPx: number | null;
  /** The step it took, by name. */
  step: keyof typeof typeScale;
  /** Why the step it took beats the one the table names. */
  why: string;
}

/**
 * Every declaration this work moved to a step the transition table does not
 * name for it, with the reason each one is allowed to.
 *
 * IP-05 FIX 1 grants the freedom and prices it in one sentence: "Tablodaki
 * hedef ile rol kurali catisirsa rol kurali kazanir ve fark envanterde adiyla
 * yazilir." The role rule is "metin bir kontrolun icindeyse veya bir kontrolu
 * adlandiriyorsa `--text-ui` (13) alir; her diger durumda `--text-label` (12)
 * alir", and it wins - but the difference has to be written down by name, and
 * this is where. Thirteen rows, measured selector by selector and call site
 * by call site against the base commit rather than remembered.
 *
 * Fourteenth, and it is not a row because it is not a declaration: REF 3.5's
 * weight column writes 400 for `--text-control` and `--text-body`, and
 * neither step declares one. They inherit on purpose - see the weights note
 * on `typeScale` below - so chrome comes out 400 or 600 by the call site
 * rather than being reset to 400 in the middle of a semibold block. That is
 * the scale disagreeing with the reference's table, argued rather than
 * silent.
 */
export const TYPE_SCALE_DEVIATIONS: ReadonlyArray<TypeScaleDeviation> = [
  {
    where: "index.css .persona-strip-avatar",
    fromPx: 12,
    step: "micro",
    why:
      "the monogram inside a 28x28 avatar circle, so REF 3.5's micro row " +
      "(badge, counter, timestamp, never prose) reads it as a badge glyph " +
      "rather than as a label. The role rule's own two outcomes are 13 and " +
      "12; this is the one place a third is right, and it moves 12 to 11",
  },
  {
    where: "index.css .char-avatar",
    fromPx: 11,
    step: "micro",
    why:
      "the same monogram, in the chat list, and REF 3.5's micro row is what " +
      "allows it: 'rozet, sayac, zaman damgasi. Duz yazi ASLA' - a glyph in " +
      "a circle is a badge and never prose. The role rule would have offered " +
      "13 or 12; 11 is the third answer and it is this row that grants it. " +
      "Pixel value unchanged",
  },
  {
    where: "index.css .vault-title",
    fromPx: 15,
    step: "title",
    why:
      "the lock screen's own h1, and REF 3.5's title row is 'panel ve dialog " +
      "basligi'. 15 is on the scale as --text-body, so nothing forced the " +
      "move; a screen title is a title",
  },
  {
    where: "index.css .persona-menu-manage",
    fromPx: 11.5,
    step: "ui",
    why: "a menu item, so the role rule's 13 beats the table's 12",
  },
  {
    where: "index.css .chat-action-menu-item",
    fromPx: 12.16,
    step: "ui",
    why: "a menu item too, so the same rule in the same direction: 13",
  },
  {
    where: "index.css .inline-confirm-button",
    fromPx: 11.52,
    step: "ui",
    why: "a button, so the role rule's 13 beats the table's 12",
  },
  {
    where: "index.css .error-fallback-button",
    fromPx: 13.6,
    step: "ui",
    why: "a button, so 13 rather than the table's 14",
  },
  {
    where: "index.css .error-fallback-text",
    fromPx: 13.6,
    step: "ui",
    why:
      "chrome prose on the crash screen. 14 is the nearest step and is also " +
      "closed to new call sites (REF 3.5: only a components/ui/ primitive's " +
      "base string and dialog body text), so the role rule's 13 takes it and " +
      "it matches the button beneath it",
  },
  {
    where:
      "ten " + "text-" + "xs call sites that name a control: " +
      "settings/SwitchRow.tsx's shared span at its four call sites, " +
      "notebook/BoundaryPanel.tsx's shared qualifier TRIGGER at its four, " +
      "and notebook/ExtractionSettings.tsx's two triggers",
    fromPx: 12,
    step: "ui",
    why:
      "REF 3.5's " + "text-" + "xs row is conditional - " +
      "'rol etiket ise burada kalir' - " +
      "and these name controls rather than labelling content, so they take " +
      "the chrome base. Ten call sites on four source lines: the span and " +
      "the trigger are each written once and rendered four times. The other " +
      "panels' fields are NOT in this group - they label content and took " +
      "the table's own 12",
  },
  {
    where: "notebook/BoundaryPanel.tsx's four qualifier LABELS",
    fromPx: null,
    step: "ui",
    why:
      "markup IP-03 FIX 6 adds rather than moves: the four qualifiers had " +
      "an " + "aria-" + "label and no words on screen, so there was no size " +
      "to come from. A new element still picks a step, and this one names a " +
      "control, so the role rule puts it on the chrome base beside the " +
      "trigger it names rather than on the 12 a label would otherwise take",
  },
  {
    where: "components/ui/input.tsx and components/ui/textarea.tsx base strings",
    fromPx: 16,
    step: "control",
    why:
      "REF 3.5's transition table sends " + "text-" + "base to " +
      "--text-title (16), " +
      "which would be absurd for a form field. --text-control's own usage " +
      "column is 'components/ui/ ilkellerinin taban puntosu', which is " +
      "literally what these two are. Both carried a md: " + "text-" + "sm " +
      "variant as well, so " +
      "above the md breakpoint - where this app always is - the rendered " +
      "size does not move at all; below it, 16 becomes 14",
  },
  {
    where: "components/chat/EmptyState.tsx's heading",
    fromPx: 16,
    step: "display",
    why:
      "REF 3.5's display row reads 'bos durum, nadir' and this is the empty " +
      "state. The reference's two sentences disagree here and the usage " +
      "column is the more specific one; 16 becomes 20",
  },
  {
    where: "components/ui/button.tsx size.sm",
    fromPx: 12.8,
    step: "ui",
    why:
      "an arbitrary rem size the plan's forty missed, so no table row names " +
      "it at all. A button's own text is chrome, which is 13. The size is " +
      "not spelled here: Tailwind scans this file as text and emits a rule " +
      "for an arbitrary size the moment it reads one",
  },
];

/** The step names, in scale order: `text-micro` ... `text-display`. */
export const TYPE_SCALE_STEPS = Object.keys(typeScale) as ReadonlyArray<
  keyof typeof typeScale
>;

/** The only font weights on the scale (KURAL 3.4). At most three, and these. */
export const fontWeights = [400, 600, 700] as const;

/**
 * The generic family the body stack resolves through.
 *
 * KARAR-V: the user's own Windows font is what the app renders in, so the
 * stack starts with a generic keyword and pins no family name. `system-ui`
 * is the one Chromium honours; `ui-sans-serif` is not a generic there and
 * was dead weight in front of it. The stack's first member is held to this
 * by test/css-contract.test.ts (T-88).
 */
export const SYSTEM_FONT_GENERIC = "system-ui";

/** A cubic-bezier as motion takes it: the four control values. */
export type Bezier = readonly [number, number, number, number];

/**
 * The easing curves as numbers, one per `--ease-*` token. `exit` is the CSS
 * keyword `ease-out` written out (CSS Easing Functions Level 1 defines it as
 * cubic-bezier(0, 0, 0.58, 1)); motion has no keyword that means the same
 * curve, so the numbers are what keeps the JS and CSS halves equal.
 */
export const easeValues = {
  spring: [0.34, 1.56, 0.64, 1],
  smooth: [0.4, 0, 0.2, 1],
  resize: [0.4, 0, 0.6, 1],
  exit: [0, 0, 0.58, 1],
} as const satisfies Record<keyof typeof tokens.ease, Bezier>;

/** The durations in seconds, one per `--duration-*` token. */
export const durationSeconds = {
  hover: 0.14,
  fast: 0.18,
  normal: 0.24,
  layout: 0.3,
  exit: 0.12,
  panel: 0.42,
  column: 0.32,
} as const satisfies Record<keyof typeof tokens.duration, number>;
