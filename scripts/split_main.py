"""Generate app/routers/conversations.py and slim main.py (Phase 27.2).

Extracts the conversations-domain route block (lines 670-1670 of the backup
main.py) into a router factory using the RouteDeps pattern, and removes those
lines from main.py, replacing them with an include_router call.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_BAK = ROOT / "app" / "main.py.bak"
ROUTER = ROOT / "app" / "routers" / "conversations.py"

START = 669  # 0-based: line 670
END = 1670  # 0-based exclusive: up to line 1670 (empty line before knowledge-gaps)


def generate_router() -> str:
    lines = MAIN_BAK.read_text(encoding="utf-8").splitlines()
    block = "\n".join(lines[START:END])
    # Rewrite decorators
    block = re.sub(r"^    @app\.", "    @router.", block, flags=re.M)
    header = '''"""Conversations, saved views, canned responses, audit, and knowledge routes (27.2)."""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Annotated, Any, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from typing_extensions import Annotated as TAnnotated  # noqa: F401

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._console import use_utf8_console  # noqa: E402

from app.main import (
    IDEMPOTENCY_KEY_PATTERN,
    BulkConversationActionOut,
    BulkConversationActionRequest,
    CannedResponseCreateRequest,
    CannedResponseOut,
    CannedResponseUpdateRequest,
    ConversationDetail,
    ConversationLabelOut,
    ConversationLabelsRequest,
    ConversationOut,
    ConversationPriorityRequest,
    FeedbackOut,
    FeedbackRequest,
    KnowledgeArticleOut,
    KnowledgeCreateRequest,
    KnowledgeReviewRequest,
    KnowledgeUpdateRequest,
    MessageOut,
    OperatorMessageRequest,
    SavedQueueViewCreateRequest,
    SavedQueueViewOut,
    TurnResponse,
    _conversation_quota_exceeded,
    _message_date_for_quality,
    _message_intent_for_quality,
    _message_prompt_version_for_quality,
    canned_response_out,
    conversation_out,
    get_principal,
    knowledge_out,
    message_out,
    require_any_permission,
    require_permission,
    turn_job_out,
)
from app.orchestrator import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TurnInProgressError,
)
from app.routers.common import RouteDeps
from app.security import Principal, Role


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database
    orchestrator = deps.orchestrator
    settings = deps.settings
    services = deps.services
    turn_worker = deps.turn_worker
    queue = deps.queue
    telemetry_metrics = deps.telemetry_metrics

'''
    return header + block + "\n    return router\n"


def slim_main() -> None:
    main = ROOT / "app" / "main.py"
    lines = main.read_text(encoding="utf-8").splitlines()
    new_lines = lines[:START] + lines[END:]
    text = "\n".join(new_lines)
    # Insert the include_router call before the quality router mount comment.
    marker = "    # Phase 21.1: mount the supervisor quality aggregator router."
    insertion = (
        "    # Phase 27.2: conversations-domain router (extracted from create_app).\n"
        "    from app.routers.conversations import build_router as build_conversations_router\n"
        "    from app.routers.common import RouteDeps\n"
        "    app.include_router(\n"
        "        build_conversations_router(\n"
        "            RouteDeps(\n"
        "                settings=settings,\n"
        "                database=database,\n"
        "                orchestrator=orchestrator,\n"
        "                turn_worker=turn_worker,\n"
        "                services=services,\n"
        "                queue=queue,\n"
        "                webhook_service=webhook_service,\n"
        "                static_dir=static_dir,\n"
        "                telemetry_metrics=telemetry_metrics,\n"
        "            )\n"
        "        )\n"
        "    )\n\n"
    )
    if marker in text:
        text = text.replace(marker, insertion + marker, 1)
    main.write_text(text + "\n", encoding="utf-8")
    print("main.py slimmed")


def main() -> None:
    ROUTER.write_text(generate_router(), encoding="utf-8")
    print(f"wrote {ROUTER}")
    slim_main()


if __name__ == "__main__":
    use_utf8_console()
    main()
