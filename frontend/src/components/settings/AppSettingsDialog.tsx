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
import { useEffect, useRef, useState, type ReactNode } from "react";
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
import {
  ArrowLeft,
  ChevronRight,
  Image as ImageIcon,
  KeyRound,
  Settings,
  Sparkles,
  Type,
} from "lucide-react";

type SettingsPage = "root" | "text" | "narration" | "background";

const PAGE_TITLES: Record<SettingsPage, string> = {
  root: "Settings",
  text: "Text & readability",
  narration: "Narration style",
  background: "Chat background",
};

const PAGE_DESCRIPTIONS: Record<SettingsPage, string> = {
  root: "Appearance and reading preferences. Stored on this device only.",
  text: "Message body size and spacing. Labels and controls stay fixed.",
  narration: "How *asterisk* narration reads inside messages.",
  background: "A picture behind the conversation, tuned for readability.",
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
        <span className="block text-xs font-semibold">{title}</span>
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
      description="Drifting fog behind the app · light GPU use"
      checked={ambientFogOn}
      onToggle={() => setAmbientFogOn(!ambientFogOn)}
    />
  );
}

// ── Text & readability ─────────────────────────────────────────────

function TextSettingsPage() {
  const msgFontPx = useUiStore((s) => s.msgFontPx);
  const msgLineHeight = useUiStore((s) => s.msgLineHeight);
  const setMsgFontPx = useUiStore((s) => s.setMsgFontPx);
  const setMsgLineHeight = useUiStore((s) => s.setMsgLineHeight);
  const isDefault =
    msgFontPx === MSG_FONT_DEFAULT && msgLineHeight === MSG_LINE_DEFAULT;

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

      <div className="settings-preview" aria-hidden="true">
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
          }}
        >
          Reset to defaults
        </Button>
      </div>
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
        <label className="text-xs font-semibold">{label}</label>
        <span
          className="text-[11px]"
          style={{ color: "rgba(202, 212, 224, 0.72)" }}
        >
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
        description={'Color "spoken lines" with the theme amber'}
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
        <span className="block text-xs font-semibold">{title}</span>
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

  const handleFile = async (file: File | undefined) => {
    if (!file || busy) return;
    setError(null);
    setBusy(true);
    try {
      const { blob, lum } = await processChatBgImage(file);
      await putChatBgBlob(blob);
      setChatBgMeta({ lum });
    } catch {
      setError("Could not read that image. Try a PNG, JPEG, or WebP.");
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
        <p className="text-[11px]" style={{ color: "var(--color-es-danger)" }}>
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
          <label className="text-xs font-semibold">Tint</label>
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
