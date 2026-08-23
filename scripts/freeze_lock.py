"""Regenerate requirements.lock from the currently installed runtime closure.

Usage: python scripts/freeze_lock.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUNTIME_PACKAGES = {
    "annotated-doc",
    "annotated-types",
    "anyio",
    "certifi",
    "click",
    "colorama",
    "fastapi",
    "h11",
    "httpcore",
    "httptools",
    "httpx",
    "idna",
    "pydantic",
    "pydantic-core",
    "python-dotenv",
    "python-multipart",
    "pyyaml",
    "sniffio",
    "starlette",
    "typing-extensions",
    "typing-inspection",
    "uvicorn",
    "uvloop",
    "watchfiles",
    "websockets",
}

WINDOWS_ONLY = {"colorama"}

# ``uvicorn[standard]`` installs uvloop on POSIX but it is absent from the
# Windows interpreter used to regenerate this lock. Keep the conditional pin
# in the lock on every platform so Linux CI remains reproducible.
CONDITIONAL_PINS = {"uvloop": "0.22.1"}

HEADER = (
    "# Pinned runtime dependency closure for reproducible builds.\n"
    "# Regenerate: python scripts/freeze_lock.py\n"
)


def normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        check=True,
    )
    pins: list[str] = []
    for line in result.stdout.splitlines():
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        canonical = normalize(name)
        if canonical not in RUNTIME_PACKAGES:
            continue
        marker = '; platform_system == "Windows"' if canonical in WINDOWS_ONLY else ""
        if canonical == "uvloop":
            marker = '; platform_system != "Windows"'
        pins.append(f"{name.replace('_', '-')}=={version}{marker}")
    present = {normalize(line.partition("==")[0]) for line in pins}
    for canonical, version in CONDITIONAL_PINS.items():
        if canonical not in present:
            pins.append(f'{canonical}=={version}; platform_system != "Windows"')
    pins.sort(key=str.lower)
    lock_path = Path(__file__).resolve().parent.parent / "requirements.lock"
    lock_path.write_text(HEADER + "\n".join(pins) + "\n", encoding="utf-8")
    print(f"Wrote {len(pins)} pins to {lock_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
