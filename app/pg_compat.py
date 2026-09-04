"""PostgreSQL server-side compatibility layer.

:mod:`app.database` writes SQLite SQL.  Most of it is portable, but a handful
of SQLite built-ins and all of its triggers have no direct PostgreSQL
equivalent.  This module installs:

* SQL functions emulating the SQLite built-ins used by the query layer
  (``json_extract``, ``json_valid``, ``json_array_length``, ``typeof``,
  ``julianday``) plus a two-argument ``max``/``min`` (SQLite's scalar form,
  which PostgreSQL spells ``GREATEST``/``LEAST``).
* PL/pgSQL equivalents of the SQLite triggers that maintain conversation
  summary columns and enforce tenant integrity.
* The ``pg_trgm`` extension and a trigram GIN index over ``messages.content``,
  so the shared LIKE-based message-search fallback can use an index-backed
  bitmap scan instead of a full-table scan (ROADMAP 18.2 / CAPACITY 3.2).
* A tenant/updated/id ordering index used by queue paging and the bounded
  dense-message search window (ROADMAP 18.5).

Installing these lets the shared SQLite query implementation run unmodified on
PostgreSQL, so there is exactly one copy of the business logic.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "COMPAT_FUNCTIONS",
    "TRIGGER_DDL",
    "install_archive_trgm_search",
    "install_archive_updated_sort_index",
    "install_compatibility",
    "install_csat_summary_index",
    "install_functions",
    "install_ordering_columns",
    "install_trgm_search",
    "install_triggers",
    "install_updated_sort_index",
]


# ---------------------------------------------------------------------------
# Scalar function shims
# ---------------------------------------------------------------------------

COMPAT_FUNCTIONS: tuple[str, ...] = (
    # SQLite's json_extract with a '$.key' path, returning the value as text.
    """
    CREATE OR REPLACE FUNCTION json_extract(doc text, path text)
    RETURNS text AS $$
    BEGIN
        RETURN doc::jsonb ->> regexp_replace(path, '^\\$\\.', '');
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    # jsonb-returning variant, needed where the result feeds json_array_length.
    """
    CREATE OR REPLACE FUNCTION json_extract_json(doc text, path text)
    RETURNS jsonb AS $$
    BEGIN
        RETURN doc::jsonb -> regexp_replace(path, '^\\$\\.', '');
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    """
    CREATE OR REPLACE FUNCTION json_valid(doc text)
    RETURNS int AS $$
    BEGIN
        PERFORM doc::jsonb;
        RETURN 1;
    EXCEPTION WHEN others THEN
        RETURN 0;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    """
    CREATE OR REPLACE FUNCTION json_array_length(doc jsonb)
    RETURNS int AS $$
    BEGIN
        RETURN jsonb_array_length(doc);
    EXCEPTION WHEN others THEN
        RETURN 0;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    """
    CREATE OR REPLACE FUNCTION json_array_length(doc text)
    RETURNS int AS $$
    BEGIN
        RETURN jsonb_array_length(doc::jsonb);
    EXCEPTION WHEN others THEN
        RETURN 0;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    # SQLite's typeof(), used to guard numeric casts on JSON values.
    """
    CREATE OR REPLACE FUNCTION typeof(value text)
    RETURNS text AS $$
    BEGIN
        IF value IS NULL THEN RETURN 'null'; END IF;
        IF value ~ '^-?[0-9]+$' THEN RETURN 'integer'; END IF;
        IF value ~ '^-?([0-9]*\\.)?[0-9]+([eE][-+]?[0-9]+)?$' THEN RETURN 'real'; END IF;
        RETURN 'text';
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    # Julian day number, so elapsed-time arithmetic ports unchanged.
    """
    CREATE OR REPLACE FUNCTION julianday(ts text)
    RETURNS double precision AS $$
    BEGIN
        RETURN EXTRACT(EPOCH FROM ts::timestamptz) / 86400.0 + 2440587.5;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END $$ LANGUAGE plpgsql IMMUTABLE
    """,
    # SQLite allows scalar MAX()/MIN(); PostgreSQL reserves those for aggregates.
    """
    CREATE OR REPLACE FUNCTION max(a int, b int) RETURNS int AS $$
        SELECT GREATEST(a, b) $$ LANGUAGE sql IMMUTABLE
    """,
    """
    CREATE OR REPLACE FUNCTION min(a int, b int) RETURNS int AS $$
        SELECT LEAST(a, b) $$ LANGUAGE sql IMMUTABLE
    """,
    # SQLite aggregates a column into a JSON array.  PostgreSQL's json_agg is
    # the equivalent but returns NULL (not '[]') for an empty set, so build an
    # aggregate of the same name that yields text and never NULL.
    """
    CREATE OR REPLACE FUNCTION hx_json_array_step(state jsonb, value anyelement)
    RETURNS jsonb AS $$
        SELECT COALESCE(state, '[]'::jsonb) || to_jsonb(value)
    $$ LANGUAGE sql IMMUTABLE
    """,
    """
    CREATE OR REPLACE FUNCTION hx_json_array_final(state jsonb)
    RETURNS text AS $$
        SELECT COALESCE(state, '[]'::jsonb)::text
    $$ LANGUAGE sql IMMUTABLE
    """,
    """
    DROP AGGREGATE IF EXISTS json_group_array(anyelement)
    """,
    """
    CREATE AGGREGATE json_group_array(anyelement) (
        SFUNC = hx_json_array_step,
        STYPE = jsonb,
        FINALFUNC = hx_json_array_final,
        INITCOND = '[]'
    )
    """,
    # SQLite's 1-arg substr(): PG's overload returns the tail from ``start``;
    # we want the same behaviour for the CSAT trend's date truncation.
    """
    CREATE OR REPLACE FUNCTION substr(text, int) RETURNS text AS $$
        SELECT substr($1, $2, character_length($1))
    $$ LANGUAGE sql IMMUTABLE
    """,
)


# ---------------------------------------------------------------------------
# Trigger equivalents
# ---------------------------------------------------------------------------

TRIGGER_DDL: tuple[str, ...] = (
    # -- tenant integrity guards -----------------------------------------
    """
    CREATE OR REPLACE FUNCTION hx_conversation_tenant_guard()
    RETURNS trigger AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.id = NEW.conversation_id AND c.tenant_id = NEW.tenant_id
        ) THEN
            RAISE EXCEPTION 'conversation tenant mismatch';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION hx_feedback_tenant_guard()
    RETURNS trigger AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM messages m
            WHERE m.id = NEW.message_id
              AND m.conversation_id = NEW.conversation_id
              AND m.tenant_id = NEW.tenant_id
        ) THEN
            RAISE EXCEPTION 'message tenant mismatch';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    # -- conversation summary maintenance --------------------------------
    """
    CREATE OR REPLACE FUNCTION hx_messages_after_insert()
    RETURNS trigger AS $$
    BEGIN
        UPDATE conversations SET
            message_count = message_count + 1,
            preview = CASE
                WHEN last_message_at IS NULL OR NEW.created_at >= last_message_at
                THEN NEW.content ELSE preview END,
            last_message_at = CASE
                WHEN last_message_at IS NULL OR NEW.created_at >= last_message_at
                THEN NEW.created_at ELSE last_message_at END,
            updated_at = CASE
                WHEN NEW.created_at >= updated_at THEN NEW.created_at ELSE updated_at END,
            version = version + 1
        WHERE tenant_id = NEW.tenant_id AND id = NEW.conversation_id;

        IF NEW.role IN ('customer', 'assistant', 'operator') THEN
            UPDATE conversations SET
                needs_response = CASE WHEN NEW.role = 'customer' THEN 1 ELSE 0 END,
                waiting_since = CASE
                    WHEN NEW.role = 'customer' THEN NEW.created_at ELSE NULL END,
                first_response_at = CASE
                    WHEN NEW.role IN ('assistant', 'operator')
                    THEN COALESCE(first_response_at, NEW.created_at)
                    ELSE first_response_at END
            WHERE tenant_id = NEW.tenant_id AND id = NEW.conversation_id;
        END IF;
        RETURN NULL;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION hx_messages_after_delete()
    RETURNS trigger AS $$
    BEGIN
        UPDATE conversations SET
            message_count = GREATEST(0, message_count - 1),
            preview = (
                SELECT content FROM messages
                WHERE tenant_id = OLD.tenant_id
                  AND conversation_id = OLD.conversation_id
                ORDER BY created_at DESC, seq DESC LIMIT 1
            ),
            last_message_at = (
                SELECT created_at FROM messages
                WHERE tenant_id = OLD.tenant_id
                  AND conversation_id = OLD.conversation_id
                ORDER BY created_at DESC, seq DESC LIMIT 1
            ),
            version = version + 1
        WHERE tenant_id = OLD.tenant_id AND id = OLD.conversation_id;

        IF OLD.role IN ('customer', 'assistant', 'operator') THEN
            UPDATE conversations SET
                needs_response = CASE WHEN (
                    SELECT role FROM messages
                    WHERE tenant_id = OLD.tenant_id
                      AND conversation_id = OLD.conversation_id
                    ORDER BY created_at DESC, seq DESC LIMIT 1
                ) = 'customer' THEN 1 ELSE 0 END,
                waiting_since = (
                    SELECT created_at FROM messages
                    WHERE tenant_id = OLD.tenant_id
                      AND conversation_id = OLD.conversation_id
                      AND role = 'customer'
                    ORDER BY created_at DESC, seq DESC LIMIT 1
                )
            WHERE tenant_id = OLD.tenant_id AND id = OLD.conversation_id;
        END IF;
        RETURN NULL;
    END $$ LANGUAGE plpgsql
    """,
)

# (trigger name, table, timing, function)
_TRIGGER_BINDINGS: tuple[tuple[str, str, str, str], ...] = (
    ("messages_tenant_guard", "messages", "BEFORE INSERT", "hx_conversation_tenant_guard"),
    ("turn_jobs_tenant_guard", "turn_jobs", "BEFORE INSERT", "hx_conversation_tenant_guard"),
    (
        "channel_threads_tenant_guard",
        "channel_threads",
        "BEFORE INSERT",
        "hx_conversation_tenant_guard",
    ),
    (
        "channel_receipts_tenant_guard",
        "channel_webhook_receipts",
        "BEFORE INSERT",
        "hx_conversation_tenant_guard",
    ),
    (
        "conversation_labels_tenant_guard",
        "conversation_labels",
        "BEFORE INSERT",
        "hx_conversation_tenant_guard",
    ),
    ("feedback_tenant_guard", "feedback", "BEFORE INSERT", "hx_feedback_tenant_guard"),
    ("messages_summary_insert", "messages", "AFTER INSERT", "hx_messages_after_insert"),
    ("messages_summary_delete", "messages", "AFTER DELETE", "hx_messages_after_delete"),
)


# ---------------------------------------------------------------------------
# Monotonic ordering columns
# ---------------------------------------------------------------------------

# SQLite orders ``ORDER BY ... rowid`` with its native per-row insertion
# counter.  PostgreSQL has no rowid, so these statements add a monotonic
# ``seq`` column to the tables that order by it (the dialect rewrites ``rowid``
# to ``seq``).  ``seq`` is filled from a sequence so concurrent writers can
# never receive the same value.  Must run after the tables exist and before any
# write reaches the trigger functions or shared queries that use ``seq``.
_ORDERING_DDL: tuple[str, ...] = (
    """
    CREATE SEQUENCE IF NOT EXISTS messages_seq START 1
    """,
    """
    ALTER TABLE messages ADD COLUMN IF NOT EXISTS seq BIGINT NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE messages ALTER COLUMN seq SET DEFAULT nextval('messages_seq')
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS audit_events_seq START 1
    """,
    """
    ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS seq BIGINT NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE audit_events ALTER COLUMN seq SET DEFAULT nextval('audit_events_seq')
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS turn_job_chunks_seq START 1
    """,
    """
    ALTER TABLE turn_job_chunks ADD COLUMN IF NOT EXISTS seq BIGINT NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE turn_job_chunks ALTER COLUMN seq SET DEFAULT nextval('turn_job_chunks_seq')
    """,
    """
    CREATE SEQUENCE IF NOT EXISTS knowledge_articles_seq START 1
    """,
    """
    ALTER TABLE knowledge_articles ADD COLUMN IF NOT EXISTS seq BIGINT NOT NULL DEFAULT 0
    """,
    """
    UPDATE knowledge_articles
       SET seq = nextval('knowledge_articles_seq')
     WHERE seq = 0
    """,
    """
    ALTER TABLE knowledge_articles ALTER COLUMN seq SET DEFAULT nextval('knowledge_articles_seq')
    """,
)


def install_ordering_columns(connection: Any) -> None:
    """Add the monotonic ``seq`` tiebreaker columns PostgreSQL lacks.

    Requires the tables to already exist; idempotent.  Must be installed before
    the trigger functions query ``seq`` at runtime.
    """
    _run(connection, _ORDERING_DDL)
    logger.info("PostgreSQL ordering columns installed")


def install_functions(connection: Any) -> None:
    """Install the SQLite-builtin function shims.

    ``connection`` is a raw psycopg2 connection.  Safe to call repeatedly.
    Must run before any schema DDL, whose backfill queries call these.
    """
    _run(connection, COMPAT_FUNCTIONS)
    logger.info("PostgreSQL compatibility functions installed")


def install_triggers(connection: Any) -> None:
    """Install the trigger functions and bind them to their tables.

    Requires the schema to already exist.  Safe to call repeatedly.
    """
    statements: list[str] = list(TRIGGER_DDL)
    for name, table, timing, function in _TRIGGER_BINDINGS:
        statements.append(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        statements.append(
            f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    _run(connection, statements)
    logger.info("PostgreSQL compatibility triggers installed")


# ---------------------------------------------------------------------------
# Trigram message search (ROADMAP 18.2)
# ---------------------------------------------------------------------------

_TRGM_EXTENSION = "CREATE EXTENSION IF NOT EXISTS pg_trgm"
_TRGM_INDEX_DDL = (
    (
        "CREATE INDEX IF NOT EXISTS idx_messages_content_trgm "
        "ON messages USING gin (content gin_trgm_ops)"
    ),
)


def install_trgm_search(connection: Any) -> None:
    """Create the ``pg_trgm`` extension and a trigram GIN index on ``messages``.

    PostgreSQL has no ``message_fts`` mirror (see
    :meth:`app.postgres_db.PostgresDatabase._initialize_message_fts`), so
    message search runs the shared LIKE fallback — a ``content LIKE '%term%'``
    scan that is a full-table scan over millions of rows and misses the §18.5
    acceptance line (CAPACITY 3.2: full-match worst case > 500ms).  A trigram
    GIN index lets that predicate use an index-backed bitmap scan for needles
    of at least three characters, converging the worst-hit path.

    Best-effort by design: if the extension is unavailable (no
    ``postgresql-contrib``) or the role lacks permission to create it, log a
    warning and leave search on the unaccelerated path rather than failing
    startup.  Requires the ``messages`` table to already exist; idempotent.
    """
    try:
        _run(connection, (_TRGM_EXTENSION,))
    except Exception:
        logger.warning(
            "pg_trgm extension unavailable; message search stays on the unaccelerated LIKE path",
            exc_info=True,
        )
        return
    try:
        _run(connection, _TRGM_INDEX_DDL)
    except Exception:
        logger.warning(
            "failed to create messages content trigram index; message search "
            "stays on the unaccelerated LIKE path",
            exc_info=True,
        )
        return
    logger.info("PostgreSQL trigram search index installed")


# ---------------------------------------------------------------------------
# Updated-sort conversations index (ROADMAP 18.5 search fast path)
# ---------------------------------------------------------------------------

# The shared queue ORDER BY is ``updated_at DESC, id DESC``.  The SQLite-era
# ``idx_conversations_tenant_updated (tenant_id, updated_at DESC)`` cannot
# satisfy the ``id DESC`` tiebreak, so on PostgreSQL the planner falls back to
# a full parallel scan + top-N sort for the ``updated`` sort (measured
# ~255ms warm on the 100k-tenant).  Adding ``id DESC`` lets the planner use an
# ordered index scan for paging AND for the windowed message-search fast path
# in :meth:`app.db.conversations_query.DatabaseConversationsQueryMixin
# ._query_conversations_windowed` (full-match term: ~0.5ms, was 627-809ms).
_UPDATED_SORT_INDEX_DDL = (
    (
        "CREATE INDEX IF NOT EXISTS idx_conversations_tenant_updated_id "
        "ON conversations (tenant_id, updated_at DESC, id DESC)"
    ),
)


def install_updated_sort_index(connection: Any) -> None:
    """Install the ``updated``-sort index used by queue paging and message search.

    PostgreSQL-only: the SQLite schema keeps the two-column
    ``idx_conversations_tenant_updated`` (its rowid ordering already breaks
    ``id`` ties), and migration-chain immutability rules out editing the shared
    DDL.  Idempotent; runs in the native post-schema pass.
    """
    _run(connection, _UPDATED_SORT_INDEX_DDL)
    logger.info("PostgreSQL updated-sort conversations index installed")


# ---------------------------------------------------------------------------
# Archive-tier acceleration (ROADMAP 18.3 cold reverse-lookup)
# ---------------------------------------------------------------------------

# The archive tables carry no FTS mirror or pg_trgm index by design (the tier
# exists to bound the hot mirror's volume), so ``archived=True`` message search
# walks the LIKE/CTE fallback.  On SQLite that is a cold-table scan regardless;
# on PostgreSQL mirroring the hot-tier indexes lets a selective term use a
# trigram bitmap scan instead (CAPACITY 3.4: selective cold search 328ms →
# index-backed) and fixes the archive ``updated``-sort paging that, like the
# hot ``idx_conversations_tenant_updated`` before it, lacked the ``id DESC``
# tiebreak the shared ``ORDER BY updated_at DESC, id DESC`` requires.
_ARCHIVE_TRGM_INDEX_DDL = (
    (
        "CREATE INDEX IF NOT EXISTS idx_messages_archive_content_trgm "
        "ON messages_archive USING gin (content gin_trgm_ops)"
    ),
)
_ARCHIVE_UPDATED_SORT_INDEX_DDL = (
    (
        "CREATE INDEX IF NOT EXISTS idx_conversations_archive_tenant_updated_id "
        "ON conversations_archive (tenant_id, updated_at DESC, id DESC)"
    ),
)


def install_archive_trgm_search(connection: Any) -> None:
    """Mirror :func:`install_trgm_search` onto the archive message table.

    Best-effort by design: if ``pg_trgm`` is unavailable (no
    ``postgresql-contrib`` or the role lacks permission), log a warning and
    leave archive search on the unaccelerated LIKE path rather than failing
    startup.  Requires the ``messages_archive`` table to already exist (it is
    created by the shared schema before this native pass); idempotent.
    """
    try:
        _run(connection, (_TRGM_EXTENSION,))
    except Exception:
        logger.warning(
            "pg_trgm extension unavailable; archive message search stays on the "
            "unaccelerated LIKE path",
            exc_info=True,
        )
        return
    try:
        _run(connection, _ARCHIVE_TRGM_INDEX_DDL)
    except Exception:
        logger.warning(
            "failed to create messages_archive content trigram index; archive "
            "message search stays on the unaccelerated LIKE path",
            exc_info=True,
        )
        return
    logger.info("PostgreSQL archive trigram search index installed")


def install_archive_updated_sort_index(connection: Any) -> None:
    """Install the archive ``updated``-sort index for paging and search.

    PostgreSQL-only and idempotent, mirroring
    :func:`install_updated_sort_index` onto ``conversations_archive`` so the
    shared ``ORDER BY updated_at DESC, id DESC`` can use an ordered index scan
    on the cold tier instead of a full scan + top-N sort.
    """
    _run(connection, _ARCHIVE_UPDATED_SORT_INDEX_DDL)
    logger.info("PostgreSQL archive updated-sort conversations index installed")


# ---------------------------------------------------------------------------
# CSAT summary partial index (backlog optimization)
# ---------------------------------------------------------------------------

_CSAT_SUMMARY_INDEX_DDL = (
    (
        "CREATE INDEX IF NOT EXISTS idx_csat_summary_tenant_responded "
        "ON csat_surveys (tenant_id, substr(responded_at, 1, 10)) "
        "WHERE rating IS NOT NULL"
    ),
)


def install_csat_summary_index(connection: Any) -> None:
    """Install the PostgreSQL equivalent of SQLite migration 25.

    PostgreSQL does not execute the SQLite migration function directly during
    compatibility initialization.  Keep the partial expression index in this
    native post-schema pass so both backends accelerate the tenant-wide CSAT
    aggregate and its per-day trend.  The DDL is idempotent.
    """
    _run(connection, _CSAT_SUMMARY_INDEX_DDL)
    logger.info("PostgreSQL CSAT summary index installed")


def install_compatibility(connection: Any) -> None:
    """Install functions, ordering columns, triggers, and native indexes."""
    install_functions(connection)
    install_ordering_columns(connection)
    install_triggers(connection)
    install_trgm_search(connection)
    install_updated_sort_index(connection)
    install_archive_trgm_search(connection)
    install_archive_updated_sort_index(connection)
    install_csat_summary_index(connection)


def _run(connection: Any, statements: Sequence[str]) -> None:
    cursor = connection.cursor()
    try:
        for statement in statements:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
