"""Helix Support database layer (Phase 27.1 refactor).

The domain methods were extracted into mixin modules under ``app/db/``
(core, tenancy, conversations, messages, jobs, knowledge, audit); this file
now composes them into the single ``Database`` class plus the module-level
helpers that the rest of the codebase imports (``utc_now`` and friends).

Pure structural move: method bodies, SQL, and public signatures are
unchanged, so the existing test suite (zero modifications) is the proof that
the split is behaviour-preserving. ``PostgresDatabase`` in
``app/postgres_db.py`` keeps inheriting from ``Database`` unchanged.
"""

from __future__ import annotations

from app.db._util import (
    ASCII_TERM_PATTERN,
    CJK_SEQUENCE_PATTERN,
    knowledge_search_document,
    knowledge_search_terms,
    utc_after,
    utc_after_seconds,
    utc_now,
)
from app.db.archive import DatabaseArchiveMixin
from app.db.audit import DatabaseAuditMixin
from app.db.channels import DatabaseChannelsMixin
from app.db.collaboration import DatabaseCollaborationMixin
from app.db.conversations import DatabaseConversationsMixin
from app.db.conversations_query import DatabaseConversationsQueryMixin
from app.db.core import DatabaseCoreMixin
from app.db.core_schema import DatabaseCoreSchemaMixin
from app.db.jobs import DatabaseJobsMixin
from app.db.knowledge import DatabaseKnowledgeMixin
from app.db.messages import DatabaseMessagesMixin
from app.db.tenancy import DatabaseTenancyMixin
from app.db.tickets import DatabaseTicketsMixin

__all__ = [
    "ASCII_TERM_PATTERN",
    "CJK_SEQUENCE_PATTERN",
    "Database",
    "knowledge_search_document",
    "knowledge_search_terms",
    "utc_after",
    "utc_after_seconds",
    "utc_now",
]


class Database(
    DatabaseCoreMixin,
    DatabaseCoreSchemaMixin,
    DatabaseTenancyMixin,
    DatabaseChannelsMixin,
    DatabaseConversationsMixin,
    DatabaseConversationsQueryMixin,
    DatabaseMessagesMixin,
    DatabaseJobsMixin,
    DatabaseKnowledgeMixin,
    DatabaseArchiveMixin,
    DatabaseAuditMixin,
    DatabaseCollaborationMixin,
    DatabaseTicketsMixin,
):
    """SQLite-backed data layer composing the domain mixins.

    Method resolution order follows the mixin order above; every public
    method from the pre-split single class is present on this class.
    """
