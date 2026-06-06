import { describe, it, expect, vi, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { mockFetch } from "@/test/mocks/api";
import {
  settingsFixture,
  proxyHealthFixture,
  characterFixture,
} from "@/test/mocks/fixtures";
import { Providers } from "@/app/providers";
import { App } from "@/app/App";

describe("Composer safety tests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-23: App shell does not call /complete on its own
  it("T-23: no /complete call is made on load", async () => {
    const fetchMock = mockFetch({
      "/settings/proxy/health": { body: proxyHealthFixture },
      "/settings": { body: settingsFixture },
      "/characters": { body: [characterFixture] },
    });

    render(
      <Providers>
        <App />
      </Providers>,
    );

    // Wait for initial renders to settle
    await new Promise((r) => setTimeout(r, 500));

    // Assert no fetch was called with a URL containing "/complete"
    const completeCalls = fetchMock.mock.calls.filter(
      (call) =>
        typeof call[0] === "string" && call[0].includes("/complete"),
    );
    expect(completeCalls).toHaveLength(0);
  });
});
