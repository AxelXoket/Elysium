/**
 * FAZ 4 - choosing an extractor.
 *
 * The panel makes two choices and spends nothing doing it: which model reads
 * the last few turns, and which language its instructions are written in. So
 * the tests here are about the two things a settings screen can get wrong -
 * saying something false about the saved state, and failing to save a change.
 *
 * "Not chosen - suggestions are off" is the string under the most pressure: it
 * tells the user a background job is not running, and it used to be rendered
 * from an unanswered query on every tab switch.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { hiddenFromReaders } from "@/test/helpers/accessibility";
import {
  chooseOption,
  expectClosed,
  expectKeyboardContract,
  openPositioner,
  openSelect,
} from "@/test/helpers/select";
import {
  CHAT_MODEL_PRICE_WARNING,
  CHAT_MODEL_PRICE_WARNING_ID,
  ExtractionSettings,
} from "@/components/notebook/ExtractionSettings";
import { INSTRUCTION_LANGUAGE_LABELS } from "@/lib/notebook/labels";
import { CHAT_MODEL_BINDING } from "@/lib/schemas/notebook";
import { POPUP_COLLISION_PADDING } from "@/lib/theme/popupPositioning";
import { mockFetch } from "../mocks/api";
import { useUiStore } from "@/lib/store/uiStore";
// The one string that is not a model id, as both suites agree on it: the
// backend asserts notebook_extract.CHAT_MODEL_BINDING against this file,
// this side asserts the frontend's constant against it.
import bindingContract from "../../../../shared/notebook_chat_model_binding.json";

const MODELS = {
  models: [
    { id: "cheap/one", provider: "P", prompt_price: 0.06,
      context_length: 131000, endpoints: 3 },
    { id: "lonely/two", provider: "Q", prompt_price: 0.05,
      context_length: 400000, endpoints: 1 },
  ],
};

describe("ExtractionSettings", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("starts with nothing chosen, and says what that means", async () => {
    // No default and no automatic pick: it is the user's API key.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect(await screen.findByText(/suggestions are off/i)).toBeInTheDocument();
  });

  it("warns which models are pinned to one provider", async () => {
    // A single-endpoint model most likely has no backup to fail over to, so
    // it stops working when that one machine does - and the user has to know.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    // The options live in the popup, so the list is opened first.
    const trigger = await screen.findByLabelText(/^Model$/);
    await waitFor(() => expect(trigger).toBeEnabled());
    await openSelect(userEvent.setup(), trigger);
    expect(screen.getByRole("option", { name: /\(1 provider\) - lonely\/two/ }))
      .toBeInTheDocument();
    expect(screen.getByRole("option", { name: /cheap\/one/ }).textContent)
      .not.toMatch(/1 provider/);
  });

  it("spends nothing on its own, even with a model chosen", async () => {
    // The panel used to carry a "try it" button that billed the user's key on
    // click. It is gone, and this is the guard that keeps it gone: opening the
    // screen reads the catalogue and the saved choice, and asks for nothing
    // that costs money.
    //
    // Two controls, because an absence assertion on a screen that failed to
    // render passes for the wrong reason. Ground: the panel is really there,
    // with a model selected. Positive: the same fetch spy that reports zero
    // extraction calls does report the reads, so it is not simply blind.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);

    await waitFor(() =>
      expect(screen.getByLabelText(/^Model$/))
        .toHaveTextContent("cheap/one"));

    const urls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((c: unknown[]) => String(c[0]));
    expect(urls.some((u) => u.includes("/extract/models"))).toBe(true);
    expect(urls.filter((u) => u.includes("/extract/dry-run"))).toEqual([]);
    // Nothing on this screen fires a POST by itself either; the only writes it
    // makes are the two selects, and both need a user.
    const writes = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .filter((c: unknown[]) =>
        (c[1] as RequestInit | undefined)?.method === "POST");
    expect(writes).toEqual([]);
  });

  it("lets the instruction language be switched", async () => {
    // Untested assumption, shipped as a choice rather than a bet.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/extract/settings": { body: { ok: true } },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);

    // Inert until the saved choice is known - see the test below for why
    // that matters more here than it looks.
    const select = await screen.findByLabelText(/instructions written in/i);
    await waitFor(() => expect(select).toBeEnabled());
    await chooseOption(user, select, INSTRUCTION_LANGUAGE_LABELS.tr);

    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/extract/settings")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ prompt_language: "tr" });
    });
  });

  it("never says suggestions are off before it knows", async () => {
    // "Not chosen - suggestions are off" is the one string in this panel that
    // must not be shown falsely: it tells the user a background job is not
    // running. Rendered from `data?.model_id ?? ""` while the query was still
    // in flight, it appeared on every single tab switch - including for a user
    // who HAD chosen a model.
    let release: () => void = () => {};
    const held = new Promise<void>((r) => { release = r; });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body),
                   { status: 200,
                     headers: { "Content-Type": "application/json" } });

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/vault/status"))
        return json({ initialized: true, unlocked: true });
      if (url.includes("/extract/models")) return json(MODELS);
      if (url.includes("/extract/settings")) {
        await held;
        return json({ model_id: "cheap/one", prompt_language: "en" });
      }
      return json({});
    }));

    renderWithQueryClient(<ExtractionSettings />);

    expect(await screen.findByText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText(/suggestions are off/i)).not.toBeInTheDocument();

    release();
    await waitFor(() =>
      expect(screen.getByLabelText(/^Model$/))
        .toHaveTextContent("cheap/one"));
  });

  it("offers the chat's own model first, and saves the binding as sent", async () => {
    // KARAR-V2-04b: a standing choice, not a model. The option sits at the
    // head of the list, and choosing it sends the marker itself - the
    // backend resolves it on every read, so nothing is resolved here.
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/extract/settings": { body: { ok: true } },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    // The options live in a portalled listbox, so the list is opened before
    // its order can be read. Base UI puts no value attribute on an option,
    // so the ORDER is read off the words a reader sees and the VALUE off the
    // request the choice sends, which is where the marker has to be exact.
    const trigger = await screen.findByLabelText(/^Model$/);
    await waitFor(() => expect(trigger).toBeEnabled());
    const listbox = await openSelect(user, trigger);
    const options = within(listbox).getAllByRole("option");
    // GROUND: the list is the real one, not an empty popup - the two mocked
    // models are in it beside the two standing entries.
    expect(options.map((o) => (o.textContent ?? "").trim())).toEqual([
      "Not chosen - suggestions are off",
      "Use the model I am chatting with",
      "$0.060/M - cheap/one",
      "$0.050/M (1 provider) - lonely/two",
    ]);

    // First after the "not chosen" option, before any listed model.
    await user.click(options[1]);

    await expectClosed(listbox);
    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/extract/settings")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ model_id: CHAT_MODEL_BINDING });
    });
  });

  it("the binding is the same string on both sides of the wall", () => {
    expect(CHAT_MODEL_BINDING).toBe(bindingContract.model_id);
  });

  it("warns about the price whenever the binding is the saved choice", async () => {
    // Mandatory beside that choice: a chat model is many times a cheap
    // extractor's price and the choice follows every change of chat model.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: CHAT_MODEL_BINDING,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    const warning = await screen.findByTestId("chat-model-price-warning");
    await waitFor(() => expect(hiddenFromReaders(warning)).toBe(false));
    expect(warning.textContent).toBe(CHAT_MODEL_PRICE_WARNING);
  });

  it("the picker itself carries the warning as its description", async () => {
    // A mandatory warning that is only a paragraph NEAR the control is not
    // announced at all to somebody sitting on the control: they hear its
    // name and its value, spend, and meet the sentence afterwards if they
    // ever walk past it. `aria-describedby` is what makes the warning part
    // of the thing it warns about, and the accessible DESCRIPTION is how a
    // reader meets it, so that is what is asked here rather than the
    // attribute alone.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    const picker = await screen.findByLabelText(/^Model$/);
    await waitFor(() =>
      expect(picker).toHaveTextContent("Not chosen - suggestions are off"));
    expect(picker).toHaveAccessibleDescription(CHAT_MODEL_PRICE_WARNING);
    // The description is that NODE, not a string that happens to match:
    // the id on the paragraph is what the picker points at, and both come
    // from the one exported constant.
    expect(picker.getAttribute("aria-describedby"))
      .toBe(CHAT_MODEL_PRICE_WARNING_ID);
    expect(screen.getByTestId("chat-model-price-warning").id)
      .toBe(CHAT_MODEL_PRICE_WARNING_ID);
    // GROUND: the description is the price warning and not whatever else
    // sits under the picker (the "only models that keep no copy" line).
    expect(picker)
      .not.toHaveAccessibleDescription(/only models that keep no copy/i);
  });

  it("says the price before anything is picked, not after", async () => {
    // The claim the sentence makes about itself: it is there while the
    // reader is DECIDING. Nothing is chosen in this fixture, which is the
    // state a reader meeting the option for the first time is in.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    // GROUND: the picker really rendered, so "the sentence is there" is
    // not "an empty component is there".
    const trigger = await screen.findByLabelText(/^Model$/);
    await waitFor(() =>
      expect(trigger).toHaveTextContent("Not chosen - suggestions are off"));
    // The option is reachable without choosing it: the popup is opened and
    // closed again, so nothing is saved and the warning is read in the state
    // a reader meeting the option for the first time is in.
    const user = userEvent.setup();
    const listbox = await openSelect(user, trigger);
    expect(within(listbox).getByRole("option",
      { name: /model I am chatting with/i })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await expectClosed(listbox);
    const warning = screen.getByTestId("chat-model-price-warning");
    expect(hiddenFromReaders(warning)).toBe(false);
    expect(warning.textContent).toBe(CHAT_MODEL_PRICE_WARNING);
  });

  it("keeps saying it once a listed model is the choice", async () => {
    // The sentence is about the OPTION, so it does not vanish when some
    // other model is selected; it names itself instead of relying on
    // context the reader no longer has.
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    await waitFor(() =>
      expect(screen.getByLabelText(/^Model$/)).toHaveTextContent("cheap/one"));
    const warning = screen.getByTestId("chat-model-price-warning");
    expect(hiddenFromReaders(warning)).toBe(false);
    expect(warning.textContent).toBe(CHAT_MODEL_PRICE_WARNING);
  });

  it("says so when the list could not be fetched", async () => {
    // An empty picker and a failed fetch look identical, and they mean
    // opposite things: "no model qualifies" versus "we could not ask".
    mockFetch({
      "/notebook/extract/models": { status: 502,
                                    body: { detail: "openrouter_unreachable" } },
      "/notebook/extract/settings": { body: { model_id: null,
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    expect((await screen.findByRole("alert")).textContent)
      .toMatch(/could not be fetched/i);
  });
});

describe("the language select spells the Turkish option as its speakers do (T-20)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  function mount() {
    mockFetch({
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "en" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
  }

  it("offers the endonym, with its accents, and nowhere the unaccented spelling", async () => {
    // The spelling IS the claim, so it is written out here rather than
    // imported: a test that read the label from the component would turn
    // green again the moment the component dropped the accents.
    const user = userEvent.setup();
    mount();
    const select = await screen.findByLabelText(/instructions written in/i);
    await waitFor(() => expect(select).toBeEnabled());
    // POSITIVE CONTROL: the English option is found by the same query, so
    // an empty list cannot pass the absence below.
    await openSelect(user, select);
    expect(screen.getByRole("option", { name: "English" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "T\u00fcrk\u00e7e" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Turkce" })).toBeNull();
    expect(document.body.textContent).not.toContain("Turkce");
  });

  it("puts the chosen endonym on the trigger and sends the router its code", async () => {
    const user = userEvent.setup();
    mockFetch({
      "POST /notebook/extract/settings": { body: { ok: true } },
      "/notebook/extract/models": { body: MODELS },
      "/notebook/extract/settings": { body: { model_id: "cheap/one",
                                              prompt_language: "tr" } },
    });
    renderWithQueryClient(<ExtractionSettings />);
    const select = await screen.findByLabelText(/instructions written in/i);
    await waitFor(() => expect(select).toHaveTextContent("T\u00fcrk\u00e7e"));
    await chooseOption(user, select, "English");
    await waitFor(() => {
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
        .find((c: unknown[]) =>
          String(c[0]).includes("/extract/settings")
          && (c[1] as RequestInit | undefined)?.method === "POST");
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)))
        .toEqual({ prompt_language: "en" });
    });
  });

  it("T-18: both dropdowns keep the whole keyboard contract", async () => {
    // The same five clauses the other six call sites are held to, including
    // the two that were being taken on trust: opened from the trigger's own
    // key path rather than by a click, and COMMITTED with Enter.
    //
    // The commit is read off the REQUEST rather than off the trigger: both of
    // these dropdowns are controlled by the settings query, so the words on
    // the trigger go back to whatever the server last said while the mock
    // keeps answering with the old value. What the keyboard did is what went
    // out on the wire.
    const user = userEvent.setup();
    mount();
    const sentValueFor = async (optionName: string) => {
      await waitFor(() => {
        // The LAST such POST, not the first: this loop drives two dropdowns
        // in a row, and the first match is the one the previous iteration
        // sent.
        const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
          .filter((c: unknown[]) =>
            String(c[0]).includes("/extract/settings")
            && (c[1] as RequestInit | undefined)?.method === "POST")
          .at(-1);
        expect(call, "Enter sent nothing to the server").toBeTruthy();
        const body = JSON.parse(String((call![1] as RequestInit).body)) as
          Record<string, string>;
        const value = String(Object.values(body)[0]);
        // The language select sends a code and shows an endonym; the model
        // select sends the id and shows it inside a priced label. Both are
        // answered by asking what the option the keyboard was on is called.
        const shown = INSTRUCTION_LANGUAGE_LABELS[
          value as keyof typeof INSTRUCTION_LANGUAGE_LABELS
        ] ?? value;
        expect(optionName, "the value sent is not the option Enter was on")
          .toContain(shown);
      });
    };
    let typeaheadAsked = 0;
    for (const name of [/instructions written in/i, /^Model$/]) {
      const trigger = await screen.findByLabelText(name);
      await waitFor(() => expect(trigger).toBeEnabled());
      if (await expectKeyboardContract(user, trigger, String(name), sentValueFor)) {
        typeaheadAsked += 1;
      }
    }
    // As in the qualifier loop: the clause skips a list with no letter that
    // picks exactly one other option, so it is shown reaching at least one.
    expect(typeaheadAsked, "neither dropdown was asked for type-ahead")
      .toBeGreaterThan(0);
  }, 60_000);

  it("names both dropdowns with an aria-labelledby of their own", async () => {
    // The file's own comment says the accessible name comes THROUGH
    // aria-labelledby on the trigger, and nothing held that: deleting the
    // attribute leaves `findByLabelText` working, because the trigger sits
    // inside a `<label>` and a button is a labelable element, so the wrapper
    // supplies the name instead. The distinction is deliberate - an explicit
    // binding survives the day the markup around it changes - so it is asked
    // for by name.
    mount();
    for (const name of [/instructions written in/i, /^Model$/]) {
      const trigger = await screen.findByLabelText(name);
      const idref = trigger.getAttribute("aria-labelledby");
      expect(idref, `${String(name)} is named by its wrapper alone`).not.toBeNull();
      // And the id it points at is really on the page, carrying the words.
      // A dangling IDREF names nothing and reads as a binding.
      const named = document.getElementById(String(idref));
      expect(named, `${String(name)} points at an id nothing carries`).not.toBeNull();
      expect(named!.textContent ?? "", `${String(name)}'s label has no words`)
        .toMatch(name);
    }
  }, 30_000);

  it("both popups keep the shared collision padding (T-75, the notebook's two call sites)", async () => {
    const user = userEvent.setup();
    mount();
    for (const name of [/instructions written in/i, /^Model$/]) {
      const trigger = await screen.findByLabelText(name);
      await waitFor(() => expect(trigger).toBeEnabled());
      const listbox = await openSelect(user, trigger);
      expect(openPositioner()!.getAttribute("data-collision-padding"))
        .toBe(String(POPUP_COLLISION_PADDING));
      await user.keyboard("{Escape}");
      await expectClosed(listbox);
    }
  });
});
