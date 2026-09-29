/**
 * PromptModulesSection - the "Prompt modules" page in Settings (Ek O 3).
 *
 * Three things live here, because they decide together what opens every
 * request (Ek O 6-8):
 *
 *   1. Prompt modules. Files the user brought in, each with its own switch.
 *      The ones that are on go first in the system message, for every
 *      character, on every message. The counter at the top is always shown
 *      (9A.4 warning 2) and counts what the request will carry.
 *   2. The roleplay layer, the app's own fixed prompt. Its switch is its
 *      own, but while any module is on the layer is NOT sent, whatever the
 *      switch says (Ek O 7). The row says so in its own description line,
 *      so a user never reads "on" and believes it is going.
 *   3. Reply length (A9). A sentence inside the layer, so it only means
 *      anything while the layer is actually going: greyed out otherwise,
 *      with the reason.
 *
 * Nothing on this page adds a module or any text by itself. Every module
 * starts off; the user turns it on.
 */
import { useId } from "react";
import { AlertTriangle } from "lucide-react";

import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { Collapse } from "@/components/motion/Collapse";
import { useErrorStore } from "@/lib/errors";
import { formatCount } from "@/lib/formatCount";
import { moduleFires } from "@/lib/prompt/assembly";

import { PromptModuleImport } from "./PromptModuleImport";
import { ChatVariablesRow, PromptModuleList } from "./PromptModuleList";
import { moduleCounter } from "./promptModuleCost";
import {
  usePromptModuleSettings,
  usePromptModules,
  useSavePromptModuleSettings,
} from "./promptModuleQueries";
import { useHeldValue } from "./useHeldValue";
import {
  MODULE_COUNTER_CHECKING,
  MODULE_COUNTER_FAILED,
  PROMPT_MODULE_LIMITS,
  WORDS_TO_TOKENS,
  formatModuleCounter,
  lengthFitsMaxTokens,
  promptMode,
  type PromptMode,
} from "./promptModuleText";

/** The one line the page shows when the module list cannot be read. */
export const MODULE_LIST_UNREACHABLE =
  "Could not reach the prompt module list. Reopen settings to retry.";
/** The layer's line when only the module list failed: the list above
 *  already says it could not be reached, so this does not say it twice. */
export const LAYER_WAITS_FOR_LIST =
  "Shown once the module list loads: whether the layer is sent depends on it.";
/** The same, for the layer's settings. */
export const LAYER_SETTINGS_UNREACHABLE =
  "Could not reach the roleplay layer settings. Reopen settings to retry.";

export function PromptModulesSection() {
  const modules = usePromptModules();
  const settings = usePromptModuleSettings();
  const rows = modules.data?.modules ?? [];
  const counter = moduleCounter(rows);
  // Every module that is on, including one that only rides on regenerate:
  // any of them keeps the roleplay layer out of the request (Ek O 7). A
  // blank one does not count, the same test the server applies.
  const enabledAny = rows.filter(
    (m) => m.kind === "module" && m.enabled && moduleFires(m),
  ).length;

  // The counter always has a line (9A.4 warning 2), but only states numbers
  // it has: "0 modules" on a list that failed to load would be false while
  // modules may be on and being sent.
  const counterText = modules.isSuccess
    ? formatModuleCounter(counter.enabled, counter.tokens)
    : modules.isError
      ? MODULE_COUNTER_FAILED
      : MODULE_COUNTER_CHECKING;

  return (
    <div className="settings-voice-page space-y-4" data-prompt-modules-page="">
      {/* No heading here: the dialog's own title already says "Prompt
          modules". Voice's headings name parts of a page, never the page. */}
      <section
        aria-label="Prompt modules"
        className="settings-voice-section space-y-2"
      >
        <p className="settings-label" role="status">
          {counterText}
        </p>
        <p className="settings-hint">
          Modules that are on go first in every chat, for every character, in
          the order below.
        </p>

        <PromptModuleImport />

        {modules.isError ? (
          <p className="settings-voice-warning" role="alert">
            {MODULE_LIST_UNREACHABLE}
          </p>
        ) : modules.isPending ? (
          <p className="settings-category-desc">Loading…</p>
        ) : (
          <>
            <PromptModuleList modules={rows} imports={modules.data?.imports ?? []} />
            <ChatVariablesRow modules={rows} />
          </>
        )}
      </section>

      <section
        aria-label="When no module is on"
        className="settings-voice-section space-y-2"
      >
        <h3 className="settings-section-title">When no module is on</h3>
        {/* Gated on BOTH queries: the layer's state depends on whether any
            module is on, and a switch showing a guess invites a click that
            writes the opposite of what was meant. */}
        {modules.isError || settings.isError ? (
          <p className="settings-voice-warning" role="alert">
            {settings.isError ? LAYER_SETTINGS_UNREACHABLE : LAYER_WAITS_FOR_LIST}
          </p>
        ) : modules.isSuccess && settings.isSuccess ? (
          <LayerControls
            mode={promptMode(enabledAny > 0, settings.data.prompt_layer_enabled)}
            layerSwitch={settings.data.prompt_layer_enabled}
            replyLengthWords={settings.data.reply_length_words ?? null}
            enabledModules={enabledAny}
          />
        ) : (
          <p className="settings-category-desc">Loading…</p>
        )}
      </section>
    </div>
  );
}

function LayerControls({
  mode,
  layerSwitch,
  replyLengthWords,
  enabledModules,
}: {
  mode: PromptMode;
  layerSwitch: boolean;
  replyLengthWords: number | null;
  enabledModules: number;
}) {
  const save = useSavePromptModuleSettings();
  const pushError = useErrorStore((s) => s.pushError);
  const { settings: generation } = useGenerationSettings();
  const lengthLineId = useId();

  // The server replaces both settings as a pair, so every write carries both:
  // the one being changed, and the stored value of the other.
  const put = (next: { prompt_layer_enabled?: boolean; reply_length_words?: number | null }) =>
    save.mutate(
      {
        prompt_layer_enabled: layerSwitch,
        reply_length_words: replyLengthWords,
        ...next,
      },
      { onError: (err) => pushError(err) },
    );

  const lengthLive = mode === "b";
  const words = replyLengthWords;
  const tooLong =
    lengthLive && words != null && !lengthFitsMaxTokens(words, generation.max_tokens);
  // Held so the warning can animate shut when the cap is raised.
  const shownWords = useHeldValue(tooLong ? words : null);

  // The one place that says the layer is not going out: the row's own
  // description, the way Voice's "Performed replies" row carries its state.
  const suppressed = mode === "a" && layerSwitch;
  const layerLine = suppressed
    ? `Not sent now: ${enabledModules === 1 ? "1 module is" : `${formatCount(enabledModules)} modules are`} on. It comes back when the last one is turned off.`
    : "The app's own craft rules, with the character card inside them.";
  const lengthLine = lengthLive
    ? "Adaptive sends no length line; the card decides."
    : mode === "a"
      ? "Part of the roleplay layer."
      : "Part of the roleplay layer, which is off.";

  const options: { value: number | null; label: string }[] = [
    { value: null, label: "Adaptive" },
    ...PROMPT_MODULE_LIMITS.reply_length_words_allowed.map((n) => ({
      value: n,
      label: `${formatCount(n)} words`,
    })),
  ];

  return (
    <>
      <button
        type="button"
        className="settings-toggle-row"
        role="switch"
        aria-checked={layerSwitch}
        aria-labelledby={`${lengthLineId}-layer-label`}
        aria-describedby={`${lengthLineId}-layer-line`}
        disabled={save.isPending}
        onClick={() => put({ prompt_layer_enabled: !layerSwitch })}
      >
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label" id={`${lengthLineId}-layer-label`}>
            Roleplay layer
          </span>
          <span
            className="settings-category-desc"
            id={`${lengthLineId}-layer-line`}
            data-testid="prompt-layer-state"
          >
            {layerLine}
          </span>
        </span>
        <span className="settings-switch" data-on={layerSwitch ? "true" : "false"}>
          <span className="settings-switch-thumb" />
        </span>
      </button>

      {/* Wraps instead of squeezing: at the dialog's narrowest the choices
          drop under the label rather than folding the label into five short
          lines. Only the choices dim while the line is not sent; the reason
          beside them keeps the full hint ink so it can still be read. */}
      <div
        className="settings-toggle-row flex-wrap"
        role="radiogroup"
        aria-label="Reply length"
        aria-describedby={lengthLineId}
        data-testid="reply-length"
      >
        <span className="min-w-[12rem] flex-1">
          <span className="settings-label">Reply length</span>
          <span className="settings-hint" id={lengthLineId}>
            {lengthLine}
          </span>
        </span>
        <span
          className={`settings-section-dimmable flex shrink-0 flex-wrap gap-1${lengthLive ? "" : " settings-section-disabled"}`}
        >
          {options.map((option) => (
            <button
              key={option.label}
              type="button"
              role="radio"
              aria-checked={words === option.value}
              disabled={!lengthLive || save.isPending}
              onClick={() => put({ reply_length_words: option.value })}
              className="settings-segment-button"
              data-active={words === option.value ? "true" : undefined}
            >
              {option.label}
            </button>
          ))}
        </span>
      </div>
      <Collapse open={tooLong}>
        {shownWords != null && (
          <p className="settings-voice-warning flex items-start gap-1.5" data-testid="reply-length-max-tokens">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>
              About {formatCount(shownWords)} words needs about{" "}
              {formatCount(Math.ceil(shownWords * WORDS_TO_TOKENS))} tokens, and
              replies stop at {formatCount(generation.max_tokens)} (Max new tokens
              in Generation settings). Raise it, or replies will be cut short.
            </span>
          </p>
        )}
      </Collapse>
    </>
  );
}
