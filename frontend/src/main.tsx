import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ErrorBoundary } from "./components/errors/ErrorBoundary";
import { Providers } from "./app/providers";
import { App } from "./app/App";
import "./index.css";

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
