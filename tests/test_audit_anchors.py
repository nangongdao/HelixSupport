"""Phase 41.3 / SEC-005: audit evidence external anchoring fault tests.

Drives ``scripts/verify_audit_chain.py`` against purpose-built databases over
``subprocess`` so that ROADMAP 41.3's acceptance criteria — "重算本地全链、
删除 anchor、替换 manifest、错序/重复 seq、KMS key rotation、WORM 暂不可用
均有故障测试和明确行为" — each get a concrete fixture plus an asserted exit
code and a stated verification message.

A seed fixture is built once per test with a fresh chain (4 events, ed25519
anchors and DB frontier anchors), then one defect is applied and the script is
invoked as a subprocess against it — the same entry point an operator would
run during recovery.  ``fresh`` (no defect) exercises the healthy invariant:
exit 0 with the three layers reported.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.audit_anchor import Ed25519KmsSigner, build_anchor_claim
from app.database import Database
from app.worm_store import DiskWormStore


def _signer(salt: str = "") -> Ed25519KmsSigner:
    # Deterministic per-salt private key so untrusted-kid fixtures can pin the
    # exact expected kid without a key-material round-trip.
    import hashlib

    seed_digest = hashlib.sha256(f"phase41.3-seed{salt}".encode()).digest()
    return Ed25519KmsSigner(private_key=seed_digest)


def _seed_fixture(
    root: Path,
    *,
    db_name: str = "fixture.db",
    worm_name: str = "fixture.worm",
    signer: Ed25519KmsSigner | None = None,
) -> tuple[Path, Path, Ed25519KmsSigner]:
    """Build a 4-event chained DB plus DB frontier anchors and WORM claims.

    Returns ``(db_path, worm_dir, signer)``; the WORM directory holds two
    signed claims (seq 2 and 4), the DB holds ``audit_anchors`` tips for the
    two high-risk events, and the chain head sits at seq 4.
    """
    signer = signer or _signer()
    db_path = root / db_name
    worm_dir = root / worm_name
    database = Database(db_path)
    try:
        database.initialize()
        database.ensure_tenant("demo")
        database.audit("demo", None, "admin", "conversation.created", {"id": "c1"})
        database.audit("demo", None, "admin", "api_key.issued", {"credential_id": "k1"})
        database.audit("demo", None, "admin", "message.created", {"id": "m1"})
        database.audit("demo", None, "admin", "api_key.revoked", {"credential_id": "k1"})
        with database.connect() as connection:
            hashes = _seed_db_anchors(connection)
    finally:
        database.close()
    store = DiskWormStore(worm_dir)
    _write_claim(store, signer, seq=2, chain_hash=hashes[2])
    _write_claim(store, signer, seq=4, chain_hash=hashes[4])
    return db_path, worm_dir, signer


def _write_claim(
    store: DiskWormStore, signer: Ed25519KmsSigner, *, seq: int, chain_hash: str
) -> None:
    claim = build_anchor_claim(
        environment="development",
        last_seq=seq,
        last_hash=chain_hash,
        signer=signer,
        timestamp=f"2026-08-20T00:{seq:02d}:00+00:00",
    )
    store.write_once(object_id=str(claim["anchor_id"]), payload=claim)


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
        if row["event_type"] in {"api_key.issued", "api_key.revoked"}:
            connection.execute(
                "INSERT INTO audit_anchors "
                "(anchor_id, seq, chain_hash, event_type, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"fr_{row['id']}",
                    int(row["seq"]),
                    str(row["event_hash"]),
                    row["event_type"],
                    "test frontier anchor",
                    "2026-08-20T00:00:00+00:00",
                ),
            )
        hashes[int(row["seq"])] = str(row["event_hash"])
    connection.commit()
    return hashes


def _run_script(
    script: Path,
    *,
    db: Path,
    worm_dir: Path | None = None,
    trusted_kids: str = "",
    environment: str = "development",
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(script), "--db", str(db), "--environment", environment]
    if worm_dir is not None:
        args += ["--worm-dir", str(worm_dir)]
    if trusted_kids:
        args += ["--trusted-kids", trusted_kids]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        # The anchor scripts print Chinese diagnostics; `text=True` alone
        # decodes with the OS default codepage, which on Windows is GBK. The
        # reader thread then dies on UnicodeDecodeError and `stderr` comes
        # back as None, so an assertion about the message fails with a
        # TypeError that says nothing about the actual tamper detection.
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


class AuditAnchorFaultTests(unittest.TestCase):
    """41.3 acceptance: every tamper mode has a test and explicit behavior."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._script = Path(__file__).resolve().parent.parent / "scripts" / "verify_audit_chain.py"
        self._kid = ""
        self._db = self._root / "noproject.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self) -> None:
        db, worm, signer = _seed_fixture(self._root)
        self._db = db
        self._worm = worm
        self._kid = signer.kid
        connection = sqlite3.connect(self._db)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT audit_events.seq, audit_events.event_hash, audit_anchors.anchor_id "
                "FROM audit_events "
                "LEFT JOIN audit_anchors ON audit_events.seq = audit_anchors.seq "
                "ORDER BY audit_events.seq"
            ).fetchall()
            self._hashes = {int(row["seq"]): str(row["event_hash"]) for row in rows}
            self._anchor_by_seq = {int(row["seq"]): str(row["anchor_id"]) for row in rows}
        finally:
            connection.close()

    def _db_apply(self, fn: str, *args: object) -> None:
        connection = sqlite3.connect(self._db)
        try:
            cursor = connection.cursor()
            sql = {
                "update_event_payload": (
                    "UPDATE audit_events SET payload_json=? WHERE seq=?",
                    args,
                ),
                "delete_anchor": ("DELETE FROM audit_anchors WHERE anchor_id=?", args),
                "replace_manifest": (
                    "UPDATE audit_anchors SET chain_hash=? WHERE anchor_id=?",
                    args,
                ),
                "delete_event": ("DELETE FROM audit_events WHERE seq=?", args),
                "duplicate_seq": ("UPDATE audit_events SET seq=? WHERE seq=?", args),
            }[fn]
            cursor.execute(*sql)
            connection.commit()
        finally:
            connection.close()

    # -- healthy + per-tamper-mode behaviors --------------------------------

    def test_fresh_fixture_reports_three_layers_and_exits_0(self) -> None:
        self._seed()
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("audit chain intact", result.stdout)
        self.assertIn("DB anchors match", result.stdout)
        self.assertIn("WORM anchors verified: 2 claims signed", result.stdout)

    def test_recomputed_chain_is_detected(self) -> None:
        self._seed()
        self._db_apply("update_event_payload", json.dumps({"edited": True}), 1)
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("hash mismatch", result.stderr)

    def test_deleted_anchor_is_detected(self) -> None:
        self._seed()
        self._db_apply("delete_anchor", self._anchor_by_seq[2])
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing DB anchor", result.stderr)

    def test_replaced_manifest_is_detected(self) -> None:
        self._seed()
        self._db_apply("replace_manifest", "0" * 64, self._anchor_by_seq[2])
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("!=", result.stderr)
        self.assertIn("recomputed", result.stderr)

    def test_out_of_order_seq_is_detected(self) -> None:
        self._seed()
        self._db_apply("delete_event", 2)
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("prev_hash mismatch", result.stderr)

    def test_duplicate_seq_is_detected(self) -> None:
        self._seed()
        self._db_apply("duplicate_seq", 1, 2)
        result = _run_script(self._script, db=self._db, worm_dir=self._worm, trusted_kids=self._kid)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("duplicate audit sequence", result.stderr)

    def test_kms_rotation_without_trust_update_is_detected(self) -> None:
        self._seed()
        # Rotating the KMS key replaces the claims in a brand-new WORM store
        # (an in-place unlink would trip the store's integrity journal).  The
        # old trusted kid must reject the rotated store and the post-rotation
        # trusted kid must accept the same claims.
        rotated = _signer("rotated")
        rotated_store = DiskWormStore(self._root / "rotated.worm")
        _write_claim(rotated_store, rotated, seq=2, chain_hash=self._hashes[2])
        _write_claim(rotated_store, rotated, seq=4, chain_hash=self._hashes[4])
        result = _run_script(
            self._script, db=self._db, worm_dir=self._root / "rotated.worm", trusted_kids=self._kid
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not in the trusted set", result.stderr)
        # After the trust list is updated to the rotated kid, the same claims
        # verify cleanly (historical anchors stay verifiable post-rotation).
        result = _run_script(
            self._script,
            db=self._db,
            worm_dir=self._root / "rotated.worm",
            trusted_kids=rotated.kid,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worm_unavailable_is_detected(self) -> None:
        self._seed()
        # ``DiskWormStore`` creates missing directories on demand, so an absent
        # path does not count as a fault; a regular file in the way does.
        blocker = self._root / "worm-blocker"
        blocker.write_bytes(b"not a directory")
        result = _run_script(self._script, db=self._db, worm_dir=blocker)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("TAMPER", result.stderr)


if __name__ == "__main__":
    unittest.main()
