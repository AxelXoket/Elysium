/**
 * ErrorMessageCopy.test.ts - the sentences, checked as sentences.
 *
 * ErrorHandling.test.ts already checks the MACHINERY of the error map: that a
 * code resolves, that unknown codes fall back, that the catalogue and the map
 * agree on which codes exist. Nothing checked what the sentences SAY, and an
 * audit of all 138 of them found the predictable result: controls named by
 * names they do not have, a cap stated at half its real size, three sentences
 * that scolded the reader, and ten that named a problem and offered no way out.
 *
 * Every assertion below is tied to a fact in the source, and the comment says
 * where that fact lives - so when the source changes, the test that fails
 * points at the line that made it false rather than at a style preference.
 *
 * Deliberately NOT asserted: exact wording. These pin the claims that can go
 * stale (a control's label, a numeric cap, an enum's members) and the two
 * shapes the house voice forbids (a dead end, a jab), and leave the prose free.
 */
import { describe, it, expect } from "vitest";
import { getErrorMessage, knownErrorCodes } from "@/lib/errors/errorMessages";

/** Codes the audit found naming a control that does not exist by that name. */
describe("sentences name controls that exist", () => {
  // VoiceSettingsPage.tsx renders {state === "broken" || failed ? "Set up
  // again" : "Set up"} - there has never been a button reading "Set up voice".
  it("the voice setup sentences name the button by its printed label", () => {
    const missing = getErrorMessage("tts_runtime_missing");
    const broken = getErrorMessage("tts_runtime_broken");
    expect(missing).toContain("Set up");
    expect(missing).not.toContain("Set up voice");
    expect(broken).toContain("Set up again");
    expect(broken).not.toContain("Set up voice again");
  });

  // BoundaryPanel.tsx has ONE text field for a limit and sends its value as
  // both `label` and `phrasing`. The old sentence asked for "both a name and
  // the wording", which describes a form that was never built.
  it("boundary_empty does not ask for a second field", () => {
    const msg = getErrorMessage("boundary_empty");
    expect(msg).not.toMatch(/\bboth\b/i);
    expect(msg).not.toMatch(/\ba name\b/i);
  });

  // BoundaryPanel's severity is a <select> that starts on "hard" and has no
  // empty option, so it cannot be unset and cannot be "chosen" as a fix.
  it("boundary_invalid does not ask for a severity that is already set", () => {
    expect(getErrorMessage("boundary_invalid")).not.toMatch(/how strict/i);
  });

  // The add-note row in NotebookPanel.tsx is a single <textarea>. Neither a
  // type control nor an importance control exists anywhere in that panel.
  it("notebook_entry_invalid does not name a type or importance control", () => {
    const msg = getErrorMessage("notebook_entry_invalid");
    expect(msg).not.toMatch(/\btype\b/i);
    expect(msg).not.toMatch(/\bimportance\b/i);
  });

  // config.py reads ELYSIUM_DATA_DIR from os.environ. No frontend file
  // mentions it, and no settings row writes it - calling it a "setting" sent
  // readers hunting through Settings for a row that does not exist.
  it("tts_cache_outside_data_dir does not call an env var a setting", () => {
    const msg = getErrorMessage("tts_cache_outside_data_dir");
    expect(msg).toContain("ELYSIUM_DATA_DIR");
    expect(msg).not.toMatch(/ELYSIUM_DATA_DIR setting/);
    expect(msg).toMatch(/environment variable/i);
  });
});

describe("sentences state limits at their real size", () => {
  // notebook_store.ENTRY_MAX_CHARS is 240, mirrored as the textarea's
  // maxLength in NotebookPanel.tsx. "One short sentence" is well under half
  // of that, so it talked readers out of room the app was giving them.
  it("notebook_entry_too_long names the 240 character cap", () => {
    const msg = getErrorMessage("notebook_entry_too_long");
    expect(msg).toContain("240");
    expect(msg).not.toMatch(/one short sentence/i);
  });

  // routers/notebook.py accepts exactly "en" and "tr"; the select in
  // ExtractionSettings.tsx offers "English" and "Turkce". Naming the count
  // without naming the two left the reader to go and look.
  it("notebook_language_unknown names both languages", () => {
    const msg = getErrorMessage("notebook_language_unknown");
    expect(msg).toContain("English");
    expect(msg).toContain("Turkce");
  });
});

describe("sentences do not blame the reader", () => {
  // The extraction model field is a text input that accepts typing, so
  // "instead of typing it" told somebody off for using the control as built.
  // What actually failed is a shape check (_MODEL_ID wants author/model).
  it("notebook_model_id_invalid names the shape, not the reader's method", () => {
    const msg = getErrorMessage("notebook_model_id_invalid");
    expect(msg).not.toMatch(/instead of typing/i);
    expect(msg).toContain("author/model");
  });

  // Its sibling model_id_too_long is plain about the identical rule; this one
  // called the reader's input a lie for crossing MODEL_ID_MAX_CHARS (128).
  it("notebook_model_id_too_long matches its polite sibling", () => {
    const msg = getErrorMessage("notebook_model_id_too_long");
    expect(msg).not.toMatch(/to be real/i);
    expect(msg).toContain("too long");
  });
});

describe("sentences say whether anything was saved", () => {
  // routers/settings.py returns {ok: false, key_status:
  // "validation_unavailable"} on the network path and explicitly does NOT
  // store the key. The old sentence reported only the failed check, which
  // reads as "saved but unverified" - so people closed the panel with no key.
  it("validation_unavailable says the key was not saved", () => {
    const msg = getErrorMessage("validation_unavailable");
    expect(msg).toMatch(/not saved/i);
  });
});

/**
 * The dead ends.
 *
 * Ten codes named a problem and stopped. A sentence that reports a failure
 * without a next step is a shrug with punctuation: the reader is left to guess
 * whether to wait, retry, change something or reinstall. Each of these now
 * carries an action the code can actually stand behind - and where the app
 * genuinely does not know the cause, the action is the honest one (retry, or
 * restart) rather than an invented diagnosis.
 */
describe("no sentence is a dead end", () => {
  const FORMERLY_DEAD = [
    "tts_worker_failed",
    "tts_worker_unavailable",
    "tts_synthesis_failed",
    "tts_load_timeout",
    "tts_audio_device_error",
    "invalid_generation_params",
    "invalid_gen_params",
    "openrouter_insufficient_credits",
    "validation_unavailable",
    "notebook_language_unknown",
    "invalid_response_shape",
  ] as const;

  it.each(FORMERLY_DEAD)("%s offers the reader something to do", (code) => {
    const msg = getErrorMessage(code);
    // Positive control: an unmapped code must NOT satisfy this test, or the
    // pattern below is matching the fallback and proving nothing.
    expect(msg).not.toBe(getErrorMessage("__no_such_code__"));
    // An imperative aimed at the reader: press, open, pick, add, try, check,
    // connect, move, reload, close, type, enable.
    expect(msg).toMatch(
      /\b(press|open|pick|choose|add|try|check|connect|enable|move|reload|close|type|start|send|shorten|keep)\b/i,
    );
  });

  it("tts_worker_unavailable points at the retry that actually reloads it", () => {
    // host.py's VoiceHost starts the worker on demand (_start_worker, reached
    // from the speak path), and no component uses useLoadVoice - there is no
    // Load button to send anyone looking for. Asking again IS the retry.
    expect(getErrorMessage("tts_worker_unavailable")).toMatch(/speak again/i);
  });
});

/**
 * The duplicates.
 *
 * Two pairs of codes carried byte-identical sentences, which makes them
 * indistinguishable to the only audience that matters. Both pairs are still
 * emitted (see the report), so both were differentiated rather than merged.
 */
describe("no two codes share a sentence", () => {
  it("openrouter_timeout and timeout read differently", () => {
    expect(getErrorMessage("openrouter_timeout")).not.toBe(
      getErrorMessage("timeout"),
    );
  });

  it("invalid_generation_params and invalid_gen_params read differently", () => {
    expect(getErrorMessage("invalid_generation_params")).not.toBe(
      getErrorMessage("invalid_gen_params"),
    );
  });

  it("no sentence in the map is used by two codes", () => {
    const seen = new Map<string, string>();
    const collisions: string[] = [];
    for (const code of knownErrorCodes()) {
      const msg = getErrorMessage(code);
      const first = seen.get(msg);
      if (first) collisions.push(`${first} / ${code}`);
      else seen.set(msg, code);
    }
    expect(collisions).toEqual([]);
  });
});

/**
 * The hygiene gate fails the build on an em or en dash, and the map is where
 * they get in: rewording under time pressure is exactly when a typographer's
 * dash gets pasted from somewhere else. Checked here so the gate is not the
 * first thing to notice.
 */
describe("punctuation", () => {
  // Built from code points rather than written as characters or as \u escapes:
  // the hygiene gate scans this file too, and the formatter rewrites an escape
  // back into the literal character it stands for - which is how the check
  // ended up tripping the exact rule it exists to enforce.
  const DASHES = [0x2013, 0x2014].map((c) => String.fromCharCode(c));

  it("no sentence uses an em dash or an en dash", () => {
    const offenders = knownErrorCodes().filter((code) =>
      DASHES.some((d) => getErrorMessage(code).includes(d)),
    );
    expect(offenders).toEqual([]);
  });

  it("GROUND: the check can see a dash when one is there", () => {
    // Without this, a broken DASHES list would report a clean map forever.
    const planted = `a${DASHES[1]}b`;
    expect(DASHES.some((d) => planted.includes(d))).toBe(true);
  });
});
