/**
 * Helix Support — Vite build configuration (D2 island scaffold)
 *
 * Source: frontend/src/  →  Output: app/static/dist/
 *
 * The operator console keeps its zero-build legacy modules (app.js + js/*.js)
 * as the host page. Vite produces a per-island entry that mounts a
 * createRoot into an existing <div> in index.html, so the six gates
 * (ui_smoke / axe / visual / performance / OpenAPI / frontend) stay green
 * during the dual-track migration. See DESKTOP_TAURI_PLAN.md §3.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { copyFileSync } from "node:fs";

/**
 * Copy the zero-build island loader into dist/ on every build. The loader
 * is plain ESM (no JSX/imports), so it is not a Vite input — but the host
 * page imports it from /static/dist/island-loader.js, so each build must
 * refresh the copy to stay in sync with frontend/src/.
 */
function copyIslandLoader() {
  return {
    name: "copy-island-loader",
    closeBundle() {
      copyFileSync(
        resolve(__dirname, "src/island-loader.js"),
        resolve(__dirname, "../app/static/dist/island-loader.js"),
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), copyIslandLoader()],
  // artifacts land at app/static/dist/ and are referenced as /static/dist/.
  base: "/static/dist/",
  build: {
    outDir: resolve(__dirname, "../app/static/dist"),
    emptyOutDir: false,
    sourcemap: true,
    // Generate manifest.json so the island loader can resolve content-hashed
    // chunk filenames without hardcoding them.
    manifest: "manifest.json",
    // Each island entry only exports mount(); Rollup treats unused exports
    // of an entry as tree-shakeable and emits a chunk with the React runtime
    // but no island code (Vite sets preserveEntrySignatures:false for MPA
    // builds). "strict" keeps every entry's own exports — and therefore the
    // island bodies — in the emitted chunk.
    rollupOptions: {
      preserveEntrySignatures: "strict",
      input: {
        quality: resolve(__dirname, "src/islands/quality-island.jsx"),
        knowledge: resolve(__dirname, "src/islands/knowledge-island.jsx"),
        ticket: resolve(__dirname, "src/islands/ticket-island.jsx"),
        queue: resolve(__dirname, "src/islands/queue-island.jsx"),
        inspector: resolve(__dirname, "src/islands/inspector-island.jsx"),
        composer: resolve(__dirname, "src/islands/composer-island.jsx"),
        "command-palette": resolve(__dirname, "src/islands/command-palette-island.jsx"),
        "session-shell": resolve(__dirname, "src/islands/session-shell-island.jsx"),
        terminal: resolve(__dirname, "src/islands/terminal-island.jsx"),
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
          "query-vendor": ["@tanstack/react-query"],
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
});
