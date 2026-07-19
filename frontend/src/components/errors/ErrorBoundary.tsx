import { Component, type ReactNode } from "react";
import { Wordmark } from "@/components/brand/Wordmark";

/**
 * ErrorBoundary - last-resort crash surface for the whole tree.
 *
 * In the packaged app there is no devtools console and no terminal: an
 * uncaught render error would unmount React and leave a permanently white
 * window. This boundary swaps the broken tree for a branded card with a
 * Reload control instead. Reload restarts the SPA only - the backend (and
 * the unlocked vault) live in the host process and are unaffected.
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
            An unexpected error broke this view. Your data is safe - reload to
            continue.
          </p>
          <button
            type="button"
            className="error-fallback-button"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
