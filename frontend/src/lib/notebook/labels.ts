/**
 * labels.ts - the words the notebook's dropdowns put on screen.
 *
 * They live here rather than beside the components that render them for one
 * mechanical reason: a file that exports a component may export constants
 * only if they are plain literals, or React Fast Refresh loses the ability to
 * hot-swap it (`react-refresh/only-export-components`, which the lint gate
 * treats as an error). These are objects, so they belong in a module of their
 * own - and the tests that look controls up by their visible words import the
 * same objects, so the words on screen and the words a test types can never
 * drift apart.
 */

/**
 * The words that name the four qualifiers on a limit row.
 *
 * They used to be `aria-label`s on four native selects in one row, so a
 * sighted reader saw four unlabelled boxes and had to open each to learn what
 * it asked. A visible `label` names the control for everyone, and it is the
 * ONLY name: an `aria-label` beside a visible label overrides it, so the words
 * on screen and the words a screen reader says would drift apart.
 */
export const BOUNDARY_SELECT_LABELS = {
  scope: "Where this applies",
  severity: "How strict",
  onViolation: "What to do if it happens",
  rating: "Rating ceiling",
} as const;

/**
 * What each qualifier may be set to, keyed by the value that is stored.
 *
 * Out here with the labels, and for a second reason on top of the lint one:
 * an option set nobody can import is an option set nobody can check. Deleting
 * `R` from the rating ceiling, or swapping two of the four responses, left
 * the whole suite green - the tests drove whatever the component happened to
 * render, so a lost option read as a shorter list rather than as a defect.
 * The tests hold each dropdown to the map it is given now.
 */
export const BOUNDARY_SCOPE_ITEMS = {
  global: "everywhere",
  chat: "this chat",
} as const;

export const BOUNDARY_SEVERITY_ITEMS = {
  hard: "never",
  veiled: "off the page",
  soft: "prefer not",
} as const;

export const BOUNDARY_ON_VIOLATION_ITEMS = {
  pause: "pause there",
  rewind: "go back and take it another way",
  fast_forward: "skip past it",
  hard_stop: "stop the scene",
} as const;

export const BOUNDARY_RATING_ITEMS = {
  "": "no rating limit",
  G: "G",
  PG: "PG",
  "PG-13": "PG-13",
  R: "R",
} as const;

/**
 * The two instruction languages, keyed by the value the router accepts.
 *
 * The Turkish label is the endonym, accents and all: it is the name of a
 * language on a control, and a language is named the way its speakers write
 * it. The error sentence in errorMessages.ts names the same language in
 * English, because that sentence is English from end to end.
 */
export const INSTRUCTION_LANGUAGE_LABELS = {
  en: "English",
  tr: "Türkçe",
} as const;

/** The note-model option that follows the chat's own model. Named here once:
 *  the picker shows it, and every sentence that names the option (the price
 *  warning, the refusal for a model that cannot write notes) quotes it. */
export const CHAT_MODEL_OPTION_LABEL = "Use the model I am chatting with";
