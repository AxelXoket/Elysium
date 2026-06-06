import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Providers } from "@/app/providers";
import { App } from "@/app/App";

describe("T-01: App shell renders without crash", () => {
  it("renders the Elysium heading", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByText("Elysium")).toBeInTheDocument();
  });

  it("renders the sidebar with Characters section", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByText("Characters")).toBeInTheDocument();
  });

  // Updated from "Settings" → "Secrets" (Phase 6E-A tab rename)
  it("renders the right panel with Secrets tab", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /secrets/i })).toBeInTheDocument();
  });

  it("renders the composer", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-72: Right panel renders Models tab
  it("T-72: renders Models tab", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /models/i })).toBeInTheDocument();
  });

  // T-73: Right panel renders Secrets tab
  it("T-73: renders Secrets tab", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /secrets/i })).toBeInTheDocument();
  });

  // T-74: Right panel renders Persona tab
  it("T-74: renders Persona tab", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByRole("tab", { name: /persona/i })).toBeInTheDocument();
  });

  // T-75: Sidebar footer shows v0.1
  it("T-75: sidebar footer shows v0.1", () => {
    render(
      <Providers>
        <App />
      </Providers>,
    );
    expect(screen.getByText(/v0\.1/i)).toBeInTheDocument();
  });
});
