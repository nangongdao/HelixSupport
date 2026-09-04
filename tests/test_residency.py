"""Tests for app/residency.py data-residency policy module."""

from app.residency import (
    RegionSpec,
    check_restore_compatibility,
    get_region_spec,
    is_known_region,
    resolve_region,
    summarize_tenant_residency,
)


def test_resolve_region_normalizes_to_lowercase():
    assert resolve_region("US-EAST") == "us-east"
    assert resolve_region("  EU-WEST  ") == "eu-west"


def test_resolve_region_defaults_to_local():
    assert resolve_region(None) == "local"
    assert resolve_region("") == "local"
    assert resolve_region("   ") == "local"


def test_get_region_spec_returns_known_region():
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
        "eu": RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup"),
    }
    spec = get_region_spec("eu", inventory)
    assert spec.name == "eu"
    assert spec.storage_location == "eu-1"


def test_get_region_spec_falls_back_to_local_for_unknown():
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
    }
    spec = get_region_spec("unknown-region", inventory)
    assert spec.name == "local"


def test_is_known_region_returns_true_for_known():
    inventory = {"eu": RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup")}
    assert is_known_region("eu", inventory) is True


def test_is_known_region_returns_false_for_unknown():
    inventory = {"eu": RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup")}
    assert is_known_region("unknown", inventory) is False


def test_region_spec_allows_data_class_within_limit():
    spec = RegionSpec(
        name="eu",
        storage_location="eu-1",
        backup_target="eu-backup",
        max_data_class="confidential",
    )
    assert spec.allows_data_class("public") is True
    assert spec.allows_data_class("internal") is True
    assert spec.allows_data_class("confidential") is True
    assert spec.allows_data_class("restricted") is False


def test_region_spec_allows_data_class_rejects_unknown():
    spec = RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup")
    assert spec.allows_data_class("unknown-class") is False


def test_summarize_tenant_residency_single_region():
    rows = [
        {"tenant_id": "t1", "region": "eu"},
        {"tenant_id": "t2", "region": "eu"},
    ]
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
        "eu": RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup"),
    }
    result = summarize_tenant_residency(rows, inventory)
    assert result["single_write_region"] is True
    assert "eu" in result["regions"]
    assert result["regions"]["eu"]["tenant_count"] == 2
    assert result["regions"]["eu"]["spec_status"] == "known"


def test_summarize_tenant_residency_multi_region():
    rows = [
        {"tenant_id": "t1", "region": "eu"},
        {"tenant_id": "t2", "region": "us"},
    ]
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
        "eu": RegionSpec(name="eu", storage_location="eu-1", backup_target="eu-backup"),
        "us": RegionSpec(name="us", storage_location="us-1", backup_target="us-backup"),
    }
    result = summarize_tenant_residency(rows, inventory)
    assert result["single_write_region"] is False
    assert len(result["regions"]) == 2


def test_summarize_tenant_residency_unknown_region_flagged():
    rows = [{"tenant_id": "t1", "region": "unknown-region"}]
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
    }
    result = summarize_tenant_residency(rows, inventory)
    # Unknown region gets normalized to "unknown-region" key, but spec_status is marked
    assert "unknown-region" in result["regions"]
    assert result["regions"]["unknown-region"]["spec_status"] == "unknown-fallback-local"


def test_summarize_tenant_residency_includes_cross_border_transfers():
    rows = [{"tenant_id": "t1", "region": "eu"}]
    inventory = {
        "local": RegionSpec(name="local", storage_location="primary", backup_target="primary"),
        "eu": RegionSpec(
            name="eu",
            storage_location="eu-1",
            backup_target="eu-backup",
            cross_border_transfers=("us", "ap"),
        ),
    }
    result = summarize_tenant_residency(rows, inventory)
    assert set(result["cross_border_transfers"]) == {"ap", "us"}


def test_check_restore_compatibility_compatible():
    manifest_residency = {
        "regions": {
            "eu": {"tenant_count": 1},
        }
    }
    violations = check_restore_compatibility(manifest_residency, {"eu"})
    assert violations == []


def test_check_restore_compatibility_violation():
    manifest_residency = {
        "regions": {
            "eu": {"tenant_count": 1},
            "us": {"tenant_count": 1},
        }
    }
    violations = check_restore_compatibility(manifest_residency, {"eu"})
    assert "us" in violations


def test_check_restore_compatibility_empty_manifest():
    violations = check_restore_compatibility({}, {"eu"})
    assert violations == []


def test_check_restore_compatibility_normalizes_region_names():
    manifest_residency = {
        "regions": {
            "EU-WEST": {"tenant_count": 1},
        }
    }
    violations = check_restore_compatibility(manifest_residency, {"eu-west"})
    assert violations == []
