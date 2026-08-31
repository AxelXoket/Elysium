import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { readFileSync } from "fs";

// Single source of truth for the app version: package.json. The sidebar footer
// renders __APP_VERSION__, so bumping package.json is the only place to change.
const pkg = JSON.parse(
  readFileSync(path.resolve(__dirname, "package.json"), "utf-8"),
) as { version: string };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    // sentinels.ts FIRST, and the order is the fragile part of the whole
    // arrangement. It installs the runtime traps - egress, storage, console,
    // header, payload - and a module that writes at import time slips past
    // every one of them if anything is imported before they are in place.
    // setup.ts comes second because it imports the draft store, which is a
    // module under test.
    setupFiles: ["./src/test/sentinels.ts", "./src/test/setup.ts"],
    // Pinned, not inherited. setup.ts's global afterEach clears the draft
    // store, and "stack" is what makes it run AFTER Testing Library's own
    // cleanup rather than before it. That is vitest's default today, so this
    // line changes nothing - it just stops a future default from silently
    // reordering the two and leaving drafts alive across tests.
    sequence: { hooks: "stack" },
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
