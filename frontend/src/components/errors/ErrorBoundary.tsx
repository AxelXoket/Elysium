import { Component, type ReactNode } from "react";
import { Wordmark } from "@/components/brand/Wordmark";

/**
 * ErrorBoundary - last-resort crash surface for the whole tree.
 *
 * In the packaged app there is no devtools console and no terminal: an
 * uncaught render error would unmount React and leave a permanently white
 * window. This boundary swaps the broken tree for a branded card instead.
 *
 * It offers no Reload, and that is the whole point of the card's wording.
 * There used to be a button here calling `location.reload()`, and it was the
 * one control in the app that could make a crash permanent. The window is
 * opened at `#elysium-token=<secret>`; launchToken.ts reads that fragment
 * once and strips it with `history.replaceState`, so the address the window
 * would reload is the address WITHOUT the token. A reload therefore comes
 * back with `launchTokenHeader()` empty, and main.py's `launch_token_gate`
 * answers 403 `launch_token_invalid` to every `/api/v1` request from then on.
 * The shell HTML and the assets still load, so the app looks alive and can do
 * nothing at all - a worse state than the crash it was offered to recover
 * from, and one only closing the process can leave.
 *
 * Nothing in the renderer can restart the host, so the card is prose. That
 * matches the sentences in errors/errorMessages.ts, which name the same act
 * in the same words for the same reason: the packaged window has no reload
 * of any kind, since run_app.py calls `webview.start()` without `debug`.
 *
 * No componentDidCatch logging: console.* is banned in src (static-safety
 * S-03), and React already reports the error in dev.
 */

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  failed: boolean;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="error-fallback" role="alert">
        <div className="error-fallback-card">
          <Wordmark tone="onDark" size={30} />
          <h1 className="error-fallback-title">Something went wrong</h1>
          <p className="error-fallback-text">
            An unexpected error broke this view. Everything you had saved is
            safe: it is kept by Elysium itself, not by this window.
          </p>
          <p className="error-fallback-text">
            Close Elysium and start it again to carry on - unsent drafts do not
            survive that.
          </p>
        </div>
      </div>
    );
  }
}
