"""AI governance registry (ROADMAP 43.5).

Deep module over the ``ai_eval_datasets`` / ``ai_eval_runs`` /
``ai_approvals`` / ``ai_online_feedback`` tables (migration v41).  Three
responsibilities:

**Eval registry** — a versioned dataset (name + version + content hash) is
the unit of reproducibility: an eval run references the dataset id, the
candidate it scored, and the immutable WORM report object, so "candidate X
scored Y on dataset Z@vN" is traceable end to end (Gate D item 4).

**Approvals** — maker-checker for AI-subject changes (prompt promotion,
tool enablement, feedback ingestion). The requester can never approve their
own request; only an approved subject may proceed to its gated action.

**Online feedback review** — live-traffic feedback destined for training or
eval datasets must pass two gates before it lands in a dataset: automated
redaction (:func:`app.redaction.redact_sensitive`, applied at ingest — raw
text is never persisted) and human review (``pending_review`` rows are
invisible to dataset builders).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.db._util import utc_now
from app.redaction import redact_sensitive

logger = logging.getLogger(__name__)

DATASET_STRATEGIES = ("golden", "adversarial", "regression", "feedback")
APPROVAL_SUBJECT_KINDS = ("prompt_promotion", "tool_enablement", "feedback_batch")
APPROVAL_DECISIONS = ("pending", "approved", "rejected")
REVIEW_STATUSES = ("pending_review", "accepted", "rejected")


class GovernanceError(RuntimeError):
    """Base class for AI governance failures."""


class ApprovalRequiredError(GovernanceError):
    """The subject's approval is missing, pending, or rejected."""


class SelfApprovalError(GovernanceError):
    """A requester attempted to decide their own approval request."""


def dataset_content_hash(items: list[dict[str, Any]]) -> str:
    """Stable sha256 over the canonical JSON of the dataset items."""
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AiGovernanceService:
    """Registry and workflow API for AI eval/approval/review state."""

    database: Any

    # ------------------------------------------------------------- datasets

    def register_dataset(
        self,
        *,
        tenant_id: str | None,
        name: str,
        strategy: str,
        items: list[dict[str, Any]],
        created_by: str,
    ) -> dict[str, Any]:
        """Create the next version of a named dataset; returns its registry row.

        Versioning is monotonic per (tenant, name); the content hash pins the
        exact item set so later edits cannot silently redefine what a run
        measured.
        """
        if strategy not in DATASET_STRATEGIES:
            raise ValueError(
                f"unknown dataset strategy {strategy!r}; allowed: {list(DATASET_STRATEGIES)}"
            )
        if not items:
            raise ValueError("an eval dataset needs at least one item")
        dataset_id = f"ds_{uuid4().hex[:12]}"
        now = utc_now()
        content_hash = dataset_content_hash(items)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(version), 0) AS v FROM ai_eval_datasets
                WHERE tenant_id IS ? AND name = ?""",
                (tenant_id, name),
            ).fetchone()
            version = int(row["v"]) + 1
            connection.execute(
                """INSERT INTO ai_eval_datasets
                (id, tenant_id, name, version, strategy, content_hash, item_count,
                 created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dataset_id,
                    tenant_id,
                    name,
                    version,
                    strategy,
                    content_hash,
                    len(items),
                    created_by,
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO ai_eval_dataset_items
                (dataset_id, position, item_json) VALUES (?, ?, ?)""",
                [
                    (dataset_id, i, json.dumps(item, ensure_ascii=False))
                    for i, item in enumerate(items)
                ],
            )
        return {
            "id": dataset_id,
            "tenant_id": tenant_id,
            "name": name,
            "version": version,
            "strategy": strategy,
            "content_hash": content_hash,
            "item_count": len(items),
            "created_at": now,
        }

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_eval_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        return dict(row) if row else None

    def load_dataset_items(self, dataset_id: str) -> list[dict[str, Any]]:
        """Return the stored items; verifies the content hash on every load."""
        items: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            meta = connection.execute(
                "SELECT content_hash FROM ai_eval_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            if meta is None:
                raise LookupError(f"unknown dataset {dataset_id!r}")
            rows = connection.execute(
                "SELECT item_json FROM ai_eval_dataset_items "
                "WHERE dataset_id = ? ORDER BY position",
                (dataset_id,),
            ).fetchall()
        for row in rows:
            items.append(json.loads(row["item_json"]))
        if dataset_content_hash(items) != str(meta["content_hash"]):
            raise GovernanceError(f"dataset {dataset_id!r} fails content-hash verification")
        return items

    # ----------------------------------------------------------- eval runs

    def record_eval_run(
        self,
        *,
        dataset_id: str,
        candidate: str,
        report_object_id: str,
        passed: bool | None,
        metrics: dict[str, Any],
        baseline: str | None = None,
    ) -> str:
        """Link one evaluation run to its dataset and WORM report object."""
        run_id = f"run_{uuid4().hex[:12]}"
        now = utc_now()
        with self.database.connect() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM ai_eval_datasets WHERE id = ?", (dataset_id,)
                ).fetchone()
                is None
            ):
                raise LookupError(f"unknown dataset {dataset_id!r}")
            connection.execute(
                """INSERT INTO ai_eval_runs
                (id, dataset_id, candidate, baseline, report_object_id, passed,
                 metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    dataset_id,
                    candidate,
                    baseline,
                    report_object_id,
                    None if passed is None else int(passed),
                    json.dumps(metrics, ensure_ascii=False),
                    now,
                ),
            )
        return run_id

    def latest_run_for(self, *, dataset_id: str, candidate: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM ai_eval_runs WHERE dataset_id = ? AND candidate = ?
                ORDER BY created_at DESC LIMIT 1""",
                (dataset_id, candidate),
            ).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------- approvals

    def request_approval(
        self,
        *,
        tenant_id: str,
        subject_kind: str,
        subject_id: str,
        requested_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Open a maker-checker request; one open request per subject at a time."""
        if subject_kind not in APPROVAL_SUBJECT_KINDS:
            raise ValueError(
                f"unknown approval subject {subject_kind!r}; allowed: {list(APPROVAL_SUBJECT_KINDS)}"
            )
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT id FROM ai_approvals
                WHERE tenant_id = ? AND subject_kind = ? AND subject_id = ?
                  AND decision = 'pending'""",
                (tenant_id, subject_kind, subject_id),
            ).fetchone()
            if existing:
                raise GovernanceError(
                    f"an open approval already exists for {subject_kind}:{subject_id}"
                )
            approval_id = f"apr_{uuid4().hex[:12]}"
            now = utc_now()
            connection.execute(
                """INSERT INTO ai_approvals
                (id, tenant_id, subject_kind, subject_id, requested_by, decision,
                 reason, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (approval_id, tenant_id, subject_kind, subject_id, requested_by, reason, now),
            )
        return {"id": approval_id, "decision": "pending", "requested_by": requested_by}

    def decide_approval(
        self,
        approval_id: str,
        *,
        decided_by: str,
        approve: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        """Resolve a pending request; the requester cannot be the approver."""
        now = utc_now()
        with self.database.audit_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM ai_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown approval {approval_id!r}")
            if row["decision"] != "pending":
                raise GovernanceError(f"approval {approval_id!r} is already {row['decision']}")
            if row["requested_by"] == decided_by:
                raise SelfApprovalError(
                    "maker-checker violation: the requester cannot approve their own request"
                )
            decision = "approved" if approve else "rejected"
            connection.execute(
                """UPDATE ai_approvals SET decision = ?, decided_by = ?, decided_at = ?,
                reason = ? WHERE id = ?""",
                (decision, decided_by, now, reason, approval_id),
            )
            self.database.audit_in_transaction(
                connection,
                str(row["tenant_id"]),
                None,
                decided_by,
                "ai.approval_decided",
                {
                    "approval_id": approval_id,
                    "subject": f"{row['subject_kind']}:{row['subject_id']}",
                    "decision": decision,
                    "reason": reason,
                },
                created_at=now,
            )
        return {"id": approval_id, "decision": decision, "decided_by": decided_by}

    def require_approved(
        self, *, tenant_id: str, subject_kind: str, subject_id: str
    ) -> dict[str, Any]:
        """Return the governing approval or raise — fail closed by default."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM ai_approvals
                WHERE tenant_id = ? AND subject_kind = ? AND subject_id = ?
                ORDER BY created_at DESC LIMIT 1""",
                (tenant_id, subject_kind, subject_id),
            ).fetchone()
        if row is None:
            raise ApprovalRequiredError(f"{subject_kind}:{subject_id} has no approval record")
        if row["decision"] == "rejected":
            raise ApprovalRequiredError(f"{subject_kind}:{subject_id} was rejected")
        if row["decision"] == "pending":
            raise ApprovalRequiredError(f"{subject_kind}:{subject_id} awaits review")
        return dict(row)

    # ------------------------------------------------------ online feedback

    def ingest_online_feedback(
        self,
        *,
        tenant_id: str,
        conversation_id: str | None,
        source: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Redact and stage one piece of live feedback for human review.

        Redaction happens here, before persistence: the stored document is the
        redacted one, so no downstream reader (including dataset builders) can
        reach raw customer text.
        """
        feedback_id = f"fbk_{uuid4().hex[:12]}"
        redacted = redact_sensitive(payload)
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO ai_online_feedback
                (id, tenant_id, conversation_id, source, redacted_json,
                 review_status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending_review', ?)""",
                (
                    feedback_id,
                    tenant_id,
                    conversation_id,
                    source,
                    json.dumps(redacted, ensure_ascii=False),
                    now,
                ),
            )
        logger.info("ai.feedback_staged tenant=%s feedback=%s", tenant_id, feedback_id)
        return {
            "id": feedback_id,
            "review_status": "pending_review",
            "redacted": redacted,
        }

    def list_pending_feedback(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ai_online_feedback
                WHERE tenant_id = ? AND review_status = 'pending_review'
                ORDER BY created_at LIMIT ?""",
                (tenant_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def review_feedback(
        self,
        feedback_id: str,
        *,
        reviewed_by: str,
        accept: bool,
    ) -> dict[str, Any]:
        status = "accepted" if accept else "rejected"
        now = utc_now()
        with self.database.audit_transaction() as connection:
            row = connection.execute(
                "SELECT tenant_id, review_status FROM ai_online_feedback WHERE id = ?",
                (feedback_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown feedback {feedback_id!r}")
            if row["review_status"] != "pending_review":
                raise GovernanceError(f"feedback {feedback_id!r} is already {row['review_status']}")
            connection.execute(
                """UPDATE ai_online_feedback SET review_status = ?, reviewed_by = ?,
                reviewed_at = ? WHERE id = ?""",
                (status, reviewed_by, now, feedback_id),
            )
            self.database.audit_in_transaction(
                connection,
                str(row["tenant_id"]),
                None,
                reviewed_by,
                "ai.feedback_reviewed",
                {"feedback_id": feedback_id, "status": status},
                created_at=now,
            )
        return {"id": feedback_id, "review_status": status}

    def promote_feedback_to_dataset(
        self,
        *,
        feedback_ids: list[str],
        tenant_id: str,
        dataset_name: str,
        requested_by: str,
    ) -> dict[str, Any]:
        """Fold accepted feedback into a new dataset version.

        Only ``accepted`` rows are eligible; any pending/rejected id aborts the
        whole batch so unreviewed material cannot ride along.
        """
        if not feedback_ids:
            raise ValueError("no feedback ids given")
        items: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT id, redacted_json, review_status FROM ai_online_feedback "
                f"WHERE id IN ({','.join('?' * len(feedback_ids))})",
                tuple(feedback_ids),
            ).fetchall()
        found = {row["id"]: dict(row) for row in rows}
        for feedback_id in feedback_ids:
            row = found.get(feedback_id)
            if row is None:
                raise LookupError(f"unknown feedback {feedback_id!r}")
            if row["review_status"] != "accepted":
                raise GovernanceError(
                    f"feedback {feedback_id!r} is {row['review_status']}, not accepted"
                )
            items.append(json.loads(row["redacted_json"]))
        dataset = self.register_dataset(
            tenant_id=tenant_id,
            name=dataset_name,
            strategy="feedback",
            items=items,
            created_by=requested_by,
        )
        with self.database.connect() as connection:
            connection.executemany(
                "UPDATE ai_online_feedback SET dataset_id = ? WHERE id = ?",
                [(dataset["id"], fid) for fid in feedback_ids],
            )
        return dataset


__all__ = [
    "APPROVAL_DECISIONS",
    "APPROVAL_SUBJECT_KINDS",
    "DATASET_STRATEGIES",
    "REVIEW_STATUSES",
    "AiGovernanceService",
    "ApprovalRequiredError",
    "GovernanceError",
    "SelfApprovalError",
    "dataset_content_hash",
]
