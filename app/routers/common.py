"""Shared route dependencies (Phase 27.2).

FastAPI routes in ``app/main.py`` were closures over ``create_app`` locals.
Each domain router in this package receives a :class:`RouteDeps` bundle so the
route bodies keep referencing the same objects without global state. The
bundle is assembled once in ``create_app`` and passed to each
``build_router(deps)`` factory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.database import Database
from app.jobs import TurnJobWorker
from app.orchestrator import ConversationOrchestrator
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.webhooks import WebhookService


@dataclass(frozen=True)
class RouteDeps:
    settings: Settings
    database: Database
    orchestrator: ConversationOrchestrator
    turn_worker: TurnJobWorker
    services: Any  # AppServices
    queue: Any  # TaskQueue
    webhook_service: WebhookService | None
    static_dir: Any  # Path
    oidc_config: Any | None = None
    oidc_authenticator: Any | None = None
    oidc_flow: Any | None = None
    telemetry_metrics: Any | None = None
    prompt_registry: PromptRegistry | None = None
    quality_service: QualityService | None = None
