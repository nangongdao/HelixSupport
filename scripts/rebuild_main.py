"""Rebuild app/main.py from the backup, removing extracted route blocks and
mounting the domain routers (Phase 27.2). Line numbers are 1-based against
app/main.py.bak (the pre-split single file).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))
from scripts._console import use_utf8_console  # noqa: E402
BAK = ROOT / "app" / "main.py.bak"
MAIN = ROOT / "app" / "main.py"

# (start, end) inclusive 1-based ranges to REMOVE from the backup
BLOCKS = [
    (629, 668),  # system routes (health/me/dashboard)
    (670, 1670),  # conversations domain (incl saved-views, queue SSE, canned, audit, knowledge)
    (1698, 2127),  # admin domain (prompts, tenants, quota, members, usage, retention, webhooks)
    (2136, 2245),  # auth routes (login/callback/logout/session/refresh)
]

MOUNT = """    # Phase 27.2: domain routers (extracted from create_app).
    from app.routers.common import RouteDeps
    from app.routers.system import build_router as build_system_router
    from app.routers.conversations import build_router as build_conversations_router
    from app.routers.knowledge import build_router as build_knowledge_router
    from app.routers.admin import build_router as build_admin_router
    from app.routers.auth import build_router as build_auth_router

    route_deps = RouteDeps(
        settings=settings,
        database=database,
        orchestrator=orchestrator,
        turn_worker=turn_worker,
        services=services,
        queue=queue,
        webhook_service=webhook_service,
        static_dir=static_dir,
        oidc_config=oidc_config,
        oidc_authenticator=oidc_authenticator,
        telemetry_metrics=telemetry_metrics,
    )
    app.include_router(build_system_router(route_deps))
    app.include_router(build_conversations_router(route_deps))
    app.include_router(build_knowledge_router(route_deps))
    app.include_router(build_admin_router(route_deps))
    app.include_router(build_auth_router(route_deps))

"""


def rebuild() -> None:
    lines = BAK.read_text(encoding="utf-8").splitlines()
    removed: list[tuple[int, int]] = []
    for start, end in BLOCKS:
        removed.append((start - 1, end))  # 0-based slice [start-1:end]
    # Keep lines not in any removed range.
    kept: list[str] = []
    cursor = 0
    for start0, end0 in sorted(removed):
        kept.extend(lines[cursor:start0])
        cursor = end0
    kept.extend(lines[cursor:])
    text = "\n".join(kept)
    # Insert the mount block before the quality router mount comment.
    marker = "    # Phase 21.1: mount the supervisor quality aggregator router."
    assert marker in text, "mount marker not found"
    text = text.replace(marker, MOUNT + marker, 1)
    MAIN.write_text(text + "\n", encoding="utf-8")
    print(f"rebuilt {MAIN} with {len(kept)} lines")


if __name__ == "__main__":
    use_utf8_console()
    rebuild()
