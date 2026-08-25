"""Static asset release metadata shared by HTTP caching and quality gates."""

STATIC_ASSET_VERSION = "1.4.0"
VERSIONED_STATIC_CACHE_CONTROL = "public, max-age=31536000, immutable"

# D2 (DESKTOP_TAURI_PLAN.md §3.4): Vite-produced React island bundles live
# under app/static/dist/. Their filenames are content-hashed by Vite, so
# they are self-cache-busting and do NOT use the ?v= mechanism. The legacy
# zero-build modules (app.js + js/*.js) continue to use ?v=STATIC_ASSET_VERSION.
DIST_DIR = "dist"
