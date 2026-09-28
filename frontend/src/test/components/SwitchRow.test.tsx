/**
 * SwitchRow - the four switch rows are one component, named by their words.
 *
 * T-77 (IP-03 FIX 7). The same row was written four times in two shapes: a
 * `label` wrapper whose words named the switch, and a `div` wrapper with an
 * `aria-label` that said something other than the words beside it. This
 * holds every call site to the first shape: the switch sits inside a label,
 * the label's visible text is the switch's accessible name, and no
 * `aria-label` overrides those words. It renders the real panels, not the
 * component in isolation, because the claim is about the call sites.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "../mocks/api";
import { settingsFixture, proxyHealthFixture } from "../mocks/fixtures";
import { useUiStore } from "@/lib/store/uiStore";
import { SwitchRow } from "@/components/settings/SwitchRow";
import { TYPE_SCALE_STEPS } from "@/lib/theme/tokens";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { ProxySection } from "@/components/settings/ProxySection";
import { ScreenPrivacySection } from "@/components/settings/ScreenPrivacySection";
import { TooltipProvider } from "@/components/ui/tooltip";

/** The four call sites and the words that name each switch. */
const CALL_SITES: ReadonlyArray<{
  where: string;
  name: string;
  mount: () => void;
}> = [
  {
    where: "notebook/BoundaryPanel",
    name: "Use my global limits here",
    mount: () => {
      mockFetch({
        "/notebook/7/boundaries": { body: { boundaries: [], use_global: true } },
      });
      renderWithQueryClient(<BoundaryPanel />);
    },
  },
  {
    where: "notebook/WorkerPanel",
    name: "Keep suggestions without asking",
    mount: () => {
      mockFetch({
        "/notebook/worker/reset": { body: { ok: true } },
        "/notebook/worker": {
          body: {
            stats: { done: 0, failed: 0, skipped: 0, skip_reasons: {} },
            spend: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
            spend_lifetime: { calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 },
            worker: {
              state: "closed", failures: 0, total_failures: 0, queued: 0,
              dropped_offers: 0, runs: 0, batch_size: 20,
            },
            daily_cap: 60,
          },
        },
        "/notebook/auto-accept": {
          body: { enabled: true, effective: true, overridden: false },
        },
      });
      renderWithQueryClient(<WorkerPanel />);
    },
  },
  {
    where: "settings/ProxySection",
    name: "Require proxy",
    mount: () => {
      mockFetch({
        "/settings/proxy/health": { body: proxyHealthFixture },
        "/settings": { body: { ...settingsFixture, proxy_configured: true } },
      });
      const wrapper = ({ children }: { children: ReactNode }) => (
        <TooltipProvider>{children}</TooltipProvider>
      );
      renderWithQueryClient(<ProxySection />, { wrapper });
    },
  },
  {
    where: "settings/ScreenPrivacySection",
    name: "Hide this window from screen capture",
    mount: () => {
      mockFetch({
        "/settings": { body: { ...settingsFixture, screen_privacy_enabled: false } },
      });
      renderWithQueryClient(<ScreenPrivacySection />);
    },
  },
];

describe("SwitchRow at its four call sites (T-77)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 7 });
  });
  afterEach(() => vi.restoreAllMocks());

  it.each(CALL_SITES)("$where: the switch is named by the words on screen, inside a label", async ({ name, mount }) => {
    mount();
    // GROUND: the words really are on screen as text. Without this the name
    // check below could be satisfied by an aria-label alone.
    expect(await screen.findByText(name)).toBeInTheDocument();

    const sw = await screen.findByRole("switch", { name });
    const label = sw.closest("label");
    expect(label, "the switch is not wrapped in a label").not.toBeNull();
    expect(label!.textContent).toContain(name);
    // The name comes from the label and from nothing else: an aria-label
    // beside visible words overrides them, which is the defect this ends.
    expect(sw).not.toHaveAttribute("aria-label");
  });

  it("clicking the words flips the switch, because the wrapper is a label", async () => {
    // The behavioural difference between the two old shapes: on the div
    // wrapper, clicking "Require proxy" did nothing.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SwitchRow checked={false} onCheckedChange={onChange}>
        Flip me
      </SwitchRow>,
    );
    await user.click(screen.getByText("Flip me"));
    // Base UI passes the event details as a second argument; the value is
    // the claim.
    await waitFor(() => expect(onChange.mock.calls[0]?.[0]).toBe(true));
  });

  it("passes disabled through, so a row that cannot be changed cannot be clicked", () => {
    // Three of the four call sites pass `disabled` with a written reason -
    // a proxy toggle that must not move while the setting is in flight, a
    // capture switch that must not move while the platform call is out. The
    // prop was forwarded and nothing held it there, so deleting one line
    // would have made all three clickable with the suite still green.
    const onCheckedChange = vi.fn();
    render(
      <SwitchRow checked={false} disabled onCheckedChange={onCheckedChange}>
        Hold still
      </SwitchRow>,
    );
    const control = screen.getByRole("switch", { name: "Hold still" });
    // Base UI reports the state in the accessibility tree rather than with
    // the HTML attribute, so that is what is read: the tree is what a
    // screen reader and a test both actually see.
    expect(
      control.getAttribute("aria-disabled") === "true" ||
        control.hasAttribute("data-disabled"),
      "the row does not report itself as disabled",
    ).toBe(true);
    fireEvent.click(screen.getByText("Hold still"));
    expect(onCheckedChange, "a disabled row still answered a click")
      .not.toHaveBeenCalled();
  });

  it("GROUND: the same row without disabled does answer a click", () => {
    const onCheckedChange = vi.fn();
    render(
      <SwitchRow checked={false} onCheckedChange={onCheckedChange}>
        Hold still
      </SwitchRow>,
    );
    fireEvent.click(screen.getByText("Hold still"));
    expect(onCheckedChange).toHaveBeenCalled();
  });

  it("puts its words on the chrome step, because they name a control", () => {
    // The role rule is above the nearest-step rule: text that names a
    // control takes 13, not the 12 a quiet label would get. The step name
    // comes from the scale rather than being typed here.
    render(
      <SwitchRow checked={false} onCheckedChange={vi.fn()}>
        Words that name a switch
      </SwitchRow>,
    );
    expect([...screen.getByText("Words that name a switch").classList])
      .toContain(`text-${TYPE_SCALE_STEPS[2]}`);
  });

  it("takes an aria-label only as the name of a row that shows no words", () => {
    // The prop exists for an icon-only row and has no default. POSITIVE
    // CONTROL for the absence assertion above: when it IS passed, it is
    // what the switch is called, so the attribute really is read.
    render(
      <SwitchRow checked={false} onCheckedChange={() => {}} aria-label="Only icon">
        <span aria-hidden="true">*</span>
      </SwitchRow>,
    );
    expect(screen.getByRole("switch", { name: "Only icon" })).toBeInTheDocument();
  });
});
