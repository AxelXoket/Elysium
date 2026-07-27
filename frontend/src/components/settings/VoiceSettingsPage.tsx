/**
 * VoiceSettingsPage.tsx - Settings › Voice.
 *
 * Everything voice, in the order a person meets it:
 *
 *   1. The global toggle (also what makes the model start writing delivery
 *      tags - the context gauge charges for that the moment it flips).
 *   2. Engine setup - the app installs the engine's own environment. One
 *      button, a live log line, cancellable, honest about failure. The user
 *      never sees a terminal; that is an acceptance criterion, not a wish.
 *   3. Models - every detected model, ALWAYS inspectable, each carrying its
 *      readiness verdict: pick one, tune it, and see plainly when (and why)
 *      it cannot run yet. Verdicts render through getErrorMessage, so the
 *      words match every other error surface in the app.
 *   4. Reference voices - the clips a model clones from, with their
 *      transcripts (editable ALWAYS: an auto-transcript is a first draft).
 *
 * The per-model controls are generated from the backend's ParamSpec
 * descriptors - this page knows no engine by name. A new engine with new
 * knobs renders correctly the day its adapter ships.
 */

import { DeliverySection } from "./DeliverySection";
import {
  clearDraft,
  readDelivered,
  readDraft,
  writeDelivered,
  writeDraft,
} from "./voiceParamDrafts";
import { ReadingRulesSection } from "./ReadingRulesSection";
import { useMemo, useState } from "react";
import {
  AudioLines,
  Check,
  ChevronDown,
  Download,
  Loader2,
  Mic,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import { getErrorMessage } from "@/lib/errors";
import { useErrorStore } from "@/lib/errors/errorStore";
import type {
  TtsMatrixRow,
  TtsModel,
  TtsParam,
  TtsParamValue,
  TtsVoice,
} from "@/lib/schemas/tts";
import {
  useCancelInstall,
  useDeleteVoice,
  useRescanTtsModels,
  useResetTtsSettings,
  useSaveTtsSettings,
  useSelectTtsModel,
  useSetVoiceMode,
  useSetVoiceTranscript,
  useStartInstall,
  useTranscribeVoice,
  useTtsActive,
  useTtsInstallPlan,
  useTtsInstallStatus,
  useTtsModels,
  useTtsRuntimes,
  useTtsSchema,
  useTtsSettings,
  useTtsVoices,
  useUninstallRuntime,
  useUploadVoice,
  useVoiceMode,
} from "@/lib/query/tts";

export function VoiceSettingsPage() {
  return (
    <div className="space-y-4">
      <VoiceModeToggle />
      {/* What the toggle above actually DOES, stated once.
          Its old copy said "Off - chat stays text-only", which describes the
          wrong thing: speaking works either way (the per-message Speak button
          and the composer's continuous-voice button are both independent of
          it). What it really controls is whether the model is ASKED to mark
          how each line is performed - so somebody who only ever pressed Speak
          read that line, decided it was not for them, and never once heard the
          performed voice they had installed an engine for. */}
      <p className="settings-hint">
        When on, a short instruction is added to every request asking the model
        to mark <em>how</em> lines are performed - [low voice], [breathless].
        The marks never appear in the chat, and only engines that can read them
        receive it. Speaking a message works with this off too; it just has no
        direction.
      </p>
      <EngineSetupSection />
      <ModelsSection />
      <ReferenceVoicesSection />
      <DeliverySection />
      <ReadingRulesSection />
      <p className="generation-helper">
        Voice runs entirely on this computer. Nothing you write or hear leaves
        it.
      </p>
    </div>
  );
}

// ── 1. global toggle ────────────────────────────────────────────────────────

function VoiceModeToggle() {
  const { data } = useVoiceMode();
  const setMode = useSetVoiceMode();
  const pushModeError = useErrorStore((s) => s.pushError);
  const enabled = data?.enabled ?? false;
  return (
    <button
      type="button"
      className="settings-toggle-row"
      role="switch"
      aria-checked={enabled}
      // The SAME words the row shows. They had drifted - the screen said
      // "Performed replies" while a screen reader was told "Voice replies",
      // so the two users of this control were reading different labels.
      aria-label="Performed replies"
      disabled={setMode.isPending || data == null}
      onClick={() =>
        setMode.mutate(!enabled, { onError: (err) => pushModeError(err) })
      }
    >
      <span className="min-w-0 flex-1 text-left">
        <span className="settings-label">Performed replies</span>
        <span className="settings-category-desc">
          {enabled && data?.active
            ? "On - every reply is written with delivery directions"
            : enabled
              ? "On - select a voice model below to hear them performed"
              : "Off - replies are written plainly"}
        </span>
      </span>
      <span className="settings-switch" data-on={enabled ? "true" : "false"}>
        <span className="settings-switch-thumb" />
      </span>
    </button>
  );
}

// ── 2. engine setup ─────────────────────────────────────────────────────────

/** The job's internal state, in words that tell someone what is happening to
 * their machine right now. "verifying" especially: that step exists so we
 * never call a half-built environment ready, and it is worth showing. */
const INSTALL_PHASE: Record<string, string> = {
  preparing: "Getting ready…",
  installing: "Downloading and installing…",
  verifying: "Checking that it actually works…",
};

function EngineSetupSection() {
  const runtimes = useTtsRuntimes();
  if (runtimes.isPending) return <SectionSkeleton label="Voice engines" />;
  if (runtimes.isError || !runtimes.data) {
    // An error must read as an error - a silently missing section looks like
    // the feature does not exist (audit-2).
    return (
      <section aria-label="Voice engines" className="settings-voice-section space-y-2">
        <h3 className="settings-section-title">Voice engines</h3>
        <p className="settings-voice-warning">
          Could not reach the voice engine list. Reopen settings to retry.
        </p>
      </section>
    );
  }
  return (
    <section aria-label="Voice engines" className="settings-voice-section space-y-2">
      <h3 className="settings-section-title">Voice engines</h3>
      {runtimes.data.engines.map((engine) => (
        <EngineRow
          key={engine.engine_id}
          engineId={engine.engine_id}
          displayName={engine.display_name}
          state={
            runtimes.data.runtimes.find((r) => r.engine_id === engine.engine_id)
              ?.state ?? "missing"
          }
        />
      ))}
    </section>
  );
}

function EngineRow({
  engineId,
  displayName,
  state,
}: {
  engineId: string;
  displayName: string;
  state: string;
}) {
  const job = useTtsInstallStatus(engineId);
  const start = useStartInstall();
  const cancel = useCancelInstall();
  const uninstall = useUninstallRuntime();
  const pushError = useErrorStore((s) => s.pushError);

  const running = job.data?.running ?? false;
  const lastLog = job.data?.log.at(-1);
  const failed = job.data?.state === "failed";
  // Only asked for when it is actually decision-relevant: the real download
  // size belongs in front of someone who has not committed yet, not next to
  // an engine they already installed.
  const plan = useTtsInstallPlan(state === "ready" || running ? null : engineId);
  const sizeNote = plan.data
    ? `about ${(plan.data.download_mb / 1024).toFixed(1)} GB to download, once`
    : "a one-time download of a few GB";

  return (
    <div className="settings-voice-row" data-testid={`engine-${engineId}`}>
      <span className="min-w-0 flex-1 text-left">
        <span className="settings-label">{displayName}</span>
        <span className="settings-category-desc">
          {running
            ? INSTALL_PHASE[job.data?.state ?? ""] ?? "Setting up…"
            : failed && job.data?.error_code
              ? getErrorMessage(job.data.error_code)
              : state === "ready"
                ? "Installed and ready"
                : state === "broken"
                  ? getErrorMessage("tts_runtime_broken")
                  : `Not set up yet · ${sizeNote}`}
        </span>
        {running && (
          <>
            <span
              className="settings-voice-progress"
              role="progressbar"
              aria-label={`Setting up ${displayName}`}
              // No value attributes on purpose: an indeterminate bar that
              // claimed a percentage would be inventing one.
            />
            {lastLog && <span className="settings-voice-log">{lastLog}</span>}
          </>
        )}
      </span>
      {running ? (
        <button
          type="button"
          className="settings-voice-button"
          onClick={() =>
            cancel.mutate(engineId, { onError: (err) => pushError(err) })
          }
        >
          <X size={12} /> Cancel
        </button>
      ) : state === "ready" ? (
        <button
          type="button"
          className="settings-voice-button is-quiet"
          aria-label={`Remove ${displayName}`}
          onClick={() =>
            uninstall.mutate(engineId, { onError: (err) => pushError(err) })
          }
          disabled={uninstall.isPending}
        >
          {uninstall.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Trash2 size={12} />
          )}
          Remove
        </button>
      ) : (
        <button
          type="button"
          className="settings-voice-button"
          onClick={() =>
            start.mutate(engineId, { onError: (err) => pushError(err) })
          }
          disabled={start.isPending}
        >
          {start.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Download size={12} />
          )}
          {state === "broken" || failed ? "Set up again" : "Set up"}
        </button>
      )}
    </div>
  );
}

// ── 3. models ───────────────────────────────────────────────────────────────

function ModelsSection() {
  const models = useTtsModels();
  const active = useTtsActive();
  const rescan = useRescanTtsModels();
  const pushScanError = useErrorStore((s) => s.pushError);
  const [openUid, setOpenUid] = useState<string | null>(null);

  if (models.isPending) return <SectionSkeleton label="Voice models" />;

  const list = models.data?.models ?? [];
  const unrecognized = models.data?.unrecognized ?? [];
  const scanFailed = models.isError;
  return (
    <section aria-label="Voice models" className="settings-voice-section space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="settings-section-title">Voice models</h3>
        <button
          type="button"
          className="settings-voice-button is-quiet"
          aria-label="Rescan the models folder"
          onClick={() =>
            rescan.mutate(undefined, { onError: (err) => pushScanError(err) })
          }
          disabled={rescan.isPending}
        >
          {rescan.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <RefreshCw size={12} />
          )}
          Rescan
        </button>
      </div>
      {scanFailed && (
        <p className="settings-voice-warning">
          Could not scan for voice models. Rescan to retry.
        </p>
      )}
      {!scanFailed && list.length === 0 && unrecognized.length === 0 && (
        <p className="settings-category-desc">
          No voice models found. Drop a model folder into the voice models
          directory and rescan.
        </p>
      )}
      {/* The backend has produced this list all along and the schema has
          parsed it all along; no component ever read it. So a user who HAD
          dropped a folder in, and got the shape slightly wrong, was told
          "No voice models found. Drop a model folder in" - advice for the
          one thing they had already done, while the reason sat in the
          response body. */}
      {!scanFailed && unrecognized.length > 0 && (
        <div className="settings-voice-warning">
          <p>
            {unrecognized.length === 1
              ? "One folder was not recognised as a voice model:"
              : `${unrecognized.length} folders were not recognised as voice models:`}
          </p>
          <ul>
            {unrecognized.map((entry) => (
              <li key={entry.path}>
                <code>{entry.path}</code> - {entry.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {list.map((model) => (
        <ModelRow
          key={model.uid}
          model={model}
          selected={active.data?.uid === model.uid}
          open={openUid === model.uid}
          onToggleOpen={() =>
            setOpenUid((cur) => (cur === model.uid ? null : model.uid))
          }
        />
      ))}
    </section>
  );
}

function ModelRow({
  model,
  selected,
  open,
  onToggleOpen,
}: {
  model: TtsModel;
  selected: boolean;
  open: boolean;
  onToggleOpen: () => void;
}) {
  const select = useSelectTtsModel();
  const pushError = useErrorStore((s) => s.pushError);
  const blockers = model.readiness.issues.filter(
    (i) => i.severity === "blocker",
  );
  const warnings = model.readiness.issues.filter(
    (i) => i.severity === "warning",
  );

  return (
    <div
      className="settings-voice-model"
      data-selected={selected ? "true" : "false"}
      data-testid={`voice-model-${model.uid}`}
    >
      {/* The NAME selects. Selecting is what arms the whole feature - the
          per-message Speak button, the live-speak button and the composer
          toggle all render only once a model is chosen - and it used to be an
          unlabelled 11px circle, while clicking the obvious target (the name)
          opened the parameter panel instead. A person could install an engine,
          download a model and record a reference voice and still see nothing
          in the chat, with no way to guess why. Settings moved to its own
          disclosure control. */}
      <div className="settings-voice-row">
        <button
          type="button"
          className="settings-voice-pick"
          role="radio"
          aria-checked={selected}
          aria-label={`Use ${model.name}`}
          onClick={() =>
            select.mutate(model.uid, { onError: (err) => pushError(err) })
          }
        >
          <span className="settings-voice-radio" aria-hidden="true">
            {selected && <Check size={11} />}
          </span>
          <span className="min-w-0 flex-1 text-left">
            <span className="settings-label">
              {model.name}
              {model.variant ? (
                <span className="settings-voice-variant"> · {model.variant}</span>
              ) : null}
            </span>
            <span className="settings-category-desc">
              {selected
                ? "Selected - this voice speaks your replies"
                : model.readiness.runnable
                  ? "Ready to use - select it to speak replies"
                  : "Cannot run yet - open settings for details"}
            </span>
          </span>
        </button>
        {model.readiness.runnable ? (
          <AudioLines size={14} className="settings-voice-ok" aria-hidden />
        ) : null}
        <button
          type="button"
          className="settings-voice-disclosure"
          aria-expanded={open}
          aria-label={`${model.name} settings`}
          onClick={onToggleOpen}
        >
          <ChevronDown
            size={14}
            style={{ transform: open ? "rotate(180deg)" : undefined }}
          />
        </button>
      </div>

      {/* The verdict: EVERY reason at once, in the same words as every other
          error surface. Fix-one-discover-the-next is the shape this avoids. */}
      {!model.readiness.runnable && blockers.length > 0 && (
        <ul className="settings-voice-issues" aria-label="Why it will not run">
          {blockers.map((issue) => (
            <li key={issue.code}>{getErrorMessage(issue.code)}</li>
          ))}
        </ul>
      )}
      {warnings.map((issue) => (
        <p key={issue.code} className="settings-voice-warning">
          {getErrorMessage(issue.code)}
        </p>
      ))}

      {/* Settings stay available whatever the verdict says - that is the
          product rule the readiness system exists to keep. */}
      {open && model.readiness.settings_available && (
        <ModelParams uid={model.uid} />
      )}
    </div>
  );
}

// ── 3b. schema-driven parameter controls ────────────────────────────────────

function ModelParams({ uid }: { uid: string }) {
  const schema = useTtsSchema(uid);
  const settings = useTtsSettings(uid);
  const save = useSaveTtsSettings(uid);
  const reset = useResetTtsSettings(uid);
  const pushError = useErrorStore((s) => s.pushError);

  // Draft-then-save: sliders fire per step, and a mutation per tick would
  // hammer the encrypted settings table. Dirty state is explicit instead.
  // Seeded from - and written through to - module scope, so the draft
  // survives this component being unmounted by a collapse, by another
  // model's row opening, or by Settings closing (KÖK 15).
  const [draft, setDraftState] = useState<Record<string, TtsParamValue>>(
    () => readDraft(uid),
  );
  const setDraft = (
    next:
      | Record<string, TtsParamValue>
      | ((cur: Record<string, TtsParamValue>) => Record<string, TtsParamValue>),
  ) =>
    setDraftState((cur) => {
      const value = typeof next === "function" ? next(cur) : next;
      writeDraft(uid, value);
      return value;
    });
  // What the last save actually delivered. A draft key is the user's UNSAVED
  // intent, so it is retired exactly when the last delivered save carried that
  // same value - not merely because new server data arrived.
  const [lastSent, setLastSentState] = useState<
    Record<string, TtsParamValue>
  >(() => readDelivered(uid));
  const setLastSent = (sent: Record<string, TtsParamValue>) => {
    writeDelivered(uid, sent);
    setLastSentState(sent);
  };
  // Fresh server values retire the draft - adjusted during render (the
  // sanctioned pattern), so a save's response never fights a stale effect.
  //
  // This used to be a blanket `setDraft({})`. useSaveTtsSettings writes the
  // response through with setQueryData, so settings.data is a NEW reference
  // after every save and the reset always fired - wiping any parameter the
  // user edited again while the request was in flight (drag Expressiveness,
  // press Save, change Language while the POST flies: the select snapped back
  // and the second edit was gone with no error and no toast). Retiring only
  // what the save delivered also keeps a server-side CLAMP visible: the value
  // we sent is retired, so `effective` falls back to the clamped server value.
  const [seenSettings, setSeenSettings] = useState(settings.data);
  if (settings.data !== seenSettings) {
    setSeenSettings(settings.data);
    setDraft((cur) =>
      Object.fromEntries(
        Object.entries(cur).filter(([k, v]) => lastSent[k] !== v),
      ),
    );
  }

  const effective = useMemo(
    () => ({ ...(settings.data?.values ?? {}), ...draft }),
    [settings.data, draft],
  );
  const dirty = Object.keys(draft).length > 0;

  if (schema.isPending || settings.isPending) {
    return <p className="settings-category-desc">Loading settings…</p>;
  }
  if (schema.isError || settings.isError || !schema.data) {
    return (
      <p className="settings-voice-warning">
        {getErrorMessage("tts_model_incomplete")}
      </p>
    );
  }

  // The union when the backend offers it, this engine's own knobs otherwise.
  // Showing the union is the point: a control that appears and disappears when
  // the model is swapped teaches that the app is inconsistent, when in fact the
  // difference belongs to the ENGINE - which is what the disabled rows say.
  const rows: TtsMatrixRow[] =
    schema.data.matrix.length > 0
      ? schema.data.matrix
      : schema.data.params.map((p) => ({
          ...p,
          editable: true,
          status: "supported" as const,
          reason: "",
        }));
  const params = rows.filter((p) => !p.advanced);
  const advanced = rows.filter((p) => p.advanced);

  return (
    <div className="settings-voice-params" data-testid={`voice-params-${uid}`}>
      {[...params, ...advanced].map((param) => (
        <ParamControl
          key={param.name}
          param={param}
          disabled={!param.editable}
          reason={param.reason}
          value={effective[param.name] ?? (param.default as TtsParamValue)}
          onChange={(value) =>
            setDraft((cur) => ({ ...cur, [param.name]: value }))
          }
        />
      ))}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="settings-voice-button"
          disabled={!dirty || save.isPending}
          onClick={() => {
            const sent = draft;
            // Recorded BEFORE the response can land: the render-phase
            // reconciliation above retires against exactly this.
            setLastSent(sent);
            save.mutate(sent, { onError: (err) => pushError(err) });
          }}
        >
          {save.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : null}
          Save changes
        </button>
        <button
          type="button"
          className="settings-voice-button is-quiet"
          disabled={reset.isPending}
          onClick={() =>
            reset.mutate(undefined, {
              // An explicit reset discards unsaved intent too - otherwise the
              // draft would survive the very action meant to clear it.
              onSuccess: () => {
                clearDraft(uid);
                setDraft({});
                setLastSent({});
              },
              onError: (err) => pushError(err),
            })
          }
        >
          Reset to defaults
        </button>
      </div>
    </div>
  );
}

function ParamControl({
  param,
  value,
  onChange,
  disabled = false,
  reason = "",
}: {
  param: TtsParam;
  value: TtsParamValue;
  onChange: (value: TtsParamValue) => void;
  /** This engine cannot act on the setting - shown, but not operable. */
  disabled?: boolean;
  /** WHY it cannot. The reason is the entire value of showing the row: a
   *  greyed control with no explanation reads as a bug in the app. */
  reason?: string;
}) {
  if (disabled) {
    // Rendered as text rather than a dead input. A disabled slider still
    // invites dragging, and failing silently under the finger is worse than
    // plainly not being a control.
    return (
      <div className="settings-param-disabled" title={reason}>
        <span className="settings-label">{param.label}</span>
        <span className="settings-hint opacity-70">{reason}</span>
      </div>
    );
  }
  if (param.type === "bool") {
    const checked = value === true || value === "true";
    return (
      <button
        type="button"
        className="settings-toggle-row"
        role="switch"
        aria-checked={checked}
        aria-label={param.label}
        onClick={() => onChange(!checked)}
      >
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label">{param.label}</span>
          {param.help && (
            <span className="settings-category-desc">{param.help}</span>
          )}
        </span>
        <span className="settings-switch" data-on={checked ? "true" : "false"}>
          <span className="settings-switch-thumb" />
        </span>
      </button>
    );
  }

  if (param.type === "enum" && param.choices) {
    return (
      <div className="generation-control">
        <label className="settings-label" htmlFor={`voice-${param.name}`}>
          {param.label}
        </label>
        <select
          id={`voice-${param.name}`}
          className="settings-voice-select"
          value={String(value)}
          onChange={(event) => onChange(event.currentTarget.value)}
        >
          {/* A saved value the current choices no longer contain must stay
              VISIBLE - a blank select silently hides what is actually
              stored (audit-2). */}
          {!param.choices.includes(String(value)) && (
            <option value={String(value)}>{String(value)} (saved)</option>
          )}
          {param.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
        {param.help && <p className="generation-helper">{param.help}</p>}
      </div>
    );
  }

  if (param.type === "float" || param.type === "int") {
    const num = typeof value === "number" ? value : Number(value) || 0;
    const min = param.minimum ?? 0;
    const max = param.maximum ?? (param.type === "int" ? 100 : 1);
    const step = param.step ?? (param.type === "int" ? 1 : 0.05);
    return (
      <div className="generation-control">
        <div className="flex items-center justify-between gap-3">
          <label className="settings-label">{param.label}</label>
          <span className="settings-value">
            {param.type === "int" ? Math.round(num) : num.toFixed(2)}
          </span>
        </div>
        <input
          type="range"
          aria-label={`${param.label} slider`}
          min={min}
          max={max}
          step={step}
          value={num}
          className="generation-range"
          onChange={(event) => {
            const raw = Number(event.currentTarget.value);
            onChange(param.type === "int" ? Math.round(raw) : raw);
          }}
        />
        {param.help && <p className="generation-helper">{param.help}</p>}
      </div>
    );
  }

  // text / voice_ref - voice_ref stores a voice id from the section below.
  return (
    <div className="generation-control">
      <label className="settings-label" htmlFor={`voice-${param.name}`}>
        {param.label}
      </label>
      <VoiceRefOrTextInput param={param} value={String(value ?? "")} onChange={onChange} />
      {param.help && <p className="generation-helper">{param.help}</p>}
    </div>
  );
}

function VoiceRefOrTextInput({
  param,
  value,
  onChange,
}: {
  param: TtsParam;
  value: string;
  onChange: (value: TtsParamValue) => void;
}) {
  const voices = useTtsVoicesForPicker(param.type === "voice_ref");
  if (param.type === "voice_ref") {
    return (
      <select
        id={`voice-${param.name}`}
        className="settings-voice-select"
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        aria-label={param.label}
      >
        <option value="">{"No cloning - the model's own voice"}</option>
        {voices.map((v) => (
          <option key={v.voice_id} value={v.voice_id}>
            {v.label}
            {v.has_transcript ? "" : " (no transcript yet)"}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      id={`voice-${param.name}`}
      type="text"
      className="settings-voice-input"
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  );
}

function useTtsVoicesForPicker(enabled: boolean): TtsVoice[] {
  // The hook always runs (rules-of-hooks); `enabled` only gates the fetch.
  const { data } = useTtsVoices(enabled);
  return data?.voices ?? [];
}

// ── 4. reference voices ─────────────────────────────────────────────────────

function ReferenceVoicesSection() {
  const voices = useTtsVoices();
  // Whether the selected engine can HEAR a clip and draft its words. No
  // shipped engine can - all three workers refuse OP_TRANSCRIBE - and the
  // button was drawn unconditionally, always enabled, its failure
  // translated as an engine that could not start while the engine was
  // running fine. Read from the declared capability, so an engine that
  // gains the ability gets its button back without touching this file.
  const active = useTtsActive();
  const activeSchema = useTtsSchema(active.data?.uid ?? null);
  const canTranscribe =
    activeSchema.data?.capabilities.transcribes_reference === true;
  const upload = useUploadVoice();
  const pushError = useErrorStore((s) => s.pushError);
  const [pendingName, setPendingName] = useState("");
  // A collision is only ever confirmed explicitly: POST /tts/voices/{id} is an
  // upsert that unlinks the previous clip, and the id is DERIVED from a typed
  // name or the filename - so "Anna" twice silently destroyed the first
  // recording (and its hand-corrected transcript) with no warning at all.
  const [collision, setCollision] = useState<
    { voiceId: string; file: File; label: string } | null
  >(null);

  const send = (vars: { voiceId: string; file: File; label: string }) => {
    setCollision(null);
    upload.mutate(vars, {
      onSuccess: () => setPendingName(""),
      onError: (err) => pushError(err),
    });
  };

  const startUpload = (file: File) => {
    const fallback = file.name.replace(/\.[^.]+$/, "");
    const voiceId = (pendingName || fallback)
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      // Anchored like the backend VOICE_ID regex: the first character must
      // be alphanumeric, or our own sanitizer produces an id the backend is
      // guaranteed to reject (audit-2).
      .replace(/^[^a-z0-9]+/, "")
      .replace(/-+$/g, "")
      .slice(0, 60);
    if (!voiceId) {
      pushError({ status: 400, detail: "tts_reference_invalid", message: "" });
      return;
    }
    const label = pendingName || fallback;
    const existing = (voices.data?.voices ?? []).some(
      (v) => v.voice_id === voiceId,
    );
    if (existing) {
      setCollision({ voiceId, file, label });
      return;
    }
    send({ voiceId, file, label });
  };

  return (
    <section aria-label="Reference voices" className="settings-voice-section space-y-2">
      <h3 className="settings-section-title">Reference voices</h3>
      <p className="settings-category-desc">
        A short, clear clip (~10s) the model clones. Some engines also need
        the words spoken in it - type them in below.
      </p>
      {(voices.data?.voices ?? []).map((voice) => (
        <VoiceRow
          key={voice.voice_id}
          voice={voice}
          canTranscribe={canTranscribe}
        />
      ))}
      {collision != null && (
        <div className="settings-voice-warning" role="alert">
          <p>
            A voice called “{collision.voiceId}” already exists. Replacing it
            deletes its clip and its transcript.
          </p>
          <div className="settings-voice-row">
            <button
              type="button"
              className="settings-voice-button"
              onClick={() => send(collision)}
            >
              Replace it
            </button>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              onClick={() => setCollision(null)}
            >
              Keep the old one
            </button>
          </div>
        </div>
      )}
      <div className="settings-voice-row">
        <input
          type="text"
          className="settings-voice-input min-w-0 flex-1"
          placeholder="Voice name (optional)"
          aria-label="New voice name"
          value={pendingName}
          onChange={(event) => setPendingName(event.currentTarget.value)}
        />
        <label className="settings-voice-button">
          {upload.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Mic size={12} />
          )}
          Add clip
          <input
            type="file"
            accept="audio/*,.wav,.mp3,.flac,.ogg,.m4a,.opus"
            className="sr-only"
            aria-label="Upload a reference clip"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file) startUpload(file);
            }}
          />
        </label>
      </div>
    </section>
  );
}

function VoiceRow({
  voice,
  canTranscribe,
}: {
  voice: TtsVoice;
  canTranscribe: boolean;
}) {
  const setTranscript = useSetVoiceTranscript();
  const transcribe = useTranscribeVoice();
  const remove = useDeleteVoice();
  const pushError = useErrorStore((s) => s.pushError);
  const [text, setText] = useState(voice.transcript);
  // A fresh transcript from the server (save, or auto-transcribe) replaces
  // the local buffer - adjusted during render, not in an effect.
  const [seenTranscript, setSeenTranscript] = useState(voice.transcript);
  if (voice.transcript !== seenTranscript) {
    setSeenTranscript(voice.transcript);
    setText(voice.transcript);
  }
  const dirty = text.trim() !== voice.transcript.trim();

  return (
    <div className="settings-voice-model" data-testid={`voice-${voice.voice_id}`}>
      <div className="settings-voice-row">
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label">{voice.label}</span>
          <span className="settings-category-desc">
            {voice.seconds != null ? `${voice.seconds.toFixed(1)}s · ` : ""}
            {voice.has_transcript
              ? voice.transcript_source === "auto"
                ? "Transcript drafted by the engine - check it"
                : "Transcript added"
              : "No transcript yet"}
          </span>
        </span>
        <button
          type="button"
          className="settings-voice-button is-quiet"
          aria-label={`Delete ${voice.label}`}
          onClick={() =>
            remove.mutate(voice.voice_id, { onError: (err) => pushError(err) })
          }
          disabled={remove.isPending}
        >
          <Trash2 size={12} />
        </button>
      </div>
      <textarea
        className="settings-voice-textarea"
        aria-label={`Words spoken in ${voice.label}`}
        placeholder="The words spoken in the clip…"
        rows={2}
        value={text}
        onChange={(event) => setText(event.currentTarget.value)}
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="settings-voice-button"
          disabled={!dirty || setTranscript.isPending}
          onClick={() =>
            setTranscript.mutate(
              { voiceId: voice.voice_id, text },
              { onError: (err) => pushError(err) },
            )
          }
        >
          Save words
        </button>
        {canTranscribe ? (
          <button
            type="button"
            className="settings-voice-button is-quiet"
            disabled={transcribe.isPending}
            title="The loaded voice engine listens to the clip and drafts the words"
            onClick={() =>
              transcribe.mutate(voice.voice_id, {
                onError: (err) => pushError(err),
              })
            }
          >
            {transcribe.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : null}
            Listen &amp; fill in
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ── shared ──────────────────────────────────────────────────────────────────

function SectionSkeleton({ label }: { label: string }) {
  return (
    <section aria-label={label} className="settings-voice-section space-y-2">
      <h3 className="settings-section-title">{label}</h3>
      <p className="settings-category-desc">Loading…</p>
    </section>
  );
}
