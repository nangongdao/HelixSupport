"""UTF-8 stdout/stderr for the gate scripts (shared by every CJK-emitting gate).

Every gate in this directory reports violations in Chinese. On Windows a
console defaults to a GBK code page, and Python encodes stdout with it, which
broke the gates two different ways:

1. **Unreadable output.** ``tauri_config_gate --allow-empty-pubkey`` printed its
   exemption warning as ``plugins.updater.pubkey Ϊ��`` — the operator cannot
   act on a warning they cannot read, and the whole point of the flag is that
   the exemption stays visible at the call site.
2. **A crash instead of a verdict.** When a violation string carried a U+FFFD
   (from a child process whose output was mis-decoded), printing it raised
   ``UnicodeEncodeError``. ``image_admission_check`` died on a traceback while
   reporting a real violation, so CI saw an unhandled exception rather than the
   ``exit 1`` it reads. A gate that cannot print its own failure cannot fail.

``errors="replace"`` keeps (2) closed even if a stream cannot represent a
character: the gate still reports and still exits non-zero.

Call :func:`use_utf8_console` from ``__main__`` only. Doing it at import time
would mutate global state for the pytest callers that import these modules as
libraries (``tests/test_vuln_review.py`` et al), which is not theirs to change.
"""

from __future__ import annotations

import sys
from typing import IO, Any


def use_utf8_console(*streams: IO[Any]) -> None:
    """Re-encode the given streams (default: stdout+stderr) as UTF-8.

    A stream that cannot be reconfigured — already-wrapped, redirected to a
    non-text sink, or replaced by a test harness — is skipped rather than
    raising: making the console readable must never itself break the gate.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
