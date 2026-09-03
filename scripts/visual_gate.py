"""Visual regression gate for stable components (ROADMAP section 43.6).

Screenshot-diff against committed baselines — but only for the surfaces that
are actually stable: the operator workspace on first load, dark/light theme
tokens, the knowledge view, and the mobile queue drawer. Transient regions
(timestamps, dynamic conversation data) are masked before comparison so a
baseline survives normal data churn.

The server must run against a CLEAN database (same requirement as CI's
browser job, ``DATABASE_PATH=/tmp/helix_ci.db``): the workspace screenshot
includes the conversation queue rows, so accumulated test data changes the
capture and fails the gate by design — that is a data-state difference, not
a UI regression.

Baselines live in ``tests/baselines/``. First run without a baseline writes
one and passes (bootstrap); later runs fail on pixel drift beyond
``DIFF_RATIO_LIMIT`` **or on any change in capture geometry** — a size change
is a visual change, not a licence to re-baseline. After an intended UI change,
re-capture with ``--update`` (or delete the PNG) and commit the new baseline.

The existing axe / keyboard / reduced-motion gates in
``tests/ui_accessibility.py`` are unchanged; this module adds only the pixel
layer.

Usage:
    HELIX_BASE_URL=http://127.0.0.1:8765 python scripts/visual_gate.py
"""

from __future__ import annotations

import argparse
import os
import sys
from io import BytesIO
from pathlib import Path
from uuid import uuid4

_DESCRIPTION = (__doc__ or "visual regression gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from scripts._console import use_utf8_console

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
WIDGET_SECRET = os.getenv("WIDGET_SECRET", "helix-widget-dev-secret")
BASELINES = ROOT / "tests" / "baselines"
# Fraction of pixels allowed to differ beyond the tolerance before failing.
DIFF_RATIO_LIMIT = 0.005
# Per-channel tolerance: theme anti-aliasing shifts pixels by a few levels.
PIXEL_TOLERANCE = 12


def launch_browser(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except PlaywrightError:
        return playwright.chromium.launch(channel="msedge", headless=True)


def mask_dynamic(page: Page) -> None:
    """Blank out regions that legitimately change between runs."""
    page.evaluate(
        """() => {
          const stamp = (selector) => {
            for (const el of document.querySelectorAll(selector)) {
              el.textContent = '0';
              el.style.color = 'transparent';
            }
          };
          stamp('time');
          stamp('.item-sla');
          stamp('#liveStatus');
          stamp('#queueCount');
          stamp('#operatorIdentity');
          stamp('.metric strong');
          stamp('.knowledge-article-meta time');
        }"""
    )


def capture(page: Page, path_hint: str) -> Image.Image:
    mask_dynamic(page)
    page.wait_for_timeout(200)  # let the mask settle
    raw = page.screenshot(full_page=False)
    return Image.open(BytesIO(raw)).convert("RGB")


def _drift_path(name: str) -> Path:
    """Where a failing capture is written for eyeballing."""
    drift_dir = ROOT / "artifacts"
    drift_dir.mkdir(parents=True, exist_ok=True)
    return drift_dir / f"visual-drift-{name}.png"


def compare(name: str, current: Image.Image, update: bool = False) -> tuple[bool, float, str]:
    """Compare against baseline; write one when missing. Returns (ok, ratio, message)."""
    BASELINES.mkdir(parents=True, exist_ok=True)
    baseline_path = BASELINES / f"{name}.png"
    if update:
        current.save(baseline_path)
        return True, 0.0, f"{name}: baseline updated ({current.size[0]}x{current.size[1]})"
    if not baseline_path.is_file():
        current.save(baseline_path)
        return True, 0.0, f"{name}: baseline created ({current.size[0]}x{current.size[1]})"
    baseline = Image.open(baseline_path).convert("RGB")
    if baseline.size != current.size:
        # A size change used to return ok=True *and* overwrite the committed
        # baseline, so any regression that also shifted layout height by a
        # pixel — which most do — was laundered into the baseline, and the
        # next run reported a clean 0.00%. Verified: a 100%-different capture
        # one pixel taller passed and replaced the baseline.
        #
        # Geometry drift is a real visual change, so it fails. Re-baselining
        # is a deliberate act (--update, or deleting the PNG), never a
        # side effect of a comparison.
        current.save(_drift_path(name))
        return (
            False,
            1.0,
            (f"{name}: geometry changed {baseline.size}->{current.size}; "
            f"capture at {_drift_path(name)}. Re-run with --update if intended"),
        )
    diff_pixels = 0
    total_pixels = baseline.size[0] * baseline.size[1]
    b_bytes = baseline.tobytes()
    c_bytes = current.tobytes()
    width3 = baseline.size[0] * 3
    for y in range(baseline.size[1]):
        row_off = y * width3
        for x in range(0, width3, 3):
            off = row_off + x
            if (
                abs(b_bytes[off] - c_bytes[off]) > PIXEL_TOLERANCE
                or abs(b_bytes[off + 1] - c_bytes[off + 1]) > PIXEL_TOLERANCE
                or abs(b_bytes[off + 2] - c_bytes[off + 2]) > PIXEL_TOLERANCE
            ):
                diff_pixels += 1
    ratio = diff_pixels / total_pixels
    if ratio > DIFF_RATIO_LIMIT:
        drift = _drift_path(name)
        current.save(drift)
        return (
            False,
            ratio,
            f"{name}: {ratio:.2%} pixels differ (limit {DIFF_RATIO_LIMIT:.2%}); capture at {drift}",
        )
    return True, ratio, f"{name}: ok ({ratio:.2%} diff)"


def widget_url() -> str:
    from app.widget_token import sign_token

    token = sign_token(
        secret=WIDGET_SECRET,
        tenant_id="demo",
        customer_ref=f"VIS-{uuid4().hex[:8]}",
        ttl_seconds=1800,
    )
    return f"{BASE_URL}/widget?brand=Northstar+Care&accent=teal&locale=zh#token={token}"


def wait_for_operator(page: Page) -> None:
    from playwright.sync_api import expect

    page.goto(BASE_URL, wait_until="domcontentloaded")
    expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
    expect(page.locator("#conversationList")).to_have_attribute("aria-busy", "false")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite every baseline from this run's captures (intentional re-baseline)",
    )
    args = parser.parse_args(argv)
    update = args.update

    results: list[tuple[bool, float, str]] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        # Pin the color scheme so resolveInitialTheme() (main.js) picks a
        # deterministic default instead of inheriting the runner's OS theme.
        # The gate captures "workspace-dark" before any explicit applyTheme()
        # call, so without this the baseline depends on prefers-color-scheme.
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            color_scheme="dark",
        )
        page = context.new_page()

        wait_for_operator(page)
        # Force dark explicitly — localStorage from a prior run in the same
        # context could otherwise leak a light preference.
        page.evaluate("() => window.HelixModules.applyTheme('dark')")
        page.wait_for_timeout(300)
        results.append(compare("workspace-dark", capture(page, "workspace-dark"), update))

        page.evaluate("() => window.HelixModules.applyTheme('light')")
        page.wait_for_timeout(400)
        results.append(compare("workspace-light", capture(page, "workspace-light"), update))

        page.evaluate("() => window.HelixModules.applyTheme('dark')")
        page.wait_for_timeout(300)

        page.locator('.nav-item[data-view="knowledge"]').click()
        page.wait_for_selector("#knowledgeList[aria-busy='false']", timeout=15000)
        results.append(compare("knowledge-view", capture(page, "knowledge-view"), update))

        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        wait_for_operator(mobile)
        # Same context shares localStorage, so the theme above (dark) carries
        # over; pin it explicitly so mobile-queue is deterministic too.
        mobile.evaluate("() => window.HelixModules.applyTheme('dark')")
        mobile.wait_for_timeout(300)
        mobile.locator("#mobileQueue").click()
        mobile.wait_for_timeout(300)
        results.append(compare("mobile-queue", capture(mobile, "mobile-queue"), update))

        browser.close()

    failures = [message for ok, _, message in results if not ok]
    for _, _, message in results:
        print(message)
    if failures:
        print(f"visual gate FAILED: {len(failures)} surface(s) drifted", file=sys.stderr)
        return 1
    print("visual gate passed")
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
