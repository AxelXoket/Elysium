import { Button } from "../ui/button";

/**
 * Let the model answer with a picture.
 *
 * Lives with the generation settings rather than with the appearance ones
 * because it changes the outgoing REQUEST: it adds `modalities: ["text","image"]`
 * when the selected model declares image output. It is stored in the vault for
 * the same reason.
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
  /** Whether the selected model declares image output. Informational only. */
  supported: boolean;
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
      <p className="text-[11px] leading-snug opacity-70">
        {enabled
          ? "Models that can draw may answer with an image as well as words. Pictures are stored in your encrypted vault like any attachment, and are never sent back to the model on later turns."
          : "Off. Replies are text only."}
      </p>
      {enabled && !supported ? (
        <p className="text-[11px] leading-snug opacity-70">
          The selected model does not list image output, so it will not be asked
          for one. The setting stays on for models that do.
        </p>
      ) : null}
    </div>
  );
}
