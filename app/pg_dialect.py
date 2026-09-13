"""SQLite-to-PostgreSQL dialect adapter.

The persistence layer in :mod:`app.database` is written against the ``sqlite3``
DB-API with ``?`` placeholders.  Rather than maintaining a second, hand-written
copy of ~60 query methods for PostgreSQL (which silently drifts out of sync),
this module adapts a psycopg2 connection so that the *same* SQL runs on
PostgreSQL.

The adapter handles:

* ``?`` -> ``%s`` placeholder translation (string-literal aware).
* ``INSERT OR IGNORE`` -> ``INSERT ... ON CONFLICT DO NOTHING``.
* ``INSERT OR REPLACE`` -> rejected with a diagnostic ``reject`` directive.
  PostgreSQL has no such syntax, and the SQLite form is destructive on
  conflict (it deletes the row and re-inserts it, dropping every column the
  source never supplied), so silently forwarding it would either fail deep in
  the server or quietly discard target-side state.  Call sites must spell the
  upsert portably as ``INSERT ... ON CONFLICT (...) DO UPDATE``, which both
  backends accept.
* ``PRAGMA`` statements -> no-ops (or ``information_schema`` lookups).
* ``sqlite_master`` introspection -> ``information_schema``.
* SQLite ``CREATE TRIGGER``/FTS5 DDL -> skipped or rejected, so the caller's
  existing fallback paths engage (PostgreSQL-native triggers are installed
  separately by :mod:`app.postgres_db`).
* psycopg2 exceptions -> ``sqlite3`` exception types, so existing
  ``except sqlite3.IntegrityError`` handlers keep working.

Every statement is wrapped in a ``SAVEPOINT`` so that a caught error does not
poison the surrounding transaction, matching SQLite's behaviour where a failed
statement leaves the transaction usable.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "PostgresConnection",
    "StatementResult",
    "split_statements",
    "translate",
]


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------

_PRAGMA_TABLE_INFO = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", re.IGNORECASE
)
_SQLITE_MASTER = re.compile(r"\bsqlite_master\b", re.IGNORECASE)
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)
# Emitted instead of the statement when the construct cannot be made portable.
# ``execute``/``executemany`` turn it into a hard failure rather than emitting
# SQL the server will reject with a bare syntax error.  This only guards the
# PostgreSQL backend — the SQLite path talks to sqlite3 directly — so the
# app-wide ban is enforced at build time by the source guard in
# ``tests/test_pg_dialect_guard.py``.
_REJECT_INSERT_OR_REPLACE = (
    "reject:INSERT OR REPLACE is SQLite-only and destructive on conflict "
    "(it deletes the conflicting row, so columns the source never sent revert "
    "to their defaults). Rewrite it as INSERT ... ON CONFLICT (...) DO UPDATE, "
    "which SQLite >= 3.24 and PostgreSQL both accept."
)
_COLLATE_NOCASE = re.compile(r"\s+COLLATE\s+NOCASE\b", re.IGNORECASE)
_CREATE_TRIGGER = re.compile(r"^\s*CREATE\s+TRIGGER\b", re.IGNORECASE)
_VIRTUAL_TABLE = re.compile(r"^\s*CREATE\s+VIRTUAL\s+TABLE\b", re.IGNORECASE)
_PRAGMA_ANY = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)
# SQLite's ``BEGIN IMMEDIATE`` takes the database write lock up front so that
# a read-then-write sequence cannot interleave with another writer.  psycopg2
# already opens a transaction implicitly, so the missing piece is the
# serialization: a transaction-scoped advisory lock provides it and is
# released automatically on commit or rollback.
_BEGIN_MODE = re.compile(r"^\s*BEGIN\s+(IMMEDIATE|EXCLUSIVE)\s*;?\s*$", re.IGNORECASE)
_BEGIN_DEFERRED = re.compile(r"^\s*BEGIN\s*(DEFERRED)?\s*;?\s*$", re.IGNORECASE)
_WRITE_LOCK_KEY = 0x48454C58  # 'HELX'
_WRITE_LOCK_SQL = f"SELECT pg_advisory_xact_lock({_WRITE_LOCK_KEY})"
# SQLite treats integers as truthy in a boolean context; PostgreSQL demands a
# real boolean.  A bare ``CASE WHEN ?`` (placeholder used directly as the
# condition) therefore needs an explicit cast.
_CASE_WHEN_PARAM = re.compile(r"\bCASE\s+WHEN\s+\?(?=\s+THEN\b)", re.IGNORECASE)
# SQLite's implicit rowid is the monotonic insertion-order tiebreaker used in
# ORDER BY clauses.  PostgreSQL has no rowid, so :mod:`app.pg_compat` adds a
# monotonic ``seq`` columns on tables whose shared queries use ``rowid``;
# rewriting the identifier keeps ordering deterministic on both backends.
_ROWID = re.compile(r"\browid\b", re.IGNORECASE)

# ``PRAGMA table_info`` rows are consumed both positionally (``row[1]`` is the
# column name) and by key (``row["name"]``), so the projection order matters.
_TABLE_INFO_SQL = """
SELECT (ordinal_position - 1) AS cid,
       column_name            AS name,
       data_type              AS type,
       CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
       column_default         AS dflt_value,
       0                      AS pk
FROM information_schema.columns
WHERE table_schema = current_schema() AND table_name = %s
ORDER BY ordinal_position
"""

_SQLITE_MASTER_SQL = """
(SELECT table_name AS name, 'table' AS type FROM information_schema.tables
  WHERE table_schema = current_schema()
 UNION ALL
 SELECT indexname AS name, 'index' AS type FROM pg_indexes
  WHERE schemaname = current_schema())
"""


def _convert_placeholders(sql: str, has_params: bool) -> str:
    """Rewrite ``?`` placeholders to ``%s``, ignoring quoted string literals.

    When parameters are supplied, literal ``%`` must be doubled so psycopg2's
    interpolation leaves it intact.
    """
    out: list[str] = []
    quote: str | None = None
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if quote:
            if char == quote:
                # A doubled quote is an escaped quote, not a terminator.
                if index + 1 < length and sql[index + 1] == quote:
                    out.append(char * 2)
                    index += 2
                    continue
                quote = None
            # ``%`` must be escaped even inside literals: psycopg2 interpolates
            # over the whole statement, not just outside quotes.
            out.append("%%" if char == "%" and has_params else char)
            index += 1
            continue

        if char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        elif char == "%" and has_params:
            out.append("%%")
        else:
            out.append(char)
        index += 1

    return "".join(out)


# ---------------------------------------------------------------------------
# ON CONFLICT … DO UPDATE self-reference qualification
# ---------------------------------------------------------------------------

# Some PostgreSQL server configurations resolve an unqualified column reference
# in ``DO UPDATE SET`` against both the target row and the ``excluded`` row,
# so the bare ``SET col = col + 1`` every SQLite increment uses is reported as
# ambiguous while SQLite accepts it.  The dialect layer qualifies the
# self-referencing column with the INSERT target table (``excluded.`` /
# ``schema.table.`` references are already portable and are left alone).
_UPSERT_INSERT_TABLE = re.compile(r"\bINSERT INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_UPSERT_DO_SET = re.compile(r"\bDO UPDATE SET\b", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=")


def _qualify_upsert_self_references(sql: str) -> str:
    """Make ``ON CONFLICT ... DO UPDATE SET`` self-increments portable.

    Rewrites ``SET turn_count = turn_count + 1`` as
    ``SET turn_count = tbl.turn_count + 1``; PostgreSQL rejects the bare form.
    Selector constraint: a SET assignment expression must not contain a
    top-level comma (every current call site is a plain ``col +/-`` / ``+ k``
    expression).
    """
    table_match = _UPSERT_INSERT_TABLE.search(sql)
    set_match = _UPSERT_DO_SET.search(sql)
    if not table_match or not set_match:
        return sql
    table = table_match.group(1)
    head = sql[: set_match.end()]
    tail = sql[set_match.end() :]
    assignments = tail.split(",")
    for index, item in enumerate(assignments):
        column = _ASSIGNMENT_RE.match(item)
        if not column:
            continue
        name = column.group(1)
        rhs = item[column.end() :]
        bare = re.compile(r"(?<![\w.])" + re.escape(name) + r"(?!\w)")
        if bare.search(rhs):
            assignments[index] = item[: column.end()] + bare.sub(f"{table}.{name}", rhs)
    return head + ",".join(assignments)


def _rejection(directive: str | None) -> str | None:
    """Return the reason carried by a ``reject`` directive, if there is one."""
    if directive and directive.startswith("reject:"):
        return directive.split(":", 1)[1]
    return None


def translate(sql: str, has_params: bool = False) -> tuple[str, str | None]:
    """Translate one SQLite statement to PostgreSQL.

    Returns ``(sql, directive)`` where ``directive`` is ``"skip"`` for
    statements PostgreSQL neither needs nor understands (SQLite triggers,
    ``PRAGMA`` tuning knobs), ``"unsupported"`` for constructs whose failure
    the caller is expected to handle (FTS5 virtual tables), or
    ``"reject:<reason>"`` for SQL that must never reach either backend.
    """
    stripped = sql.strip()
    if not stripped:
        return "", "skip"

    if _VIRTUAL_TABLE.match(stripped):
        return stripped, "unsupported"
    if _BEGIN_MODE.match(stripped):
        return _WRITE_LOCK_SQL, None
    if _BEGIN_DEFERRED.match(stripped):
        return stripped, "skip"
    if _CREATE_TRIGGER.match(stripped):
        # PostgreSQL-native equivalents are installed by app.postgres_db.
        return stripped, "skip"

    match = _PRAGMA_TABLE_INFO.match(stripped)
    if match:
        return _TABLE_INFO_SQL, f"table_info:{match.group(1)}"
    if _PRAGMA_ANY.match(stripped):
        return stripped, "skip"

    if _SQLITE_MASTER.search(stripped):
        stripped = _SQLITE_MASTER.sub(_SQLITE_MASTER_SQL + " AS sqlite_master", stripped)

    stripped = _COLLATE_NOCASE.sub("", stripped)
    stripped = _ROWID.sub("seq", stripped)
    stripped = _CASE_WHEN_PARAM.sub("CASE WHEN (?)::int::boolean", stripped)

    append_on_conflict = False
    if _INSERT_OR_REPLACE.match(stripped):
        return stripped, _REJECT_INSERT_OR_REPLACE
    if _INSERT_OR_IGNORE.match(stripped):
        stripped = _INSERT_OR_IGNORE.sub("INSERT INTO", stripped, count=1)
        append_on_conflict = True

    stripped = _convert_placeholders(stripped, has_params)

    if append_on_conflict:
        stripped = stripped.rstrip().rstrip(";")
        if "ON CONFLICT" not in stripped.upper():
            stripped += " ON CONFLICT DO NOTHING"

    stripped = _qualify_upsert_self_references(stripped)

    return stripped, None


def split_statements(script: str) -> list[str]:
    """Split a multi-statement script, honouring quotes and block nesting.

    SQLite trigger bodies contain semicolons, so a naive ``split(";")`` would
    tear them apart.  Both ``BEGIN`` (trigger body) and ``CASE`` (expression)
    open a block terminated by ``END``, and trigger bodies routinely contain
    ``CASE`` expressions, so the two must share one depth counter.
    """
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    depth = 0
    index = 0
    length = len(script)

    while index < length:
        char = script[index]

        if quote:
            buffer.append(char)
            if char == quote:
                if index + 1 < length and script[index + 1] == quote:
                    buffer.append(script[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue

        keyword = _match_keyword(script, index)
        if keyword in ("BEGIN", "CASE"):
            depth += 1
            buffer.append(script[index : index + len(keyword)])
            index += len(keyword)
            continue
        if keyword == "END" and depth:
            depth -= 1
            buffer.append(script[index : index + 3])
            index += 3
            continue

        buffer.append(char)
        if char == ";" and depth == 0:
            statements.append("".join(buffer).strip())
            buffer = []
        index += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s.strip().rstrip(";").strip()]


def _match_keyword(text: str, start: int) -> str | None:
    """Return the block keyword at ``start``, if it stands as a whole word."""
    upper = text[start : start + 5].upper()
    for keyword in ("BEGIN", "CASE", "END"):
        if upper.startswith(keyword) and _is_word_boundary(text, start, len(keyword)):
            return keyword
    return None


def _is_word_boundary(text: str, start: int, length: int) -> bool:
    before = text[start - 1] if start else " "
    after = text[start + length] if start + length < len(text) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


# ---------------------------------------------------------------------------
# DB-API shim
# ---------------------------------------------------------------------------


class StatementResult:
    """Eagerly-materialised result, mimicking a ``sqlite3.Cursor``.

    Rows are buffered at execute time so callers may read ``rowcount`` or
    fetch results after the connection has been returned to the pool, which
    :mod:`app.database` does in several places.
    """

    __slots__ = ("_index", "_rows", "lastrowid", "rowcount")

    def __init__(self, rows: list[Any], rowcount: int) -> None:
        self._rows = rows
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = None

    def fetchone(self) -> Any:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def fetchmany(self, size: int = 1) -> list[Any]:
        rows = self._rows[self._index : self._index + size]
        self._index += len(rows)
        return rows

    def __iter__(self) -> Any:
        return iter(self.fetchall())


def _map_exception(exc: Exception) -> Exception:
    """Map psycopg2 errors onto the ``sqlite3`` types callers already catch."""
    import psycopg2

    if isinstance(exc, psycopg2.IntegrityError):
        return sqlite3.IntegrityError(str(exc))
    if isinstance(exc, (psycopg2.ProgrammingError, psycopg2.OperationalError, psycopg2.DataError)):
        return sqlite3.OperationalError(str(exc))
    if isinstance(exc, psycopg2.Error):
        return sqlite3.DatabaseError(str(exc))
    return exc


class PostgresConnection:
    """Adapts a psycopg2 connection to the ``sqlite3.Connection`` surface used
    by :class:`app.database.Database`."""

    _SAVEPOINT = "helix_stmt"

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.row_factory = None

    # -- core API ---------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> StatementResult:
        translated, directive = translate(sql, params is not None)
        if directive == "skip":
            return StatementResult([], 0)
        if directive == "unsupported":
            raise sqlite3.OperationalError(f"unsupported on postgresql: {sql.strip()[:60]}")
        rejection = _rejection(directive)
        if rejection is not None:
            raise sqlite3.OperationalError(rejection)
        if directive and directive.startswith("table_info:"):
            params = (directive.split(":", 1)[1],)

        return self._execute(translated, params)

    def _execute(self, sql: str, params: Sequence[Any] | None) -> StatementResult:
        from psycopg2.extras import DictCursor

        cursor = self._raw.cursor(cursor_factory=DictCursor)
        try:
            cursor.execute(f"SAVEPOINT {self._SAVEPOINT}")
            try:
                cursor.execute(sql, tuple(params) if params is not None else None)
                rows = cursor.fetchall() if cursor.description is not None else []
                result = StatementResult(list(rows), cursor.rowcount)
            except Exception as exc:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
                raise _map_exception(exc) from exc
            cursor.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            return result
        finally:
            cursor.close()

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> StatementResult:
        rows = list(seq_of_params)
        if not rows:
            return StatementResult([], 0)
        translated, directive = translate(sql, True)
        rejection = _rejection(directive)
        if rejection is not None:
            raise sqlite3.OperationalError(rejection)
        if directive in ("skip", "unsupported"):
            return StatementResult([], 0)

        from psycopg2.extras import DictCursor

        cursor = self._raw.cursor(cursor_factory=DictCursor)
        try:
            cursor.execute(f"SAVEPOINT {self._SAVEPOINT}")
            try:
                cursor.executemany(translated, [tuple(item) for item in rows])
                result = StatementResult([], cursor.rowcount)
            except Exception as exc:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
                raise _map_exception(exc) from exc
            cursor.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            return result
        finally:
            cursor.close()

    def executescript(self, script: str) -> StatementResult:
        for statement in split_statements(script):
            self.execute(statement)
        return StatementResult([], 0)

    # -- transaction / lifecycle -----------------------------------------
    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:  # pragma: no cover - best effort
            logger.debug("Failed to close PostgreSQL connection", exc_info=True)

    def create_function(self, *args: Any, **kwargs: Any) -> None:
        """No-op: SQL-level compatibility functions are installed server-side."""

    @property
    def raw(self) -> Any:
        return self._raw
