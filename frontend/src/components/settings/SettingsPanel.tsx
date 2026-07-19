import { ApiKeySection } from "./ApiKeySection";
import { ProxySection } from "./ProxySection";
import { VaultSection } from "./VaultSection";
import { Separator } from "@/components/ui/separator";
import { SlideIn } from "@/components/motion/SlideIn";
import { ShieldCheck } from "lucide-react";

export function SettingsPanel() {
  return (
    <SlideIn>
      <div className="space-y-5 p-4">
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
      </div>
    </SlideIn>
  );
}
