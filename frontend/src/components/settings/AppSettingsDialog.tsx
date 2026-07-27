/**
 * AppSettingsDialog - the bottom-left Settings entry point.
 *
 * A nested-page dialog: the root page lists setting categories; picking one
 * slides to that category's page INSIDE the same dialog (back arrow top-left,
 * the house close X top-right). Page transitions reuse the VariantCarousel
 * primitive so settings navigation moves exactly like the rest of the app.
 *
 * Pages own only APPEARANCE preferences (persisted in uiStore - harmless UI
 * prefs, never content or secrets). The Secrets row is a bridge: it closes
 * this dialog and opens the right panel's Secrets tab, preserving the old
 * Settings-button behavior as a discoverable path.
 */
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { VariantCarousel } from "@/components/motion/VariantCarousel";
import { MessageText } from "@/components/chat/MessageText";
import { ACCEPTED_IMAGE_ACCEPT } from "@/components/chat/attachments";
import {
  CHAT_BG_CONTRAST_MAX,
  CHAT_BG_CONTRAST_MIN,
  CHAT_BG_TINTS,
  processChatBgImage,
} from "@/lib/appearance/chatBackground";
import { deleteChatBgBlob, putChatBgBlob } from "@/lib/store/chatBgDb";
import {
  useUiStore,
  MSG_FONT_DEFAULT,
  MSG_FONT_MIN,
  MSG_FONT_MAX,
  MSG_LINE_DEFAULT,
  MSG_LINE_MIN,
  MSG_LINE_MAX,
} from "@/lib/store/uiStore";
import { useTtsActive } from "@/lib/query/tts";
import { saveTagPrefs } from "@/lib/api/tts";
import { useErrorStore } from "@/lib/errors";
import { InkPicker } from "./InkPicker";
import { VoiceSettingsPage } from "./VoiceSettingsPage";
import {
  ArrowLeft,
  AudioLines,
  ChevronRight,
  Image as ImageIcon,
  KeyRound,
  Settings,
  Sparkles,
  Type,
} from "lucide-react";

type SettingsPage = "root" | "text" | "narration" | "background" | "voice";

const PAGE_TITLES: Record<SettingsPage, string> = {
  root: "Settings",
  text: "Text & readability",
  narration: "Narration style",
  background: "Chat background",
  voice: "Voice",
};

const PAGE_DESCRIPTIONS: Record<SettingsPage, string> = {
  root: "Appearance and reading preferences. Stored on this device only.",
  text: "Message body size and spacing. Labels and controls stay fixed.",
  narration: "How *asterisk* narration reads inside messages.",
  background: "A picture behind the conversation, tuned for readability.",
  voice: "Local voice models, cloning, and spoken replies. Fully on-device.",
};

interface AppSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AppSettingsDialog({
  open,
  onOpenChange,
}: AppSettingsDialogProps) {
  const [page, setPage] = useState<SettingsPage>("root");
  const [direction, setDirection] = useState<1 | -1>(1);
  const [hasNavigated, setHasNavigated] = useState(false);
  const setTab = useUiStore((s) => s.setActiveRightPanelTab);

  // Opened AT a page (the composer's voice hint asks for "voice"). Consumed on
  // the commit that opens, so closing and reopening by hand still starts at the
  // root - a dialog that keeps jumping to a sub-page is disorienting.
  const initialPage = useUiStore((s) => s.settingsInitialPage);
  const [seenInitialPage, setSeenInitialPage] = useState<string | null>(null);
  if (open && initialPage != null && initialPage !== seenInitialPage) {
    setSeenInitialPage(initialPage);
    setPage(initialPage as SettingsPage);
    setHasNavigated(true);
  }
  if (!open && seenInitialPage != null) setSeenInitialPage(null);

  const goTo = (next: SettingsPage) => {
    setDirection(1);
    setHasNavigated(true);
    setPage(next);
  };

  const goBack = () => {
    setDirection(-1);
    setHasNavigated(true);
    setPage("root");
  };

  const handleOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      // Fresh entry next time - reopening on a stale sub-page is disorienting.
      setPage("root");
      setHasNavigated(false);
    }
  };

  const openSecrets = () => {
    handleOpenChange(false);
    setTab("secrets");
  };

  // Keyboard flow: a page swap unmounts the control that was activated,
  // dropping focus to <body>. Re-anchor it - the back button after forward
  // navigation, the first category row after going back. Skipped on initial
  // open (hasNavigated), where the dialog's own focus handling applies.
  useEffect(() => {
    if (!hasNavigated) return;
    const root = document.querySelector<HTMLElement>(".settings-dialog");
    if (!root) return;
    const target =
      page === "root"
        ? root.querySelector<HTMLElement>(".settings-category-row")
        : root.querySelector<HTMLElement>(".settings-back-button");
    target?.focus();
  }, [page, hasNavigated]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="glass-dialog sidebar-dialog settings-dialog max-h-[85vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-base font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            {page !== "root" ? (
              <button
                type="button"
                className="settings-back-button"
                aria-label="Back to settings"
                title="Back to settings"
                onClick={goBack}
              >
                <ArrowLeft size={14} />
              </button>
            ) : (
              <Settings size={15} />
            )}
            {PAGE_TITLES[page]}
          </DialogTitle>
          <DialogDescription
            className="text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            {PAGE_DESCRIPTIONS[page]}
          </DialogDescription>
        </DialogHeader>

        <VariantCarousel
          paneKey={page}
          direction={direction}
          animateEnter={hasNavigated}
        >
          {page === "root" && (
            <div className="space-y-2">
              <CategoryRow
                icon={<Type size={15} />}
                title="Text & readability"
                description="Message font size and line spacing"
                onClick={() => goTo("text")}
              />
              <CategoryRow
                icon={<Sparkles size={15} />}
                title="Narration style"
                description="Emphasis for *asterisk* narration"
                onClick={() => goTo("narration")}
              />
              <CategoryRow
                icon={<ImageIcon size={15} />}
                title="Chat background"
                description="Picture, contrast, and adaptive text"
                onClick={() => goTo("background")}
              />
              <CategoryRow
                icon={<AudioLines size={15} />}
                title="Voice"
                description="Spoken replies with local voice models"
                onClick={() => goTo("voice")}
              />
              <CategoryRow
                icon={<KeyRound size={15} />}
                title="Secrets & API"
                description="API key and proxy, in the side panel"
                onClick={openSecrets}
              />
              <AmbientMistToggle />
            </div>
          )}
          {page === "text" && <TextSettingsPage />}
          {page === "narration" && <NarrationSettingsPage />}
          {page === "background" && <BackgroundSettingsPage />}
          {page === "voice" && <VoiceSettingsPage />}
        </VariantCarousel>
      </DialogContent>
    </Dialog>
  );
}

// ── Root page rows ─────────────────────────────────────────────────

function CategoryRow({
  icon,
  title,
  description,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="settings-category-row"
      onClick={onClick}
    >
      <span className="settings-category-icon">{icon}</span>
      <span className="min-w-0 flex-1 text-left">
        <span className="settings-label">{title}</span>
        <span className="settings-category-desc">{description}</span>
      </span>
      <ChevronRight size={14} style={{ opacity: 0.45 }} />
    </button>
  );
}

/** Root-level switch for the living mist backdrop - no sub-page needed. */
function AmbientMistToggle() {
  const ambientFogOn = useUiStore((s) => s.ambientFogOn);
  const setAmbientFogOn = useUiStore((s) => s.setAmbientFogOn);
  return (
    <ToggleRow
      title="Ambient mist"
      description="Drifting fog behind the app - light GPU use"
      checked={ambientFogOn}
      onToggle={() => setAmbientFogOn(!ambientFogOn)}
    />
  );
}

// ── Text & readability ─────────────────────────────────────────────

const CONTRAST_LEVELS = ["soft", "default", "high"] as const;

function TextSettingsPage() {
  const msgFontPx = useUiStore((s) => s.msgFontPx);
  const msgLineHeight = useUiStore((s) => s.msgLineHeight);
  const msgContrast = useUiStore((s) => s.msgContrast);
  const msgInk = useUiStore((s) => s.msgInk);
  const surfaceFinish = useUiStore((s) => s.surfaceFinish);
  const setMsgFontPx = useUiStore((s) => s.setMsgFontPx);
  const setMsgInk = useUiStore((s) => s.setMsgInk);
  const setSurfaceFinish = useUiStore((s) => s.setSurfaceFinish);
  const setMsgLineHeight = useUiStore((s) => s.setMsgLineHeight);
  const setMsgContrast = useUiStore((s) => s.setMsgContrast);
  // EVERY setting the reset handler clears. It used to check only font, line
  // height and contrast while the handler also cleared msgInk and
  // surfaceFinish, so the button disagreed with itself in both directions: a
  // custom ink plus a Glossy finish left "Reset to defaults" permanently
  // disabled (the advertised escape hatch unreachable), and a user who had
  // only nudged the font size clicked it and silently lost the ink colour.
  const isDefault =
    msgFontPx === MSG_FONT_DEFAULT &&
    msgLineHeight === MSG_LINE_DEFAULT &&
    msgContrast === "default" &&
    msgInk == null &&
    surfaceFinish === "matte";

  return (
    <div className="space-y-4">
      <SliderRow
        label="Font size"
        value={msgFontPx}
        min={MSG_FONT_MIN}
        max={MSG_FONT_MAX}
        step={0.5}
        display={`${msgFontPx}px`}
        helper="Applies to message text only."
        onChange={setMsgFontPx}
      />
      <SliderRow
        label="Line spacing"
        value={msgLineHeight}
        min={MSG_LINE_MIN}
        max={MSG_LINE_MAX}
        step={0.05}
        display={msgLineHeight.toFixed(2)}
        helper="Room between lines in a message."
        onChange={setMsgLineHeight}
      />

      <div className="generation-control">
        <label className="settings-label">Message contrast</label>
        <div
          className="mt-2 flex items-center gap-2"
          role="radiogroup"
          aria-label="Message contrast"
        >
          {CONTRAST_LEVELS.map((level) => (
            <button
              key={level}
              type="button"
              role="radio"
              aria-checked={msgContrast === level}
              aria-label={`${level[0].toUpperCase()}${level.slice(1)} contrast`}
              className="settings-segment-option"
              data-selected={msgContrast === level ? "true" : "false"}
              onClick={() => setMsgContrast(level)}
            >
              {level === "soft" ? "Soft" : level === "high" ? "High" : "Default"}
            </button>
          ))}
        </div>
        <p className="generation-helper">
          How strongly message text stands out from its bubble.
        </p>
      </div>

      {/* The ink rides along with the contrast preset, exactly as it does on
          the real scroller (ChatCanvas). `.settings-preview.msg-ink-custom`
          existed in index.css but nothing ever put the class or the variable
          on a preview, so the picker showed a contrast RATIO and no colour -
          the user had to close the dialog and read a real message to find out
          what they had chosen. */}
      <div
        className={`settings-preview${
          msgContrast === "default" ? "" : ` msg-contrast-${msgContrast}`
        }${msgInk ? " msg-ink-custom" : ""}`}
        style={msgInk ? ({ ["--msg-ink-custom"]: msgInk } as CSSProperties) : undefined}
        aria-hidden="true"
      >
        <p
          className="message-text whitespace-pre-wrap"
          style={{
            ["--msg-fs" as string]: `${msgFontPx}px`,
            ["--msg-lh" as string]: String(msgLineHeight),
          }}
        >
          A quick preview of message text at this size, wrapping over a couple
          of lines so the spacing is easy to judge.
        </p>
      </div>

      <div className="flex justify-end">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="sidebar-dialog-cancel text-xs"
          disabled={isDefault}
          onClick={() => {
            setMsgFontPx(MSG_FONT_DEFAULT);
            setMsgLineHeight(MSG_LINE_DEFAULT);
            setMsgContrast("default");
            setMsgInk(null);
            setSurfaceFinish("matte");
          }}
        >
          Reset to defaults
        </Button>
      </div>

      {/* Ink and finish live on THIS page rather than under a new heading:
          they are the same decision as the contrast preset above them - how
          the message text reads - and separating them would mean tuning one
          while the thing it depends on is two pages away. */}
      <SurfaceFinishRow />
      <InkPicker />
    </div>
  );
}

function SliderRow({
  label,
  value,
  min,
  max,
  step,
  display,
  helper,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  helper: string;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="generation-control">
      <div className="flex items-center justify-between gap-3">
        <label className="settings-label">{label}</label>
        <span className="settings-value">
          {display}
        </span>
      </div>
      <input
        type="range"
        aria-label={`${label} slider`}
        min={min}
        max={max}
        step={step}
        value={value}
        className="generation-range"
        disabled={disabled}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
      <p className="generation-helper">{helper}</p>
    </div>
  );
}

// ── Narration style ────────────────────────────────────────────────

const NARRATION_SAMPLE =
  '*She smiles softly and waves.* "It is good to see you again."';

function NarrationSettingsPage() {
  const narrationEnabled = useUiStore((s) => s.narrationEnabled);
  const setNarrationEnabled = useUiStore((s) => s.setNarrationEnabled);
  const quoteTintEnabled = useUiStore((s) => s.quoteTintEnabled);
  const setQuoteTintEnabled = useUiStore((s) => s.setQuoteTintEnabled);

  return (
    <div className="space-y-4">
      <ToggleRow
        title="Style narration"
        description="Render *text between asterisks* as narration"
        checked={narrationEnabled}
        onToggle={() => setNarrationEnabled(!narrationEnabled)}
      />
      <ToggleRow
        title="Tint quoted speech"
        description={'Color "spoken lines" with the theme accent'}
        checked={quoteTintEnabled}
        onToggle={() => setQuoteTintEnabled(!quoteTintEnabled)}
      />

      {/* Live preview through the REAL parser - one source of truth. */}
      <div className="settings-preview">
        <p className="message-text whitespace-pre-wrap">
          <MessageText text={NARRATION_SAMPLE} />
        </p>
      </div>
      <p className="generation-helper">
        Copying a message always copies the original text, asterisks included.
      </p>

      {/* Narration is decided ONCE and used twice: the spans styled above are
          the spans voiced below. Keeping the choice on this page rather than
          under Voice is what makes that relationship visible. */}
      <NarrationVoiceRow />
      <p className="generation-helper">
        Only applies while replies are being spoken aloud.
      </p>
    </div>
  );
}

/**
 * How narration is SPOKEN (V9-3).
 *
 * "Narrator tone" works by handing the span to the engine with a delivery tag,
 * so it only means anything on an engine that reads inline directions; on the
 * others the text is spoken normally rather than having brackets read aloud.
 * Nothing here is shown unless a voice model is actually selected - a control
 * that cannot affect anything is clutter at best.
 */
/**
 * Bubble surface finish (V11).
 *
 * Bubbles ONLY. The v1.1 pass deliberately flattened every control - no bevels,
 * no gradients - and that rule is not reopened for a preference: a control has
 * to read as pressable and shading fights that. A message bubble is content,
 * so it can carry a surface quality without contradicting anything.
 */
function SurfaceFinishRow() {
  const finish = useUiStore((s) => s.surfaceFinish);
  const setFinish = useUiStore((s) => s.setSurfaceFinish);

  const OPTIONS = [
    { value: "matte" as const, label: "Matte", hint: "Flat, as today" },
    { value: "glossy" as const, label: "Glossy", hint: "A hairline of light along the top" },
    { value: "metallic" as const, label: "Metallic", hint: "A cool directional wash" },
  ];

  return (
    <div className="settings-toggle-row" role="radiogroup" aria-label="Bubble finish">
      <span className="min-w-0 flex-1">
        <span className="settings-label">Bubble finish</span>
        <span className="settings-hint opacity-70">
          How message surfaces catch light. Controls stay flat.
        </span>
      </span>
      <span className="flex shrink-0 gap-1">
        {OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={finish === option.value}
            title={option.hint}
            onClick={() => setFinish(option.value)}
            className="settings-segment-button"
            data-active={finish === option.value ? "true" : undefined}
          >
            {option.label}
          </button>
        ))}
      </span>
    </div>
  );
}

function NarrationVoiceRow() {
  const active = useTtsActive();
  const mode = useUiStore((s) => s.narrationVoice);
  const setMode = useUiStore((s) => s.setNarrationVoice);

  // KÖK 6: the choice ALSO has to be stored, not just carried on the live
  // request. The replay path has read tts_narrative all along and nothing
  // wrote it, so "Skip" was honoured while a reply streamed and silently
  // ignored when the Speak button repeated the very same message. Best-effort:
  // the live path still works from the store if this write fails, and a toast
  // about a preference that did apply would be its own kind of lie.
  const choose = (next: "same" | "narrator" | "skip") => {
    setMode(next);
    void saveTagPrefs({ narrative: next }).catch(() => undefined);
  };

  if (!active.data?.uid) return null;

  const OPTIONS = [
    { value: "same" as const, label: "Same voice", hint: "Read like the rest" },
    { value: "narrator" as const, label: "Narrator", hint: "A flatter, told tone" },
    { value: "skip" as const, label: "Skip", hint: "Speak dialogue only" },
  ];

  return (
    <div className="settings-toggle-row" role="radiogroup" aria-label="Narration voice">
      <span className="min-w-0 flex-1">
        <span className="settings-label">Narration voice</span>
        <span className="settings-hint opacity-70">
          How *narration* sounds when replies are read aloud.
        </span>
      </span>
      <span className="flex shrink-0 gap-1">
        {OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={mode === option.value}
            title={option.hint}
            onClick={() => choose(option.value)}
            className="settings-segment-button"
            data-active={mode === option.value ? "true" : undefined}
          >
            {option.label}
          </button>
        ))}
      </span>
    </div>
  );
}

function ToggleRow({
  title,
  description,
  checked,
  onToggle,
}: {
  title: string;
  description: string;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="settings-toggle-row"
      role="switch"
      aria-checked={checked}
      aria-label={title}
      onClick={onToggle}
    >
      <span className="min-w-0 flex-1 text-left">
        <span className="settings-label">{title}</span>
        <span className="settings-category-desc">{description}</span>
      </span>
      <span className="settings-switch" data-on={checked ? "true" : "false"}>
        <span className="settings-switch-thumb" />
      </span>
    </button>
  );
}

// ── Chat background ────────────────────────────────────────────────

function BackgroundSettingsPage() {
  const chatBgOn = useUiStore((s) => s.chatBgOn);
  const chatBgContrast = useUiStore((s) => s.chatBgContrast);
  const chatBgTint = useUiStore((s) => s.chatBgTint);
  const setChatBgMeta = useUiStore((s) => s.setChatBgMeta);
  const clearChatBg = useUiStore((s) => s.clearChatBg);
  const setChatBgContrast = useUiStore((s) => s.setChatBgContrast);
  const setChatBgTint = useUiStore((s) => s.setChatBgTint);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The inline sentence tells the user what to DO; the store is where the
  // actual failure goes. Before, neither surface saw the real one.
  const pushError = useErrorStore((s) => s.pushError);

  const handleFile = async (file: File | undefined) => {
    if (!file || busy) return;
    setError(null);
    setBusy(true);
    // Two steps, two failures, two different things to do about them. One
    // `try` covered both and blamed the FILE unconditionally (KÖK 14), so a
    // full browser profile told the user their PNG was unreadable and sent
    // them round an endless loop of re-exporting a perfectly good image -
    // while the real error reached nothing at all.
    let processed: { blob: Blob; lum: number };
    try {
      processed = await processChatBgImage(file);
    } catch (err) {
      setError("Could not read that image. Try a PNG, JPEG, or WebP.");
      setBusy(false);
      pushError(err);
      return;
    }
    try {
      await putChatBgBlob(processed.blob);
      setChatBgMeta({ lum: processed.lum });
    } catch (err) {
      setError(
        "The image could not be saved - this browser profile is out of space.",
      );
      pushError(err);
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    clearChatBg();
    try {
      await deleteChatBgBlob();
    } catch {
      // The flag is off either way; a stale blob is unreachable.
    }
  };

  return (
    <div className="space-y-4">
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_IMAGE_ACCEPT}
        className="hidden"
        aria-label="Choose a background image file"
        onChange={(event) => {
          void handleFile(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          className="sidebar-dialog-action text-xs"
          disabled={busy}
          onClick={() => fileInputRef.current?.click()}
        >
          {busy
            ? "Processing…"
            : chatBgOn
              ? "Change image"
              : "Choose image"}
        </Button>
        {chatBgOn && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="sidebar-dialog-cancel text-xs"
            disabled={busy}
            onClick={() => void handleRemove()}
          >
            Remove
          </Button>
        )}
      </div>
      {error && (
        <p className="settings-error">
          {error}
        </p>
      )}

      {/* Dim-only class: generation-control's padding/border must not appear
          and vanish with the toggle, or the page reflows on image add/remove. */}
      <div className={chatBgOn ? "" : "settings-section-disabled"}>
        <SliderRow
          label="Contrast"
          value={chatBgContrast}
          min={CHAT_BG_CONTRAST_MIN}
          max={CHAT_BG_CONTRAST_MAX}
          step={0.05}
          display={`${Math.round(chatBgContrast * 100)}%`}
          helper="A tint layer over the picture - higher calms the photo."
          onChange={setChatBgContrast}
          disabled={!chatBgOn}
        />

        <div className="generation-control mt-3">
          <label className="settings-label">Tint</label>
          <div
            className="mt-2 flex flex-wrap items-center gap-2"
            role="radiogroup"
            aria-label="Background tint"
          >
            {CHAT_BG_TINTS.map((swatch) => (
              <button
                key={swatch.id}
                type="button"
                role="radio"
                aria-checked={chatBgTint === swatch.id}
                aria-label={`${swatch.label} tint`}
                title={swatch.label}
                className="settings-tint-chip"
                data-selected={chatBgTint === swatch.id ? "true" : "false"}
                style={
                  swatch.id === "auto"
                    ? {
                        background:
                          "linear-gradient(135deg, #EDF3FA 49%, #161a1d 51%)",
                      }
                    : { background: swatch.id }
                }
                disabled={!chatBgOn}
                onClick={() => setChatBgTint(swatch.id)}
              />
            ))}
          </div>
          <p className="generation-helper">
            Auto follows the picture's brightness. Text over the canvas
            adapts on its own.
          </p>
        </div>
      </div>

      {!chatBgOn && (
        <p className="generation-support-note">
          Contrast and tint have no effect without an image.
        </p>
      )}
    </div>
  );
}
