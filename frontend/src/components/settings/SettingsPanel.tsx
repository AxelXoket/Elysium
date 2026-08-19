import { ApiKeySection } from "./ApiKeySection";
import { ProxySection } from "./ProxySection";
import { VaultSection } from "./VaultSection";
import { PremigrateBackupNotice } from "./PremigrateBackupNotice";
import { ScreenPrivacySection } from "./ScreenPrivacySection";
import { Separator } from "@/components/ui/separator";
import { SlideIn } from "@/components/motion/SlideIn";
import { ShieldCheck } from "lucide-react";

export function SettingsPanel() {
  return (
    <SlideIn>
      <div className="space-y-5 p-4">
        {/* A57. The tab reads "Security" because four tabs have to fit at a
            300px panel; the panel itself can afford the whole name, and it
            needed one - this panel had no heading at all. The stored tab
            value stays "secrets": renaming it would cost a persist version
            bump and buy nothing. */}
        <h3 className="text-sm font-semibold">{"Secrets & Security"}</h3>

        {/* Privacy note (E5).

            READ THIS BEFORE SHORTENING THE SENTENCE BELOW. It used to say
            "Nothing is stored in the browser, and nothing leaves this
            machine." Both halves were false, and both are load-bearing
            because this note sits directly above the API key and proxy
            sections, where a reader decides whether to trust the app with a
            secret.

            1. The browser DOES store things. lib/store/uiStore.ts persists to
               localStorage under "elysium-ui-state", and its `partialize`
               includes selectedCharacterId, selectedChatId and
               selectedModelId. Which character and which chat were last open
               therefore sit in plaintext outside the vault and survive a
               lock. That is a deliberate trade the owner made - reopening
               where you left off is worth more than the change - so the note
               names what is kept instead of pretending nothing is. The claim
               that survives is the narrow one that is actually true: no key
               and no message TEXT is in browser storage.

            2. Things DO leave this machine. Sending the conversation to the
               provider is the entire function of the app, and the API key
               goes with it as an Authorization header. The honest promise is
               not "nothing leaves" but "one destination, the one you chose",
               and the proxy clause is there because ProxySection below can
               add a hop the reader should not have to discover.

            So: do not "simplify" this back into a clean absolute. The clean
            version was a lie, and it was a lie on the one screen where being
            caught in one costs the most. */}
        <div
          role="note"
          aria-label="Privacy note"
          className="flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs leading-relaxed"
          style={{
            backgroundColor: "rgba(62, 114, 176, 0.07)",
            color: "var(--color-es-text-muted)",
            border: "1px solid rgba(62, 114, 176, 0.14)",
          }}
        >
          <ShieldCheck
            size={13}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--color-es-primary-sage)" }}
          />
          <span>
            Your API key and your messages stay in the encrypted vault, locked
            with your passphrase. The browser holds only display preferences
            and which character, chat and model were last open, so the app can
            reopen where you left off; no key and no message text is kept
            there. What you send goes to the one provider you chose, through
            your proxy if you set one, and nowhere else.
          </span>
        </div>

        <ApiKeySection />
        <Separator className="opacity-15" />
        <ProxySection />
        <Separator className="opacity-15" />
        {/* Its three siblings (Plaintext/Orphaned/Rotation) live INSIDE
            VaultSection, above the passphrase form. This one is wired here
            instead - not by choice of layout but of ownership: VaultSection.tsx
            belongs to another concurrent pass and is off limits to this one, so
            the notice is a sibling of the section it is about rather than a
            child of it. It still reads first, before the vault does anything
            else on this screen, for the same reason its siblings sit above the
            form: a stale full copy of the vault outranks the form that follows. */}
        <PremigrateBackupNotice />
        <VaultSection />
        <Separator className="opacity-15" />
        <ScreenPrivacySection />
      </div>
    </SlideIn>
  );
}
