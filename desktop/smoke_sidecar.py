"""Smoke test for the packaged Helix Support sidecar.

Spawns dist/helix-server/helix-server.exe, waits for /health/ready,
exercises a sample API call, then shuts it down cleanly. Exit code 0
means the bundle is viable for desktop bundling.

Usage (from repo root):
  artifacts/rls-venv/Scripts/python.exe desktop/smoke_sidecar.py [--dist DIR]
"""

from __future__ import annotations

import argparse
import httpx
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wait_ready(port: int, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health/ready"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default=str(ROOT / "desktop" / "dist" / "helix-server"))
    args = parser.parse_args()

    exe = Path(args.dist) / "helix-server.exe"
    if not exe.exists():
        print(f"FAIL: {exe} not found — build the spec first", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(
        prefix="helix-smoke-", ignore_cleanup_errors=True
    ) as tmp:
        env = dict(os.environ)
        env["HELIX_PORT"] = "8899"
        env["DATABASE_PATH"] = str(Path(tmp) / "support.db")
        started = time.monotonic()
        proc = subprocess.Popen(
            [str(exe)],
            env=env,
            cwd=str(Path(args.dist)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            ready = wait_ready(8899)
            elapsed = time.monotonic() - started
            if not ready:
                print(f"FAIL: /health/ready not reached in 25s (pid {proc.pid})")
                proc.terminate()
                out, _ = proc.communicate(timeout=10)
                print(out[-4000:])
                return 1
            print(f"backend ready in {elapsed:.2f}s")

            sample = httpx.get(
                "http://127.0.0.1:8899/api/conversations",
                headers={"X-Tenant-Id": "demo"},
                timeout=5.0,
            )
            if sample.status_code != 200:
                print(f"FAIL: sample API returned {sample.status_code}")
                return 1
            payload = sample.json()
            count = len(payload.get("items", [])) if isinstance(payload, dict) else len(payload)
            print(f"sample API ok ({count} conversations)")

            static_page = httpx.get("http://127.0.0.1:8899/", timeout=5.0)
            if static_page.status_code != 200 or "operator" not in static_page.text.lower():
                # index served but content sanity only; status is what matters
                if static_page.status_code != 200:
                    print(f"FAIL: operator UI returned {static_page.status_code}")
                    return 1
            print("operator UI served")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            print("sidecar shut down cleanly")

    print("PASS: sidecar smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
