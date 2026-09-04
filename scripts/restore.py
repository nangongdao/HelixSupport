"""SQLite database restore utility.

Restores from a backup created by backup.py, verifies the checksum,
and swaps the restored database into place safely.

Usage:
    python scripts/restore.py --backup backups/support_20260801_120000.db.gz --target data/support.db
    python scripts/restore.py --backup backups/support_20260801_120000.db --target data/support.db --verify-only
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

# Same bootstrap as the other repo-root importers (frontend_gate, visual_gate,
# readme_screenshots, …). Without it a direct `python scripts/restore.py` — the
# invocation the docs and runbooks document — dies on `No module named 'app'`
# unless the package happens to be installed editable, which only CI does.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.residency import check_restore_compatibility
from scripts._console import use_utf8_console

logger = logging.getLogger(__name__)


def compute_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_backup(backup_path: Path, manifest: dict) -> bool:
    """Verify backup integrity against its manifest."""
    if not backup_path.exists():
        logger.error("Backup file not found: %s", backup_path)
        return False
    actual_checksum = compute_sha256(backup_path)
    expected_checksum = manifest.get("sha256", "")
    if actual_checksum != expected_checksum:
        logger.error(
            "Checksum mismatch: expected %s, got %s", expected_checksum[:12], actual_checksum[:12]
        )
        return False
    logger.info("Checksum verified: %s", actual_checksum[:12])
    return True


def decompress_backup(backup_path: Path) -> Path:
    """Decompress a .db.gz backup, returning a temporary .db path."""
    if backup_path.suffix == ".gz":
        decompressed = backup_path.with_suffix("")
        with gzip.open(backup_path, "rb") as f_in, open(decompressed, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return decompressed
    return backup_path


def restore_database(
    backup_path: Path,
    target_path: Path,
    *,
    verify_only: bool = False,
    manifest_path: Path | None = None,
    allowed_regions: set[str] | None = None,
) -> dict[str, str]:
    """Restore a database from a backup file.

    The restore is atomic: the backup is decompressed and verified,
    then swapped into place only if all checks pass. ``allowed_regions``
    (ROADMAP 43.4) constrains where the backup's tenants may land: when
    given, any region the manifest covers that is not listed here aborts
    the restore before the swap — controlled failover means explicitly
    adding the disaster-recovery region, never silently widening residency.
    """
    # Load manifest
    if manifest_path is None:
        manifest_path = backup_path.with_suffix(".manifest.json").with_suffix(
            ".json" if backup_path.suffix != ".gz" else ".manifest.json"
        )
        # Try common manifest naming
        stem = backup_path.name
        if stem.endswith(".db.gz"):
            stem = stem[:-6]
        elif stem.endswith(".db"):
            stem = stem[:-3]
        manifest_path = backup_path.parent / f"{stem}.manifest.json"

    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Verify checksum if manifest available
    if manifest:
        if not verify_backup(backup_path, manifest):
            raise RuntimeError("Backup checksum verification failed")
    else:
        logger.warning("No manifest found; skipping checksum verification")

    if allowed_regions is not None:
        # An explicit region allow-list is a hard residency commitment:
        # without the manifest's residency summary there is nothing to
        # check against, so fail closed rather than restore unverified
        # residency into an approved region.
        if not manifest.get("residency"):
            raise RuntimeError(
                "Residency violation: --allowed-region was given but the "
                "backup manifest has no residency summary; refusing to "
                "restore. Re-create the backup with scripts/backup.py."
            )
        violations = check_restore_compatibility(manifest["residency"], allowed_regions)
        if violations:
            raise RuntimeError(
                "Residency violation: backup covers regions outside the "
                f"approved restore targets {sorted(allowed_regions)}: {violations}"
            )

    if verify_only:
        logger.info("Verify-only mode: database not restored")
        return {"status": "verified", "backup": str(backup_path)}

    # Decompress if needed
    decompressed = decompress_backup(backup_path)

    # Validate the decompressed database is readable
    test_conn = sqlite3.connect(str(decompressed))
    try:
        test_conn.execute("PRAGMA integrity_check").fetchone()
        test_conn.execute("SELECT 1 FROM tenants LIMIT 1")
    except sqlite3.Error as exc:
        raise RuntimeError(f"Restored database is corrupt: {exc}") from exc
    finally:
        test_conn.close()

    # Atomic swap: write to temp then rename
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(".db.restoring")
    shutil.copy2(decompressed, temp_path)

    # Backup the current database if it exists
    if target_path.exists():
        backup_current = target_path.with_suffix(".db.pre-restore")
        shutil.copy2(target_path, backup_current)
        logger.info("Previous database backed up to %s", backup_current)

    # Atomic rename
    temp_path.replace(target_path)

    # Clean up decompressed temp
    if decompressed != backup_path:
        decompressed.unlink()

    logger.info("Database restored to %s", target_path)
    return {
        "status": "restored",
        "target": str(target_path),
        "backup": str(backup_path),
        "previous": str(target_path.with_suffix(".db.pre-restore")),
        "sha256": manifest.get("sha256", "unknown"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore SQLite database from backup")
    parser.add_argument("--backup", required=True, help="Path to backup file (.db or .db.gz)")
    parser.add_argument("--target", required=True, help="Path to target database")
    parser.add_argument("--manifest", help="Path to manifest file (auto-detected if omitted)")
    parser.add_argument("--verify-only", action="store_true", help="Only verify, do not restore")
    parser.add_argument(
        "--allowed-region",
        action="append",
        dest="allowed_regions",
        help="Region the restored tenants may land in; repeatable. "
        "Omit to skip residency checking (legacy behaviour).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    backup_path = Path(args.backup)
    target_path = Path(args.target)
    manifest_path = Path(args.manifest) if args.manifest else None
    allowed_regions = set(args.allowed_regions) if args.allowed_regions else None

    try:
        result = restore_database(
            backup_path,
            target_path,
            verify_only=args.verify_only,
            manifest_path=manifest_path,
            allowed_regions=allowed_regions,
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        logger.error("Restore failed: %s", exc)
        return 1


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
