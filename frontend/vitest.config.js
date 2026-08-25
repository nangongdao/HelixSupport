/**
 * Helix Support — vitest config (D2, §3.5)
 *
 * Runs the React island component tests with jsdom + @testing-library/react.
 * The Node test runner (tests/frontend/*.test.js) covers the legacy
 * zero-build modules; vitest covers the Vite-built React islands so the
 * frontend gate enforces both suites.
 *
 * See DESKTOP_TAURI_PLAN.md §3.5 + §D2.
 */

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{js,jsx}"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
});
