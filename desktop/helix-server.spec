# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: package the Helix Support backend as an onedir sidecar.

Output: dist/helix-server/helix-server.exe (+ support DLLs and _internal).
The Tauri supervisor bundles this directory under src-tauri/resources/server/.

Build (from repo root):
  artifacts/rls-venv/Scripts/python.exe -m PyInstaller desktop/helix-server.spec --noconfirm
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).parent

block_cipher = None

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # uvicorn[standard] extras that resolve dynamically
    "uvicorn.loops.asyncio",
    "anyio._backends._asyncio",
    # app runtime pieces imported via string paths or lazy imports
    "app.main",
    "desktop_server",
]

# app.migrations loads vNN_* modules dynamically (importlib.import_module),
# so PyInstaller's static analysis cannot see them — collect them explicitly.
import os as _os

_migrations_dir = ROOT / "app" / "migrations"
for _fname in sorted(_os.listdir(_migrations_dir)):
    if _fname.startswith("v") and _fname.endswith(".py"):
        hiddenimports.append(f"app.migrations.{_fname[:-3]}")

a = Analysis(
    [str(ROOT / "desktop_server.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Static frontend assets must ship inside the exe bundle because
        # app/main.py mounts them relative to the package directory.
        (str(ROOT / "app" / "static"), "app/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # dev-only heavyweights that must never leak into the bundle
        "playwright",
        "pyright",
        "ruff",
        "pytest",
        "PIL",
        "tkinter",
        "setuptools",
        "pip",
        "wheel",
        "pkg_resources",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Vite emits a .map per chunk (2.1 MB in the current build) for debugging
# the web build in a real browser. The desktop sidecar serves the same
# assets but its bundle is local and shipped to end users, so the maps are
# pure dead weight there — drop them from the collected datas.
a.datas = [d for d in a.datas if not str(d[0]).endswith(".map")]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="helix-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # supervisor pipes stdout/stderr; CREATE_NO_WINDOW hides it
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="helix-server",
)
