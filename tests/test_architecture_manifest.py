from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.check_architecture_manifest import (
    CAMPAIGN_ORDER,
    CANONICAL_PACKAGE_DEPENDENCIES,
    REQUIRED_FAILURE_IMPACTS,
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


def test_duplicate_edge_ids_and_edge_enums_fail() -> None:
    manifest, _ = manifest_copy()
    manifest["edges"].append(copy.deepcopy(manifest["edges"][0]))
    manifest["edges"][0]["kind"] = "CALL"
    manifest["edges"][1]["criticality"] = "EVERYTHING"
    errors = errors_for(manifest)
    assert "duplicate edge id" in errors
    assert any("invalid edge kind" in error for error in errors)
    assert any("invalid edge criticality" in error for error in errors)


def test_view_edges_stay_inside_visible_nodes_and_no_node_is_orphaned() -> None:
    manifest, _ = manifest_copy()
    view = manifest["views"][0]
    view["node_ids"].remove("ctrader")
    errors = errors_for(manifest)
    assert any("leaves visible view nodes" in error for error in errors)
    assert any("lane membership" in error for error in errors)


def test_primary_path_and_lane_membership_are_validated() -> None:
    manifest, _ = manifest_copy()
    view = manifest["views"][0]
    view["primary_path"][1], view["primary_path"][2] = view["primary_path"][2], view["primary_path"][1]
    view["lanes"][0]["node_ids"].append(view["lanes"][1]["node_ids"][0])
    errors = errors_for(manifest)
    assert any("primary path" in error for error in errors)
    assert any("lane membership" in error for error in errors)


def test_scenario_continuity_and_failure_references_fail_closed() -> None:
    manifest, _ = manifest_copy()
    manifest["scenarios"][0]["edge_ids"][0] = manifest["scenarios"][0]["edge_ids"][1]
    manifest["failure_impacts"][0]["continues"][0]["node_id"] = "missing-node"
    errors = errors_for(manifest)
    assert any("follow-decision" in error and "continuous" in error for error in errors)
    assert any("failure impact references missing node" in error for error in errors)


def test_six_architecture_dimensions_are_required() -> None:
    manifest, _ = manifest_copy()
    del manifest["nodes"][0]["architecture"]["bounded_work"]
    assert any("all six dimensions" in error for error in errors_for(manifest))


def test_every_node_has_an_explicit_beginner_purpose() -> None:
    manifest, _ = manifest_copy()
    assert all(node["purpose"].strip() for node in manifest["nodes"])
    manifest["nodes"][0]["purpose"] = " "
    assert any("purpose must be a non-empty string" in error for error in errors_for(manifest))


def test_package_dependency_view_matches_the_canonical_contract() -> None:
    manifest, _ = manifest_copy()
    view = next(item for item in manifest["views"] if item["id"] == "package-dependencies")
    edges = {item["id"]: item for item in manifest["edges"]}
    actual = {(edges[edge_id]["from"], edges[edge_id]["to"]) for edge_id in view["edge_ids"]}
    expected = {
        (f"package-{source}", f"package-{dependency}")
        for source, dependencies in CANONICAL_PACKAGE_DEPENDENCIES.items()
        for dependency in dependencies
    }
    assert actual == expected
    assert all(edges[edge_id]["kind"] == "DEPENDENCY" for edge_id in view["edge_ids"])
    assert view["relationship_note"] == "A → B means A may import or depend on B."
    assert view["prohibited_directions"]

    view["edge_ids"].remove(view["edge_ids"][0])
    assert any("view must contain every dependency edge" in error for error in errors_for(manifest))


def test_required_failure_impacts_are_explicit() -> None:
    manifest, _ = manifest_copy()
    impacts = {item["node_id"]: item for item in manifest["failure_impacts"]}
    assert REQUIRED_FAILURE_IMPACTS.issubset(impacts)
    assert all("AFFECTED" in entry["message"] for impact in impacts.values() for entry in impact["affected"])
    assert all("CONTINUES" in entry["message"] for impact in impacts.values() for entry in impact["continues"])


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
