import { ApiKeySection } from "./ApiKeySection";
import { ProxySection } from "./ProxySection";
import { Separator } from "@/components/ui/separator";
import { SlideIn } from "@/components/motion/SlideIn";
import { ShieldCheck } from "lucide-react";

export function SettingsPanel() {
  return (
    <SlideIn>
      <div className="space-y-5 p-4">
        {/* Privacy note — informs the user about keyring storage */}
        <div
          className="flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs leading-relaxed"
          style={{
            backgroundColor: "rgba(167, 200, 161, 0.07)",
            color: "var(--color-es-text-muted)",
            border: "1px solid rgba(167, 200, 161, 0.14)",
          }}
        >
          <ShieldCheck
            size={13}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--color-es-primary-sage)" }}
          />
          <span>
            Secrets are stored in the OS keyring, never in the browser.
          </span>
        </div>

        <ApiKeySection />
        <Separator className="opacity-15" />
        <ProxySection />
      </div>
    </SlideIn>
  );
}
