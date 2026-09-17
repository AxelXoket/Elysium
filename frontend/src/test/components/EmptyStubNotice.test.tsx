/**
 * The leftover recovery file has to reach a screen, or it is not reported.
 *
 * /vault/status has carried orphaned_copy since it was added, and for a while
 * nothing rendered it - a field nobody paints is the same as a log line nobody
 * opens. empty_stub is the newest of the three and this is the test that keeps
 * it from repeating that.
 */
import { afterAll, beforeAll, describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { EmptyStubNotice } from "@/components/settings/EmptyStubNotice";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { delayRoute } from "@/test/helpers/delayRoute";
import { mockFetchUnreachable } from "@/test/helpers/fetchUnreachable";
import { expectNothingOnceAnswered } from "@/test/helpers/vaultStatusSettled";
import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
} from "@/test/helpers/glassSurfaceCss";


const BASE = { initialized: true, unlocked: true };

describe("EmptyStubNotice", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("says nothing when there is no leftover file", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: false } } });
    // Asserted only once the status query has ANSWERED: an absence checked
    // on the first render passes before the answer exists (IP-11b).
    await expectNothingOnceAnswered(renderWithQueryClient(<EmptyStubNotice />));
  });

  it("names the file when one is there", async () => {
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: true } } });
    renderWithQueryClient(<EmptyStubNotice />);

    expect(await screen.findByTestId("empty-stub-notice")).toBeInTheDocument();
    // Naming it is the point: the user has to be able to match what the app
    // says against what they see in the folder.
    expect(screen.getByText("app.db.empty-stub-bak")).toBeInTheDocument();
  });

  it("removes it without asking twice", async () => {
    // No confirmation step, unlike the other two notices in this folder. They
    // guard copies of the user's data; this file is provably empty.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": { body: { removed: true, reason: "" } },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    await waitFor(() => {
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          (c: unknown[]) => String(c[0]).includes("/vault/discard-empty-stub"),
        ),
      ).toBe(true);
    });
  });

  it("reports a refusal instead of pretending it worked", async () => {
    // The backend re-measures the file and refuses one that is not empty. A
    // screen that swallowed that would leave the user believing a file is gone
    // while it is still there, which is the failure mode every notice in this
    // folder exists to prevent.
    const user = userEvent.setup();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": {
        body: { removed: false, reason: "not_empty" },
      },
    });
    renderWithQueryClient(<EmptyStubNotice />);

    await user.click(await screen.findByRole("button", { name: /remove it/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not empty/i);
  });
});

/**
 * This notice renders inside `.glass-right`, the app's one light surface,
 * but `settings-hint` and `settings-error` are painted for the dark settings
 * dialog. See helpers/glassSurfaceCss.ts for why these tests read
 * custom-property tokens rather than `.color`.
 */
describe("EmptyStubNotice - light surface contrast", () => {
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    styleEl = injectCss(await loadGlassRightCss());
  }, 20000);

  afterAll(() => styleEl.remove());

  afterEach(() => {
    document.querySelectorAll(".glass-right").forEach((el) => el.remove());
  });

  it("the housekeeping hint reads .glass-right's own muted token", async () => {
    const glassRight = wrapInGlassRight();
    mockFetch({ "/vault/status": { body: { ...BASE, empty_stub: true } } });
    renderWithQueryClient(<EmptyStubNotice />, {
      container: glassRight,
      baseElement: document.body,
    });

    const hint = await screen.findByTestId("empty-stub-hint");
    const mutedText = surfaceToken(glassRight, "--muted-foreground");
    expect(mutedText, "the surface never resolved a muted token").not.toBe("");
    expect(surfaceToken(hint, "--muted-foreground")).toBe(mutedText);
    expect(hint.className).not.toMatch(/settings-/);

    // Negative control: settings-hint's colour is a hardcoded rgba, not a
    // variable, so jsdom resolves `.color` for it directly.
    const oldHint = document.createElement("p");
    oldHint.className = "settings-hint";
    glassRight.appendChild(oldHint);
    expect(
      getComputedStyle(oldHint).color,
      "settings-hint's colour drifted - re-measure before trusting this ground",
    ).toBe("rgba(202, 212, 224, 0.72)");
  });

  it("a refusal alert uses persona-local-error, not settings-error", async () => {
    const user = userEvent.setup();
    const glassRight = wrapInGlassRight();
    mockFetch({
      "/vault/status": { body: { ...BASE, empty_stub: true } },
      "/vault/discard-empty-stub": { body: { removed: false, reason: "not_empty" } },
    });
    renderWithQueryClient(<EmptyStubNotice />, {
      container: glassRight,
      baseElement: document.body,
    });

    await user.click(await screen.findByRole("button", { name: /remove it/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.className).not.toMatch(/settings-error/);
    expect(alert.className).toMatch(/persona-local-error/);
    // settings-error and persona-local-error both read the same :root-only
    // --color-es-danger token, which jsdom cannot resolve either way - see
    // AutoLockControl's contrast tests for the same note. Font-size is the
    // real, measured difference between the two hand-written rules.
    expect(getComputedStyle(alert).fontSize).toBe("12px");
  });


  describe("when the deletion fails", () => {
    // Four irreversible deletions, and none of them had an `onError`. No
    // MutationCache either, and the `role="alert"` paragraphs in these
    // notices report fields of a SUCCESSFUL response - so a 500 or a 423
    // said nothing at all. "The file is gone" and "nothing was even
    // attempted" looked identical to the one person who cannot check.
    //
    // COUNTED across both lists, and after a reset. The store dedups by
    // identity, so a leftover event from an earlier test would satisfy
    // "there is an error" without this change doing anything.
    function errorCount(): number {
      const s = useErrorStore.getState();
      return s.errors.length + s.queuedErrors.length;
    }

    beforeEach(() => useErrorStore.getState().clearAll());

    it("tells the user when the request is refused", async () => {
      mockFetch({
        "/vault/status": { body: { ...BASE, empty_stub: true } },
        "/vault/discard-empty-stub": { status: 500, body: { detail: "boom" } },
      });
      renderWithQueryClient(<EmptyStubNotice />);
      await userEvent.click(
        await screen.findByRole("button", { name: /remove it/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("tells the user when the vault is locked", async () => {
      // POSITIVE CONTROL. 423 is the likeliest of these in practice - the
      // vault locks on idle while the notice is still on screen - and it
      // travels a different path through the client than a 500.
      mockFetch({
        "/vault/status": { body: { ...BASE, empty_stub: true } },
        "/vault/discard-empty-stub": { status: 423, body: { detail: "vault_locked" } },
      });
      renderWithQueryClient(<EmptyStubNotice />);
      await userEvent.click(
        await screen.findByRole("button", { name: /remove it/i }));
      await waitFor(() => expect(errorCount()).toBe(1));
    });

    it("says nothing when the deletion succeeds", async () => {
      // GROUND CONTROL. Without it a handler that reports on every outcome
      // passes both tests above and puts an error on screen for work that
      // went fine.
      mockFetch({
        "/vault/status": { body: { ...BASE, empty_stub: true } },
        "/vault/discard-empty-stub": { body: { removed: true, reason: "", left: [] } },
      });
      renderWithQueryClient(<EmptyStubNotice />);
      await userEvent.click(
        await screen.findByRole("button", { name: /remove it/i }));
      await waitFor(() => expect(
        vi.mocked(globalThis.fetch).mock.calls.some(([url]) =>
          String(url).includes("/vault/discard-empty-stub"))).toBe(true));
      expect(errorCount()).toBe(0);
    });
  });
});

/**
 * IP-11b. The gate on this notice used to be `status.data?.empty_stub`, so a
 * status query that FAILED rendered the same nothing as a clean disk, and no
 * console call exists in this app to leave a trace of it. These tests drive
 * /vault/status itself, at the fetch boundary, into every state the real
 * useVaultStatus hook can be in, and read what the DOM shows for each.
 */
describe("EmptyStubNotice - when the check itself cannot run", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  const NOTICE = "empty-stub-notice";
  const CHECK = "empty-stub-check";
  const PRESENT = { ...BASE, empty_stub: true };
  const ABSENT = { ...BASE, empty_stub: false };
  /** An older backend that does not know the field at all. */
  const OLDER = { ...BASE };
  /** The warning's own sentences. None of them may leak into the check line. */
  const WARNING_WORDING = /nothing uses it|remove it|empty file it replaced/i;

  it("T-57: a failed status query is said out loud, and does not read as all clear", async () => {
    mockFetch({ "/vault/status": { status: 500, body: { detail: "boom" } } });
    const { container } = renderWithQueryClient(<EmptyStubNotice />);

    const line = await screen.findByText(/could not check for a leftover recovery file/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    // Not silence: the whole defect was that this container stayed empty.
    expect(container).not.toBeEmptyDOMElement();
    // Not the warning either: none of its furniture and none of its wording.
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(WARNING_WORDING);
  });

  it("T-57: the same line when the backend cannot be reached at all", async () => {
    // fetch itself rejects before any status code exists. request() turns
    // that into NETWORK_ERROR, a different path through the client than a
    // 500 takes and the likelier one in practice.
    mockFetchUnreachable();
    renderWithQueryClient(<EmptyStubNotice />);

    // findByText, not findByTestId: the pending line carries the same
    // testid and is on screen before the rejection lands.
    const line = await screen.findByText(/could not check for a leftover recovery file/i);
    expect(line).toBe(screen.getByTestId(CHECK));
    expect(line).toHaveAttribute("data-state", "error");
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
  });

  it("holds a quiet placeholder until the answer lands, then shows the truth", async () => {
    const fetchMock = mockFetch({ "/vault/status": { body: PRESENT } });
    const release = delayRoute(fetchMock, "GET", "/vault/status", { body: PRESENT });
    const { container } = renderWithQueryClient(<EmptyStubNotice />);

    const line = screen.getByTestId(CHECK);
    expect(line).toHaveAttribute("data-state", "pending");
    expect(line.textContent).toMatch(/checking for a leftover recovery file/i);
    // Quiet: no bordered section, no icon, no alert, and no verdict in
    // either direction while the answer is still on its way.
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(line.textContent).not.toMatch(/could not/i);
    expect(line.textContent).not.toMatch(WARNING_WORDING);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();

    release();
    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });

  it("T-57z GROUND: a status query that answered with nothing to report renders nothing at all", async () => {
    // The settled signal comes from the real query cache, not from the DOM:
    // an empty container before the answer would pass this on a component
    // that never renders anything. See helpers/vaultStatusSettled.ts.
    mockFetch({ "/vault/status": { body: ABSENT } });
    await expectNothingOnceAnswered(renderWithQueryClient(<EmptyStubNotice />));
  });

  it("T-57z GROUND: an older backend that does not know the field renders nothing at all", async () => {
    mockFetch({ "/vault/status": { body: OLDER } });
    await expectNothingOnceAnswered(renderWithQueryClient(<EmptyStubNotice />));
  });

  it("T-57p POSITIVE: with the file there, the warning renders as before and no check line", async () => {
    mockFetch({ "/vault/status": { body: PRESENT } });
    renderWithQueryClient(<EmptyStubNotice />);

    expect(await screen.findByTestId(NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Leftover recovery file" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A leftover file from a recovery" })).toBeInTheDocument();
    expect(screen.getByText("app.db.empty-stub-bak")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove it/i })).toBeInTheDocument();

    expect(screen.queryByTestId(CHECK)).not.toBeInTheDocument();
  });
});
