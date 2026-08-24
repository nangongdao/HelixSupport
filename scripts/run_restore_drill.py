"""Gate B (1.4): audit-anchor restore drill — backup → restore → verify.

Exercises the SEC-005 three-evidence restore path on a scratch database:
seed a chained audit trail with high-risk events, export signed WORM claims
(Ed25519) and DB frontier anchors, run the real backup + restore tools, then
run ``verify_audit_chain.py`` and assert an intact chain. Finally tamper one
historical row and assert the verifier reports TAMPER — proving the restored
copy really is the anchored one.

Writes ``supplychain/restore-drills.json`` (schema_version 1) with the run
outcome so Gate B automation can consume per-drill timestamps.

Usage:
    python scripts/run_restore_drill.py [--out supplychain/restore-drills.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from app.audit_anchor import Ed25519KmsSigner, build_anchor_claim
from app.database import Database
from app.worm_store import DiskWormStore

logger = logging.getLogger("helix")

SEED = "phase41.3-restore-drill"


def _signer() -> Ed25519KmsSigner:
    import hashlib

    return Ed25519KmsSigner(private_key=hashlib.sha256(SEED.encode("utf-8")).digest())


def _seed_db_anchors(connection: sqlite3.Connection) -> dict[int, str]:
    """Insert one frontier tip per high-risk row from the recomputed chain.

    Returns ``seq -> event_hash`` so the caller can sign WORM claims that
    match the actual chain (a placeholder hash would fail every check).
    """
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
                    f"dr_{row['id']}",
                    int(row["seq"]),
                    str(row["event_hash"]),
                    row["event_type"],
                    "restore drill frontier anchor",
                    "2026-08-20T00:00:00+00:00",
                ),
            )
        hashes[int(row["seq"])] = str(row["event_hash"])
    return hashes


def _seed(root: Path) -> tuple[Path, Path, Ed25519KmsSigner]:
    """Scratch DB with high-risk events + frontier anchors + WORM claims."""
    db_path = root / "drill.db"
    worm_dir = root / "drill.worm"
    signer = _signer()
    database = Database(db_path)
    try:
        database.initialize()
        database.ensure_tenant("demo")
        database.audit("demo", None, "admin", "conversation.created", {"id": "c1"})
        database.audit("demo", None, "admin", "api_key.issued", {"credential_id": "k1"})
        database.audit("demo", None, "admin", "message.created", {"id": "m1"})
        database.audit("demo", None, "admin", "member.invited", {"member": "u2"})
        with database.connect() as connection:
            tips = _seed_db_anchors(connection)
    finally:
        database.close()
    store = DiskWormStore(worm_dir)
    for seq, chain_hash in tips.items():
        claim = build_anchor_claim(
            environment="development",
            last_seq=seq,
            last_hash=chain_hash,
            signer=signer,
            timestamp=f"2026-08-20T00:{seq:02d}:00+00:00",
        )
        store.write_once(object_id=str(claim["anchor_id"]), payload=claim)
    return db_path, worm_dir, signer


def _verify(
    db: Path, worm_dir: Path, trusted_kids: str, environment: str = "development"
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "scripts/verify_audit_chain.py",
        "--db",
        str(db),
        "--worm-dir",
        str(worm_dir),
        "--trusted-kids",
        trusted_kids,
        "--environment",
        environment,
    ]
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _tamper(db: Path) -> None:
    """Flip a historical payload column; the hash chain must catch it."""
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE audit_events SET payload_json = '{\"id\":\"c1-tampered\"}' "
            "WHERE event_type = 'conversation.created'"
        )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="supplychain/restore-drills.json")
    args = parser.parse_args()

    failures: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        db_path, worm_dir, signer = _seed(root)
        kid = signer.kid

        # 1. Backup (compressed, checksummed) then restore to a new path —
        #    the exact DISASTER_RECOVERY.md flow.
        backup_dir = root / "drill-backups"
        backed = subprocess.run(
            [
                sys.executable,
                "scripts/backup.py",
                "--database",
                str(db_path),
                "--output",
                str(backup_dir),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        backup_file: Path | None = None
        if backed.returncode != 0:
            failures.append(f"backup failed: {backed.stderr[:500]}")
        else:
            manifests = list(backup_dir.glob("*.manifest.json"))
            if manifests:
                backup_file = backup_dir / json.loads(
                    manifests[0].read_text(encoding="utf-8")
                )["backup_file"]
            if backup_file is None or not backup_file.exists():
                failures.append("backup produced no manifest/backup file")

        restored = root / "restored.db"
        restore_ok = False
        if backup_file is not None:
            restore = subprocess.run(
                [
                    sys.executable,
                    "scripts/restore.py",
                    "--backup",
                    str(backup_file),
                    "--target",
                    str(restored),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if restore.returncode != 0:
                failures.append(f"restore failed: {restore.stderr[:500]}")
            else:
                restore_ok = True

        # 2. Verify the restored copy against the three evidence layers — only
        #    when the restore actually produced a file, otherwise upstream
        #    failures already explain why there is nothing to verify.
        if restore_ok and restored.exists():
            intact = _verify(restored, worm_dir, kid)
            if intact.returncode != 0:
                failures.append(
                    f"verify on restored copy failed: {(intact.stdout + intact.stderr)[-500:]}"
                )

            # 3. Tamper the restored copy; the verifier must now report TAMPER.
            _tamper(restored)
            tampered = _verify(restored, worm_dir, kid)
            tampered_output = (tampered.stdout or "") + (tampered.stderr or "")
            if tampered.returncode == 0:
                failures.append("tampered restore unexpectedly verified clean")
            if "TAMPER" not in tampered_output:
                failures.append(
                    f"tampered restore did not report TAMPER: {tampered_output[-300:]}"
                )
        elif not failures:
            failures.append("restore produced no restored.db to verify")

    passed = not failures
    out = Path(args.out)
    if out.exists():
        ledger = json.loads(out.read_text(encoding="utf-8"))
    else:
        ledger = {"schema_version": 1, "policy": "restore drill ledger (Gate B)"}

    ledger.setdefault("drills", []).append(
        {
            "drill_type": "audit_anchor_restore",
            "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "passed": passed,
            "details": "; ".join(failures) if failures else "backup→restore→verify intact; tamper→TAMPER",
        }
    )
    out.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("restore drill %s recorded in %s", "PASSED" if passed else "FAILED", out)
    if failures:
        print("\n".join(failures))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())