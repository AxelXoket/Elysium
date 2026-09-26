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
 *      switch says (Ek O 7). The page says so beside the switch, so a user
 *      never reads "on" and believes it is going.
 *   3. Reply length (A9). A sentence inside the layer, so it only means
 *      anything while the layer is actually going: greyed out otherwise,
 *      with the reason.
 *
 * Nothing on this page adds a module or any text by itself. Every module
 * starts off; the user turns it on.
 */
import { AlertTriangle } from "lucide-react";

import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { isApiError } from "@/lib/api/client";
import { getErrorMessage, useErrorStore } from "@/lib/errors";
import { moduleFires } from "@/lib/prompt/assembly";

import { PromptModuleImport } from "./PromptModuleImport";
import { PromptModuleList } from "./PromptModuleList";
import { moduleCounter } from "./promptModuleCost";
import {
  usePromptModuleSettings,
  usePromptModules,
  useSavePromptModuleSettings,
} from "./promptModuleQueries";
import {
  PROMPT_MODULE_LIMITS,
  WORDS_TO_TOKENS,
  formatModuleCounter,
  lengthFitsMaxTokens,
  promptMode,
  type PromptMode,
} from "./promptModuleText";

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
  const layerSwitch = settings.data?.prompt_layer_enabled ?? false;
  const mode = promptMode(enabledAny > 0, layerSwitch);

  return (
    <div className="space-y-4">
      <section
        aria-label="Prompt modules"
        className="settings-voice-section space-y-2"
      >
        <h3 className="settings-section-title">Prompt modules</h3>
        {/* Always on screen, loading or not: the count is the one line that
            tells a user what every message is carrying. It reads 0 until the
            list arrives rather than hiding, because a hidden counter and a
            zero counter mean different things. */}
        <p
          className="settings-label"
          role="status"
          data-testid="prompt-module-counter"
        >
          {formatModuleCounter(counter.enabled, counter.tokens)}
        </p>
        <p className="settings-hint">
          Modules that are on go first in every chat, for every character, in
          the order below.
        </p>

        <PromptModuleImport />

        {modules.isError ? (
          <p className="settings-error" role="alert">
            {getErrorMessage(isApiError(modules.error) ? modules.error.detail : null)}
          </p>
        ) : modules.isPending ? (
          <p className="settings-category-desc">Loading…</p>
        ) : (
          <PromptModuleList modules={rows} />
        )}
      </section>

      <section
        aria-label="Roleplay layer"
        className="settings-voice-section space-y-2"
      >
        <h3 className="settings-section-title">Roleplay layer</h3>
        <LayerControls
          mode={mode}
          layerSwitch={layerSwitch}
          ready={settings.isSuccess}
          replyLengthWords={settings.data?.reply_length_words ?? null}
          enabledModules={enabledAny}
        />
      </section>
    </div>
  );
}

function LayerControls({
  mode,
  layerSwitch,
  ready,
  replyLengthWords,
  enabledModules,
}: {
  mode: PromptMode;
  layerSwitch: boolean;
  ready: boolean;
  replyLengthWords: number | null;
  enabledModules: number;
}) {
  const save = useSavePromptModuleSettings();
  const pushError = useErrorStore((s) => s.pushError);
  const { settings: generation } = useGenerationSettings();

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
  const lengthReason =
    mode === "a"
      ? `Prompt modules are on (${enabledModules}), so the roleplay layer and its length line are not sent.`
      : "The length line is part of the roleplay layer, which is off.";
  const words = replyLengthWords;
  const tooLong =
    lengthLive && words != null && !lengthFitsMaxTokens(words, generation.max_tokens);

  const options: { value: number | null; label: string }[] = [
    { value: null, label: "Adaptive" },
    ...PROMPT_MODULE_LIMITS.reply_length_words_allowed.map((n) => ({
      value: n,
      label: `About ${n} words`,
    })),
  ];

  return (
    <>
      <button
        type="button"
        className="settings-toggle-row"
        role="switch"
        aria-checked={layerSwitch}
        aria-label="Roleplay layer"
        // Not operable until the stored value is known: a switch showing a
        // guess invites a click that writes the opposite of what was meant.
        disabled={!ready || save.isPending}
        onClick={() => put({ prompt_layer_enabled: !layerSwitch })}
      >
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label">Roleplay layer</span>
          <span className="settings-category-desc">
            The app's own craft rules, with the character card inside them
          </span>
        </span>
        <span className="settings-switch" data-on={layerSwitch ? "true" : "false"}>
          <span className="settings-switch-thumb" />
        </span>
      </button>
      <p className="settings-hint" data-testid="prompt-layer-note">
        Never sent while any prompt module is on.
      </p>
      {mode === "a" && layerSwitch && (
        <p className="settings-voice-warning" data-testid="prompt-layer-suppressed">
          Not being sent right now: {enabledModules === 1 ? "1 module is" : `${enabledModules} modules are`} on.
          It comes back when the last module is turned off.
        </p>
      )}

      <div
        className={`settings-section-dimmable${lengthLive ? "" : " settings-section-disabled"}`}
        data-testid="reply-length"
      >
        <div className="settings-toggle-row" role="radiogroup" aria-label="Reply length">
          <span className="min-w-0 flex-1">
            <span className="settings-label">Reply length</span>
            <span className="settings-hint opacity-70">
              Adaptive sends no length line; the card decides.
            </span>
          </span>
          <span className="flex shrink-0 flex-wrap gap-1">
            {options.map((option) => (
              <button
                key={option.label}
                type="button"
                role="radio"
                aria-checked={words === option.value}
                disabled={!lengthLive || !ready || save.isPending}
                onClick={() => put({ reply_length_words: option.value })}
                className="settings-segment-button"
                data-active={words === option.value ? "true" : undefined}
              >
                {option.label}
              </button>
            ))}
          </span>
        </div>
      </div>
      {!lengthLive && (
        <p className="settings-hint" data-testid="reply-length-reason">
          {lengthReason}
        </p>
      )}
      {tooLong && words != null && (
        <p className="settings-voice-warning flex items-start gap-1.5" data-testid="reply-length-max-tokens">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          About {words} words needs about {Math.ceil(words * WORDS_TO_TOKENS)} tokens,
          and replies stop at {generation.max_tokens} (Max tokens in the generation
          settings). Raise it, or replies will be cut short.
        </p>
      )}
    </>
  );
}
