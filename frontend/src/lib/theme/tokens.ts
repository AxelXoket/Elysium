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
} as const;

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
