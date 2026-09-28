/**
 * paletteBudget.ts - the two numbers the colour guard holds, and the method
 * they only mean anything under.
 *
 * They live beside the other theme constants rather than in the test tree
 * because the product is what is held to them: a number kept only in a test is
 * a number the product never agreed to. The method itself is NOT here. It
 * lives in the body of palette-guard.test.ts, because a count is only as good
 * as its rules and those rules have to be read beside the assertion that uses
 * them: `#hex` / `rgb(` / `rgba(` / `hsl(` counted, named colours and
 * `transparent` and `currentColor` not counted, a `--` property counted as a
 * token definition and anything else as a direct declaration, and one
 * declaration able to carry several colours.
 *
 * WHERE THE COUNT IS TAKEN, and this is half the definition. It is taken over
 * the COMPILED stylesheet - index.css as Vite and Tailwind actually emit it -
 * PARSED with postcss, and it skips the rules Tailwind generates for itself:
 * `@property --tw-*`, `@layer utilities` and `@layer properties`. Those are
 * the library's output, not this app's decisions, and counting them would
 * mean a ceiling that moves when a component starts using a utility. What is
 * left is index.css's own cascade, `@theme inline` and the media blocks
 * included, so a literal written inside `@media (prefers-reduced-transparency:
 * reduce)` is counted exactly as one written at the top level.
 *
 * Measured on 19 September 2026 with one counter over both substrates, on
 * the tree that carries the three tokenised families, the type scale and
 * both halves of the scrollbar work:
 *
 *   index.css (source)   262 direct / 191 tokenised
 *   the compiled sheet   262 direct / 192 tokenised
 *
 * The direct halves agree to the unit, which is why the guard could stop
 * reading the source file at all: reading the source was never the point, it
 * was the only way anybody had. The single extra token is `--color-black`,
 * one of Tailwind's own theme defaults, emitted into `@layer theme` because a
 * utility references it; the floor below is a minimum, so it costs nothing.
 *
 * The ceiling is a RATCHET: it only ever goes down. Moving a literal onto a
 * token lowers the direct count; the new, lower count becomes the ceiling in
 * the same commit, and it never comes back up. A commit that tokenises
 * nothing cannot go green by lowering it, because the guard measures.
 *
 * Measured, in order:
 *   416 direct / 107 tokenised   before this work (audit OLCUM item 3)
 *   269 direct / 189 tokenised   after the three heaviest families moved
 *   268 direct / 191 tokenised   once the scrollbar rules were rewritten
 *   262 direct / 191 tokenised   once the webkit families were deleted
 *
 * The compiled sheet reads one token higher than the last two lines, 192
 * rather than 191, and that difference is `--color-black` and nothing else:
 * Tailwind emits its own theme default into `@layer theme` because a utility
 * references it. The floor is a minimum, so it clears either way.
 *
 * The last two lines are not a second round of tokenising. Rewriting the
 * scrollbar rules came out one direct literal lighter and two token
 * definitions heavier - the two entries in `lib/theme/tokens.ts`, one thumb
 * colour for light surfaces and one for dark. Deleting the
 * `::-webkit-scrollbar` families those rules had made inert, and the
 * ScrollArea's own thumb styling, then took six more, all of them thumb and
 * hover colours. Four were colours nothing could
 * paint: two on the page-wide family, two on the dark-wallpaper thumb. The
 * other two were painted, by the thumb of the ScrollArea the Models and
 * Security tabs scrolled in, and left with that component when both tabs
 * moved to the panel's own native scroller. The ceiling follows the count
 * down in the same commit, which is what the ratchet means; the floor is a
 * minimum and stays where it was measured, because no token definition was
 * deleted.
 *
 * Two of the tokenised literals are known dead and are counted anyway:
 * `--card: #1B2634` and `--card-foreground: #EAEEF3`. Their only consumer was
 * `components/ui/card.tsx`, which no longer exists, and nothing paints the
 * background or the foreground utility those two tokens feed any more - but
 * `test/tokens-sync.test.ts`
 * still requires their `--color-*` aliases, so the three have to go together or
 * not at all, and that is a change of its own. The two utility names are NOT
 * spelled out above: Tailwind scans this module as raw text, and while they
 * were written here it went on emitting both rules for a component that no
 * longer exists - measured, they left the compiled sheet the moment the words
 * were broken. The floor is a minimum, so two
 * dead entries can only make it harder to satisfy, never easier; the ceiling is
 * not affected at all, because a `--` property is a token definition and not a
 * direct declaration. Recorded so the number is honest rather than tidy.
 *
 * The 147 that moved out of the three families are .glass-right 10,
 * persona-* 50 and settings-* 87.
 * 139 became token references and 8 were `var(--token, <literal>)` fallbacks
 * that no longer had anything to fall back from, so they were dropped rather
 * than given a token; the 139 collapse onto 82 tokens because the same colour
 * was written out as many as eight times.
 */

/** The most direct colour literals the sheet may carry. Goes down, never up. */
export const LITERAL_CEILING = 262;

/**
 * The fewest literals that must sit INSIDE a token definition.
 *
 * The ceiling on its own cannot tell tokenising apart from deletion: both
 * lower the direct count. This is the other half, so a commit that "fixes"
 * the ceiling by throwing colours away instead of naming them is caught.
 */
export const TOKENISED_FLOOR = 189;

/** The three families FIX 2 emptied. Not one direct literal may return. */
export const TOKENISED_FAMILIES = [".glass-right", "persona-", "settings-"] as const;
