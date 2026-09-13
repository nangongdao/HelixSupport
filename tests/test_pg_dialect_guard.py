"""Source guard: SQLite-only statements must not reach the shared query layer.

The persistence layer is written once against ``sqlite3`` and adapted to
PostgreSQL by :mod:`app.pg_dialect`, so any statement it emits has to be valid
on *both* backends — or be explicitly translated.  Two constructs have shipped
without that property and each was invisible to the default test run:

* SQLite's two-argument ``date(x, modifier)`` and ``MIN/MAX`` over ``COUNT(*)``
  broke the cost analytics query on real PostgreSQL (2.3.0).  That regression
  has its own guard next to the code it protects
  (``tests/test_cost_attribution.py::CostDialectPortabilityTests``).
* SQLite's ``INSERT OR REPLACE`` broke the cross-cell replication ingress: it
  is a syntax error on PostgreSQL, and on SQLite it deletes the conflicting row
  before re-inserting it, so every column the source never sent reverts to its
  default on the target cell.  Guarded here, because the defect is about the
  dialect surface rather than any single feature.

The dialect layer now rejects ``INSERT OR REPLACE`` instead of forwarding it,
and this guard keeps new call sites from introducing it in the first place.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import unittest
from pathlib import Path

from app.pg_dialect import PostgresConnection, translate

# The statement form, not the words: the dialect module and the code comments
# legitimately *name* the construct while explaining why it is banned.
_INSERT_OR_REPLACE_INTO = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)

_REPLACEMENT_HINT = (
    "SQLite-only and destructive on conflict (REPLACE deletes the conflicting "
    "row, so columns the source never sent revert to their defaults). Rewrite "
    "it as INSERT ... ON CONFLICT (...) DO UPDATE, which SQLite >= 3.24 and "
    "PostgreSQL both accept."
)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """Ids of string constants that are docstrings rather than SQL literals."""
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


class BannedStatementSourceGuardTests(unittest.TestCase):
    """``app/`` must not contain an ``INSERT OR REPLACE`` statement."""

    def test_the_guard_keeps_its_teeth(self) -> None:
        self.assertIsNotNone(
            _INSERT_OR_REPLACE_INTO.search("INSERT OR REPLACE INTO conversations (id) VALUES (?)")
        )
        # The portable spelling must not trip the guard.
        self.assertIsNone(
            _INSERT_OR_REPLACE_INTO.search(
                "INSERT INTO conversations (id) VALUES (?) "
                "ON CONFLICT(id) DO UPDATE SET preview = excluded.preview"
            )
        )

    def test_app_contains_no_insert_or_replace_statement(self) -> None:
        app_dir = Path(__file__).resolve().parents[1] / "app"
        offenders: list[str] = []
        for path in sorted(app_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = _docstring_constant_ids(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if _INSERT_OR_REPLACE_INTO.search(node.value):
                    offenders.append(
                        f"{path.relative_to(app_dir.parent)}:{node.lineno} "
                        f"{node.value.strip()[:60]!r}"
                    )
        self.assertEqual(offenders, [], f"{_REPLACEMENT_HINT}\nOffenders: {offenders}")


class InsertOrReplaceRejectionTests(unittest.TestCase):
    """The dialect layer must fail loudly, not emit invalid PostgreSQL."""

    SQL = "INSERT OR REPLACE INTO conversations (id, tenant_id) VALUES (?, ?)"

    def test_translate_reports_a_reject_directive(self) -> None:
        sql, directive = translate(self.SQL, True)
        self.assertEqual(sql, self.SQL)
        self.assertIsNotNone(directive)
        assert directive is not None
        self.assertTrue(directive.startswith("reject:"), directive)
        self.assertIn("ON CONFLICT", directive)

    def test_insert_or_ignore_is_still_translated(self) -> None:
        """The reject must not swallow the neighbouring, translatable form."""
        sql, directive = translate("INSERT OR IGNORE INTO conversations (id) VALUES (?)", True)
        self.assertIsNone(directive)
        self.assertIn("ON CONFLICT DO NOTHING", sql)

    def test_connection_refuses_to_execute_it(self) -> None:
        # ``PostgresConnection`` translates before it ever touches the socket,
        # so a stub raw connection is enough to pin the failure.
        connection = PostgresConnection(None)
        with self.assertRaises(sqlite3.OperationalError) as raised:
            connection.execute(self.SQL, ("conv-1", "demo"))
        self.assertIn("ON CONFLICT", str(raised.exception))

    def test_connection_refuses_it_in_executemany_too(self) -> None:
        connection = PostgresConnection(None)
        with self.assertRaises(sqlite3.OperationalError) as raised:
            connection.executemany(self.SQL, [("conv-1", "demo")])
        self.assertIn("ON CONFLICT", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
