/**
 * One way to print a count the user reads: grouped digits ("5,810"), the
 * same form the prompt module counter has always used. The module pages,
 * the chat's refusal sentence and the reply length warning all go through
 * this, so the same number never shows up as "5810" in one place and
 * "5,810" in the next.
 *
 * en-US on purpose: every other word on these pages is English, and a
 * machine locale that groups with a dot would make "1.234" read as a
 * decimal next to an English sentence.
 */
const COUNT_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
}
