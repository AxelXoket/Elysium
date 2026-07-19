/**
 * ErrorBoundary - the packaged app's last-resort crash surface.
 *
 * A render error anywhere must swap to the branded fallback (title + Reload)
 * instead of unmounting React into a white window.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
    expect(
      screen.getByRole("button", { name: "Reload" }),
    ).toBeInTheDocument();
  });

  it("Reload button calls window.location.reload", async () => {
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
      await userEvent.click(screen.getByRole("button", { name: "Reload" }));
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: original,
      });
    }
  });
});
