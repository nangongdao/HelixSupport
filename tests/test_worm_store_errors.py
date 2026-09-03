"""WORM store error path coverage tests.

Targets uncovered exception paths in app/worm_store.py for coverage improvement.
Focuses on integrity violations and edge cases that can be reliably tested.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.worm_store import (
    DiskWormStore,
    WormIntegrityError,
    WormUnavailableError,
    _validate_object_id,
)


class WormStoreErrorPathTests(unittest.TestCase):
    """Test error handling paths in DiskWormStore."""

    def test_invalid_object_id_raises(self) -> None:
        """Invalid object_id characters raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            _validate_object_id("invalid/slash")
        self.assertIn("invalid WORM object_id", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            _validate_object_id("")
        self.assertIn("invalid WORM object_id", str(ctx.exception))

    def test_directory_creation_failure_raises_unavailable(self) -> None:
        """Directory creation OSError raises WormUnavailableError."""
        # Use mock instead of chmod (unreliable on Windows)
        with TemporaryDirectory() as root:
            target = Path(root) / "subdir"
            with mock.patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
                with self.assertRaises(WormUnavailableError) as ctx:
                    DiskWormStore(target)
                self.assertIn("unavailable", str(ctx.exception))

    def test_write_with_object_id_in_payload_raises(self) -> None:
        """Payload containing 'object_id' field is rejected."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            with self.assertRaises(ValueError) as ctx:
                store.write_once("test_id", {"object_id": "conflict", "data": "x"})
            self.assertIn("must not carry an object_id", str(ctx.exception))

    def test_write_once_duplicate_raises_integrity_error(self) -> None:
        """Writing to same object_id twice raises WormIntegrityError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "first"})

            with self.assertRaises(WormIntegrityError) as ctx:
                store.write_once("obj1", {"data": "second"})
            self.assertIn("already exists", str(ctx.exception))

    def test_write_failure_raises_unavailable(self) -> None:
        """Object write OSError raises WormUnavailableError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))

            # Mock open to fail on object write
            with mock.patch("builtins.open", side_effect=OSError("disk full")):
                with self.assertRaises(WormUnavailableError) as ctx:
                    store.write_once("obj1", {"data": "test"})
                self.assertIn("WORM write failed", str(ctx.exception))

    def test_read_directory_unavailable_raises(self) -> None:
        """Directory listing OSError during read_all raises WormUnavailableError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "test"})

            # Mock iterdir to raise OSError
            with mock.patch.object(Path, "iterdir", side_effect=OSError("disk error")):
                with self.assertRaises(WormUnavailableError) as ctx:
                    store.read_all()
                self.assertIn("WORM read failed", str(ctx.exception))

    def test_read_object_file_unavailable_raises(self) -> None:
        """Object file read OSError raises WormUnavailableError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "test"})

            # Mock Path.read_bytes to raise OSError
            original_read_bytes = Path.read_bytes

            def mock_read_bytes(self):
                if self.name == "obj1.json":
                    raise OSError("disk read error")
                return original_read_bytes(self)

            with mock.patch.object(Path, "read_bytes", mock_read_bytes):
                with self.assertRaises(WormUnavailableError) as ctx:
                    store.read_all()
                self.assertIn("read failed for obj1.json", str(ctx.exception))

    def test_object_not_in_journal_raises_integrity_error(self) -> None:
        """Object file present but missing from journal raises WormIntegrityError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))

            # Write object directly without journal entry
            obj_path = Path(root) / "orphan.json"
            obj_path.write_text('{"data":"orphan"}')

            with self.assertRaises(WormIntegrityError) as ctx:
                store.read_all()
            self.assertIn("present but not in the journal", str(ctx.exception))

    def test_object_hash_mismatch_raises_integrity_error(self) -> None:
        """Object content modified after write raises WormIntegrityError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "original"})

            # Tamper with object content
            obj_path = Path(root) / "obj1.json"
            obj_path.write_text('{"data":"tampered"}')

            with self.assertRaises(WormIntegrityError) as ctx:
                store.read_all()
            self.assertIn("content hash mismatch", str(ctx.exception))

    def test_object_mtime_changed_raises_integrity_error(self) -> None:
        """Object file replaced (mtime changed) raises WormIntegrityError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "original"})

            # Get original content and re-write to change mtime
            obj_path = Path(root) / "obj1.json"
            original_content = obj_path.read_bytes()
            import time
            time.sleep(0.01)
            obj_path.write_bytes(original_content)

            with self.assertRaises(WormIntegrityError) as ctx:
                store.read_all()
            self.assertIn("was replaced after first write", str(ctx.exception))

    def test_journal_object_lost_raises_integrity_error(self) -> None:
        """Journal entry for missing object raises WormIntegrityError."""
        with TemporaryDirectory() as root:
            store = DiskWormStore(Path(root))
            store.write_once("obj1", {"data": "test"})

            # Delete object but keep journal entry
            obj_path = Path(root) / "obj1.json"
            obj_path.unlink()

            with self.assertRaises(WormIntegrityError) as ctx:
                store.read_all()
            self.assertIn("objects lost from store", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
