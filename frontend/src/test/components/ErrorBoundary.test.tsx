/**
 * ErrorBoundary - the packaged app's last-resort crash surface.
 *
 * A render error anywhere must swap to the branded fallback instead of
 * unmounting React into a white window.
 *
 * The card used to carry a Reload button, and these tests pinned it. It is
 * gone: the window is opened at `#elysium-token=<secret>`, launchToken.ts
 * strips that fragment as soon as it has read it, and a reload therefore
 * returns to an address with no token in it - after which main.py's
 * `launch_token_gate` answers 403 to every /api/v1 call and the window is
 * alive but unable to do anything. The tests below pin the absence, because
 * the absence is the fix.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ErrorBoundary } from "@/components/errors/ErrorBoundary";

function Bomb(): never {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    // React reports caught render errors via console.error - expected noise
    // for these tests, not a failure.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>healthy subtree</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy subtree")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("swaps a throwing subtree for the branded fallback card", () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    const card = screen.getByRole("alert");
    expect(card).toHaveTextContent("Something went wrong");
    expect(card).toHaveTextContent("Elysium");
  });

  it("tells the reader the one recovery the packaged app actually has", () => {
    // Same words as errorMessages.ts uses for the codes with no in-window
    // fix, so the crash card and a toast do not describe one act two ways.
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    const card = screen.getByRole("alert");
    expect(card).toHaveTextContent(/close Elysium and start it again/i);
    // And it does not promise the thing the window cannot do.
    expect(card.textContent ?? "").not.toMatch(/\breload\b/i);
  });

  it("offers no control, because there is nothing the window can do", () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    const card = screen.getByRole("alert");
    expect(within(card).queryAllByRole("button")).toEqual([]);
    expect(within(card).queryAllByRole("link")).toEqual([]);
  });

  it("POSITIVE CONTROL: that query does see a control when one is there", () => {
    // Otherwise a card rendered empty, or a broken query, would report "no
    // Reload button" for the rest of the app's life.
    render(
      <div role="alert">
        <button type="button">Reload</button>
      </div>,
    );
    expect(
      within(screen.getByRole("alert")).queryAllByRole("button"),
    ).toHaveLength(1);
  });

  it("nothing the card renders reloads the window", async () => {
    // The reload is what strands the app: the token was stripped from the
    // address at boot, so the reloaded document cannot present it and the
    // backend gate 403s every request afterwards.
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, reload },
    });
    try {
      render(
        <ErrorBoundary>
          <Bomb />
        </ErrorBoundary>,
      );
      const card = screen.getByRole("alert");
      for (const el of within(card).queryAllByRole("button")) {
        await userEvent.click(el);
      }
      expect(reload).not.toHaveBeenCalled();

      // POSITIVE CONTROL: the same spy, the same click loop, on a card that
      // does still offer one. Without this the assertion above passes on a
      // stub that was never wired to window.location at all.
      render(
        <div role="alert" data-testid="old-card">
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>,
      );
      const old = screen.getByTestId("old-card");
      for (const el of within(old).queryAllByRole("button")) {
        await userEvent.click(el);
      }
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: original,
      });
    }
  });
});
