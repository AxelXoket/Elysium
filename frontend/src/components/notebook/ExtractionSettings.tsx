/**
 * ExtractionSettings - who proposes notes, in which language, and does it work.
 *
 * The dry run is the reason this screen exists. Everything else about the
 * feature could be reasoned about; one thing could not - whether a small,
 * cheap model reads THIS user's Turkish well enough to be trusted with it.
 * The literature says structured output degrades on non-English input and that
 * the commonest failure is the model answering schema fields in the user's own
 * language, but nobody has measured it for Turkish. So the app stops arguing
 * and shows the output next to the text it came from.
 *
 * Nothing here is chosen automatically. It is the user's API key: picking a
 * model for them would spend it on something they never agreed to.
 *
 * Surface note: this lives inside `.glass-right`, the app's one LIGHT island,
 * which redefines the colour tokens. The `settings-*` classes are painted for
 * the dark settings dialog - a heading in `settings-section-title` renders at
 * about 1.2:1 here, present in the DOM and invisible on screen. The vocabulary
 * of this panel is the one its siblings use: an `h4` in `--color-es-text-light`,
 * `persona-field` for controls, `persona-local-error` for refusals.
 */
import { FlaskConical, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SlideIn } from "@/components/motion/SlideIn";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useExtractionModels,
  useExtractSettings,
  useSaveExtractSettings,
  useDryRun,
  useDryRunState,
} from "@/lib/query/notebook";

/** The parser's failure vocabulary, in sentences.
 *
 *  These are not error-catalogue codes - they ride a 200 response - but they
 *  reach a reader all the same, and `The reply could not be used (no_facts_key)`
 *  is not a sentence. Every other error surface in the app turns a token into
 *  prose; this one printed snake_case. */
const FAILURE_PROSE: Record<string, string> = {
  truncated:
    "the model ran out of room mid-answer. A cut-off reply is treated as a failure, never as \"nothing found\" - so nothing was skipped.",
  refused: "the model declined to answer this passage.",
  no_choices: "the reply arrived empty.",
  empty_content: "the reply had no content in it.",
  unparseable: "the reply was not the JSON it promised.",
  not_an_object: "the reply was JSON, but not of the shape the schema asked for.",
  no_facts_key: "the reply left out the one key the schema requires.",
  facts_not_a_list: "the reply put something other than a list where the notes go.",
};

/** Why a proposal was thrown away before it reached the screen.
 *
 *  Broken out because one number cannot tell "a quote was invented" - the
 *  defence working - from "a Turkish quote failed a byte comparison" - the
 *  defence eating a true fact. Those call for opposite responses. */
const DROP_PROSE: Record<string, string> = {
  ungrounded: "quoted something that is not in the text",
  off_schema: "answered outside the schema",
  too_long: "was longer than a note may be",
  empty_text: "came back empty",
  not_worth_a_slot: "was scene detail, not worth a permanent slot",
  over_cap: "arrived past the limit of six",
};

export function ExtractionSettings() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const models = useExtractionModels();
  const settings = useExtractSettings();
  const save = useSaveExtractSettings();
  const dryRun = useDryRun();
  // Read from the caches, not from this component's copy of the mutation:
  // the panel is remounted on every tab switch.
  const { pending, result } = useDryRunState(chatId);
  const pushError = useErrorStore((s) => s.pushError);

  // Read from the query, not from local state: `?? ""` on a query that has not
  // answered yet renders "Not chosen - suggestions are off", which is the one
  // string in this panel that must never be shown falsely.
  const loaded = settings.isSuccess;
  const chosen = settings.data?.model_id ?? "";
  const language = settings.data?.prompt_language ?? "en";
  const busy = save.isPending;

  async function pick(next: Partial<{ model_id: string; prompt_language: string }>) {
    try {
      await save.mutateAsync([next]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  async function runOnce() {
    if (chatId == null) return;
    try {
      await dryRun.mutateAsync([chatId]);
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Note suggestions">
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Note suggestions
        </h4>

        <p className="text-xs leading-relaxed text-muted-foreground">
          A cheap model can read the last few turns and propose notes. It runs
          on your own API key, so nothing is chosen for you - and until you pick
          a model, nothing runs at all.
        </p>

        <label className="block space-y-1">
          <span className="text-xs leading-relaxed text-muted-foreground">
            Model
          </span>
          <select
            value={chosen}
            disabled={busy || !loaded || models.isLoading}
            onChange={(e) => void pick({ model_id: e.target.value })}
            aria-label="Extraction model"
            className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-xs"
          >
            <option value="">
              {loaded ? "Not chosen - suggestions are off" : "Loading..."}
            </option>
            {(models.data?.models ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {/* Price first, then the id: a native select CLIPS rather
                    than wraps, and at the panel's 300px floor the tail of a
                    long vendor slug was eating both the price and the
                    single-provider warning - the two things the line is for. */}
                ${m.prompt_price.toFixed(3)}/M
                {m.endpoints === 1 ? " (1 provider)" : ""} - {m.id}
              </option>
            ))}
          </select>
        </label>

        {/* A silent empty picker cannot be told apart from "no model
            qualifies", and those mean opposite things. */}
        {models.isError ? (
          <p className="persona-local-error" role="alert">
            The list of models could not be fetched, so this is empty for a
            reason that has nothing to do with which models qualify.
          </p>
        ) : (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Only models that keep no copy of what they read and can return a
            strict schema are listed. A single-provider model stops working
            when that one provider does.
          </p>
        )}

        {settings.isError && (
          <p className="persona-local-error" role="alert">
            Your saved choice could not be read, so this panel does not know
            whether suggestions are on. Nothing was changed.
          </p>
        )}

        <label className="block space-y-1">
          <span className="text-xs leading-relaxed text-muted-foreground">
            Instructions written in
          </span>
          <select
            value={language}
            disabled={busy || !loaded}
            onChange={(e) => void pick({ prompt_language: e.target.value })}
            aria-label="Instruction language"
            className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-xs"
          >
            <option value="en">English</option>
            <option value="tr">Turkce</option>
          </select>
        </label>

        <p className="text-xs leading-relaxed text-muted-foreground">
          Notes are written in English either way. Which language the
          INSTRUCTIONS use is not settled - try both and keep the one that reads
          your messages better.
        </p>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={!chosen || chatId == null || pending}
          onClick={() => void runOnce()}
          className="persona-ghost-action h-8 gap-1.5 px-2 text-xs"
        >
          {pending ? (
            <Loader2 size={12} className="size-3 animate-spin" />
          ) : (
            <FlaskConical size={12} className="size-3" />
          )}
          Try it on this chat
        </Button>

        {result && (
          <div className="space-y-2" data-testid="dry-run-result">
            <p className="text-xs leading-relaxed text-muted-foreground">
              Nothing was saved. This is what it would have proposed.
            </p>

            {result.failure && (
              <p className="persona-local-error" role="alert">
                The reply could not be used:{" "}
                {FAILURE_PROSE[result.failure] ?? result.failure} A reply that
                cannot be trusted is reported as a failure, never as "nothing
                found".
              </p>
            )}

            {/* The source beside the answer. Read one against the other:
                quotes should be copied, not translated; names and diacritics
                should survive; the speaker credited should be the one who
                spoke. Bounded, so a long transcript cannot push the proposals
                and the cost line off the bottom of the panel. */}
            <div className="persona-card space-y-1">
              <p className="text-xs leading-relaxed text-muted-foreground">
                What it read
              </p>
              <pre // font-sans: a <pre> inherits the mono stack, and this is Turkish
              // prose meant to be read beside the proposals, not code.
              className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words font-sans text-xs">
                {result.source}
              </pre>
            </div>

            {result.proposals.map((p, i) => (
              <div key={i} className="persona-card space-y-1">
                <p className="break-words text-sm font-medium">{p.text}</p>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  quoted: "{p.evidence}"
                </p>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {p.kind} - {p.durability} - importance {p.importance}
                </p>
              </div>
            ))}

            {result.proposals.length === 0 && !result.failure && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                It proposed nothing. For a quiet scene that is the right answer.
              </p>
            )}

            {result.dropped > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {result.dropped} more were discarded before you saw them
                {Object.keys(result.dropped_by_reason ?? {}).length > 0 && (
                  <>
                    :{" "}
                    {Object.entries(result.dropped_by_reason ?? {})
                      .map(([why, n]) => `${n} ${DROP_PROSE[why] ?? why}`)
                      .join(", ")}
                  </>
                )}
                .
              </p>
            )}

            <p className="text-xs leading-relaxed text-muted-foreground">
              Cost: {(result.usage.cost ?? 0).toFixed(5)} credits ({result.usage.tokens_in ?? 0}
              {" in / "}
              {result.usage.tokens_out ?? 0} out)
            </p>
          </div>
        )}
      </section>
    </SlideIn>
  );
}
