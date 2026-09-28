/**
 * ExtractionSettings - who proposes notes, and in which language.
 *
 * Two choices and nothing else: which cheap model reads the last few turns,
 * and whether its instructions are written in English or Turkish. Both are
 * saved the moment they are picked; there is no Save button to forget.
 *
 * Nothing here is chosen automatically. It is the user's API key: picking a
 * model for them would spend it on something they never agreed to. That is why
 * the empty option says what its emptiness MEANS - "suggestions are off" - and
 * why it is never shown before the saved choice has actually been read back.
 *
 * The extractor's output is not previewed here. It arrives as proposals in the
 * notebook itself, where it can be accepted or thrown away, which is the only
 * place a proposal was ever going to be judged.
 *
 * Surface note: this lives inside `.glass-right`, the app's one LIGHT island,
 * which redefines the colour tokens. The `settings-*` classes are painted for
 * the dark settings dialog - a heading in `settings-section-title` renders at
 * about 1.2:1 here, present in the DOM and invisible on screen. The vocabulary
 * of this panel is the one its siblings use: an `h4` in `--color-es-text-light`,
 * `persona-field` for controls, `persona-local-error` for refusals.
 */
import { useId } from "react";

import { SlideIn } from "@/components/motion/SlideIn";
import { Collapse } from "@/components/motion/Collapse";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { INSTRUCTION_LANGUAGE_LABELS } from "@/lib/notebook/labels";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useExtractionModels,
  useExtractSettings,
  useSaveExtractSettings,
} from "@/lib/query/notebook";

export function ExtractionSettings() {
  const modelLabelId = useId();
  const languageLabelId = useId();
  const chatId = useUiStore((s) => s.selectedChatId);
  const models = useExtractionModels();
  const settings = useExtractSettings();
  const save = useSaveExtractSettings();
  const pushError = useErrorStore((s) => s.pushError);

  // Read from the query, not from local state: `?? ""` on a query that has not
  // answered yet renders "Not chosen - suggestions are off", which is the one
  // string in this panel that must never be shown falsely.
  const loaded = settings.isSuccess;
  const chosen = settings.data?.model_id ?? "";
  const language = settings.data?.prompt_language ?? "en";
  const busy = save.isPending;

  // The trigger shows the label of whatever is chosen, so every value the
  // select can hold has to have one, the empty choice included.
  const modelLabels: Record<string, string> = {
    "": loaded ? "Not chosen - suggestions are off" : "Loading...",
  };
  for (const m of models.data?.models ?? []) {
    modelLabels[m.id] =
      `$${m.prompt_price.toFixed(3)}/M` +
      `${m.endpoints === 1 ? " (1 provider)" : ""} - ${m.id}`;
  }

  async function pick(next: Partial<{ model_id: string; prompt_language: string }>) {
    try {
      await save.mutateAsync([next]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Note suggestions">
        <h4
          className="text-label font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Note suggestions
        </h4>

        <p className="text-label leading-relaxed text-muted-foreground">
          A cheap model can read the last few turns and propose notes. It runs
          on your own API key, so nothing is chosen for you.
        </p>

        <label className="block space-y-1">
          {/* The words on screen are unchanged - the wording of this
              panel belongs to IP-02, not here - and they are now also the
              accessible name, through aria-labelledby on the trigger. The
              trigger used to carry an aria-label saying something else, so
              the screen and a screen reader named the same control two
              different ways and voice control could reach it by neither. */}
          <span
            id={modelLabelId}
            className="text-label leading-relaxed text-muted-foreground"
          >
            Model
          </span>
          <Select
            value={chosen}
            disabled={busy || !loaded || models.isLoading}
            onValueChange={(v) => {
              if (v != null) void pick({ model_id: String(v) });
            }}
            items={modelLabels}
          >
            <SelectTrigger
              aria-labelledby={modelLabelId}
              className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-ui"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(modelLabels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        {/* A silent empty picker cannot be told apart from "no model
            qualifies", and those mean opposite things. */}
        {models.isError ? (
          <p className="persona-local-error" role="alert">
            The list of models could not be fetched, so this is empty for a
            reason that has nothing to do with which models qualify.
          </p>
        ) : (
          <p className="text-label leading-relaxed text-muted-foreground">
            Only models that keep no copy of what they read and can return a
            strict schema are listed. A model with one provider stops working
            when that provider does.
          </p>
        )}

        <Collapse open={settings.isError}>
          <p className="persona-local-error" role="alert">
            Your saved choice could not be read, so this panel does not know
            whether suggestions are on. Nothing was changed.
          </p>
        </Collapse>

        <label className="block space-y-1">
          <span
            id={languageLabelId}
            className="text-label leading-relaxed text-muted-foreground"
          >
            Instructions written in
          </span>
          <Select
            value={language}
            disabled={busy || !loaded}
            onValueChange={(v) => {
              if (v != null) void pick({ prompt_language: String(v) });
            }}
            items={INSTRUCTION_LANGUAGE_LABELS}
          >
            <SelectTrigger
              aria-labelledby={languageLabelId}
              className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-ui"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(INSTRUCTION_LANGUAGE_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <p className="text-label leading-relaxed text-muted-foreground">
          Notes are written in English either way. Try both and keep whichever
          reads your messages better.
        </p>
      </section>
    </SlideIn>
  );
}
