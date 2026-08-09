/**
 * The app window must stay the app window.
 *
 * Elysium runs inside a WebView2 control with no address bar, no tabs and no
 * way back. A single top-level navigation away from the local origin turns
 * that control into a browser showing somebody else's page inside a frame the
 * user reads as "Elysium" - and there is no visible URL to contradict it. The
 * page it lands on can ask for the passphrase and look exactly right.
 *
 * The link does not have to be malicious to get there. Model output is
 * rendered as text the user can click, and a URL in a reply is an ordinary
 * thing for a model to produce.
 *
 * So navigation off this origin is refused at the document level, in the
 * CAPTURE phase, before any component's own handler runs. A guard that
 * depends on every link component remembering to call it is a guard that
 * lasts until the next component.
 *
 * What this is NOT: a way to open external links safely. There is no such
 * path here - the WebView2 control has no system-browser bridge configured -
 * so the honest behaviour is to refuse rather than to appear to do something.
 *
 * Today nothing legitimate is blocked: the app renders no anchors at all and
 * message text is not linkified, so this costs the user nothing and exists
 * for the day one of those changes. setBlockedNavigationHandler is the seam
 * for telling them when it does; it is deliberately unset rather than wired
 * to a placeholder, because a link that silently does nothing is better than
 * one that pretends to have worked.
 */
let announce: ((url: string) => void) | null = null;

/** Where a blocked navigation is reported. Set by the app shell. */
export function setBlockedNavigationHandler(
  handler: ((url: string) => void) | null,
): void {
  announce = handler;
}

function isOffOrigin(raw: string): boolean {
  try {
    const target = new URL(raw, window.location.href);
    // javascript: and data: are not "another origin", they are script and
    // content injection, and neither belongs in a link the user clicks.
    if (target.protocol === "javascript:" || target.protocol === "data:") {
      return true;
    }
    if (target.protocol === "blob:") return false; // our own object URLs
    return target.origin !== window.location.origin;
  } catch {
    // An href this cannot even parse is not one to follow.
    return true;
  }
}

function onClick(event: MouseEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const anchor = target.closest("a");
  const href = anchor?.getAttribute("href");
  if (!anchor || !href) return;
  // A pure fragment link is in-page navigation, not a departure.
  if (href.startsWith("#")) return;
  if (!isOffOrigin(href)) return;

  event.preventDefault();
  event.stopPropagation();
  announce?.(href);
}

export function installNavigationGuard(): () => void {
  // Capture phase: this decision has to be made before anything downstream
  // gets a chance to navigate.
  document.addEventListener("click", onClick, true);
  return () => document.removeEventListener("click", onClick, true);
}
