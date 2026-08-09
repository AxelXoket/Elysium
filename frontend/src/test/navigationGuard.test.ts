/**
 * The app window has no address bar. A single navigation off this origin puts
 * somebody else's page inside a frame the user reads as "Elysium", with
 * nothing visible to contradict it - and that page can ask for the passphrase
 * and look exactly right.
 *
 * The link does not have to be planted. Model output is rendered as clickable
 * text, and a URL in a reply is an ordinary thing for a model to produce.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  installNavigationGuard,
  setBlockedNavigationHandler,
} from "@/lib/navigationGuard";

let uninstall: (() => void) | null = null;
const blocked = vi.fn();

function clickLink(href: string): MouseEvent {
  const anchor = document.createElement("a");
  anchor.setAttribute("href", href);
  anchor.textContent = "link";
  document.body.appendChild(anchor);
  const event = new MouseEvent("click", { bubbles: true, cancelable: true });
  anchor.dispatchEvent(event);
  anchor.remove();
  return event;
}

describe("navigation guard", () => {
  beforeEach(() => {
    blocked.mockClear();
    setBlockedNavigationHandler(blocked);
    uninstall = installNavigationGuard();
  });

  afterEach(() => {
    uninstall?.();
    setBlockedNavigationHandler(null);
  });

  it.each([
    "https://example.com/",
    "http://evil.example/phish",
    "//evil.example/protocol-relative",
    "javascript:alert(1)",
    "data:text/html,<h1>hi</h1>",
  ])("refuses to leave for %s", (href) => {
    const event = clickLink(href);
    expect(event.defaultPrevented).toBe(true);
    expect(blocked).toHaveBeenCalledWith(href);
  });

  it("allows a link inside the app", () => {
    // The control. A guard that blocks everything is not a guard, it is a
    // broken app, and it would pass every test above.
    const event = clickLink("/settings");
    expect(event.defaultPrevented).toBe(false);
    expect(blocked).not.toHaveBeenCalled();
  });

  it("allows an in-page anchor", () => {
    const event = clickLink("#section-appearance");
    expect(event.defaultPrevented).toBe(false);
  });

  it("allows a blob url", () => {
    // Object URLs the app makes for its own images and audio.
    const event = clickLink("blob:http://localhost/abc-123");
    expect(event.defaultPrevented).toBe(false);
  });

  it("treats a malformed href the way the browser does", () => {
    // "ht!tp://[[[" is not a scheme the URL parser recognises, so it resolves
    // as a RELATIVE path against this origin - which is where a real click
    // would go too. Blocking it would be the guard disagreeing with the
    // navigation it is guarding.
    const event = clickLink("ht!tp://[[[");
    expect(event.defaultPrevented).toBe(false);
  });

  it("refuses a mailto or any other foreign scheme", () => {
    // No mail client is wired up here, and an external handler launching from
    // a chat reply is a departure from the window either way.
    const event = clickLink("mailto:someone@example.com");
    expect(event.defaultPrevented).toBe(true);
  });

  it("catches a click on something nested inside the link", () => {
    // Markdown renders links containing <code>, <em>, <strong>. The event
    // target is the child, not the anchor.
    const anchor = document.createElement("a");
    anchor.setAttribute("href", "https://evil.example/");
    const inner = document.createElement("code");
    inner.textContent = "click me";
    anchor.appendChild(inner);
    document.body.appendChild(anchor);

    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    inner.dispatchEvent(event);
    anchor.remove();

    expect(event.defaultPrevented).toBe(true);
  });

  it("stops listening once uninstalled", () => {
    uninstall?.();
    uninstall = null;
    const event = clickLink("https://example.com/");
    expect(event.defaultPrevented).toBe(false);
  });

  it("does not crash when nothing is listening for the report", () => {
    setBlockedNavigationHandler(null);
    expect(() => clickLink("https://example.com/")).not.toThrow();
  });
});
