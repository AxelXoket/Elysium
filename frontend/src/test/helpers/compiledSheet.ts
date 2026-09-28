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

/** One hand-written duration as the compiled sheet emits it: a `duration-*`
 *  utility, or an arbitrary property that sets a transition or an animation. */
export interface DurationUtility {
  /** The class as an author writes it, unescaped: the bare-number form, the
   *  bracketed arbitrary form, `duration-(--duration-fast)`, or an arbitrary
   *  property, a property and its value in square brackets as one class.
   *  None is spelled out whole here: this file is scanned as source, and a
   *  whole spelling would put that rule into the sheet it reads. */
  className: string;
  /** The property it sets: `transition-duration` for a `duration-*`
   *  utility, the arbitrary property's own for the other kind, Tailwind's
   *  duration variable included. */
  prop: string;
  /** The value it sets, as written. */
  value: string;
}

/** Tailwind's own duration variable. Every transition utility reads it
 *  (`var(--tw-duration, ...)`), so an arbitrary property that sets it is a
 *  hand-written duration for whatever transition the element also wears,
 *  even though it is not itself one of the properties `kindOf` reads. */
export const TW_DURATION = "--tw-duration";

/** An arbitrary property that carries a duration: one of the four
 *  properties `kindOf` reads, or Tailwind's duration variable, written as a
 *  class of its own. */
const ARBITRARY_DURATION = /^\[(?:(?:transition|animation)(?:-duration)?|--tw-duration):/;

/** A class without its variants and its `!`: cut after the last colon that
 *  sits outside brackets and parentheses, where Tailwind ends a variant. A
 *  colon inside brackets is an arbitrary property's own and stays. */
export function utilityOf(cls: string): string {
  let depth = 0;
  let cut = 0;
  for (let i = 0; i < cls.length; i += 1) {
    const c = cls[i];
    if (c === "[" || c === "(") depth += 1;
    else if (c === "]" || c === ")") depth -= 1;
    else if (c === ":" && depth === 0) cut = i + 1;
  }
  return cls.slice(cut).replace(/^!|!$/g, "");
}

/*
 * There is no roster of exempted `duration-*` classes, and the scanner below
 * tolerates nothing: `ui/select.tsx` reads `--duration-fast`, which was the
 * last numeric duration in the tree and the only thing a roster was ever
 * holding. A component that needs a number again does not get an exemption
 * back, it gets a token - an exemption list is a place for the next one to
 * hide, and this reader is the only thing that would have caught it.
 */

/**
 * Every `duration-*` utility Tailwind emitted into the compiled sheet.
 *
 * This is FIX 1's `.tsx` scanner, and it never reads a `.tsx`. Tailwind only
 * emits `.duration-<x>` when a source file actually uses the class, so the
 * compiled sheet IS the inventory of hand-written durations in the component
 * tree, arrived at through the real pipeline rather than through a grep over
 * source text. A component that ships an arbitrary bracketed duration puts
 * that utility in here, where the test can see it is not one of the tokens.
 *
 * Two spellings used to be invisible here, so a component could carry a
 * hand-written duration with this whole net green. An arbitrary PROPERTY
 * (`transition-duration` and a time in square brackets, as one class) emits
 * its rule under a class that does not start with `duration-`. And a
 * VARIANT (`hover:` in front of a `duration-` utility) puts its name before
 * the utility's and nests its declarations under `&:hover` and a media
 * query, where a rule's own nodes do not reach. Both are read now: the class
 * is taken without its variants, and the declarations are walked. A third
 * spelling sets only Tailwind's duration variable, which the transition
 * utilities read: it is read as the variable it sets.
 */
export function durationUtilities(root: Root): DurationUtility[] {
  const out: DurationUtility[] = [];
  const seen = new Set<string>();
  root.walkRules((rule) => {
    const names = new Set<string>();
    for (const selector of resolvedSelectors(rule)) {
      for (const match of selector.matchAll(/\.((?:\\.|[A-Za-z0-9_-])+)/g)) {
        const cls = match[1].replace(/\\(.)/g, "$1");
        const utility = utilityOf(cls);
        if (utility.startsWith("duration-") || ARBITRARY_DURATION.test(utility)) {
          names.add(cls);
        }
      }
    }
    if (names.size === 0) return;
    const decls: Declaration[] = [];
    rule.walkDecls((decl) => {
      if (kindOf(decl.prop) !== null || decl.prop === TW_DURATION) decls.push(decl);
    });
    if (decls.length === 0) return;
    const { prop } = decls[decls.length - 1];
    const value = decls[decls.length - 1].value.trim();
    for (const className of names) {
      const key = `${className}=${prop}:${value}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ className, prop, value });
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

/* ------------------------------------------------------------------ *
 * WHICH RULES REACH A RENDERED ELEMENT, AND WHICH ONE WINS
 *
 * The scrollbar contract (css-contract.test.ts, T-89) asks cascade
 * questions about elements the components actually render: does any rule
 * declaring `scrollbar-width` match this dialog, and what colour does the
 * sheet paint behind its thumb. jsdom cannot apply the compiled sheet (see
 * glassSurfaceCss.ts) but its selector engine is real, so matching is
 * delegated to `Element.matches` and only the cascade ORDER is modelled
 * here: importance, layers, specificity, source order, as CSS Cascade 5
 * ranks them. Nothing below compares a selector string to a class string.
 * ------------------------------------------------------------------ */

/** Under `@media`, `@supports` or a container query. `@layer` is not a
 *  condition: a layered rule always applies. The at-rule names are not
 *  all spelled out here: Tailwind scans this file as raw text and the
 *  container one is also a utility, which it would emit into the sheet. */
export function conditional(node: ChildNode): boolean {
  return inAtRule(node, (at) => /^(media|supports|container)$/.test(at.name));
}

/** The outermost `@layer` a node sits in, or null when it is unlayered. */
export function layerOf(node: ChildNode): string | null {
  let found: string | null = null;
  let p: Container | undefined = node.parent as Container | undefined;
  while (p && p.type !== "root") {
    if (p.type === "atrule" && (p as AtRule).name === "layer") {
      found = (p as AtRule).params.trim();
    }
    p = p.parent as Container | undefined;
  }
  return found;
}

/** Layer names in cascade order: the order the sheet first mentions them,
 *  whether in an `@layer a, b, c;` statement or as a block. */
export function layerOrder(root: Root): string[] {
  const out: string[] = [];
  root.each((n) => {
    if (n.type !== "atrule" || (n as AtRule).name !== "layer") return;
    for (const name of (n as AtRule).params.split(",")) {
      const trimmed = name.trim();
      if (trimmed && !out.includes(trimmed)) out.push(trimmed);
    }
  });
  return out;
}

export type Specificity = [ids: number, classes: number, elements: number];

export function compareSpecificity(a: Specificity, b: Specificity): number {
  for (let k = 0; k < 3; k += 1) {
    if (a[k] !== b[k]) return a[k] - b[k];
  }
  return 0;
}

/**
 * Selectors Level 4 specificity, read with a small tokenizer rather than a
 * regex because Tailwind's escaped class names (`.max-h-\[85vh\]`) put
 * brackets inside identifiers. `:where()` adds nothing; `:not()`, `:is()`
 * and `:has()` add their most specific argument; a pseudo-element counts
 * as an element; `*` and combinators count as nothing.
 */
export function specificity(selector: string): Specificity {
  let ids = 0;
  let classes = 0;
  let elements = 0;
  const s = selector;
  let i = 0;
  const identifier = () => {
    while (i < s.length) {
      if (s[i] === "\\") {
        i += 2;
        continue;
      }
      if (/[\w-]/.test(s[i])) {
        i += 1;
        continue;
      }
      break;
    }
  };
  const parenthesised = (): string => {
    const start = i;
    let depth = 0;
    for (; i < s.length; i += 1) {
      if (s[i] === "(") depth += 1;
      else if (s[i] === ")") {
        depth -= 1;
        if (depth === 0) {
          i += 1;
          break;
        }
      }
    }
    return s.slice(start + 1, i - 1);
  };
  while (i < s.length) {
    const ch = s[i];
    if (ch === "#") {
      i += 1;
      identifier();
      ids += 1;
    } else if (ch === ".") {
      i += 1;
      identifier();
      classes += 1;
    } else if (ch === "[") {
      while (i < s.length && s[i] !== "]") {
        if (s[i] === "\\") i += 1;
        i += 1;
      }
      i += 1;
      classes += 1;
    } else if (ch === ":" && s[i + 1] === ":") {
      i += 2;
      identifier();
      elements += 1;
    } else if (ch === ":") {
      i += 1;
      const start = i;
      identifier();
      const name = s.slice(start, i);
      if (s[i] !== "(") {
        classes += 1;
        continue;
      }
      const inner = parenthesised();
      if (name === "where") continue;
      if (name === "not" || name === "is" || name === "has") {
        let best: Specificity = [0, 0, 0];
        for (const arg of splitTopLevel(inner, ",")) {
          const sp = specificity(arg);
          if (compareSpecificity(sp, best) > 0) best = sp;
        }
        ids += best[0];
        classes += best[1];
        elements += best[2];
        continue;
      }
      classes += 1;
    } else if (/[A-Za-z_]/.test(ch) || ch === "\\") {
      identifier();
      elements += 1;
    } else {
      i += 1;
    }
  }
  return [ids, classes, elements];
}

/** A selector that paints a box other than the element's own (a
 *  pseudo-element) or one jsdom's engine does not speak (a vendor
 *  pseudo-class). A `::-webkit-scrollbar-thumb` background is the thumb's
 *  colour, never the scroller's, so these are not candidates. */
export function paintsAnotherBox(selector: string): boolean {
  return /::|:-(webkit|moz|ms)-/.test(selector);
}

export interface Candidate {
  selector: string;
  prop: string;
  value: string;
  important: boolean;
  layer: string | null;
  specificity: Specificity;
  /** Position in the sheet, for the last tie-break. */
  order: number;
}

/** How the cascade readers treat a declaration under a condition.
 *  `includeConditional` takes every one, read as "could reach". `viewportPx`
 *  takes the ones whose every enclosing `@media` holds in a window that
 *  wide, which is how a breakpoint variant is asked about. */
export interface CascadeOptions {
  includeConditional?: boolean;
  viewportPx?: number;
  /** Takes a declaration whose only conditions are `@supports` blocks as
   *  holding. The app runs in one engine, Chromium's, and the `@supports`
   *  blocks this sheet carries are Tailwind's own, around the colour-mix an
   *  opacity modifier (a /40 on a text colour, say) compiles to, which that
   *  engine satisfies. Read without them, a dimmed ink is its full-strength
   *  fallback. */
  supports?: boolean;
}

/** What one `rem` or `em` is worth INSIDE a media query: the initial font
 *  size, whatever the root declares, because a query is evaluated before
 *  any rule applies. */
const MEDIA_EM_PX = 16;

/** Tailwind's `md` breakpoint as a window width, which is 48rem in the
 *  queries the compiler writes. The rows that read a size on either side of
 *  it take it from here, and the size they read on each side is what holds
 *  it to the sheet. */
export const MD_FROM_PX = 48 * MEDIA_EM_PX;

/** A length inside a media query, in px, or null for one this cannot read. */
function mediaLengthPx(text: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(px|rem|em)$/.exec(text.trim());
  if (!m) return null;
  return m[2] === "px" ? Number(m[1]) : Number(m[1]) * MEDIA_EM_PX;
}

/** Whether one parenthesised media feature holds in a window `widthPx`
 *  wide. Only the width is read, in the range forms the compiler writes and
 *  the `min-`/`max-` forms written by hand; any other feature does not hold,
 *  so a rule under one is not a candidate for a width question. */
function widthFeatureHolds(feature: string, widthPx: number): boolean {
  const text = feature.trim().replace(/^\(|\)$/g, "").trim();
  const range = /^width\s*(>=|<=|>|<)\s*(.+)$/.exec(text);
  if (range) {
    const px = mediaLengthPx(range[2]);
    if (px === null) return false;
    return range[1] === ">=" ? widthPx >= px : range[1] === "<=" ? widthPx <= px
      : range[1] === ">" ? widthPx > px : widthPx < px;
  }
  const minMax = /^(min|max)-width\s*:\s*(.+)$/.exec(text);
  if (minMax) {
    const px = mediaLengthPx(minMax[2]);
    if (px === null) return false;
    return minMax[1] === "min" ? widthPx >= px : widthPx <= px;
  }
  return false;
}

/** Whether every condition around `node` holds in a window `widthPx` wide:
 *  each enclosing `@media` has one query in its list whose features all
 *  hold. `@supports` and container queries are not evaluated and never
 *  hold. */
function holdsAtWidth(node: ChildNode, widthPx: number): boolean {
  let p: Container | undefined = node.parent as Container | undefined;
  while (p && p.type !== "root") {
    if (p.type === "atrule") {
      const at = p as AtRule;
      if (at.name === "media") {
        const anyQuery = splitTopLevel(at.params, ",").some((query) =>
          query.split(/\s+and\s+/).every((feature) => widthFeatureHolds(feature, widthPx)));
        if (!anyQuery) return false;
      } else if (at.name !== "layer") {
        return false;
      }
    }
    p = p.parent as Container | undefined;
  }
  return true;
}

/**
 * Every unconditional declaration of `prop` whose rule matches `el`, one
 * entry per matching selector, in source order. Whether a selector matches
 * is the DOM's decision (`Element.matches`): `.elysium-page .x` is a
 * descendant question only a tree can answer. A selector the engine cannot
 * parse throws instead of quietly thinning the set.
 *
 * `@media`, `@supports` and container-query declarations are left out, because
 * what wins under a condition jsdom cannot evaluate is not a question the
 * cascade model here can answer. That makes this the wrong reader for a
 * SAFETY question, where a declaration that might apply is exactly what you
 * are looking for: pass `includeConditional` for those, and read the result
 * as "could reach", not "wins".
 */
export function candidatesFor(
  root: Root,
  el: Element,
  prop: RegExp,
  options: CascadeOptions = {},
): Candidate[] {
  const out: Candidate[] = [];
  let order = 0;
  root.walkDecls(prop, (decl) => {
    order += 1;
    if (inKeyframes(decl)) return;
    if (conditional(decl) && options.includeConditional !== true
      && !(options.viewportPx !== undefined && holdsAtWidth(decl, options.viewportPx))
      && !(options.supports === true && onlyUnderSupports(decl))) return;
    // The rule this declaration belongs to, which is NOT always its parent:
    // `.warm-canvas { @media (...) { scrollbar-color: ... } }` is kept
    // verbatim by the compiler, and there the parent is the at-rule. Reading
    // `decl.parent` alone dropped that declaration silently, which made the
    // conditional mode above a half-truth: top-level `@media` was seen and
    // the nested form, the one an author is most likely to write, was not.
    let rule: Container | undefined = decl.parent as Container | undefined;
    while (rule && rule.type === "atrule") rule = rule.parent as Container | undefined;
    if (!rule || rule.type !== "rule") return;
    for (const selector of resolvedSelectors(rule as Rule)) {
      if (paintsAnotherBox(selector)) continue;
      let hit: boolean;
      try {
        hit = el.matches(selector);
      } catch (error) {
        throw new Error(`the selector engine could not read "${selector}"`, {
          cause: error,
        });
      }
      if (!hit) continue;
      out.push({
        selector,
        prop: decl.prop,
        value: decl.value.replace(/\s+/g, " ").trim(),
        important: decl.important === true,
        layer: layerOf(decl),
        specificity: specificity(selector),
        order,
      });
    }
  });
  return out;
}

/**
 * CSS Cascade 5 between two candidates that both match: importance first;
 * then layers, where a normal declaration prefers unlayered over layered
 * and a later layer over an earlier one, and an important declaration
 * prefers the reverse; then specificity; then source order. Positive when
 * `a` wins.
 */
export function cascadeCompare(a: Candidate, b: Candidate, layers: string[]): number {
  if (a.important !== b.important) return a.important ? 1 : -1;
  const rank = (c: Candidate): number => {
    if (c.layer === null) return c.important ? -1 : layers.length;
    const at = layers.indexOf(c.layer);
    const index = at < 0 ? layers.length - 1 : at;
    return c.important ? layers.length - 1 - index : index;
  };
  if (rank(a) !== rank(b)) return rank(a) - rank(b);
  const bySpecificity = compareSpecificity(a.specificity, b.specificity);
  if (bySpecificity !== 0) return bySpecificity;
  return a.order - b.order;
}

/** The candidate the cascade lets paint, or null when nothing matches. */
export function winningDeclaration(
  root: Root,
  el: Element,
  prop: RegExp,
  options: CascadeOptions = {},
): Candidate | null {
  const layers = layerOrder(root);
  let best: Candidate | null = null;
  for (const c of candidatesFor(root, el, prop, options)) {
    if (best === null || cascadeCompare(c, best, layers) > 0) best = c;
  }
  return best;
}

/**
 * The colours a background value actually paints, `var()` resolved: a
 * plain colour is one stop, a gradient is each of its colour stops, and
 * `transparent` or a zero alpha is not paint. Of a multi-layer background
 * only the BOTTOM layer is read (the last one written): the translucent
 * radial highlights a dialog surface stacks on top never cover the whole
 * box, the base layer is what the eye meets everywhere, and it is what a
 * scrollbar thumb has to stand out from.
 */
export function paintedStops(value: string, table: Map<string, string>): string[] {
  const layers = splitTopLevel(resolveVars(value, table), ",");
  const bottom = layers[layers.length - 1] ?? "";
  const out: string[] = [];
  for (const m of bottom.matchAll(/rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}\b/g)) {
    const colour = m[0];
    const alpha =
      /^rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)$/.exec(colour);
    if (alpha && Number(alpha[1]) === 0) continue;
    out.push(colour);
  }
  return out;
}

/**
 * The nearest of `el` and its ancestors that a declaration of `prop`
 * reaches, with the declaration that wins there. This is how an inherited
 * property (`scrollbar-color`) arrives at a scroller that declares none,
 * and where the paint behind an unpainted scroller comes from.
 */
export function nearestDeclaration(
  root: Root,
  el: Element,
  prop: RegExp,
): { element: Element; declaration: Candidate } | null {
  for (let node: Element | null = el; node; node = node.parentElement) {
    const declaration = winningDeclaration(root, node, prop);
    if (declaration) return { element: node, declaration };
  }
  return null;
}

/* ------------------------------------------------------------------
 * The ground a scrollbar is actually drawn over.
 *
 * `paintedStops` reads one background value's bottom layer, which is the
 * right answer for "what colour is this box" and the wrong one for "what
 * is under the bar". A panel paints in layers: its own background, an
 * absolutely positioned veil inside it, a `::before` full of radial
 * highlights at its own opacity, and a `::after` sheen down one edge in
 * `mix-blend-mode: screen`. The bar's track is transparent, so all of it
 * shows through, and the lightest of those layers is where a light thumb
 * comes closest to disappearing.
 *
 * What this module does is find the layers and say where each one is
 * anchored. What each layer PAINTS at the place a bar actually runs is
 * glassSurfaceCss.ts's `layerPaintOver`, because a layer that offers every
 * stop it contains stacks highlights the engine cannot put in one place: a
 * radial centred at 12% of the width never reaches a bar on the right edge,
 * and a shadow that fades out at 46% of the height never reaches the top of
 * one. The two halves together give the ground at a region, which is the
 * claim WCAG 1.4.11 needs and the claim a bottom-layer reading cannot make.
 * ------------------------------------------------------------------ */

/**
 * The layers of a background value, BOTTOM layer first, with every `var()`
 * resolved. CSS writes them top-first and paints them last-first, and a
 * reader that keeps the written order composites the surface upside down.
 *
 * Nothing is dropped here. Whether a layer paints at a given place is a
 * question about its geometry, which `layerPaintOver` answers; a layer that
 * looks like nothing but a `transparent` keyword still covers its box with
 * the colour the stop before it ramps from.
 */
export function backgroundLayers(value: string, table: Map<string, string>): string[] {
  const layers = splitTopLevel(resolveVars(value, table), ",");
  return layers
    .map((layer) => layer.trim())
    .filter((layer) => layer !== "")
    .reverse();
}

/** Where a painted box sits inside the surface. A `::before` with `inset:
 *  0` covers the whole of it; the sidebar's sheen is `left: auto; width:
 *  46px`, a band down the right edge - which is where the bar is, so the
 *  difference decides whether the layer is over the bar at all. */
export type Anchor =
  | { edge: "full" }
  | { edge: "right"; bandPx: number }
  | { edge: "left"; bandPx: number };

/** One painted layer of a surface: the layer value as the sheet declares
 *  it, the box it is anchored in, the opacity multiplying the whole
 *  pseudo-element, whether it is screen-blended, and where it came from. */
export interface PaintLayer {
  /** One background layer, `var()` resolved. What it paints at a place is
   *  `layerPaintOver`'s answer, not a list of stops taken in advance. */
  value: string;
  anchor: Anchor;
  opacity: number;
  screen: boolean;
  /** Pixels cut off the box's RIGHT edge by a `clip-path`, 0 when nothing
   *  clips it. A clip does not move a gradient's stops, so this is not part
   *  of the anchor: the layer is placed and evaluated exactly as before and
   *  then simply is not there in that column. */
  clipRightPx: number;
  source: string;
}

/** The declaration that wins for `prop` on a selector built from `base`
 *  plus a pseudo-element, which `candidatesFor` refuses to consider
 *  because its subject is another box. Here that other box is the point. */
function pseudoDeclarations(
  root: Root,
  el: Element,
  pseudo: string,
  prop: RegExp,
): { value: string; selector: string }[] {
  const out: { value: string; selector: string }[] = [];
  root.walkDecls(prop, (decl) => {
    if (inKeyframes(decl) || conditional(decl)) return;
    let rule: Container | undefined = decl.parent as Container | undefined;
    while (rule && rule.type === "atrule") rule = rule.parent as Container | undefined;
    if (!rule || rule.type !== "rule") return;
    for (const selector of resolvedSelectors(rule as Rule)) {
      if (!selector.endsWith(pseudo)) continue;
      const host = selector.slice(0, -pseudo.length).trim();
      if (host === "") continue;
      let hit: boolean;
      try {
        hit = el.matches(host);
      } catch {
        // A host part jsdom's engine cannot read is not this reader's
        // business; the pseudo-elements it is after are plain class hosts.
        continue;
      }
      if (hit) out.push({ value: decl.value.replace(/\s+/g, " ").trim(), selector });
    }
  });
  return out;
}

/**
 * The value `prop` is given by a rule whose selector list contains exactly
 * `selector`, or null when no unconditional rule does. This is how a
 * pseudo-element's `opacity` is found beside its `background`, and how a
 * layer whose element no test can render - a veil the fog gate takes back
 * out of the DOM - is read from the sheet instead of from the document.
 * Selector equality is on the RESOLVED selector, so a nesting change or a
 * rename comes back null rather than quietly matching something else.
 */
export function declarationOnSelector(
  root: Root,
  selector: string,
  prop: RegExp,
): string | null {
  let found: string | null = null;
  root.walkRules((rule) => {
    if (conditional(rule)) return;
    if (!resolvedSelectors(rule).includes(selector)) return;
    rule.walkDecls(prop, (decl) => {
      found = decl.value.replace(/\s+/g, " ").trim();
    });
  });
  return found;
}

/** A sibling declaration on the same rule as `selector`, e.g. the
 *  `opacity` that goes with a `::before` background. */
function companionValue(root: Root, selector: string, prop: RegExp): string | null {
  return declarationOnSelector(root, selector, prop);
}

const PAINT_PROP = /^background(-color)?$/;
const OPACITY_PROP = /^opacity$/;
const BLEND_PROP = /^mix-blend-mode$/;
const POSITION_PROP = /^position$/;
const INSET_PROP = /^inset$/;
const LEFT_PROP = /^left$/;
const RIGHT_PROP = /^right$/;
const WIDTH_PROP = /^width$/;
const CLIP_PROP = /^clip-path$/;
/** Everything that can take a bite out of a painted box, both spellings. */
const CUT_PROP = /^(-webkit-)?(clip-path|clip|mask|mask-image)$/;
const FONT_SIZE_PROP = /^font-size$/;

/** What one `rem` is worth in this sheet. Nothing here sets a root font
 *  size, so it is the browser's own 16px; if something ever does, it is
 *  read rather than assumed, and a unit this cannot turn into pixels stops
 *  the run instead of being guessed at. */
export function remPx(root: Root): number {
  for (const selector of ["html", ":root"]) {
    const declared = declarationOnSelector(root, selector, FONT_SIZE_PROP);
    if (declared === null) continue;
    const px = /^(\d+(?:\.\d+)?)px$/.exec(declared);
    if (!px) {
      throw new Error(
        `${selector} sets a root font size of "${declared}", which this reader ` +
          "cannot turn into pixels; teach it that unit before trusting a rem",
      );
    }
    return Number(px[1]);
  }
  return 16;
}

/**
 * Every declaration on `selector` that takes a bite out of the box it
 * paints: a clip or a mask, in either the standard or the `-webkit-`
 * spelling. Returned as `prop: value` strings so a failure can print what
 * it found, and empty when the box paints whole.
 *
 * A mask is in the list because it cuts the same way a clip does and is one
 * substitution away from it: a guard that only knew `clip-path` would go on
 * reporting an uncut box while `mask-image` took the same column out.
 */
export function clipsAndMasksOn(root: Root, selector: string): string[] {
  const found: string[] = [];
  root.walkRules((rule) => {
    if (conditional(rule)) return;
    if (!resolvedSelectors(rule).includes(selector)) return;
    rule.walkDecls(CUT_PROP, (decl) => {
      const value = decl.value.replace(/\s+/g, " ").trim();
      // `none` is the initial value written out; it cuts nothing.
      if (value === "none") return;
      found.push(`${decl.prop}: ${value}`);
    });
  });
  return found;
}

/**
 * A length the sheet writes, in pixels. `px` and `rem` outright, and the
 * one arithmetic shape Tailwind's spacing scale compiles to,
 * `calc(<length> * <number>)` in either order. Anything else throws: a
 * length silently read as zero would move a measurement band without
 * saying so, and that is the failure this whole reader exists to prevent.
 */
export function lengthPx(value: string, rem: number): number {
  const text = value.trim();
  if (text === "0") return 0;
  const px = /^(-?\d+(?:\.\d+)?)px$/.exec(text);
  if (px) return Number(px[1]);
  const inRem = /^(-?\d+(?:\.\d+)?)rem$/.exec(text);
  if (inRem) return Number(inRem[1]) * rem;
  const calc = /^calc\(\s*(.+?)\s*\*\s*(.+?)\s*\)$/.exec(text);
  if (calc) {
    const [, left, right] = calc;
    const asNumber = (s: string): number | null =>
      /^-?\d+(?:\.\d+)?$/.test(s) ? Number(s) : null;
    const leftNumber = asNumber(left);
    const rightNumber = asNumber(right);
    if (leftNumber !== null && rightNumber === null) return leftNumber * lengthPx(right, rem);
    if (rightNumber !== null && leftNumber === null) return lengthPx(left, rem) * rightNumber;
  }
  throw new Error(`"${value}" is a length this reader cannot turn into pixels`);
}

/** `font-size`, and the `font` shorthand that competes with it. */
const FONT_SIZE_OR_SHORTHAND = /^font(-size)?$/;

/**
 * The size `el`'s words render at, in px: the cascade on the element, and
 * inheritance when it declares none.
 *
 * `font-size` inherits, so the winning declaration is looked for on the
 * element first and then on each ancestor in turn. `inherit` passes the
 * question to the parent, and so does the `font` shorthand written as
 * `inherit` - which is how the preflight hands a button its parent's size;
 * any other shorthand value is refused rather than parsed. `em` and `%` are
 * taken against the parent's size, the way the engine takes them. A tree that
 * declares nothing all the way up is at the root size.
 *
 * An inline `style` is part of the cascade too: its `font-size` outranks
 * every sheet rule that is not `!important`, so a word sized inline is read
 * at that size, and an inline `font` shorthand is refused like a sheet one.
 *
 * Built for sweeps: a row that asks "is any button in this tab below 13"
 * needs an answer for every button, whichever of these ways it gets its
 * size, and a reader that skipped the ones it could not resolve would report
 * a clean tab over the ones it never read.
 *
 * With `viewportPx` the size is the one a window that wide gets: a
 * breakpoint variant's `@media` rule competes when its width holds, and
 * with no width a conditional rule never does.
 */
export function fontSizePx(
  root: Root,
  table: Map<string, string>,
  rem: number,
  el: Element,
  options: { viewportPx?: number } = {},
): number {
  const won = winningDeclaration(root, el, FONT_SIZE_OR_SHORTHAND, options);
  const parent = (): number =>
    el.parentElement ? fontSizePx(root, table, rem, el.parentElement, options) : rem;
  const inline = won?.important === true ? new Map<string, string>() : inlineDeclarations(el);
  if (inline.has("font")) {
    throw new Error(
      `an inline style sets the font shorthand to "${inline.get("font")}", which this ` +
        "reader does not take apart; teach it that value before trusting a size",
    );
  }
  const inlineSize = inline.get("font-size");
  if (won === null && inlineSize === undefined) return parent();
  const value = resolveVars(inlineSize ?? won!.value, table).trim();
  if (value === "inherit") return parent();
  if (inlineSize === undefined && won!.prop === "font") {
    throw new Error(
      `${won!.selector} sets the font shorthand to "${value}", which this reader ` +
        "does not take apart; teach it that value before trusting a size",
    );
  }
  const em = /^(\d+(?:\.\d+)?)em$/.exec(value);
  if (em) return Number(em[1]) * parent();
  const percent = /^(\d+(?:\.\d+)?)%$/.exec(value);
  if (percent) return (Number(percent[1]) / 100) * parent();
  return lengthPx(value, rem);
}

/* ------------------------------------------------------------------
 * The ink words are painted in.
 *
 * `color` inherits as a COMPUTED value: an element that declares none gets
 * the colour its parent already resolved, and a `var()` inside it was
 * substituted at the element that declared it. That is the whole of the
 * trap on the right panel: the body resolves `var(--foreground)` against
 * the root's dark-theme value, the panel redefines `--foreground` further
 * down, and a line with no colour of its own inherits the body's result,
 * which the redefinition can never reach. A reader that resolved `var()`
 * against the root table alone, or against the element being read, would
 * report either the defect everywhere or nowhere, so these resolve every
 * reference where the engine does.
 * ------------------------------------------------------------------ */

/** Where an element's words get their colour. */
export interface Ink {
  /** The colour as the winning declaration writes it, every `var()`
   *  resolved. `canvastext`, the initial value, when nothing up the tree
   *  declares one. */
  colour: string;
  /** The element whose declaration decided it, null for the initial value. */
  from: Element | null;
  /** That declaration's value as written, before any `var()` in it is
   *  resolved: a token reference follows whichever surface resolves it, a
   *  literal is the same colour everywhere. null for the initial value. */
  written: string | null;
}

const escapeForRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const NON_INHERITING = new WeakMap<Root, Map<string, string | null>>();

/** Custom properties registered with `@property` and `inherits: false`, with
 *  their initial value (null when none is given). Tailwind registers its own
 *  bookkeeping properties this way; such a property set on an ancestor never
 *  reaches a descendant. */
function nonInheriting(root: Root): Map<string, string | null> {
  const known = NON_INHERITING.get(root);
  if (known) return known;
  const out = new Map<string, string | null>();
  root.walkAtRules("property", (at) => {
    let inherits = true;
    let initial: string | null = null;
    at.each((node) => {
      if (node.type !== "decl") return;
      if (node.prop === "inherits") inherits = node.value.trim() !== "false";
      if (node.prop === "initial-value") initial = node.value.trim();
    });
    if (!inherits) out.set(at.params.trim(), initial);
  });
  NON_INHERITING.set(root, out);
  return out;
}

/** What `el` itself specifies for `prop`: its inline style over the sheet,
 *  unless the sheet's winner is important. undefined when neither says. */
function specifiedOn(
  root: Root,
  el: Element,
  prop: string,
  options: CascadeOptions = {},
): string | undefined {
  const won = winningDeclaration(root, el, new RegExp(`^${escapeForRegExp(prop)}$`), options);
  if (won?.important === true) return won.value;
  return inlineDeclarations(el).get(prop) ?? won?.value;
}

/** The first comma outside every parenthesis, or -1. */
function firstTopLevelComma(text: string): number {
  let depth = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "(") depth += 1;
    else if (text[i] === ")") depth -= 1;
    else if (text[i] === "," && depth === 0) return i;
  }
  return -1;
}

/**
 * The computed value of the custom property `name` at `el`: its own
 * declaration or the nearest ancestor's, with every `var()` inside it
 * substituted at the element that declares it. null is the guaranteed
 * invalid value: nothing up the tree declares it (or the references form a
 * loop).
 */
export function customPropertyAt(
  root: Root,
  el: Element,
  name: string,
  resolving: ReadonlySet<string> = new Set(),
): string | null {
  const inherits = !nonInheriting(root).has(name);
  for (let node: Element | null = el; node; node = node.parentElement) {
    const own = specifiedOn(root, node, name);
    if (own !== undefined) {
      if (resolving.has(name)) return null;
      return substituteAt(root, node, own, new Set([...resolving, name]));
    }
    if (!inherits) break;
  }
  return nonInheriting(root).get(name) ?? null;
}

/**
 * Every `var(--name, fallback)` in `value`, substituted at `el`. null when a
 * reference resolves to nothing and carries no fallback, which makes the
 * whole declaration invalid at computed-value time.
 */
export function substituteAt(
  root: Root,
  el: Element,
  value: string,
  resolving: ReadonlySet<string> = new Set(),
): string | null {
  let out = "";
  let i = 0;
  while (i < value.length) {
    const at = value.indexOf("var(", i);
    if (at < 0) {
      out += value.slice(i);
      break;
    }
    out += value.slice(i, at);
    let depth = 0;
    let end = at + 3;
    for (; end < value.length; end += 1) {
      if (value[end] === "(") depth += 1;
      else if (value[end] === ")") {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    if (end >= value.length) throw new Error(`an unclosed var() in "${value}"`);
    const inner = value.slice(at + 4, end);
    const comma = firstTopLevelComma(inner);
    const name = (comma < 0 ? inner : inner.slice(0, comma)).trim();
    let replacement = customPropertyAt(root, el, name, resolving);
    if (replacement === null && comma >= 0) {
      replacement = substituteAt(root, el, inner.slice(comma + 1).trim(), resolving);
    }
    if (replacement === null) return null;
    out += replacement;
    i = end + 1;
  }
  return out.trim();
}

/**
 * The colour `el`'s words are painted in, and the element that decided it.
 *
 * The cascade on the element first, its inline style included (it outranks
 * every sheet rule that is not important). No declaration, `inherit`,
 * `unset`, `currentcolor`, or a value made invalid by a `var()` that
 * resolves to nothing: the question passes to the parent, which is how
 * `color` inherits. Nothing all the way up is the initial value. `initial`
 * and the two `revert` keywords are refused rather than guessed at. The
 * `@supports` blocks the engine satisfies are read with the rest, so an ink
 * dimmed with an opacity modifier is the mix the screen shows, not the
 * fallback written before it.
 *
 * Pass one `memo` across a sweep: every text node in a tab shares most of
 * its ancestors, and each ancestor is read once.
 */
export function inkOf(root: Root, el: Element, memo?: Map<Element, Ink>): Ink {
  const known = memo?.get(el);
  if (known) return known;
  const inherited = (): Ink =>
    el.parentElement
      ? inkOf(root, el.parentElement, memo)
      : { colour: "canvastext", from: null, written: null };
  const own = specifiedOn(root, el, "color", { supports: true });
  let ink: Ink;
  if (own === undefined) {
    ink = inherited();
  } else {
    const value = substituteAt(root, el, own);
    if (value === null || /^(inherit|unset|currentcolor)$/i.test(value)) {
      ink = inherited();
    } else if (/^(initial|revert|revert-layer)$/i.test(value)) {
      throw new Error(
        `the colour on this element is "${value}", which this reader does not ` +
          "resolve; teach it that keyword before trusting an ink",
      );
    } else {
      ink = { colour: value, from: el, written: own.trim() };
    }
  }
  memo?.set(el, ink);
  return ink;
}

/** Whether every condition around `decl` is an `@supports` (or a layer).
 *  The app runs in one engine, Chromium's, and every `@supports` block this
 *  sheet puts around a `::placeholder` rule (Tailwind's own, for the colour
 *  it gives every placeholder) is one that engine satisfies; a `@media` or
 *  a container query is a question about the window, and is not taken. */
function onlyUnderSupports(decl: ChildNode): boolean {
  for (let p = decl.parent as Container | undefined; p; p = p.parent as Container | undefined) {
    if (p.type !== "atrule") continue;
    const name = (p as AtRule).name;
    if (name !== "supports" && name !== "layer") return false;
  }
  return true;
}

/** The winning declaration of `prop` among the `::placeholder` rules whose
 *  host matches `field`, `@supports` blocks included. */
function placeholderWinner(root: Root, field: Element, prop: RegExp): Candidate | null {
  const layers = layerOrder(root);
  let best: Candidate | null = null;
  let order = 0;
  root.walkDecls(prop, (decl) => {
    order += 1;
    if (inKeyframes(decl) || !onlyUnderSupports(decl)) return;
    let rule: Container | undefined = decl.parent as Container | undefined;
    while (rule && rule.type === "atrule") rule = rule.parent as Container | undefined;
    if (!rule || rule.type !== "rule") return;
    for (const selector of resolvedSelectors(rule as Rule)) {
      if (!selector.endsWith("::placeholder")) continue;
      const host = selector.slice(0, -"::placeholder".length).trim();
      let hit: boolean;
      try {
        hit = host === "" ? true : field.matches(host);
      } catch (error) {
        throw new Error(`the selector engine could not read "${host}"`, { cause: error });
      }
      if (!hit) continue;
      const candidate: Candidate = {
        selector,
        prop: decl.prop,
        value: decl.value.replace(/\s+/g, " ").trim(),
        important: decl.important === true,
        layer: layerOf(decl),
        specificity: specificity(selector),
        order,
      };
      if (best === null || cascadeCompare(candidate, best, layers) > 0) best = candidate;
    }
  });
  return best;
}

/**
 * The colour a text field's placeholder is painted in, and the field that
 * decided it. A `::placeholder` rule is read at its host: the part before
 * the pseudo-element has to match the field, and the cascade between such
 * rules is the ordinary one, with the `@supports` blocks the engine
 * satisfies taken in. `currentcolor` in it is the field's own ink, which is
 * what Tailwind's preflight mixes at half strength for every placeholder
 * that names no colour of its own. An opacity on the placeholder is laid
 * into the colour, so the ink a ratio is taken from is the ink on screen.
 * With no rule at all the placeholder follows the field's own colour.
 */
export function placeholderInkOf(root: Root, field: Element): Ink {
  const colour = placeholderWinner(root, field, /^color$/);
  const own = inkOf(root, field);
  let ink: Ink = own;
  if (colour !== null) {
    const value = substituteAt(root, field, colour.value);
    if (value !== null && !/^(inherit|unset)$/i.test(value)) {
      ink = {
        colour: value.replace(/currentcolor/gi, own.colour),
        from: field,
        written: colour.value,
      };
    }
  }
  const opacity = placeholderWinner(root, field, OPACITY_PROP);
  const faded = opacity === null ? 1 : parseOpacity(substituteAt(root, field, opacity.value) ?? "1");
  if (faded < 1) {
    ink = { ...ink, colour: `color-mix(in srgb, ${ink.colour} ${faded * 100}%, transparent)` };
  }
  return ink;
}

/* ------------------------------------------------------------------
 * What a line's ink is laid over, and how much of it reaches the eye.
 *
 * KURAL 2.4b measures a text colour with its opacity applied against the
 * ground under it. Two things stand between the ink the cascade names and
 * the pixel on screen, and a reader that skips either certifies a line
 * nobody can read: the opacity of the element and of every ancestor (an
 * `opacity-70` span, a disabled button's 50%), which the engine applies to
 * everything that element paints; and the paint of every element between
 * the surface and the words (a card, a tinted notice, a button's fill).
 * These read both; glassSurfaceCss.ts's `wordsOverStack` composites them.
 * ------------------------------------------------------------------ */

/** An opacity as written, a number or a percentage, clamped to 0..1 the way
 *  the engine clamps it. Anything else throws rather than reading as 1. */
export function parseOpacity(value: string): number {
  const text = value.trim();
  const m = /^(-?\d*\.?\d+)(%?)$/.exec(text);
  if (!m) throw new Error(`"${value}" is not an opacity this reader can read`);
  const n = Number(m[1]) / (m[2] === "%" ? 100 : 1);
  return Math.min(1, Math.max(0, n));
}

/** What `el` itself writes for a family of properties, and which member
 *  of the family wrote it: its inline style over the sheet's winner, unless
 *  that winner is important. The inline declaration written last is the one
 *  that stands. null when neither says anything. */
export function ownDeclaration(
  root: Root,
  el: Element,
  family: RegExp,
): { prop: string; value: string } | null {
  const won = winningDeclaration(root, el, family);
  if (won?.important === true) return { prop: won.prop, value: won.value };
  const inline = [...inlineDeclarations(el).entries()].filter(([prop]) => family.test(prop));
  if (inline.length > 0) {
    const [prop, value] = inline[inline.length - 1];
    return { prop, value };
  }
  return won === null ? null : { prop: won.prop, value: won.value };
}

/** The value `ownDeclaration` finds, on its own. */
function ownValueOf(root: Root, el: Element, family: RegExp): string | null {
  return ownDeclaration(root, el, family)?.value ?? null;
}

/** The opacity `el` itself is painted at, 1 when nothing sets one. A `var()`
 *  in it is resolved at `el`. */
export function ownOpacity(root: Root, el: Element): number {
  const written = ownValueOf(root, el, OPACITY_PROP);
  if (written === null) return 1;
  const value = substituteAt(root, el, written);
  if (value === null || /^(initial|unset|inherit|revert|revert-layer)$/i.test(value)) return 1;
  return parseOpacity(value);
}

const NAMED_PAINT: Record<string, string> = {
  transparent: "rgba(0, 0, 0, 0)",
  white: "#FFFFFF",
  black: "#000000",
};

/** The words a background layer or a gradient can carry that are not a
 *  colour: repeats, positions, sizes, boxes, attachments, a gradient's
 *  direction and shape, and the colour space it interpolates in. */
const NOT_A_COLOUR = new Set([
  "repeat", "repeat-x", "repeat-y", "no-repeat", "space", "round",
  "center", "top", "bottom", "left", "right", "auto", "cover", "contain",
  "scroll", "fixed", "local", "border-box", "padding-box", "content-box", "text", "none",
  "to", "at", "circle", "ellipse", "closest-side", "closest-corner", "farthest-side",
  "farthest-corner", "from", "in", "srgb", "srgb-linear", "display-p3", "lab", "lch",
  "oklab", "oklch", "hsl", "hwb", "xyz", "xyz-d50", "xyz-d65", "shorter", "longer",
  "increasing", "decreasing", "hue",
]);

/** A token of a background layer that is a colour, or null for one that is
 *  not (a position, a size, `no-repeat`). `currentcolor` comes back as the
 *  `ink` given, which is the element's own text colour. Any other bare word
 *  is a named colour this does not know, and throws: dropped, it would be a
 *  ground left out. */
function colourToken(token: string, ink: () => string): string | null {
  const t = token.trim();
  if (/^(#[0-9a-fA-F]{3,8}|rgba?\(.*\)|color-mix\(.*\))$/.test(t)) return t;
  const word = t.toLowerCase();
  const named = NAMED_PAINT[word];
  if (named !== undefined) return named;
  if (word === "currentcolor") return ink();
  if (/^[a-z][a-z-]*$/.test(word) && !NOT_A_COLOUR.has(word)) {
    throw new Error(`"${t}" is a colour name this reader does not know; teach it the value`);
  }
  return null;
}

/**
 * Every colour `el` itself can put under its words: its winning `background`
 * or `background-color`, its inline style included, every `var()` resolved
 * at `el`. [] when it paints nothing; one colour for a flat fill; for a
 * gradient each of its stops, because a line can sit over any of them.
 * `currentcolor` is the element's own ink. An image, a colour syntax this
 * does not read, or a colour name it does not know throws: a ground left
 * out is a green reading of a line that was never measured.
 */
export function ownPaints(root: Root, el: Element): string[] {
  const written = ownValueOf(root, el, PAINT_PROP);
  if (written === null) return [];
  const value = substituteAt(root, el, written);
  if (value === null || /^(none|initial|unset|inherit|transparent)$/i.test(value.trim())) return [];
  const out: string[] = [];
  const ink = (): string => inkOf(root, el).colour;
  for (const layer of splitTopLevel(value, ",")) {
    for (const token of tokensOf(layer.trim())) {
      if (/^url\(/i.test(token) || /^image-set\(/i.test(token)) {
        throw new Error(`"${value}" paints an image, which this reader cannot put a colour to`);
      }
      const gradient = /^(repeating-)?(linear|radial|conic)-gradient\((.*)\)$/i.exec(token);
      if (gradient) {
        for (const part of splitTopLevel(gradient[3], ",")) {
          for (const bit of tokensOf(part.trim())) {
            const colour = colourToken(bit, ink);
            if (colour !== null) out.push(colour);
          }
        }
        continue;
      }
      const colour = colourToken(token, ink);
      if (colour !== null) out.push(colour);
      else if (/^[a-z-]+\(/i.test(token)) {
        throw new Error(`"${token}" in "${value}" is a paint this reader cannot read`);
      }
    }
  }
  return out;
}

/** One element between a surface and a line of words, as the engine paints
 *  it: its own opacity, and the colours it can lay under the words. */
export interface StackStep {
  element: Element;
  opacity: number;
  paints: string[];
}

/**
 * The elements from the first one inside `surface` down to `el`, outermost
 * first, each with its own opacity and its paint. The surface's own paint is
 * the ground this stack sits on, which KURAL 2.4b defines, so it is not a
 * step; a surface that is itself translucent, or sits in one, is not a
 * ground that definition covers, and throws.
 *
 * Pass one `memo` across a sweep, as with `inkOf`: every line in a tab
 * shares most of its ancestors, and each is read once.
 */
export function stackOf(
  root: Root,
  surface: Element,
  el: Element,
  memo?: Map<Element, StackStep>,
): StackStep[] {
  if (!surface.contains(el)) throw new Error("stackOf was given an element outside its surface");
  for (let node: Element | null = surface; node; node = node.parentElement) {
    const opacity = ownOpacity(root, node);
    if (opacity !== 1) {
      throw new Error(`the surface is painted at opacity ${opacity} by <${node.tagName.toLowerCase()}>, `
        + "which the two-stop ground does not model");
    }
  }
  const steps: StackStep[] = [];
  for (let node: Element | null = el; node && node !== surface; node = node.parentElement) {
    let step = memo?.get(node);
    if (!step) {
      step = { element: node, opacity: ownOpacity(root, node), paints: ownPaints(root, node) };
      memo?.set(node, step);
    }
    steps.unshift(step);
  }
  return steps;
}

/** The side of a box shorthand that faces right, for a value written with
 *  one to four parts. `padding: 4px 8px` puts 8px on the right. */
function rightOfBox(value: string, rem: number): number {
  const parts = splitTopLevel(value, " ").filter((p) => p.trim() !== "");
  if (parts.length === 1) return lengthPx(parts[0], rem);
  if (parts.length <= 4) return lengthPx(parts[1], rem);
  throw new Error(`"${value}" is not a box shorthand this reader can read`);
}

/** The right-hand side of one physical edge (`padding`, `margin`) of `el`,
 *  as the cascade leaves it: every declaration of the family that matches,
 *  applied weakest first, so a `padding-inline` utility in a later layer
 *  beats the `padding: 0` of the reset the way the browser has it. */
function edgeRightPx(
  root: Root,
  el: Element,
  edge: "padding" | "margin",
  rem: number,
  table: Map<string, string>,
): number {
  const family = new RegExp(`^${edge}(-inline|-inline-end|-right)?$`);
  const layers = layerOrder(root);
  const applied = candidatesFor(root, el, family).sort((a, b) =>
    cascadeCompare(a, b, layers),
  );
  let right = 0;
  const apply = (prop: string, raw: string): void => {
    // Tailwind writes its spacing scale as `calc(var(--spacing) * n)`, so
    // the token table the browser resolves against is resolved here first.
    const value = resolveVars(raw, table);
    // `-inline` takes up to two values, start then end; the rest are a box
    // shorthand or a single length on the right edge itself.
    if (prop.endsWith("-inline")) {
      const parts = splitTopLevel(value, " ").filter((p) => p.trim() !== "");
      right = lengthPx(parts.length > 1 ? parts[1] : parts[0], rem);
    } else if (prop === edge) {
      right = rightOfBox(value, rem);
    } else {
      right = lengthPx(value, rem);
    }
  };
  for (const candidate of applied) apply(candidate.prop, candidate.value);
  // An inline style outranks every sheet rule that is not `!important`, and
  // the app writes real geometry there (the sidebar's own right border).
  const inline = inlineDeclarations(el);
  for (const prop of [edge, `${edge}-inline`, `${edge}-inline-end`, `${edge}-right`]) {
    const value = inline.get(prop);
    if (value === undefined) continue;
    apply(prop, value);
  }
  return right;
}

/** The width of `el`'s right border, from the sheet and from its inline
 *  style. A border with no style is not drawn and takes no room, which is
 *  what `border-style: none` means and what an element with a width and no
 *  style gets. */
function borderRightPx(
  root: Root,
  el: Element,
  rem: number,
  table: Map<string, string>,
): number {
  const layers = layerOrder(root);
  const family = /^border(-right|-inline-end)?(-width|-style)?$/;
  const applied = candidatesFor(root, el, family).sort((a, b) =>
    cascadeCompare(a, b, layers),
  );
  let width = 0;
  let style = "none";
  const take = (prop: string, raw: string): void => {
    const value = resolveVars(raw, table);
    if (prop.endsWith("-style")) {
      style = prop === "border-style" ? rightWordOfBox(value) : value.trim();
      return;
    }
    if (prop.endsWith("-width")) {
      width = prop === "border-width" ? rightOfBox(value, rem) : lengthPx(value, rem);
      return;
    }
    // A shorthand: `1px solid <colour>`, in any order, and `border-right`
    // and `border` put the same value on this edge.
    const parts = splitTopLevel(value, " ").filter((p) => p.trim() !== "");
    let sawWidth = false;
    for (const part of parts) {
      if (BORDER_STYLES.has(part)) {
        style = part;
        continue;
      }
      if (sawWidth) continue;
      try {
        width = lengthPx(part, rem);
        sawWidth = true;
      } catch {
        // A colour, including a `var()` the sheet has not resolved here.
      }
    }
    if (!sawWidth) width = 3; // `medium`, the initial value, when only a style is written
  };
  for (const candidate of applied) take(candidate.prop, candidate.value);
  const inline = inlineDeclarations(el);
  for (const prop of [
    "border",
    "border-width",
    "border-style",
    "border-inline-end",
    "border-inline-end-width",
    "border-inline-end-style",
    "border-right",
    "border-right-width",
    "border-right-style",
  ]) {
    const value = inline.get(prop);
    if (value !== undefined) take(prop, value);
  }
  return style === "none" || style === "hidden" ? 0 : width;
}

const BORDER_STYLES = new Set([
  "none",
  "hidden",
  "dotted",
  "dashed",
  "solid",
  "double",
  "groove",
  "ridge",
  "inset",
  "outset",
]);

/** The right-hand word of a one-to-four-part keyword shorthand. */
function rightWordOfBox(value: string): string {
  const parts = value.trim().split(/\s+/);
  return parts.length === 1 ? parts[0] : parts[1];
}

/** An element's own `style` attribute, parsed. jsdom's own
 *  `CSSStyleDeclaration` drops a shorthand it cannot fully parse - the
 *  sidebar writes `border-right: 1px solid var(...)` and reading
 *  `style.borderRightWidth` back gives an empty string - so the attribute
 *  text is read directly rather than through it. */
function inlineDeclarations(el: Element): Map<string, string> {
  const out = new Map<string, string>();
  const text = el.getAttribute("style");
  if (!text) return out;
  for (const part of splitTopLevel(text, ";")) {
    const at = part.indexOf(":");
    if (at < 0) continue;
    out.set(part.slice(0, at).trim().toLowerCase(), part.slice(at + 1).trim());
  }
  return out;
}

/**
 * How far the right edge of `from`'s PADDING box sits inboard of the right
 * edge of `to`'s padding box, in pixels, walking the tree the components
 * really rendered.
 *
 * This is where a scrollbar's column comes from. A scrollbar is drawn
 * inside its scroller's padding box, against that box's right edge, so the
 * distance from the surface the ground is measured on to the bar itself is
 * the wrappers' padding, the elements' margins and any border between the
 * two - none of which is visible from the scroller's own selector.
 *
 * `to`'s own border is deliberately NOT counted. The surface's decoration
 * is drawn in its padding box (`position: absolute; inset: 0` resolves
 * there), which is the box the bands are measured over, and counting the
 * border as well would move the band one pixel further from the panel's lit
 * edge, which is the kinder direction.
 */
export function insetRightPx(
  root: Root,
  from: Element,
  to: Element,
  rem: number,
  table: Map<string, string>,
): number {
  if (!to.contains(from)) {
    throw new Error("insetRightPx was given two elements that are not on one path");
  }
  let inset = borderRightPx(root, from, rem, table);
  let node: Element = from;
  while (node !== to) {
    inset += edgeRightPx(root, node, "margin", rem, table);
    const parent = node.parentElement;
    if (!parent) throw new Error("insetRightPx walked off the top of the tree");
    inset += edgeRightPx(root, parent, "padding", rem, table);
    if (parent !== to) inset += borderRightPx(root, parent, rem, table);
    node = parent;
  }
  return inset;
}

/**
 * Pixels cut off the RIGHT edge of the box carrying `selector`.
 *
 * Only `inset()` with four lengths is read, and only when the three other
 * sides are zero: a clip that also cuts the top, the bottom or the left
 * moves which part of the layer is over the bar in a direction this reader
 * does not model, and a shape function it cannot measure at all must stop
 * the run rather than be read as "nothing is clipped".
 */
export function clipRightPx(root: Root, selector: string, rem: number): number {
  const declared = companionValue(root, selector, CLIP_PROP);
  if (declared === null) return 0;
  const inset = /^inset\(\s*([^)]*?)\s*\)$/.exec(declared);
  if (!inset) {
    throw new Error(
      `${selector} is clipped with "${declared}", a shape this reader cannot measure`,
    );
  }
  const sides = inset[1].split(/\s+/);
  if (sides.length !== 4) {
    throw new Error(
      `${selector} writes its clip as "${declared}"; this reader reads the ` +
        "four-length form only, so the right edge is unambiguous",
    );
  }
  const asPx = (side: string): number => {
    if (side === "0") return 0;
    const px = /^(\d+(?:\.\d+)?)px$/.exec(side);
    if (px) return Number(px[1]);
    const inRem = /^(\d+(?:\.\d+)?)rem$/.exec(side);
    if (inRem) return Number(inRem[1]) * rem;
    throw new Error(
      `${selector} clips by "${side}", a length this reader cannot turn into pixels`,
    );
  };
  const [top, right, bottom, left] = sides.map(asPx);
  if (top !== 0 || bottom !== 0 || left !== 0) {
    throw new Error(
      `${selector} clips on more than its right edge ("${declared}"); which part of ` +
        "the layer is still over the bar is then a question this reader cannot answer",
    );
  }
  return right;
}

/**
 * Where the box carrying `selector` sits. `inset: 0` with neither side
 * overridden is the whole surface; `left: auto` with a pixel width is a
 * band down the right edge and `right: auto` with one a band down the left.
 * Anything else throws rather than being read as full-bleed: a layer placed
 * wrongly is a ground measured wrongly, and a light band silently widened to
 * the whole panel lifts every ground behind the bar.
 */
function anchorOf(root: Root, selector: string): Anchor {
  const left = companionValue(root, selector, LEFT_PROP);
  const right = companionValue(root, selector, RIGHT_PROP);
  const width = companionValue(root, selector, WIDTH_PROP);
  const band = width === null ? null : /^(\d+(?:\.\d+)?)px$/.exec(width);
  if (left === "auto" || right === "auto") {
    if (!band) {
      throw new Error(
        `${selector} releases one side without a pixel width ("${width}"), ` +
          "so this reader cannot say what part of the surface it covers",
      );
    }
    return { edge: left === "auto" ? "right" : "left", bandPx: Number(band[1]) };
  }
  if (left !== null || right !== null) {
    throw new Error(
      `${selector} places itself with left/right ("${left}" / "${right}"), ` +
        "a shape this reader does not speak",
    );
  }
  return { edge: "full" };
}

/**
 * How wide a scrollbar this sheet draws, in pixels: the widest width any
 * unconditional `::-webkit-scrollbar` rule gives one.
 *
 * It is a MAXIMUM on purpose. The band the bar's ground is measured over has
 * to contain the bar, and the cell at the surface's right edge is in the band
 * whatever the width is, so widening it only adds cells further inboard and
 * the envelope over the band can only get worse or stay the same. Reading the
 * narrowest would be the direction that quietly drops the part of the bar
 * nothing measured.
 */
export function scrollbarWidthPx(root: Root, rem: number): number {
  let widest = 0;
  root.walkRules((rule) => {
    if (conditional(rule)) return;
    if (!resolvedSelectors(rule).some((s) => s.endsWith("::-webkit-scrollbar"))) return;
    rule.walkDecls(WIDTH_PROP, (decl) => {
      const value = decl.value.trim();
      const px = /^(\d+(?:\.\d+)?)px$/.exec(value);
      const inRem = /^(\d+(?:\.\d+)?)rem$/.exec(value);
      if (!px && !inRem) {
        throw new Error(
          `${rule.selector} draws a bar "${value}" wide, a length this reader ` +
            "cannot turn into pixels",
        );
      }
      const asPx = px ? Number(px[1]) : Number((inRem as RegExpExecArray)[1]) * rem;
      if (asPx > widest) widest = asPx;
    });
  });
  if (widest === 0) {
    throw new Error("no ::-webkit-scrollbar rule in this sheet gives the bar a width");
  }
  return widest;
}

/**
 * Every layer painted behind a scroller, in paint order: the nearest
 * painted ancestor's own background layers, any absolutely positioned
 * full-bleed element rendered inside that ancestor (a mist veil), and its
 * `::before` and `::after`. Pseudo-elements carry their own `opacity` and
 * their `mix-blend-mode`.
 */
export function paintLayersBehind(
  root: Root,
  el: Element,
  table: Map<string, string>,
): { surface: Element; surfaceRule: string; layers: PaintLayer[] } | null {
  const paint = nearestDeclaration(root, el, PAINT_PROP);
  if (!paint) return null;
  const surface = paint.element;
  const surfaceRule = paint.declaration.selector;
  const layers: PaintLayer[] = [];

  const rem = remPx(root);

  for (const value of backgroundLayers(paint.declaration.value, table)) {
    layers.push({
      value,
      anchor: { edge: "full" },
      opacity: 1,
      screen: false,
      clipRightPx: clipRightPx(root, surfaceRule, rem),
      source: surfaceRule,
    });
  }

  // A veil is a real element the component rendered, not a guess: it is
  // inside the surface, the sheet gives it `position: absolute` and
  // `inset: 0`, and it paints. That is the shape the panel mist uses.
  for (const node of Array.from(surface.querySelectorAll("*"))) {
    if (node === el || node.contains(el)) continue;
    const position = winningDeclaration(root, node, POSITION_PROP);
    if (position?.value !== "absolute") continue;
    if (winningDeclaration(root, node, INSET_PROP)?.value !== "0") continue;
    const veil = winningDeclaration(root, node, PAINT_PROP);
    if (!veil) continue;
    for (const value of backgroundLayers(veil.value, table)) {
      layers.push({
        value,
        anchor: { edge: "full" },
        opacity: 1,
        screen: false,
        clipRightPx: clipRightPx(root, veil.selector, rem),
        source: veil.selector,
      });
    }
  }

  for (const pseudo of ["::before", "::after"]) {
    for (const found of pseudoDeclarations(root, surface, pseudo, PAINT_PROP)) {
      const opacity = Number(companionValue(root, found.selector, OPACITY_PROP) ?? "1");
      const screen = companionValue(root, found.selector, BLEND_PROP) === "screen";
      const anchor = anchorOf(root, found.selector);
      const clip = clipRightPx(root, found.selector, rem);
      for (const value of backgroundLayers(found.value, table)) {
        layers.push({
          value,
          anchor,
          opacity: Number.isFinite(opacity) ? opacity : 1,
          screen,
          clipRightPx: clip,
          source: found.selector,
        });
      }
    }
  }

  return { surface, surfaceRule, layers };
}
