"""Phase 41.6 (ARC-001) migration registry sanity gate tests."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from app.migrations import (
    MIGRATION_FN,
    Migration,
    all_migrations,
    verify_migration_registry,
)


def _fake_up(version: int, module: str) -> MIGRATION_FN:
    def _up(connection: object) -> None:  # pragma: no cover - never called
        return None

    _up.__module__ = module
    _up.__qualname__ = f"migration_{version}"
    return _up


def test_registry_is_sound_on_the_real_package() -> None:
    problems = verify_migration_registry()
    assert problems == []
    chain = all_migrations()
    assert [m.version for m in chain] == list(range(1, 42))


def test_every_migration_lives_in_its_version_module() -> None:
    for migration in all_migrations():
        assert migration.up.__module__.startswith(f"app.migrations.v{migration.version:02d}_"), (
            migration.version
        )


def test_orphan_version_module_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "v99_orphan.py").write_text("", encoding="utf-8")
    problems = verify_migration_registry(tmp_path)
    assert any("v99_orphan.py registers no migration 99" in p for p in problems)


def test_missing_version_module_is_flagged(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    extra = Migration(
        version=99, description="phantom", up=_fake_up(99, "app.migrations.v99_phantom")
    )
    monkeypatch.setattr("app.migrations.all_migrations", lambda: [*all_migrations(), extra])
    problems = verify_migration_registry(tmp_path)
    assert any("migration 99 has no version module file" in p for p in problems)


def test_wrong_module_attribution_is_flagged(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    misplaced = Migration(
        version=5,
        description="moved",
        up=_fake_up(5, "app.migrations.v06_turn_job_chunks_streaming_table"),
    )
    monkeypatch.setattr(
        "app.migrations.all_migrations",
        lambda: [m if m.version != 5 else misplaced for m in all_migrations()],
    )
    problems = verify_migration_registry(tmp_path)
    assert any("migration 5 registered from app.migrations.v06_" in p for p in problems)
    assert all("migration 5" in p for p in problems if "registered from" in p)


def test_version_gap_is_flagged(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.migrations.all_migrations",
        lambda: [m for m in all_migrations() if m.version != 13],
    )
    problems = verify_migration_registry(tmp_path)
    assert any("non-contiguous migration versions" in p and "missing: [13]" in p for p in problems)


def test_gate_script_exit_codes() -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parent.parent
    clean = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "scripts" / "verify_migration_registry.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert clean.returncode == 0, clean.stderr
    assert "41 migrations" in clean.stdout
