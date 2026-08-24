from __future__ import annotations

from collections.abc import Sequence


def normalize_conversation_labels(values: Sequence[str], max_labels: int = 20) -> list[str]:
    normalized: list[str] = []
    for value in values:
        label = value.strip().casefold()
        if not label or len(label) > 32 or not label.isprintable():
            raise ValueError("labels must be printable values up to 32 characters")
        if label not in normalized:
            normalized.append(label)
    if len(normalized) > max_labels:
        raise ValueError(f"a conversation can have at most {max_labels} labels")
    return normalized
