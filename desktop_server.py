"""Packaged server entrypoint for the Helix Support desktop sidecar.

Reads HELIX_PORT (default 8766) and DATABASE_PATH from the environment —
both are injected by the Tauri supervisor. Kept dependency-free so
PyInstaller collects exactly the app runtime.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from app.main import create_app

    app = create_app()
    port = int(os.getenv("HELIX_PORT", "8766"))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        lifespan="on",
    )


if __name__ == "__main__":
    main()
