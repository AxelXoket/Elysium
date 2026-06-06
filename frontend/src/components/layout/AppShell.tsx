import { Sidebar } from "./Sidebar";
import { RightPanel } from "./RightPanel";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { FadeIn } from "@/components/motion/FadeIn";

/**
 * AppShell — Phase 6E-A seasonal canvas layout.
 *
 * .elysium-shell applies a CSS-only seasonal gradient background
 * with a faint warm radial haze via ::before pseudo-element.
 * No remote images or assets.
 */
export function AppShell() {
  return (
    <FadeIn duration={0.3}>
      <div className="elysium-shell elysium-page">
        <div className="elysium-stage">
          <div className="elysium-frame">
            <Sidebar />
            <ChatCanvas />
            <RightPanel />
          </div>
        </div>
      </div>
    </FadeIn>
  );
}
