/**
 * The only control in the app that shortens the window in which the data is
 * decrypted. Everything else protects a file at rest; this protects an open
 * window on a desk.
 *
 * The failure worth testing is not "the button does not work" - it is a
 * control that reports a setting the vault does not actually have, which is
 * how somebody ends up believing their vault locks itself when it does not.
 */
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AutoLockControl } from "@/components/settings/AutoLockControl";

const setAutoLock = vi.fn();
const settingsData = { current: { auto_lock_minutes: 0 } as { auto_lock_minutes: number } | undefined };
const mutationState = { isPending: false, isError: false };

vi.mock("@/lib/query/settings", () => ({
  useSettings: () => ({ data: settingsData.current }),
  useSetAutoLock: () => ({
    mutate: setAutoLock,
    isPending: mutationState.isPending,
    isError: mutationState.isError,
  }),
}));

function renderIt() {
  return renderWithQueryClient(<AutoLockControl />);
}

describe("AutoLockControl", () => {
  beforeEach(() => {
    setAutoLock.mockClear();
    settingsData.current = { auto_lock_minutes: 0 };
    mutationState.isPending = false;
    mutationState.isError = false;
  });

  it("shows which timeout is actually in force", () => {
    settingsData.current = { auto_lock_minutes: 15 };
    renderIt();
    expect(screen.getByRole("radio", { name: "15 min" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "Never" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("reads the server's value, not a local default", async () => {
    // A control that always drew "Never" while the vault held 30 would be
    // worse than no control: it would invite turning on something already on,
    // and hide something already off.
    settingsData.current = { auto_lock_minutes: 60 };
    renderIt();
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "1 hour" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
  });

  it("sends the chosen number of minutes", async () => {
    renderIt();
    await userEvent.click(screen.getByRole("radio", { name: "30 min" }));
    expect(setAutoLock).toHaveBeenCalledWith(30);
  });

  it("sends zero for Never", async () => {
    settingsData.current = { auto_lock_minutes: 30 };
    renderIt();
    await userEvent.click(screen.getByRole("radio", { name: "Never" }));
    expect(setAutoLock).toHaveBeenCalledWith(0);
  });

  it("says plainly that off means off", () => {
    renderIt();
    expect(
      screen.getByText(/stays open until you lock it/i),
    ).toBeInTheDocument();
  });

  it("does not claim off when a timeout is set", () => {
    settingsData.current = { auto_lock_minutes: 5 };
    renderIt();
    expect(screen.queryByText(/stays open until you lock it/i)).toBeNull();
  });

  it("promises not to interrupt a reply being written", () => {
    // The reason people turn this kind of thing off. Saying it is the
    // difference between a feature that gets used and one that gets disabled.
    renderIt();
    expect(
      screen.getByText(/still being written counts as something happening/i),
    ).toBeInTheDocument();
  });

  it("says the setting did not save rather than showing it as saved", async () => {
    mutationState.isError = true;
    renderIt();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /still using the previous setting/i,
    );
  });

  it("cannot be clicked twice while saving", () => {
    mutationState.isPending = true;
    renderIt();
    expect(screen.getByRole("radio", { name: "5 min" })).toBeDisabled();
  });

  it("is reachable as one group by a screen reader", () => {
    renderIt();
    expect(
      screen.getByRole("radiogroup", {
        name: /lock the vault after this long idle/i,
      }),
    ).toBeInTheDocument();
  });

  it("survives a settings response that predates this field", () => {
    // An older server does not send auto_lock_minutes at all. The schema
    // defaults it, and the control must read that as off rather than crash.
    settingsData.current = {} as { auto_lock_minutes: number };
    renderIt();
    expect(screen.getByRole("radio", { name: "Never" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
