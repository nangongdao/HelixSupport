"""Online SQLite database backup utility.

Creates a consistent snapshot using the SQLite Online Backup API,
compresses it, and records a manifest with checksum for verification.

Usage:
    python scripts/backup.py --database data/support.db --output backups/
    python scripts/backup.py --database data/support.db --output backups/ --compress
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.residency import summarize_tenant_residency

logger = logging.getLogger(__name__)


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def backup_database(
    source_path: Path,
    output_dir: Path,
    *,
    compress: bool = True,
) -> dict[str, str]:
    """Create an online backup of the SQLite database.

    Uses the SQLite Online Backup API to ensure consistency without
    blocking writes to the source database. The manifest records the
    residency spread of the tenants covered (ROADMAP 43.4) so restores can
    verify they stay inside approved regions.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    db_name = source_path.stem
    backup_name = f"{db_name}_{timestamp}.db"
    backup_path = output_dir / backup_name

    logger.info("Starting backup: %s -> %s", source_path, backup_path)

    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    dest = sqlite3.connect(str(backup_path))
    dest.row_factory = sqlite3.Row
    try:
        source.backup(dest)
        # Read residency from the backup copy itself, not the live source:
        # a region change between copy and read would otherwise make the
        # manifest describe a newer state than the bytes it certifies.
        try:
            tenant_rows = [
                {"tenant_id": row["id"], "region": row["region"]}
                for row in dest.execute("SELECT id, region FROM tenants").fetchall()
            ]
        except sqlite3.OperationalError:
            # Pre-v40 databases have no region column yet; treat every
            # tenant as unpinned (residency defaults apply downstream).
            try:
                tenant_rows = [
                    {"tenant_id": row["id"], "region": None}
                    for row in dest.execute("SELECT id FROM tenants").fetchall()
                ]
            except sqlite3.OperationalError:
                tenant_rows = []
    finally:
        dest.close()
        source.close()

    checksum = compute_sha256(backup_path)
    file_size = backup_path.stat().st_size

    manifest = {
        "database": source_path.name,
        "backup_file": backup_name,
        "created_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "sha256": checksum,
        "size_bytes": file_size,
        "compressed": False,
    }

    if compress:
        import gzip

        compressed_path = backup_path.with_suffix(".db.gz")
        with open(backup_path, "rb") as f_in:
            with gzip.open(compressed_path, "wb") as f_out:
                f_out.writelines(f_in)
        backup_path.unlink()
        backup_path = compressed_path
        manifest["backup_file"] = backup_path.name
        manifest["sha256"] = compute_sha256(backup_path)
        manifest["size_bytes"] = backup_path.stat().st_size
        manifest["compressed"] = True

    manifest["residency"] = summarize_tenant_residency(tenant_rows)

    manifest_path = output_dir / f"{db_name}_{timestamp}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info(
        "Backup complete: %s (%d bytes, sha256=%s...)",
        backup_path,
        manifest["size_bytes"],
        checksum[:12],
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup SQLite database")
    parser.add_argument("--database", required=True, help="Path to source database")
    parser.add_argument("--output", required=True, help="Output directory for backup")
    parser.add_argument(
        "--compress", action="store_true", default=True, help="Gzip compress backup"
    )
    parser.add_argument("--no-compress", dest="compress", action="store_false")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    source = Path(args.database)
    if not source.exists():
        logger.error("Database file not found: %s", source)
        return 1

    manifest = backup_database(source, Path(args.output), compress=args.compress)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
