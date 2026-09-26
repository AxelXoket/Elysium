import type { ModalitySupport } from "@/lib/models/modelHelpers";
import { Button } from "../ui/button";

/**
 * Copy shown when the catalogue POSITIVELY rules image output out: the list is
 * populated and "image" is not in it. Exported so the test that asserts its
 * absence imports the sentence instead of retyping it, which is what makes the
 * absence assertion mean anything.
 */
export const IMAGE_OUTPUT_NOT_ASKED_NOTE =
  "The selected model does not list image output, so it will not be asked for one. The setting stays on for models that do.";

/**
 * Copy shown when the catalogue says NOTHING about output modalities.
 *
 * This is the sentence the unknown state was missing. `_model_emits_images`
 * asks for a picture in exactly this state, so the reader has to be told that,
 * not the opposite of it.
 */
export const IMAGE_OUTPUT_UNKNOWN_NOTE =
  "Elysium has no output modality list for the selected model, so the request still asks for a picture and the provider decides.";

/**
 * Let the model answer with a picture.
 *
 * Lives with the generation settings rather than with the appearance ones
 * because it changes the outgoing REQUEST: it adds `modalities: ["text","image"]`
 * unless the selected model's cached metadata positively rules image output
 * out. It is stored in the vault for the same reason.
 *
 * Off by default and deliberately not gated on the currently selected model's
 * capability: the switch is a standing preference, and greying it out because
 * the model open right now cannot draw would make it mean something different
 * from what it says. Whether a given request actually asks is decided per
 * request, server-side, in one place.
 */
export function ImageOutputSetting({
  enabled,
  onChange,
  supported,
  busy,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  /**
   * What the catalogue says about image output for the selected model.
   * Informational only; the per-request decision is server-side.
   *
   * `"unknown"` must never render as `"no"`. The backend asks for a picture in
   * that state, so a "will not be asked for one" line there would be a false
   * negative about the request this app is about to send.
   */
  supported: ModalitySupport;
  busy?: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <label className="text-xs font-semibold" htmlFor="image-output-toggle">
          Let replies include pictures
        </label>
        <Button
          id="image-output-toggle"
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-busy={busy || undefined}
          size="sm"
          variant={enabled ? "default" : "ghost"}
          className="text-xs"
          disabled={busy}
          onClick={() => onChange(!enabled)}
        >
          {enabled ? "On" : "Off"}
        </Button>
      </div>
      <p className="generation-helper">
        {enabled
          ? "Models that can draw may answer with an image as well as words. Pictures are stored in your encrypted vault like any attachment, and are never sent back to the model on later turns."
          : "Off. Replies are text only."}
      </p>
      {enabled && supported === "no" ? (
        <p className="generation-helper">
          {IMAGE_OUTPUT_NOT_ASKED_NOTE}
        </p>
      ) : null}
      {enabled && supported === "unknown" ? (
        <p className="generation-helper">
          {IMAGE_OUTPUT_UNKNOWN_NOTE}
        </p>
      ) : null}
    </div>
  );
}
