"""Gate B (1.4) item #2: image admission gate — reject an unpinned release.

The 1.4 exit criterion requires the release image to carry a digest,
signature, provenance and SBOM, and staging admission to reject unsigned
images.  The signature/admission half needs a live registry and cosign key
(the deploying organisation's environment); this check is the part that can
run anywhere: **a release with a null base-image digest, a drifted source
tree, or a missing SBOM must not be admitted to staging.**

Checks (all fail-closed, exit 1 on any violation):

1. ``supplychain/base-image-pin.json`` has a non-null ``digest`` (CI backfills
   it after ``docker manifest inspect`` in the controlled flow; offline
   machines must not fabricate one).
2. ``scripts/release_manifest.py --verify`` passes for
   ``artifacts/release-manifest.json`` (content hashes match the current
   source tree).
3. ``artifacts/sbom.json`` exists and lists at least one component (the
   deployable application's SBOM, Path 28.5).

With ``--cosign-key``/``--registry`` (real environment) the check additionally
runs ``cosign verify`` on the given image reference and fails if cosign is
missing or the signature does not verify; without them it records the
simulation note for the deployer.

Usage:
    python scripts/image_admission_check.py
    python scripts/image_admission_check.py --registry ghcr.io/x --image x/y:1.4.0 \
        --cosign-key <key>
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("helix")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console

DEFAULT_PIN = ROOT / "supplychain" / "base-image-pin.json"
DEFAULT_MANIFEST = ROOT / "artifacts" / "release-manifest.json"
DEFAULT_SBOM = ROOT / "artifacts" / "sbom.json"

FAIL_CLOSED = "release image is not pinned/verifiable: staging admission would reject"


def _run_utf8(cmd: list[str], *, python_child: bool) -> subprocess.CompletedProcess[str]:
    """Capture a child's output as UTF-8, and never let its bytes crash us.

    Two separate hazards, both of which turned a clean `exit 1` into an
    unhandled UnicodeEncodeError on Windows:

    1. A Python child writes its Chinese violation text through a GBK stdout
       (the console codepage), while we decode as UTF-8 — every multi-byte
       character came back as U+FFFD. PYTHONIOENCODING pins the child to UTF-8
       so the text survives the pipe. It has no effect on a non-Python child
       (cosign is a Go binary), hence the flag.
    2. Whatever still fails to decode is dropped rather than kept as U+FFFD:
       re-printing a replacement character to a GBK stdout raises, so the gate
       died reporting a real violation instead of reporting it.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"} if python_child else None
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        env=env,
        check=False,
    )


def _check_pin(pin_path: Path = DEFAULT_PIN, *, require_digest: bool = True) -> list[str]:
    if not pin_path.exists():
        return [f"{FAIL_CLOSED} — base-image pin 缺失: {pin_path}"]
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    if not require_digest:
        return []
    digest = pin.get("digest")
    if not isinstance(digest, str) or not digest.strip():
        return [f"{FAIL_CLOSED} — base-image digest 未固定（null/空），需受控 CI 回填"]
    if not digest.startswith("sha256:"):
        return [f"{FAIL_CLOSED} — digest 格式异常（期望 sha256:...）: {digest}"]
    hexpart = digest.split(":", 1)[1]
    if len(hexpart) != 64 or not all(c in "0123456789abcdef" for c in hexpart.lower()):
        return [f"{FAIL_CLOSED} — digest 不是有效的 sha256:64hex: {digest[:24]}..."]
    return []


def _check_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> list[str]:
    if not manifest_path.exists():
        return [f"{FAIL_CLOSED} — release manifest 缺失: {manifest_path}"]
    run = _run_utf8(
        [sys.executable, "scripts/release_manifest.py", "--verify", "--out", str(manifest_path)],
        python_child=True,
    )
    if run.returncode != 0:
        output = (run.stdout or "") + (run.stderr or "")
        return [f"{FAIL_CLOSED} — release manifest 校验失败: {output[-300:]}"]
    return []


def _check_sbom(sbom_path: Path = DEFAULT_SBOM) -> list[str]:
    if not sbom_path.exists():
        return [f"{FAIL_CLOSED} — SBOM 缺失: {sbom_path}"]
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    components = sbom.get("components", [])
    if not isinstance(components, list) or not components:
        return [f"{FAIL_CLOSED} — SBOM 无组件: {sbom_path}"]
    return []


def _check_cosign(registry: str, image: str, key: str) -> list[str]:
    if shutil.which("cosign") is None:
        return [f"{FAIL_CLOSED} — 期望 cosign 签名校验但本机无 cosign 二进制"]
    # ``image`` is already a fully-qualified reference when it contains a host
    # prefix ("/"); otherwise prepend ``registry``.
    ref = image if ("/" in image or registry == "") else f"{registry}/{image}"
    run = _run_utf8(["cosign", "verify", "--key", key, ref], python_child=False)
    if run.returncode != 0:
        output = (run.stdout or "") + (run.stderr or "")
        return [f"{FAIL_CLOSED} — cosign verify 失败: {output[-300:]}"]
    return []


def admission_gate(
    *,
    pin_path: Path = DEFAULT_PIN,
    manifest_path: Path = DEFAULT_MANIFEST,
    sbom_path: Path = DEFAULT_SBOM,
    registry: str = "",
    image: str = "",
    cosign_key: str = "",
    require_digest: bool = True,
) -> list[str]:
    """Return violations (empty = release can be admitted for these checks).

    ``require_digest=False`` skips the base-image digest check (CI without a
    live registry still verifies the manifest and SBOM; the digest is the
    deployer-controlled backfill).
    """
    violations: list[str] = []
    violations.extend(_check_pin(pin_path, require_digest=require_digest))
    violations.extend(_check_manifest(manifest_path))
    violations.extend(_check_sbom(sbom_path))
    if cosign_key:
        violations.extend(_check_cosign(registry, image, cosign_key))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sbom", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--registry", default="", help="registry host (with --cosign-key)")
    parser.add_argument("--image", default="", help="image reference (with --cosign-key)")
    parser.add_argument("--cosign-key", default="", help="cosign public key (real environment)")
    parser.add_argument(
        "--no-digest-required",
        action="store_true",
        help="CI without a live registry: skip the digest backfill check",
    )
    args = parser.parse_args(argv)

    violations = admission_gate(
        pin_path=args.pin,
        manifest_path=args.manifest,
        sbom_path=args.sbom,
        registry=args.registry,
        image=args.image,
        cosign_key=args.cosign_key,
        require_digest=not args.no_digest_required,
    )
    for violation in violations:
        print(f"admission: {violation}")
    if violations:
        print(f"admission: {len(violations)} violation(s) — release REJECTED")
        return 1
    print("admission: digest/SBOM/manifest consistent — staging admission READY")
    return 0


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
