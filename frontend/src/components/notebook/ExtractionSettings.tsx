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
import { settledAt } from "@/components/notebook/useRetryPress";
import { SettingUnread } from "@/components/notebook/SettingUnread";
import { CHOICE_UNREAD, EXTRACTION_WORDS } from "@/components/notebook/noteProse";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  CHAT_MODEL_OPTION_LABEL,
  INSTRUCTION_LANGUAGE_LABELS,
} from "@/lib/notebook/labels";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useExtractionModels,
  useExtractSettings,
  useSaveExtractSettings,
} from "@/lib/query/notebook";
import { CHAT_MODEL_BINDING } from "@/lib/schemas/notebook";

/** The sentence for "use the model I am chatting with". Exported so the
 *  test asserts the sentence the reader sees rather than a paraphrase of
 *  it. Mandatory, and mandatory BEFORE the choice: a chat model is usually
 *  many times a cheap extractor's price per token, the choice follows every
 *  change of chat model, and picking it in the list saves it immediately -
 *  so a sentence that appeared only once it was chosen would arrive after
 *  the decision it is about. The option lives inside a popup that closes on
 *  the choice, so nothing can sit beside it there: the sentence names the
 *  option, stays on screen whatever is chosen, and is the picker's own
 *  accessible description so a reader meets it before opening the list. */
export const CHAT_MODEL_PRICE_WARNING =
  `"${CHAT_MODEL_OPTION_LABEL}" costs your chat model's price.`
  + " Often many times a note model's, on every read.";

/** The id the picker points at with `aria-describedby`. Exported for the
 *  same reason the sentence is: the test asks the accessible DESCRIPTION of
 *  the control rather than looking for a paragraph somewhere on the page,
 *  and an id typed out in two places is an id that goes stale in one. */
export const CHAT_MODEL_PRICE_WARNING_ID = "extract-chat-model-price";

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
  // Insertion order is render order: the empty choice, then the standing
  // choice, then the models. The middle one is not a model - the backend
  // resolves it on every read to whatever the chat is using then - and it
  // comes before the list so it is met as a choice about the chat rather
  // than as an odd entry among vendor slugs. The price warning below is
  // not optional and is this picker's accessible description.
  const modelLabels: Record<string, string> = {
    "": loaded ? "Not chosen - suggestions are off" : "Loading...",
    [CHAT_MODEL_BINDING]: CHAT_MODEL_OPTION_LABEL,
  };
  // Price first, then the id: at the panel's 300px floor the tail of a long
  // vendor slug is what gets cut, and the price and the single-provider
  // warning are the two things this line exists to say.
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
          {EXTRACTION_WORDS.intro}
        </p>

        <label className="block space-y-1">
          {/* The words on screen are unchanged - the wording of this
              panel belongs to IP-02, not here - and they are now also the
              accessible name, through aria-labelledby on the trigger. The
              trigger used to carry an aria-label saying something else, so
              the screen and a screen reader named the same control two
              different ways and voice control could reach it by neither.
              Words that name a control are chrome, so this span and the
              language picker's below take the chrome step (13), the same as
              the boundary panel's four labels and the triggers they name. */}
          <span
            id={modelLabelId}
            className="text-ui leading-relaxed text-muted-foreground"
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
              aria-describedby={CHAT_MODEL_PRICE_WARNING_ID}
              className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-ui"
            >
              {/* The words sit in a span that truncates and carries the whole
                  label as its title, as the qualifiers and the strip's scope
                  field do. A vendor id runs past any width this panel has: at
                  the 300 px floor the field holds about 210 px of words and
                  one label here is 361, and it was cut mid-word with nothing
                  left to read it by (KURAL 4.4). The value is itself a flex
                  box, and text inside a flex box cannot end in an ellipsis,
                  so the span is what can. */}
              <SelectValue>
                {(value: string) => (
                  <span className="truncate" title={modelLabels[value] ?? value}>
                    {modelLabels[value] ?? value}
                  </span>
                )}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {/* The same in each row of the list: the list stops at 20rem
                  (KURAL 7.5), or at its field's own width where the field is
                  wider (the list's minimum is the field's width, and a
                  minimum outranks a maximum), so the longest id is cut there,
                  and before the check mark rather than under it. */}
              {Object.entries(modelLabels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  <span className="truncate" title={label}>{label}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        {/* The picker's own description, carried by `aria-describedby`
            rather than left as a paragraph a reader meets only by walking
            past the control. The plan marks this warning mandatory; a
            warning announced after the choice was made is not one. */}
        <p
          id={CHAT_MODEL_PRICE_WARNING_ID}
          className="text-label leading-relaxed text-muted-foreground"
          data-testid="chat-model-price-warning"
        >
          {CHAT_MODEL_PRICE_WARNING}
        </p>

        {/* A silent empty picker cannot be told apart from "no model
            qualifies", and those mean opposite things. */}
        {models.isError ? (
          <p className="persona-local-error" role="alert">
            {EXTRACTION_WORDS.listFailed}
          </p>
        ) : (
          <p className="text-label leading-relaxed text-muted-foreground">
            {EXTRACTION_WORDS.listNote}
          </p>
        )}

        <SettingUnread
          open={settings.isError}
          text={CHOICE_UNREAD}
          retrying={settings.isFetching}
          settled={settledAt(settings)}
          onRetry={() => void settings.refetch()}
        />

        <label className="block space-y-1">
          <span
            id={languageLabelId}
            className="text-ui leading-relaxed text-muted-foreground"
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
          {EXTRACTION_WORDS.languageNote}
        </p>
      </section>
    </SlideIn>
  );
}
