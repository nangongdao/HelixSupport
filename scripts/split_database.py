"""Split app/database.py into domain mixin modules (Phase 27.1).

Uses the AST to extract each method's source faithfully (decorators
included, dedented to class-body level), writes per-domain mixin files,
and rewrites app/database.py to a thin class combining all mixins.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "app" / "database.py"
DB_DIR = ROOT / "app" / "db"

DOMAINS: dict[str, list[str]] = {
    "core": [
        "__init__",
        "connect",
        "_acquire_connection",
        "_new_connection",
        "_release_connection",
        "close",
        "performance_stats",
        "_invalidate_dashboard",
        "_invalidate_knowledge",
        "_touch_queue_watermark",
        "queue_revision",
        "ping",
    ],
    "core_schema": [
        "initialize",
        "_initialize_knowledge_fts",
        "_initialize_message_fts",
        "_ensure_column",
    ],
    "tenancy": [
        "ensure_tenant",
        "set_tenant_model_policy",
        "get_tenant_model_policy",
        "increment_tenant_usage",
        "increment_tenant_usage_conversations",
        "increment_tenant_usage_messages",
        "list_tenant_usage",
        "get_tenant_daily_usage",
        "tenant_exists",
        "seed_demo",
        "provision_tenant",
        "get_tenant_quota",
        "set_tenant_quota",
        "_seed_tenant_knowledge",
        "invite_member",
        "list_members",
        "get_member",
        "update_member_role",
        "deactivate_member",
        "record_member_login",
    ],
    "conversations": [
        "_conversation_from_row",
        "create_conversation",
        "get_conversation",
        "set_routing",
        "transition_conversation",
        "claim_conversation",
        "assign_conversation",
        "bulk_claim_conversations",
        "bulk_release_claims",
        "release_conversation_claim",
        "update_priority",
        "replace_conversation_labels",
        "list_conversation_labels",
        "list_saved_views",
        "create_saved_view",
        "delete_saved_view",
        "_saved_view_row",
        "bulk_update_priority",
        "bulk_modify_labels",
    ],
    "conversations_query": [
        "list_conversations",
        "conversation_watermark",
        "_query_conversations",
    ],
    "messages": [
        "add_message",
        "get_message_by_channel_id",
        "list_messages",
        "get_message",
    ],
    "jobs": [
        "claim_turn",
        "get_turn_by_message_id",
        "complete_turn",
        "fail_turn",
        "enqueue_turn_job",
        "get_turn_job",
        "get_latest_turn_job",
        "list_turn_jobs",
        "append_turn_job_chunk",
        "list_turn_job_chunks",
        "claim_next_turn_job",
        "claim_turn_job_by_id",
        "complete_turn_job",
        "fail_turn_job",
        "retry_turn_job",
        "recover_turn_jobs",
        "prune_turn_jobs",
        "turn_job_stats",
    ],
    "knowledge": [
        "search_knowledge",
        "_decorate_knowledge_matches",
        "_fallback_search_knowledge",
        "list_knowledge",
        "create_knowledge",
        "update_knowledge",
        "create_knowledge_draft",
        "review_knowledge",
        "list_knowledge_gaps",
        "get_order_for_customer",
        "get_customer_profile",
        "list_canned_responses",
        "get_canned_response",
        "create_canned_response",
        "update_canned_response",
        "record_canned_response_usage",
        "_canned_response_row",
    ],
    "audit": [
        "record_feedback",
        "audit",
        "audit_many",
        "list_audit",
        "export_audit_events",
        "_assistant_metrics",
        "dashboard",
    ],
}


def _method_source(tree: ast.Module, name: str, source_text: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Database":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    # Include decorator lines (@contextmanager, @staticmethod).
                    lines = source_text.splitlines()
                    start_line = item.lineno - 1
                    while start_line > 0 and lines[start_line - 1].strip().startswith("@"):
                        start_line -= 1
                    end_line = item.end_lineno  # 1-based inclusive
                    segment = "\n".join(lines[start_line:end_line])
                    # Normalize: dedent to column 0, then indent uniformly by 4
                    # so the whole method (multi-line signatures included) sits
                    # at class-body level.
                    dedented = textwrap.dedent(segment)
                    return textwrap.indent(dedented, "    ")
    raise SystemExit(f"method not found: {name}")


def extract() -> dict[str, str]:
    source_text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    mixins: dict[str, str] = {}
    for domain, names in DOMAINS.items():
        parts: list[str] = []
        for name in names:
            parts.append(_method_source(tree, name, source_text))
        class_name = f"Database{''.join(part.capitalize() for part in domain.split('_'))}Mixin"
        body = "\n\n".join(parts)
        mixins[domain] = f"class {class_name}:\n{body}\n"
    return mixins


def write_mixins(mixins: dict[str, str]) -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    # Shared helpers module: module-level functions extracted from the source.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    util_src: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "utc_now",
            "utc_after",
            "utc_after_seconds",
            "knowledge_search_terms",
            "knowledge_search_document",
        }:
            segment = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), node)
            assert segment is not None
            util_src.append(segment)
    constants = (
        'ASCII_TERM_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]+")\n'
        'CJK_SEQUENCE_PATTERN = re.compile(r"[\\u3400-\\u9fff]+")\n\n\n'
    )
    util_imports = (
        "from __future__ import annotations\n\n"
        "import re\n"
        "from datetime import UTC, datetime, timedelta\n\n"
    )
    (DB_DIR / "_util.py").write_text(
        util_imports + constants + "\n\n\n".join(util_src) + "\n", encoding="utf-8"
    )
    print("wrote app/db/_util.py")

    for domain, body in mixins.items():
        common = (
            "from __future__ import annotations\n\n"
            "import json\n"
            "import sqlite3\n"
            "from datetime import UTC, datetime, timedelta\n"
            "from typing import Any, Iterator, Sequence\n"
            "from uuid import uuid4\n\n"
            "from app.db._util import (\n"
            "    ASCII_TERM_PATTERN,\n"
            "    knowledge_search_document,\n"
            "    knowledge_search_terms,\n"
            "    utc_after,\n"
            "    utc_after_seconds,\n"
            "    utc_now,\n"
            ")\n"
        )
        if domain == "core":
            imports = (
                "from __future__ import annotations\n\n"
                "import json\n"
                "import sqlite3\n"
                "from contextlib import contextmanager\n"
                "from datetime import UTC, datetime, timedelta\n"
                "from pathlib import Path\n"
                "from queue import Empty, Queue\n"
                "from threading import Lock\n"
                "from typing import Any, Iterator, Sequence\n"
                "from uuid import uuid4\n\n"
                "from app.cache import TTLCache\n"
                "from app.db._util import (\n"
                "    ASCII_TERM_PATTERN,\n"
                "    knowledge_search_document,\n"
                "    knowledge_search_terms,\n"
                "    utc_after,\n"
                "    utc_after_seconds,\n"
                "    utc_now,\n"
                ")\n"
            )
        elif domain in {"conversations", "conversations_query"}:
            imports = (
                common
                + "from app.domain import ConversationStatus\nfrom app.labels import normalize_conversation_labels\n"
            )
        elif domain == "core_schema":
            imports = (
                "from __future__ import annotations\n\n"
                "import sqlite3\n"
                "from typing import Any\n\n"
                "from app.db._util import (\n"
                "    knowledge_search_document,\n"
                "    knowledge_search_terms,\n"
                "    utc_after,\n"
                "    utc_after_seconds,\n"
                "    utc_now,\n"
                ")\n"
                "from app.domain import ConversationStatus\n"
            )
        elif domain == "audit":
            imports = (
                common
                + "from app.context import current_request_id\n"
                + "from app.domain import ConversationStatus\n"
                + "from app.security import sanitize_for_audit\n"
            )
        else:
            imports = common + "from app.domain import ConversationStatus\n"
        header = (
            f'"""Database {domain} mixin (Phase 27.1, extracted from app/database.py)."""\n\n'
            + imports
        )
        (DB_DIR / f"{domain}.py").write_text(header + body, encoding="utf-8")
        print(f"wrote app/db/{domain}.py")


def main() -> None:
    write_mixins(extract())


if __name__ == "__main__":
    main()
