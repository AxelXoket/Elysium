/**
 * WorkerPanel - what the background extractor has been doing with your money.
 *
 * The whole feature spends the user's own OpenRouter credits, unattended,
 * while they are reading something else. There are exactly two ways that goes
 * wrong: a loop that will not stop, and a refusal nobody can see. This screen
 * is the answer to the second one.
 *
 * Without it, "the notebook has proposed nothing this week" and "the notebook
 * refused sixty times for a reason nobody can see" are the same screen - and
 * the second is the one that needs a person.
 *
 * Surface: `.glass-right`, the app's one LIGHT island. Heading and controls
 * follow the vocabulary its siblings use, not the dark settings dialog's.
 */
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SlideIn } from "@/components/motion/SlideIn";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useWorkerStatus,
  useResetWorker,
  useAutoAccept,
  useSetAutoAccept,
} from "@/lib/query/notebook";

/** Why a run was refused, in words. These are machine tokens on the wire and
 *  must not reach a reader as such. */
const SKIP_PROSE: Record<string, string> = {
  notebook_daily_cap_reached: "today's call limit was already used",
  proxy_gate: "your proxy was required and not healthy",
};

/** The three states, said plainly. "closed" is engineering vocabulary for
 *  "working", and a panel that prints it is asking the reader to learn the
 *  implementation before they can read their own status. */
const STATE_PROSE: Record<string, string> = {
  closed: "Running.",
  open: "Paused after repeated failures. It will try again by itself.",
  stopped: "Stopped after too many failures. It will not try again until you say so.",
};

export function WorkerPanel() {
  const status = useWorkerStatus();
  const reset = useResetWorker();
  const auto = useAutoAccept();
  const setAuto = useSetAutoAccept();
  const pushError = useErrorStore((s) => s.pushError);

  const body = status.data;
  const state = body?.worker.state ?? "closed";
  const skips = body?.stats.skip_reasons ?? {};

  async function toggle(next: boolean) {
    try {
      await setAuto.mutateAsync([next]);
    } catch (err) {
      pushError(err, "error");
    }
  }

  async function unstick() {
    try {
      await reset.mutateAsync();
    } catch (err) {
      pushError(err, "error");
    }
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Background notes">
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Background notes
        </h4>

        <label className="flex items-center gap-2">
          <Switch
            checked={auto.data?.enabled ?? true}
            // Not operable until the stored value is known: a switch that
            // shows a guess and accepts a click is the "you believe it is in
            // force and it is not" failure this feature warns about.
            disabled={setAuto.isPending || !auto.isSuccess}
            onCheckedChange={(v) => void toggle(v)}
          />
          {/* No aria-label. The wrapping <label> already names this control
              with the words on screen; an aria-label OVERRIDES those, so
              voice control could not address the switch by what it says -
              and here it also duplicated the name, which is worse. */}
          <span className="text-xs leading-relaxed text-muted-foreground">
            Keep suggestions without asking
          </span>
        </label>

        <p className="text-xs leading-relaxed text-muted-foreground">
          Off, a suggestion waits in the list until you keep it, and is not
          sent to the model meanwhile. A chat opened from an imported card
          always waits, whatever this says.
        </p>

        {body && (
          <div className="persona-card space-y-1" data-testid="worker-status">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {STATE_PROSE[state] ?? state}
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {body.stats.done} runs · {body.spend.calls} of {body.daily_cap}{" "}
              calls today · {body.spend.cost.toFixed(5)} credits{" "}
              {/* Same line as today's count - "beside", not below - but
                  dimmed with the app's existing secondary-text idiom
                  (opacity-70, as ModelPanel's "Selected:" label and
                  ImageOutputSetting's hint text already use) and named
                  "lifetime" rather than positioned next to "today" with no
                  label of its own. Today's number is the one that governs
                  the cap, so it keeps full weight; this one does not. */}
              <span className="opacity-70">
                ({body.spend_lifetime.calls} lifetime)
              </span>
            </p>

            {/* Failures and refusals are separate lines because they call for
                different things: one is the provider's problem, the other is
                a limit doing its job. */}
            {body.stats.failed > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.stats.failed} runs failed. Nothing was lost - the
                messages they could not read stay unread, not skipped.
              </p>
            )}

            {Object.entries(skips).map(([why, n]) => (
              <p
                key={why}
                className="text-xs leading-relaxed text-muted-foreground"
              >
                {n} skipped: {SKIP_PROSE[why] ?? why}.
              </p>
            ))}

            {/* The worker died. Everything else on this card describes a
                worker that is running; without this line the card describes
                one that is not, in the same words. */}
            {!body.worker.alive && (
              <p className="persona-local-error" role="alert">
                The background reader has stopped
                {body.worker.died ? ` (${body.worker.died})` : ""}. Restart
                Elysium to bring it back. Nothing was lost - unread messages
                stay unread.
              </p>
            )}

            {body.worker.unhandled > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.unhandled} runs ended in an unexpected error
                {body.worker.last_error ? ` (${body.worker.last_error})` : ""}.
                Those messages stay unread.
              </p>
            )}

            {body.worker.refused_by_breaker > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.refused_by_breaker} turns were passed over while
                it was cooling down. Their messages are still unread.
              </p>
            )}

            {body.worker.dropped_offers > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.dropped_offers} turns went unqueued while it was
                behind. Their messages are still unread, so a later run picks
                them up.
              </p>
            )}
          </div>
        )}

        {/* Only when it means something. A reset button that is always there
            invites pressing it at random; one that appears when the thing is
            actually stuck says what it is for. */}
        {(state === "stopped" || state === "open") && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={reset.isPending}
            onClick={() => void unstick()}
            className="persona-ghost-action h-7 gap-1.5 px-2 text-xs"
          >
            <RotateCcw size={12} className="size-3" />
            Try again now
          </Button>
        )}
      </section>
    </SlideIn>
  );
}
