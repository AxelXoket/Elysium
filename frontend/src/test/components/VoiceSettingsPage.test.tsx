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
import { TtsScanSchema } from "@/lib/schemas/tts";
import { useErrorStore } from "@/lib/errors/errorStore";
import { mockFetch } from "../mocks/api";
import { delayRoute } from "../helpers/delayRoute";
import {
  chooseOption,
  expectClosed,
  expectKeyboardContract,
  expectOptionsOnTheOptionStep,
  expectPlacedCollisionPadding,
  openPositioner,
  openSelect,
} from "../helpers/select";
import { POPUP_COLLISION_PADDING } from "@/lib/theme/popupPositioning";


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
    await chooseOption(userEvent, language, "tr");
    expect(language).toHaveTextContent("tr");

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
    expect(screen.getByLabelText("Language")).toHaveTextContent("tr");
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

  // ── Audit: the voice id must never be built from what the user typed ───
  //
  // The id used to be a slug of the typed name (or, failing that, the
  // filename), and that slug became a URL path segment, a folder name on
  // disk, and the text in the collision dialog itself. The rule it broke:
  // a name a person reads on screen never sits outside the vault, and a
  // roleplay reference voice is named after the character it belongs to. The
  // id is opaque now (a UUID the label plays no part in producing), the
  // collision question moved to comparing what voices are CALLED rather than
  // their ids, and "replace" reuses the existing voice's id instead of
  // minting a new one beside it.

  const EXISTING_ANNA_ID = "3f2a9c11-58cc-4372-a567-0e02b2c3d479";

  function voicePostCalls(fetchMock: ReturnType<typeof mockFetch>) {
    return fetchMock.mock.calls.filter(
      ([url, init]) =>
        String(url).includes("/tts/voices/") && init?.method === "POST",
    );
  }

  it("the decisive test: a distinctive typed name reaches the server in the body, never in a URL", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    const distinctiveName = "Persimmon Vale Ashworth";
    await userEvent.type(
      await screen.findByLabelText("New voice name"),
      distinctiveName,
    );
    await userEvent.upload(
      screen.getByLabelText("Upload a reference clip"),
      new File(["x"], "take.wav", { type: "audio/wav" }),
    );

    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(1));
    const [url, init] = voicePostCalls(fetchMock)[0];

    // The name is nowhere in the URL - not verbatim, not slugified.
    expect(String(url).toLowerCase()).not.toContain("persimmon");
    expect(String(url).toLowerCase()).not.toContain("ashworth");
    // The id really is opaque, not just "not the name".
    const idSegment = String(url).split("/tts/voices/")[1];
    expect(idSegment).toMatch(
      /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,
    );

    // The name DID reach the server - in the body, not the path.
    const form = init!.body as FormData;
    expect(form.get("label")).toBe(distinctiveName);
  });

  // GROUND: nothing typed still has to work - refusing to upload without a
  // name would be its own broken promise.
  it("the ground: a voice with no typed name still uploads", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.upload(
      await screen.findByLabelText("Upload a reference clip"),
      new File(["x"], "sunday-clip.wav", { type: "audio/wav" }),
    );

    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(1));
    const [url, init] = voicePostCalls(fetchMock)[0];
    // The file's own name is not a leak this app created - but it must not
    // become the id either, same as a typed name would not.
    expect(String(url).toLowerCase()).not.toContain("sunday-clip");
    const idSegment = String(url).split("/tts/voices/")[1];
    expect(idSegment).toMatch(
      /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,
    );
    // The label still falls back to something readable, drawn from the
    // user's own file, so the list does not show a raw id as a name.
    const form = init!.body as FormData;
    expect(form.get("label")).toBe("sunday-clip");
  });

  // POSITIVE CONTROL: the label really does render back where the person
  // can see it, and the opaque id never does.
  it("the positive control: the label renders back in the list, the id never does", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: EXISTING_ANNA_ID,
              label: "Zinnia Combe",
              audio_name: "ref.wav",
              transcript: "",
              transcript_source: "none",
              seconds: 9.5,
              needs_conversion: false,
              has_transcript: false,
            },
          ],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    expect(await screen.findByText("Zinnia Combe")).toBeInTheDocument();
    expect(screen.queryByText(/3f2a9c11/)).not.toBeInTheDocument();
  });

  it("a matching label asks before destroying the existing clip, and reuses its id to replace it", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: EXISTING_ANNA_ID, label: "Anna", path: "C:/v/anna",
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
    expect(voicePostCalls(fetchMock)).toHaveLength(0);
    // The dialog names the voice, not the id (there was no id to derive from).
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Anna.*already exists/i,
    );

    await userEvent.click(screen.getByRole("button", { name: "Replace it" }));
    await waitFor(() => {
      const calls = voicePostCalls(fetchMock);
      expect(calls).toHaveLength(1);
      // Lands on the EXISTING voice's id - a fresh one here would leave the
      // old voice behind under its own id instead of replacing it.
      expect(String(calls[0][0])).toContain(`/tts/voices/${EXISTING_ANNA_ID}`);
    });
  });

  // ── the overwrite an invented fallback name used to cause ────────────────
  //
  // The panel used to substitute the fixed string "Untitled voice" whenever
  // neither a typed name nor a usable filename existed. Two unnamed uploads
  // therefore carried the SAME label, the collision check below matched them,
  // and the second was sent under the first voice's id - a replace nobody
  // asked for, which destroyed the first clip and the transcript the user had
  // corrected by hand. The fallback is gone: an unnamed upload travels with an
  // empty label and the server numbers it.

  it("an unnamed upload never lands on an existing voice's id", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: EXISTING_ANNA_ID, label: "", path: "C:/v/anna",
              audio_name: "ref.wav", transcript: "hand corrected",
              transcript_source: "user", seconds: 10, needs_conversion: false,
            },
          ],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    // No typed name, and a filename that leaves nothing behind once the
    // extension is stripped - the exact case the invented fallback existed
    // for.
    await userEvent.upload(
      await screen.findByLabelText("Upload a reference clip"),
      new File(["x"], ".wav", { type: "audio/wav" }),
    );

    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(1));
    const [url, init] = voicePostCalls(fetchMock)[0];

    // Not a replace: a fresh id, so the stored clip and its transcript stay.
    expect(String(url)).not.toContain(EXISTING_ANNA_ID);
    expect(String(url).split("/tts/voices/")[1]).toMatch(
      /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,
    );
    // No invented name rides along; the server picks one. The transport
    // leaves an empty label OUT of the form rather than sending "", and the
    // route reads a missing field as "" - so both spellings of "nothing" are
    // accepted here and any actual string is a failure.
    const sentLabel = (init!.body as FormData).get("label") ?? "";
    expect(sentLabel, "the panel invented a name again").toBe("");
    // And no dialog claimed a collision that was never real.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("two unnamed uploads are two voices, not one overwritten twice", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    const input = await screen.findByLabelText("Upload a reference clip");
    await userEvent.upload(input, new File(["a"], ".wav", { type: "audio/wav" }));
    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(1));
    await userEvent.upload(input, new File(["b"], ".wav", { type: "audio/wav" }));
    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(2));

    const ids = voicePostCalls(fetchMock).map(
      ([url]) => String(url).split("/tts/voices/")[1],
    );
    expect(ids[0], "the second unnamed clip replaced the first").not.toBe(
      ids[1],
    );
  });

  it("declining the collision uploads nothing", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "/tts/voices": {
        body: {
          voices: [
            {
              voice_id: EXISTING_ANNA_ID, label: "Anna", path: "C:/v/anna",
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
    expect(voicePostCalls(fetchMock)).toHaveLength(0);
  });

  it("a fresh name uploads immediately, with no prompt", async () => {
    const fetchMock = mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    await userEvent.type(await screen.findByLabelText("New voice name"), "Bella");
    await userEvent.upload(
      screen.getByLabelText("Upload a reference clip"),
      new File(["x"], "take.wav", { type: "audio/wav" }),
    );

    await waitFor(() => expect(voicePostCalls(fetchMock)).toHaveLength(1));
    expect(String(voicePostCalls(fetchMock)[0][0]).toLowerCase()).not.toContain(
      "bella",
    );
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

  it("an engine that failed its trust check says so, and its button is the one the sentence names", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/runtimes": {
        body: {
          ...RUNTIMES,
          runtimes: [{
            engine_id: "fish_s2", state: "untrusted",
            python: "C:/elsewhere/python.exe", error_code: "tts_runtime_untrusted",
          }],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);

    const row = await screen.findByTestId("engine-fish_s2");
    expect(row).toHaveTextContent(getErrorMessage("tts_runtime_untrusted"));
    // That sentence ends "Press Set up again in Settings". Until this test the
    // button under it read "Set up", a control the sentence does not name.
    expect(screen.getByRole("button", { name: "Set up again" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set up" })).not.toBeInTheDocument();
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
    // The trigger shows the stored value, marked; the option is in the
    // list once it is opened.
    expect(select).toHaveTextContent("legacy-choice (saved)");
    await openSelect(userEvent, select);
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
    engine_id: "fish_s2", env_dir: "<env dir>",
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

  it("A8: the hint's example marks are neutral", async () => {
    mockFetch(baseRoutes());
    renderWithQueryClient(<VoiceSettingsPage />);

    const hint = await screen.findByText(/added to every request/i);
    // Positive control: the hint still shows example marks.
    expect(hint).toHaveTextContent("[low voice]");
    expect(hint.textContent ?? "").not.toMatch(/breathless|seductive|husky|intima/i);
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

describe("Voice models - the scan says when it could not look everywhere", () => {
  // The backend computed `truncated` and named the roots it could not open,
  // and the schema dropped the first and had no field for the second, so a
  // walk cut at the cap and a mistyped extra root both rendered as a complete,
  // healthy list. Detection with no carrier, the KÖK 1 shape.
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  const CUT = /may be missing models/;
  const COULD_NOT_LOOK = /Could not look in/;

  it("the schema carries truncated and unreadable_roots, and defaults them when absent", () => {
    const full = TtsScanSchema.parse({
      models: [], unrecognized: [], roots: ["C:/m"],
      truncated: true, unreadable_roots: ["D:/gone"],
    });
    expect(full.truncated).toBe(true);
    expect(full.unreadable_roots).toEqual(["D:/gone"]);
    // An older backend that sends neither must still parse as a complete,
    // fully readable scan rather than fail the whole page.
    const bare = TtsScanSchema.parse({ models: [], unrecognized: [], roots: [] });
    expect(bare.truncated).toBe(false);
    expect(bare.unreadable_roots).toEqual([]);
  });

  it("a walk cut at the directory cap says the list may be incomplete, even from a backend that names no root", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: { models: [BLOCKED_MODEL], unrecognized: [], roots: [], truncated: true },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText("s2-pro")).toBeInTheDocument();
    expect(screen.getByText(CUT)).toBeInTheDocument();
  });

  it("the schema carries truncated_roots and defaults it when absent", () => {
    const full = TtsScanSchema.parse({
      models: [], unrecognized: [], roots: [], truncated: true, truncated_roots: ["D:/hf"],
    });
    expect(full.truncated_roots).toEqual(["D:/hf"]);
    const bare = TtsScanSchema.parse({ models: [], unrecognized: [], roots: [], truncated: true });
    expect(bare.truncated_roots).toEqual([]);
  });

  it("the cut roots are named, so the sentence says where the list may be short", async () => {
    // The first sentence told the user to point the scan at a smaller folder,
    // a control this page does not have. Naming the folder is what it CAN say.
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          truncated: true, truncated_roots: ["D:\\hf-cache", "E:\\more-models"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText(CUT)).toBeInTheDocument();
    expect(screen.getByText("D:\\hf-cache")).toBeInTheDocument();
    expect(screen.getByText("E:\\more-models")).toBeInTheDocument();
    expect(screen.getByText(/those folders/)).toBeInTheDocument();
  });

  it("one cut root reads in the singular", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          truncated: true, truncated_roots: ["D:\\hf-cache"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText(CUT)).toBeInTheDocument();
    expect(screen.getByText("D:\\hf-cache")).toBeInTheDocument();
    expect(screen.getByText(/that folder/)).toBeInTheDocument();
    expect(screen.queryByText(/those folders/)).not.toBeInTheDocument();
  });

  it("a complete walk does not say so - the ground for the line above", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: { models: [BLOCKED_MODEL], unrecognized: [], roots: [], truncated: false },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText("s2-pro")).toBeInTheDocument();
    expect(screen.queryByText(CUT)).not.toBeInTheDocument();
  });

  it("a root the scan could not open is named, path and all", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          unreadable_roots: ["D:\\voice-models", "E:\\hf-cache\\typo"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText(COULD_NOT_LOOK)).toBeInTheDocument();
    expect(screen.getByText("D:\\voice-models")).toBeInTheDocument();
    expect(screen.getByText("E:\\hf-cache\\typo")).toBeInTheDocument();
    // Naming a bad root is not a scan failure: the models it did find stay.
    expect(screen.getByText("s2-pro")).toBeInTheDocument();
    expect(
      screen.queryByText("Could not scan for voice models. Rescan to retry."),
    ).not.toBeInTheDocument();
  });

  it("readable roots are not named - the ground for the line above", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: { models: [BLOCKED_MODEL], unrecognized: [], roots: [], unreadable_roots: [] },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText("s2-pro")).toBeInTheDocument();
    expect(screen.queryByText(COULD_NOT_LOOK)).not.toBeInTheDocument();
  });

  it("an empty scan with an unreadable root says both: nothing found, and where it could not look", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: { models: [], unrecognized: [], roots: [], unreadable_roots: ["D:\\voice-models"] },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    expect(await screen.findByText(COULD_NOT_LOOK)).toBeInTheDocument();
    expect(screen.getByText(/No voice models found/)).toBeInTheDocument();
  });

  it("a typo root and a wide cache in one payload are two sentences, each naming only its own folder", async () => {
    // The two warnings are independent fields and independent sentences.
    // Rendering them from one payload is the shape a real machine has: an
    // extra root mistyped AND a cache too wide for the cap, at the same time.
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          truncated: true, truncated_roots: ["D:\\hf-cache"],
          unreadable_roots: ["D:\\voice-modles"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    const cut = await screen.findByText(CUT);
    const couldNotLook = screen.getByText(COULD_NOT_LOOK);
    expect(cut).not.toBe(couldNotLook);
    expect(cut).toHaveTextContent("D:\\hf-cache");
    expect(cut).not.toHaveTextContent("D:\\voice-modles");
    expect(couldNotLook).toHaveTextContent("D:\\voice-modles");
    expect(couldNotLook).not.toHaveTextContent("D:\\hf-cache");
    // Neither warning is a scan failure: the model that was found stays.
    expect(screen.getByText("s2-pro")).toBeInTheDocument();
  });

  it("one unreadable root reads in the singular", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          unreadable_roots: ["D:\\voice-models"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    const sentence = await screen.findByText(COULD_NOT_LOOK);
    expect(sentence).toHaveTextContent("Could not look in this folder:");
    expect(sentence).toHaveTextContent("D:\\voice-models");
    expect(sentence).not.toHaveTextContent("these folders");
  });

  it("two unreadable roots read in the plural - the ground for the line above", async () => {
    mockFetch({
      ...baseRoutes(),
      "/tts/models": {
        body: {
          models: [BLOCKED_MODEL], unrecognized: [], roots: [],
          unreadable_roots: ["D:\\voice-models", "E:\\hf-cache\\typo"],
        },
      },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    const sentence = await screen.findByText(COULD_NOT_LOOK);
    expect(sentence).toHaveTextContent("Could not look in these folders:");
    expect(sentence).not.toHaveTextContent("this folder");
  });
});

describe("the voice page's two dropdowns are the same Select (T-18, T-75)", () => {
  /**
   * The voice-reference picker, which no fixture rendered.
   *
   * The eighth converted select is the `voice_ref` branch of
   * `VoiceParamControl`, and the `s2-pro` schema carried a float and an enum
   * and nothing else - so that branch was never in any test's tree, and
   * reverting it to a native `<select>` left all 2506 tests green. T-18 and
   * T-75 both claim eight call sites; they were walking seven.
   */
  const VOICE_REF_PARAM = {
    name: "speaker", type: "voice_ref", default: "", label: "Reference voice",
    help: "", minimum: null, maximum: null, step: null, choices: null,
    group: "voice", advanced: false,
  };

  /** Two saved voices, so the picker has something to walk between. */
  const VOICES = {
    voices: [
      { voice_id: "v-one", label: "Narrator", audio_name: "one.wav",
        transcript: "hello", transcript_source: "user", seconds: 6,
        needs_conversion: false, has_transcript: true },
      { voice_id: "v-two", label: "Second reader", audio_name: "two.wav",
        transcript: "", transcript_source: "none", seconds: 5,
        needs_conversion: false, has_transcript: false },
    ],
  };

  /** How many dropdowns this page must render: the enum and the voice_ref. */
  const VOICE_DROPDOWNS = 2;

  /** Opens the settings of a model whose schema carries both dropdowns. */
  async function openVoiceSettings() {
    // Same keys as baseRoutes, so these replace rather than shadow, and the
    // schema route keeps its position ahead of the broader `/tts/models`.
    mockFetch({
      ...baseRoutes(),
      "/tts/models/u1/schema": {
        body: { ...SCHEMA, params: [...SCHEMA.params, VOICE_REF_PARAM] },
      },
      "/tts/voices": { body: VOICES },
    });
    renderWithQueryClient(<VoiceSettingsPage />);
    await userEvent.click(await screen.findByLabelText("s2-pro settings"));
    const triggers = await screen.findAllByRole("combobox");
    // The count is PINNED, not floored. `> 0` over a query whose length is
    // whatever the fixture happens to render is the loop-only fake: a call
    // site can drop out of the page and every assertion below stays green,
    // which is exactly what was happening.
    await waitFor(() => {
      expect(
        screen.getAllByRole("combobox").length,
        "the voice page no longer renders both dropdowns",
      ).toBe(VOICE_DROPDOWNS);
    });
    return triggers;
  }

  it("T-18: each one keeps the whole keyboard contract", async () => {
    const user = userEvent.setup();
    const triggers = await openVoiceSettings();
    expect(triggers).toHaveLength(VOICE_DROPDOWNS);
    let typeaheadAsked = 0;
    for (const trigger of triggers) {
      const asked = await expectKeyboardContract(
        user, trigger, trigger.getAttribute("id") ?? "a voice dropdown");
      if (asked) typeaheadAsked += 1;
    }
    // As in the other two loops: the clause skips a list with no letter that
    // picks exactly one other option, so it is shown reaching at least one.
    expect(typeaheadAsked, "neither voice dropdown was asked for type-ahead")
      .toBeGreaterThan(0);
  }, 60_000);

  it("T-76: each one is named by the visible label beside it", async () => {
    // The voice_ref trigger pointed its `aria-labelledby` at an id no element
    // carried: the name survived only because the accessible-name algorithm
    // falls through a dangling reference to the `<label for>` pairing, which
    // is a validity error standing in for a binding. Both halves are asked
    // for - the attribute is there, and the id it names is on the page with
    // the words in it.
    const triggers = await openVoiceSettings();
    for (const trigger of triggers) {
      const idref = trigger.getAttribute("aria-labelledby");
      expect(idref, `${trigger.id} is named by nothing it points at`)
        .not.toBeNull();
      const named = document.getElementById(String(idref));
      expect(named, `${trigger.id} points at an id nothing carries`)
        .not.toBeNull();
      expect((named!.textContent ?? "").trim(), `${trigger.id}'s label is empty`)
        .not.toBe("");
      expect(trigger, `${trigger.id} carries an aria-label as well`)
        .not.toHaveAttribute("aria-label");
    }
  }, 30_000);

  it("T-75: both carry the shared collision padding", async () => {
    const user = userEvent.setup();
    const triggers = await openVoiceSettings();
    expect(triggers).toHaveLength(VOICE_DROPDOWNS);
    for (const trigger of triggers) {
      const listbox = await openSelect(user, trigger);
      expect(openPositioner()!.getAttribute("data-collision-padding"))
        .toBe(String(POPUP_COLLISION_PADDING));
      // And against what Base UI placed the popup with, which the attribute
      // alone cannot vouch for: the prop can be lost on the way while the
      // attribute stays, and a padding can fall back to the library's own.
      await expectPlacedCollisionPadding(listbox, trigger.id);
      expectOptionsOnTheOptionStep(listbox, trigger.id);
      await user.keyboard("{Escape}");
      await expectClosed(listbox);
    }
  }, 60_000);
});
