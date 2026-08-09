// FIRST, before anything that builds a Zod schema at module scope. See the
// module's own comment: the flag it sets is read once, lazily, and our schema
// files read it while they are being evaluated.
import "./lib/zodJitless";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ErrorBoundary } from "./components/errors/ErrorBoundary";
import { Providers } from "./app/providers";
import { App } from "./app/App";
import "./index.css";
import { readLaunchToken } from "./lib/api/launchToken";
import { installNavigationGuard } from "./lib/navigationGuard";

// FIRST, before anything can make a request. The desktop app opens this page
// at #elysium-token=<secret>; without it every API call is refused, and the
// fragment is stripped from the address as soon as it is read.
readLaunchToken();

// The app window has no address bar and no way back, so a single navigation
// off this origin would leave somebody else's page wearing Elysium's frame.
// Installed before the first render, in the capture phase, so it does not
// depend on any component remembering to ask for it.
installNavigationGuard();

// ErrorBoundary sits OUTSIDE Providers so a crash anywhere - including a
// provider itself - swaps to the branded fallback instead of a white window
// (the packaged app has no console to even hint at what happened).
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <Providers>
        <App />
      </Providers>
    </ErrorBoundary>
  </StrictMode>,
);
