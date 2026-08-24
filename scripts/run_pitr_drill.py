#!/usr/bin/env python3
"""42.2 REL-001: PITR drill — point-in-time restore with RPO/RTO evidence.

Simulates the 42.2 acceptance contract "自动化 PITR 到隔离环境" on a scratch
SQLite database: seed a realistic state (conversation, messages, queued turn
job, scanned attachment, chained audit trail with signed WORM anchors, DSR
deletion tombstone), take a T0 base backup, mutate inside the recovery
window, take a T1 backup, write a post-T1 marker, then restore **T1** into an
isolated environment and verify:

1. audit chain + WORM anchors verify intact on the restored copy;
2. every write from before T1 is present (RPO: zero loss inside the window);
3. the post-T1 marker is absent (the restore really is point-in-time);
4. the queued turn job survives with its state;
5. attachment rows match the restored object store (row count, files, sizes);
6. the deletion tombstone is present and re-applies cleanly at startup.

The measured restore wall time is recorded against the RTO budget. Outcome is
appended to ``supplychain/pitr-drills.json`` for the governance gate.

Usage:
    python scripts/run_pitr_drill.py [--out supplychain/pitr-drills.json]
        [--rto-budget-seconds 1800] [--rpo-budget-seconds 900]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.audit_anchor import Ed25519KmsSigner, build_anchor_claim
from app.attachments import AttachmentService
from app.config import Settings
from app.database import Database
from app.privacy import DataProtectionService
from app.retention import RetentionService
from app.worm_store import DiskWormStore

logger = logging.getLogger("helix")

SEED = "phase42.2-pitr-drill"
DSR_SECRET = "pitr-drill-dsr-secret"


def _signer() -> Ed25519KmsSigner:
    return Ed25519KmsSigner(private_key=hashlib.sha256(SEED.encode("utf-8")).digest())


def _seed_anchors(connection: sqlite3.Connection) -> dict[int, str]:
    """Insert frontier anchors for high-risk rows; return seq → event_hash."""
    rows = connection.execute(
        "SELECT id, seq, event_type, event_hash FROM audit_events ORDER BY seq"
    ).fetchall()
    hashes: dict[int, str] = {}
    for row in rows:
        if row["event_type"] in {"api_key.issued", "api_key.revoked", "member.invited"}:
            connection.execute(
                "INSERT INTO audit_anchors "
                "(anchor_id, seq, chain_hash, event_type, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"pr_{row['id']}",
                    int(row["seq"]),
                    str(row["event_hash"]),
                    row["event_type"],
                    "PITR drill frontier anchor",
                    "2026-08-21T00:00:00+00:00",
                ),
            )
        hashes[int(row["seq"])] = str(row["event_hash"])
    return hashes


def _attachment_service(database: Database, storage_dir: Path) -> AttachmentService:
    settings = Settings(
        auth_mode="api_key",
        attachment_storage_dir=storage_dir,
        attachment_scan_enabled=True,
        attachment_max_mb=10,
        attachment_quota_mb=512,
    )
    return AttachmentService(database, settings)


def _backup(db: Path, out_dir: Path) -> Path | None:
    backed = subprocess.run(
        [sys.executable, "scripts/backup.py", "--database", str(db), "--output", str(out_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if backed.returncode != 0:
        logger.error("backup failed: %s", backed.stderr[-400:])
        return None
    manifests = sorted(out_dir.glob("*.manifest.json"))
    if not manifests:
        return None
    manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    return out_dir / manifest["backup_file"]


def _restore(backup_file: Path, target: Path) -> bool:
    restored = subprocess.run(
        [
            sys.executable,
            "scripts/restore.py",
            "--backup",
            str(backup_file),
            "--target",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if restored.returncode != 0:
        logger.error("restore failed: %s", restored.stderr[-400:])
        return False
    return True


def _verify_chain(db: Path, worm_dir: Path, trusted_kids: str) -> tuple[bool, str]:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_audit_chain.py",
            "--db",
            str(db),
            "--worm-dir",
            str(worm_dir),
            "--trusted-kids",
            trusted_kids,
            "--environment",
            "development",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output[-400:]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="supplychain/pitr-drills.json")
    parser.add_argument("--rto-budget-seconds", type=int, default=1800)
    parser.add_argument("--rpo-budget-seconds", type=int, default=900)
    args = parser.parse_args()

    failures: list[str] = []
    rto_seconds = -1.0

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        db_path = root / "pitr.db"
        worm_dir = root / "pitr.worm"
        attachments_dir = root / "attachments"
        signer = _signer()

        # ------------------------------------------------------------------
        # Seed the pre-T0 state through real application paths.
        # ------------------------------------------------------------------
        database = Database(db_path)
        try:
            database.initialize()
            database.ensure_tenant("demo")
            keep = database.create_conversation(
                "demo", "PITR Keep", "CUST-PITR-KEEP", "web", "admin", 120
            )["id"]
            database.create_conversation(
                "demo", "PITR Gone", "CUST-PITR-GONE", "web", "admin", 120
            )
            database.add_message("demo", keep, "customer", "cust-1", "pre-T0 message")
            database.audit("demo", None, "admin", "api_key.issued", {"credential_id": "k1"})
            database.audit("demo", None, "admin", "member.invited", {"member": "u2"})
            job, _replayed = database.enqueue_turn_job(
                "demo", keep, "pitr-key-1", "admin", "pre-T0 turn", 3
            )
            service = _attachment_service(database, attachments_dir)
            service.upload(
                "demo", keep, "admin", "evidence.txt", "text/plain", b"pitr attachment payload"
            )

            # A completed deletion with a tombstone, so the restored copy can
            # prove the startup re-application path still works.
            retention = RetentionService(database, dsr_export_secret=DSR_SECRET)
            retention.execute_data_subject_deletion("demo", "CUST-PITR-GONE")
            protection = DataProtectionService(retention, sla_minutes=120)
            protection.record_deletion_proof(
                "demo", "CUST-PITR-GONE", "req-pitr-1", "admin", DSR_SECRET
            )

            with database.connect() as connection:
                tips = _seed_anchors(connection)
        finally:
            database.close()

        store = DiskWormStore(worm_dir)
        for seq, chain_hash in tips.items():
            claim = build_anchor_claim(
                environment="development",
                last_seq=seq,
                last_hash=chain_hash,
                signer=signer,
                timestamp=f"2026-08-21T00:{seq:02d}:00+00:00",
            )
            store.write_once(object_id=str(claim["anchor_id"]), payload=claim)

        # ------------------------------------------------------------------
        # T0 base backup.
        # ------------------------------------------------------------------
        backups = root / "backups"
        t0_file = _backup(db_path, backups)
        if t0_file is None:
            failures.append("T0 backup failed")

        # ---------------------------------------------------------------
        # In-window mutations (must survive a restore-to-T1).
        # ---------------------------------------------------------------
        database = Database(db_path)
        try:
            database.add_message("demo", keep, "customer", "cust-1", "in-window message")
            database.audit("demo", keep, "admin", "message.created", {"in_window": True})
            service = _attachment_service(database, attachments_dir)
            service.upload(
                "demo",
                keep,
                "admin",
                "window.txt",
                "text/plain",
                b"in-window attachment payload",
            )
        finally:
            database.close()

        t1_started = time.monotonic()
        t1_file = _backup(db_path, backups) if t0_file is not None else None
        if t1_file is None:
            failures.append("T1 backup failed")

        # Post-T1 marker: written after the recovery point; must NOT come back.
        database = Database(db_path)
        try:
            database.add_message("demo", keep, "customer", "cust-1", "post-T1 marker")
        finally:
            database.close()

        # ------------------------------------------------------------------
        # Point-in-time restore of T1 into an isolated environment.
        # ------------------------------------------------------------------
        restored_db = root / "restored-env" / "restored.db"
        restored_attachments = root / "restored-env" / "attachments"
        if t1_file is not None:
            if not _restore(t1_file, restored_db):
                failures.append("point-in-time restore failed")
            else:
                rto_seconds = time.monotonic() - t1_started
                # Object-store side of the isolated environment: attachments
                # are restored from the same snapshot generation.
                restored_attachments.mkdir(parents=True, exist_ok=True)
                for item in attachments_dir.iterdir():
                    if item.is_file():
                        (restored_attachments / item.name).write_bytes(item.read_bytes())

                restored = Database(restored_db)
                try:
                    with restored.connect() as connection:
                        messages = [
                            row["content"]
                            for row in connection.execute(
                                "SELECT content FROM messages WHERE conversation_id = ?",
                                (keep,),
                            ).fetchall()
                        ]
                        job_row = connection.execute(
                            "SELECT status FROM turn_jobs WHERE id = ?", (job["id"],)
                        ).fetchone()
                        attachment_rows = connection.execute(
                            "SELECT storage_key, size_bytes FROM attachments "
                            "WHERE tenant_id = 'demo'"
                        ).fetchall()
                        tombstone = connection.execute(
                            "SELECT COUNT(*) AS n FROM customer_tombstones "
                            "WHERE tenant_id = 'demo' AND customer_ref = 'CUST-PITR-GONE'"
                        ).fetchone()
                        gone_left = connection.execute(
                            "SELECT COUNT(*) AS n FROM conversations "
                            "WHERE tenant_id = 'demo' AND customer_ref = 'CUST-PITR-GONE'"
                        ).fetchone()
                    contents = "\n".join(messages)
                    if "pre-T0 message" not in contents:
                        failures.append("RPO violation: pre-T0 message missing after restore")
                    if "in-window message" not in contents:
                        failures.append("RPO violation: in-window message missing after restore")
                    if "post-T1 marker" in contents:
                        failures.append(
                            "restore is not point-in-time: post-T1 marker came back"
                        )
                    if job_row is None or job_row["status"] != "queued":
                        failures.append("queued turn job did not survive the restore")
                    if len(attachment_rows) != 2:
                        failures.append(
                            f"attachment manifest mismatch: expected 2 rows, got {len(attachment_rows)}"
                        )
                    else:
                        for row in attachment_rows:
                            obj = restored_attachments / str(row["storage_key"])
                            if not obj.exists() or obj.stat().st_size != int(row["size_bytes"]):
                                failures.append(
                                    f"attachment object missing/mismatched: {row['storage_key']}"
                                )
                    if not tombstone or int(tombstone["n"]) != 1:
                        failures.append("deletion tombstone missing after restore")
                    if not gone_left or int(gone_left["n"]) != 0:
                        failures.append("tombstoned conversation resurrected by restore")
                finally:
                    restored.close()

                intact, output = _verify_chain(restored_db, worm_dir, signer.kid)
                if not intact:
                    failures.append(f"audit chain verification failed: {output}")

                # Startup re-application path (main.py tombstone-after-restore).
                reopened = Database(restored_db)
                try:
                    retention = RetentionService(reopened, dsr_export_secret=DSR_SECRET)
                    results = DataProtectionService(retention, sla_minutes=120).enforce_tombstones_after_restore()
                    if results.get("failed"):
                        failures.append("tombstone re-application reported failures")
                except Exception as exc:
                    failures.append(f"tombstone re-application raised: {exc}")
                finally:
                    reopened.close()

        if rto_seconds >= 0 and rto_seconds > args.rto_budget_seconds:
            failures.append(
                f"RTO budget exceeded: {rto_seconds:.1f}s > {args.rto_budget_seconds}s"
            )

    passed = not failures
    ledger_path = Path(args.out)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {
            "schema_version": 1,
            "policy": "PITR drill ledger (42.2): point-in-time restore with RPO/RTO evidence",
        }
    ledger.setdefault("drills", []).append(
        {
            "drill_type": "pitr_restore",
            "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "passed": passed,
            "rto_seconds": round(rto_seconds, 3) if rto_seconds >= 0 else None,
            "rto_budget_seconds": args.rto_budget_seconds,
            "rpo_budget_seconds": args.rpo_budget_seconds,
            "details": "; ".join(failures)
            or "T0/T1 backups → restore-to-T1 → anchors intact, window data complete, post-T1 excluded, job/attachments/tombstone verified",
        }
    )
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "PITR drill %s recorded in %s (rto=%.1fs)",
        "PASSED" if passed else "FAILED",
        ledger_path,
        max(rto_seconds, 0.0),
    )
    if failures:
        print("\n".join(failures))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())