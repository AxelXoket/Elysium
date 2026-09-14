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
import {
  countedErrorCodes,
  getCountMessage,
  getErrorMessage,
  knownErrorCodes,
} from "@/lib/errors/errorMessages";

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
 * A build the engine cannot load is not a broken download.
 *
 * readiness.py reports tts_model_variant_unsupported when every file is
 * present and the T3 backbone names a Chatterbox build (Turbo, Multilingual
 * v3) the bundled engine has no loader for. Its neighbour's sentence says a
 * file "did not finish downloading", and a reader sent by that sentence
 * fetches the same files again and lands back here.
 */
describe("tts_model_variant_unsupported is not the incomplete sentence", () => {
  it("does not send the reader to download again", () => {
    const msg = getErrorMessage("tts_model_variant_unsupported");
    // Positive control: the fallback would pass the two absences below.
    expect(msg).not.toBe(getErrorMessage("__no_such_code__"));
    expect(msg).not.toMatch(/download/i);
    expect(msg).not.toMatch(/missing/i);
  });

  it("POSITIVE CONTROL: the incomplete sentence is the one about downloading", () => {
    expect(getErrorMessage("tts_model_incomplete")).toMatch(/download/i);
  });

  it("does not send the reader to reinstall the engine either", () => {
    // Before the worker carried this code its refusal arrived as
    // tts_worker_failed, whose sentence ends in "press Set up again": the
    // wrong errand about an engine that is fine. This sentence must not
    // repeat it.
    expect(getErrorMessage("tts_model_variant_unsupported")).not.toMatch(/set up/i);
  });

  it("POSITIVE CONTROL: tts_worker_failed is the sentence that says Set up", () => {
    expect(getErrorMessage("tts_worker_failed")).toMatch(/set up/i);
  });

  it("names a build the engine does load, so the reader knows what to fetch", () => {
    // adapters/chatterbox.py REQUIRED_FILES has exactly two loadable builds,
    // english and multilingual v2; the sentence names both.
    const msg = getErrorMessage("tts_model_variant_unsupported");
    expect(msg).toMatch(/English/);
    expect(msg).toMatch(/Multilingual v2/);
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
 * The one sentence shown for every rejected request in the tree.
 *
 * backend/main.py registers its RequestValidationError handler on the APP, not
 * on a router, so `validation_failed` answers a rejected vault passphrase, an
 * upload posted without its file, a notebook entry, a voice enum and a
 * generation parameter with the SAME words. It shipped once as
 * `invalid_generation_params`, whose sentence sends the reader to press Reset
 * all in Generation Settings - so a refused upload pointed at a destructive
 * button in a panel the reader had never opened. Nothing in this repository
 * could see that, because nothing pinned the one property that makes a
 * route-neutral code correct. This is that pin.
 */
describe("validation_failed is route-neutral", () => {
  const msg = () => getErrorMessage("validation_failed");

  //: Every panel this app can put in front of a reader, plus the control that
  //: made the original defect destructive rather than merely confusing.
  const PANELS = [
    "Generation Settings",
    "Reset all",
    "Settings",
    "Notebook",
    "Persona",
    "Character",
    "Models",
    "Voice",
    "Vault",
  ];

  it("names no panel and no button", () => {
    const named = PANELS.filter((p) => msg().includes(p));
    expect(named, `validation_failed points the reader at: ${named.join(", ")}`)
      .toEqual([]);
  });

  it("POSITIVE CONTROL: the same list catches a code that does name one", () => {
    // Without this, an emptied PANELS or a typo in every entry would leave the
    // assertion above green while the sentence pointed wherever it liked.
    const named = PANELS.filter((p) =>
      getErrorMessage("invalid_gen_params").includes(p));
    expect(named).toContain("Generation Settings");
    expect(named).toContain("Reset all");
  });

  it("says nothing was CHANGED, which is the clause that survives a DELETE", () => {
    // The handler also answers DELETE routes with typed query parameters, so
    // "nothing was saved" would be answering a question the reader did not
    // ask. "Changed" covers saved, deleted and sent at once.
    expect(msg()).toMatch(/nothing was changed/i);
  });

  it("offers an action the packaged window can actually perform", () => {
    // run_app.py calls webview.start() with no debug flag, so the Edge
    // Chromium backend leaves the browser accelerator keys and the default
    // context menu disabled: F5, Ctrl+R and right-click Reload are all dead,
    // and the only location.reload() in the tree sits behind ErrorBoundary's
    // crash card, which a toast never reaches. "Reload Elysium" is therefore
    // an instruction with nothing behind it.
    expect(msg()).not.toMatch(/\breload\b/i);
    expect(msg()).toMatch(/\b(try again|start it again)\b/i);
  });

  it("GROUND: the fallback would fail every check that has a discriminator", () => {
    // "Please try again." is IN the fallback, which is exactly why the check
    // above is an alternation and not a bare /try again/: the second arm is
    // the one no generic message carries. So the ground control pins the two
    // clauses that can actually tell this sentence from a shrug.
    const fallback = getErrorMessage("__no_such_code__");
    expect(msg()).not.toBe(fallback);
    expect(fallback).not.toMatch(/nothing was changed/i);
    expect(fallback).not.toMatch(/\bstart it again\b/i);
  });
});

/**
 * The instructions the packaged window cannot carry out.
 *
 * The check above pins one code. This one sweeps the whole map, because the
 * defect it exists for was never about `validation_failed`: three more
 * sentences said "Reload Elysium" at the same time, and the comment that
 * explained why reload is impossible sat eight lines above two of them. A pin
 * on one code cannot see the next sentence somebody writes.
 *
 * What makes an instruction impossible here: run_app.py calls
 * `webview.start()` with no `debug` flag, so the Edge Chromium backend leaves
 * `AreBrowserAcceleratorKeysEnabled` and `AreDefaultContextMenusEnabled` off.
 * F5, Ctrl+R and right-click Reload are dead, there is no address bar, no
 * menu bar, no devtools and no Back. Nothing in the renderer can put them
 * back: navigationGuard.ts refuses off-origin navigation outright, and there
 * is no host bridge to ask for a new window.
 *
 * Deliberately NOT banned: the bare verb "refresh". Seven codes use it for a
 * stale list rather than for the window, and rewriting those needs each
 * producing path measured for what the reader can actually reach - a
 * different job from this one, and inventing sentences without that
 * measurement is how the three "Reload" ones got written.
 */
describe("no sentence names an action the packaged window cannot perform", () => {
  //: Each pattern is one way of saying "use the browser's own chrome".
  const BROWSER_ONLY: readonly [string, RegExp][] = [
    ["reload", /\breload\b/i],
    ["refresh the page", /\brefresh the (page|window|app|browser)\b/i],
    ["hard refresh", /\bhard refresh\b/i],
    ["F5", /\bF5\b/],
    ["Ctrl+R", /\bctrl\s*[-+]?\s*r\b/i],
    ["Ctrl+Shift+R", /\bctrl\s*[-+]?\s*shift\s*[-+]?\s*r\b/i],
    ["right-click", /\bright[- ]click\b/i],
    ["context menu", /\bcontext menu\b/i],
    ["developer tools", /\bdev(eloper )?tools\b/i],
    ["address bar", /\baddress bar\b/i],
    ["back button", /\bback button\b/i],
    ["view source", /\bview source\b/i],
    ["your browser", /\byour browser\b/i],
    ["incognito", /\bincognito\b/i],
  ];

  const forbiddenIn = (text: string): string[] =>
    BROWSER_ONLY.filter(([, re]) => re.test(text)).map(([name]) => name);

  /** Every sentence this module can put in front of a reader, counted forms
   *  in both their singular and plural shapes, plus the fallback. */
  const everySentence = (): [string, string][] => [
    ...knownErrorCodes().map(
      (code) => [code, getErrorMessage(code)] as [string, string],
    ),
    ...countedErrorCodes().flatMap(
      (code) =>
        [
          [`${code} (1)`, getCountMessage(code, 1)],
          [`${code} (2)`, getCountMessage(code, 2)],
        ] as [string, string][],
    ),
    ["__fallback__", getErrorMessage("__no_such_code__")],
  ];

  it("not one of them", () => {
    const offenders = everySentence()
      .map(([label, text]) => [label, forbiddenIn(text)] as const)
      .filter(([, hits]) => hits.length > 0)
      .map(([label, hits]) => `${label}: ${hits.join(", ")}`);
    expect(offenders, "sentences telling the reader to use browser chrome")
      .toEqual([]);
  });

  it("POSITIVE CONTROL: the sweep sees every one of them when it IS there", () => {
    // Without this, one mistyped pattern would go quietly dead and the sweep
    // above would report a clean map forever. Each plant is written the way a
    // person would actually write it, not as the regex reversed.
    const PLANTS: Record<string, string> = {
      "reload": "Reload Elysium and write it again.",
      "refresh the page": "Please refresh the page and try again.",
      "hard refresh": "Do a hard refresh, then retry.",
      "F5": "Press F5 to try again.",
      "Ctrl+R": "Press Ctrl+R to load it again.",
      "Ctrl+Shift+R": "Press Ctrl + Shift + R to load it again.",
      "right-click": "Right-click the page and choose Reload.",
      "context menu": "Open the context menu and pick Reload.",
      "developer tools": "Open the developer tools and read the console.",
      "address bar": "Type the address again in the address bar.",
      "back button": "Press the back button and try once more.",
      "view source": "Use view source to check what arrived.",
      "your browser": "Close your browser and open it again.",
      "incognito": "Try again in an incognito window.",
    };
    const blind = BROWSER_ONLY.filter(
      ([name]) => !forbiddenIn(PLANTS[name]).includes(name),
    ).map(([name]) => name);
    expect(blind, "patterns that cannot see their own instruction").toEqual([]);
    // And the map's own sentences are not what makes the plants trip: the
    // sweep must be finding the planted text, not matching everything.
    expect(forbiddenIn(getErrorMessage("boundary_empty"))).toEqual([]);
  });

  it("the codes that HAVE no in-window fix say so the one way that works", () => {
    // Nothing the reader can reach caused any of these, so the sentence has to
    // end somewhere. Closing the window and starting Elysium again is the only
    // recovery the packaged app actually has - and the phrasing is shared so
    // five sentences do not describe the same act five ways.
    const RESTART = [
      "validation_failed",
      "invalid_response_shape",
      "notebook_entry_invalid",
      "notebook_reorder_incomplete",
      "boundary_invalid",
    ] as const;
    const silent = RESTART.filter(
      (code) => !/close Elysium and start it again/i.test(getErrorMessage(code)),
    );
    expect(silent, "codes that lost the only recovery they had").toEqual([]);
  });

  it("GROUND: the fallback carries neither the ban nor the cure", () => {
    // The two checks above would both pass on a map that had quietly become
    // nothing but fallbacks, which is the failure this rules out.
    const fallback = getErrorMessage("__no_such_code__");
    expect(forbiddenIn(fallback)).toEqual([]);
    expect(fallback).not.toMatch(/close Elysium and start it again/i);
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
