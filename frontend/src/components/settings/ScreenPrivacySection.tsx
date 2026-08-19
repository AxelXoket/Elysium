/**
 * ScreenPrivacySection - hide the window from screen capture.
 *
 * The backend for this shipped a while ago: the vault-stored setting, the
 * apply-on-unlock / remove-on-lock transition, and its tests. Nothing in the
 * UI ever reached it, so the toggle the owner asked for existed only as an
 * HTTP route. This is that half.
 *
 * Two things are said out loud rather than left to be discovered:
 *
 *   * it does NOT apply while the vault is locked, on purpose - a locked
 *     screen has no conversation on it, and the passphrase field is masked;
 *   * it is a layer, not a guarantee. It stops the ordinary Windows capture
 *     and screen-share paths. It is not a promise that no pixel can ever be
 *     read by anything.
 *
 * Default OFF, because the owner said they take screenshots of this app.
 */
import { MonitorOff } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useSettings, useSetScreenPrivacy } from "@/lib/query/settings";

export function ScreenPrivacySection() {
  const settings = useSettings();
  const save = useSetScreenPrivacy();
  const enabled = settings.data?.screen_privacy_enabled ?? false;

  return (
    <section className="space-y-2" aria-label="Screen privacy">
      {/* `settings-section-title` is the dark dialog's heading class - 12px
          uppercase at rgba(202,212,224,0.62), about 1.2:1 here and the worst
          case on this surface: it is the label of a SECURITY control. The
          sibling notebook panels already carry the right idiom for a
          heading on `.glass-right`: an `h4` in `--color-es-text-light`. */}
      <h4
        data-testid="screen-privacy-heading"
        className="text-xs font-semibold"
        style={{ color: "var(--color-es-text-light)" }}
      >
        Screen privacy
      </h4>

      <label className="flex items-center gap-2">
        <Switch
          checked={enabled}
          // Not operable until the stored value is known. A protection switch
          // that shows a guess and accepts a click is the worst kind: the
          // user believes it is on.
          disabled={save.isPending || !settings.isSuccess}
          onCheckedChange={(v) => save.mutate(v)}
        />
        {/* Same defect as the two paragraphs below, on the one line that
            names the control. `text-muted-foreground` is the sibling
            panels' hint idiom - it reads `--muted-foreground`, which
            `.glass-right` redefines for this surface. */}
        <span
          data-testid="screen-privacy-switch-label"
          className="flex items-center gap-1.5 text-xs leading-relaxed text-muted-foreground"
        >
          <MonitorOff size={13} className="shrink-0" />
          Hide this window from screen capture
        </span>
      </label>

      <p data-testid="screen-privacy-hint-1" className="text-xs leading-relaxed text-muted-foreground">
        Screenshots, screen recording and screen sharing see a blank window
        instead of your conversation. It is not applied while the vault is
        locked - there is nothing on that screen to protect - and it comes
        back the moment you unlock.
      </p>

      <p data-testid="screen-privacy-hint-2" className="text-xs leading-relaxed text-muted-foreground">
        A layer, not a guarantee: it stops the ordinary Windows capture paths,
        not every possible way a screen can be read. Off by default, so your
        own screenshots keep working until you say otherwise.
      </p>
    </section>
  );
}
