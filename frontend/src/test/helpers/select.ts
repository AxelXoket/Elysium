/**
 * select.ts - driving a ui/select.tsx Select the way a person does.
 *
 * `userEvent.selectOptions` only knows a native `<select>`; the app's selects
 * are Base UI's, which open a body-portalled listbox on click and are walked
 * by keyboard. Every test that picks an option goes through here so the
 * gesture is written once: click the trigger, wait for the listbox, click the
 * option, wait for the listbox to be gone. The last wait is load-bearing -
 * Base UI closes the popup on its exit animation's end, and a test that reads
 * the trigger before that lands reads the old value.
 */
import { expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

/** Either a `userEvent.setup()` instance or the default export: both click. */
export type Clicker = { click: (element: Element) => Promise<unknown> };

/** The same, for a session that also sends keys. */
export type Typist = Clicker & { keyboard: (text: string) => Promise<unknown> };

/** The popup element a trigger opened, or null while it is closed. */
export function openPopup(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="select-content"]');
}

/** Opens the select behind `trigger` and returns its listbox. */
export async function openSelect(
  user: Clicker,
  trigger: HTMLElement,
): Promise<HTMLElement> {
  await user.click(trigger);
  return screen.findByRole("listbox");
}

/**
 * Waits until the listbox `opened` is no longer open.
 *
 * It takes the NODE rather than looking one up, and that is the whole
 * difference: "wait until no listbox is present" is green on its first tick
 * when nothing ever opened, so a test whose click missed the trigger
 * reported a clean close. Handed the node, this is waiting on something that
 * demonstrably existed.
 *
 * "No longer open" and not "removed": Base UI keeps the popup mounted while
 * it plays its exit animation and takes it out of the accessibility tree
 * first, so waiting for removal times out on a popup that closed correctly.
 * The measure is whether the node is still one of the listboxes a reader
 * could reach.
 */
export async function expectClosed(opened: HTMLElement): Promise<void> {
  await waitFor(() => {
    if (screen.queryAllByRole("listbox").includes(opened)) {
      throw new Error("the listbox is still open");
    }
  });
}

/** Picks the option named `optionName` from the select behind `trigger`. */
export async function chooseOption(
  user: Clicker,
  trigger: HTMLElement,
  optionName: string | RegExp,
): Promise<void> {
  const listbox = await openSelect(user, trigger);
  await user.click(within(listbox).getByRole("option", { name: optionName }));
  await expectClosed(listbox);
}

/**
 * Type-ahead on an OPEN listbox, asked in both directions.
 *
 * A letter that names exactly one option must move the highlight to it; a
 * letter that names none must leave the highlight alone. The second half is
 * what stops the first being satisfied by a popup that moves the highlight on
 * any key at all, and it is also what makes a popup that ignores every letter
 * distinguishable from one that answers.
 *
 * A dropdown whose options share their first letters, or whose highlighted
 * option is the only unique one, cannot be asked - there is no letter that
 * picks exactly one other option - so it is skipped rather than asked a
 * question with no answer. The count of dropdowns that WERE asked is returned
 * so a caller can refuse a suite where every one of them opted out.
 */
export async function expectTypeahead(
  user: Typist,
  listbox: HTMLElement,
  where: string,
): Promise<boolean> {
  const options = within(listbox).getAllByRole("option");
  await waitFor(() => {
    expect(options, `${where}: focus never entered the list`)
      .toContain(document.activeElement);
  });
  const from = document.activeElement as HTMLElement;
  const firstLetter = (el: Element) =>
    ((el.textContent ?? "").trim()[0] ?? "").toLowerCase();
  const counts = new Map<string, number>();
  for (const option of options) {
    const letter = firstLetter(option);
    if (/^[a-z]$/.test(letter)) counts.set(letter, (counts.get(letter) ?? 0) + 1);
  }
  const target = options.find(
    (option) => option !== from && counts.get(firstLetter(option)) === 1,
  );
  if (!target) return false;

  // GROUND: a letter no option starts with moves nothing. Found rather than
  // assumed, so this is never asked with a letter that does match.
  const unused = "abcdefghijklmnopqrstuvwxyz"
    .split("")
    .find((letter) => !counts.has(letter));
  if (unused) {
    await user.keyboard(unused);
    expect(
      document.activeElement,
      `${where}: the highlight moved on a letter no option starts with`,
    ).toBe(from);
  }

  await user.keyboard(firstLetter(target));
  await waitFor(() => {
    expect(
      document.activeElement,
      `${where}: typing a letter did not jump to the option that starts with it`,
    ).toBe(target);
  });
  return true;
}

/**
 * The keyboard contract IP-03 FIX 2 writes for EVERY converted dropdown, in
 * one gesture, so all eight call sites are held to the same clauses.
 *
 * IP-03's acceptance sentence is "tetikleyiciye tiklaninca `role="listbox"`
 * acilir ve secenekler klavyeyle gezilebilir, `Enter` secer, `Escape` kapatir
 * ve odak tetikleyiciye doner", and it says `her acilir icin`. Two of those
 * clauses were being taken on trust. Every open in the suite was a CLICK, so nothing
 * depended on the trigger's own key path - the case T-18's own mutation aims
 * at - and `Enter` was asserted at exactly one call site out of
 * eight, which proves the list can be walked and abandoned but never that the
 * keyboard can commit a value.
 *
 * So the order here is: open from the keyboard alone, close on Escape and
 * take focus back, then open again and COMMIT with Enter, and read the
 * committed value off the trigger. A native `<select>` would satisfy all of
 * this for free; the whole risk of the conversion is that a hand-built popup
 * does not, so it is asked rather than assumed.
 *
 * `where` names the call site, because eight identical failures in one run
 * are unreadable without it.
 *
 * `committed` is how the commit is OBSERVED, and it has to be a parameter.
 * Six of the eight dropdowns hold their own value, so the words on the
 * trigger are the answer; the notebook's two are controlled by the server
 * round trip, and their answer is the request that went out - the trigger
 * goes back to whatever the settings query still says. Defaulting to the
 * trigger and letting those two pass their own reader keeps one contract
 * instead of two.
 *
 * The sentence quoted above lists four clauses; type-ahead is the fifth and
 * here because it is the one a hand-built popup silently loses. Returns
 * whether it could be asked at this call site, so a caller looping over its
 * dropdowns can refuse a run where every one of them opted out - a clause
 * nothing ever reaches has been deleted without anyone saying so.
 */
export async function expectKeyboardContract(
  user: Typist,
  trigger: HTMLElement,
  where: string,
  committed?: (optionName: string) => Promise<void>,
): Promise<boolean> {
  // GROUND: closed really reads as closed. Every one of the eight selects
  // changed in this work, so the ground is the closed state rather than some
  // unchanged control to compare against.
  expect(screen.queryAllByRole("listbox"), `${where}: a listbox was already open`)
    .toEqual([]);

  // 1. Opened by the KEYBOARD, from the trigger's own key path.
  trigger.focus();
  expect(document.activeElement, `${where}: the trigger cannot take focus`)
    .toBe(trigger);
  await user.keyboard("{ArrowDown}");
  const opened = await screen.findByRole("listbox");
  expect(
    within(opened).getAllByRole("option").length,
    `${where}: opened with nothing in it`,
  ).toBeGreaterThan(1);

  // 2. Type-ahead: a letter jumps to the option that starts with it.
  //
  // The one clause of a native `<select>`'s keyboard that a hand-built popup
  // most often drops, and the one nothing here was asking for. It is asked in
  // BOTH directions: a letter no option starts with must leave the highlight
  // where it is, which is the ground - without it, a popup that ignored every
  // letter would pass the half that matters by accident.
  const typeaheadAsked = await expectTypeahead(user, opened, where);

  // 3. Escape closes it and hands focus back.
  await user.keyboard("{Escape}");
  await expectClosed(opened);
  expect(document.activeElement, `${where}: Escape did not return focus`)
    .toBe(trigger);

  // 4. Enter COMMITS the option the keyboard walked to. Opened with a click
  //    this time, so the two ways in are both exercised.
  const listbox = await openSelect(user, trigger);
  const options = within(listbox).getAllByRole("option");
  // The list takes focus a tick after it opens, and a key sent before that
  // lands nowhere.
  await waitFor(() => {
    expect(options, `${where}: focus never entered the list`)
      .toContain(document.activeElement);
  });
  const from = document.activeElement as HTMLElement;
  await user.keyboard("{ArrowDown}");
  if (document.activeElement === from) {
    // Already on the last option: walk the other way rather than assert
    // against the order the list happens to be in.
    await user.keyboard("{ArrowUp}");
  }
  const chosen = document.activeElement as HTMLElement;
  expect(options, `${where}: the arrow keys did not move inside the list`)
    .toContain(chosen);
  expect(chosen, `${where}: the arrow keys did not move off the current option`)
    .not.toBe(from);
  const chosenName = (chosen.textContent ?? "").trim();
  expect(chosenName, `${where}: the option the keyboard is on has no words`)
    .not.toBe("");
  await user.keyboard("{Enter}");
  await expectClosed(listbox);
  expect(document.activeElement, `${where}: Enter did not return focus`)
    .toBe(trigger);
  if (committed) {
    await committed(chosenName);
    return typeaheadAsked;
  }
  await waitFor(() => {
    // The VALUE node, and an equality. The trigger's own text carries the
    // chevron as well, and a substring match over it is not the question
    // being asked: in the rating ceiling, "PG-13" contains both "G" and
    // "PG", so `toContain` would accept two wrong answers out of five. It
    // only passes today because that control starts empty.
    const value = trigger.querySelector<HTMLElement>('[data-slot="select-value"]');
    expect(value, `${where}: the trigger shows no value node to read`)
      .not.toBeNull();
    expect(
      (value!.textContent ?? "").trim(),
      `${where}: Enter did not commit the option the keyboard was on`,
    ).toBe(chosenName);
  });
  return typeaheadAsked;
}

/**
 * The positioner behind the open popup: the node that carries the real
 * collision padding AND the attribute that proves it got it.
 *
 * The attribute used to sit on the popup while the prop went to the
 * positioner, so a test reading it could not tell the two apart and deleting
 * the prop left the assertion green. They travel together now, on this node.
 */
export function openPositioner(): HTMLElement | null {
  const popup = openPopup();
  return popup?.closest<HTMLElement>("[data-collision-padding]") ?? null;
}
