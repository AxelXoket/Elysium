/**
 * compiledSheet.ts - reading motion and blur facts out of the COMPILED sheet.
 *
 * Every function here takes the postcss tree of index.css as Vite and
 * Tailwind actually emit it (see glassSurfaceCss.ts for how that text is
 * obtained), never the source file. Three things about that output shape
 * every reader below: Tailwind keeps CSS nesting in dev output (`&:hover`
 * inside a utility rule, `&[data-open]` inside the popup rule), so a rule's
 * selector is only meaningful once its parent rules are folded in; token
 * values arrive as `var(--duration-fast)` and have to be resolved against
 * the `:root, :host` theme block before a number can be read; and the
 * reduced-motion and reduced-transparency answers live inside `@media`
 * at-rules that jsdom never evaluates, so they are read structurally.
 *
 * What is NOT here is a list of class names to compare against. The
 * reduced-motion model asks the cascade question directly: for each rule
 * that moves, is its motion neutralised under the media query, and is it
 * re-opened afterwards. The only list this module carries is the frozen
 * roster of NAMED exceptions from KURAL 5.6, which is the one thing that
 * has to be typed by hand because its whole point is that nobody adds to it
 * without writing the name down.
 */
import postcss, {
  type AtRule,
  type ChildNode,
  type Container,
  type Declaration,
  type Root,
  type Rule,
} from "postcss";

export const REDUCED_MOTION = /prefers-reduced-motion:\s*reduce/;
export const REDUCED_TRANSPARENCY = /prefers-reduced-transparency:\s*reduce/;

/** The six Base UI popup slots the stylesheet gives an entry/exit pair.
 *  The list is the popup rule's stated contract (dialog used today; the rest
 *  ready for the day a Select, Tooltip, Popover or Menu is rendered), and
 *  DialogSlotDuration.test.tsx ties the rendered dialog's slots back to it. */
export const POPUP_SLOTS = [
  "dialog-overlay",
  "dialog-content",
  "select-content",
  "tooltip-content",
  "popover-content",
  "menu-content",
] as const;

/**
 * KURAL 5.6's two exception families, by name. Under reduced motion every
 * animation and transition is neutralised by a universal rule, and these are
 * the only selectors allowed to re-open motion afterwards: family 1 keeps
 * loading indicators turning slowly, family 2 keeps a 120ms opacity fade so
 * a control still acknowledges the pointer. A selector that re-opens motion
 * and is not written here is a defect, and a name written here that no
 * longer re-opens anything is a stale entry; both are asserted.
 *
 * Naming a selector here is NOT a blanket amnesty for that selector. The
 * exception the plan writes is a property ("opacity fades at 120ms"), not an
 * element, and a roster read as "this selector is exempt" would let a 900ms
 * transform ride into the reduced-motion answer on the back of a name that
 * was added for a fade. What each name buys is spelled out in `amnestyFor`
 * below and enforced in `stillMoving`.
 */
export const NAMED_REDUCED_MOTION_EXCEPTIONS = {
  slowedIndicators: [".animate-spin", ".animate-pulse"],
  opacityFades: [
    // the persona panel and its three action variants
    ".persona-card",
    ".persona-select-action",
    ".persona-ghost-action",
    ".persona-danger-action",
    // the error toast: a 160ms keyframe fade in, a 120ms opacity fade
    ".error-toast",
    // the chat and message controls
    ".chat-action-trigger",
    ".chat-action-menu-item",
    ".chat-action-menu",
    ".chat-action-confirm",
    ".message-bubble-shell",
    ".message-actions",
    ".message-action-button",
    ".message-action-confirm",
    ".message-edit-textarea",
    ".inline-confirm-button",
    ".variant-nav-button",
    ".jump-to-latest",
    ".composer-icon-button",
    // the settings surface
    ".settings-category-row",
    ".settings-toggle-row",
    ".settings-back-button",
    ".settings-segment-option",
    ".settings-segment-button",
    ".settings-tint-chip",
    ".settings-inline-reset",
    ".settings-switch",
    ".settings-switch-thumb",
    ".settings-voice-model",
    ".settings-voice-pick",
    ".settings-voice-disclosure",
    ".settings-voice-button",
    ".settings-voice-input",
    ".settings-voice-select",
    ".settings-voice-textarea",
    ".settings-text-input",
    ".settings-section-disabled",
    ".settings-preview",
  ],
} as const;

export function allNamedExceptions(): Set<string> {
  return new Set<string>([
    ...NAMED_REDUCED_MOTION_EXCEPTIONS.slowedIndicators,
    ...NAMED_REDUCED_MOTION_EXCEPTIONS.opacityFades,
  ]);
}

/** What one named selector is allowed to keep moving, by property. */
export interface Amnesty {
  /** Whether an ANIMATION may stay open on it. */
  animation: boolean;
  /** The transition properties that may stay open. A re-opened transition
   *  touching anything outside this list is not covered. */
  transitionProperties: readonly string[];
}

/**
 * The amnesty a name buys, or null for a selector nobody named.
 *
 * Family 1 keeps its animation and nothing else: the spinner turns slowly,
 * it does not also acquire a transition. Family 2 keeps a transition on
 * opacity and nothing else: the fade acknowledges the pointer, the transform
 * that used to ride with it is exactly what reduced motion asks to lose.
 * `.error-toast` is family 2's one member that also keeps an animation,
 * because its fade is a KEYFRAME - the toast arrives as well as reacting,
 * and an arrival with no animation never appears at all.
 */
export function amnestyFor(selector: string): Amnesty | null {
  const roster: readonly string[] = NAMED_REDUCED_MOTION_EXCEPTIONS.slowedIndicators;
  if (roster.includes(selector)) {
    return { animation: true, transitionProperties: [] };
  }
  const fades: readonly string[] = NAMED_REDUCED_MOTION_EXCEPTIONS.opacityFades;
  if (fades.includes(selector)) {
    return {
      animation: selector === ".error-toast",
      transitionProperties: ["opacity"],
    };
  }
  return null;
}

export function parseSheet(css: string): Root {
  return postcss.parse(css);
}

/** The nearest ancestor that is a style rule, looking through at-rules. */
function parentRule(node: ChildNode): Rule | undefined {
  let p: Container | undefined = node.parent as Container | undefined;
  while (p && p.type !== "root") {
    if (p.type === "rule") return p as Rule;
    p = p.parent as Container | undefined;
  }
  return undefined;
}

/** Whether any enclosing at-rule satisfies `test`. */
export function inAtRule(
  node: ChildNode,
  test: (at: AtRule) => boolean,
): boolean {
  let p: Container | undefined = node.parent as Container | undefined;
  while (p && p.type !== "root") {
    if (p.type === "atrule" && test(p as AtRule)) return true;
    p = p.parent as Container | undefined;
  }
  return false;
}

export function inMedia(node: ChildNode, params: RegExp): boolean {
  return inAtRule(node, (at) => at.name === "media" && params.test(at.params));
}

export function inKeyframes(node: ChildNode): boolean {
  return inAtRule(node, (at) => /keyframes$/.test(at.name));
}

/** Split on a separator that sits outside every parenthesis. */
export function splitTopLevel(value: string, sep: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const c of value) {
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    if (c === sep && depth === 0) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter((s) => s !== "");
}

/** Whitespace-separated tokens, keeping `cubic-bezier(0.4, 0, 0.2, 1)` whole. */
export function tokensOf(value: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const c of value) {
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    if (/\s/.test(c) && depth === 0) {
      if (cur) out.push(cur);
      cur = "";
      continue;
    }
    cur += c;
  }
  if (cur) out.push(cur);
  return out;
}

/**
 * A rule's selectors with CSS nesting folded in: `&` is replaced by each
 * parent selector, and a nested selector with no `&` is a descendant of it.
 */
export function resolvedSelectors(rule: Rule): string[] {
  const own = rule.selectors.map((s) => s.replace(/\s+/g, " ").trim());
  const outer = parentRule(rule);
  if (!outer) return own;
  const out: string[] = [];
  for (const o of resolvedSelectors(outer)) {
    for (const s of own) {
      out.push(s.includes("&") ? s.split("&").join(o) : `${o} ${s}`);
    }
  }
  return out;
}

/**
 * Custom properties declared unconditionally on the root (`:root`, `:host`,
 * `html`), first declaration wins. This is the table `var()` resolves
 * against; a scoped redefinition (`.glass-right { --x: ... }`) is not a
 * token and is not read.
 */
export function rootTokens(root: Root): Map<string, string> {
  const table = new Map<string, string>();
  root.walkRules((rule) => {
    if (!resolvedSelectors(rule).some((s) => /^(:root|:host|html)$/.test(s))) return;
    if (inAtRule(rule, (at) => at.name === "media")) return;
    rule.each((n) => {
      if (n.type === "decl" && n.prop.startsWith("--") && !table.has(n.prop)) {
        table.set(n.prop, n.value.trim());
      }
    });
  });
  return table;
}

/** `var(--name, fallback)` substituted recursively against `table`. */
export function resolveVars(
  value: string,
  table: Map<string, string>,
  depth = 0,
): string {
  let out = "";
  let i = 0;
  while (i < value.length) {
    const at = value.indexOf("var(", i);
    if (at < 0) {
      out += value.slice(i);
      break;
    }
    out += value.slice(i, at);
    let parens = 0;
    let j = at + 3;
    for (; j < value.length; j += 1) {
      if (value[j] === "(") parens += 1;
      else if (value[j] === ")") {
        parens -= 1;
        if (parens === 0) break;
      }
    }
    const inner = value.slice(at + 4, j);
    const parts = splitTopLevel(inner, ",");
    const name = parts[0] ?? "";
    const fallback = parts.length > 1 ? parts.slice(1).join(", ") : undefined;
    const known = table.get(name);
    const replacement = known ?? fallback ?? "";
    out += depth > 24 ? replacement : resolveVars(replacement, table, depth + 1);
    i = j + 1;
  }
  return out;
}

/** `180ms` -> 180, `.15s` -> 150, anything else -> null. */
export function timeMs(token: string): number | null {
  const m = /^([+-]?(?:\d+\.?\d*|\.\d+))(ms|s)$/i.exec(token.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2].toLowerCase() === "s" ? n * 1000 : n;
}

export type MotionKind = "transition" | "animation";

/**
 * The two properties that carry a DURATION, which is the only thing the set
 * difference below measures. Deliberately narrow, and NOT the reader for the
 * universal reset: that block writes four declarations and two of them
 * (`animation-iteration-count`, `scroll-behavior`) name no duration at all,
 * so they are invisible here by design. `universalResetDeclarations` reads
 * the block literally instead, because a reset declaration no reader can see
 * is a declaration anybody can delete with the suite still green.
 */
export function kindOf(prop: string): MotionKind | null {
  if (prop === "transition" || prop === "transition-duration") return "transition";
  if (prop === "animation" || prop === "animation-duration") return "animation";
  return null;
}

/**
 * The durations a declaration sets, in ms, after `var()` resolution. A
 * shorthand yields one per comma segment (the FIRST time in a segment is the
 * duration, a second one is the delay); a `-duration` longhand yields each
 * listed time; `none` yields nothing.
 */
export function durationsOf(
  decl: Declaration,
  table: Map<string, string>,
): number[] {
  const value = resolveVars(decl.value, table).trim();
  if (/^none\b/.test(value)) return [];
  const out: number[] = [];
  for (const segment of splitTopLevel(value, ",")) {
    if (decl.prop.endsWith("-duration")) {
      const t = timeMs(segment);
      if (t !== null) out.push(t);
      continue;
    }
    const times = tokensOf(segment)
      .map(timeMs)
      .filter((t): t is number => t !== null);
    if (times.length > 0) out.push(times[0]);
  }
  return out;
}

/** The easing named in a resolved `animation`/`transition` shorthand. */
export function timingFunctionOf(resolvedValue: string): string | null {
  for (const tok of tokensOf(resolvedValue)) {
    if (/^(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end)$/.test(tok)) return tok;
    if (/^(cubic-bezier|steps|linear)\(/.test(tok)) return tok.replace(/\s+/g, "");
  }
  return null;
}

/** A token that names an easing rather than a property. */
function isEasingToken(token: string): boolean {
  return (
    /^(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end)$/.test(token) ||
    /^(cubic-bezier|steps|linear)\(/.test(token)
  );
}

/**
 * The properties a transition declaration animates, `var()` resolved. In a
 * shorthand segment the property is the first token that is neither a time
 * nor an easing nor the `allow-discrete` behaviour keyword, which is what
 * the grammar says it is. An animation, or a bare `transition-duration`
 * that names no property, yields nothing.
 */
export function transitionPropertiesOf(
  decl: Declaration,
  table: Map<string, string>,
): string[] {
  const value = resolveVars(decl.value, table);
  if (decl.prop === "transition-property") return splitTopLevel(value, ",");
  if (decl.prop !== "transition") return [];
  const out: string[] = [];
  for (const segment of splitTopLevel(value, ",")) {
    for (const token of tokensOf(segment)) {
      if (timeMs(token) !== null) continue;
      if (isEasingToken(token)) continue;
      if (token === "allow-discrete" || token === "normal") continue;
      out.push(token);
      break;
    }
  }
  return out;
}

/**
 * A selector reduced to what identifies the element it styles: whitespace
 * collapsed, state pseudo-classes dropped. `.x:hover:not(:disabled)` and
 * `.x` name the same element; `.x::before` does not, so pseudo-elements are
 * kept.
 */
export function normalizeSelector(selector: string): string {
  let s = selector.replace(/\s+/g, " ").trim();
  for (;;) {
    const before = s;
    s = s.replace(/:not\([^()]*\)/g, "");
    s = s.replace(/:(hover|active|focus-visible|focus-within|focus|disabled|enabled|checked)\b/g, "");
    if (s === before) break;
  }
  return s;
}

export interface MotionEntry {
  selector: string;
  kind: MotionKind;
  durationMs: number;
  important: boolean;
  prop: string;
}

/**
 * Set A: every rule outside the reduced-motion media (and outside any
 * keyframes) that gives an element a positive transition or animation
 * duration, one entry per resolved selector.
 */
export function movingRules(root: Root, table: Map<string, string>): MotionEntry[] {
  const out: MotionEntry[] = [];
  root.walkDecls((decl) => {
    const kind = kindOf(decl.prop);
    if (!kind) return;
    if (inMedia(decl, REDUCED_MOTION) || inKeyframes(decl)) return;
    const rule = decl.parent;
    if (!rule || rule.type !== "rule") return;
    const durations = durationsOf(decl, table);
    const longest = Math.max(0, ...durations);
    if (longest <= 0) return;
    for (const selector of resolvedSelectors(rule as Rule)) {
      out.push({
        selector,
        kind,
        durationMs: longest,
        important: decl.important === true,
        prop: decl.prop,
      });
    }
  });
  return out;
}

/** What a selector is given back inside the reduced-motion media query. */
export interface Reopening {
  /** The longest duration it re-opens at. */
  durationMs: number;
  /** For a transition, the properties it re-opens, merged across every
   *  declaration that re-opens it. Empty for an animation. */
  properties: string[];
}

export interface ReducedMotionModel {
  /** Universal selectors (`*`, `*::before`, `*::after`) that neutralise a
   *  kind WITH !important, which is the only way a universal rule can win. */
  universal: Record<MotionKind, Set<string>>;
  /** Normalised selectors neutralised by name (`transition: none`,
   *  `animation: none`, a duration of 1ms or less). */
  named: Record<MotionKind, Set<string>>;
  /** Normalised selectors that RE-OPEN motion inside the media query, with
   *  the duration and the properties they re-open. */
  reopened: Record<MotionKind, Map<string, Reopening>>;
}

/** Set B, read from the reduced-motion media block(s). */
export function reducedMotionModel(
  root: Root,
  table: Map<string, string>,
): ReducedMotionModel {
  const model: ReducedMotionModel = {
    universal: { transition: new Set(), animation: new Set() },
    named: { transition: new Set(), animation: new Set() },
    reopened: { transition: new Map(), animation: new Map() },
  };
  root.walkDecls((decl) => {
    const kind = kindOf(decl.prop);
    if (!kind) return;
    if (!inMedia(decl, REDUCED_MOTION) || inKeyframes(decl)) return;
    const rule = decl.parent;
    if (!rule || rule.type !== "rule") return;
    const durations = durationsOf(decl, table);
    const longest = Math.max(0, ...durations);
    for (const raw of resolvedSelectors(rule as Rule)) {
      const selector = normalizeSelector(raw);
      if (longest > 1) {
        const before = model.reopened[kind].get(selector);
        model.reopened[kind].set(selector, {
          durationMs: Math.max(longest, before?.durationMs ?? 0),
          properties: [
            ...new Set([
              ...(before?.properties ?? []),
              ...transitionPropertiesOf(decl, table),
            ]),
          ],
        });
        continue;
      }
      if (/^\*(::before|::after)?$/.test(selector)) {
        if (decl.important === true) model.universal[kind].add(selector);
        continue;
      }
      model.named[kind].add(selector);
    }
  });
  return model;
}

/**
 * FIX 5's universal reset, declaration by declaration: four and no more.
 *
 * The two durations go to 1ms rather than 0 so every transitionend and
 * animationend still fires (Base UI unmounts a popup on the exit animation's
 * end, and an animation of length 0 never ends). The iteration count caps an
 * infinite keyframe, which the 1ms duration would otherwise turn from a
 * one-second loop into a thousand-per-second strobe, the exact harm the
 * preference exists to prevent. `scroll-behavior: auto` answers WCAG 2.3.3.
 *
 * The table lives here rather than in the test file because the test imports
 * it: a duration retyped beside the assertion is a second copy of the spec.
 */
export const UNIVERSAL_RESET: Readonly<Record<string, string>> = {
  "animation-duration": "1ms",
  "animation-iteration-count": "1",
  "transition-duration": "1ms",
  "scroll-behavior": "auto",
};

/** The three selectors the reset has to reach. `*` does not match a
 *  pseudo-element on its own, and the sheet animates several of them. */
export const UNIVERSAL_RESET_SELECTORS = ["*", "*::before", "*::after"] as const;

export interface ResetDeclaration {
  value: string;
  important: boolean;
}

/**
 * Every declaration the reduced-motion media writes on a UNIVERSAL selector,
 * by selector and then by property, `var()` resolved.
 *
 * Property-agnostic on purpose. Asking only for the properties some other
 * reader already knows about is exactly how two of the reset's four
 * declarations went unguarded: `kindOf` sees durations, so deleting the
 * iteration count or the scroll behaviour left the whole suite green.
 */
export function universalResetDeclarations(
  root: Root,
  table: Map<string, string>,
): Map<string, Map<string, ResetDeclaration>> {
  const out = new Map<string, Map<string, ResetDeclaration>>();
  root.walkDecls((decl) => {
    if (!inMedia(decl, REDUCED_MOTION) || inKeyframes(decl)) return;
    const rule = decl.parent;
    if (!rule || rule.type !== "rule") return;
    for (const raw of resolvedSelectors(rule as Rule)) {
      const selector = raw.replace(/\s+/g, "");
      if (!/^\*(::[a-z-]+)?$/.test(selector)) continue;
      let byProp = out.get(selector);
      if (!byProp) {
        byProp = new Map<string, ResetDeclaration>();
        out.set(selector, byProp);
      }
      byProp.set(decl.prop, {
        value: resolveVars(decl.value, table).replace(/\s+/g, " ").trim(),
        important: decl.important === true,
      });
    }
  });
  return out;
}

/**
 * Every selector given an INFINITE animation outside the reduced-motion
 * media, normalised. This is the set the iteration count in the reset above
 * exists for, and it is what makes that declaration a behavioural claim
 * rather than a line of text somebody wrote once.
 */
export function infiniteAnimations(root: Root, table: Map<string, string>): string[] {
  const out = new Set<string>();
  root.walkDecls((decl) => {
    if (decl.prop !== "animation" && decl.prop !== "animation-iteration-count") return;
    if (inMedia(decl, REDUCED_MOTION) || inKeyframes(decl)) return;
    const rule = decl.parent;
    if (!rule || rule.type !== "rule") return;
    if (!/\binfinite\b/.test(resolveVars(decl.value, table))) return;
    for (const selector of resolvedSelectors(rule as Rule)) {
      out.add(normalizeSelector(selector));
    }
  });
  return [...out].sort();
}

/** One `duration-*` utility as the compiled sheet emits it. */
export interface DurationUtility {
  /** The class as an author writes it, unescaped: the bare-number form, the
   *  bracketed arbitrary form, or `duration-(--duration-fast)`. */
  className: string;
  /** The `transition-duration` the utility sets, as written. */
  value: string;
}

/**
 * The `duration-*` classes a component may keep while the motion language is
 * one set of tokens, with the reason each one is still here.
 *
 * Empty is the goal. An entry is a dated exemption, not a blanket: the test
 * that reads this roster also fails when a name on it stops being emitted,
 * so the exemption disappears from the sheet and from this table in the same
 * commit rather than outliving the call site.
 */
/**
 * The millisecond number ui/select.tsx:86 still writes on the Select popup.
 *
 * The class is ASSEMBLED from it rather than spelled out, here and
 * everywhere else in the suite, and that is load-bearing: Tailwind's scanner
 * reads every source file under `src/` as plain text, test files included,
 * so a comment or a string literal carrying the class emits the utility into
 * the compiled sheet all by itself. Spelled out, this roster would keep its
 * own exemption alive after IP-03 removed the real call site, and the
 * staleness check below could never fire.
 */
const SELECT_POPUP_MS = 100;

export const DURATION_UTILITY_EXCEPTIONS: Readonly<Record<string, string>> = {
  // Converting the Select popup is IP-03 FIX 1's job, not this unit's, so it
  // is named here instead of being fixed here. When IP-03 lands, this line
  // goes with it.
  [`duration-${SELECT_POPUP_MS}`]: "ui/select.tsx:86, converted to the token by IP-03 FIX 1",
};

/**
 * Every `duration-*` utility Tailwind emitted into the compiled sheet.
 *
 * This is FIX 1's `.tsx` scanner, and it never reads a `.tsx`. Tailwind only
 * emits `.duration-<x>` when a source file actually uses the class, so the
 * compiled sheet IS the inventory of hand-written durations in the component
 * tree, arrived at through the real pipeline rather than through a grep over
 * source text. A component that ships an arbitrary bracketed duration puts
 * that utility in here, where the test can see it is not one of the tokens.
 */
export function durationUtilities(root: Root): DurationUtility[] {
  const out: DurationUtility[] = [];
  const seen = new Set<string>();
  root.walkRules((rule) => {
    const names = new Set<string>();
    for (const selector of resolvedSelectors(rule)) {
      for (const match of selector.matchAll(/\.((?:\\.|[A-Za-z0-9_-])+)/g)) {
        const cls = match[1].replace(/\\(.)/g, "$1");
        if (cls.startsWith("duration-")) names.add(cls);
      }
    }
    if (names.size === 0) return;
    const decls = rule.nodes.filter(
      (n): n is Declaration => n.type === "decl" && n.prop === "transition-duration",
    );
    if (decls.length === 0) return;
    const value = decls[decls.length - 1].value.trim();
    for (const className of names) {
      const key = `${className}=${value}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ className, value });
    }
  });
  return out;
}

/** Whether one moving rule is silenced under reduced motion. */
export function isNeutralised(entry: MotionEntry, model: ReducedMotionModel): boolean {
  const selector = normalizeSelector(entry.selector);
  const byName = model.named[entry.kind].has(selector);
  if (entry.important) {
    // An !important duration outranks the universal reset; only a rule
    // naming the same element can win against it.
    return byName;
  }
  const pseudo = /::(before|after)$/.exec(selector)?.[0];
  const universal = model.universal[entry.kind];
  const byUniversal = pseudo ? universal.has(`*${pseudo}`) : universal.has("*");
  return byUniversal || byName;
}

export function isReopened(entry: MotionEntry, model: ReducedMotionModel): boolean {
  return model.reopened[entry.kind].has(normalizeSelector(entry.selector));
}

/**
 * Whether the named-exception roster actually covers this rule.
 *
 * Two narrowings, and both matter. First, the amnesty is for a re-opening
 * INSIDE the media query: a rule that outranks the universal reset from
 * outside it was never something the roster granted, so a name added for a
 * fade cannot shelter it. Second, the amnesty is for the PROPERTY the family
 * names, so a spinner may keep its animation and an opacity fade may keep
 * its opacity, and neither buys anything else on the same selector.
 */
export function amnestied(entry: MotionEntry, model: ReducedMotionModel): boolean {
  const selector = normalizeSelector(entry.selector);
  const amnesty = amnestyFor(selector);
  if (!amnesty) return false;
  if (!isNeutralised(entry, model) || !isReopened(entry, model)) return false;
  const reopening = model.reopened[entry.kind].get(selector);
  if (!reopening) return false;
  if (entry.kind === "animation") return amnesty.animation;
  return (
    reopening.properties.length > 0 &&
    reopening.properties.every((p) => amnesty.transitionProperties.includes(p))
  );
}

/**
 * A minus B: the selectors that still move under reduced motion, with the
 * named exceptions subtracted by the PROPERTY each name buys (see
 * `amnestied`), never by the name alone. Empty is the pass condition.
 * Throws when A is empty, because a difference over nothing proves nothing.
 */
export function stillMoving(root: Root): {
  moving: MotionEntry[];
  model: ReducedMotionModel;
  leftover: string[];
  unnamedReopenings: string[];
  staleNames: string[];
} {
  const table = rootTokens(root);
  const moving = movingRules(root, table);
  if (moving.length === 0) {
    throw new Error(
      "the compiled sheet carries no rule with a positive transition or " +
        "animation duration; the reader is broken and the difference below " +
        "would pass on nothing",
    );
  }
  const model = reducedMotionModel(root, table);
  const named = allNamedExceptions();
  const leftover = [
    ...new Set(
      moving
        .filter((e) => !isNeutralised(e, model) || isReopened(e, model))
        .filter((e) => !amnestied(e, model))
        .map((e) => `${e.kind}: ${normalizeSelector(e.selector)}`),
    ),
  ].sort();
  const reopenedAll = new Set<string>([
    ...model.reopened.transition.keys(),
    ...model.reopened.animation.keys(),
  ]);
  const unnamedReopenings = [...reopenedAll].filter((s) => !named.has(s)).sort();
  const staleNames = [...named].filter((s) => !reopenedAll.has(s)).sort();
  return { moving, model, leftover, unnamedReopenings, staleNames };
}

/**
 * The declarations of `prop` that apply to exactly `selector` outside the
 * reduced-motion media, in cascade order (last one wins), values resolved.
 */
export function declarationsFor(
  root: Root,
  table: Map<string, string>,
  selector: string,
  prop: string,
): Array<{ value: string; important: boolean }> {
  const out: Array<{ value: string; important: boolean }> = [];
  root.walkDecls(prop, (decl) => {
    if (inMedia(decl, REDUCED_MOTION) || inKeyframes(decl)) return;
    const rule = decl.parent;
    if (!rule || rule.type !== "rule") return;
    if (!resolvedSelectors(rule as Rule).includes(selector)) return;
    out.push({
      value: resolveVars(decl.value, table).replace(/\s+/g, " ").trim(),
      important: decl.important === true,
    });
  });
  return out;
}

/** `cubic-bezier(a, b, c, d)` or a CSS easing keyword as four numbers. */
export function parseBezier(value: string): [number, number, number, number] {
  const keyword: Record<string, [number, number, number, number]> = {
    // CSS Easing Functions Level 1, section 2.2: the keyword definitions.
    linear: [0, 0, 1, 1],
    ease: [0.25, 0.1, 0.25, 1],
    "ease-in": [0.42, 0, 1, 1],
    "ease-out": [0, 0, 0.58, 1],
    "ease-in-out": [0.42, 0, 0.58, 1],
  };
  const v = value.trim();
  if (v in keyword) return keyword[v];
  const m = /^cubic-bezier\(([^)]*)\)$/.exec(v);
  if (!m) throw new Error(`not an easing this reader understands: ${value}`);
  const nums = m[1].split(",").map((x) => Number(x.trim()));
  if (nums.length !== 4 || nums.some((n) => !Number.isFinite(n))) {
    throw new Error(`cubic-bezier did not parse: ${value}`);
  }
  return [nums[0], nums[1], nums[2], nums[3]];
}

/** The last compound of a selector: the element the rule paints. */
export function subjectOf(selector: string): {
  classes: string[];
  attributeSubstrings: string[];
  pseudoElement: string;
} {
  const compounds = selector
    .replace(/\s+/g, " ")
    .trim()
    .split(/\s*[>+~]\s*|\s+/)
    .filter((c) => c !== "");
  const last = compounds[compounds.length - 1] ?? "";
  const pseudoElement = /::[a-z-]+$/.exec(last)?.[0] ?? "";
  const classes = [...last.matchAll(/\.([A-Za-z0-9_\\-]+)/g)].map((m) =>
    m[1].replace(/\\/g, ""),
  );
  const attributeSubstrings = [
    ...last.matchAll(/\[class\*="([^"]+)"\]/g),
  ].map((m) => m[1]);
  return { classes, attributeSubstrings, pseudoElement };
}

/**
 * Whether a switch-off rule written for `entry` reaches the element that
 * `blurred` paints: same pseudo-element, and either a class the element
 * carries or a `[class*="x"]` substring one of its classes contains.
 */
export function switchOffReaches(entry: string, blurred: string): boolean {
  const off = subjectOf(entry);
  const on = subjectOf(blurred);
  if (off.pseudoElement !== on.pseudoElement) return false;
  if (off.classes.length > 0 && off.classes.every((c) => on.classes.includes(c))) {
    return true;
  }
  return off.attributeSubstrings.some((sub) =>
    on.classes.some((cls) => cls.includes(sub)),
  );
}
