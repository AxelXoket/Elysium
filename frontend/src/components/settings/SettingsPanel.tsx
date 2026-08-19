import { ApiKeySection } from "./ApiKeySection";
import { ProxySection } from "./ProxySection";
import { VaultSection } from "./VaultSection";
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

        {/* Privacy note - secrets are sealed inside the encrypted vault (E5) */}
        <div
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
            Secrets are sealed inside your encrypted vault - locked with your
            passphrase, together with everything else. Nothing is stored in
            the browser, and nothing leaves this machine.
          </span>
        </div>

        <ApiKeySection />
        <Separator className="opacity-15" />
        <ProxySection />
        <Separator className="opacity-15" />
        <VaultSection />
        <Separator className="opacity-15" />
        <ScreenPrivacySection />
      </div>
    </SlideIn>
  );
}
