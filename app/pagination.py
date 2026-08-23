from __future__ import annotations

import base64
import binascii
import json
from typing import Any


class InvalidCursorError(ValueError):
    pass


def _decode_cursor_payload(value: str, *, label: str) -> dict[str, Any]:
    if (
        not value
        or len(value) > 512
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in value
        )
    ):
        raise InvalidCursorError(f"Invalid {label}")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidCursorError(f"Invalid {label}") from exc
    if not isinstance(payload, dict):
        raise InvalidCursorError(f"Invalid {label}")
    return payload


def _encode_cursor_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


SUPPORTED_QUEUE_SORTS = frozenset({"priority", "waiting", "sla", "updated"})


def encode_conversation_cursor(
    priority_rank: int,
    updated_at: str,
    conversation_id: str,
    *,
    sort: str = "priority",
    sort_key: str | None = None,
) -> str:
    if sort not in SUPPORTED_QUEUE_SORTS:
        raise InvalidCursorError("Unsupported conversation cursor sort")
    if sort == "priority":
        return _encode_cursor_payload(
            {"v": 1, "p": priority_rank, "u": updated_at, "i": conversation_id}
        )
    return _encode_cursor_payload(
        {
            "v": 2,
            "s": sort,
            "k": sort_key or "",
            "u": updated_at,
            "i": conversation_id,
        }
    )


def decode_conversation_cursor(
    value: str,
) -> tuple[str, int | None, str | None, str, str]:
    """Return (sort, priority_rank, sort_key, updated_at, conversation_id)."""
    payload = _decode_cursor_payload(value, label="conversation cursor")
    version = payload.get("v")
    conversation_id = payload.get("i")
    updated_at = payload.get("u")
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 160:
        raise InvalidCursorError("Invalid conversation cursor")
    if version == 1:
        priority_rank = payload.get("p")
        if (
            isinstance(priority_rank, bool)
            or not isinstance(priority_rank, int)
            or priority_rank not in {0, 1}
            or not isinstance(updated_at, str)
            or not updated_at
        ):
            raise InvalidCursorError("Invalid conversation cursor")
        return "priority", priority_rank, None, updated_at, conversation_id
    if version == 2:
        sort = payload.get("s")
        sort_key = payload.get("k")
        if (
            sort not in SUPPORTED_QUEUE_SORTS - {"priority"}
            or not isinstance(sort_key, str)
            or len(sort_key) > 64
            or not isinstance(updated_at, str)
            or not updated_at
        ):
            raise InvalidCursorError("Invalid conversation cursor")
        return str(sort), None, sort_key, updated_at, conversation_id
    raise InvalidCursorError("Unsupported conversation cursor")


def encode_message_cursor(created_at: str, seq: int) -> str:
    """Encode a message cursor keyed on the monotonic ordering column.

    ``seq`` is the message's insertion-order tiebreaker (``rowid`` on SQLite,
    the ``seq`` column on PostgreSQL), which makes pagination stable even when
    two messages share a ``created_at`` timestamp.
    """
    return _encode_cursor_payload({"v": 2, "t": created_at, "s": seq})


def decode_message_cursor(value: str) -> tuple[str, int]:
    payload = _decode_cursor_payload(value, label="message cursor")
    if payload.get("v") != 2:
        raise InvalidCursorError("Unsupported message cursor")
    created_at = payload.get("t")
    seq = payload.get("s")
    if (
        not isinstance(created_at, str)
        or not created_at
        or isinstance(seq, bool)
        or not isinstance(seq, int)
        or seq < 0
    ):
        raise InvalidCursorError("Invalid message cursor")
    return created_at, seq
