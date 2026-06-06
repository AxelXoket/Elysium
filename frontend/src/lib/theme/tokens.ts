/**
 * Elysium Seasonal token names — TS reference for consistency.
 * Actual CSS variables are defined in index.css @theme block.
 * This file is read-only reference; import for autocomplete convenience.
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
    primarySage: "var(--color-es-primary-sage)",
    primarySageDeep: "var(--color-es-primary-sage-deep)",
    accentAmber: "var(--color-es-accent-amber)",
    mistBlue: "var(--color-es-mist-blue)",
    textLight: "var(--color-es-text-light)",
    textMuted: "var(--color-es-text-muted)",
    textDark: "var(--color-es-text-dark)",
    success: "var(--color-es-success)",
    warning: "var(--color-es-warning)",
    danger: "var(--color-es-danger)",
  },
  duration: {
    hover: "var(--duration-hover)",
    fast: "var(--duration-fast)",
    normal: "var(--duration-normal)",
    layout: "var(--duration-layout)",
  },
  ease: {
    spring: "var(--ease-spring)",
    smooth: "var(--ease-smooth)",
  },
} as const;
