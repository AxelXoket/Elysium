import { Sidebar } from "./Sidebar";
import { RightPanel } from "./RightPanel";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { MistCanvas } from "@/components/backdrop/MistCanvas";
import { FadeIn } from "@/components/motion/FadeIn";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useStaleSelectionReconciliation } from "@/app/useStaleSelectionReconciliation";

/**
 * AppShell - Phase 6E-A seasonal canvas layout.
 *
 * .elysium-shell applies a CSS-only seasonal gradient background
 * with a faint warm radial haze via ::before pseudo-element.
 * No remote images or assets.
 */
export function AppShell() {
  // Clear persisted selections that no longer exist on the server
  useStaleSelectionReconciliation();

  return (
    <FadeIn duration={0.3}>
      <div className="elysium-shell elysium-page">
        {/* Living mist backdrop: z:-1 slots between the shell's static
            gradient and its ::before haze - no existing layer moves. */}
        <MistCanvas />
        <div className="elysium-stage">
          <GenerationSettingsProvider>
            <div className="elysium-frame">
              <Sidebar />
              <ChatCanvas />
              <RightPanel />
            </div>
          </GenerationSettingsProvider>
        </div>
      </div>
    </FadeIn>
  );
}
