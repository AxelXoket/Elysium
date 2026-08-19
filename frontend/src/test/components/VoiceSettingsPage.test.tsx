/**
 * V5-b - Settings › Voice, rendered against a mocked wire.
 *
 * The page's promises, each pinned: a model is ALWAYS inspectable but its
 * blockers are listed in the same words as every other error surface; the
 * schema-driven controls render from ParamSpec descriptors alone (no engine
 * knowledge in the page); saving sends ONLY what changed; engine setup is one
 * button with a live log line and a cancel; transcripts stay editable.
 */
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceSettingsPage } from "@/components/settings/VoiceSettingsPage";
import { getErrorMessage } from "@/lib/errors";
import { useErrorStore } from "@/lib/errors/errorStore";
import { mockFetch } from "../mocks/api";
import { delayRoute } from "../helpers/delayRoute";


const VOICE_MODE = { enabled: false, active: false, prompt_chars: 3200 };

const RUNTIMES = {
  runtimes: [{ engine_id: "fish_s2", state: "missing", python: null, error_code: "tts_runtime_missing" }],
  engines: [{ engine_id: "fish_s2", display_name: "Fish Audio S2 Pro" }],
};

const IDLE_JOB = {
  engine_id: "fish_s2", state: "idle", log: [], error_code: null,
  error_detail: "", running: false,
};

const BLOCKED_MODEL = {
  uid: "u1", engine_id: "fish_s2", name: "s2-pro", path: "C:/m/s2",
  variant: null, source: "signature", incomplete: false, missing: [],
  readiness: {
    uid: "u1", engine_id: "fish_s2", runnable: false, settings_available: true,
    runtime_state: "missing",
    issues: [
      { code: "tts_runtime_missing", severity: "blocker", detail: "", transient: false, action: "setup_runtime" },
      { code: "tts_gpu_unavailable", severity: "blocker", detail: "", transient: false, action: null },
    ],
    languages: ["en"], fit: null,
  },
};

const SCHEMA = {
  uid: "u1", engine_id: "fish_s2", display_name: "Fish Audio S2 Pro",
  variant: null,
  capabilities: {
    voice_cloning: true, needs_reference_transcript: true,
    inline_prosody_tags: true, streaming: false, languages: ["en"],
    native_sample_rate: 44100,
  },
  params: [
    { name: "temperature", type: "float", default: 0.7, label: "Expressiveness",
      help: "", minimum: 0.05, maximum: 1.5, step: 0.05, choices: null,
      group: "quality", advanced: false },
    { name: "language", type: "enum", default: "en", label: "Language",
      help: "", minimum: null, maximum: null, step: null,
      choices: ["en", "tr"], group: "voice", advanced: false },
  ],
};

const SETTINGS = { uid: "u1", values: { temperature: 0.7, language: "en" }, source_map: {} };

function baseRoutes() {
  return {
    "/tts/voice-mode": { body: VOICE_MODE },
    "/tts/runtimes/fish_s2/install": { body: IDLE_JOB },
    "/tts/runtimes": { body: RUNTIMES },
    "/tts/models/u1/schema": { body: SCHEMA },
    "/tts/models/u1/settings": { body: SETTINGS },
    "/tts/models": {
      body: { models: [BLOCKED_MODEL], unrecognized: [], roots: [] },
    },
    "/tts/active": {
      body: { uid: null, state: "unloaded", engine_id: null, vram_mb: null, error_code: null, readiness: null },
    },
    "/tts/voices": { body: { voices: [] } },
  };
}

describe("VoiceSettingsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("lists a model with EVERY blocker, in the shared error words", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    expect(await screen.findByText("s2-pro")).toBeInTheDocument();
    expect(
      screen.getByText("Cannot run yet - open settings for details"),
    ).toBeInTheDocument();

    const issues = await screen.findByLabelText("Why it will not run");
    // Both blockers at once - fix-one-discover-the-next is the banned shape.
    expect(issues).toHaveTextContent(getErrorMessage("tts_runtime_missing"));
    expect(issues).toHaveTextContent(getErrorMessage("tts_gpu_unavailable"));
  });

  it("opens the settings of a model that cannot run - the core promise", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.click(await screen.findByLabelText("s2-pro settings"));
    // Schema-driven controls appear even though the model has blockers.
    expect(await screen.findByLabelText("Expressiveness slider")).toBeInTheDocument();
    expect(screen.getByLabelText("Language")).toBeInTheDocument();
  });

  it("saving sends only the values that actually changed", async () => {
    // The method-scoped route must PRECEDE the spread: mockFetch is
    // first-match-wins, so baseRoutes' method-less "/tts/models/u1/settings"
    // was swallowing this POST and the echo below never came back. Found in
    // KADEME 19b by asserting the CONSEQUENCE - with only the request body
    // checked, the override could sit here dead and nobody would know.
    const fetchMock = mockFetch({
      "POST /tts/models/u1/settings": {
        body: { uid: "u1", values: { temperature: 1.2, language: "en" }, source_map: {} },
      },
      ...baseRoutes(),
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.click(await screen.findByLabelText("s2-pro settings"));
    const slider = await screen.findByLabelText("Expressiveness slider");
    // range inputs do not respond to userEvent typing; fire a change directly.
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(slider, { target: { value: "1.2" } });

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/tts/models/u1/settings") &&
          init?.method === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(call![1]!.body as string)).toEqual({
        values: { temperature: 1.2 },
      });
    });

    // KADEME 19b: the request body was the trigger. Once the server echoes
    // the value back, the draft is retired and the button has to go quiet
    // again - otherwise "Save changes" stays lit over nothing and the next
    // press sends an empty change set.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save changes" }),
        "the save button stayed lit after the value landed",
      ).toBeDisabled(),
    );
  });

  // ── Audit HIGH: edits made while a save is in flight were discarded ────
  //
  // The render-phase "fresh server values retire the draft" reset blanket
  // cleared the WHOLE draft whenever settings.data changed identity - and
  // useSaveTtsSettings writes its response through with setQueryData, so that
  // happened after every save. A parameter edited again while the POST flew
  // snapped back with no error, no toast and no sign it had been dropped.

  it("keeps a parameter edited again while the save is in flight", async () => {
    const { fireEvent } = await import("@testing-library/react");
    let releaseSave!: (r: Response) => void;
    const fetchMock = mockFetch(baseRoutes());
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url, init) => {
      if (String(url).includes("/tts/models/u1/settings") && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          releaseSave = resolve;
        });
      }
      return original(url, init);
    });

    renderWithQueryClient(<VoiceSettingsPage />);
    await userEvent.click(await screen.findByLabelText("s2-pro settings"));

    fireEvent.change(await screen.findByLabelText("Expressiveness slider"), {
      target: { value: "1.2" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    // Second edit, while the POST is still flying.
    const language = screen.getByLabelText("Language");
    fireEvent.change(language, { target: { value: "tr" } });
    expect(language).toHaveValue("tr");

    // The response lands, carrying only what was sent (clamped, so its arrival
    // is observable on the slider).
    releaseSave(
      new Response(
        JSON.stringify({
          uid: "u1",
          values: { temperature: 0.9, language: "en" },
          source_map: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await waitFor(() => {
      expect(screen.getByLabelText("Expressiveness slider")).toHaveValue("0.9");
    });

    // The edit made mid-flight survived, and is still savable.
    expect(screen.getByLabelText("Language")).toHaveValue("tr");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("a saved value that the server clamped shows the clamped value", async () => {
    const { fireEvent } = await import("@testing-library/react");
    mockFetch({
      // BEFORE the method-less route below - first match wins.
      "POST /tts/models/u1/settings": {
        body: { uid: "u1", values: { temperature: 0.9, language: "en" }, source_map: {} },
      },
      ...baseRoutes(),
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    await userEvent.click(await screen.findByLabelText("s2-pro settings"));

    const slider = await screen.findByLabelText("Expressiveness slider");
    fireEvent.change(slider, { target: { value: "1.2" } });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Expressiveness slider")).toHaveValue("0.9");
    });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  // ── Audit: "Add clip" derives the voice id, and POST is an UPSERT ──────

  it("an id collision asks before destroying the existing clip", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: "anna", label: "Anna", path: "C:/v/anna",
              audio_name: "ref.wav", transcript: "hand corrected",
              transcript_source: "user", seconds: 10, needs_conversion: false,
            },
          ],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    const nameInput = await screen.findByLabelText("New voice name");
    await userEvent.type(nameInput, "Anna");
    const file = new File(["x"], "take2.wav", { type: "audio/wav" });
    await userEvent.upload(
      screen.getByLabelText("Upload a reference clip"),
      file,
    );

    // Nothing uploaded yet - the old recording is still there.
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).includes("/tts/voices/anna") && init?.method === "POST",
      ),
    ).toHaveLength(0);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /already exists/i,
    );

    await userEvent.click(screen.getByRole("button", { name: "Replace it" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(
          ([url, init]) =>
            String(url).includes("/tts/voices/anna") && init?.method === "POST",
        ),
      ).toHaveLength(1);
    });
  });

  it("declining the collision uploads nothing", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: "anna", label: "Anna", path: "C:/v/anna",
              audio_name: "ref.wav", transcript: "hand corrected",
              transcript_source: "user", seconds: 10, needs_conversion: false,
            },
          ],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.type(await screen.findByLabelText("New voice name"), "Anna");
    await userEvent.upload(
      screen.getByLabelText("Upload a reference clip"),
      new File(["x"], "take2.wav", { type: "audio/wav" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Keep the old one" }),
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).includes("/tts/voices/anna") && init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("a fresh name uploads immediately, with no prompt", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.type(await screen.findByLabelText("New voice name"), "Bella");
    await userEvent.upload(
      screen.getByLabelText("Upload a reference clip"),
      new File(["x"], "take.wav", { type: "audio/wav" }),
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(
          ([url, init]) =>
            String(url).includes("/tts/voices/bella") && init?.method === "POST",
        ),
      ).toHaveLength(1);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("engine setup is one button that flips into a cancellable live log", async () => {
    const runningJob = {
      engine_id: "fish_s2", state: "installing",
      log: ["Resolved 174 packages", "downloading torch"],
      error_code: null, error_detail: "", running: true,
    };
    mockFetch({
      ...baseRoutes(),
      "POST /tts/runtimes/fish_s2/install": { body: runningJob },
      "/tts/runtimes/fish_s2/install": { body: runningJob },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    const row = await screen.findByTestId("engine-fish_s2");
    expect(row).toHaveTextContent("Fish Audio S2 Pro");
    expect(row).toHaveTextContent("Not set up yet");

    await userEvent.click(screen.getByRole("button", { name: "Set up" }));
    // The live log line replaces the description; cancel appears.
    expect(await screen.findByText("downloading torch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
  });

  it("a failed install says why, through the shared error map", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/runtimes/fish_s2/install": {
        body: {
          engine_id: "fish_s2", state: "failed", log: ["setup failed"],
          error_code: "tts_insufficient_disk", error_detail: "", running: false,
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findByText(getErrorMessage("tts_insufficient_disk")),
    ).toBeInTheDocument();
    // And the button reads as a retry, not a fresh start.
    expect(screen.getByRole("button", { name: "Set up again" })).toBeInTheDocument();
  });

  it("an auto transcript is labelled as a draft and stays editable", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [{
            voice_id: "ayse", label: "Ayse", audio_name: "ref.wav",
            transcript: "your mind", transcript_source: "auto",
            seconds: 8.4, needs_conversion: false, has_transcript: true,
          }],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    // The status shares a node with the duration prefix ("8.4s · …").
    expect(
      await screen.findByText(/Transcript drafted by the engine - check it/),
    ).toBeInTheDocument();
    const box = screen.getByLabelText("Words spoken in Ayse");
    expect(box).toHaveValue("your mind");
    await userEvent.clear(box);
    await userEvent.type(box, "you're mine");
    expect(screen.getByRole("button", { name: "Save words" })).toBeEnabled();
  });

  it("the voice toggle reflects and mutates voice mode", async () => {
    // Method-scoped route first - see the note on the failure test below.
    // This override was dead until KADEME 19b: baseRoutes' method-less
    // "/tts/voice-mode" answered the POST with the OFF payload, so the
    // write-through wrote `enabled: false` back. Only the request body was
    // asserted, so nothing noticed.
    const fetchMock = mockFetch({
      "POST /tts/voice-mode": {
        body: { enabled: true, active: false, prompt_chars: 3200 },
      },
      ...baseRoutes(),
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    const toggle = await screen.findByRole("switch", { name: "Performed replies" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await userEvent.click(toggle);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/tts/voice-mode") && init?.method === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(call![1]!.body as string)).toEqual({ enabled: true });
    });

    // KADEME 19b: the POST was the trigger; this is what the person sees.
    // The mutation writes through to the query cache on success, so the
    // switch and its caption both have to follow. Asserting only the request
    // left `aria-checked={enabled}` free to be hardcoded false.
    await waitFor(() =>
      expect(toggle, "the switch never moved").toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    expect(await screen.findByText(/^On - /)).toBeInTheDocument();
  });
});

describe("audit-2 additions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("a failed voice-mode toggle reaches the error store - never silence", async () => {
    // The method-scoped route must PRECEDE the spread: mockFetch is
    // first-match-wins and baseRoutes' method-less "/tts/voice-mode" would
    // otherwise swallow the POST.
    mockFetch({
      "POST /tts/voice-mode": {
        status: 500,
        body: { detail: "tts_worker_failed" },
      },
      ...baseRoutes(),
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    const toggle = await screen.findByRole("switch", { name: "Performed replies" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    await waitFor(() => {
      expect(
        useErrorStore.getState().errors.some(
          (e) => e.code === "tts_worker_failed",
        ),
      ).toBe(true);
    });
  });

  it("an enum value the choices no longer contain stays visible as (saved)", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models/u1/settings": {
        body: {
          uid: "u1",
          values: { temperature: 0.7, language: "legacy-choice" },
          source_map: {},
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    await userEvent.click(await screen.findByLabelText("s2-pro settings"));
    const select = await screen.findByLabelText("Language");
    expect(select).toHaveValue("legacy-choice");
    expect(
      screen.getByRole("option", { name: "legacy-choice (saved)" }),
    ).toBeInTheDocument();
  });

  it("a failed model scan reads as an error, not as an empty library", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": { status: 500, body: { detail: "internal_error" } },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findByText("Could not scan for voice models. Rescan to retry."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No voice models found/),
    ).not.toBeInTheDocument();
  });

  it("asks for a model folder when the scan worked and found none", async () => {
    // POSITIVE CONTROL for the line above. That assertion passes by finding
    // nothing, and nothing in this file ever rendered the empty-list state -
    // so a typo in the pattern would have passed just as quietly. It is also
    // a real state worth pinning: a scan that succeeds and finds nothing must
    // say what to do, not repeat the failure copy.
    mockFetch({
      ...baseRoutes(),
      "/tts/models": { body: { models: [], unrecognized: [], roots: [] } },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText(/No voice models found/)).toBeInTheDocument();
    expect(
      screen.queryByText("Could not scan for voice models. Rescan to retry."),
    ).not.toBeInTheDocument();
  });
});

describe("V6 install presentation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  const PLAN = {
    engine_id: "fish_s2", env_dir: "C:/voice/envs/fish_s2",
    requirements: "fish_s2.txt", python_version: "3.12",
    download_mb: 4200, gpu_available: true,
  };

  it("shows the REAL download size before the user commits", async () => {
    mockFetch({ "/tts/runtimes/fish_s2/plan": { body: PLAN }, ...baseRoutes() });
    renderWithQueryClient(<VoiceSettingsPage />);
    // 4200 MB -> "about 4.1 GB", not the old hand-waved "a few GB".
    expect(await screen.findByText(/about 4\.1 GB to download, once/)).toBeInTheDocument();
  });

  it("a running install shows the phase in words, a bar, and the live log", async () => {
    const runningJob = {
      engine_id: "fish_s2", state: "verifying",
      log: ["checking torch"], error_code: null, error_detail: "", running: true,
    };
    // IDENTICAL key to one in baseRoutes, so the override goes AFTER the
    // spread (last wins on a key collision). Method-scoped keys are the
    // opposite case - those go BEFORE, because mockFetch is first-match-wins.
    mockFetch({
      ...baseRoutes(),
      "/tts/runtimes/fish_s2/install": { body: runningJob },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    // The phase a person can act on ("do not close this"), not the raw enum.
    expect(
      await screen.findByText("Checking that it actually works…"),
    ).toBeInTheDocument();
    const bar = await screen.findByRole("progressbar", {
      name: /Setting up Fish Audio S2 Pro/,
    });
    // Indeterminate ON PURPOSE - uv reports no byte progress through a pipe,
    // so any percentage here would be invented.
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText("checking torch")).toBeInTheDocument();
  });

  it("an installed engine shows no progress bar and asks for no plan", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/runtimes": {
        body: {
          runtimes: [{ engine_id: "fish_s2", state: "ready", python: "py.exe", error_code: null }],
          engines: [{ engine_id: "fish_s2", display_name: "Fish Audio S2 Pro" }],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText("Installed and ready")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    // No pointless round trip for a size nobody is deciding on.
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes("/plan")),
    ).toBe(false);
  });
});

/**
 * The control that arms voice at all.
 *
 * Selecting a model is what makes the per-message Speak button, the live-speak
 * button and the composer toggle render - all three return null without it. It
 * used to be an unlabelled 18px circle, while clicking the obvious target (the
 * model name) opened the parameter panel instead. So a person could install an
 * engine, download a model and record a reference voice and still see nothing
 * in the chat, with nothing anywhere to explain why.
 */
describe("VoiceSettingsPage - choosing the voice", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("selects the model when its NAME is clicked", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.click(await screen.findByText("s2-pro"));

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).includes("/tts/active") && init?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]!.body as string)).toEqual({ uid: "u1" });
    });
  });

  it("says what selecting will do, before you do it", async () => {
    // KADEME 19b rewrote this. It used to render TWICE into the same DOM
    // (once with the blocked model, once with a runnable one) and then assert
    // `findAllByText(...).not.toHaveLength(0)` across the merged result. That
    // is the banned shape: making every model - blocked ones included - claim
    // readiness still left it green, because a match anywhere satisfied it.
    // One render, an exact count, and the other direction below.
    const runnable = {
      ...BLOCKED_MODEL,
      readiness: { ...BLOCKED_MODEL.readiness, runnable: true, issues: [] },
    };
    mockFetch({
      ...baseRoutes(),
      "/tts/models": { body: { models: [runnable], unrecognized: [], roots: [] } },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findAllByText("Ready to use - select it to speak replies"),
    ).toHaveLength(1);
  });

  it("does NOT tell you to select a model that cannot run", async () => {
    // The other half, and the half that was missing: an invitation to press
    // something that can only fail is the same broken promise the speak
    // button avoids by not appearing at all.
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: { models: [BLOCKED_MODEL], unrecognized: [], roots: [] },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    await screen.findByText(BLOCKED_MODEL.name ?? BLOCKED_MODEL.uid);
    expect(
      screen.queryByText("Ready to use - select it to speak replies"),
    ).not.toBeInTheDocument();
  });

  it("keeps settings behind their own control, not the name", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    // The disclosure is a separate target with its own label.
    const disclosure = await screen.findByLabelText("s2-pro settings");
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
  });

  it("exposes the pick control as a radio that says whether it is chosen", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);
    const pick = await screen.findByRole("radio", { name: "Use s2-pro" });
    expect(pick).toHaveAttribute("aria-checked", "false");
    expect(pick).toHaveTextContent("s2-pro");
  });
});

/**
 * The Settings toggle and the composer button are DIFFERENT controls, and the
 * copy has to make that obvious.
 *
 *   Settings "Performed replies"  -> tts_voice_enabled (encrypted DB). Injects
 *                                    VOICE_PROMPT into every request, so the
 *                                    model writes [low voice] style directions.
 *   Composer button               -> uiStore.continuousVoice (localStorage).
 *                                    Sends speak:true, so the reply is spoken
 *                                    live as it streams.
 *
 * The old description read "Off - chat stays text-only", which describes the
 * wrong thing entirely: speaking works either way. Somebody who only ever used
 * the per-message Speak button read that, concluded it was not for them, and
 * never once heard the performed voice they had installed an engine for.
 */
describe("VoiceSettingsPage - what the voice toggle actually controls", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("says the prompt injection is what the toggle does", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    const hint = await screen.findByText(/added to every request/i);
    expect(hint).toHaveTextContent(/how/i);
    expect(hint).toHaveTextContent(/never appear in the chat/i);
  });

  it("does not claim the chat is silent when it is off", async () => {
    // Speaking works with this off - the per-message button and the composer's
    // continuous-voice button are both independent of it.
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await screen.findByText("Performed replies");
    // KADEME 19b removed a line here that read
    // `queryByText(/chat stays text-only/i)).not.toBeInTheDocument()`.
    // That phrase exists in the whole tree only inside a COMMENT in
    // VoiceSettingsPage.tsx explaining why the old copy was dropped - it is
    // never rendered, in any state, so the assertion could not fail and was
    // not testing the deletion either. The line below is the real guard: it
    // pins the words that replaced it.
    expect(
      await screen.findByText("Off - replies are written plainly"),
    ).toBeInTheDocument();
  });

  it("tells the user speaking still works without it", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findByText(/works with this off too/i),
    ).toBeInTheDocument();
  });
});

/**
 * The "Performed replies" caption - pending / error / resolved.
 *
 * `data?.enabled ?? false` used to make the caption say "Off - replies are
 * written plainly" to a user whose GET /tts/voice-mode had not answered yet,
 * even one who had it ON. The switch stayed disabled the whole time (`data
 * == null` in the disabled expression), so no wrong click was ever possible
 * - only the sentence was a false statement of the user's own configuration.
 * These tests hold the fetch open with delayRoute so the assertion lands
 * inside that window, not after it.
 */
describe("Performed replies caption - pending must not read as Off", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("says it is checking, not Off, while the toggle would resolve to ON", async () => {
    const ON = { enabled: true, active: false, prompt_chars: 3200 };
    const fetchMock = mockFetch({ ...baseRoutes(), "/tts/voice-mode": { body: ON } });
    const release = delayRoute(fetchMock, "GET", "/tts/voice-mode", { body: ON });
    renderWithQueryClient(<VoiceSettingsPage />);

    const toggle = screen.getByRole("switch", { name: "Performed replies" });
    expect(toggle).toHaveAttribute("data-state", "pending");
    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(
      screen.queryByText("Off - replies are written plainly"),
    ).not.toBeInTheDocument();
    // The ground rule the pending fix must not break: still no click possible.
    expect(toggle).toBeDisabled();

    release();
    await waitFor(() => {
      expect(
        screen.getByText("On - select a voice model below to hear them performed"),
      ).toBeInTheDocument();
    });
  });

  it("reports an unreachable voice-mode fetch as its own state, not Off", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/voice-mode": { status: 500, body: { detail: "internal_error" } },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    expect(
      await screen.findByText("Could not check whether performed replies are on."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Off - replies are written plainly"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Performed replies" }),
    ).toHaveAttribute("data-state", "error");
  });

  // GROUND: a genuinely-off setup still reports Off once the query actually
  // resolves - the fix must not silence the true state.
  it("still reports Off once voice-mode resolves for real - the ground", async () => {
    mockFetch(baseRoutes()); // VOICE_MODE.enabled is false
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findByText("Off - replies are written plainly"),
    ).toBeInTheDocument();
  });

  // POSITIVE CONTROL.
  it("reports On once voice-mode resolves for real - the positive control", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/voice-mode": {
        body: { enabled: true, active: false, prompt_chars: 3200 },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(
      await screen.findByText("On - select a voice model below to hear them performed"),
    ).toBeInTheDocument();
  });
});
