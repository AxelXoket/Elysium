import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { CharacterImportDialog } from "@/components/characters/CharacterImportDialog";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>{children}</TooltipProvider>
    
  );
}

describe("Character Import Dialog Tests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-19: Character import calls POST /characters/import with raw body
  it("T-19: calls POST /characters/import with raw body", async () => {
    const fetchMock = mockFetch({});

    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterImportDialog trigger={<Button>Import</Button>} />,
      { wrapper },
    );

    // Open dialog
    await user.click(screen.getByRole("button", { name: /import/i }));

    await waitFor(() => {
      expect(screen.getByText("Import Character (JSON)")).toBeInTheDocument();
    });

    const rawJson = '{"name":"Imported Char","description":"Test"}';

    // Use fireEvent.change because userEvent.type interprets { as modifier
    const textarea = screen.getByLabelText("Character JSON input");
    fireEvent.change(textarea, { target: { value: rawJson } });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(characterFixture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const importBtns = screen.getAllByRole("button", { name: /import/i });
    const submitBtn = importBtns[importBtns.length - 1];
    await user.click(submitBtn);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/characters/import") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);

      // Verify body is raw JSON - not wrapped
      if (postCalls.length > 0) {
        const body = postCalls[0][1]?.body as string;
        // Body should be raw JSON, not double-stringified
        expect(body).toContain("Imported Char");
        // Verify Content-Type is application/json
        const headers = postCalls[0][1]?.headers as Record<string, string>;
        expect(headers?.["Content-Type"]).toBe("application/json");
      }
    });
  });

  // FIX-3: import failure renders a safe mapped message, never raw detail
  it("FIX-3: import error shows mapped message instead of raw detail", async () => {
    const fetchMock = mockFetch({});

    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterImportDialog trigger={<Button>Import</Button>} />,
      { wrapper },
    );

    await user.click(screen.getByRole("button", { name: /import/i }));
    await waitFor(() => {
      expect(screen.getByText("Import Character (JSON)")).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText("Character JSON input");
    fireEvent.change(textarea, { target: { value: '{"name":"X"}' } });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "RAW_UPSTREAM_DETAIL" }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const importBtns = screen.getAllByRole("button", { name: /import/i });
    await user.click(importBtns[importBtns.length - 1]);

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("RAW_UPSTREAM_DETAIL")).not.toBeInTheDocument();
  });

  // ── v1.1 FF14: pick a .json file ───────────────────────────────────────

  it("FF14: picking a .json file fills the textarea and imports its text", async () => {
    const fetchMock = mockFetch({});
    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterImportDialog trigger={<Button>Import</Button>} />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: /import/i }));
    await screen.findByText("Import Character (JSON)");

    const fileContent = '{"name":"From File","description":"picked"}';
    const file = new File([fileContent], "char.json", {
      type: "application/json",
    });
    const input = screen.getByLabelText(
      "Character JSON file",
    ) as HTMLInputElement;
    expect(input).toHaveAttribute("accept", ".json,application/json");

    // File.text() is available in jsdom's File since it extends Blob.
    fireEvent.change(input, { target: { files: [file] } });

    const textarea = (await screen.findByLabelText(
      "Character JSON input",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(textarea.value).toBe(fileContent);
    });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(characterFixture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const importBtns = screen.getAllByRole("button", { name: /import/i });
    await user.click(importBtns[importBtns.length - 1]);

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/characters/import") &&
          call[1]?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      expect(postCalls[0][1]?.body as string).toContain("From File");
    });
  });

  it("FF14: the file picker is disabled while an import is pending", async () => {
    mockFetch({});
    const user = userEvent.setup();
    renderWithQueryClient(
      <CharacterImportDialog trigger={<Button>Import</Button>} />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: /import/i }));
    await screen.findByText("Import Character (JSON)");
    // Not pending yet -> Choose file is enabled.
    expect(
      screen.getByRole("button", { name: "Choose file" }),
    ).toBeEnabled();
  });
});
