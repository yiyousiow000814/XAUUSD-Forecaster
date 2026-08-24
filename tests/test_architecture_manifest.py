from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.check_architecture_manifest import (
    CAMPAIGN_ORDER,
    load_manifest,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def manifest_copy():
    manifest, size = load_manifest(ROOT)
    return copy.deepcopy(manifest), size


def errors_for(manifest, size=None):
    actual_size = size if size is not None else len(json.dumps(manifest).encode())
    return validate_manifest(ROOT, manifest, actual_size)


def test_repository_architecture_manifest_is_valid() -> None:
    manifest, size = load_manifest(ROOT)
    assert errors_for(manifest, size) == []


def test_duplicate_node_ids_fail() -> None:
    manifest, _ = manifest_copy()
    manifest["nodes"].append(copy.deepcopy(manifest["nodes"][0]))
    assert "duplicate node id" in errors_for(manifest)


def test_missing_path_and_code_test_distinction_fail() -> None:
    manifest, _ = manifest_copy()
    manifest["nodes"][0]["code_paths"] = ["tests/not-code.py", "missing.py"]
    errors = errors_for(manifest)
    assert any("code path is a test path" in error for error in errors)
    assert any("missing code path" in error for error in errors)


def test_bad_edge_endpoint_fails() -> None:
    manifest, _ = manifest_copy()
    manifest["edges"][0]["to"] = "missing-node"
    assert any("bad edge endpoint" in error for error in errors_for(manifest))


def test_six_architecture_dimensions_are_required() -> None:
    manifest, _ = manifest_copy()
    del manifest["nodes"][0]["architecture"]["bounded_work"]
    assert any("all six dimensions" in error for error in errors_for(manifest))


def test_enum_values_fail_closed() -> None:
    manifest, _ = manifest_copy()
    manifest["nodes"][0]["runtime_state"] = "MERGED"
    manifest["nodes"][1]["implementation_state"] = "OLD"
    errors = errors_for(manifest)
    assert any("invalid runtime_state" in error for error in errors)
    assert any("invalid implementation_state" in error for error in errors)


def test_legacy_shim_requires_migration_map_entry() -> None:
    manifest, _ = manifest_copy()
    manifest["nodes"][0]["implementation_state"] = "LEGACY_SHIM"
    assert any("legacy shim absent" in error for error in errors_for(manifest))


def test_manifest_byte_bound_is_enforced() -> None:
    manifest, _ = manifest_copy()
    assert any("exceeds valid limit" in error for error in errors_for(manifest, manifest["byte_limit"] + 1))


def test_campaign_order_and_pending_semantics_are_fixed() -> None:
    manifest, _ = manifest_copy()
    assert [item["id"] for item in manifest["campaign"]] == CAMPAIGN_ORDER
    manifest["campaign"].reverse()
    manifest["campaign"][0]["state"] = "CURRENT"
    errors = errors_for(manifest)
    assert any("campaign order" in error for error in errors)
    assert any("stale campaign entry" in error for error in errors)
